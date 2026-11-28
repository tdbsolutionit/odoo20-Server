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
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare, float_round

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
    code = fields.Char(string="Reference", tracking=True)
    product_qty = fields.Float(string="Quantity", tracking=True)

    # =========================================================================
    # THUẬT TOÁN ĐỐI SOÁT KHÁC BIỆT BOM (BOM DIFF ENGINE - HT-3.1)
    # =========================================================================
    def get_previous_revision_bom(self):
        """Tìm bản ghi BOM phiên bản trước đó của cùng sản phẩm:
        - Cùng sản phẩm / mẫu sản phẩm (hoặc trong tập các revision của product_tmpl_id có cùng engineering_code).
        - Ưu tiên revision nhỏ hơn gần nhất (engineering_revision < self.engineering_revision).
        - Hoặc trạng thái đã ban hành (released/confirmed) tạo trước đó.
        - Hoặc BOM tạo trước gần nhất (id < self.id).
        Trả về recordset mrp.bom (1 bản ghi) hoặc False nếu không có.
        """
        self.ensure_one()
        all_tmpl_ids = [self.product_tmpl_id.id] if self.product_tmpl_id else []
        if (
            self.product_tmpl_id
            and self.product_tmpl_id.engineering_code
            and hasattr(self.product_tmpl_id, "get_all_revision")
        ):
            try:
                rev_tmpls = self.product_tmpl_id.get_all_revision()
                if rev_tmpls:
                    all_tmpl_ids = rev_tmpls.ids
            except Exception as e:
                _logger.debug(f"[TDB PLM] get_all_revision error: {e}")

        domain_base = [("id", "!=", self.id)]
        if all_tmpl_ids:
            domain_base.append(("product_tmpl_id", "in", all_tmpl_ids))
        elif self.product_id:
            domain_base.append(("product_id", "=", self.product_id.id))
        else:
            return False

        # 1. Tìm theo engineering_revision < self.engineering_revision
        curr_rev = getattr(self, "engineering_revision", 0) or 0
        if curr_rev > 0:
            prev_bom = self.search(
                domain_base + [("engineering_revision", "<", curr_rev)],
                order="engineering_revision desc, id desc",
                limit=1,
            )
            if prev_bom:
                return prev_bom

        # 2. Tìm theo trạng thái đã ban hành (released/confirmed) tạo trước đó
        if self.id:
            prev_bom = self.search(
                domain_base + [
                    ("id", "<", self.id),
                    ("engineering_state", "in", ["released", "confirmed"]),
                ],
                order="id desc",
                limit=1,
            )
            if prev_bom:
                return prev_bom

        # 3. Tìm BOM tạo trước gần nhất (id < self.id)
        if self.id:
            prev_bom = self.search(
                domain_base + [("id", "<", self.id)],
                order="id desc",
                limit=1,
            )
            if prev_bom:
                return prev_bom

        # 4. Tìm bất kỳ BOM khác của sản phẩm
        prev_bom = self.search(
            domain_base,
            order="id desc",
            limit=1,
        )
        return prev_bom or False

    def compute_bom_diff(self, target_bom_id):
        """So sánh đối soát khác biệt giữa 2 phiên bản BOM (BOM Diff Engine):
        Base BOM (self): Đại diện cho phiên bản MỚI (New).
        Target BOM (target_bom): Đại diện cho phiên bản CŨ (Old).

        Quy trình xử lý:
        1. Gom nhóm các dòng bom_line_ids của cả 2 BOM theo product_id (Aggregation).
        2. So khớp tập hợp sản phẩm (Set Operations).
        3. Phân loại 4 trạng thái:
           - added: Có ở BOM mới, không có ở BOM cũ (diff_qty = +new_qty).
           - removed: Có ở BOM cũ, không có ở BOM mới (diff_qty = -old_qty).
           - qty_changed: Có ở cả 2 nhưng số lượng khác nhau (diff_qty = new_qty - old_qty).
           - unchanged: Có ở cả 2 và số lượng bằng nhau (diff_qty = 0).
        4. Đóng gói kết quả theo Data Contract chuẩn hóa.
        """
        self.ensure_one()

        # Chuẩn hóa target_bom_id thành recordset
        if isinstance(target_bom_id, int):
            target_bom = self.browse(target_bom_id)
        elif isinstance(target_bom_id, models.Model):
            target_bom = target_bom_id
        elif isinstance(target_bom_id, str) and target_bom_id.strip().isdigit():
            target_bom = self.browse(int(target_bom_id.strip()))
        else:
            target_bom = self.browse()

        if not target_bom or not target_bom.exists():
            raise UserError(_("Không tìm thấy BOM mục tiêu để so sánh đối chiếu."))

        # 1. Trích xuất & Gom nhóm dòng BOM mới (Base BOM)
        dict_new = {}
        for line in self.bom_line_ids:
            prod = line.product_id
            if not prod:
                continue
            if prod.id not in dict_new:
                dict_new[prod.id] = {
                    "product": prod,
                    "qty": line.product_qty or 0.0,
                    "uom": line.uom_id or prod.uom_id,
                    "source_doc": line.source_id,
                    "line_ids": [line.id],
                }
            else:
                dict_new[prod.id]["qty"] += (line.product_qty or 0.0)
                dict_new[prod.id]["line_ids"].append(line.id)
                if not dict_new[prod.id]["source_doc"] and line.source_id:
                    dict_new[prod.id]["source_doc"] = line.source_id

        # Trích xuất & Gom nhóm dòng BOM cũ (Target BOM)
        dict_old = {}
        for line in target_bom.bom_line_ids:
            prod = line.product_id
            if not prod:
                continue
            if prod.id not in dict_old:
                dict_old[prod.id] = {
                    "product": prod,
                    "qty": line.product_qty or 0.0,
                    "uom": line.uom_id or prod.uom_id,
                    "source_doc": line.source_id,
                    "line_ids": [line.id],
                }
            else:
                dict_old[prod.id]["qty"] += (line.product_qty or 0.0)
                dict_old[prod.id]["line_ids"].append(line.id)
                if not dict_old[prod.id]["source_doc"] and line.source_id:
                    dict_old[prod.id]["source_doc"] = line.source_id

        # 2. So khớp tập hợp sản phẩm (Set Operations)
        all_product_ids = set(dict_new.keys()) | set(dict_old.keys())

        added_list = []
        removed_list = []
        qty_changed_list = []
        unchanged_list = []

        for p_id in all_product_ids:
            if p_id in dict_new and p_id not in dict_old:
                prod = dict_new[p_id]["product"]
                uom = dict_new[p_id]["uom"]
                cad_doc = dict_new[p_id]["source_doc"]
                old_qty = 0.0
                new_qty = float_round(dict_new[p_id]["qty"], precision_digits=3)
                diff_qty = new_qty
                item = {
                    "product_id": prod.id,
                    "product_code": prod.default_code or "",
                    "product_name": prod.name or prod.display_name,
                    "uom_name": uom.name if uom else "",
                    "state": "added",
                    "state_label": _("Thêm mới"),
                    "old_qty": old_qty,
                    "new_qty": new_qty,
                    "diff_qty": diff_qty,
                    "source_doc_name": cad_doc.name if cad_doc else "",
                }
                added_list.append(item)

            elif p_id in dict_old and p_id not in dict_new:
                prod = dict_old[p_id]["product"]
                uom = dict_old[p_id]["uom"]
                cad_doc = dict_old[p_id]["source_doc"]
                old_qty = float_round(dict_old[p_id]["qty"], precision_digits=3)
                new_qty = 0.0
                diff_qty = -old_qty
                item = {
                    "product_id": prod.id,
                    "product_code": prod.default_code or "",
                    "product_name": prod.name or prod.display_name,
                    "uom_name": uom.name if uom else "",
                    "state": "removed",
                    "state_label": _("Loại bỏ"),
                    "old_qty": old_qty,
                    "new_qty": new_qty,
                    "diff_qty": diff_qty,
                    "source_doc_name": cad_doc.name if cad_doc else "",
                }
                removed_list.append(item)

            else:
                prod = dict_new[p_id]["product"]
                uom = dict_new[p_id]["uom"] or dict_old[p_id]["uom"]
                cad_doc = dict_new[p_id]["source_doc"] or dict_old[p_id]["source_doc"]
                old_qty = float_round(dict_old[p_id]["qty"], precision_digits=3)
                new_qty = float_round(dict_new[p_id]["qty"], precision_digits=3)
                diff_qty = float_round(new_qty - old_qty, precision_digits=3)

                cmp_res = float_compare(new_qty, old_qty, precision_digits=3)
                if cmp_res != 0:
                    item = {
                        "product_id": prod.id,
                        "product_code": prod.default_code or "",
                        "product_name": prod.name or prod.display_name,
                        "uom_name": uom.name if uom else "",
                        "state": "qty_changed",
                        "state_label": _("Đổi số lượng"),
                        "old_qty": old_qty,
                        "new_qty": new_qty,
                        "diff_qty": diff_qty,
                        "source_doc_name": cad_doc.name if cad_doc else "",
                    }
                    qty_changed_list.append(item)
                else:
                    item = {
                        "product_id": prod.id,
                        "product_code": prod.default_code or "",
                        "product_name": prod.name or prod.display_name,
                        "uom_name": uom.name if uom else "",
                        "state": "unchanged",
                        "state_label": _("Giữ nguyên"),
                        "old_qty": old_qty,
                        "new_qty": new_qty,
                        "diff_qty": 0.0,
                        "source_doc_name": cad_doc.name if cad_doc else "",
                    }
                    unchanged_list.append(item)

        # 3. Sắp xếp thứ tự hiển thị: added -> removed -> qty_changed -> unchanged
        sort_key = lambda x: (x["product_code"] or "", x["product_name"] or "")
        added_list.sort(key=sort_key)
        removed_list.sort(key=sort_key)
        qty_changed_list.sort(key=sort_key)
        unchanged_list.sort(key=sort_key)

        diff_lines = added_list + removed_list + qty_changed_list + unchanged_list

        base_prod = self.product_id or (self.product_tmpl_id.product_variant_id if self.product_tmpl_id else False)
        target_prod = target_bom.product_id or (target_bom.product_tmpl_id.product_variant_id if target_bom.product_tmpl_id else False)

        return {
            "base_bom": {
                "id": self.id,
                "code": self.code or self.display_name,
                "product_name": (base_prod.name or base_prod.display_name) if base_prod else (self.product_tmpl_id.name if self.product_tmpl_id else ""),
                "product_code": (base_prod.default_code or "") if base_prod else (self.product_tmpl_id.default_code or ""),
                "revision": getattr(self, "engineering_revision", 0) or 0,
            },
            "target_bom": {
                "id": target_bom.id,
                "code": target_bom.code or target_bom.display_name,
                "product_name": (target_prod.name or target_prod.display_name) if target_prod else (target_bom.product_tmpl_id.name if target_bom.product_tmpl_id else ""),
                "product_code": (target_prod.default_code or "") if target_prod else (target_bom.product_tmpl_id.default_code or ""),
                "revision": getattr(target_bom, "engineering_revision", 0) or 0,
            },
            "summary": {
                "total_added": len(added_list),
                "total_removed": len(removed_list),
                "total_qty_changed": len(qty_changed_list),
                "total_unchanged": len(unchanged_list),
                "has_difference": bool(added_list or removed_list or qty_changed_list),
            },
            "diff_lines": diff_lines,
            "added": added_list,
            "removed": removed_list,
            "qty_changed": qty_changed_list,
            "unchanged": unchanged_list,
        }

    # =========================================================================
    # GIAO DIỆN BÁO CÁO ĐỐI SOÁT BOM (VISUAL DIFF WIZARD ACTION - HT-3.2)
    # =========================================================================
    def action_open_bom_diff_wizard(self):
        """Mở Popup Wizard đối soát khác biệt Định mức vật tư BOM (HT-3.2)."""
        self.ensure_one()
        target_bom = self.get_previous_revision_bom()
        return {
            "name": _("So sánh đối soát BOM"),
            "type": "ir.actions.act_window",
            "res_model": "plm.bom.diff.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_base_bom_id": self.id,
                "default_target_bom_id": target_bom.id if target_bom else False,
                "dialog_size": "extra-large",
            },
        }


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
                        'Thêm linh kiện: <strong>%(code)s%(name)s</strong> (Số lượng: <strong>%(qty)s</strong>%(uom)s)'
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
        1. Đổi số lượng (product_qty): Ghi thông báo số lượng cũ và mới.
        2. Thay thế linh kiện (product_id): Ghi thông báo cảnh báo đổi mã vật tư.
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
                            'Đổi số lượng linh kiện: <strong>%(code)s%(name)s</strong> đổi từ <strong>%(old_qty)s</strong> sang <strong>%(new_qty)s</strong>%(uom)s'
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
                            'Thay thế linh kiện: Thay đổi từ <strong>%(old_code)s%(old_name)s</strong> sang <strong>%(new_code)s%(new_name)s</strong>'
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
        Ghi nhận thông báo vào Chatter của BOM cha trước khi xóa vĩnh viễn dòng.
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
                    'Bỏ linh kiện: <strong>%(code)s%(name)s</strong> (Số lượng cũ: <strong>%(qty)s</strong>%(uom)s)'
                    '</p>'
                ) % {
                    "code": info["code_str"],
                    "name": info["prod_name"],
                    "qty": _format_qty(info["qty"]),
                    "uom": info["uom_str"],
                }
                bom.sudo().message_post(body=body)

        return res
