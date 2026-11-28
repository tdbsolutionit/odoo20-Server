# -*- coding: utf-8 -*-
# Copyright 2026 TDB Solution Co., Ltd.
"""
Mở rộng model product.template cho phân hệ PLM.
Tuân thủ Layer 2: Quản lý schema dữ liệu và business logic cốt lõi.
Chuỗi gốc tiếng Anh chuẩn quốc tế - Hỗ trợ đa ngôn ngữ Anh / Việt.
"""
import logging
from odoo import models, fields

_logger = logging.getLogger(__name__)


class ProductTemplate(models.Model):
    """
    Kế thừa product.template để hỗ trợ phân hệ PLM.
    """
    _inherit = "product.template"

    engineering_revision = fields.Integer(string="Engineering Revision")
    engineering_material = fields.Char(string="CAD Material")
    engineering_surface = fields.Char(string="CAD Surface Finishing")
    kit_bom = fields.Boolean(string="KIT BoM Type")
