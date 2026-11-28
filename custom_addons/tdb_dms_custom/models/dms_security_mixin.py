# -*- coding: utf-8 -*-
from odoo import api, models
from odoo.fields import Domain
from odoo.tools import SQL


class DmsSecurityMixin(models.AbstractModel):
    _inherit = "dms.security.mixin"

    def _is_dms_admin_or_manager(self):
        user = self.env.user
        return (
            self.env.su
            or user._is_superuser()
            or user.has_group("base.group_system")
            or user.has_group("dms.group_dms_manager")
        )

    def _compute_permissions(self):
        super()._compute_permissions()
        for record in self:
            if record._is_dms_admin_or_manager():
                for operation in ("create", "read", "unlink", "write"):
                    record[f"permission_{operation}"] = True

    @api.model
    def _get_permission_domain(self, operator, value, operation):
        _self = self.sudo(False) if self.env.su else self
        if (
            _self.env.su
            or _self.env.user._is_superuser()
            or _self.env.user.has_group("base.group_system")
            or _self.env.user.has_group("dms.group_dms_manager")
        ):
            positive = operator == "in"
            return Domain.TRUE if positive else Domain.FALSE
        return super()._get_permission_domain(operator, value, operation)

    def _filtered_access(self, operation):
        if not self or self._is_dms_admin_or_manager():
            return self
        return super()._filtered_access(operation)

    def filtered_domain(self, domain):
        if self._is_dms_admin_or_manager():
            return self
        return super().filtered_domain(domain)

    @api.model
    def _get_access_groups_query(self, operation):
        base_sql = super()._get_access_groups_query(operation)
        uid = self.env.uid
        select_parts = [base_sql]

        # 1. Leader của thư mục có quyền trên thư mục và tất cả thư mục con
        try:
            self.env.cr.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name = 'dms_directory_leader_rel'"
            )
            has_leader_table = bool(self.env.cr.fetchone())
        except Exception:
            has_leader_table = False

        if has_leader_table:
            leader_query = SQL(
                """(
                SELECT
                    child.id
                FROM
                    dms_directory child
                    INNER JOIN dms_directory parent
                        ON child.parent_path LIKE CONCAT(parent.parent_path, '%%')
                    INNER JOIN dms_directory_leader_rel lrel
                        ON lrel.directory_id = parent.id
                WHERE
                    lrel.user_id = %s
                )""",
                uid,
            )
            select_parts.append(leader_query)

        # 2. Phòng ban Chia sẻ (is_sharing):
        # Mọi nhân viên nội bộ (base.group_user) đều được xem (read) và tải tệp tin lên (create)
        if self.env.user.has_group("base.group_user"):
            is_file_model = self._name == "dms.file"
            allow_sharing = is_file_model or operation in ("read", "create")
            if allow_sharing:
                try:
                    self.env.cr.execute(
                        "SELECT 1 FROM information_schema.columns WHERE table_name = 'dms_directory' AND column_name = 'is_sharing'"
                    )
                    has_sharing_col = bool(self.env.cr.fetchone())
                except Exception:
                    has_sharing_col = False

                if has_sharing_col:
                    sharing_query = SQL(
                        """(
                        SELECT
                            id
                        FROM
                            dms_directory
                        WHERE
                            is_sharing = TRUE
                        )"""
                    )
                    select_parts.append(sharing_query)

        if len(select_parts) == 1:
            return base_sql
        return SQL("(%s)", SQL(" UNION ").join(select_parts))
