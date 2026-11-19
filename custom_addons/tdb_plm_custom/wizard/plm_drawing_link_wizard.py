# -*- coding: utf-8 -*-
# Copyright 2026 TDB Solution Co., Ltd.
"""
Wizard hỗ trợ thao tác Liên kết Bản vẽ 2D (LyTree) và Tải lên Bản in PDF Kỹ thuật:
1. PlmLinkDrawingWizard: Hỗ trợ người dùng chọn và liên kết nhanh bản vẽ 2D (.slddrw) với mô hình 3D (.sldprt, .sldasm).
2. PlmUploadPrintoutWizard: Hỗ trợ tải lên tệp PDF kỹ thuật (printout) cho bản vẽ 2D từ giao diện Web.
"""
import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PlmLinkDrawingWizard(models.TransientModel):
    _name = "plm.link.drawing.wizard"
    _description = "Wizard liên kết Bản vẽ 2D với Mô hình 3D"

    parent_id = fields.Many2one(
        "ir.attachment",
        string="Mô hình 3D",
        required=True,
        readonly=True,
        help="Chi tiết / Cụm lắp ráp 3D đang được liên kết bản vẽ."
    )
    parent_name = fields.Char(
        related="parent_id.name",
        string="Tên tệp 3D",
        readonly=True
    )
    drawing_id = fields.Many2one(
        "ir.attachment",
        string="Bản vẽ 2D",
        required=True,
        domain="[('is_plm', '=', True), ('id', '!=', parent_id), '|', ('document_type', '=', '2d'), ('name', '=ilike', '%.slddrw')]",
        help="Chọn bản vẽ 2D (.slddrw) cần liên kết với mô hình 3D này."
    )
    drawing_code = fields.Char(
        related="drawing_id.engineering_code",
        string="Mã kỹ thuật",
        readonly=True
    )
    drawing_state = fields.Selection(
        related="drawing_id.engineering_state",
        string="Trạng thái bản vẽ",
        readonly=True
    )

    def action_confirm_link(self):
        """Xác nhận liên kết bản vẽ 2D với mô hình 3D (link_kind='LyTree')."""
        self.ensure_one()
        if not self.parent_id or not self.drawing_id:
            raise UserError(_("Vui lòng chọn bản vẽ 2D hợp lệ cần liên kết."))

        self.parent_id.link_drawing_document(self.drawing_id.id)
        _logger.info(
            f"[TDB PLM] Đã liên kết bản vẽ 2D '{self.drawing_id.name}' (id={self.drawing_id.id}) "
            f"với mô hình 3D '{self.parent_id.name}' (id={self.parent_id.id})"
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Liên kết thành công"),
                "message": _("Đã liên kết bản vẽ 2D '%s' với mô hình 3D '%s'.") % (self.drawing_id.name, self.parent_id.name),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }


class PlmUploadPrintoutWizard(models.TransientModel):
    _name = "plm.upload.printout.wizard"
    _description = "Wizard tải lên Bản in PDF Kỹ thuật"

    attachment_id = fields.Many2one(
        "ir.attachment",
        string="Tài liệu bản vẽ",
        required=True,
        readonly=True,
        help="Tài liệu bản vẽ nhận tệp in PDF kỹ thuật."
    )
    attachment_name = fields.Char(
        string="Tên tài liệu",
        readonly=True
    )
    engineering_code = fields.Char(
        string="Mã kỹ thuật",
        readonly=True
    )
    pdf_file = fields.Binary(
        string="Tệp PDF kỹ thuật",
        required=True,
        help="Chọn tệp PDF bản vẽ kỹ thuật tải lên từ máy tính."
    )
    pdf_filename = fields.Char(
        string="Tên file PDF",
        default="printout.pdf"
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        att_id = self.env.context.get("default_attachment_id")
        if not att_id and self.env.context.get("active_model") == "ir.attachment":
            att_id = self.env.context.get("active_id")
        if att_id:
            doc = self.env["ir.attachment"].browse(att_id)
            if doc.exists():
                res["attachment_id"] = doc.id
                res["attachment_name"] = doc.name or doc.engineering_code or ""
                res["engineering_code"] = doc.engineering_code or ""
                code = doc.engineering_code or doc.name or "document"
                rev = doc.engineering_revision or 0
                res["pdf_filename"] = f"{code}_{rev}.pdf"
        return res

    def action_confirm_upload(self):
        """Xác nhận lưu tệp PDF kỹ thuật vào trường printout của tài liệu."""
        self.ensure_one()
        if not self.pdf_file:
            raise UserError(_("Vui lòng chọn tệp PDF kỹ thuật cần tải lên!"))

        filename = self.pdf_filename or f"{self.attachment_id.engineering_code}_{self.attachment_id.engineering_revision}.pdf"
        if not filename.lower().endswith(".pdf"):
            filename = f"{filename}.pdf"

        self.attachment_id.sudo().write({
            "printout": self.pdf_file,
            "printout_name": filename,
        })

        self.attachment_id.message_post(
            body=_("Đã tải lên bản in PDF kỹ thuật: <b>%s</b>") % filename,
            subtype_xmlid="mail.mt_note",
        )

        _logger.info(
            f"[TDB PLM] Đã tải lên bản in PDF '{filename}' cho tài liệu '{self.attachment_id.name}' (id={self.attachment_id.id})"
        )

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Tải lên PDF thành công"),
                "message": _("Đã cập nhật bản in PDF kỹ thuật cho tài liệu '%s'.") % self.attachment_id.name,
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }
