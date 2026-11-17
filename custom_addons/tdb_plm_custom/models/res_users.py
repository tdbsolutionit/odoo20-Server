# -*- coding: utf-8 -*-
# Copyright 2026 TDB Solution Co., Ltd.
"""
Mo rong res.users de mac dinh mui gio Viet Nam (GMT+7 / Asia/Ho_Chi_Minh) cho moi tai khoan.
"""
from odoo import models, fields, api


class ResUsers(models.Model):
    _inherit = "res.users"

    tz = fields.Selection(default="Asia/Ho_Chi_Minh")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("tz"):
                vals["tz"] = "Asia/Ho_Chi_Minh"
        return super().create(vals_list)
