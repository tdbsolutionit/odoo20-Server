from odoo import models
from odoo.addons.plm_project.models.project_project import FOLDERS

DOC_KINDS = ('RfTree', 'LyTree', 'HiTree')


class ProjectProject(models.Model):
    _inherit = 'project.project'

    def _plm_scope_doc_ids(self, folder=None, kinds=DOC_KINDS):
        """Id tài liệu thuộc phạm vi dự án: mục gốc + toàn bộ con cháu."""
        self.ensure_one()
        project_id = self._origin.id
        if not project_id:
            return []
        self.env['plm.project.item'].flush_model()
        self.env['ir.attachment.relation'].flush_model()
        folder_sql = 'AND folder = %s' if folder else ''
        params = [project_id] + ([folder] if folder else []) + [tuple(kinds)]
        self.env.cr.execute(f"""
            WITH RECURSIVE tree(id, expand) AS (
                SELECT document_id, include_children
                  FROM plm_project_item
                 WHERE project_id = %s AND document_id IS NOT NULL {folder_sql}
                UNION
                SELECT r.child_id, TRUE
                  FROM tree t
                  JOIN ir_attachment_relation r ON r.parent_id = t.id
                 WHERE t.expand AND r.link_kind IN %s AND r.child_id IS NOT NULL
            )
            SELECT DISTINCT id FROM tree
        """, params)
        return [row[0] for row in self.env.cr.fetchall()]

    def _plm_scope_part_ids(self, folder=None):
        """Id part thuộc phạm vi dự án: mục gốc + toàn bộ part con theo BoM."""
        self.ensure_one()
        project_id = self._origin.id
        if not project_id:
            return []
        self.env['plm.project.item'].flush_model()
        folder_sql = 'AND folder = %s' if folder else ''
        params = [project_id] + ([folder] if folder else [])
        self.env.cr.execute(f"""
            WITH RECURSIVE tree(id, expand) AS (
                SELECT part_id, include_children
                  FROM plm_project_item
                 WHERE project_id = %s AND part_id IS NOT NULL {folder_sql}
                UNION
                SELECT l.product_id, TRUE
                  FROM tree t
                  JOIN product_product pp ON pp.id = t.id
                  JOIN mrp_bom b ON b.active
                                AND b.product_tmpl_id = pp.product_tmpl_id
                                AND (b.product_id IS NULL OR b.product_id = t.id)
                  JOIN mrp_bom_line l ON l.bom_id = b.id
                 WHERE t.expand AND l.product_id IS NOT NULL
            )
            SELECT DISTINCT id FROM tree
        """, params)
        return [row[0] for row in self.env.cr.fetchall()]

    def action_plm_open_scope_docs(self):
        self.ensure_one()
        folder = self.env.context.get('plm_scope_folder') or False
        label = dict(FOLDERS).get(folder)
        return {
            'type': 'ir.actions.act_window',
            'name': 'Tài liệu: %s' % label if label else 'Tài liệu của dự án',
            'res_model': 'ir.attachment',
            'view_mode': 'list,form',
            'domain': [('plm_scope_project_id', '=', self.id)],
            'context': {'odooPLM': True, 'plm_scope_folder': folder},
        }

    def action_plm_open_scope_parts(self):
        self.ensure_one()
        folder = self.env.context.get('plm_scope_folder') or False
        label = dict(FOLDERS).get(folder)
        return {
            'type': 'ir.actions.act_window',
            'name': 'Part: %s' % label if label else 'Part của dự án',
            'res_model': 'product.product',
            'view_mode': 'list,form',
            'domain': [('plm_scope_project_id', '=', self.id)],
            'context': {'plm_scope_folder': folder},
        }
