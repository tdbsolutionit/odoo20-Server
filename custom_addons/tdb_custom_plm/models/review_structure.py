"""Prevent editing an approved structure through its child records."""
from odoo import api, models


class ReviewBom(models.Model):
    _inherit = 'mrp.bom'

    @api.model_create_multi
    def create(self, vals_list):
        templates = self.env['product.template'].browse([
            vals.get('product_tmpl_id') or self.env.context.get('default_product_tmpl_id')
            for vals in vals_list
            if vals.get('product_tmpl_id') or self.env.context.get('default_product_tmpl_id')
        ])
        templates._tdb_assert_content_editable()
        records = super().create(vals_list)
        records.product_tmpl_id._tdb_assert_content_editable()
        records.product_tmpl_id._tdb_notify_live()
        return records

    def write(self, vals):
        templates = self.product_tmpl_id
        if vals.get('product_tmpl_id'):
            templates |= self.env['product.template'].browse(vals['product_tmpl_id'])
        templates._tdb_assert_content_editable()
        result = super().write(vals)
        templates._tdb_notify_live()
        return result

    def unlink(self):
        self.product_tmpl_id._tdb_assert_content_editable()
        self.product_tmpl_id._tdb_notify_live()
        return super().unlink()


class ReviewBomLine(models.Model):
    _inherit = 'mrp.bom.line'

    @api.model_create_multi
    def create(self, vals_list):
        boms = self.env['mrp.bom'].browse([
            vals.get('bom_id') or self.env.context.get('default_bom_id')
            for vals in vals_list if vals.get('bom_id') or self.env.context.get('default_bom_id')
        ])
        boms.product_tmpl_id._tdb_assert_content_editable()
        records = super().create(vals_list)
        records.bom_id.product_tmpl_id._tdb_assert_content_editable()
        records.bom_id.product_tmpl_id._tdb_notify_live()
        return records

    def write(self, vals):
        boms = self.bom_id
        if vals.get('bom_id'):
            boms |= self.env['mrp.bom'].browse(vals['bom_id'])
        boms.product_tmpl_id._tdb_assert_content_editable()
        result = super().write(vals)
        boms.product_tmpl_id._tdb_notify_live()
        return result

    def unlink(self):
        self.bom_id.product_tmpl_id._tdb_assert_content_editable()
        self.bom_id.product_tmpl_id._tdb_notify_live()
        return super().unlink()


class ReviewDocumentRelation(models.Model):
    _inherit = 'ir.attachment.relation'

    @api.model_create_multi
    def create(self, vals_list):
        parents = self.env['ir.attachment'].browse([
            vals.get('parent_id') or self.env.context.get('default_parent_id')
            for vals in vals_list if vals.get('parent_id') or self.env.context.get('default_parent_id')
        ])
        parents._tdb_assert_content_editable()
        records = super().create(vals_list)
        records.parent_id._tdb_assert_content_editable()
        records.parent_id._tdb_notify_live()
        return records

    def write(self, vals):
        parents = self.parent_id
        if vals.get('parent_id'):
            parents |= self.env['ir.attachment'].browse(vals['parent_id'])
        parents._tdb_assert_content_editable()
        result = super().write(vals)
        parents._tdb_notify_live()
        return result

    def unlink(self):
        self.parent_id._tdb_assert_content_editable()
        self.parent_id._tdb_notify_live()
        return super().unlink()
