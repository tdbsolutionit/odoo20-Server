# -*- coding: utf-8 -*-
from odoo import _, models
from odoo.exceptions import AccessError


class IrBinary(models.AbstractModel):
    _inherit = "ir.binary"

    def _record_to_stream(self, record, field_name):
        if record and record._name == "dms.file" and field_name == "content":
            if not record.can_download:
                raise AccessError(
                    _("Tệp tin '%s' đã bị khóa tải về. Vui lòng liên hệ Quản lý hoặc Quản trị viên để được cấp quyền tải về!")
                    % record.name
                )
        return super()._record_to_stream(record, field_name)
