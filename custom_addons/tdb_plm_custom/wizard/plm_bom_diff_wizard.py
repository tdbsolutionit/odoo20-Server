# -*- coding: utf-8 -*-
# Copyright 2026 TDB Solution Co., Ltd.
"""
Wizard Đối soát Khác biệt Định mức Vật tư (BOM Visual Diff Wizard - HT-3.2):
1. plm.bom.diff.wizard: TransientModel Header quản lý 2 bản ghi BOM so sánh, bộ lọc và các thẻ thống kê.
2. plm.bom.diff.line: TransientModel Detail hiển thị từng dòng linh kiện kèm trạng thái biến động và tô màu trực quan.
"""
import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PlmBomDiffWizard(models.TransientModel):
    _name = "plm.bom.diff.wizard"
    _description = "BOM Comparison & Diff Wizard"

    base_bom_id = fields.Many2one(
        "mrp.bom",
        string="BOM hiện tại (Mới)",
        required=True,
        readonly=True,
        help="Định mức vật tư hiện tại / phiên bản mới làm gốc đối chiếu",
    )
    target_bom_id = fields.Many2one(
        "mrp.bom",
        string="BOM đối chiếu (Phiên bản trước)",
        required=True,
        domain="[('id', '!=', base_bom_id)]",
        help="Định mức vật tư mục tiêu để đối chiếu (thường là phiên bản cũ)",
    )
    filter_state = fields.Selection(
        [
            ("changes_only", "Chỉ xem thay đổi (+ / -)"),
            ("all", "Toàn bộ linh kiện"),
        ],
        string="Bộ lọc hiển thị",
        default="changes_only",
        required=True,
    )

    # Thống kê tổng quan (Summary Metrics)
    total_added = fields.Integer(string="Thêm mới", readonly=True)
    total_removed = fields.Integer(string="Loại bỏ", readonly=True)
    total_qty_changed = fields.Integer(string="Đổi số lượng", readonly=True)
    total_unchanged = fields.Integer(string="Giữ nguyên", readonly=True)
    has_difference = fields.Boolean(string="Có khác biệt", readonly=True)

    diff_line_ids = fields.One2many(
        "plm.bom.diff.line",
        "wizard_id",
        string="Chi tiết đối soát",
        readonly=True,
    )

    @api.model
    def default_get(self, fields_list):
        """Khởi tạo Wizard: Tự động bắt cặp BOM cũ/mới và nạp sẵn dữ liệu đối soát."""
        res = super().default_get(fields_list)
        active_id = self.env.context.get("active_id")
        active_model = self.env.context.get("active_model")

        base_bom = False
        if res.get("base_bom_id"):
            base_bom = self.env["mrp.bom"].browse(res["base_bom_id"])
        elif active_model == "mrp.bom" and active_id:
            base_bom = self.env["mrp.bom"].browse(active_id)
            res["base_bom_id"] = base_bom.id

        target_bom = False
        if res.get("target_bom_id"):
            target_bom = self.env["mrp.bom"].browse(res["target_bom_id"])
        elif base_bom and base_bom.exists():
            prev_bom = base_bom.get_previous_revision_bom()
            if prev_bom:
                target_bom = prev_bom
                res["target_bom_id"] = prev_bom.id

        # Nếu có cả 2 BOM, tính toán ngay dữ liệu ban đầu cho form popup
        if base_bom and target_bom and base_bom.exists() and target_bom.exists():
            try:
                diff_res = base_bom.compute_bom_diff(target_bom)
                summary = diff_res.get("summary", {})
                res["total_added"] = summary.get("total_added", 0)
                res["total_removed"] = summary.get("total_removed", 0)
                res["total_qty_changed"] = summary.get("total_qty_changed", 0)
                res["total_unchanged"] = summary.get("total_unchanged", 0)
                res["has_difference"] = summary.get("has_difference", False)

                filter_st = res.get("filter_state", "changes_only")
                lines = []
                for item in diff_res.get("diff_lines", []):
                    if filter_st == "changes_only" and item.get("state") == "unchanged":
                        continue
                    lines.append((0, 0, {
                        "product_id": item.get("product_id"),
                        "product_code": item.get("product_code", ""),
                        "product_name": item.get("product_name", ""),
                        "state": item.get("state", "unchanged"),
                        "state_label": item.get("state_label", ""),
                        "old_qty": item.get("old_qty", 0.0),
                        "new_qty": item.get("new_qty", 0.0),
                        "diff_qty": item.get("diff_qty", 0.0),
                        "uom_name": item.get("uom_name", ""),
                        "source_doc_name": item.get("source_doc_name", ""),
                    }))
                res["diff_line_ids"] = lines
            except Exception as e:
                _logger.warning(f"[TDB PLM] Lỗi tính toán đối soát BOM ban đầu: {e}")

        return res

    @api.onchange("target_bom_id", "filter_state")
    def _onchange_target_bom_or_filter(self):
        """Tính toán lại đối soát khác biệt khi chọn BOM đối chiếu khác hoặc chuyển đổi bộ lọc."""
        if not self.base_bom_id or not self.target_bom_id:
            self.total_added = 0
            self.total_removed = 0
            self.total_qty_changed = 0
            self.total_unchanged = 0
            self.has_difference = False
            self.diff_line_ids = [(5, 0, 0)]
            return

        diff_res = self.base_bom_id.compute_bom_diff(self.target_bom_id)
        summary = diff_res.get("summary", {})
        self.total_added = summary.get("total_added", 0)
        self.total_removed = summary.get("total_removed", 0)
        self.total_qty_changed = summary.get("total_qty_changed", 0)
        self.total_unchanged = summary.get("total_unchanged", 0)
        self.has_difference = summary.get("has_difference", False)

        lines_to_show = []
        for item in diff_res.get("diff_lines", []):
            if self.filter_state == "changes_only" and item.get("state") == "unchanged":
                continue
            lines_to_show.append((0, 0, {
                "product_id": item.get("product_id"),
                "product_code": item.get("product_code", ""),
                "product_name": item.get("product_name", ""),
                "state": item.get("state", "unchanged"),
                "state_label": item.get("state_label", ""),
                "old_qty": item.get("old_qty", 0.0),
                "new_qty": item.get("new_qty", 0.0),
                "diff_qty": item.get("diff_qty", 0.0),
                "uom_name": item.get("uom_name", ""),
                "source_doc_name": item.get("source_doc_name", ""),
            }))

        self.diff_line_ids = [(5, 0, 0)] + lines_to_show


class PlmBomDiffLine(models.TransientModel):
    _name = "plm.bom.diff.line"
    _description = "BOM Comparison Detail Line"
    _order = "id asc"

    wizard_id = fields.Many2one("plm.bom.diff.wizard", ondelete="cascade", index=True)
    product_id = fields.Many2one("product.product", string="Linh kiện", readonly=True)
    product_code = fields.Char(string="Mã linh kiện", readonly=True)
    product_name = fields.Char(string="Tên linh kiện", readonly=True)
    state = fields.Selection(
        [
            ("added", "Thêm mới"),
            ("removed", "Loại bỏ"),
            ("qty_changed", "Đổi số lượng"),
            ("unchanged", "Giữ nguyên"),
        ],
        string="Trạng thái",
        readonly=True,
    )
    state_label = fields.Char(string="Nhãn trạng thái", readonly=True)
    old_qty = fields.Float(string="SL Cũ (Target)", readonly=True)
    new_qty = fields.Float(string="SL Mới (Base)", readonly=True)
    diff_qty = fields.Float(string="Độ lệch (+/-)", readonly=True)
    uom_name = fields.Char(string="ĐVT", readonly=True)
    source_doc_name = fields.Char(string="Bản vẽ CAD", readonly=True)
