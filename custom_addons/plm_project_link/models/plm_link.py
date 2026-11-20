from odoo import _, fields, models
from odoo.exceptions import UserError
from odoo.addons.plm_project.models.project_project import FOLDERS

MANAGER_GROUP = 'plm_project.group_plm_project_manager'


def _check_manager(env):
    if not env.user.has_group(MANAGER_GROUP):
        raise UserError(_('Chỉ nhóm Quản lý dự án mới được gỡ.'))


class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    plm_project_id = fields.Many2one('project.project', string='Dự án', index=True, ondelete='set null')
    plm_folder = fields.Selection(FOLDERS, string='Thư mục dự án')

    def action_plm_unassign(self):
        _check_manager(self.env)
        for doc in self:
            project = doc.plm_project_id
            if not project:
                continue
            name = doc.engineering_code or doc.name
            doc.with_context(check=False).write({'plm_project_id': False, 'plm_folder': False})
            project.message_post(
                body=_('Đã gỡ tài liệu "%s" khỏi dự án.') % name,
                subtype_xmlid='mail.mt_note')
        return True


class ProductProduct(models.Model):
    _inherit = 'product.product'

    plm_project_id = fields.Many2one('project.project', string='Dự án', index=True, ondelete='set null')
    plm_folder = fields.Selection(FOLDERS, string='Thư mục dự án')

    def action_plm_unassign(self):
        _check_manager(self.env)
        for part in self:
            project = part.plm_project_id
            if not project:
                continue
            name = part.engineering_code or part.display_name
            part.with_context(check=False).write({'plm_project_id': False, 'plm_folder': False})
            project.message_post(
                body=_('Đã gỡ part "%s" khỏi dự án.') % name,
                subtype_xmlid='mail.mt_note')
        return True


class ProjectProject(models.Model):
    _inherit = 'project.project'

    plm_doc_info_ids = fields.One2many('ir.attachment', 'plm_project_id', domain=[('plm_folder', '=', 'info')])
    plm_doc_cad_ids = fields.One2many('ir.attachment', 'plm_project_id', domain=[('plm_folder', '=', 'cad')])
    plm_doc_production_ids = fields.One2many('ir.attachment', 'plm_project_id', domain=[('plm_folder', '=', 'production')])
    plm_doc_quality_ids = fields.One2many('ir.attachment', 'plm_project_id', domain=[('plm_folder', '=', 'quality')])
    plm_doc_maintenance_ids = fields.One2many('ir.attachment', 'plm_project_id', domain=[('plm_folder', '=', 'maintenance')])
    plm_part_info_ids = fields.One2many('product.product', 'plm_project_id', domain=[('plm_folder', '=', 'info')])
    plm_part_cad_ids = fields.One2many('product.product', 'plm_project_id', domain=[('plm_folder', '=', 'cad')])
    plm_part_production_ids = fields.One2many('product.product', 'plm_project_id', domain=[('plm_folder', '=', 'production')])
    plm_part_quality_ids = fields.One2many('product.product', 'plm_project_id', domain=[('plm_folder', '=', 'quality')])
    plm_part_maintenance_ids = fields.One2many('product.product', 'plm_project_id', domain=[('plm_folder', '=', 'maintenance')])
