# -*- coding: utf-8 -*-
# Copyright 2026 TDB Solution Co., Ltd.
"""
Wizard Bàn giao & Chuyển giao Key (Force Unlock & Key Transfer):
1. Quản trị viên / Trưởng phòng: Cưỡng chế chuyển giao Key (Force Unlock) khi kỹ sư vắng mặt hoặc đổi nhân sự.
2. Kỹ sư đang giữ Key: Tự nguyện bàn giao Key cho kỹ sư khác hoặc nhả Key tự do khi hoàn tất phần việc.
"""
import logging
from markupsafe import Markup
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PlmTransferKeyWizard(models.TransientModel):
    _name = "plm.transfer.key.wizard"
    _description = "Wizard bàn giao và chuyển giao quyền giữ Key tài liệu PLM"

    document_id = fields.Many2one(
        "ir.attachment",
        string="Document",
        required=True,
        readonly=True,
        help="CAD document undergoing Key Holder transfer."
    )
    document_name = fields.Char(
        related="document_id.name",
        string="Document Name",
        readonly=True
    )
    current_key_holder_id = fields.Many2one(
        "res.users",
        string="Current Key Holder",
        readonly=True,
        help="Engineer currently holding exclusive Check-in rights."
    )
    is_admin = fields.Boolean(
        string="Is Administrator",
        compute="_compute_is_admin"
    )
    is_holder = fields.Boolean(
        string="Is Key Holder",
        compute="_compute_is_holder"
    )

    def _compute_is_admin(self):
        for rec in self:
            rec.is_admin = (
                rec.env.user.has_group('plm.group_plm_admin')
                or rec.env.user.has_group('base.group_system')
                or rec.env.is_admin()
            )

    def _compute_is_holder(self):
        for rec in self:
            rec.is_holder = bool(
                rec.current_key_holder_id and rec.current_key_holder_id == self.env.user
            )

    transfer_type_holder = fields.Selection(
        selection=[
            ('to_user', 'Chuyển giao cho kỹ sư khác'),
            ('release', 'Mở khóa tài liệu (Trả Key)'),
        ],
        string="Tác vụ thực hiện",
        default='to_user',
        required=True,
    )
    transfer_type_admin = fields.Selection(
        selection=[
            ('to_user', 'Chuyển giao cho kỹ sư khác'),
            ('release', 'Thu hồi Key bắt buộc (Force Unlock)'),
        ],
        string="Tác vụ thực hiện",
        default='to_user',
        required=True,
    )
    transfer_type = fields.Selection(
        selection=[
            ('to_user', 'Chuyển giao cho kỹ sư khác'),
            ('release', 'Mở khóa / Trả Key'),
        ],
        string="Tác vụ thực hiện",
        default='to_user',
    )
    new_key_holder_id = fields.Many2one(
        "res.users",
        string="Kỹ sư nhận Key mới",
        domain="[('share', '=', False)]",
        help="Chọn kỹ sư sẽ tiếp nhận quyền giữ Key."
    )
    reason = fields.Text(
        string="Lý do chuyển giao / Thu hồi",
        required=False,
    )
    release_all_checkouts = fields.Boolean(
        string="Hủy toàn bộ trạng thái Check-out cục bộ",
        default=True,
        help="Tự động hủy các phiên Check-out trước đó để kỹ sư mới có thể Check-out ngay."
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        doc_id = res.get('document_id') or self.env.context.get('default_document_id')
        if doc_id:
            doc = self.env['ir.attachment'].browse(doc_id)
            if doc.engineering_state in ['released', 'obsoleted']:
                raise UserError(_("Tài liệu '%s' đã ở trạng thái Ban hành (%s). Không thể chuyển giao hoặc mở khóa Key.") % (
                    doc.name or doc.engineering_code, doc.engineering_state
                ))
            is_admin = self.env.user.has_group('plm.group_plm_admin') or self.env.user.has_group('base.group_system') or self.env.is_admin()
            is_holder = (doc.key_holder_id == self.env.user)
            if not is_admin and not is_holder:
                raise UserError(_("Bạn không có quyền chuyển giao hoặc mở khóa Key cho tài liệu này."))
            res['current_key_holder_id'] = doc.key_holder_id.id if doc.key_holder_id else False
            res['is_holder'] = is_holder
        return res

    def action_confirm_transfer(self):
        """Xác nhận chuyển giao quyền giữ Key và ghi nhận nhật ký kiểm toán."""
        self.ensure_one()
        doc = self.document_id
        if doc.engineering_state in ['released', 'obsoleted']:
            raise UserError(_("Tài liệu '%s' đã ở trạng thái Ban hành (%s). Không thể chuyển giao hoặc mở khóa Key.") % (
                doc.name or doc.engineering_code, doc.engineering_state
            ))
        is_admin = self.env.user.has_group('plm.group_plm_admin') or self.env.user.has_group('base.group_system') or self.env.is_admin()
        is_holder = (doc.key_holder_id == self.env.user)

        if not is_admin and not is_holder:
            raise UserError(_("Bạn không có quyền chuyển giao hoặc mở khóa Key. Chỉ Quản trị viên PLM hoặc người đang giữ Key mới được phép thực hiện."))

        prev_holder = self.current_key_holder_id or doc.key_holder_id
        prev_name = prev_holder.name if prev_holder else _("Chưa có ai")

        action_type = self.transfer_type_holder if is_holder else self.transfer_type_admin

        if action_type == 'to_user':
            if not self.new_key_holder_id:
                raise UserError(_("Vui lòng chọn kỹ sư tiếp nhận Key mới."))
            if prev_holder and prev_holder == self.new_key_holder_id:
                raise UserError(_("Kỹ sư tiếp nhận phải khác với kỹ sư đang giữ Key hiện tại."))

            new_holder = self.new_key_holder_id
            new_name = new_holder.name

            # 1. Cập nhật quyền giữ Key mới trên tài liệu
            doc.sudo().write({
                'key_holder_id': new_holder.id,
                'engineering_writable': False,
            })

            # 2. Giải phóng các phiên Check-out nếu được chọn
            if self.release_all_checkouts:
                self.env['plm.checkout'].sudo().search([('documentid', '=', doc.id)]).unlink()

            # 3. Ghi nhận nhật ký kiểm toán vào Chatter
            reason_text = self.reason.strip() if self.reason else _("Không có ghi chú")
            action_name = _("Chuyển giao quyền giữ Key (Force Unlock)") if (is_admin and not is_holder) else _("Chuyển giao quyền giữ Key")
            audit_msg = Markup(_(
                "<p>"
                "%s<br/>"
                "Người thực hiện: %s<br/>"
                "Kỹ sư từng giữ Key: %s<br/>"
                "Kỹ sư nhận Key mới: %s<br/>"
                "Lý do: %s<br/>"
                "Trạng thái: Đã chuyển giao Key thành công."
                "</p>"
            )) % (action_name, self.env.user.name, prev_name, new_name, reason_text)

            doc.message_post(body=audit_msg, message_type='notification')
            _logger.warning(
                f">>> [TDB PLM TRANSFER KEY] {self.env.user.name} đã chuyển giao Key tài liệu "
                f"{doc.name} (ID {doc.id}) từ '{prev_name}' sang '{new_name}'"
            )

        else:
            if not doc.key_holder_id:
                raise UserError(_("Tài liệu hiện tại chưa có ai giữ Key. Không cần thực hiện thu hồi/mở khóa."))

            # Thu hồi / Unlock quyền giữ Key (Chuyển sang trạng thái tự do)
            doc.sudo().write({
                'key_holder_id': False,
                'engineering_writable': False,
            })

            if self.release_all_checkouts:
                self.env['plm.checkout'].sudo().search([('documentid', '=', doc.id)]).unlink()

            reason_text = self.reason.strip() if self.reason else _("Không có ghi chú")
            action_name = _("Force Unlock") if (is_admin and not is_holder) else _("Unlock")
            status_desc = (
                _("Đã cưỡng chế thu hồi quyền giữ Key (Force Unlock). Tài liệu chuyển về trạng thái tự do (chờ bấm 'Lock' để nhận Key).")
                if (is_admin and not is_holder) else
                _("Đã mở khóa quyền giữ Key (Unlock). Tài liệu chuyển về trạng thái tự do (chờ bấm 'Lock' để nhận Key).")
            )
            audit_msg = Markup(_(
                "<p>"
                "%s<br/>"
                "Người thực hiện: %s<br/>"
                "Kỹ sư từng giữ Key: %s<br/>"
                "Lý do: %s<br/>"
                "Trạng thái: %s"
                "</p>"
            )) % (action_name, self.env.user.name, prev_name, reason_text, status_desc)

            doc.message_post(body=audit_msg, message_type='notification')
            _logger.warning(
                f">>> [TDB PLM RELEASE KEY] {self.env.user.name} đã thực hiện {action_name} cho tài liệu {doc.name} (ID {doc.id})"
            )

        # Phát thông báo bus real-time để các máy trạm khác cập nhật tức thời
        doc._notify_plm_document_update()

        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }
