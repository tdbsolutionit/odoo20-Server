# -*- coding: utf-8 -*-
# Copyright 2026 TDB Solution Co., Ltd.
"""
Mở rộng model product.product cho phân hệ Engineering Parts (PLM).
Tuân thủ Layer 2: Quản lý schema dữ liệu và business logic cốt lõi.
Bảo vệ tầng ORM: Chặn đứng lưu/sửa linh kiện khi token 180 ngày hết hạn hoặc bị thu hồi.
"""
from odoo import models, fields, api, _
from odoo.exceptions import UserError

class ProductProduct(models.Model):
    """
    Kế thừa product.product để quản lý thông tin linh kiện kỹ thuật (Engineering Parts).
    Chuẩn hóa nhãn chuỗi gốc tiếng Anh (Revision, CAD Raw Material, CAD Surface Finishing).
    Kiểm soát quyền truy cập thực thi theo Access Token 180 ngày từ máy trạm CAD.
    """
    _inherit = "product.product"

    engineering_material = fields.Char(string="CAD Raw Material")
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

    @api.model
    def _resolve_cad_part_revision(self, attributes):
        """Use the exact revision for existing codes, and Rev 0 for new codes."""
        values = dict(attributes)
        code = values.get("engineering_code")
        if not code or code == "-":
            return values, self.browse()

        revision = values.get("engineering_revision")
        if revision is None or revision is False or revision in ("", "False"):
            revision = 0
        elif type(revision) is not int:
            try:
                if not isinstance(revision, str):
                    raise ValueError
                revision = int(revision)
            except ValueError:
                raise UserError(_("Invalid revision for Part %(code)s.", code=code)) from None

        code_domain = [("engineering_code", "=", code)]
        part = self.search(code_domain + [("engineering_revision", "=", revision)], limit=1)
        if not part and self.env["product.template"].sudo().with_context(active_test=False).search_count(
            code_domain + [("active", "=", True)], limit=1,
        ):
            raise UserError(_(
                "Could not find Part %(code)s at revision %(revision)s. Open the correct revision in the CAD Client.",
                code=code, revision=revision,
            ))
        values["engineering_revision"] = part.engineering_revision if part else 0
        return values, part

    @api.model
    def SaveOrUpdate(self, vals):
        """Lưu / Cập nhật linh kiện từ CAD Client:
        Linh kiện đã Ban hành (released), Chờ duyệt (confirmed), Lỗi thời (obsoleted):
        Bảo vệ không cho phép ghi đè, đặt hasSaved = False để SolidWorks không đánh dấu xanh và không lưu.
        """
        self._check_cad_access_token()
        writable_vals = []
        readonly_vals = []
        for part_values in vals or []:
            values, part = self._resolve_cad_part_revision(part_values)
            if part and part.engineering_state != 'draft':
                values.update(componentID=part.id, name=part.name, hasSaved=False)
                readonly_vals.append(values)
                continue
            writable_vals.append(values)

        res = []
        if writable_vals:
            res = super().SaveOrUpdate(writable_vals)
        res.extend(readonly_vals)
        return res

    @api.model
    def ConvertToPlmProduct(self, attributes_list=[]):
        """Chặn chuyển đổi linh kiện PLM nếu token hết hạn hoặc bị thu hồi."""
        self._check_cad_access_token()
        return super().ConvertToPlmProduct([
            self._resolve_cad_part_revision(values)[0] for values in attributes_list
        ])

    @api.model
    def createFromProps(self, productAttribute):
        """Chặn tạo/sửa linh kiện từ hộp thoại Component Properties của SolidWorks Add-in nếu đã Ban hành/Chờ duyệt."""
        self._check_cad_access_token()
        values, part = self._resolve_cad_part_revision(productAttribute)
        if part and part.engineering_state != 'draft':
            raise UserError(_(
                "Cannot edit Part %(code)s revision %(revision)s. Open a Draft revision in the CAD Client.",
                code=part.engineering_code, revision=part.engineering_revision,
            ))
        return super().createFromProps(values)

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
                    state = comp.engineering_state
                    if state in ['confirmed', 'released', 'obsoleted']:
                        state_desc = "Đã ban hành" if state == 'released' else ("Đang chờ duyệt" if state == 'confirmed' else "Lỗi thời")
                        raise UserError(_(
                            "Linh kiện '%s' đang ở trạng thái %s (%s). "
                            "Không được phép chỉnh sửa thông tin từ SolidWorks!"
                        ) % (comp.engineering_code or comp.name, state_desc, state))
        return super().write(vals)
