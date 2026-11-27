"""Activities are task inbox entries, never the source of approval authority."""
import json
import threading

from odoo import api, fields, models, _
from odoo.addons.mail.tools.discuss import Store
from odoo.exceptions import AccessError, UserError
from odoo.http import request
from odoo.fields import Domain

from .review_workflow import _TASK_ACTION, ADMIN, review_level

TASK_FIELDS = {'tdb_plm_kind', 'tdb_plm_target_model', 'tdb_plm_target_id'}
TASK_IDENTITY = TASK_FIELDS | {'res_model', 'res_model_id', 'res_id', 'user_id',
                               'activity_type_id', 'active', 'date_done', 'plm_state', 'summary', 'note'}
_CREATE_ECR_CHILD = object()


def _check_ecr_assignment(lines):
    if any(not line.user_id or not (line.name or '').strip() for line in lines):
        raise UserError(lines.env._('Enter a name and select a planned assignee for every ECR activity before starting review.'))


class WorkflowActivitySchedule(models.TransientModel):
    _inherit = 'mail.activity.schedule'

    tdb_plm_part_ids = fields.Many2many('product.product', compute='_compute_tdb_plm_parts')
    tdb_plm_part_id = fields.Many2one('product.product', string='Engineering Part')
    tdb_plm_choose_part = fields.Boolean(compute='_compute_tdb_plm_parts')

    def _tdb_native_plm_types(self):
        return (self.env.ref('plm.mail_activity_plm_activity')
                | self.env.ref('activity_validation.mail_activity_change_request'))

    def _tdb_template_parts(self):
        self.ensure_one()
        Part = self.env['product.product']
        if self.res_model != 'product.template':
            return Part
        templates = super()._get_applied_on_records()
        if len(templates) != 1:
            return Part
        return Part.search([
            ('product_tmpl_id', '=', templates.id),
            ('engineering_code', 'not in', ('', False)),
        ])

    @api.depends('res_model', 'res_ids', 'activity_type_id')
    def _compute_tdb_plm_parts(self):
        native_types = self._tdb_native_plm_types()
        for wizard in self:
            parts = wizard._tdb_template_parts() if wizard.activity_type_id in native_types else self.env['product.product']
            wizard.tdb_plm_part_ids = parts
            wizard.tdb_plm_choose_part = len(parts) > 1

    @api.depends('res_model', 'res_ids')
    def _compute_activity_type_available_ids(self):
        super()._compute_activity_type_available_ids()

    def _get_activity_type_available_base_domain(self):
        domain = super()._get_activity_type_available_base_domain()
        if self._tdb_template_parts():
            domain = Domain(domain) | Domain('id', 'in', self._tdb_native_plm_types().ids)
        managed_types = self.env['mail.activity']._tdb_managed_types()
        return Domain(domain) & Domain('id', 'not in', sorted(managed_types))

    @api.depends('res_model')
    def _compute_activity_type_id(self):
        native_types = self._tdb_native_plm_types()
        routed = self.filtered(lambda wizard: wizard.res_model == 'product.template'
                               and wizard.activity_type_id in native_types
                               and wizard._tdb_template_parts())
        super(WorkflowActivitySchedule, self - routed)._compute_activity_type_id()

    def _tdb_uses_part_target(self):
        return self.res_model == 'product.template' and self.activity_type_id in self._tdb_native_plm_types()

    def _tdb_selected_part(self):
        parts = self._tdb_template_parts()
        if len(parts) == 1:
            return parts
        return self.tdb_plm_part_id & parts

    def _get_applied_on_records(self):
        if self._tdb_uses_part_target() and (part := self._tdb_selected_part()):
            return part
        return super()._get_applied_on_records()

    def _action_schedule_activities(self):
        if self._tdb_uses_part_target():
            part = self._tdb_selected_part()
            if not part:
                raise UserError(_('Select the Engineering Part for this activity.'))
            part.check_access('read')
        activities = super()._action_schedule_activities()
        if self.change_activity_type == 'request' and self.plm_state != 'in_progress':
            # The native scheduler initially points draft child rows at the parent.
            # A child row has an assignee of its own only after Progress.
            for activity in activities:
                draft_lines = activity.children_ids.filtered(
                    lambda line: line.mail_children_activity_id == activity
                )
                if draft_lines:
                    draft_lines.write({'mail_children_activity_id': False})
        return activities

    def action_in_progress(self):
        _check_ecr_assignment(self.filtered(lambda wizard: wizard.change_activity_type == 'request').children_ids)
        return super().action_in_progress()

    def action_open_releted_ent(self):
        self.ensure_one()
        if self.change_activity_type in ('request', 'plm_activity'):
            records = self._get_applied_on_records()
            if records and records._name == 'product.product' and len(records) == 1:
                return self.env['mail.activity']._tdb_related_record_action(
                    records.exists(), title=_('Engineering Part'))
        return super().action_open_releted_ent()


class WorkflowActivity(models.Model):
    _inherit = 'mail.activity'

    def action_in_progress(self):
        requests = self.filtered(lambda activity: activity.change_activity_type == 'request')
        _check_ecr_assignment(requests.children_ids)
        return super(WorkflowActivity, self.with_context(_tdb_create_ecr_child=_CREATE_ECR_CHILD)).action_in_progress()

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
        if self.env.context.get('_tdb_create_ecr_child') is _CREATE_ECR_CHILD:
            for vals in vals_list:
                if vals.get('name'):
                    vals.setdefault('summary', vals['name'].strip())
        if self.env.context.get('_tdb_task_action') is not _TASK_ACTION:
            managed_types = self._tdb_managed_types()
            for vals in vals_list:
                if any(vals.get(key) or self.env.context.get('default_' + key) for key in TASK_FIELDS):
                    raise AccessError(_('PLM workflow tasks are created by workflow actions only.'))
                if vals.get('activity_type_id', self.env.context.get('default_activity_type_id')) in managed_types:
                    raise UserError(_('Use Submit Review or Assign Redo to create this activity.'))
        activities = super().create(vals_list)
        activities._tdb_refresh_activity_badge(activities.filtered('active').user_id)
        return activities

    def _tdb_managed_types(self):
        return {record.id for kind in ('review', 'forward', 'redo')
                if (record := self.env.ref('tdb_custom_plm.activity_type_' + kind, raise_if_not_found=False))}

    def write(self, vals):
        if self.env.context.get('_tdb_task_action') is not _TASK_ACTION:
            if TASK_FIELDS.intersection(vals) or (TASK_IDENTITY.intersection(vals) and self.filtered('tdb_plm_kind')):
                raise AccessError(_('Use the PLM workflow actions to reassign or finish this task.'))
            if vals.get('activity_type_id') in self._tdb_managed_types():
                raise UserError(_('Use the PLM workflow actions to create a review or redo assignment.'))
        if not {'user_id', 'date_deadline', 'active'}.intersection(vals):
            return super().write(vals)
        previous_users = self.filtered('active').user_id
        result = super().write(vals)
        self._tdb_refresh_activity_badge(previous_users | self.filtered('active').user_id)
        return result

    def unlink(self):
        if self.filtered('tdb_plm_kind') and self.env.context.get('_tdb_task_action') is not _TASK_ACTION:
            raise AccessError(_('Workflow task history cannot be deleted manually.'))
        users = self.filtered('active').user_id
        result = super().unlink()
        self._tdb_refresh_activity_badge(users)
        return result

    def _tdb_refresh_activity_badge(self, users):
        # Refresh the grouped count: a change can replace the most urgent
        # activity on a record, so adding or subtracting one is insufficient.
        for user in users:
            user._bus_send('tdb_plm/activity_counter_changed', {})

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
        if self.change_activity_type in ('request', 'plm_activity'):
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'mail.activity',
                'res_id': self.id,
                'views': [(self.env.ref('mail.mail_activity_view_form_popup').id, 'form')],
                'target': 'new',
            }
        if self.res_model in ('product.template', 'product.product', 'ir.attachment'):
            record = self.env[self.res_model].browse(self.res_id).exists()
            return self._tdb_related_record_action(record)
        return self.action_open_document()

    def action_open_releted_ent(self):
        self.ensure_one()
        self.check_access('read')
        if self.change_activity_type in ('request', 'plm_activity') and self.res_model == 'product.product':
            record = self.env['product.product'].browse(self.res_id).exists()
            return self._tdb_related_record_action(record, title=_('Engineering Part'))
        return super().action_open_releted_ent()

    def _tdb_related_record_action(self, record, cad_rpc=False, title=None):
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
                                else 'tdb_custom_plm.engineering_part_form')
        else:
            view = self.env.ref('plm.view_attachment_form_plm_hinerit')
        return {'type': 'ir.actions.act_window', 'name': title or _('PLM Review / Redo'),
                'res_model': record._name, 'res_id': record.id, 'view_mode': 'form',
                'views': [(view.id, 'form')], 'target': 'current', 'context': {'odooPLM': True}}


class CadWorkflowActivities(models.Model):
    _inherit = 'product.product'

    def tdb_get_activity_completion_messages(self):
        self.ensure_one()
        self.check_access('read')
        messages = self.env['mail.message'].search([
            ('model', '=', self._name),
            ('res_id', '=', self.id),
            ('subtype_id', '=', self.env.ref('mail.mt_activities').id),
            ('mail_activity_type_id', '!=', False),
        ], order='id desc', limit=20)
        store = Store()
        store.add(messages, '_store_message_fields', fields_params={'chatter_fields': True})
        return {'ids': messages.ids, 'store_data': store.as_dict()}

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
