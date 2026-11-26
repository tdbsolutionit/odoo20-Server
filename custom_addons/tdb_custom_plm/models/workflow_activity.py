"""Activities are task inbox entries, never the source of approval authority."""
import json
import threading

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError
from odoo.http import request
from odoo.fields import Domain

from .review_workflow import _TASK_ACTION, ADMIN, review_level

TASK_FIELDS = {'tdb_plm_kind', 'tdb_plm_target_model', 'tdb_plm_target_id'}
TASK_IDENTITY = TASK_FIELDS | {'res_model', 'res_model_id', 'res_id', 'user_id',
                               'activity_type_id', 'active', 'date_done', 'plm_state', 'summary', 'note'}


class WorkflowActivitySchedule(models.TransientModel):
    _inherit = 'mail.activity.schedule'

    def _get_activity_type_available_base_domain(self):
        domain = super()._get_activity_type_available_base_domain()
        managed_types = self.env['mail.activity']._tdb_managed_types()
        return Domain(domain) & Domain('id', 'not in', sorted(managed_types))


class WorkflowActivity(models.Model):
    _inherit = 'mail.activity'

    tdb_plm_kind = fields.Selection([
        ('review', 'PLM Review'), ('forward', 'Submit PLM Review to Higher Level'),
        ('redo', 'PLM Redo'),
    ], readonly=True, copy=False)
    tdb_plm_target_model = fields.Selection([
        ('ir.attachment', 'Document'), ('product.template', 'Part'),
    ], readonly=True, copy=False)
    tdb_plm_target_id = fields.Integer(readonly=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get('_tdb_task_action') is not _TASK_ACTION:
            managed_types = self._tdb_managed_types()
            for vals in vals_list:
                if any(vals.get(key) or self.env.context.get('default_' + key) for key in TASK_FIELDS):
                    raise AccessError(_('PLM workflow tasks are created by workflow actions only.'))
                if vals.get('activity_type_id', self.env.context.get('default_activity_type_id')) in managed_types:
                    raise UserError(_('Use Submit Review or Assign Redo to create this activity.'))
        return super().create(vals_list)

    def _tdb_managed_types(self):
        return {record.id for kind in ('review', 'forward', 'redo')
                if (record := self.env.ref('tdb_custom_plm.activity_type_' + kind, raise_if_not_found=False))}

    def write(self, vals):
        if self.env.context.get('_tdb_task_action') is not _TASK_ACTION:
            if TASK_FIELDS.intersection(vals) or (TASK_IDENTITY.intersection(vals) and self.filtered('tdb_plm_kind')):
                raise AccessError(_('Use the PLM workflow actions to reassign or finish this task.'))
            if vals.get('activity_type_id') in self._tdb_managed_types():
                raise UserError(_('Use the PLM workflow actions to create a review or redo assignment.'))
        return super().write(vals)

    def unlink(self):
        if self.filtered('tdb_plm_kind') and self.env.context.get('_tdb_task_action') is not _TASK_ACTION:
            raise AccessError(_('Workflow task history cannot be deleted manually.'))
        return super().unlink()

    def _action_done(self, feedback=False, attachment_ids=None):
        if self.filtered('tdb_plm_kind') and self.env.context.get('_tdb_task_action') is not _TASK_ACTION:
            raise UserError(_('Open the PLM workflow to approve, reject, submit onward or return redo work.'))
        return super()._action_done(feedback=feedback, attachment_ids=attachment_ids)

    def action_tdb_open_workflow(self):
        self.ensure_one()
        self.check_access('read')
        if not self.tdb_plm_kind or self.tdb_plm_target_model not in ('ir.attachment', 'product.template'):
            raise UserError(_('This is not a managed PLM workflow task.'))
        record = self.env[self.tdb_plm_target_model].browse(self.tdb_plm_target_id).exists()
        cad_rpc = (not request and getattr(threading.current_thread(), 'rpc_model_method', None)
                   == 'mail.activity.action_tdb_open_workflow')
        return self._tdb_related_record_action(record, cad_rpc)

    def action_tdb_open_activity_record(self):
        """PLM list row navigation; native activity behavior elsewhere is unchanged."""
        self.ensure_one()
        self.check_access('read')
        if self.tdb_plm_kind:
            return self.action_tdb_open_workflow()
        if self.res_model in ('product.template', 'product.product', 'ir.attachment'):
            record = self.env[self.res_model].browse(self.res_id).exists()
            return self._tdb_related_record_action(record)
        return self.action_open_document()

    def _tdb_related_record_action(self, record, cad_rpc=False):
        record.check_access('read')
        if not record:
            raise UserError(_('The related record no longer exists.'))
        if record._name == 'product.template':
            record = record.product_variant_id
            if not record:
                raise UserError(_('The related Part variant no longer exists.'))
            record.check_access('read')
        if record._name == 'product.product':
            view = self.env.ref('plm.plm_component_base_form' if cad_rpc
                                else 'product.product_normal_form_view')
        else:
            view = self.env.ref('plm.view_attachment_form_plm_hinerit')
        return {'type': 'ir.actions.act_window', 'name': _('PLM Review / Redo'),
                'res_model': record._name, 'res_id': record.id, 'view_mode': 'form',
                'views': [(view.id, 'form')], 'target': 'current', 'context': {'odooPLM': True}}


class CadWorkflowActivities(models.Model):
    _inherit = 'product.product'

    @api.model
    def get_user_activities(self, ids_to_read=None):
        headers, rows = json.loads(super().get_user_activities(ids_to_read or []))
        key = 'action_tdb_open_workflow'
        headers.append({'name': 'Review / Redo', 'key': key, 'readonly': False,
                        'type': 'BUTTON', 'callback': 'activitiesCallback'})
        for row in rows:
            row[key] = True
        domain = [('tdb_plm_kind', '!=', False), ('user_id', '=', self.env.uid), ('date_done', '=', False)]
        if ids_to_read:
            domain.append(('id', 'in', ids_to_read))
        for activity in self.env['mail.activity'].search(domain):
            row = {header['key']: '' for header in headers}
            row.update({'id': activity.id, 'res_name': activity.res_name,
                        'summary': activity.summary, 'note': activity.note,
                        'date_deadline': str(activity.date_deadline), 'user_id': activity.user_id.display_name,
                        'plm_state': activity.plm_state or 'draft', key: False})
            for header in headers:
                if header.get('type') == 'BUTTON' and header['key'] != key:
                    row[header['key']] = True
            rows.append(row)
        return json.dumps([headers, rows], default=str)


class WorkflowTaskWizard(models.TransientModel):
    _inherit = 'tdb.plm.task.wizard'

    allowed_user_ids = fields.Many2many('res.users', compute='_compute_allowed_users')
    requester_id = fields.Many2one(domain=lambda self: [
        ('active', '=', True), ('share', '=', False),
        ('all_group_ids', 'in', self.env.ref('plm.group_plm_integration_user').ids),
        ('all_group_ids', 'not in', self.env.ref(ADMIN).ids),
    ])

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        if values.get('operation') != 'recover':
            return values
        if 'reason' in fields_list:
            values['reason'] = _('Recover review with no valid assigned reviewer or task.')
        model = values.get('target_model')
        if model in ('ir.attachment', 'product.template') and 'requester_id' in fields_list:
            records = self.env[model].browse(values.get('target_ids') or [values.get('target_id')]).exists()
            records.check_access('read')
            owners = [record.tdb_requester_id or record.tdb_submitted_by for record in records]
            if owners and all(owner and review_level(owner) in (1, 2) for owner in owners):
                owner_ids = {owner.id for owner in owners}
                if len(owner_ids) == 1:
                    values['requester_id'] = owner_ids.pop()
        return values

    @api.depends('operation', 'requester_id')
    def _compute_allowed_users(self):
        users = self.env['res.users'].search([('active', '=', True), ('share', '=', False)])
        level = review_level(self.env.user)
        for wizard in self:
            if wizard.operation == 'recover':
                owner_level = review_level(wizard.requester_id) if wizard.requester_id else 3
                wizard.allowed_user_ids = users.filtered(lambda user: review_level(user) > owner_level)
            elif wizard.operation in ('forward', 'approve_forward'):
                wizard.allowed_user_ids = users.filtered(lambda user: review_level(user) == 3)
            elif wizard.operation == 'submit':
                wizard.allowed_user_ids = users.filtered(lambda user: review_level(user) > level)
            else:
                wizard.allowed_user_ids = users.filtered(lambda user:
                    0 < review_level(user) < level or (wizard.operation == 'reject' and user == self.env.user))
