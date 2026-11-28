from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.addons.plm_project.models.project_project import FOLDERS


class PlmProjectItem(models.Model):
    _name = 'plm.project.item'
    _description = 'Mục gốc PLM gắn vào thư mục dự án'
    _order = 'project_id, folder, id'

    project_id = fields.Many2one('project.project', required=True, index=True, ondelete='cascade')
    folder = fields.Selection(FOLDERS, string='Thư mục', required=True, index=True)
    document_id = fields.Many2one('ir.attachment', string='Tài liệu', index=True, ondelete='cascade')
    part_id = fields.Many2one('product.product', string='Part', index=True, ondelete='cascade')
    include_children = fields.Boolean(string='Kèm cấu trúc con', default=True)
    assigned_uid = fields.Many2one('res.users', string='Người gán', default=lambda self: self.env.user, readonly=True)
    assigned_date = fields.Datetime(string='Ngày gán', default=fields.Datetime.now, readonly=True)
    item_name = fields.Char(string='Mục', compute='_compute_item_name')

    _one_target = models.Constraint(
        'CHECK ((document_id IS NOT NULL)::int + (part_id IS NOT NULL)::int = 1)',
        'Mỗi dòng phải gắn đúng một tài liệu hoặc một part.')
    _uniq_doc = models.Constraint(
        'unique (project_id, folder, document_id)', 'Tài liệu đã có trong thư mục này.')
    _uniq_part = models.Constraint(
        'unique (project_id, folder, part_id)', 'Part đã có trong thư mục này.')

    @api.depends('document_id', 'part_id')
    def _compute_item_name(self):
        for rec in self:
            doc, part = rec.document_id, rec.part_id
            rec.item_name = (
                (doc.engineering_code or doc.name) if doc
                else (part.engineering_code or part.display_name) if part else False)

    def action_plm_remove(self):
        if not self.env.user.has_group('plm_project.group_plm_project_manager'):
            raise UserError(_('Chỉ nhóm Quản lý dự án mới được gỡ.'))
        for item in self:
            project, name = item.project_id, item.item_name
            item.unlink()
            project.message_post(
                body=_('Đã gỡ mục gốc "%s" khỏi dự án.') % name,
                subtype_xmlid='mail.mt_note')
        return True
