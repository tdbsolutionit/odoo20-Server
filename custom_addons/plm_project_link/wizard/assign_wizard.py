from odoo import _, api, fields, models
from odoo.addons.plm_project.models.project_project import FOLDERS
from odoo.addons.plm_project_link.models.project_scope import DOC_KINDS


class PlmProjectAssignWizard(models.TransientModel):
    _name = 'plm.project.assign.wizard'
    _description = 'Gán tài liệu và part vào dự án'

    project_id = fields.Many2one('project.project', required=True)
    plm_folder = fields.Selection(FOLDERS, string='Thư mục', required=True)
    document_ids = fields.Many2many(
        'ir.attachment', 'plm_assign_wiz_doc_rel', 'wizard_id', 'attachment_id',
        string='Tài liệu gốc', bypass_search_access=True)
    part_ids = fields.Many2many(
        'product.product', 'plm_assign_wiz_part_rel', 'wizard_id', 'product_id',
        string='Part gốc', bypass_search_access=True)
    include_children = fields.Boolean(string='Kèm toàn bộ cấu trúc con', default=True)
    doc_count = fields.Integer(string='Tài liệu sẽ nằm trong phạm vi', compute='_compute_counts')
    part_count = fields.Integer(string='Part sẽ nằm trong phạm vi', compute='_compute_counts')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        project = self.env['project.project'].browse(self.env.context.get('active_id'))
        if project.exists():
            res['project_id'] = project.id
        return res

    @api.depends('document_ids', 'part_ids', 'include_children')
    def _compute_counts(self):
        cr = self.env.cr
        self.env['ir.attachment.relation'].flush_model()
        for w in self:
            docs = [i for i in w.document_ids.ids if isinstance(i, int)]
            parts = [i for i in w.part_ids.ids if isinstance(i, int)]
            w.doc_count, w.part_count = len(docs), len(parts)
            if not w.include_children:
                continue
            if docs:
                cr.execute("""
                    WITH RECURSIVE tree(id) AS (
                        SELECT unnest(%s::int[])
                        UNION
                        SELECT r.child_id FROM tree t
                          JOIN ir_attachment_relation r ON r.parent_id = t.id
                         WHERE r.link_kind IN %s AND r.child_id IS NOT NULL)
                    SELECT count(*) FROM tree""", (docs, tuple(DOC_KINDS)))
                w.doc_count = cr.fetchone()[0]
            if parts:
                cr.execute("""
                    WITH RECURSIVE tree(id) AS (
                        SELECT unnest(%s::int[])
                        UNION
                        SELECT l.product_id FROM tree t
                          JOIN product_product pp ON pp.id = t.id
                          JOIN mrp_bom b ON b.active
                                        AND b.product_tmpl_id = pp.product_tmpl_id
                                        AND (b.product_id IS NULL OR b.product_id = t.id)
                          JOIN mrp_bom_line l ON l.bom_id = b.id
                         WHERE l.product_id IS NOT NULL)
                    SELECT count(*) FROM tree""", (parts,))
                w.part_count = cr.fetchone()[0]

    def action_confirm(self):
        self.ensure_one()
        Item = self.env['plm.project.item']
        base = [('project_id', '=', self.project_id.id), ('folder', '=', self.plm_folder)]
        have_docs = set(Item.search(base + [('document_id', 'in', self.document_ids.ids)]).document_id.ids)
        have_parts = set(Item.search(base + [('part_id', 'in', self.part_ids.ids)]).part_id.ids)
        vals = [
            {'project_id': self.project_id.id, 'folder': self.plm_folder,
             'document_id': d, 'include_children': self.include_children}
            for d in self.document_ids.ids if d not in have_docs
        ] + [
            {'project_id': self.project_id.id, 'folder': self.plm_folder,
             'part_id': p, 'include_children': self.include_children}
            for p in self.part_ids.ids if p not in have_parts
        ]
        if vals:
            Item.create(vals)
        self.project_id.message_post(
            body=_('Đã gán %(n)s mục gốc vào thư mục "%(f)s" (%(c)s).') % {
                'n': len(vals),
                'f': dict(self._fields['plm_folder'].selection)[self.plm_folder],
                'c': _('kèm cấu trúc con') if self.include_children else _('không kèm cấu trúc con')},
            subtype_xmlid='mail.mt_note')
        return {'type': 'ir.actions.act_window_close'}
