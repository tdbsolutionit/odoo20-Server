# -*- coding: utf-8 -*-
# Copyright 2026 TDB Solution Co., Ltd.
"""
Mở rộng model product.product cho phân hệ Engineering Parts (PLM).
Tuân thủ Layer 2: Quản lý schema dữ liệu và business logic cốt lõi.
Bảo vệ tầng ORM: Chặn đứng lưu/sửa linh kiện khi token 180 ngày hết hạn hoặc bị thu hồi.
"""
import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ProductProduct(models.Model):
    """
    Kế thừa product.product để quản lý thông tin linh kiện kỹ thuật (Engineering Parts).
    Chuẩn hóa nhãn chuỗi gốc tiếng Anh (Revision, CAD Raw Material, CAD Surface Finishing).
    Kiểm soát quyền truy cập thực thi theo Access Token 180 ngày từ máy trạm CAD.
    """
    _inherit = "product.product"

    engineering_revision = fields.Integer(string="Engineering Revision")
    engineering_material = fields.Char(string="CAD Material")
    engineering_surface = fields.Char(string="CAD Surface Finishing")

    def _check_cad_access_token(self):
        """Kiểm tra token thiết bị CAD tại tầng ORM:
        Chặn tuyệt đối mọi thao tác tạo/sửa linh kiện từ CAD Client nếu token bị thu hồi hoặc hết hạn 180 ngày.
        Bỏ qua hoàn toàn đối với các thao tác từ Web Browser của Admin / Kỹ sư.
        """
        # 1. Bỏ qua nếu có cờ định danh thao tác Web
        if self.env.context.get('is_web_operation'):
            return

        # 2. Bỏ qua hoàn toàn nếu request xuất phát từ trình duyệt Web (/web, /odoo)
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

    def _get_comp_effective_state(self, comp):
        """Xác định trạng thái hiệu lực thực tế của linh kiện:
        Nếu linh kiện ở trạng thái 'confirmed' hoặc 'draft' nhưng bản vẽ CAD / tài liệu PLM liên kết
        đã ở trạng thái 'released' (Đã ban hành), thì ưu tiên trạng thái 'released' để thông báo chính xác cho người dùng.
        """
        state = comp.engineering_state
        if state != 'released':
            # 1. Kiểm tra qua quan hệ linkeddocuments
            if hasattr(comp, 'linkeddocuments') and comp.linkeddocuments:
                if any(d.engineering_state == 'released' for d in comp.linkeddocuments):
                    try:
                        comp.sudo().with_context(skip_released_freeze=True, odooPLM=False).write({'engineering_state': 'released'})
                    except Exception:
                        pass
                    return 'released'
            # 2. Tìm kiếm tài liệu PLM theo mã linh kiện (engineering_code hoặc tên tệp)
            if comp.engineering_code:
                rel_docs = self.env['ir.attachment'].sudo().search([
                    ('is_plm', '=', True),
                    '|',
                    ('engineering_code', 'ilike', comp.engineering_code),
                    ('name', 'ilike', comp.engineering_code)
                ], order='engineering_revision desc, id desc', limit=10)
                if any(d.engineering_state == 'released' for d in rel_docs):
                    try:
                        comp.sudo().with_context(skip_released_freeze=True, odooPLM=False).write({'engineering_state': 'released'})
                    except Exception:
                        pass
                    return 'released'
        return state

    @api.model
    def SaveOrUpdate(self, vals):
        """Lưu / Cập nhật linh kiện từ CAD Client:
        Linh kiện đã Ban hành (released), Chờ duyệt (confirmed), Lỗi thời (obsoleted):
        Bảo vệ không cho phép ghi đè, đặt hasSaved = False để SolidWorks không đánh dấu xanh và không lưu.
        """
        self._check_cad_access_token()
        writable_vals = []
        readonly_vals = []
        for partVals in (vals or []):
            eng_code = partVals.get("engineering_code")
            eng_rev = partVals.get("engineering_revision", 0)
            try:
                eng_rev_int = int(eng_rev) if eng_rev is not None else 0
            except (ValueError, TypeError):
                eng_rev_int = 0
            comp = False
            if eng_code:
                comp = self.search([
                    ("engineering_code", "=", eng_code),
                    ("engineering_revision", "=", eng_rev_int)
                ], limit=1)
                if not comp:
                    comp = self.search([
                        ("engineering_code", "=", eng_code)
                    ], order="engineering_revision desc, id desc", limit=1)
            if comp:
                state = self._get_comp_effective_state(comp)
                if state in ['confirmed', 'released', 'obsoleted']:
                    part_copy = dict(partVals)
                    part_copy['componentID'] = comp.id
                    part_copy['name'] = comp.name
                    part_copy['hasSaved'] = False
                    readonly_vals.append(part_copy)
                    continue
            writable_vals.append(partVals)

        res = []
        if writable_vals:
            res = super().SaveOrUpdate(writable_vals)
        res.extend(readonly_vals)
        return res

    @api.model
    def ConvertToPlmProduct(self, attributes_list=[]):
        """Chặn chuyển đổi linh kiện PLM nếu token hết hạn hoặc bị thu hồi."""
        self._check_cad_access_token()
        return super().ConvertToPlmProduct(attributes_list)

    @api.model
    def createFromProps(self, productAttribute):
        """Chặn tạo/sửa linh kiện từ hộp thoại Component Properties của SolidWorks Add-in nếu đã Ban hành/Chờ duyệt."""
        self._check_cad_access_token()
        eng_code = productAttribute.get("engineering_code")
        eng_rev = productAttribute.get("engineering_revision", 0)
        try:
            eng_rev_int = int(eng_rev) if eng_rev is not None else 0
        except (ValueError, TypeError):
            eng_rev_int = 0

        comp = False
        if eng_code:
            comp = self.search([
                ("engineering_code", "=", eng_code),
                ("engineering_revision", "=", eng_rev_int)
            ], limit=1)
            if not comp:
                comp = self.search([
                    ("engineering_code", "=", eng_code)
                ], order="engineering_revision desc, id desc", limit=1)

        if comp:
            state = self._get_comp_effective_state(comp)
            if state in ['confirmed', 'released', 'obsoleted']:
                state_desc = "Đã ban hành" if state == 'released' else ("Đang chờ duyệt" if state == 'confirmed' else "Lỗi thời")
                raise UserError(_(
                    "Linh kiện '%s' đang ở trạng thái %s (%s). "
                    "Không được phép chỉnh sửa thông tin từ SolidWorks!"
                ) % (comp.engineering_code or comp.name, state_desc, state))

        return super().createFromProps(productAttribute)

    @api.model_create_multi
    def create(self, vals_list):
        """Chặn tạo linh kiện khi thao tác từ SolidWorks nếu token hết hạn."""
        if self.env.context.get('odooPLM'):
            self._check_cad_access_token()
        return super().create(vals_list)

    def write(self, vals):
        """Chặn sửa linh kiện khi thao tác từ SolidWorks nếu đã Ban hành/Chờ duyệt hoặc token hết hạn."""
        if self.env.context.get('odooPLM') and not self.env.context.get('skip_released_freeze'):
            self._check_cad_access_token()
            system_fields = {'message_follower_ids', 'message_ids', 'activity_ids'}
            data_fields = set(vals.keys()) - system_fields
            if data_fields:
                for comp in self:
                    state = self._get_comp_effective_state(comp)
                    if state in ['confirmed', 'released', 'obsoleted']:
                        state_desc = "Đã ban hành" if state == 'released' else ("Đang chờ duyệt" if state == 'confirmed' else "Lỗi thời")
                        raise UserError(_(
                            "Linh kiện '%s' đang ở trạng thái %s (%s). "
                            "Không được phép chỉnh sửa thông tin từ SolidWorks!"
                        ) % (comp.engineering_code or comp.name, state_desc, state))
        return super().write(vals)
