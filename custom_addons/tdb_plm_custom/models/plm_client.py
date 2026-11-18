# -*- coding: utf-8 -*-
# Copyright 2026 TDB Solution Co., Ltd.

import logging
from odoo import models, api

_logger = logging.getLogger(__name__)


class PlmClient(models.TransientModel):
    _inherit = "plm.client"

    @api.model
    def get_checkout_snapshot(self, stamp="", file_names=None):
        """Mở rộng get_checkout_snapshot cho SolidWorks:
        Tài liệu đã Ban hành (released) hoặc Xác nhận (confirmed) vẫn được phép Check-out
        tải về máy trạm để tham chiếu / mở CAD.
        Chỉ những tài liệu Lỗi thời (obsoleted) mới bị chặn không cho Check-out.
        """
        res = super().get_checkout_snapshot(stamp=stamp, file_names=file_names)
        if file_names and isinstance(res, dict) and "states" in res:
            # Lọc bỏ các trạng thái không phải obsoleted để CAD helper không chặn Check-out
            res["states"] = [s for s in res.get("states", []) if s.get("state") == "obsoleted"]
        return res
