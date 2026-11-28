from odoo import _, fields, models
from odoo.exceptions import UserError


def _scope_search(env, operator, value, kind):
    if operator not in ('=', 'in'):
        raise UserError(_('Chỉ hỗ trợ toán tử "=" hoặc "in" cho phạm vi dự án.'))
    ids = [value] if isinstance(value, int) else list(value or [])
    out = set()
    for project in env['project.project'].browse(ids).exists():
        folder = env.context.get('plm_scope_folder') or None
        out.update(project._plm_scope_doc_ids(folder=folder) if kind == 'doc'
                   else project._plm_scope_part_ids(folder=folder))
    return [('id', 'in', list(out))]


class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    plm_scope_project_id = fields.Many2one(
        'project.project', string='Thuộc phạm vi dự án',
        compute='_compute_plm_scope_project_id',
        search='_search_plm_scope_project_id')

    def _compute_plm_scope_project_id(self):
        for rec in self:
            rec.plm_scope_project_id = False

    def _search_plm_scope_project_id(self, operator, value):
        return _scope_search(self.env, operator, value, 'doc')


class ProductProduct(models.Model):
    _inherit = 'product.product'

    plm_scope_project_id = fields.Many2one(
        'project.project', string='Thuộc phạm vi dự án',
        compute='_compute_plm_scope_project_id',
        search='_search_plm_scope_project_id')

    def _compute_plm_scope_project_id(self):
        for rec in self:
            rec.plm_scope_project_id = False

    def _search_plm_scope_project_id(self, operator, value):
        return _scope_search(self.env, operator, value, 'part')


class ProjectProject(models.Model):
    _inherit = 'project.project'

    plm_scope_doc_count = fields.Integer(
        string='Số tài liệu', compute='_compute_plm_scope_counts')
    plm_scope_part_count = fields.Integer(
        string='Số part', compute='_compute_plm_scope_counts')

    def _compute_plm_scope_counts(self):
        for project in self:
            project.plm_scope_doc_count = len(project._plm_scope_doc_ids())
            project.plm_scope_part_count = len(project._plm_scope_part_ids())

    plm_item_info_ids = fields.One2many(
        'plm.project.item', 'project_id', domain=[('folder', '=', 'info')])
    plm_item_cad_ids = fields.One2many(
        'plm.project.item', 'project_id', domain=[('folder', '=', 'cad')])
    plm_item_production_ids = fields.One2many(
        'plm.project.item', 'project_id', domain=[('folder', '=', 'production')])
    plm_item_quality_ids = fields.One2many(
        'plm.project.item', 'project_id', domain=[('folder', '=', 'quality')])
    plm_item_maintenance_ids = fields.One2many(
        'plm.project.item', 'project_id', domain=[('folder', '=', 'maintenance')])
