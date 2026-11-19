# -*- coding: utf-8 -*-
# Copyright 2026 TDB Solution Co., Ltd.
"""
Mở rộng model ir.attachment cho phân hệ OdooPLM:
1. Kiến trúc Người giữ Key (Key Holder Architecture):
   - Kỹ sư đầu tiên tạo / check-in tài liệu từ máy trạm sẽ giữ Key độc quyền.
   - Kỹ sư giữ Key sau khi Check-in vẫn tiếp tục giữ Key độc quyền của tài liệu.
   - Mọi kỹ sư đều được quyền Check-out tải file về máy trạm phục vụ thiết kế, tham chiếu.
   - Duy nhất kỹ sư giữ Key mới được quyền Check-in cập nhật file lên máy chủ.
   - Kỹ sư khác chỉ được Check-in khi Kỹ sư giữ Key tự nguyện Bàn giao / Nhả Key hoặc Sếp Force Unlock.
2. Lịch sử phiên bản tệp (File History - Task T4):
   - Tự động lưu 1 snapshot phiên bản duy nhất (v1, v2,...) khi Check-in (có debounce chống trùng lặp).
   - Tự động khởi tạo phiên bản v1 cho các tài liệu đã có file gốc trên hệ thống.
3. Quản lý quan hệ file tham chiếu Assembly & Where-Used (Task 5).
4. Quản trị vòng đời Access Token 180 ngày cho CAD Client SolidWorks.
5. Chuẩn hóa nhật ký Chatter bằng văn bản ERP thuần (không icon, không bullet, không gạch đầu dòng).
"""
import os
import json
import logging
from markupsafe import Markup
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    # =========================================================================
    # 1. KIẾN TRÚC NGƯỜI GIỮ KEY (KEY HOLDER ARCHITECTURE)
    # =========================================================================
    key_holder_id = fields.Many2one(
        "res.users",
        string="Key Holder",
        readonly=True,
        index=True,
        tracking=True,
        help="Engineer who holds exclusive Check-in permission for this CAD document."
    )
    is_key_holder = fields.Boolean(
        string="I Hold Key",
        compute="_compute_is_key_holder",
        store=False
    )
    is_checked_out_by_me = fields.Boolean(
        string="Checked Out By Me",
        compute="_compute_is_checked_out_by_me",
        store=False,
        help="Marks whether this document has been checked out by current user."
    )
    lock_state = fields.Selection(
        [
            ('unlocked', 'Unlocked'),
            ('locked', 'Locked'),
        ],
        string="Status",
        compute="_compute_lock_state",
        store=False,
        help="Check-out lock status of document."
    )
    locked_user_id = fields.Many2one(
        "res.users",
        string="Checked Out By",
        compute="_compute_lock_state",
        store=False
    )
    locked_hostname = fields.Char(
        string="Workstation",
        compute="_compute_lock_state",
        store=False
    )
    locked_date = fields.Datetime(
        string="Check-out Time",
        compute="_compute_lock_state",
        store=False
    )

    # =========================================================================
    # FIELD-LEVEL TRACKING CHO QUẢN LÝ VÒNG ĐỜI & BẢN VẼ PLM (HT-5)
    # =========================================================================
    engineering_state = fields.Selection(tracking=True)
    engineering_revision = fields.Integer(tracking=True)
    document_type = fields.Selection(tracking=True)

    # Trường datas tương thích ngược cho Odoo 18 / phân hệ DMS / CAD Client
    datas = fields.Binary(
        string="Attachment (base64)",
        compute="_compute_datas",
        inverse="_inverse_datas",
        store=False,
        help="Backward compatibility field for DMS and CAD Clients accessing attachment.datas"
    )

    def _compute_datas(self):
        for attach in self:
            if attach.raw:
                import base64
                attach.datas = base64.b64encode(bytes(attach.raw)).decode()
            elif attach.db_datas:
                import base64
                attach.datas = base64.b64encode(bytes(attach.db_datas)).decode()
            else:
                attach.datas = False

    def _inverse_datas(self):
        for attach in self:
            if attach.datas:
                import base64
                data = attach.datas
                if isinstance(data, str):
                    try:
                        raw_bytes = base64.b64decode(data)
                    except Exception:
                        raw_bytes = data.encode()
                else:
                    raw_bytes = bytes(data)
                attach.raw = raw_bytes
            else:
                attach.raw = False

    @api.depends_context('uid')
    @api.depends('key_holder_id')
    def _compute_is_key_holder(self):
        for doc in self:
            doc.is_key_holder = (doc.key_holder_id.id == self.env.uid)

    @api.depends_context('uid')
    @api.depends('checkout_ids', 'checkout_ids.userid')
    def _compute_is_checked_out_by_me(self):
        for doc in self:
            doc.is_checked_out_by_me = any(co.userid.id == self.env.uid for co in doc.checkout_ids)

    @api.depends('checkout_ids', 'key_holder_id')
    def _compute_lock_state(self):
        """Tính toán trạng thái Check-out từ model plm.checkout."""
        for doc in self:
            if doc.checkout_ids:
                co = doc.checkout_ids[0]
                doc.lock_state = 'locked'
                doc.locked_user_id = co.userid
                doc.locked_hostname = co.hostname
                doc.locked_date = co.create_date
            else:
                doc.lock_state = 'unlocked'
                doc.locked_user_id = False
                doc.locked_hostname = False
                doc.locked_date = False

    def _check_key_holder_permission(self):
        """Kiểm tra quyền giữ Key độc quyền trước khi Check-in:
        Chỉ áp dụng cho tài liệu PLM (is_plm = True).
        Chỉ kỹ sư giữ Key mới được quyền Check-in để ghi đè dữ liệu tệp lên hệ thống.
        Kể cả Quản trị viên (Admin) cũng phải có Key mới được Check-in.
        Nếu tài liệu chưa có ai giữ Key, phải bấm nút 'Lock' để nhận quyền giữ Key trước.
        Các kỹ sư khác chỉ có quyền Check-out tải file về máy trạm phục vụ tham chiếu.
        Tài liệu đã Ban hành (released/obsoleted) bị khóa tuyệt đối, không được Check-in.
        """
        for doc in self:
            # BẮT BUỘC: Bỏ qua hoàn toàn đối với các tệp đính kèm không phải tài liệu PLM (như favicon.ico, logo, web assets, chatter,...)
            if not getattr(doc, 'is_plm', False):
                continue

            if doc.engineering_state in ['confirmed', 'released', 'obsoleted']:
                state_desc = "Đã ban hành" if doc.engineering_state == 'released' else ("Đang chờ duyệt" if doc.engineering_state == 'confirmed' else "Lỗi thời")
                raise UserError(_(
                    "Tài liệu '%s' đang ở trạng thái %s (%s). "
                    "Không được phép Check-in hoặc cập nhật bản vẽ!"
                ) % (doc.name or doc.engineering_code, state_desc, doc.engineering_state))

            if doc.is_parent_frozen:
                raise UserError(_(
                    "Tài liệu '%s' thuộc Cụm máy cha '%s' đã ở trạng thái Ban hành / Đóng băng. "
                    "Không được phép Check-in hoặc cập nhật bản vẽ!"
                ) % (doc.name or doc.engineering_code, doc.parent_released_name))

            if not doc.key_holder_id:
                raise UserError(_(
                    "Tài liệu '%s' hiện chưa có ai giữ Key. "
                    "Vui lòng bấm nút 'Lock' để nhận quyền giữ Key trước khi Check-in!"
                ) % (doc.name or doc.engineering_code))

            if doc.key_holder_id != self.env.user:
                key_holder_name = doc.key_holder_id.name
                raise UserError(_(
                    "Tài liệu '%s' hiện do kỹ sư '%s' giữ Key. "
                    "Chỉ người giữ Key mới có quyền Check-in để ghi đè dữ liệu. "
                    "Bạn chỉ được quyền Check-out ở chế độ tham chiếu. "
                    "Vui lòng liên hệ kỹ sư '%s' hoặc Quản trị viên để chuyển giao Key."
                ) % (doc.name or doc.engineering_code, key_holder_name, key_holder_name))

    # =========================================================================
    # 2. LỊCH SỬ PHIÊN BẢN TỆP (FILE HISTORY - TASK T4)
    # =========================================================================
    history_ids = fields.One2many(
        "plm.document.history",
        "document_id",
        string="Version History",
        readonly=True,
        help="List of previous file snapshots for this document."
    )
    history_count = fields.Integer(
        string="Version Count",
        compute="_compute_history_count",
        store=False,
        help="Total number of historical file versions stored."
    )

    @api.depends("history_ids")
    def _compute_history_count(self):
        for doc in self:
            count = len(doc.history_ids)
            # Tự động tạo snapshot v1 nếu tài liệu đã có file gốc nhưng chưa có lịch sử
            if count == 0 and doc.is_plm and (doc.raw or (doc.file_size and doc.file_size > 0)):
                try:
                    doc._create_document_history_snapshot(
                        description=_("Bản vẽ gốc ban đầu"),
                        user_id=doc.create_uid or self.env.user
                    )
                    count = len(doc.history_ids)
                except Exception as e:
                    _logger.warning(f"[TDB PLM] Lỗi tự động tạo snapshot ban đầu: {e}")
            doc.history_count = count

    def action_view_file_history(self):
        """Mở danh sách lịch sử phiên bản của tài liệu hiện tại."""
        self.ensure_one()
        return {
            "name": _("Lịch sử phiên bản: %s") % (self.name or ""),
            "type": "ir.actions.act_window",
            "res_model": "plm.document.history",
            "view_mode": "list,form",
            "domain": [("document_id", "=", self.id)],
            "context": {"default_document_id": self.id},
        }

    def _create_document_history_snapshot(self, description=None, user_id=None, custom_datas=None):
        """Tạo snapshot phiên bản tệp lưu vào plm.document.history (chống trùng lặp tuyệt đối)."""
        if 'plm.document.history' not in self.env:
            return
        History = self.env['plm.document.history'].sudo()
        now = fields.Datetime.now()

        for doc in self:
            last_history = History.search(
                [('document_id', '=', doc.id)],
                order='version_number desc, id desc',
                limit=1
            )

            # Debounce: Bỏ qua tạo snapshot trùng lặp nếu vừa tạo trong vòng 10 giây trước
            if last_history and last_history.create_date:
                time_diff = (now - last_history.create_date).total_seconds()
                if time_diff < 10 and not self.env.context.get('force_create_snapshot'):
                    _logger.info(f"[TDB PLM] Bỏ qua snapshot trùng lặp (vừa tạo cách đây {time_diff:.1f}s)")
                    continue

            raw_bytes = b""
            snapshot_datas = custom_datas

            if not snapshot_datas:
                if hasattr(History, '_read_attachment_bytes'):
                    raw_bytes = History._read_attachment_bytes(doc)
                if not raw_bytes:
                    doc_raw = getattr(doc, 'raw', None)
                    if doc_raw:
                        try:
                            raw_bytes = bytes(doc_raw)
                        except Exception:
                            raw_bytes = doc_raw if isinstance(doc_raw, bytes) else b""

                if not raw_bytes:
                    db_d = getattr(doc, 'db_datas', None)
                    if db_d:
                        try:
                            raw_bytes = bytes(db_d)
                        except Exception:
                            raw_bytes = db_d if isinstance(db_d, bytes) else b""

                if raw_bytes:
                    import base64
                    try:
                        snapshot_datas = base64.b64encode(raw_bytes).decode()
                    except Exception:
                        snapshot_datas = False
            else:
                if isinstance(snapshot_datas, bytes):
                    raw_bytes = snapshot_datas
                    import base64
                    snapshot_datas = base64.b64encode(raw_bytes).decode()
                elif isinstance(snapshot_datas, str):
                    import base64
                    try:
                        raw_bytes = base64.b64decode(snapshot_datas)
                    except Exception:
                        raw_bytes = snapshot_datas.encode()

            size_bytes = len(raw_bytes) if raw_bytes else (doc.file_size or 0)
            next_ver_num = (last_history.version_number + 1) if last_history else 1
            version_str = f"v{next_ver_num}"
            creator_user = user_id or self.env.user

            # Xác định tên file chuẩn xác từ doc.name hoặc doc.engineering_code (tuyệt đối không dùng doc.cad_name vì là tên phần mềm CAD 'SOLIDWORKS')
            cad_filename = doc.name or doc.engineering_code or 'document'
            if '/' in cad_filename:
                base_part, ext_part = cad_filename.rsplit('/', 1)
                cad_filename = f"{base_part}.{ext_part.lower()}"
            elif '.' not in cad_filename:
                ext = ""
                if hasattr(doc, 'getFileExtension'):
                    ext = doc.getFileExtension(doc)
                if ext:
                    cad_filename = f"{cad_filename}{ext.lower()}"

            History.create({
                'document_id': doc.id,
                'version': version_str,
                'version_number': next_ver_num,
                'datas': snapshot_datas or False,
                'file_name': cad_filename,
                'file_size': size_bytes,
                'user_id': creator_user.id,
                'description': description or _("Lưu khi Check-in bản vẽ"),
            })
            _logger.info(
                f"[TDB PLM] Đã tạo snapshot phiên bản {version_str} cho tài liệu ID {doc.id} ({doc.name}), size: {size_bytes} B"
            )
            # Phát thông báo bus real-time để các máy trạm khác cập nhật tức thời không cần F5
            doc._notify_plm_document_update()

    def _notify_plm_document_update(self):
        """Phát thông báo bus real-time đến tất cả người dùng khi tài liệu có phiên bản mới hoặc cập nhật trạng thái."""
        for doc in self:
            try:
                bus = self.env['bus.bus'].sudo()
                payload = {
                    'document_id': doc.id,
                    'name': doc.name,
                    'engineering_code': doc.engineering_code,
                    'version_count': getattr(doc, 'history_count', 0),
                    'action': 'version_updated',
                }
                # Broadcast channel
                try:
                    bus._sendone('broadcast', 'plm_document_updated', payload)
                except Exception:
                    pass
                try:
                    bus._sendone('plm_document_channel', 'plm_document_updated', payload)
                except Exception:
                    pass
                # Gửi đến tất cả người dùng nội bộ để nhận qua WebSocket/Longpolling
                partners = self.env['res.users'].sudo().search([('share', '=', False)]).mapped('partner_id')
                for partner in partners:
                    try:
                        bus._sendone(partner, 'plm_document_updated', payload)
                    except Exception:
                        pass
            except Exception as e:
                _logger.warning(f"[TDB PLM] Không thể gửi thông báo bus real-time: {e}")

    # =========================================================================
    # 3. QUẢN LÝ FILE THAM CHIẾU ASSEMBLY (RELATED REFERENCES & WHERE-USED)
    # =========================================================================
    child_relation_ids = fields.One2many(
        "ir.attachment.relation",
        "parent_id",
        domain=[("link_kind", "!=", "LyTree")],
        string="Child Documents",
        help="List of child component files referenced by this Assembly."
    )
    drawing_relation_ids = fields.One2many(
        "ir.attachment.relation",
        "parent_id",
        domain=[("link_kind", "=", "LyTree")],
        string="2D Drawing Relations",
        help="Danh sách quan hệ bản vẽ 2D (Layout Tree) liên kết với mô hình 3D này."
    )
    parent_relation_ids = fields.One2many(
        "ir.attachment.relation",
        "child_id",
        string="Where Used",
        help="List of parent assemblies referencing this document."
    )
    child_document_count = fields.Integer(
        string="Child Documents Count",
        compute="_compute_relation_counts",
        store=False,
        help="Number of child component references."
    )
    parent_document_count = fields.Integer(
        string="Where Used Count",
        compute="_compute_relation_counts",
        store=False,
        help="Number of parent assemblies using this document."
    )
    is_parent_frozen = fields.Boolean(
        string="Is Parent Frozen",
        compute="_compute_parent_assembly_status",
        store=False,
        help="Đánh dấu True nếu tài liệu này thuộc một Cụm máy cha (Where Used) đã ở trạng thái Đóng băng/Ban hành."
    )
    parent_released_name = fields.Char(
        string="Frozen Parent Name",
        compute="_compute_parent_assembly_status",
        store=False
    )
    linked_product_count = fields.Integer(
        string="Linked Products Count",
        compute="_compute_linked_product_info",
        store=False,
        help="Số lượng sản phẩm linh kiện kỹ thuật liên kết với tài liệu CAD này."
    )
    bom_count = fields.Integer(
        string="BOM Count",
        compute="_compute_bom_info",
        store=False,
        help="Số lượng Định mức vật tư (mrp.bom) liên kết với tài liệu CAD này."
    )

    @api.depends("linkedcomponents")
    def _compute_linked_product_info(self):
        for doc in self:
            doc.linked_product_count = len(doc.linkedcomponents)

    def _get_related_boms(self):
        """Lấy danh sách các bản ghi mrp.bom liên quan đến tài liệu này."""
        self.ensure_one()
        if not self.id:
            return self.env["mrp.bom"]
        if self.linkedcomponents:
            domain = ["|", ("source_id", "=", self.id), ("product_id", "in", self.linkedcomponents.ids)]
        else:
            domain = [("source_id", "=", self.id)]
        return self.env["mrp.bom"].sudo().search(domain)

    @api.depends("linkedcomponents")
    def _compute_bom_info(self):
        for doc in self:
            if not doc.id:
                doc.bom_count = 0
                continue
            doc.bom_count = len(doc._get_related_boms())

    def get_all_parent_assemblies(self):
        """Lấy danh sách tất cả các Cụm máy cha (tất cả các cấp Where-Used) chứa tài liệu này."""
        all_parents = self.env['ir.attachment']
        current_docs = self
        visited_ids = set(self.ids)
        while current_docs:
            relations = self.env['ir.attachment.relation'].search([
                ('child_id', 'in', current_docs.ids)
            ])
            parents = relations.mapped('parent_id').filtered(lambda p: p.id not in visited_ids and p.exists())
            if not parents:
                break
            all_parents |= parents
            visited_ids.update(parents.ids)
            current_docs = parents
        return all_parents

    @api.depends("parent_relation_ids", "parent_relation_ids.parent_id.engineering_state")
    def _compute_parent_assembly_status(self):
        for doc in self:
            all_parents = doc.get_all_parent_assemblies()
            frozen_parents = all_parents.filtered(lambda p: p.engineering_state in ['confirmed', 'released', 'obsoleted'])
            if frozen_parents:
                doc.is_parent_frozen = True
                names = [p.name or p.engineering_code or str(p.id) for p in frozen_parents[:3]]
                doc.parent_released_name = ", ".join(names)
            else:
                doc.is_parent_frozen = False
                doc.parent_released_name = False

    @api.depends("child_relation_ids", "parent_relation_ids")
    def _compute_relation_counts(self):
        for doc in self:
            doc.child_document_count = len(doc.child_relation_ids)
            doc.parent_document_count = len(doc.parent_relation_ids)

    def get_child_documents(self, link_kinds=None):
        """Trả về Recordset các tài liệu ir.attachment con trực tiếp của tài liệu hiện tại.
        :param link_kinds: Danh sách loại liên kết (ví dụ: ['HiTree', 'LyTree', 'RfTree']),
                           hoặc chuỗi đơn. Nếu None, lấy tất cả loại liên kết trực tiếp.
        :return: Recordset ir.attachment các tài liệu con trực tiếp.
        """
        self.ensure_one()
        relations = self.child_relation_ids
        if link_kinds is not None:
            if isinstance(link_kinds, str):
                link_kinds = [link_kinds]
            relations = relations.filtered(lambda r: r.link_kind in link_kinds)
        return relations.mapped("child_id").exists()

    def get_where_used_documents(self):
        """Trả về Recordset các tài liệu ir.attachment cha đang trực tiếp sử dụng tài liệu này (Where Used).
        :return: Recordset ir.attachment các tài liệu cha.
        """
        self.ensure_one()
        return self.parent_relation_ids.mapped("parent_id").exists()

    def add_child_reference(self, child_doc_id, configuration="", link_kind="HiTree"):
        """Thêm liên kết tham chiếu cha-con an toàn giữa tài liệu hiện tại và tài liệu con.
        :param child_doc_id: ID (int) hoặc Recordset ir.attachment của tài liệu con.
        :param configuration: Tên cấu hình CAD (mặc định '').
        :param link_kind: Loại liên kết CAD (HiTree, LyTree, RfTree,... mặc định 'HiTree').
        :return: Recordset ir.attachment.relation được tạo hoặc đã tồn tại.
        """
        self.ensure_one()
        if not child_doc_id:
            raise UserError(_("Vui lòng chỉ định tài liệu con cần liên kết."))

        child = self.browse(child_doc_id) if isinstance(child_doc_id, int) else child_doc_id
        if not child.exists():
            raise UserError(_("Tài liệu con không tồn tại trên hệ thống."))

        if self.id == child.id:
            raise UserError(_("Tài liệu không thể tự tham chiếu chính mình làm file con."))

        rel_model = self.env["ir.attachment.relation"]
        link_kind = link_kind or "HiTree"
        configuration = configuration or ""

        # Kiểm tra quan hệ trùng lặp theo đúng unique constraint của Omnia: (parent_id, child_id, link_kind)
        existing = rel_model.search([
            ("parent_id", "=", self.id),
            ("child_id", "=", child.id),
            ("link_kind", "=", link_kind),
        ], limit=1)

        if existing:
            if configuration and existing.configuration != configuration:
                existing.write({"configuration": configuration})
            return existing

        return rel_model.create({
            "parent_id": self.id,
            "child_id": child.id,
            "configuration": configuration,
            "link_kind": link_kind,
        })

    def action_view_child_documents(self):
        """Mở danh sách các tài liệu con liên quan."""
        self.ensure_one()
        child_docs = self.get_child_documents()
        list_view = self.env.ref('plm.ir_attachment_list', raise_if_not_found=False)
        form_view = self.env.ref('plm.view_attachment_form_plm_hinerit', raise_if_not_found=False)
        views = []
        if list_view:
            views.append((list_view.id, 'list'))
        if form_view:
            views.append((form_view.id, 'form'))
        return {
            "name": _("Chi tiết con liên quan: %s") % (self.name or self.engineering_code or self.id),
            "type": "ir.actions.act_window",
            "res_model": "ir.attachment",
            "view_mode": "list,form",
            "views": views or False,
            "domain": [("id", "in", child_docs.ids)],
            "context": {
                "odooPLM": True,
                "default_is_plm": True,
                "form_view_ref": "plm.view_attachment_form_plm_hinerit",
                "list_view_ref": "plm.ir_attachment_list",
            },
        }

    def action_view_where_used_documents(self):
        """Mở danh sách các nơi sử dụng tài liệu này (Where Used)."""
        self.ensure_one()
        parent_docs = self.get_where_used_documents()
        list_view = self.env.ref('plm.ir_attachment_list', raise_if_not_found=False)
        form_view = self.env.ref('plm.view_attachment_form_plm_hinerit', raise_if_not_found=False)
        views = []
        if list_view:
            views.append((list_view.id, 'list'))
        if form_view:
            views.append((form_view.id, 'form'))
        return {
            "name": _("Nơi sử dụng (Where Used): %s") % (self.name or self.engineering_code or self.id),
            "type": "ir.actions.act_window",
            "res_model": "ir.attachment",
            "view_mode": "list,form",
            "views": views or False,
            "domain": [("id", "in", parent_docs.ids)],
            "context": {
                "odooPLM": True,
                "default_is_plm": True,
                "form_view_ref": "plm.view_attachment_form_plm_hinerit",
                "list_view_ref": "plm.ir_attachment_list",
            },
        }

    # =========================================================================
    # TASK HT-1: LIÊN KẾT BẢN VẼ 2D VỚI MÔ HÌNH 3D (2D - 3D LYTREE RELATION)
    # =========================================================================
    has_printout = fields.Boolean(
        string="Đã có bản in PDF",
        compute="_compute_has_printout",
        store=False,
        help="Đánh dấu True nếu tài liệu đã có tệp PDF in kỹ thuật (printout)."
    )
    preview_pdf_html = fields.Html(
        string="Bản xem trước PDF",
        compute="_compute_preview_pdf_html",
        sanitize=False,
        help="Khung hiển thị bản xem trước PDF kỹ thuật trong cửa sổ popup."
    )
    printout_name = fields.Char(
        string="Printout Name",
        compute="_getPrintoutName",
        inverse="_inverse_printout_name",
    )
    drawing_count = fields.Integer(
        string="Số bản vẽ 2D",
        compute="_compute_drawing_info",
        store=False,
        help="Số lượng bản vẽ 2D (Layout Tree) liên kết với mô hình 3D này."
    )
    has_drawing = fields.Boolean(
        string="Có bản vẽ 2D",
        compute="_compute_drawing_info",
        store=False,
        help="Đánh dấu True nếu mô hình 3D này có ít nhất một bản vẽ 2D liên kết."
    )
    source_3d_count = fields.Integer(
        string="Số mô hình 3D nguồn",
        compute="_compute_source_3d_info",
        store=False,
        help="Số lượng mô hình 3D nguồn mà bản vẽ 2D này biểu diễn."
    )
    linked_drawing_ids = fields.Many2many(
        "ir.attachment",
        compute="_compute_drawing_info",
        string="Bản vẽ 2D liên kết",
        help="Danh sách các bản vẽ 2D (Layout Tree) liên kết với mô hình 3D này."
    )
    linked_source_3d_ids = fields.Many2many(
        "ir.attachment",
        compute="_compute_source_3d_info",
        string="Mô hình 3D nguồn",
        help="Danh sách các mô hình 3D mà bản vẽ 2D này liên kết."
    )

    @api.depends("printout", "raw", "datas", "name")
    def _compute_has_printout(self):
        for doc in self:
            has_p = bool(doc.printout)
            if not has_p and (doc.name or "").lower().endswith(".pdf"):
                has_p = bool(doc.raw or doc.datas)
            doc.has_printout = has_p

    @api.depends("printout", "raw", "datas", "name", "has_printout")
    def _compute_preview_pdf_html(self):
        for doc in self:
            if not doc.id:
                doc.preview_pdf_html = False
                continue
            if doc.has_printout:
                ts = int(fields.Datetime.now().timestamp())
                pdf_url = f"/plm/ir_attachment_printout/{doc.id}?v={ts}"
                doc.preview_pdf_html = (
                    f'<iframe src="{pdf_url}" '
                    f'style="width: 100%; height: 84vh; border: none; display: block; background: #525659;"></iframe>'
                )
            else:
                doc.preview_pdf_html = (
                    '<div class="d-flex flex-column align-items-center justify-content-center h-100 text-center p-5 text-muted" style="min-height: 60vh;">'
                    '<i class="fa fa-file-pdf-o fa-5x mb-3 text-secondary"></i>'
                    '<h4>Tài liệu chưa có bản in PDF kỹ thuật.</h4>'
                    '<p class="mb-0">Vui lòng tải lên tệp PDF hoặc xuất printout từ phần mềm CAD.</p>'
                    '</div>'
                )

    def _inverse_printout_name(self):
        for doc in self:
            pass

    @api.depends("child_relation_ids", "parent_relation_ids", "child_relation_ids.link_kind", "parent_relation_ids.link_kind")
    def _compute_drawing_info(self):
        """Tính toán số lượng và danh sách bản vẽ 2D liên kết với mô hình 3D."""
        for doc in self:
            if doc.id:
                drawings = doc.get_related_drawings()
                doc.linked_drawing_ids = drawings
                doc.drawing_count = len(drawings)
                doc.has_drawing = bool(doc.drawing_count > 0)
            else:
                doc.linked_drawing_ids = self.env["ir.attachment"]
                doc.drawing_count = 0
                doc.has_drawing = False

    @api.depends("child_relation_ids", "parent_relation_ids", "child_relation_ids.link_kind", "parent_relation_ids.link_kind")
    def _compute_source_3d_info(self):
        """Tính toán số lượng và danh sách mô hình 3D nguồn khi đang ở bản vẽ 2D."""
        for doc in self:
            if doc.id:
                models_3d = doc.get_related_3d_models()
                doc.linked_source_3d_ids = models_3d
                doc.source_3d_count = len(models_3d)
            else:
                doc.linked_source_3d_ids = self.env["ir.attachment"]
                doc.source_3d_count = 0

    def get_related_drawings(self):
        """Trả về Recordset các tài liệu ir.attachment là Bản vẽ 2D (document_type == '2d')
        liên kết với chi tiết 3D hiện tại qua link_kind='LyTree'.
        Tương thích 100% với getRelatedLyTree của Omnia PLM và SolidWorks Add-in.
        """
        self.ensure_one()
        if not self.id:
            return self.env["ir.attachment"]

        drawing_ids = set()
        # 1. Tận dụng hàm chuẩn của Omnia PLM nếu có thể
        try:
            omnia_drawings = self.getRelatedLyTree(self.id, optional_return_type=["2d"])
            if omnia_drawings:
                drawing_ids.update(omnia_drawings)
        except Exception as e:
            _logger.debug(f"[TDB PLM] getRelatedLyTree error: {e}")

        # 2. Truy vấn trực tiếp từ bảng quan hệ ir.attachment.relation để đảm bảo nhận diện 2 chiều
        rel_domain = [
            ("link_kind", "in", ["LyTree", "PkgTree"]),
            "|",
            ("parent_id", "=", self.id),
            ("child_id", "=", self.id),
        ]
        relations = self.env["ir.attachment.relation"].search(rel_domain)
        for rel in relations:
            other = rel.child_id if rel.parent_id.id == self.id else rel.parent_id
            if not other or not other.exists() or other.id == self.id:
                continue
            is_2d = (
                other.document_type == "2d"
                or (other.name and other.name.lower().endswith((".slddrw", ".dwg", ".dxf", ".pdf")))
            )
            if is_2d:
                drawing_ids.add(other.id)

        return self.browse(list(drawing_ids)).exists()

    def get_related_3d_models(self):
        """Trả về Recordset các tài liệu ir.attachment là Mô hình 3D (document_type == '3d')
        liên kết với bản vẽ 2D hiện tại qua link_kind='LyTree'.
        """
        self.ensure_one()
        if not self.id:
            return self.env["ir.attachment"]

        model_ids = set()
        # 1. Tận dụng hàm chuẩn của Omnia PLM nếu có thể
        try:
            omnia_models = self.getRelatedLyTree(self.id, optional_return_type=["3d"])
            if omnia_models:
                model_ids.update(omnia_models)
        except Exception as e:
            _logger.debug(f"[TDB PLM] getRelatedLyTree error: {e}")

        # 2. Truy vấn trực tiếp từ bảng quan hệ ir.attachment.relation
        rel_domain = [
            ("link_kind", "in", ["LyTree", "PkgTree"]),
            "|",
            ("parent_id", "=", self.id),
            ("child_id", "=", self.id),
        ]
        relations = self.env["ir.attachment.relation"].search(rel_domain)
        for rel in relations:
            other = rel.child_id if rel.parent_id.id == self.id else rel.parent_id
            if not other or not other.exists() or other.id == self.id:
                continue
            is_3d = (
                other.document_type == "3d"
                or (other.name and other.name.lower().endswith((".sldprt", ".sldasm", ".prt", ".asm", ".step", ".stp")))
            )
            if is_3d:
                model_ids.add(other.id)

        return self.browse(list(model_ids)).exists()

    def link_drawing_document(self, drawing_doc_id):
        """Liên kết bản vẽ 2D với mô hình 3D hiện tại qua link_kind='LyTree'.
        Sử dụng hàm add_child_reference có sẵn để đảm bảo toàn vẹn dữ liệu.
        :param drawing_doc_id: ID (int) hoặc Recordset ir.attachment của bản vẽ 2D.
        :return: Recordset ir.attachment.relation vừa tạo hoặc đã tồn tại.
        """
        self.ensure_one()
        if not drawing_doc_id:
            raise UserError(_("Vui lòng chỉ định bản vẽ 2D cần liên kết."))

        drawing = self.browse(drawing_doc_id) if isinstance(drawing_doc_id, int) else drawing_doc_id
        if not drawing.exists():
            raise UserError(_("Bản vẽ 2D không tồn tại trên hệ thống."))

        # Kiểm tra an toàn: File được liên kết phải có document_type == '2d' hoặc phần mở rộng là file bản vẽ
        valid_extensions = ('.slddrw', '.dwg', '.dxf', '.pdf')
        has_2d_ext = bool(drawing.name and drawing.name.lower().endswith(valid_extensions))
        is_2d_type = (drawing.document_type == '2d')

        if not (is_2d_type or has_2d_ext):
            raise UserError(_(
                "Tài liệu '%s' không phải là bản vẽ 2D hợp lệ! "
                "(Yêu cầu document_type == '2d' hoặc đuôi file thuộc: .slddrw, .dwg, .dxf, .pdf)"
            ) % (drawing.name or drawing.id))

        # Tự động cập nhật document_type nếu chưa đặt đúng
        if drawing.document_type != '2d':
            drawing.sudo().write({'document_type': '2d'})

        if not self.document_type or self.document_type == 'other':
            if self.name and self.name.lower().endswith(('.sldprt', '.sldasm', '.prt', '.asm', '.step', '.stp')):
                self.sudo().write({'document_type': '3d'})

        # Gọi hàm add_child_reference chuẩn hóa với link_kind='LyTree'
        return self.add_child_reference(drawing.id, link_kind='LyTree')

    def action_view_drawings(self):
        """Action Window mở danh sách / chi tiết các bản vẽ 2D của Part/Assembly hiện tại."""
        self.ensure_one()
        drawings = self.get_related_drawings()
        list_view = self.env.ref("plm.ir_attachment_list", raise_if_not_found=False)
        form_view = self.env.ref("plm.view_attachment_form_plm_hinerit", raise_if_not_found=False)

        if len(drawings) == 1:
            return {
                "name": drawings.name or drawings.engineering_code or _("Bản vẽ 2D"),
                "type": "ir.actions.act_window",
                "res_model": "ir.attachment",
                "res_id": drawings.id,
                "view_mode": "form",
                "views": [(form_view.id, "form")] if form_view else False,
                "context": {
                    "odooPLM": True,
                    "default_is_plm": True,
                    "default_document_type": "2d",
                    "form_view_ref": "plm.view_attachment_form_plm_hinerit",
                },
            }

        views = []
        if list_view:
            views.append((list_view.id, "list"))
        if form_view:
            views.append((form_view.id, "form"))

        return {
            "name": _("Bản vẽ 2D liên kết: %s") % (self.name or self.engineering_code or self.id),
            "type": "ir.actions.act_window",
            "res_model": "ir.attachment",
            "view_mode": "list,form",
            "views": views or False,
            "domain": [("id", "in", drawings.ids)],
            "context": {
                "odooPLM": True,
                "default_is_plm": True,
                "default_document_type": "2d",
                "form_view_ref": "plm.view_attachment_form_plm_hinerit",
                "list_view_ref": "plm.ir_attachment_list",
            },
        }

    def action_open_source_3d(self):
        """Action Window mở chi tiết mô hình 3D gốc khi người dùng đang đứng ở form bản vẽ 2D."""
        self.ensure_one()
        models_3d = self.get_related_3d_models()
        form_view = self.env.ref("plm.view_attachment_form_plm_hinerit", raise_if_not_found=False)
        list_view = self.env.ref("plm.ir_attachment_list", raise_if_not_found=False)

        if len(models_3d) == 1:
            return {
                "name": models_3d.name or models_3d.engineering_code or _("Mô hình 3D"),
                "type": "ir.actions.act_window",
                "res_model": "ir.attachment",
                "res_id": models_3d.id,
                "view_mode": "form",
                "views": [(form_view.id, "form")] if form_view else False,
                "context": {
                    "odooPLM": True,
                    "default_is_plm": True,
                    "default_document_type": "3d",
                    "form_view_ref": "plm.view_attachment_form_plm_hinerit",
                },
            }

        views = []
        if list_view:
            views.append((list_view.id, "list"))
        if form_view:
            views.append((form_view.id, "form"))

        return {
            "name": _("Mô hình 3D nguồn: %s") % (self.name or self.engineering_code or self.id),
            "type": "ir.actions.act_window",
            "res_model": "ir.attachment",
            "view_mode": "list,form",
            "views": views or False,
            "domain": [("id", "in", models_3d.ids)],
            "context": {
                "odooPLM": True,
                "default_is_plm": True,
                "default_document_type": "3d",
                "form_view_ref": "plm.view_attachment_form_plm_hinerit",
                "list_view_ref": "plm.ir_attachment_list",
            },
        }

    def action_open_document_form(self):
        """Mở Form view PLM chi tiết của chính tài liệu này trên Odoo."""
        self.ensure_one()
        form_view = self.env.ref("plm.view_attachment_form_plm_hinerit", raise_if_not_found=False)
        return {
            "name": self.name or self.engineering_code or _("Tài liệu kỹ thuật"),
            "type": "ir.actions.act_window",
            "res_model": "ir.attachment",
            "res_id": self.id,
            "view_mode": "form",
            "views": [(form_view.id, "form")] if form_view else False,
            "target": "current",
            "context": {
                "odooPLM": True,
                "default_is_plm": True,
                "form_view_ref": "plm.view_attachment_form_plm_hinerit",
            },
        }

    def action_open_link_drawing_wizard(self):
        """Mở wizard liên kết bản vẽ 2D với mô hình 3D hiện tại."""
        self.ensure_one()
        return {
            "name": _("Liên kết Bản vẽ 2D với %s") % (self.name or self.engineering_code or self.id),
            "type": "ir.actions.act_window",
            "res_model": "plm.link.drawing.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_parent_id": self.id,
            },
        }

    def action_upload_printout_wizard(self):
        """Mở wizard tải lên bản in PDF kỹ thuật cho tài liệu này."""
        self.ensure_one()
        code = self.engineering_code or self.name or "document"
        rev = self.engineering_revision or 0
        return {
            "name": _("Tải lên bản in PDF: %s") % (self.name or self.engineering_code or self.id),
            "type": "ir.actions.act_window",
            "res_model": "plm.upload.printout.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "active_id": self.id,
                "active_ids": [self.id],
                "active_model": "ir.attachment",
                "default_attachment_id": self.id,
                "default_pdf_filename": f"{code}_{rev}.pdf",
            },
        }

    def action_preview_pdf(self):
        """Mở popup modal xem trước bản in PDF kỹ thuật trực tiếp trên Odoo."""
        self.ensure_one()
        view_id = self.env.ref("tdb_plm_custom.view_plm_attachment_preview_modal", raise_if_not_found=False)
        name_display = self.printout_name or self.engineering_code or self.name or str(self.id)
        return {
            "name": _("Xem trước Bản in PDF: %s") % name_display,
            "type": "ir.actions.act_window",
            "res_model": "ir.attachment",
            "res_id": self.id,
            "view_mode": "form",
            "views": [(view_id.id if view_id else False, "form")],
            "target": "new",
            "context": {"dialog_size": "extra-large"},
        }

    def action_download_printout(self):
        """Tải tệp bản in PDF kỹ thuật trực tiếp về máy trạm."""
        self.ensure_one()
        base_url = self.env["ir.config_parameter"].sudo().get_str("web.base.url")
        return {
            "type": "ir.actions.act_url",
            "url": f"{base_url}/plm/ir_attachment_printout/{self.id}?download=1",
            "target": "self",
        }

    # =========================================================================
    # 4. KIỂM SOÁT TOKEN 180 NGÀY CHO CAD CLIENT
    # =========================================================================
    def _check_cad_access_token(self):
        """Kiểm tra token thiết bị CAD tại tầng ORM:
        Chặn tuyệt đối mọi thao tác lưu bản vẽ, check-in, check-out từ SolidWorks nếu token bị thu hồi hoặc hết hạn.
        Bỏ qua hoàn toàn đối với các thao tác từ Web Browser.
        """
        if self.env.context.get('is_web_operation'):
            return

        try:
            from odoo.http import request
            if request and hasattr(request, 'httprequest'):
                path = getattr(request.httprequest, 'path', '') or ''
                if path.startswith('/web') or path.startswith('/odoo'):
                    return
        except Exception:
            pass

        if 'plm.desktop.token' in self.env.registry:
            token_model = self.env['plm.desktop.token'].sudo()
            tokens = token_model.search([('user_id', '=', self.env.uid)], order='id desc')
            if tokens:
                if any(t.is_revoked for t in tokens):
                    raise UserError(_("Thiết bị CAD đã bị Quản trị viên thu hồi quyền truy cập. Vui lòng liên hệ Quản trị viên."))
                now = fields.Datetime.now()
                active = [t for t in tokens if (not t.is_revoked) and t.expires_at and (t.expires_at > now)]
                if not active:
                    raise UserError(_("Phiên làm việc CAD 180 ngày đã hết hạn. Vui lòng bấm 'Execute New Login' từ SolidWorks để tiếp tục."))

    # =========================================================================
    # 4. THAO TÁC CHECK-OUT VÀ CHECK-IN
    # =========================================================================
    def _clean_invalid_checkouts(self):
        """Tự động dọn dẹp các bản ghi check-out không hợp lệ của tài liệu đã Ban hành / Chờ duyệt / Lỗi thời."""
        try:
            invalid_co = self.env['plm.checkout'].sudo().search([
                ('documentid.engineering_state', 'in', ['confirmed', 'released', 'obsoleted'])
            ])
            if invalid_co:
                _logger.info(f"[TDB PLM] Tự động giải phóng {len(invalid_co)} bản ghi checkout của tài liệu đã ban hành/chờ duyệt/lỗi thời.")
                invalid_co.with_context(skip_checkin_audit=True).unlink()
        except Exception as e:
            _logger.warning(f"[TDB PLM] Lỗi dọn dẹp invalid checkout: {e}")

    def canCheckOut(self, showError=False):
        """Mở rộng canCheckOut: Chỉ cho phép Check-out tài liệu khi ở trạng thái Dự thảo (draft).
        Tài liệu đã Ban hành (released), Chờ duyệt (confirmed) hoặc Lỗi thời (obsoleted) bị khóa tuyệt đối.
        Đồng thời chặn Check-out nếu tài liệu thuộc Cụm máy cha (Where Used) đã ở trạng thái Ban hành / Đóng băng.
        """
        for doc in self:
            if doc.engineering_state not in ['draft', False]:
                state_desc = "Đã ban hành" if doc.engineering_state == 'released' else ("Đang chờ duyệt" if doc.engineering_state == 'confirmed' else "Lỗi thời")
                msg = _("Tài liệu '%s' đang ở trạng thái %s (%s) nên không thể Check-out.") % (
                    doc.name or doc.engineering_code, state_desc, doc.engineering_state
                )
                if showError:
                    raise UserError(msg)
                return False, msg

            if doc.is_parent_frozen:
                msg = _("Tài liệu '%s' thuộc Cụm máy cha '%s' đã ở trạng thái Ban hành / Đóng băng nên không thể Check-out.") % (
                    doc.name or doc.engineering_code, doc.parent_released_name
                )
                if showError:
                    raise UserError(msg)
                return False, msg
        return super().canCheckOut(showError=showError)

    def canCheckOut1(self, host_name='', host_pws=''):
        """Kiểm tra quyền Check-out cho SolidWorks CAD Client:
        Tài liệu đã Ban hành (released), Chờ duyệt (confirmed), Lỗi thời (obsoleted) hoặc thuộc Cụm máy cha đã Ban hành:
        Phải trả về 'check_out_released' theo đúng chuẩn OdooPLM gốc.
        """
        self._clean_invalid_checkouts()
        for docBrws in self:
            if docBrws.engineering_state not in ['draft', False]:
                msg = _("Unable to check-Out document %s that is in state %s") % (
                    docBrws.name or docBrws.engineering_code, docBrws.engineering_state
                )
                return docBrws.id, 'check_out_released', msg

            if docBrws.is_parent_frozen:
                msg = _("Unable to check-Out document %s because parent assembly %s is released") % (
                    docBrws.name or docBrws.engineering_code, docBrws.parent_released_name
                )
                return docBrws.id, 'check_out_released', msg

            check_out, check_out_browser = docBrws.isCheckedOutByMeOr(host_name, host_pws)
            if check_out:
                msg = _("Unable to check-Out document %s that is already checked Out By %s") % (
                    docBrws.name or docBrws.engineering_code, docBrws.checkout_user
                )
                return docBrws.id, 'check_out_by_me', msg
            else:
                if check_out_browser:
                    msg = _("Unable to check-Out document %s that is already checked out by user %s on %s, %s") % (
                        docBrws.name or docBrws.engineering_code, docBrws.checkout_user, host_name, host_pws
                    )
                    return docBrws.id, 'check_out_by_user', msg
                else:
                    return docBrws.id, 'check_in', ''
        return False, 'not_found', 'No document found'

    def checkout(self, hostName, hostPws, showError=True, user_id=False):
        """Khóa tài liệu Check-out:
        Chặn đứng Check-out nếu tài liệu đã Ban hành (released), Chờ duyệt (confirmed), Lỗi thời (obsoleted)
        hoặc thuộc Cụm máy cha đã Ban hành.
        """
        self._check_cad_access_token()
        for doc in self:
            if doc.engineering_state not in ['draft', False]:
                state_desc = "Đã ban hành" if doc.engineering_state == 'released' else ("Đang chờ duyệt" if doc.engineering_state == 'confirmed' else "Lỗi thời")
                msg = _("Tài liệu '%s' đang ở trạng thái %s (%s) nên không thể Check-out.") % (
                    doc.name or doc.engineering_code, state_desc, doc.engineering_state
                )
                if showError:
                    raise UserError(msg)
                return False, msg

            if doc.is_parent_frozen:
                msg = _("Tài liệu '%s' thuộc Cụm máy cha '%s' đã ở trạng thái Ban hành / Đóng băng nên không thể Check-out.") % (
                    doc.name or doc.engineering_code, doc.parent_released_name
                )
                if showError:
                    raise UserError(msg)
                return False, msg

        if not user_id:
            user_id = self.env.uid

        return super().checkout(hostName, hostPws, showError=showError, user_id=user_id)

    def action_web_checkout(self):
        """Cho phép kỹ sư bấm Check-out trực tiếp trên Web Browser để lấy file."""
        self.ensure_one()
        if self.is_parent_frozen:
            raise UserError(_(
                "Tài liệu '%s' thuộc Cụm máy cha '%s' đã ở trạng thái Ban hành / Đóng băng. "
                "Không được phép Check-out trên Web!"
            ) % (self.name or self.engineering_code, self.parent_released_name))
        self.with_context(is_web_operation=True).checkout(f"Web ({self.env.user.name})", "web_browser", showError=True)
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }

    def _check_in(self):
        """Kế thừa _check_in:
        1. Kiểm tra quyền giữ Key độc quyền (chỉ người giữ Key mới được Check-in).
        2. Tự động lưu duy nhất 1 snapshot lịch sử phiên bản.
        3. Giải phóng bản ghi check-out của người dùng này (kể cả khi file_size == 0).
        Người giữ Key sau khi Check-in vẫn tiếp tục giữ quyền Key cho tài liệu này.
        """
        self._check_key_holder_permission()

        for doc in self:
            # 1. Tạo snapshot lịch sử phiên bản duy nhất
            doc._create_document_history_snapshot(
                description=_("Lưu khi Check-in bản vẽ"),
                user_id=self.env.user
            )

            # 2. Xóa bản ghi plm.checkout của chính người này để giải phóng trạng thái khóa
            checkout_records = self.env['plm.checkout'].sudo().search([
                ('documentid', '=', doc.id),
                ('userid', '=', self.env.user.id)
            ])
            if checkout_records:
                checkout_records.with_context(skip_checkin_audit=True).unlink()

            doc.write({'engineering_writable': False})

        # 3. Gọi super để PLM thực hiện các logic cơ sở khác (tránh bị chặn khi file_size <= 0)
        try:
            super()._check_in()
        except Exception as e:
            _logger.info(f"[TDB PLM] Base _check_in note: {e}")

        return True

    def action_web_checkin(self):
        """Cho phép kỹ sư giữ Key bấm Check-in trực tiếp trên Web Browser."""
        self.ensure_one()
        self._check_key_holder_permission()

        if not self.isCheckedOutByMe():
            self.with_context(is_web_operation=True).checkout(f"Web ({self.env.user.name})", "web_browser", showError=False)

        # _check_in() đã tự động tạo 1 snapshot duy nhất và xóa checkout
        self.with_context(is_web_operation=True)._check_in()

        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }

    def action_web_undo_checkout(self):
        """Hủy Check-out: Giải phóng phiên khóa Check-out của chính người dùng hiện tại mà không lưu thay đổi."""
        self.ensure_one()
        # Dọn dẹp lỗi dbthread cũ nếu có để không bị kẹt trạng thái has_error
        try:
            key = f"{self.engineering_code}_{self.engineering_revision}"
            threads = self.env['plm.dbthread'].sudo().search([('name', '=', key)])
            if threads:
                threads.write({'error_message': False})
        except Exception as e:
            _logger.debug(f"[TDB PLM] Clear dbthread error: {e}")

        checkout_records = self.env['plm.checkout'].sudo().search([
            ('documentid', '=', self.id),
            ('userid', '=', self.env.uid)
        ])
        if checkout_records:
            checkout_records.with_context(skip_error_check=True, skip_checkin_audit=True).unlink()
            self.write({'engineering_writable': False})
            self.message_post(
                body=Markup(_("<p>Hủy Check-out<br/>Người thực hiện: %s</p>")) % self.env.user.name,
                message_type='notification'
            )
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }

    def action_clear_error(self):
        """Xóa trạng thái lỗi lưu tệp (has_error / S.ERR) để mở khóa tài liệu."""
        self.ensure_one()
        try:
            key = f"{self.engineering_code}_{self.engineering_revision}"
            threads = self.env['plm.dbthread'].sudo().search([('name', '=', key)])
            if threads:
                threads.write({'error_message': False})
        except Exception as e:
            _logger.info(f"[TDB PLM] Clear dbthread error: {e}")
        self.invalidate_recordset(['has_error'])
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }

    # =========================================================================
    # 5. KHÓA LẤY KEY (LOCK), FORCE UNLOCK & BÀN GIAO KEY
    # =========================================================================
    def action_lock_key(self):
        """Khóa lấy Key (Lock): Kỹ sư/Quản trị viên nhận quyền giữ Key khi tài liệu chưa có ai giữ Key."""
        self.ensure_one()
        if self.engineering_state in ['released', 'obsoleted']:
            raise UserError(_("Tài liệu '%s' đã ở trạng thái Ban hành (%s). Không thể khóa lấy Key.") % (
                self.name or self.engineering_code, self.engineering_state
            ))
        if self.is_parent_frozen:
            raise UserError(_("Tài liệu '%s' thuộc Cụm máy cha '%s' đã ở trạng thái Ban hành / Đóng băng. Không thể khóa lấy Key.") % (
                self.name or self.engineering_code, self.parent_released_name
            ))
        if self.key_holder_id:
            raise UserError(_("Tài liệu '%s' hiện đã có người giữ Key (%s).") % (
                self.name or self.engineering_code, self.key_holder_id.name
            ))
        self.sudo().write({'key_holder_id': self.env.user.id})
        self.message_post(
            body=Markup(_(
                "<p>"
                "Khóa lấy Key (Lock)<br/>"
                "Kỹ sư nhận Key: %s<br/>"
                "Trạng thái: Đã nhận quyền giữ Key độc quyền (được phép Check-in cập nhật bản vẽ)."
                "</p>"
            )) % self.env.user.name,
            message_type='notification'
        )
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_force_unlock(self):
        """Phá khóa cưỡng chế / Chuyển giao Key dành cho Quản trị viên PLM hoặc Trưởng phòng."""
        return self.action_open_transfer_key_wizard()

    def action_open_transfer_key_wizard(self):
        """Mở popup chuyển giao quyền giữ Key (Force Unlock cho Admin khi không giữ Key, Unlock cho người giữ Key)."""
        self.ensure_one()
        if self.engineering_state in ['released', 'obsoleted']:
            raise UserError(_("Tài liệu '%s' đã ở trạng thái Ban hành (%s). Không thể chuyển giao hoặc mở khóa Key.") % (
                self.name or self.engineering_code, self.engineering_state
            ))
        if self.is_parent_frozen:
            raise UserError(_("Tài liệu '%s' thuộc Cụm máy cha '%s' đã ở trạng thái Ban hành / Đóng băng. Không thể chuyển giao hoặc mở khóa Key.") % (
                self.name or self.engineering_code, self.parent_released_name
            ))
        is_admin = self.env.user.has_group('plm.group_plm_admin') or self.env.user.has_group('base.group_system') or self.env.is_admin()
        is_holder = (self.key_holder_id == self.env.user)

        if not is_admin and not is_holder:
            raise UserError(_("Bạn không có quyền chuyển giao hoặc mở khóa Key. Chỉ Quản trị viên PLM hoặc người đang giữ Key mới được phép thực hiện."))

        title = _("Unlock") if is_holder else _("Force Unlock")
        return {
            'name': title,
            'type': 'ir.actions.act_window',
            'res_model': 'plm.transfer.key.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_document_id': self.id,
                'default_current_key_holder_id': self.key_holder_id.id if self.key_holder_id else False,
                'is_holder': is_holder,
            }
        }

    # =========================================================================
    # 6. CÁC HÀM CAD CHẶN LƯU, CHECK-IN & BẢO VỆ CLIENT COM
    # =========================================================================
    @api.model
    def _resolve_cad_doc(self, arg):
        """Hàm phụ trợ tìm bản ghi ir.attachment từ tham số dict/id/name từ CAD Client."""
        if not arg:
            return self.browse()
        if isinstance(arg, int):
            return self.browse(arg)
        if isinstance(arg, models.Model):
            return arg
        if isinstance(arg, dict):
            doc_id = arg.get("id") or arg.get("_id") or arg.get("documentID")
            if doc_id:
                doc = self.browse(doc_id)
                if doc.exists():
                    return doc
            eng_code = arg.get("engineering_code")
            doc_rev = arg.get("engineering_revision")
            doc_name = arg.get("name") or arg.get("datas_fname")
            if eng_code:
                domain = [("engineering_code", "=", eng_code)]
                if doc_rev is not None and doc_rev is not False:
                    domain.append(("engineering_revision", "=", doc_rev))
                doc = self.search(domain, limit=1)
                if doc:
                    return doc
                doc = self.search([("engineering_code", "=", eng_code)], limit=1)
                if doc:
                    return doc
            if doc_name:
                doc = self.search([("name", "=", doc_name)], limit=1)
                if doc:
                    return doc
                if "." in doc_name:
                    base_name = doc_name.rsplit(".", 1)[0]
                    doc = self.search([("engineering_code", "=", base_name)], limit=1)
                    if doc:
                        return doc
        return self.browse()

    @api.model
    def preCheckInRecursive_all(self, doc_props):
        """Chặn Check-in trước khi CAD client mở giao diện Check-in nếu tài liệu đã Ban hành hoặc không giữ Key."""
        self._check_cad_access_token()
        if doc_props and len(doc_props) > 0:
            dict_props = json.loads(doc_props[0]) if isinstance(doc_props[0], str) else doc_props[0]
            root_id = self._resolve_cad_doc(dict_props)
            if root_id:
                if root_id.engineering_state in ['confirmed', 'released', 'obsoleted']:
                    state_desc = "Đã ban hành" if root_id.engineering_state == 'released' else ("Đang chờ duyệt" if root_id.engineering_state == 'confirmed' else "Lỗi thời")
                    raise UserError(_(
                        "Tài liệu '%s' đang ở trạng thái %s (%s). "
                        "Không được phép Check-in hoặc cập nhật bản vẽ!"
                    ) % (root_id.name or root_id.engineering_code, state_desc, root_id.engineering_state))
                root_id._check_key_holder_permission()
        return super().preCheckInRecursive_all(doc_props)

    @api.model
    def preCheckInRecursive(self, doc_props, **kwargs):
        """Chặn Check-in đơn lẻ/đệ quy cũ nếu tài liệu đã Ban hành hoặc không giữ Key."""
        self._check_cad_access_token()
        if doc_props:
            dict_props = json.loads(doc_props) if isinstance(doc_props, str) else doc_props
            root_id = self._resolve_cad_doc(dict_props)
            if root_id:
                if root_id.engineering_state in ['confirmed', 'released', 'obsoleted']:
                    state_desc = "Đã ban hành" if root_id.engineering_state == 'released' else ("Đang chờ duyệt" if root_id.engineering_state == 'confirmed' else "Lỗi thời")
                    raise UserError(_(
                        "Tài liệu '%s' đang ở trạng thái %s (%s). "
                        "Không được phép Check-in hoặc cập nhật bản vẽ!"
                    ) % (root_id.name or root_id.engineering_code, state_desc, root_id.engineering_state))
                root_id._check_key_holder_permission()
        return super().preCheckInRecursive(doc_props, **kwargs)

    @api.model
    def SaveOrUpdate(self, documents, default=None):
        """Lưu / Cập nhật tài liệu CAD:
        Bảo vệ tài liệu đã Ban hành (released), Chờ duyệt (confirmed), Lỗi thời (obsoleted):
        Tuyệt đối không ghi đè, đặt hasSaved = False, hasUpdated = False để SolidWorks không lưu lại.
        """
        self._check_cad_access_token()
        self._clean_invalid_checkouts()
        writable_docs = []
        readonly_docs = []
        for doc_vals in (documents or []):
            doc = self._resolve_cad_doc(doc_vals)
            if doc and doc.engineering_state in ['confirmed', 'released', 'obsoleted']:
                doc_copy = dict(doc_vals)
                doc_copy['documentID'] = doc.id
                doc_copy['hasSaved'] = False
                doc_copy['hasUpdated'] = False
                readonly_docs.append(doc_copy)
            else:
                writable_docs.append(doc_vals)

        res = []
        if writable_docs:
            res = super().SaveOrUpdate(writable_docs, default=default)
        res.extend(readonly_docs)
        return res

    @api.model
    def CheckSaveUpdate(self, documents, default=None):
        """Kiểm tra lưu tài liệu CAD:
        Đối với tài liệu đã Ban hành (released), Chờ duyệt (confirmed), Lỗi thời (obsoleted):
        Tuyệt đối đặt hasSaved = False (không được phép lưu/ghi đè), không đánh dấu xanh trong Multi Edit Parts!
        """
        self._check_cad_access_token()
        self._clean_invalid_checkouts()
        res = super().CheckSaveUpdate(documents, default=default)
        for doc_dict in res:
            doc = self._resolve_cad_doc(doc_dict)
            if doc and doc.engineering_state in ['confirmed', 'released', 'obsoleted']:
                doc_dict['hasSaved'] = False
        return res

    @api.model
    def CheckIn(self, attrs):
        """Chặn Check-in đơn lẻ khi tài liệu đã Ban hành hoặc không giữ Key hoặc token hết hạn."""
        self._check_cad_access_token()
        doc = self._resolve_cad_doc(attrs)
        if doc:
            if doc.engineering_state in ['confirmed', 'released', 'obsoleted']:
                state_desc = "Đã ban hành" if doc.engineering_state == 'released' else ("Đang chờ duyệt" if doc.engineering_state == 'confirmed' else "Lỗi thời")
                raise UserError(_(
                    "Tài liệu '%s' đang ở trạng thái %s (%s). "
                    "Không được phép Check-in hoặc cập nhật bản vẽ!"
                ) % (doc.name or doc.engineering_code, state_desc, doc.engineering_state))

            if doc.is_parent_frozen:
                raise UserError(_(
                    "Tài liệu '%s' thuộc Cụm máy cha '%s' đã ở trạng thái Ban hành / Đóng băng. "
                    "Không được phép Check-in hoặc cập nhật bản vẽ!"
                ) % (doc.name or doc.engineering_code, doc.parent_released_name))
            doc._check_key_holder_permission()
        return super().CheckIn(attrs)

    @api.model
    def CheckInRecursive(self, request, default=None):
        """Chặn Check-in đệ quy khi tài liệu đã Ban hành/Chờ duyệt hoặc không giữ Key hoặc token hết hạn."""
        self._check_cad_access_token()

        target_doc = False
        if request and isinstance(request, (list, tuple)) and len(request) > 0:
            target_doc = self._resolve_cad_doc(request[0])

        if target_doc:
            if target_doc.engineering_state in ['confirmed', 'released', 'obsoleted']:
                state_desc = "Đã ban hành" if target_doc.engineering_state == 'released' else ("Đang chờ duyệt" if target_doc.engineering_state == 'confirmed' else "Lỗi thời")
                raise UserError(_(
                    "Tài liệu '%s' đang ở trạng thái %s (%s). "
                    "Không được phép Check-in hoặc cập nhật bản vẽ!"
                ) % (target_doc.name or target_doc.engineering_code, state_desc, target_doc.engineering_state))
            target_doc._check_key_holder_permission()

            # Đảm bảo bản ghi checkout tồn tại cho user này để tránh lỗi COM SolidWorks
            if not target_doc.isCheckedOutByMe():
                doc_args = request[0] if isinstance(request[0], dict) else {}
                host_name = doc_args.get("hostname") or "SolidWorks CAD"
                target_doc.checkout(host_name, "pws", showError=False)

            try:
                doc_tree_ids = self.getRelatedAllLevelDocumentsTree(target_doc)
                for rel_doc in self.browse(doc_tree_ids):
                    if rel_doc.engineering_state in ['confirmed', 'released', 'obsoleted']:
                        state_desc = "Đã ban hành" if rel_doc.engineering_state == 'released' else ("Đang chờ duyệt" if rel_doc.engineering_state == 'confirmed' else "Lỗi thời")
                        raise UserError(_(
                            "Tài liệu '%s' (trong cây lắp ráp) đang ở trạng thái %s (%s). "
                            "Không được phép Check-in hoặc cập nhật bản vẽ!"
                        ) % (rel_doc.name or rel_doc.engineering_code, state_desc, rel_doc.engineering_state))
                    rel_doc._check_key_holder_permission()
            except UserError:
                raise
            except Exception as e:
                _logger.warning(f"[TDB PLM] Kiểm tra cây tài liệu CheckInRecursive: {e}")

            target_doc._create_document_history_snapshot(
                description=_("Check-in từ SolidWorks CAD"),
                user_id=self.env.user
            )

        res = super().CheckInRecursive(request, default=default)
        if res is False:
            return []
        return res

    @api.model
    def preCheckOutRecursive(self, comp_vals=""):
        """Bảo vệ preCheckOutRecursive khỏi lỗi JSON rỗng hoặc không hợp lệ từ SolidWorks."""
        self._check_cad_access_token()
        if not comp_vals or (isinstance(comp_vals, str) and not comp_vals.strip()):
            return json.dumps([])
        if isinstance(comp_vals, (list, dict)):
            comp_vals = json.dumps(comp_vals)
        try:
            return super().preCheckOutRecursive(comp_vals)
        except Exception as e:
            _logger.warning(f"[TDB PLM] preCheckOutRecursive warning: {e}")
            return json.dumps([])

    @api.model
    def CheckOutRecursive(self, structure="", pws_path="", hostname="", force=False):
        """Kế thừa CheckOutRecursive:
        1. Kiểm tra Access Token CAD SolidWorks.
        2. Bẫy lỗi JSONDecodeError khi SolidWorks truyền structure trống hoặc không hợp lệ.
        3. Tự động phục hồi thông tin Part đơn lẻ từ pws_path nếu SolidWorks không truyền structure.
        4. Đảm bảo bản ghi plm.checkout được tạo thành công trên hệ thống.
        """
        self._check_cad_access_token()

        parsed_structure = []
        if structure and isinstance(structure, str) and structure.strip():
            try:
                loaded = json.loads(structure)
                if isinstance(loaded, list):
                    parsed_structure = loaded
                elif isinstance(loaded, dict):
                    parsed_structure = [loaded]
            except Exception as e:
                _logger.warning(f"[TDB PLM] CheckOutRecursive parse warning: {structure!r} - {e}")
        elif isinstance(structure, list):
            parsed_structure = structure
        elif isinstance(structure, dict):
            parsed_structure = [structure]

        # Nếu structure rỗng nhưng có pws_path (trường hợp SolidWorks mở part hoặc assembly đơn lẻ)
        if not parsed_structure and pws_path:
            file_name = os.path.basename(pws_path)
            base_code = os.path.splitext(file_name)[0]
            clean_code = base_code.split(' (')[0].strip()

            target_doc = self.search([
                ('is_plm', '=', True),
                '|', '|', '|',
                ('name', '=', file_name),
                ('engineering_code', '=', base_code),
                ('engineering_code', '=', clean_code),
                ('name', 'ilike', clean_code)
            ], order="engineering_revision desc, id desc", limit=1)

            if target_doc:
                parsed_structure = [{
                    'engineering_code': target_doc.engineering_code or base_code,
                    'engineering_revision': target_doc.engineering_revision or 0,
                    'name': target_doc.name or file_name,
                    'checkout': True,
                    'err_msg': ''
                }]
                _logger.info(f"[TDB PLM] CheckOutRecursive: Tự động phục hồi checkout cho file từ pws_path: {target_doc.name}")

        if not parsed_structure:
            _logger.info(f"[TDB PLM] CheckOutRecursive: Structure rỗng và không tìm thấy file từ pws_path={pws_path!r}")
            return json.dumps([])

        # Thực hiện check-out trực tiếp cho từng tài liệu và đồng bộ trạng thái
        for item in parsed_structure:
            eng_code = item.get('engineering_code')
            eng_rev = item.get('engineering_revision', 0)
            doc_name = item.get('name')

            docs = self.search([
                ('is_plm', '=', True),
                ('engineering_code', '=', eng_code),
                ('engineering_revision', '=', eng_rev)
            ])
            if not docs and doc_name:
                docs = self.search([
                    ('is_plm', '=', True),
                    ('name', '=', doc_name)
                ])

            for doc in docs:
                if doc.engineering_state not in ['draft', False]:
                    item['checkout'] = False
                    item['err_msg'] = _("Unable to check-Out document %s that is in state %s") % (doc.name or doc.engineering_code, doc.engineering_state)
                    continue

                isCheckoutByMe, _checkoutUser = doc.checkoutByMeWithUser()
                if not isCheckoutByMe:
                    checkout_id, msg = doc.checkout(hostname, pws_path, showError=False)
                    if checkout_id:
                        doc.setupCadOpen(hostname, pws_path, operation_type="check-out")
                        item['checkout'] = True
                        item['err_msg'] = ''
                        _logger.info(f"[TDB PLM] CheckOutRecursive: Đã checkout thành công doc {doc.name} cho user {self.env.user.name}")
                    else:
                        item['checkout'] = False
                        item['err_msg'] = msg or 'Check-out failed'
                        _logger.warning(f"[TDB PLM] CheckOutRecursive: Không thể checkout {doc.name}: {msg}")
                else:
                    doc.setupCadOpen(hostname, pws_path, operation_type="check-out")
                    item['checkout'] = True
                    item['err_msg'] = ''

        return json.dumps(parsed_structure)

    def saveStructure(self, arguments):
        """Chặn lưu cấu trúc lắp ráp CAD khi token hết hạn."""
        self._check_cad_access_token()
        return super().saveStructure(arguments)

    def _commonWFAction(self, writable, state, check):
        """Đồng bộ trạng thái linh kiện liên kết và tài liệu con khi tài liệu chuyển trạng thái workflow."""
        res = super()._commonWFAction(writable, state, check)
        for doc in self:
            if getattr(doc, 'is_plm', False):
                # 1. Đồng bộ linh kiện liên kết
                if hasattr(doc, 'linkedcomponents') and doc.linkedcomponents:
                    for comp in doc.linkedcomponents:
                        if comp.engineering_state != state:
                            try:
                                comp.sudo().with_context(skip_released_freeze=True, odooPLM=False).write({'engineering_state': state})
                            except Exception as e:
                                _logger.debug(f"[TDB PLM] Sync component state on workflow: {e}")
                # 2. Đồng bộ các tài liệu con (HiTree) khi cụm cha được xác nhận hoặc ban hành
                if state in ['confirmed', 'released', 'obsoleted']:
                    child_docs = doc.get_child_documents()
                    for child in child_docs:
                        if child.engineering_state != state:
                            try:
                                child.sudo().with_context(skip_released_freeze=True, odooPLM=False).write({'engineering_state': state})
                            except Exception as e:
                                _logger.debug(f"[TDB PLM] Sync child doc state on workflow: {e}")
        return res

    @api.model
    def createFromProps(self, documentAttribute={}, dbThread=False, hostName=False, hostPws=False):
        """Chặn sửa thuộc tính tài liệu từ hộp thoại Properties của SolidWorks nếu đã Ban hành / Chờ duyệt."""
        self._check_cad_access_token()
        eng_code = documentAttribute.get("engineering_code", False)
        eng_rev = documentAttribute.get("engineering_revision", 0)
        try:
            eng_rev_int = int(eng_rev) if eng_rev is not None else 0
        except (ValueError, TypeError):
            eng_rev_int = 0

        target_doc = False
        if eng_code:
            target_doc = self.search([
                ("engineering_code", "=", eng_code),
                ("engineering_revision", "=", eng_rev_int),
                ("is_plm", "=", True)
            ], limit=1)
            if not target_doc:
                target_doc = self.search([
                    ("engineering_code", "=", eng_code),
                    ("is_plm", "=", True)
                ], order="engineering_revision desc, id desc", limit=1)

        if not target_doc:
            doc_name = documentAttribute.get("name") or documentAttribute.get("KEY", "")
            if doc_name:
                target_doc = self.search([
                    ("name", "=", doc_name),
                    ("is_plm", "=", True)
                ], order="engineering_revision desc, id desc", limit=1)

        if target_doc and target_doc.engineering_state in ['confirmed', 'released', 'obsoleted']:
            state_desc = "Đã ban hành" if target_doc.engineering_state == 'released' else ("Đang chờ duyệt" if target_doc.engineering_state == 'confirmed' else "Lỗi thời")
            raise UserError(_(
                "Tài liệu '%s' đang ở trạng thái %s (%s). "
                "Không được phép chỉnh sửa thuộc tính từ SolidWorks!"
            ) % (target_doc.name or target_doc.engineering_code, state_desc, target_doc.engineering_state))

        return super().createFromProps(documentAttribute=documentAttribute, dbThread=dbThread, hostName=hostName, hostPws=hostPws)

    @api.model
    def saveSingleLevel(self, clientArg):
        """Chặn lưu bản vẽ đơn cấp khi token hết hạn."""
        self._check_cad_access_token()
        return super().saveSingleLevel(clientArg)

    @api.model
    def CheckIn2(self, request, default=None, force=False):
        """Chặn CheckIn2 khi tài liệu đã Ban hành hoặc không giữ Key hoặc token hết hạn."""
        self._check_cad_access_token()
        if request and isinstance(request, (list, tuple)) and len(request) > 0:
            doc = self._resolve_cad_doc(request[0])
            if doc:
                if doc.engineering_state in ['confirmed', 'released', 'obsoleted']:
                    state_desc = "Đã ban hành" if doc.engineering_state == 'released' else ("Đang chờ duyệt" if doc.engineering_state == 'confirmed' else "Lỗi thời")
                    raise UserError(_(
                        "Tài liệu '%s' đang ở trạng thái %s (%s). "
                        "Không được phép Check-in hoặc cập nhật bản vẽ!"
                    ) % (doc.name or doc.engineering_code, state_desc, doc.engineering_state))
                doc._check_key_holder_permission()
        return super().CheckIn2(request, default=default, force=force)

    @api.model
    def CheckInRecursive2(self, involved_docs_dict, **kargs):
        """Chặn CheckInRecursive2 khi tài liệu đã Ban hành hoặc không giữ Key hoặc token hết hạn."""
        self._check_cad_access_token()
        dict_data = json.loads(involved_docs_dict) if isinstance(involved_docs_dict, str) else (involved_docs_dict or {})
        for doc_vals in dict_data.get("to_check_in", []):
            checked = doc_vals.get("checked", False)
            if checked or kargs.get("force", False):
                doc_id = self.getDocId(doc_vals) or self._resolve_cad_doc(doc_vals)
                if doc_id:
                    if doc_id.engineering_state in ['confirmed', 'released', 'obsoleted']:
                        state_desc = "Đã ban hành" if doc_id.engineering_state == 'released' else ("Đang chờ duyệt" if doc_id.engineering_state == 'confirmed' else "Lỗi thời")
                        raise UserError(_(
                            "Tài liệu '%s' đang ở trạng thái %s (%s). "
                            "Không được phép Check-in hoặc cập nhật bản vẽ!"
                        ) % (doc_id.name or doc_id.engineering_code, state_desc, doc_id.engineering_state))
                    doc_id._check_key_holder_permission()
                    try:
                        doc_id._create_document_history_snapshot(
                            description=_("Check-in từ SolidWorks CAD (v2)"),
                            user_id=self.env.user
                        )
                    except Exception as e:
                        _logger.warning(f"[TDB PLM] Lỗi khi tạo snapshot CheckInRecursive2: {e}")

        return super().CheckInRecursive2(involved_docs_dict, **kargs)

    @api.model_create_multi
    def create(self, vals_list):
        """Tự động thiết lập người tạo đầu tiên làm Người giữ Key (chỉ cho tài liệu PLM)."""
        if self.env.context.get('odooPLM'):
            self._check_cad_access_token()
        for vals in vals_list:
            is_plm_doc = vals.get('is_plm') or self.env.context.get('odooPLM') or self.env.context.get('default_is_plm')
            if is_plm_doc and not vals.get('key_holder_id'):
                vals['key_holder_id'] = self.env.user.id
        return super().create(vals_list)

    def write(self, vals):
        """Kế thừa write của ir.attachment:
        1. Kiểm tra quyền giữ Key nếu có thay đổi nội dung file (chỉ áp dụng cho tài liệu PLM).
        2. Tự động lưu snapshot phiên bản cũ vào plm.document.history khi nội dung file thay đổi.
        3. Chặn cập nhật từ CAD khi token hết hạn.
        4. Đóng băng tài liệu khi đã Ban hành (released/obsoleted): không cho sửa tên, mã, hoặc tệp.
        """
        # Chặn sửa tên tài liệu, mã kỹ thuật hoặc tệp CAD khi tài liệu đã ban hành (released) hoặc obsoleted
        restricted_fields = {'name', 'engineering_code', 'raw', 'datas', 'db_datas', 'store_fname'}
        attempted_restricted = restricted_fields.intersection(vals.keys())
        if attempted_restricted and not self.env.context.get('skip_released_freeze'):
            for doc in self:
                if getattr(doc, 'is_plm', False) and doc.engineering_state in ['released', 'obsoleted']:
                    # Cho phép nếu là thao tác chuyển đổi trạng thái khỏi ban hành
                    if 'engineering_state' in vals and vals['engineering_state'] not in ['released', 'obsoleted']:
                        continue
                    raise UserError(_(
                        "Tài liệu '%s' đã ở trạng thái Ban hành (%s). "
                        "Không được phép thay đổi tên tài liệu, mã kỹ thuật hoặc tệp bản vẽ!"
                    ) % (doc.name or doc.engineering_code, doc.engineering_state))

        has_file_change = ('raw' in vals or 'datas' in vals or 'db_datas' in vals)

        if has_file_change and not self.env.context.get('skip_key_holder_check'):
            for doc in self:
                # BẮT BUỘC: Chỉ kiểm tra quyền giữ Key đối với tài liệu PLM (is_plm = True)
                # Bỏ qua hoàn toàn các file hệ thống (favicon.ico, logo công ty, tệp đính kèm chatter,...)
                if not getattr(doc, 'is_plm', False):
                    continue
                if not doc.key_holder_id:
                    raise UserError(_(
                        "Tài liệu '%s' hiện chưa có ai giữ Key. "
                        "Vui lòng bấm nút 'Lock' để nhận quyền giữ Key trước khi cập nhật tệp/Check-in!"
                    ) % (doc.name or doc.engineering_code))
                if doc.key_holder_id != self.env.user:
                    key_holder_name = doc.key_holder_id.name
                    raise UserError(_(
                        "Tài liệu '%s' hiện do kỹ sư '%s' giữ Key. "
                        "Chỉ người giữ Key mới có quyền cập nhật tệp lên hệ thống. "
                        "Vui lòng liên hệ kỹ sư '%s' hoặc Quản trị viên để chuyển giao Key."
                    ) % (doc.name or doc.engineering_code, key_holder_name, key_holder_name))

        if has_file_change and not self.env.context.get('skip_document_history'):
            if 'plm.document.history' in self.env:
                history_desc = vals.pop('history_description', None)
                for doc in self:
                    if getattr(doc, 'is_plm', False):
                        prev_user = doc.write_uid or doc.create_uid or self.env.user
                        doc._create_document_history_snapshot(
                            description=history_desc or _("Lưu bản vẽ trước khi cập nhật"),
                            user_id=prev_user
                        )

        if self.env.context.get('odooPLM'):
            self._check_cad_access_token()

        return super().write(vals)

    # =========================================================================
    # 7. CHUẨN HÓA NHẬT KÝ CHATTER CHO CHECK-OUT & CHECK-IN
    # =========================================================================
    def message_post(self, **kwargs):
        """Chuẩn hóa nhật ký Chatter cho các sự kiện Check-out và Check-in:
        Ghi rõ người thực hiện, máy trạm bằng văn bản thuần ERP (không icon, không bullet).
        """
        body = kwargs.get('body', '')
        body_str = str(body or '').strip()

        if 'Checked-Out ID' in body_str:
            user_name = self.env.user.name
            hostname = ''
            if self.checkout_ids:
                co = self.checkout_ids[0]
                if co.userid:
                    user_name = co.userid.name
                if co.hostname:
                    hostname = co.hostname
            if not hostname:
                hostname = self.env.context.get('hostname') or 'CAD Client'

            kwargs['body'] = Markup(_(
                "<p>"
                "Khóa tài liệu (Check-out)<br/>"
                "Người thực hiện: %s<br/>"
                "Máy trạm: %s"
                "</p>"
            )) % (user_name, hostname)

        elif body_str == 'Checked-In' or body_str == _('Checked-In'):
            if self.env.context.get('skip_checkin_audit'):
                return self.env['mail.message']
            user_name = self.env.user.name
            kwargs['body'] = Markup(_(
                "<p>"
                "Mở khóa tài liệu (Check-in)<br/>"
                "Người thực hiện: %s"
                "</p>"
            )) % user_name

        return super().message_post(**kwargs)

    # =========================================================================
    # 8. ĐỒNG BỘ TỪ BẢN VẼ CAD SANG SẢN PHẨM & ĐỊNH MỨC VẬT TƯ BOM (HT-2.1 & HT-2.2)
    # =========================================================================
    def _clean_product_name(self):
        """Lấy tên hiển thị sạch của sản phẩm từ tài liệu CAD (loại bỏ phần mở rộng CAD nếu có)."""
        self.ensure_one()
        name = (self.name or "").strip()
        cad_exts = [
            ".sldprt", ".sldasm", ".slddrw",
            ".prt", ".asm", ".drw",
            ".step", ".stp", ".iges", ".igs",
            ".dwg", ".dxf", ".pdf"
        ]
        lower_name = name.lower()
        for ext in cad_exts:
            if lower_name.endswith(ext):
                name = name[:-len(ext)].strip()
                break
        return name or self.engineering_code or self.name or _("Linh kiện PLM")

    def _get_or_create_related_product(self):
        """Ánh xạ hoặc khởi tạo sản phẩm product.product từ tài liệu CAD:
        - Đối soát theo default_code (engineering_code hoặc name hoặc clean_name).
        - Nếu chưa có, tự động tạo mới product.product chuẩn lưu kho (type='consu', is_storable=True),
          gán mã kỹ thuật, revision, trạng thái engineering, và thumbnail 3D (image_1920 = self.preview).
        - Thiết lập liên kết 2 chiều thông qua linkedcomponents (plm_component_document_rel).
        """
        self.ensure_one()
        # 1. Nếu đã có sản phẩm trong linkedcomponents, kiểm tra đồng bộ ảnh preview và trả về
        if self.linkedcomponents:
            product = self.linkedcomponents[0]
            if self.preview and not product.image_1920:
                try:
                    product.sudo().write({"image_1920": self.preview})
                except Exception as e:
                    _logger.debug(f"[TDB PLM] Không thể cập nhật ảnh preview sản phẩm: {e}")
            return product

        Product = self.env["product.product"].sudo()
        clean_name = self._clean_product_name()
        code_candidates = [
            c for c in [self.engineering_code, self.name, clean_name] if c
        ]

        # 2. Tìm kiếm sản phẩm đối soát theo mã kỹ thuật hoặc default_code
        product = False
        for code in code_candidates:
            product = Product.search([("default_code", "=", code)], limit=1)
            if product:
                break
            if hasattr(Product, "engineering_code"):
                product = Product.search([("engineering_code", "=", code)], limit=1)
                if product:
                    break

        # 3. Nếu chưa có, tạo mới sản phẩm lưu kho chuẩn Odoo 20
        if not product:
            primary_code = self.engineering_code or self.name or clean_name
            prod_vals = {
                "name": clean_name or primary_code,
                "default_code": primary_code,
                "type": "consu",
                "is_storable": True,
            }
            if hasattr(Product, "engineering_code") and self.engineering_code:
                prod_vals["engineering_code"] = self.engineering_code
            if hasattr(Product, "engineering_revision"):
                prod_vals["engineering_revision"] = self.engineering_revision or 0
            if hasattr(Product, "engineering_state"):
                prod_vals["engineering_state"] = self.engineering_state or "draft"
            if self.preview:
                prod_vals["image_1920"] = self.preview

            product = Product.create(prod_vals)

        # 4. Thiết lập liên kết 2 chiều vào linkedcomponents
        if product not in self.linkedcomponents:
            self.sudo().write({"linkedcomponents": [(4, product.id)]})

        return product

    def action_sync_cad_bom_to_mrp(self):
        """Tự động hóa 1-Click: Từ Cụm Assembly CAD (3D) đồng bộ toàn bộ Part/Assembly thành
        Sản phẩm (product.product) và dựng cây định mức vật tư (mrp.bom & mrp.bom.line).
        - Kiểm tra điều kiện Cụm lắp ráp 3D và quan hệ chi tiết con.
        - Khởi tạo Sản phẩm cha và BOM Header (mrp.bom) có source_id trỏ về Assembly CAD.
        - Bóc tách toàn bộ chi tiết con, khởi tạo linh kiện con, gom nhóm số lượng.
        - Dọn sạch dòng BoM cũ (tránh trùng lặp khi đồng bộ lại) và tạo các mrp.bom.line mới.
        - Ghi nhận Audit Trail vào Chatter và mở Form view của BOM vừa tạo.
        """
        self.ensure_one()

        # 1. Kiểm tra điều kiện tài liệu CAD
        if self.document_type != "3d":
            raise UserError(_(
                "Tính năng Đồng bộ BOM sang Sản xuất chỉ áp dụng cho tài liệu Mô hình 3D (Assembly/Part).\n"
                "Tài liệu '%s' có kiểu là: %s."
            ) % (self.name or self.engineering_code, self.document_type))

        # Lấy các quan hệ chi tiết con (loại bỏ quan hệ bản vẽ 2D LyTree và self-reference)
        child_relations = self.child_relation_ids.filtered(
            lambda r: r.link_kind != "LyTree" and r.child_id and r.child_id.id != self.id
        )
        if not child_relations:
            raise UserError(_(
                "Tài liệu Cụm lắp ráp '%s' hiện chưa có chi tiết con nào trong cây cấu trúc (Child Documents).\n"
                "Vui lòng kiểm tra lại cấu trúc tham chiếu CAD trước khi đồng bộ BOM."
            ) % (self.name or self.engineering_code))

        # 2. Khởi tạo / Lấy Sản phẩm đại diện Cụm cha
        parent_product = self._get_or_create_related_product()

        # 3. Tìm hoặc Tạo mới BOM Header (mrp.bom)
        Bom = self.env["mrp.bom"].sudo()
        bom = Bom.search([("source_id", "=", self.id)], limit=1)
        if not bom and parent_product:
            bom = Bom.search([("product_id", "=", parent_product.id)], limit=1)

        bom_vals = {
            "product_tmpl_id": parent_product.product_tmpl_id.id,
            "product_id": parent_product.id,
            "product_qty": 1.0,
            "type": "normal",
            "source_id": self.id,
            "code": self.engineering_code or self.name,
        }
        if not bom:
            bom = Bom.create(bom_vals)
        else:
            update_vals = {}
            if not bom.source_id:
                update_vals["source_id"] = self.id
            if not bom.product_id:
                update_vals["product_id"] = parent_product.id
            if update_vals:
                bom.write(update_vals)

        # 4. Gom nhóm số lượng chi tiết con (Aggregation)
        components = {}
        for rel in child_relations:
            child_doc = rel.child_id
            if not child_doc.exists():
                continue
            child_prod = child_doc._get_or_create_related_product()
            if child_prod.id not in components:
                components[child_prod.id] = {
                    "product": child_prod,
                    "source_doc": child_doc,
                    "qty": 1.0,
                }
            else:
                components[child_prod.id]["qty"] += 1.0

        if not components:
            raise UserError(_("Không tìm thấy linh kiện con hợp lệ để tạo Định mức vật tư (BOM)."))

        # 5. Cập nhật các dòng định mức (mrp.bom.line):
        # Thu thập các dòng BOM hiện có để bảo toàn số lượng thực tế đã được xác định trước đó
        # (ví dụ: số lượng chính xác 4 bulông, 2 thanh giằng do SolidWorks Add-in Check-in đẩy lên).
        existing_lines_by_prod = {}
        existing_lines_by_source = {}
        for line in bom.bom_line_ids:
            if line.product_id:
                existing_lines_by_prod[line.product_id.id] = line
            if line.source_id:
                existing_lines_by_source[line.source_id.id] = line

        BomLine = self.env["mrp.bom.line"].sudo().with_context(skip_line_audit=True)
        active_line_ids = set()

        for item in components.values():
            child_prod = item["product"]
            child_doc = item["source_doc"]

            # Ưu tiên tìm dòng BOM đã có sẵn theo sản phẩm hoặc tài liệu nguồn CAD
            existing_line = existing_lines_by_prod.get(child_prod.id) or existing_lines_by_source.get(child_doc.id)

            if existing_line and existing_line.exists():
                # Bảo toàn số lượng thực tế đã có từ trước (SolidWorks Add-in hoặc người dùng tinh chỉnh)
                # Chỉ lấy số lượng mặc định 1.0 nếu số lượng cũ không hợp lệ hoặc <= 0
                preserved_qty = existing_line.product_qty if existing_line.product_qty > 0 else item["qty"]
                vals_to_update = {}
                if existing_line.product_id.id != child_prod.id:
                    vals_to_update["product_id"] = child_prod.id
                if not existing_line.source_id or existing_line.source_id.id != child_doc.id:
                    vals_to_update["source_id"] = child_doc.id
                if existing_line.product_qty != preserved_qty:
                    vals_to_update["product_qty"] = preserved_qty
                if vals_to_update:
                    existing_line.with_context(skip_line_audit=True).write(vals_to_update)
                active_line_ids.add(existing_line.id)
            else:
                # Chi tiết con mới được bổ sung vào cây CAD: tạo mới dòng định mức
                line_vals = {
                    "bom_id": bom.id,
                    "product_id": child_prod.id,
                    "product_qty": item["qty"],
                    "source_id": child_doc.id,
                    "type": "normal",
                }
                if hasattr(BomLine, "uom_id"):
                    line_vals["uom_id"] = child_prod.uom_id.id
                new_line = BomLine.create(line_vals)
                active_line_ids.add(new_line.id)

        # Dọn dẹp các dòng BOM cũ không còn tồn tại trong cấu trúc CAD hiện tại (kèm cờ skip_line_audit chống spam log)
        obsolete_lines = bom.bom_line_ids.filtered(lambda l: l.id not in active_line_ids)
        if obsolete_lines:
            obsolete_lines.with_context(skip_line_audit=True).unlink()

        # 6. Ghi nhận Audit Trail vào Chatter của cả ir.attachment và mrp.bom
        summary_msg = Markup(_(
            "<p>"
            "<strong>Đồng bộ thành công Định mức vật tư BOM từ bản vẽ CAD</strong><br/>"
            "Mã BOM: <strong>%s</strong> (ID: %s)<br/>"
            "Sản phẩm Cụm cha: <strong>[%s] %s</strong><br/>"
            "Tổng số lượng linh kiện con: <strong>%s</strong> loại chi tiết "
            "(Đã bảo toàn số lượng thực tế từ SolidWorks CAD / BOM)."
            "</p>"
        )) % (
            bom.display_name or bom.code or str(bom.id),
            bom.id,
            parent_product.default_code or '',
            parent_product.name,
            len(components)
        )
        self.sudo().message_post(body=summary_msg)
        bom.sudo().message_post(body=summary_msg)

        # 7. Mở trực tiếp Form view của BOM vừa tạo
        return {
            "type": "ir.actions.act_window",
            "name": _("Định mức vật tư (BOM)"),
            "res_model": "mrp.bom",
            "view_mode": "form",
            "res_id": bom.id,
            "target": "current",
        }

    def action_view_linked_products(self):
        """Mở xem Form/Tree của sản phẩm (product.product) liên kết với tài liệu CAD này.
        Nếu tài liệu chưa liên kết sản phẩm, tự động tạo/liên kết sản phẩm tương ứng.
        """
        self.ensure_one()
        if not self.linkedcomponents:
            self._get_or_create_related_product()

        products = self.linkedcomponents
        action = {
            "type": "ir.actions.act_window",
            "name": _("Sản phẩm linh kiện kỹ thuật"),
            "res_model": "product.product",
            "target": "current",
        }
        if len(products) == 1:
            action.update({
                "view_mode": "form",
                "res_id": products.id,
            })
        else:
            action.update({
                "view_mode": "list,form",
                "domain": [("id", "in", products.ids)],
            })
        return action

    def action_view_mrp_boms(self):
        """Mở xem Định mức vật tư (mrp.bom) liên kết với tài liệu CAD này.
        Nếu tài liệu là Cụm 3D có chi tiết con và chưa có BOM, tự động kích hoạt đồng bộ BOM.
        """
        self.ensure_one()
        boms = self._get_related_boms()
        if not boms:
            if self.document_type == "3d" and self.child_relation_ids.filtered(lambda r: r.link_kind != 'LyTree' and r.child_id and r.child_id.id != self.id):
                return self.action_sync_cad_bom_to_mrp()
            else:
                raise UserError(_("Tài liệu '%s' hiện chưa có Định mức vật tư (BOM) nào được liên kết.") % (self.name or self.engineering_code))

        action = {
            "type": "ir.actions.act_window",
            "name": _("Định mức vật tư (BOM)"),
            "res_model": "mrp.bom",
            "target": "current",
        }
        if len(boms) == 1:
            action.update({
                "view_mode": "form",
                "res_id": boms.id,
            })
        else:
            action.update({
                "view_mode": "list,form",
                "domain": [("id", "in", boms.ids)],
            })
        return action
