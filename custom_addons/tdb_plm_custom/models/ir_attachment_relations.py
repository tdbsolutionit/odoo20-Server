# -*- coding: utf-8 -*-
# Copyright 2026 TDB Solution Co., Ltd.
"""
Mở rộng model ir.attachment.relation cho OdooPLM:
1. Ràng buộc toàn vẹn dữ liệu: Ngăn chặn tự tham chiếu chính mình (parent_id != child_id).
2. Bổ sung các trường related hiển thị trạng thái khóa, người giữ Key, mã kỹ thuật.
3. Cung cấp navigation method mở nhanh tài liệu con hoặc Assembly cha từ dòng quan hệ.
"""
import logging
from markupsafe import Markup
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError

_logger = logging.getLogger(__name__)


class PlmDocumentRelations(models.Model):
    _inherit = "ir.attachment.relation"

    # =========================================================================
    # 1. TRƯỜNG THÔNG TIN LIÊN KẾT BỔ SUNG CHO TÀI LIỆU CON
    # =========================================================================
    child_engineering_code = fields.Char(
        related="child_id.engineering_code",
        string="Child Engineering Code",
        store=False,
        readonly=True
    )
    child_lock_state = fields.Selection(
        related="child_id.lock_state",
        string="Child Lock Status",
        store=False,
        readonly=True
    )
    child_key_holder_id = fields.Many2one(
        related="child_id.key_holder_id",
        string="Child Key Holder",
        store=False,
        readonly=True
    )
    child_printout_name = fields.Char(
        related="child_id.printout_name",
        string="Child PDF Printout",
        store=False,
        readonly=True
    )
    child_has_printout = fields.Boolean(
        related="child_id.has_printout",
        string="Has Child PDF Printout",
        store=False,
        readonly=True
    )

    # =========================================================================
    # 2. TRƯỜNG THÔNG TIN LIÊN KẾT BỔ SUNG CHO TÀI LIỆU CHA (WHERE USED)
    # =========================================================================
    parent_engineering_code = fields.Char(
        related="parent_id.engineering_code",
        string="Parent Engineering Code",
        store=False,
        readonly=True
    )
    parent_lock_state = fields.Selection(
        related="parent_id.lock_state",
        string="Parent Lock Status",
        store=False,
        readonly=True
    )
    parent_key_holder_id = fields.Many2one(
        related="parent_id.key_holder_id",
        string="Parent Key Holder",
        store=False,
        readonly=True
    )
    parent_printout_name = fields.Char(
        related="parent_id.printout_name",
        string="Parent PDF Printout",
        store=False,
        readonly=True
    )
    parent_has_printout = fields.Boolean(
        related="parent_id.has_printout",
        string="Has Parent PDF Printout",
        store=False,
        readonly=True
    )

    # =========================================================================
    # 3. CHUẨN HÓA HIỂN THỊ CẤU HÌNH CAD (LOẠI BỎ DẤU GẠCH MẶC ĐỊNH '-')
    # =========================================================================
    cad_configuration = fields.Char(
        string="CAD Configuration",
        compute="_compute_cad_configuration",
        inverse="_inverse_cad_configuration",
        store=False,
        help="Cấu hình CAD sạch sẽ, không hiển thị dấu gạch ngang mặc định từ SolidWorks"
    )

    @api.depends("configuration")
    def _compute_cad_configuration(self):
        """Nếu cấu hình CAD là dấu gạch ngang '-' hoặc rỗng từ CAD Add-in thì để trống."""
        for rec in self:
            cfg = rec.configuration
            if not cfg or str(cfg).strip() in ("-", "None", "false", "False", ""):
                rec.cad_configuration = False
            else:
                rec.cad_configuration = str(cfg).strip()

    def _inverse_cad_configuration(self):
        """Ghi ngược lại trường configuration gốc để đồng bộ với CAD client."""
        for rec in self:
            rec.configuration = rec.cad_configuration or "-"

    # =========================================================================
    # 3. RÀNG BUỘC TOÀN VẸN DỮ LIỆU
    # =========================================================================
    @api.constrains('parent_id', 'child_id')
    def _check_parent_child_different(self):
        """Đảm bảo tài liệu không thể tự tham chiếu chính mình."""
        for rel in self:
            if rel.parent_id and rel.child_id and rel.parent_id.id == rel.child_id.id:
                raise ValidationError(_(
                    "Tài liệu không thể tự tham chiếu chính mình làm file con! "
                    "(Tài liệu: %s)"
                ) % (rel.parent_id.name or rel.parent_id.engineering_code or rel.parent_id.id))

    # =========================================================================
    # 4. ACTION HELPERS ĐIỀU HƯỚNG NHANH TỪ BẢNG QUAN HỆ
    # =========================================================================
    def action_open_child_document(self):
        """Mở chi tiết Form view PLM của tài liệu con."""
        self.ensure_one()
        if not self.child_id:
            return False
        form_view = self.env.ref('plm.view_attachment_form_plm_hinerit', raise_if_not_found=False)
        return {
            'name': self.child_id.name or self.child_id.engineering_code or _("Tài liệu kỹ thuật"),
            'type': 'ir.actions.act_window',
            'res_model': 'ir.attachment',
            'res_id': self.child_id.id,
            'view_mode': 'form',
            'views': [(form_view.id, 'form')] if form_view else False,
            'target': 'current',
            'context': {
                'odooPLM': True,
                'default_is_plm': True,
                'form_view_ref': 'plm.view_attachment_form_plm_hinerit',
            },
        }

    def action_open_parent_document(self):
        """Mở chi tiết Form view PLM của Assembly cha."""
        self.ensure_one()
        if not self.parent_id:
            return False
        form_view = self.env.ref('plm.view_attachment_form_plm_hinerit', raise_if_not_found=False)
        return {
            'name': self.parent_id.name or self.parent_id.engineering_code or _("Assembly cha"),
            'type': 'ir.actions.act_window',
            'res_model': 'ir.attachment',
            'res_id': self.parent_id.id,
            'view_mode': 'form',
            'views': [(form_view.id, 'form')] if form_view else False,
            'target': 'current',
            'context': {
                'odooPLM': True,
                'default_is_plm': True,
                'form_view_ref': 'plm.view_attachment_form_plm_hinerit',
            },
        }

    def action_preview_child_pdf(self):
        """Mở popup modal xem trước bản in PDF kỹ thuật của tài liệu bản vẽ con."""
        self.ensure_one()
        if not self.child_id:
            raise UserError(_("Không tìm thấy tài liệu bản vẽ con."))
        return self.child_id.action_preview_pdf()

    def action_view_child_printout(self):
        """Mở popup xem trước bản in PDF kỹ thuật của tài liệu bản vẽ con."""
        return self.action_preview_child_pdf()

    def action_upload_child_printout_wizard(self):
        """Mở wizard tải lên bản in PDF kỹ thuật cho tài liệu bản vẽ con."""
        self.ensure_one()
        if not self.child_id:
            raise UserError(_("Không tìm thấy tài liệu bản vẽ con."))
        code = self.child_id.engineering_code or self.child_id.name or "document"
        rev = self.child_id.engineering_revision or 0
        return {
            "name": _("Tải lên bản in PDF: %s") % (self.child_id.name or code),
            "type": "ir.actions.act_window",
            "res_model": "plm.upload.printout.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "active_id": self.child_id.id,
                "active_ids": [self.child_id.id],
                "active_model": "ir.attachment",
                "default_attachment_id": self.child_id.id,
                "default_pdf_filename": f"{code}_{rev}.pdf",
            },
        }

    @api.model_create_multi
    def create(self, vals_list):
        """Tự động chuẩn hóa link_kind='LyTree' nếu tài liệu con là bản vẽ 2D (.slddrw).
        Đồng thời ghi nhận nhật ký kiểm toán (Audit Trail) vào Chatter của tài liệu cha/con (HT-5).
        """
        for vals in vals_list:
            child_id = vals.get("child_id")
            if child_id and vals.get("link_kind") in (False, "HiTree"):
                child = self.env["ir.attachment"].browse(child_id)
                if child.exists() and (
                    child.document_type == "2d"
                    or (child.name and child.name.lower().endswith((".slddrw", ".dwg", ".dxf", ".pdf")))
                ):
                    vals["link_kind"] = "LyTree"

        records = super().create(vals_list)

        # Kiểm toán quan hệ CAD & Bản vẽ (HT-5)
        if not self.env.context.get("skip_relation_audit"):
            for rel in records:
                parent = rel.parent_id
                child = rel.child_id
                if not parent or not child:
                    continue
                parent_desc = f"[{parent.engineering_code}] {parent.name}" if parent.engineering_code else (parent.name or f"ID {parent.id}")
                child_desc = f"[{child.engineering_code}] {child.name}" if child.engineering_code else (child.name or f"ID {child.id}")
                cfg_str = rel.cad_configuration or rel.configuration or ""
                cfg_display = f" (Cấu hình: {cfg_str})" if cfg_str and str(cfg_str).strip() not in ("-", "None", "") else ""

                if rel.link_kind == "LyTree":
                    # Ghi vào Chatter của file 3D (parent_id)
                    if parent.exists():
                        parent.sudo().message_post(
                            body=Markup(
                                '<p style="margin-bottom: 2px;">'
                                'Đã liên kết bản vẽ 2D: <strong>%(child)s</strong> (ID: %(child_id)s)'
                                '</p>'
                            ) % {
                                "child": child_desc,
                                "child_id": child.id,
                            }
                        )
                    # Ghi vào Chatter của file 2D (child_id)
                    if child.exists():
                        child.sudo().message_post(
                            body=Markup(
                                '<p style="margin-bottom: 2px;">'
                                'Đã liên kết mô hình 3D: <strong>%(parent)s</strong> (ID: %(parent_id)s)'
                                '</p>'
                            ) % {
                                "parent": parent_desc,
                                "parent_id": parent.id,
                            }
                        )
                elif rel.link_kind in ("HiTree", "RfTree"):
                    # Ghi vào Chatter của Cụm máy cha (parent_id)
                    if parent.exists():
                        parent.sudo().message_post(
                            body=Markup(
                                '<p style="margin-bottom: 2px;">'
                                'Đã thêm chi tiết con: <strong>%(child)s</strong> vào cây cấu trúc%(cfg)s'
                                '</p>'
                            ) % {
                                "child": child_desc,
                                "cfg": cfg_display,
                            }
                        )

        return records

    def write(self, vals):
        """Tự động chuẩn hóa link_kind='LyTree' khi cập nhật sang bản vẽ 2D."""
        if "child_id" in vals and vals.get("link_kind", "HiTree") in (False, "HiTree"):
            child = self.env["ir.attachment"].browse(vals["child_id"])
            if child.exists() and (
                child.document_type == "2d"
                or (child.name and child.name.lower().endswith((".slddrw", ".dwg", ".dxf", ".pdf")))
            ):
                vals["link_kind"] = "LyTree"
        return super().write(vals)

    def unlink(self):
        """Khi gỡ bỏ liên kết quan hệ CAD (chi tiết con hoặc bản vẽ 2D):
        Thu thập thông tin và ghi nhận nhật ký kiểm toán vào Chatter trước khi xóa (HT-5).
        """
        if self.env.context.get("skip_relation_audit"):
            return super().unlink()

        audit_items = []
        for rel in self:
            parent = rel.parent_id
            child = rel.child_id
            if parent and child:
                parent_desc = f"[{parent.engineering_code}] {parent.name}" if parent.engineering_code else (parent.name or f"ID {parent.id}")
                child_desc = f"[{child.engineering_code}] {child.name}" if child.engineering_code else (child.name or f"ID {child.id}")
                audit_items.append({
                    "parent": parent,
                    "child": child,
                    "parent_desc": parent_desc,
                    "child_desc": child_desc,
                    "link_kind": rel.link_kind,
                })

        res = super().unlink()

        for item in audit_items:
            parent = item["parent"]
            child = item["child"]
            link_kind = item["link_kind"]

            if link_kind == "LyTree":
                if parent.exists():
                    parent.sudo().message_post(
                        body=Markup(
                            '<p style="margin-bottom: 2px;">'
                            'Đã gỡ bỏ liên kết bản vẽ 2D: <strong>%(child)s</strong>'
                            '</p>'
                        ) % {"child": item["child_desc"]}
                    )
                if child.exists():
                    child.sudo().message_post(
                        body=Markup(
                            '<p style="margin-bottom: 2px;">'
                            'Đã gỡ bỏ liên kết với mô hình 3D: <strong>%(parent)s</strong>'
                            '</p>'
                        ) % {"parent": item["parent_desc"]}
                    )
            elif link_kind in ("HiTree", "RfTree"):
                if parent.exists():
                    parent.sudo().message_post(
                        body=Markup(
                            '<p style="margin-bottom: 2px;">'
                            'Đã gỡ bỏ chi tiết con: <strong>%(child)s</strong> khỏi cây cấu trúc'
                            '</p>'
                        ) % {"child": item["child_desc"]}
                    )

        return res

