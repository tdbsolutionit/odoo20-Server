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

            if doc.engineering_state in ['released', 'obsoleted']:
                raise UserError(_(
                    "Tài liệu '%s' đã ở trạng thái Ban hành (%s). "
                    "Không được phép Check-in hoặc cập nhật bản vẽ!"
                ) % (doc.name or doc.engineering_code, doc.engineering_state))

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
        string="Child Documents",
        help="List of child component files referenced by this Assembly."
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
    def canCheckOut(self, showError=False):
        """Mở rộng canCheckOut: Cho phép tất cả kỹ sư đều có thể Check-out tài liệu để lấy file về máy trạm,
        kể cả khi tài liệu ở trạng thái Xác nhận (confirmed) hoặc Ban hành (released) phục vụ tham chiếu / mở bản vẽ trong CAD.
        Chỉ chặn khi tài liệu đã bị Lỗi thời / Hủy bỏ (obsoleted).
        """
        for doc in self:
            if doc.engineering_state == 'obsoleted':
                msg = _("Tài liệu '%s' đã ở trạng thái Lỗi thời (obsoleted) nên không thể Check-out.") % (
                    doc.name or doc.engineering_code
                )
                if showError:
                    raise UserError(msg)
                return False, msg
        return True, ""

    def canCheckOut1(self, host_name='', host_pws=''):
        """Mở rộng canCheckOut1 cho SolidWorks CAD Client:
        Cho phép Check-out tài liệu ở trạng thái Xác nhận (confirmed) hoặc Ban hành (released)
        phục vụ mở / đọc / tham chiếu CAD trên máy trạm SolidWorks.
        Chỉ chặn khi tài liệu đã ở trạng thái Lỗi thời (obsoleted).
        """
        for docBrws in self:
            if docBrws.engineering_state == 'obsoleted':
                msg = _("Unable to check-Out document %s that is in state %s") % (
                    docBrws.name or docBrws.engineering_code, docBrws.engineering_state
                )
                return docBrws.id, 'check_out_released', msg

            check_out, check_out_browser = docBrws.isCheckedOutByMeOr(host_name, host_pws)
            if check_out:
                msg = _("Unable to check-Out document %s that is already checked Out By %s") % (
                    docBrws.name or docBrws.engineering_code, docBrws.checkout_user
                )
                return docBrws.id, 'check_out_by_me', msg
            else:
                # Trả về cờ 'check_in' để SolidWorks Add-in biết tài liệu được phép Check-out tải về PWS
                return docBrws.id, 'check_in', ''
        return False, 'not_found', 'No document found'

    def checkout(self, hostName, hostPws, showError=True, user_id=False):
        """Khóa tài liệu Check-out:
        Cho phép mọi kỹ sư Check-out để lấy file về máy trạm SolidWorks (PWS) phục vụ thiết kế và tham chiếu.
        Quyền Check-in ghi đè được kiểm soát riêng biệt bởi Người giữ Key (key_holder_id).
        Check-out không tự động cấp Key (kỹ sư phải bấm nút 'Lock' khi tài liệu tự do để nhận quyền giữ Key).
        """
        self._check_cad_access_token()
        if not user_id:
            user_id = self.env.uid

        return super().checkout(hostName, hostPws, showError=showError, user_id=user_id)

    def action_web_checkout(self):
        """Cho phép kỹ sư bấm Check-out trực tiếp trên Web Browser để lấy file."""
        self.ensure_one()
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
    # 6. CÁC HÀM CAD CHẶN LƯU & BẢO VỆ CLIENT COM
    # =========================================================================
    @api.model
    def SaveOrUpdate(self, documents, default=None):
        """Chặn lưu/cập nhật tài liệu CAD khi token hết hạn."""
        self._check_cad_access_token()
        return super().SaveOrUpdate(documents, default=default)

    @api.model
    def CheckSaveUpdate(self, documents, default=None):
        """Chặn kiểm tra lưu tài liệu CAD khi token hết hạn."""
        self._check_cad_access_token()
        return super().CheckSaveUpdate(documents, default=default)

    @api.model
    def CheckIn(self, attrs):
        """Chặn Check-in đơn lẻ khi không giữ Key hoặc token hết hạn."""
        self._check_cad_access_token()
        doc_id = attrs.get("id")
        if doc_id:
            doc = self.browse(doc_id)
            doc._check_key_holder_permission()
        return super().CheckIn(attrs)

    @api.model
    def CheckInRecursive(self, request, default=None):
        """Chặn Check-in đệ quy khi token hết hạn, kiểm tra quyền giữ Key và bảo vệ client CAD chống crash COM."""
        self._check_cad_access_token()

        target_doc = False
        if request and isinstance(request, (list, tuple)) and len(request) > 0:
            doc_args = request[0]
            if isinstance(doc_args, dict):
                eng_code = doc_args.get("engineering_code")
                doc_rev = doc_args.get("engineering_revision")
                if eng_code:
                    target_doc = self.search([
                        ("engineering_code", "=", eng_code),
                        ("engineering_revision", "=", doc_rev)
                    ], limit=1)
                    if target_doc:
                        # 1. Kiểm tra quyền giữ Key độc quyền
                        target_doc._check_key_holder_permission()

                        # 2. Đảm bảo bản ghi checkout tồn tại cho user này để tránh lỗi COM SolidWorks
                        if not target_doc.isCheckedOutByMe():
                            host_name = doc_args.get("hostname") or "SolidWorks CAD"
                            target_doc.checkout(host_name, "pws", showError=False)

        if target_doc:
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

    @api.model
    def createFromProps(self, *args, **kwargs):
        """Chặn tạo/sửa bản vẽ từ hộp thoại Properties của SolidWorks."""
        self._check_cad_access_token()
        return super().createFromProps(*args, **kwargs)

    @api.model
    def saveSingleLevel(self, clientArg):
        """Chặn lưu bản vẽ đơn cấp khi token hết hạn."""
        self._check_cad_access_token()
        return super().saveSingleLevel(clientArg)

    @api.model
    def CheckIn2(self, request, default=None, force=False):
        """Chặn CheckIn2 khi không giữ Key hoặc token hết hạn."""
        self._check_cad_access_token()
        return super().CheckIn2(request, default=default, force=force)

    @api.model
    def CheckInRecursive2(self, involved_docs_dict, **kargs):
        """Chặn CheckInRecursive2 khi token hết hạn và lưu snapshot lịch sử."""
        self._check_cad_access_token()
        try:
            import json
            involved = json.loads(involved_docs_dict)
            for doc_vals in involved.get("to_check_in", []):
                docId = self.getDocId(doc_vals)
                if docId:
                    docId._check_key_holder_permission()
                    docId._create_document_history_snapshot(
                        description=_("Check-in từ SolidWorks CAD"),
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
