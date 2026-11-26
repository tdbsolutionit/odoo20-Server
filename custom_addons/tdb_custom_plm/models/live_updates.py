from odoo import models


EVENT = 'tdb_plm/changed'
PENDING = 'tdb_plm.live.models'
VIEW_MODELS = {'ir.attachment', 'product.product', 'product.template', 'mrp.bom', 'mail.activity'}


class PlmLiveUpdates(models.AbstractModel):
    _name = 'tdb.plm.live'
    _description = 'PLM live view invalidation'

    def _notify(self):
        # Batch related writes until the next precommit flush. Odoo's bus
        # publishes the resulting signals only after the transaction commits.
        if PENDING not in self.env.cr.precommit.data:
            self.env.cr.precommit.data[PENDING] = set()
            self.env.cr.precommit.add(self._flush_notifications)
        self.env.cr.precommit.data[PENDING].update(VIEW_MODELS)

    def _flush_notifications(self):
        models_changed = self.env.cr.precommit.data.pop(PENDING, set())
        if not models_changed:
            return
        group = self.env.ref('plm.group_plm_view_user', raise_if_not_found=False)
        if not group:
            return
        users = self.env['res.users'].sudo().search([
            ('active', '=', True), ('share', '=', False),
            ('all_group_ids', 'in', group.ids),
        ])
        # Only model names are sent: no record IDs, names, counts or business
        # values. Reloads use the recipient's normal ORM access rights.
        payload = {'models': sorted(models_changed)}
        for user in users:
            user._bus_send(EVENT, payload)


class RevisionLiveUpdates(models.AbstractModel):
    _inherit = 'revision.plm.mixin'

    def _tdb_notify_live(self):
        relevant = self.filtered('is_plm') if self._name == 'ir.attachment' else self.filtered('engineering_code')
        if relevant:
            self.env['tdb.plm.live']._notify()
