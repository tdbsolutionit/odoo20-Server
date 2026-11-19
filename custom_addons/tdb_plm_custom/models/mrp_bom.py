# -*- coding: utf-8 -*-
# Copyright 2026 TDB Solution Co., Ltd.
"""
Phần mở rộng Kiểm toán Thay đổi Kỹ thuật cho Định mức Vật tư (BOM Audit Trail):
1. MrpBom: Kích hoạt Field-Level Tracking cho mã định mức (code) và số lượng thành phẩm (product_qty).
2. MrpBomLine: Tự động bắt trọn 3 biến động (Thêm mới, Đổi số lượng / Thay thế sản phẩm, Loại bỏ linh kiện)
   và ghi nhận nhật ký kiểm toán trực quan vào Chatter của BOM cha (chống spam log khi đồng bộ batch).
"""
import logging
from markupsafe import Markup
from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


def _format_qty(qty):
    """Định dạng số lượng linh kiện gọn gàng (ví dụ: 2 thay vì 2.0, 2.5 giữ nguyên)."""
    try:
        f = float(qty)
        return f"{f:g}"
    except (ValueError, TypeError):
        return str(qty)


class MrpBom(models.Model):
    _inherit = "mrp.bom"

    # =========================================================================
    # FIELD-LEVEL TRACKING CHO BOM HEADER (HT-5)
    # =========================================================================
    code = fields.Char(tracking=True)
    product_qty = fields.Float(tracking=True)


class MrpBomLine(models.Model):
    _inherit = "mrp.bom.line"

    # =========================================================================
    # KIỂM TOÁN BIẾN ĐỘNG DÒNG LINH KIỆN (BOM LINE AUDIT TRAIL)
    # =========================================================================

    @api.model_create_multi
    def create(self, vals_list):
        """Khi thêm mới dòng linh kiện vào BOM:
        Ghi nhận thông báo HTML màu xanh lá vào Chatter của BOM cha (trừ khi có context skip_line_audit).
        """
        records = super().create(vals_list)
        if not self.env.context.get("skip_line_audit"):
            for line in records:
                bom = line.bom_id
                if bom and bom.exists():
                    prod = line.product_id
                    code_str = f"[{prod.default_code}] " if prod and prod.default_code else ""
                    prod_name = prod.name or prod.display_name or _("Unknown Product") if prod else _("Unknown Product")
                    uom_name = line.uom_id.name if line.uom_id else ""
                    uom_str = f" ({uom_name})" if uom_name else ""
                    body = Markup(
                        '<p style="margin-bottom: 2px;">'
                        '  <span class="badge bg-success" style="font-size: 11px;">+ Thêm linh kiện</span> '
                        '  <strong>%(code)s%(name)s</strong> — Số lượng: <strong>%(qty)s</strong>%(uom)s'
                        '</p>'
                    ) % {
                        "code": code_str,
                        "name": prod_name,
                        "qty": _format_qty(line.product_qty),
                        "uom": uom_str,
                    }
                    bom.sudo().message_post(body=body)
        return records

    def write(self, vals):
        """Khi sửa đổi dòng linh kiện:
        1. Đổi số lượng (product_qty): Ghi thông báo màu vàng kèm số lượng cũ và mới.
        2. Thay thế linh kiện (product_id): Ghi thông báo màu vàng cảnh báo đổi mã vật tư.
        """
        if self.env.context.get("skip_line_audit"):
            return super().write(vals)

        track_qty = "product_qty" in vals
        track_prod = "product_id" in vals

        old_data = {}
        if track_qty or track_prod:
            for line in self:
                old_data[line.id] = {
                    "bom": line.bom_id,
                    "product": line.product_id,
                    "qty": line.product_qty,
                    "uom_name": line.uom_id.name if line.uom_id else "",
                }

        res = super().write(vals)

        if track_qty or track_prod:
            for line in self:
                old = old_data.get(line.id)
                if not old:
                    continue
                bom = old["bom"] or line.bom_id
                if not bom or not bom.exists():
                    continue

                # 1. Kiểm toán Đổi số lượng linh kiện
                if track_qty and vals.get("product_qty") is not None:
                    new_qty = vals["product_qty"]
                    old_qty = old["qty"]
                    if float(new_qty) != float(old_qty):
                        prod = line.product_id
                        code_str = f"[{prod.default_code}] " if prod and prod.default_code else ""
                        prod_name = prod.name or prod.display_name or _("Unknown Product") if prod else _("Unknown Product")
                        uom_name = line.uom_id.name if line.uom_id else old["uom_name"]
                        uom_str = f" ({uom_name})" if uom_name else ""
                        body = Markup(
                            '<p style="margin-bottom: 2px;">'
                            '  <span class="badge bg-warning text-dark" style="font-size: 11px;">~ Đổi số lượng</span> '
                            '  <strong>%(code)s%(name)s</strong>: Số lượng đổi từ <strong>%(old_qty)s</strong> ➔ <strong>%(new_qty)s</strong>%(uom)s'
                            '</p>'
                        ) % {
                            "code": code_str,
                            "name": prod_name,
                            "old_qty": _format_qty(old_qty),
                            "new_qty": _format_qty(new_qty),
                            "uom": uom_str,
                        }
                        bom.sudo().message_post(body=body)

                # 2. Kiểm toán Thay thế sản phẩm linh kiện
                if track_prod and vals.get("product_id") is not None:
                    new_prod_id = vals["product_id"]
                    old_prod = old["product"]
                    if not old_prod or new_prod_id != old_prod.id:
                        new_prod = line.product_id
                        old_code = f"[{old_prod.default_code}] " if old_prod and old_prod.default_code else ""
                        new_code = f"[{new_prod.default_code}] " if new_prod and new_prod.default_code else ""
                        old_name = old_prod.name or old_prod.display_name if old_prod else _("Chưa có")
                        new_name = new_prod.name or new_prod.display_name if new_prod else _("Chưa có")
                        body = Markup(
                            '<p style="margin-bottom: 2px;">'
                            '  <span class="badge bg-warning text-dark" style="font-size: 11px;">~ Thay thế linh kiện</span> '
                            '  Thay đổi linh kiện từ <strong>%(old_code)s%(old_name)s</strong> ➔ <strong>%(new_code)s%(new_name)s</strong>'
                            '</p>'
                        ) % {
                            "old_code": old_code,
                            "old_name": old_name,
                            "new_code": new_code,
                            "new_name": new_name,
                        }
                        bom.sudo().message_post(body=body)

        return res

    def unlink(self):
        """Khi xóa dòng linh kiện khỏi BOM:
        Ghi nhận thông báo HTML màu đỏ vào Chatter của BOM cha trước khi xóa vĩnh viễn dòng.
        """
        if self.env.context.get("skip_line_audit"):
            return super().unlink()

        lines_info = []
        for line in self:
            bom = line.bom_id
            if bom and bom.exists():
                prod = line.product_id
                code_str = f"[{prod.default_code}] " if prod and prod.default_code else ""
                prod_name = prod.name or prod.display_name or _("Unknown Product") if prod else _("Unknown Product")
                uom_name = line.uom_id.name if line.uom_id else ""
                lines_info.append({
                    "bom": bom,
                    "code_str": code_str,
                    "prod_name": prod_name,
                    "qty": line.product_qty,
                    "uom_str": f" ({uom_name})" if uom_name else "",
                })

        res = super().unlink()

        for info in lines_info:
            bom = info["bom"]
            if bom.exists():
                body = Markup(
                    '<p style="margin-bottom: 2px;">'
                    '  <span class="badge bg-danger" style="font-size: 11px;">- Bỏ linh kiện</span> '
                    '  <strong>%(code)s%(name)s</strong> — Số lượng cũ: <strong>%(qty)s</strong>%(uom)s'
                    '</p>'
                ) % {
                    "code": info["code_str"],
                    "name": info["prod_name"],
                    "qty": _format_qty(info["qty"]),
                    "uom": info["uom_str"],
                }
                bom.sudo().message_post(body=body)

        return res
