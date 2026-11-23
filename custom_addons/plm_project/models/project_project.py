from odoo import fields, models


class ProjectProject(models.Model):
    _inherit = 'project.project'

    allowed_internal_user_ids = fields.Many2many(
        domain=lambda self: (
            "[('share', '=', False), "
            f"('all_group_ids', 'in', {self.env.ref('project.group_project_user').id}), "
            "('company_ids', '=?', company_id)]"
        ),
    )


FOLDERS = [
    ('info', 'Thông tin ban đầu'),
    ('cad', 'Design CAD'),
    ('production', 'Sản xuất và mua hàng'),
    ('quality', 'Quản lý chất lượng'),
    ('maintenance', 'Bảo trì'),
]
