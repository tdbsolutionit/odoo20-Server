from odoo import api, models
from odoo.tools.translate import code_translations, xml_translate


def _localize_form(env, result, view_xmlid):
    """Fill untranslated shared Odoo labels only on the designated PLM form."""
    form = result['views'].get('form')
    if not form or env.lang != 'vi_VN':
        return
    view = env.ref(view_xmlid, raise_if_not_found=False)
    if not view or form['id'] != view.id:
        return
    terms = code_translations.get_python_translations('tdb_custom_plm', env.lang)
    form['arch'] = xml_translate(terms.get, form['arch'])
    for model in result['models'].values():
        for field in model['fields'].values():
            for key in ('string', 'help'):
                if field.get(key) in terms:
                    field[key] = terms[field[key]]
            if field.get('selection'):
                field['selection'] = [(value, terms.get(label, label)) for value, label in field['selection']]


class ProductProduct(models.Model):
    _inherit = 'product.product'

    @api.model
    def get_views(self, views, options=None):
        result = super().get_views(views, options)
        _localize_form(self.env, result, 'tdb_custom_plm.engineering_part_form')
        return result


class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    @api.model
    def get_views(self, views, options=None):
        result = super().get_views(views, options)
        _localize_form(self.env, result, 'plm.view_attachment_form_plm_hinerit')
        return result
