# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError
try:
    from odoo.orm.domains import Domain as expression
except ImportError:
    from odoo.osv import expression


class DmsDirectory(models.Model):
    _inherit = "dms.directory"

    is_recycle_bin = fields.Boolean(
        string="Là Thùng Rác (Recycle Bin)",
        default=False,
        index=True,
        help="Thư mục đặc biệt lưu trữ các tệp tin đã bị xóa, chỉ dành cho Sếp/Admin quản lý và khôi phục.",
    )
    default_allow_download = fields.Boolean(
        string="Mặc định cho phép tải về",
        default=True,
        help="Nếu bỏ chọn, mọi tệp tin tải lên thư mục này sẽ mặc định không cho phép nhân viên tải về máy.",
    )
    is_sharing = fields.Boolean(
        string="Phòng ban Chia sẻ (Dùng chung)",
        default=False,
        compute="_compute_is_sharing",
        store=True,
        readonly=False,
        recursive=True,
        tracking=True,
        help="Nếu bật, tất cả nhân viên trong công ty đều được xem thư mục này.",
    )
    available_leader_ids = fields.Many2many(
        comodel_name="res.users",
        compute="_compute_available_leader_ids",
        string="Nhân viên thuộc phòng ban",
        help="Danh sách nhân viên thuộc các nhóm truy cập của phòng ban này.",
    )
    leader_ids = fields.Many2many(
        comodel_name="res.users",
        relation="dms_directory_leader_rel",
        column1="directory_id",
        column2="user_id",
        string="Trưởng phòng / Quản lý",
        domain="[('id', 'in', available_leader_ids)]",
        tracking=True,
        help="Trưởng phòng hoặc người được Sếp/Admin giao quyền quản lý thư mục này.",
    )
    can_manage_directory = fields.Boolean(
        string="Có quyền quản lý thư mục",
        compute="_compute_can_manage_directory",
        help="Xác định người dùng hiện tại có quyền quản lý cấu hình thư mục này hay không.",
    )

    @api.depends("parent_id.is_sharing")
    def _compute_is_sharing(self):
        for record in self:
            if record.parent_id and getattr(record.parent_id, 'is_sharing', False):
                record.is_sharing = True
            elif not record.is_sharing:
                record.is_sharing = False

    def _is_sharing_directory(self):
        curr = self.sudo()
        while curr:
            if getattr(curr, 'is_sharing', False):
                return True
            curr = curr.parent_id
        return False

    def _is_user_leader(self, user=None):
        user = user or self.env.user
        if not user:
            return False
        if user._is_superuser() or user.has_group("base.group_system"):
            return True
        curr = self.sudo()
        user_id = user.id
        while curr:
            if user_id in curr.leader_ids.ids:
                return True
            curr = curr.parent_id
        return False

    @api.depends("leader_ids", "parent_id")
    def _compute_available_leader_ids(self):
        root_user = self.env.ref("base.user_root", raise_if_not_found=False)
        root_id = root_user.id if root_user else 1
        for record in self:
            groups = getattr(record, 'complete_group_ids', False) or getattr(record, 'group_ids', False)
            if groups:
                users = groups.mapped("users")
                if hasattr(groups, 'explicit_user_ids'):
                    users = users | groups.mapped("explicit_user_ids")
                record.available_leader_ids = users.filtered(lambda u: u.active and u.id != root_id)
            else:
                record.available_leader_ids = self.env["res.users"].search([("share", "=", False), ("id", "!=", root_id)])

    @api.depends("leader_ids", "parent_id")
    def _compute_can_manage_directory(self):
        user = self.env.user
        is_admin = user._is_superuser() or user.has_group("base.group_system")
        for record in self:
            record.can_manage_directory = is_admin or record._is_user_leader(user)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if "is_root_directory" in fields_list and "is_root_directory" not in res and not res.get("parent_id"):
            res["is_root_directory"] = True
        if "storage_id" in fields_list and not res.get("storage_id"):
            storage = self.env["dms.storage"].sudo().search([], limit=1)
            if not storage:
                storage = self.env["dms.storage"].sudo().create({
                    "name": "Documents Storage",
                    "save_type": "database",
                })
            res["storage_id"] = storage.id
        return res

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, **kwargs):
        """Ẩn hoàn toàn thư mục Thùng Rác đối với người dùng không phải Quản trị viên/Sếp."""
        if not self.env.su:
            user = self.env.user
            is_admin = user._is_superuser() or user.has_group("base.group_system")
            if not is_admin:
                domain = expression.AND([domain, [("is_recycle_bin", "=", False)]])
        return super()._search(domain, offset=offset, limit=limit, order=order, **kwargs)

    def write(self, vals):
        if not self.env.su:
            user = self.env.user
            is_admin = user._is_superuser() or user.has_group("base.group_system")
            if "is_recycle_bin" in vals and not is_admin:
                raise AccessError(_("Chỉ Quản trị viên (Sếp/Admin) mới có quyền cấu hình Thùng Rác!"))
            for record in self:
                if record.is_recycle_bin and not is_admin:
                    raise AccessError(_("Chỉ Quản trị viên (Sếp/Admin) mới có quyền chỉnh sửa Thùng Rác!"))
        return super().write(vals)

    def unlink(self):
        if not self.env.su:
            user = self.env.user
            is_admin = user._is_superuser() or user.has_group("base.group_system")
            for record in self:
                if record.is_recycle_bin and not is_admin:
                    raise AccessError(_("Không thể xóa thư mục Thùng Rác của hệ thống!"))
        return super().unlink()

    def action_open_create_subfolder_wizard(self):
        """Mở wizard tạo thư mục con bên trong thư mục / phòng ban hiện tại."""
        self.ensure_one()
        return {
            "name": _("Tạo thư mục con trong %s") % self.name,
            "type": "ir.actions.act_window",
            "res_model": "dms.create.subfolder.wizard",
            "views": [[False, "form"]],
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_parent_id": self.id,
                "default_default_allow_download": self.default_allow_download,
            },
        }

    @api.model
    def get_recycle_bin(self):
        """Lấy bản ghi Thùng Rác duy nhất của hệ thống, tự động tạo nếu chưa có."""
        recycle = self.sudo().search([("is_recycle_bin", "=", True)], limit=1)
        if not recycle:
            storage = self.env["dms.storage"].sudo().search([], limit=1)
            if not storage:
                storage = self.env["dms.storage"].sudo().create({
                    "name": "Documents Storage",
                    "save_type": "database",
                })
            recycle = self.sudo().create({
                "name": "00. Thùng Rác (Recycle Bin)",
                "is_root_directory": True,
                "is_recycle_bin": True,
                "default_allow_download": False,
                "storage_id": storage.id,
            })
        elif not recycle.storage_id:
            storage = self.env["dms.storage"].sudo().search([], limit=1)
            if not storage:
                storage = self.env["dms.storage"].sudo().create({
                    "name": "Documents Storage",
                    "save_type": "database",
                })
            recycle.sudo().write({"storage_id": storage.id})
        return recycle

    @api.model
    def _init_recycle_bin(self):
        """Khởi tạo phòng ban Thùng Rác nếu chưa tồn tại."""
        self.get_recycle_bin()
