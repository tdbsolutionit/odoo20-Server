"""Level-based review and delegated redo, enforced at the ORM boundary."""
from copy import deepcopy
from uuid import uuid4
from markupsafe import escape
from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError

MANAGER = 'tdb_custom_plm.group_review_manager'
ADMIN = 'tdb_custom_plm.group_review_admin'
_REVISION_CHAIN = object()
_DISCARD_REVISION = object()
_TASK_ACTION = object()
PROTECTED = {
    'tdb_review_stage', 'tdb_review_history', 'tdb_submitted_by', 'tdb_submitted_at',
    'tdb_manager_approved_by', 'tdb_manager_approved_at', 'tdb_admin_approved_by',
    'tdb_admin_approved_at', 'tdb_requester_id', 'tdb_request_level',
    'tdb_forwarder_id', 'tdb_task_activity_id', 'tdb_redo_chain', 'tdb_redo_user_id',
}
CONFIG = {'tdb_reviewer_id', 'tdb_review_deadline', 'tdb_manager_id', 'tdb_admin_id'}
CONTENT = {
    'datas', 'raw', 'db_datas', 'store_fname', 'checksum', 'url', 'type',
    'name', 'description', 'res_model', 'res_id', 'company_id', 'active',
    'linkeddocuments', 'bom_ids', 'bom_line_ids', 'default_code',
    'std_description', 'std_description_name', 'weight', 'volume',
    'mimetype', 'cad_name', 'cad_version', 'document_type', 'product_tmpl_id',
    'attribute_line_ids', 'product_template_attribute_value_ids',
    'product_template_variant_value_ids',
}
WORKFLOW_METADATA = {
    'engineering_state', 'engineering_workflow_date', 'engineering_workflow_user',
    'engineering_release_date', 'engineering_release_user',
    'engineering_writable', 'engineering_code_editable',
}



def review_level(user):
    if not user.active or user.share:
        return 0
    if user.has_group(ADMIN):
        return 3
    if user.has_group(MANAGER):
        return 2
    return 1 if user.has_group('plm.group_plm_integration_user') else 0


def reviewer_domain(env):
    level = review_level(env.user)
    groups = []
    if level == 1:
        groups.append(env.ref(MANAGER).id)
    if level in (1, 2):
        groups.append(env.ref(ADMIN).id)
    return [('active', '=', True), ('share', '=', False), ('all_group_ids', 'in', groups)]


class ReviewWorkflow(models.AbstractModel):
    _inherit = 'revision.plm.mixin'

    engineering_state = fields.Selection(selection_add=[('confirmed', 'Review'), ('obsoleted', 'Obsolete')])
    engineering_release_date = fields.Datetime(tracking=False)
    engineering_workflow_date = fields.Datetime(tracking=False)
    tdb_reviewer_id = fields.Many2one('res.users', string='Reviewer', copy=True,
                                    domain=lambda self: reviewer_domain(self.env))
    tdb_review_deadline = fields.Date(string='Review Deadline', default=fields.Date.context_today, copy=False)
    # Retain old columns and audit evidence. These are no longer workflow inputs.
    tdb_manager_id = fields.Many2one('res.users', string='Legacy Review Manager', readonly=True, copy=False)
    tdb_admin_id = fields.Many2one('res.users', string='Legacy Release Admin', readonly=True, copy=False)
    tdb_review_stage = fields.Selection([
        ('review', 'Waiting for Reviewer'), ('forward', 'Submit to Higher Level'),
        ('redo', 'Redo in Progress'), ('released', 'Released'), ('rejected', 'Rejected'),
        ('manager', 'Legacy Manager Review'), ('admin', 'Legacy Admin Review'),
    ], readonly=True, copy=False, string='Review Progress')
    tdb_review_history = fields.Json(readonly=True, copy=False)
    tdb_submitted_by = fields.Many2one('res.users', readonly=True, copy=False)
    tdb_submitted_at = fields.Datetime(readonly=True, copy=False)
    tdb_manager_approved_by = fields.Many2one('res.users', readonly=True, copy=False)
    tdb_manager_approved_at = fields.Datetime(readonly=True, copy=False)
    tdb_admin_approved_by = fields.Many2one('res.users', readonly=True, copy=False)
    tdb_admin_approved_at = fields.Datetime(readonly=True, copy=False)
    tdb_requester_id = fields.Many2one('res.users', readonly=True, copy=False, string='Current Requester')
    tdb_request_level = fields.Integer(readonly=True, copy=False)
    tdb_forwarder_id = fields.Many2one('res.users', readonly=True, copy=False)
    tdb_task_activity_id = fields.Many2one('mail.activity', readonly=True, copy=False, ondelete='set null')
    tdb_redo_chain = fields.Json(readonly=True, copy=False)
    tdb_redo_user_id = fields.Many2one('res.users', readonly=True, copy=False, string='Current Redo Assignee')
    tdb_actor_level = fields.Integer(compute='_compute_actor_controls', compute_sudo=False)
    tdb_actor_owns_task = fields.Boolean(compute='_compute_actor_controls', compute_sudo=False)
    tdb_needs_recovery = fields.Boolean(
        string='Review Needs Recovery', compute='_compute_recovery_needed',
        search='_search_recovery_needed', compute_sudo=False,
    )

    def _tdb_review_needs_recovery(self):
        self.ensure_one()
        if self.engineering_state != 'confirmed' or self.tdb_redo_chain:
            return False
        if self.tdb_review_stage in (False, 'manager', 'admin'):
            return True
        if self.tdb_review_stage != 'review':
            return False
        reviewer, activity = self.tdb_reviewer_id, self.tdb_task_activity_id
        return (not reviewer or not reviewer.active or review_level(reviewer) <= self.tdb_request_level
                or self.tdb_request_level not in (1, 2) or not self.tdb_requester_id
                or not activity or bool(activity.date_done) or not activity.active
                or activity.user_id != reviewer or activity.tdb_plm_kind != 'review'
                or activity.tdb_plm_target_model != self._name or activity.tdb_plm_target_id != self.id
                or activity.res_model != self._name or activity.res_id != self.id)

    @api.depends('engineering_state', 'tdb_review_stage', 'tdb_reviewer_id',
                 'tdb_reviewer_id.active', 'tdb_task_activity_id',
                 'tdb_task_activity_id.date_done', 'tdb_request_level', 'tdb_requester_id')
    def _compute_recovery_needed(self):
        for record in self:
            record.tdb_needs_recovery = record._tdb_review_needs_recovery()

    def _search_recovery_needed(self, operator, value):
        if operator not in ('=', '!='):
            return NotImplemented
        records = self.search([('engineering_state', '=', 'confirmed')])
        ids = records.filtered(lambda record: record._tdb_review_needs_recovery()).ids
        return [('id', 'in' if bool(value) == (operator == '=') else 'not in', ids)]

    @api.depends_context('uid')
    @api.depends('tdb_review_stage', 'tdb_reviewer_id', 'tdb_forwarder_id', 'tdb_redo_user_id')
    def _compute_actor_controls(self):
        level = review_level(self.env.user)
        for record in self:
            owner = (record.tdb_reviewer_id if record.tdb_review_stage == 'review' else
                     record.tdb_forwarder_id if record.tdb_review_stage == 'forward' else
                     record.tdb_redo_user_id if record.tdb_review_stage == 'redo' else self.env['res.users'])
            legacy = record._tdb_review_needs_recovery()
            record.tdb_actor_level = level
            record.tdb_actor_owns_task = (level == 3) if legacy else owner == self.env.user

    def _tdb_lock(self):
        self.check_access('write')
        if self.ids:
            self.flush_recordset()
            self.env.cr.execute('SELECT id FROM "%s" WHERE id IN %%s ORDER BY id FOR UPDATE' % self._table,
                                [tuple(sorted(self.ids))])
            self.invalidate_recordset()

    def _tdb_internal_write(self, vals):
        result = super(ReviewWorkflow, self).write(vals)
        self._tdb_notify_live()
        return result

    def _tdb_check_worker(self):
        self.ensure_one()
        if self.tdb_redo_chain and self.tdb_redo_user_id != self.env.user:
            raise AccessError(_('Only the active redo assignee can edit or complete this work.'))

    def _tdb_restore_after_discard(self):
        if self.env.context.get('_tdb_discard_revision') is not _DISCARD_REVISION:
            raise AccessError(_('The previous revision can only be restored while discarding a draft.'))
        self.check_access('unlink')
        for draft in self:
            if (not draft.engineering_code or not draft.engineering_revision
                    or draft.engineering_branch_parent_id):
                continue
            draft._tdb_lock()
            if draft.engineering_state != 'draft' or draft.tdb_review_history:
                raise UserError(_('Only a draft without review history can be discarded.'))
            chain = [('engineering_code', '=', draft.engineering_code),
                     ('engineering_branch_parent_id', '=', False)]
            if draft.sudo().search_count(chain + [('engineering_revision', '>', draft.engineering_revision)]):
                continue
            previous = draft.search(chain + [
                ('engineering_revision', '=', draft.engineering_revision - 1),
                ('engineering_state', '=', 'undermodify'),
            ], limit=1)
            if previous:
                previous._tdb_lock()
                if previous.engineering_state == 'undermodify':
                    previous._tdb_internal_write({'engineering_state': 'released'})
                    previous.message_post(body=_('Restored after discarding the draft revision.'))
        return True

    def _tdb_refuse_checked_out(self):
        if self._name == 'ir.attachment':
            self.refuseIfCheckedOut()
        elif self._name == 'product.template':
            self.linkeddocuments.refuseIfCheckedOut()

    def _tdb_assert_content_editable(self):
        self._tdb_lock()
        for record in self:
            if record.engineering_code and record.engineering_state != 'draft':
                raise UserError(_(
                    'Cannot edit %(code)s revision %(revision)s (%(state)s). Open the Draft revision to edit it.',
                    code=record.engineering_code,
                    revision=record.engineering_revision,
                    state=record.engineering_state,
                ))
            record._tdb_check_worker()

    def _fix_previous_revisions_state(self):
        for record in self.filtered('engineering_code'):
            if record.search_count([('engineering_code', '=', record.engineering_code),
                                    ('engineering_revision', '<', record.engineering_revision),
                                    ('engineering_state', '=', 'confirmed')]):
                raise UserError(_('Finish or reject the previous review before creating a new revision.'))
        return super(ReviewWorkflow, self.with_context(_tdb_revision_chain=_REVISION_CHAIN))._fix_previous_revisions_state()

    def _tdb_check_user_access(self, user):
        self.ensure_one()
        if not user or not review_level(user):
            raise UserError(_('Select an active internal PLM user.'))
        if self.company_id and self.company_id not in user.company_ids:
            raise UserError(_('The assignee must have access to the record company.'))
        self.with_user(user).with_context(allowed_company_ids=user.company_ids.ids).check_access('write')

    def _tdb_validate_reviewer(self, reviewer=None):
        self.ensure_one()
        reviewer = reviewer or self.tdb_reviewer_id
        self._tdb_check_user_access(reviewer)
        if review_level(reviewer) <= review_level(self.env.user):
            raise UserError(_('Select one reviewer at a higher level than the submitter.'))
        if not self.engineering_code:
            raise UserError(_('Set the engineering code before submitting.'))
        return reviewer

    def _tdb_prepare_reviewers(self):
        self.ensure_one()
        if not self.tdb_reviewer_id:
            # Reuse an eligible old assignment as a migration default only.
            for user in (self.tdb_manager_id, self.tdb_admin_id):
                if user and review_level(user) > review_level(self.env.user):
                    self.write({'tdb_reviewer_id': user.id})
                    break

    def _tdb_history_event(self, decision, reason=None, extra=None, new_round=False):
        self.ensure_one()
        history = deepcopy(self.tdb_review_history or [])
        if new_round or not history:
            history.append({'round': len(history) + 1, 'revision': self.engineering_revision,
                            'reviewer_id': self.tdb_reviewer_id.id, 'events': []})
        event = {'decision': decision, 'user_id': self.env.uid,
                 'level': review_level(self.env.user), 'date': fields.Datetime.to_string(fields.Datetime.now()),
                 'reason': reason or ''}
        event.update(extra or {})
        history[-1]['events'].append(event)
        self._tdb_internal_write({'tdb_review_history': history})
        body = _('%(decision)s - round %(round)s - %(user)s',
                 decision=decision.replace('_', ' ').capitalize(), round=len(history), user=self.env.user.display_name)
        if decision == 'admin_direct_confirm':
            body = _('Released this revision directly from Draft.')
        if reason:
            body += '\n' + _('Reason: %s', reason)
        self.message_post(body=body, subtype_xmlid='mail.mt_note')

    def _tdb_close_activity(self, feedback):
        activity = self.tdb_task_activity_id
        if activity and not activity.date_done:
            activity = activity.with_context(_tdb_task_action=_TASK_ACTION)
            activity.write({'plm_state': 'done'})
            activity._action_done(feedback=feedback)
        self._tdb_internal_write({'tdb_task_activity_id': False})

    def _tdb_notify_task_user(self, user, subject, text):
        """Durable inbox + live alert; independent of SMTP and global preferences."""
        self.ensure_one()
        self._tdb_check_user_access(user)
        message = self._message_log(
            subject=subject, body=escape(text), partner_ids=user.partner_id.ids,
        )
        self._notify_thread_by_inbox(message, [{
            'id': user.partner_id.id, 'uid': user.id, 'notif': 'inbox',
        }])
        user._bus_send('simple_notification', {
            'title': subject, 'message': text, 'type': 'info', 'sticky': True,
        })
        return message

    def _tdb_open_activity(self, kind, user, reason=None, deadline=None):
        self.ensure_one()
        self._tdb_check_user_access(user)
        activity_type = self.env.ref('tdb_custom_plm.activity_type_' + kind)
        activity = self.env['mail.activity'].with_context(
            _tdb_task_action=_TASK_ACTION, mail_activity_quick_update=True,
        ).create({
            'res_model_id': self.env['ir.model']._get(self._name).id, 'res_id': self.id,
            'activity_type_id': activity_type.id, 'user_id': user.id,
            'date_deadline': deadline or self.tdb_review_deadline or fields.Date.context_today(self),
            'summary': activity_type.name, 'note': reason or '',
            'tdb_plm_kind': kind, 'tdb_plm_target_model': self._name, 'tdb_plm_target_id': self.id,
        })
        self._tdb_internal_write({'tdb_task_activity_id': activity.id})
        if user != self.env.user:
            self._tdb_notify_task_user(user, _('%(task)s: %(record)s',
                task=activity_type.name, record=self.display_name),
                _('%(sender)s assigned you a task on %(record)s: %(task)s.',
                  sender=self.env.user.display_name, record=self.display_name, task=activity_type.name))
            # Opt-in email complements the guaranteed inbox alert. With no
            # configured SMTP the workflow remains usable without email failures.
            if (self.env['ir.config_parameter'].sudo().get_bool('tdb_plm.email_notifications', False)
                    and user.notification_type == 'email'):
                activity.action_notify()
        return activity

    def _tdb_start_review(self, reviewer, new_round=True):
        self._tdb_refuse_checked_out()
        reviewer = self._tdb_validate_reviewer(reviewer)
        self._tdb_internal_write({
            'engineering_state': 'confirmed', 'engineering_writable': False,
            'tdb_reviewer_id': reviewer.id, 'tdb_review_stage': 'review',
            'tdb_requester_id': self.env.uid, 'tdb_request_level': review_level(self.env.user),
            'tdb_forwarder_id': False, 'tdb_redo_user_id': False,
            'tdb_submitted_by': self.env.uid, 'tdb_submitted_at': fields.Datetime.now(),
            'tdb_manager_approved_by': False, 'tdb_manager_approved_at': False,
            'tdb_admin_approved_by': False, 'tdb_admin_approved_at': False,
        })
        activity = self._tdb_open_activity('review', reviewer)
        self._tdb_history_event('submitted_for_review', extra={
            'reviewer_id': reviewer.id, 'reviewer_level': review_level(reviewer), 'activity_id': activity.id,
        }, new_round=new_round)

    def _tdb_check_review_actor(self):
        self.ensure_one()
        if self._tdb_review_needs_recovery():
            raise UserError(_('Level 3 must recover this unassigned or invalid review first.'))
        if self.engineering_state != 'confirmed' or self.tdb_review_stage != 'review':
            raise UserError(_('This record is not waiting for a reviewer decision.'))
        if self.tdb_reviewer_id != self.env.user:
            raise AccessError(_('Only the assigned reviewer may decide this review.'))
        if review_level(self.env.user) <= self.tdb_request_level:
            raise AccessError(_('The reviewer must still have a higher level than the requester.'))

    def _tdb_check_release(self):
        self.ensure_one()
        if not self.engineering_code:
            raise UserError(_('Set the engineering code before confirming.'))
        if review_level(self.env.user) != 3:
            raise AccessError(_('Only Level 3 can confirm and release.'))
        if self.tdb_redo_chain:
            raise UserError(_('Complete or return the active redo task before release.'))
        if self.engineering_state == 'confirmed':
            self._tdb_check_review_actor()
        elif self.engineering_state != 'draft':
            raise UserError(_('Only Draft or reviewed records can be released.'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if any(vals.get(key) or self.env.context.get('default_' + key) for key in PROTECTED):
                raise AccessError(_('Workflow data can only be set by workflow actions.'))
            vals.update(dict.fromkeys(PROTECTED, False))
            if vals.get('engineering_state', self.env.context.get('default_engineering_state', 'draft')) not in (False, 'draft'):
                raise UserError(_('New records must start in Draft.'))
            vals['engineering_state'] = 'draft'
        records = super().create(vals_list)
        records._tdb_notify_live()
        return records

    def write(self, vals):
        if PROTECTED.intersection(vals):
            raise AccessError(_('Workflow data can only be changed by workflow actions.'))
        self._tdb_lock()
        state = vals.get('engineering_state')
        if 'engineering_state' in vals and state not in ('draft', 'confirmed', 'released', 'obsoleted', 'undermodify'):
            raise UserError(_('Unsupported workflow transition.'))
        transitions = []
        for record in self:
            content = CONTENT.intersection(vals) or any(
                key.startswith(('engineering_', 'std_', 'tmp_')) and key not in WORKFLOW_METADATA for key in vals)
            forward_edit = (record.tdb_review_stage == 'forward' and record.tdb_forwarder_id == self.env.user
                            and review_level(self.env.user) == 2)
            if content:
                record._tdb_assert_content_editable()
            if CONFIG.intersection(vals):
                changed_config = False
                for name in CONFIG.intersection(vals):
                    field = record._fields[name]
                    current = record[name].id if field.type == 'many2one' else record[name]
                    incoming = fields.Date.to_date(vals[name]) if field.type == 'date' else vals[name]
                    changed_config |= (current or False) != (incoming or False)
                if changed_config and (record.tdb_redo_chain or (record.engineering_state != 'draft' and not forward_edit)):
                    raise UserError(_('Reviewer assignments are locked during an active review or redo.'))
            if not state or state == record.engineering_state:
                continue
            record._tdb_refuse_checked_out()
            if state == 'confirmed':
                record._tdb_check_shared_reviews(lock=True)
                if record.engineering_state != 'draft' or record.tdb_redo_chain:
                    raise UserError(_('Complete redo tasks before submitting for review.'))
                if not review_level(self.env.user):
                    raise AccessError(_('Only a PLM user can submit.'))
                if review_level(self.env.user) == 3:
                    raise UserError(_('Level 3 confirms directly to Released; use Confirm.'))
                if 'tdb_reviewer_id' in vals:
                    record._tdb_internal_write({'tdb_reviewer_id': vals['tdb_reviewer_id']})
                record._tdb_prepare_reviewers()
                record._tdb_validate_reviewer()
            elif state == 'released':
                record._tdb_check_release()
            elif state == 'draft':
                raise UserError(_('Use Reject and assign a redo task with a reason.'))
            elif state == 'obsoleted':
                if record.engineering_state not in ('released', 'undermodify'):
                    raise UserError(_('Only a released revision can become Obsolete.'))
                if review_level(self.env.user) != 3 and self.env.context.get('_tdb_revision_chain') is not _REVISION_CHAIN:
                    raise AccessError(_('Only Level 3 can obsolete a record.'))
            elif state == 'undermodify' and record.engineering_state != 'released':
                raise UserError(_('Under Modify is reserved for the revision chain.'))
            transitions.append((record, record.engineering_state))
        result = super().write(vals)
        for record, previous in transitions:
            if state == 'confirmed':
                record._tdb_start_review(record.tdb_reviewer_id)
            elif state == 'released':
                record._tdb_close_activity(_('Approved and released'))
                record._tdb_internal_write({
                    'tdb_review_stage': 'released', 'tdb_reviewer_id': self.env.uid,
                    'tdb_admin_approved_by': self.env.uid, 'tdb_admin_approved_at': fields.Datetime.now(),
                    'engineering_release_user': self.env.uid, 'engineering_release_date': fields.Datetime.now(),
                    'engineering_workflow_user': self.env.uid, 'engineering_workflow_date': fields.Datetime.now(),
                    'engineering_writable': False,
                })
                record._tdb_history_event('admin_direct_confirm' if previous == 'draft' else 'approved_and_released',
                                          new_round=previous == 'draft')
        self._tdb_notify_live()
        return result

    def move_to_state(self, state):
        # Omnia has no Draft -> Released transition method. Admin Confirm is
        # explicitly allowed by policy, while normal write guards remain active.
        for record in self:
            if state == 'released' and record.engineering_state == 'draft':
                record.write({'engineering_state': 'released'})
                record._mark_obsolete_previous()
            else:
                super(ReviewWorkflow, record).move_to_state(state)

    def unlink(self):
        self._tdb_lock()
        if self.filtered(lambda r: r.tdb_review_history or (r.engineering_code and r.engineering_state != 'draft')):
            raise UserError(_('Records with workflow history cannot be deleted.'))
        self._tdb_notify_live()
        return super().unlink()

    def action_tdb_approve(self):
        self._tdb_lock()
        for record in self:
            record._tdb_check_review_actor()
            if review_level(self.env.user) == 3:
                record.move_to_state('released')
            else:
                record._tdb_close_activity(_('Level 2 approved; submit to Level 3'))
                record._tdb_internal_write({
                    'tdb_review_stage': 'forward', 'tdb_forwarder_id': self.env.uid,
                    'tdb_reviewer_id': False, 'tdb_manager_approved_by': self.env.uid,
                    'tdb_manager_approved_at': fields.Datetime.now(),
                })
                activity = record._tdb_open_activity('forward', self.env.user)
                record._tdb_history_event('level_2_approved', extra={'activity_id': activity.id})
        return True

    def action_tdb_manager_approve(self):
        return self.action_tdb_approve()

    def action_tdb_approve_submit(self):
        self.ensure_one()
        self._tdb_check_review_actor()
        if review_level(self.env.user) != 2:
            raise AccessError(_('Only the assigned Level 2 reviewer can approve and submit to Level 3.'))
        action = self._tdb_wizard('approve_forward')
        action['name'] = _('Approve and Submit to Level 3')
        return action

    def _tdb_approve_and_submit(self, reviewer, deadline=None):
        self._tdb_lock()
        self.ensure_one()
        self._tdb_check_review_actor()
        if review_level(self.env.user) != 2 or review_level(reviewer) != 3:
            raise UserError(_('Level 2 must select a Level 3 reviewer before approving.'))
        self._tdb_validate_reviewer(reviewer)
        self.action_tdb_approve()
        self._tdb_forward(reviewer, deadline)
        return True

    def _tdb_forward(self, reviewer, deadline=None):
        self._tdb_lock()
        self.ensure_one()
        if (self.tdb_review_stage != 'forward' or self.tdb_forwarder_id != self.env.user
                or review_level(self.env.user) != 2):
            raise AccessError(_('Only the responsible Level 2 user can submit this onward review.'))
        reviewer = self._tdb_validate_reviewer(reviewer)
        if review_level(reviewer) != 3:
            raise UserError(_('An onward review must be assigned to Level 3.'))
        self._tdb_close_activity(_('Submitted to higher level'))
        if deadline:
            self._tdb_internal_write({'tdb_review_deadline': deadline})
        self._tdb_start_review(reviewer, new_round=False)
        return True

    def action_tdb_forward_review(self):
        self.ensure_one()
        return self._tdb_wizard('forward')

    def action_tdb_submit_review(self):
        self.ensure_one()
        self.check_access('write')
        level = review_level(self.env.user)
        if level == 3:
            record = self.product_variant_id if self._name == 'product.template' else self
            return record.action_confirm()
        if level not in (1, 2):
            raise AccessError(_('Only a PLM user can submit for review.'))
        if self.engineering_state != 'draft' or self.tdb_redo_chain:
            raise UserError(_('Submit only Draft work without an active redo task.'))
        self._tdb_check_shared_reviews()
        action = self._tdb_wizard('submit')
        action['name'] = _('Submit Review')
        reviewer = self.tdb_reviewer_id
        if reviewer.active and not reviewer.share and review_level(reviewer) > level:
            action['context']['default_user_id'] = reviewer.id
        return action

    def _tdb_submit_for_review(self, reviewer, deadline):
        self._tdb_lock()
        self.ensure_one()
        if review_level(self.env.user) not in (1, 2):
            raise AccessError(_('Only Level 1 or Level 2 can submit a review request.'))
        if self.engineering_state != 'draft' or self.tdb_redo_chain:
            raise UserError(_('Submit only Draft work without an active redo task.'))
        self._tdb_check_shared_reviews(lock=True)
        reviewer = self._tdb_validate_reviewer(reviewer)
        self.write({'tdb_reviewer_id': reviewer.id, 'tdb_review_deadline': deadline})
        record = self.product_variant_id if self._name == 'product.template' else self
        return record.action_confirm()

    def _tdb_wizard(self, operation):
        self.check_access('write')
        return {'type': 'ir.actions.act_window', 'name': _('Review / Redo'),
                'res_model': 'tdb.plm.task.wizard', 'view_mode': 'form', 'target': 'new',
                'context': {'default_target_model': self._name, 'default_target_id': self.id,
                            'default_operation': operation}}

    def action_tdb_reject(self):
        self.ensure_one()
        self._tdb_check_reject_actor()
        return self._tdb_wizard('reject')

    def _tdb_check_reject_actor(self):
        if self._tdb_review_needs_recovery():
            if review_level(self.env.user) != 3:
                raise AccessError(_('Level 3 must reset legacy reviews with a reason.'))
        else:
            self._tdb_check_review_actor()

    def _tdb_reject(self, reason, assignee=None, deadline=None):
        self._tdb_lock()
        self.ensure_one()
        self._tdb_check_reject_actor()
        reason = (reason or '').strip()
        if not reason:
            raise UserError(_('A rejection reason is required.'))
        assignee = assignee or self.env.user
        self._tdb_check_user_access(assignee)
        if assignee != self.env.user and review_level(assignee) >= review_level(self.env.user):
            raise UserError(_('Assign redo to yourself or to a lower-level user.'))
        self._tdb_refuse_checked_out()
        self._tdb_close_activity(_('Rejected: %s', reason))
        frame = {'id': uuid4().hex, 'assigner_id': self.env.uid, 'worker_id': assignee.id,
                 'reason': reason, 'assigner_level': review_level(self.env.user)}
        self._tdb_internal_write({
            'engineering_state': 'draft', 'engineering_writable': True,
            'tdb_review_stage': 'redo', 'tdb_redo_chain': [frame],
            'tdb_redo_user_id': assignee.id, 'tdb_reviewer_id': False,
            'tdb_manager_approved_by': False, 'tdb_manager_approved_at': False,
            'tdb_admin_approved_by': False, 'tdb_admin_approved_at': False,
        })
        activity = self._tdb_open_activity('redo', assignee, reason, deadline)
        frame['activity_id'] = activity.id
        self._tdb_internal_write({'tdb_redo_chain': [frame]})
        self._tdb_history_event('rejected_redo_assigned', reason, extra=frame)
        return True

    def _tdb_discard_invalid_activity_link(self):
        activity = self.tdb_task_activity_id
        if activity and (not activity.tdb_plm_kind or activity.tdb_plm_target_model != self._name
                         or activity.tdb_plm_target_id != self.id
                         or activity.res_model != self._name or activity.res_id != self.id):
            self._tdb_internal_write({'tdb_task_activity_id': False})

    def _tdb_close_recovery_activities(self):
        self._tdb_discard_invalid_activity_link()
        activities = self.env['mail.activity'].search([
            ('tdb_plm_target_model', '=', self._name), ('tdb_plm_target_id', '=', self.id),
            ('res_model', '=', self._name), ('res_id', '=', self.id),
            ('tdb_plm_kind', '!=', False), ('date_done', '=', False),
        ])
        for activity in activities:
            activity = activity.with_context(_tdb_task_action=_TASK_ACTION)
            activity.write({'plm_state': 'done'})
            activity._action_done(feedback=_('Replaced by administrative review recovery'))
        self._tdb_internal_write({'tdb_task_activity_id': False})

    def action_tdb_recover_review(self):
        self.check_access('write')
        if review_level(self.env.user) != 3:
            raise AccessError(_('Only Level 3 can recover orphaned reviews.'))
        if not self or any(not record._tdb_review_needs_recovery() for record in self):
            raise UserError(_('Select only records marked Review Needs Recovery. Valid active reviews are not replaced.'))
        return {'type': 'ir.actions.act_window', 'name': _('Recover Unassigned Review'),
                'res_model': 'tdb.plm.task.wizard', 'view_mode': 'form', 'target': 'new',
                'context': {'default_target_model': self._name, 'default_target_id': self[0].id,
                            'default_target_ids': self.ids, 'default_operation': 'recover'}}

    def _tdb_recover_review(self, requester, reviewer, reason, deadline=None):
        self._tdb_lock()
        self.ensure_one()
        if review_level(self.env.user) != 3 or not self._tdb_review_needs_recovery():
            raise AccessError(_('Level 3 can recover only an orphaned review, not replace a valid active review.'))
        reason = (reason or '').strip()
        if not reason:
            raise UserError(_('A recovery reason is required.'))
        self._tdb_check_user_access(requester)
        self._tdb_check_user_access(reviewer)
        if review_level(requester) not in (1, 2) or review_level(reviewer) <= review_level(requester):
            raise UserError(_('Choose an LV1/LV2 request owner and a higher-level reviewer.'))
        self._tdb_refuse_checked_out()
        previous = {'previous_stage': self.tdb_review_stage,
                    'previous_reviewer_id': self.tdb_reviewer_id.id,
                    'previous_activity_id': self.tdb_task_activity_id.id,
                    'previous_manager_approval_id': self.tdb_manager_approved_by.id,
                    'previous_admin_approval_id': self.tdb_admin_approved_by.id}
        self._tdb_close_recovery_activities()
        self._tdb_internal_write({
            'engineering_state': 'confirmed', 'engineering_writable': False,
            'tdb_review_stage': 'review', 'tdb_reviewer_id': reviewer.id,
            'tdb_requester_id': requester.id, 'tdb_request_level': review_level(requester),
            'tdb_forwarder_id': False, 'tdb_redo_user_id': False,
            'tdb_review_deadline': deadline or fields.Date.context_today(self),
            'tdb_manager_approved_by': False, 'tdb_manager_approved_at': False,
            'tdb_admin_approved_by': False, 'tdb_admin_approved_at': False,
        })
        activity = self._tdb_open_activity('review', reviewer, reason, deadline)
        self._tdb_history_event('legacy_review_recovered', reason, new_round=True,
            extra=dict(previous, requested_for=requester.id, reviewer_id=reviewer.id,
                       activity_id=activity.id))
        return True

    def action_tdb_delegate_redo(self):
        self.ensure_one()
        self._tdb_check_worker()
        if not self.tdb_redo_chain:
            raise UserError(_('No active redo task.'))
        return self._tdb_wizard('delegate')

    def _tdb_delegate_redo(self, worker, reason, deadline=None):
        self._tdb_lock()
        self._tdb_check_worker()
        if not self.tdb_redo_chain:
            raise UserError(_('No active redo task.'))
        self._tdb_check_user_access(worker)
        if review_level(worker) >= review_level(self.env.user):
            raise UserError(_('Delegate only to a lower-level user.'))
        reason = (reason or '').strip()
        if not reason:
            raise UserError(_('Delegation instructions are required.'))
        self._tdb_refuse_checked_out()
        chain = deepcopy(self.tdb_redo_chain)
        self.tdb_task_activity_id.with_context(_tdb_task_action=_TASK_ACTION).write({'plm_state': 'in_progress'})
        frame = {'id': uuid4().hex, 'parent_id': chain[-1]['id'], 'assigner_id': self.env.uid,
                 'worker_id': worker.id, 'reason': reason, 'assigner_level': review_level(self.env.user)}
        # The parent's activity stays open while its child task is in progress.
        frame['activity_id'] = self._tdb_open_activity('redo', worker, reason, deadline).id
        chain.append(frame)
        self._tdb_internal_write({'tdb_redo_chain': chain, 'tdb_redo_user_id': worker.id})
        self._tdb_history_event('redo_delegated', reason, extra=frame)
        return True

    def action_tdb_complete_redo(self):
        self._tdb_lock()
        for record in self:
            record._tdb_check_worker()
            chain = deepcopy(record.tdb_redo_chain or [])
            if not chain:
                raise UserError(_('No active redo task.'))
            record._tdb_refuse_checked_out()
            frame = chain.pop()
            record._tdb_close_activity(_('Redo work completed and returned'))
            record._tdb_history_event('redo_completed', extra=frame)
            if chain:
                # Reactivate the assigning worker's suspended redo task.
                parent = chain[-1]
                record._tdb_internal_write({'tdb_redo_chain': chain,
                    'tdb_redo_user_id': parent['worker_id'],
                    'tdb_task_activity_id': parent['activity_id']})
                record.tdb_task_activity_id.with_context(_tdb_task_action=_TASK_ACTION).write({'plm_state': 'draft',
                    'summary': _('Delegated redo completed - please verify returned work')})
                record._tdb_history_event('redo_returned_to_assigner', extra={'user_id': self.env.uid,
                    'returned_to': parent['worker_id'], 'task_id': parent['id']})
                record._tdb_notify_task_user(record.tdb_redo_user_id,
                    _('Delegated redo returned: %s', record.display_name),
                    _('Delegated redo completed—please verify the returned work. '
                      '%(worker)s returned %(record)s to you.',
                      worker=self.env.user.display_name, record=record.display_name))
            else:
                record._tdb_internal_write({'tdb_redo_chain': False, 'tdb_redo_user_id': False})
                assigner = self.env['res.users'].browse(frame['assigner_id'])
                if assigner == self.env.user:
                    if review_level(self.env.user) == 3:
                        record.move_to_state('released')
                    else:
                        # LV2 changed the work themselves: no self-approval.
                        record._tdb_internal_write({'engineering_state': 'confirmed',
                            'engineering_writable': False, 'tdb_review_stage': 'forward',
                            'tdb_forwarder_id': self.env.uid, 'tdb_reviewer_id': False})
                        record._tdb_open_activity('forward', self.env.user)
                        record._tdb_history_event('self_redo_requires_level_3')
                else:
                    record._tdb_start_review(assigner)
        return True


class ProductTemplateReviewDefaults(models.Model):
    _inherit = 'product.template'

    def _tdb_parent_reviewers(self, visited=None):
        self.ensure_one()
        visited = set(visited or ())
        if self.id in visited:
            return set()
        visited.add(self.id)
        if self.tdb_reviewer_id:
            return {self.tdb_reviewer_id.id}
        lines = self.env['mrp.bom.line'].sudo().search([
            ('product_id.product_tmpl_id', '=', self.id)]).with_env(self.env)
        lines.check_access('read')
        parents = lines.bom_id.product_tmpl_id
        parents.check_access('read')
        return set().union(*(parent._tdb_parent_reviewers(visited) for parent in parents)) if parents else set()

    def _tdb_prepare_reviewers(self):
        super()._tdb_prepare_reviewers()
        if self.engineering_state != 'draft' or self.tdb_reviewer_id:
            return
        self._tdb_lock()
        reviewers = self._tdb_parent_reviewers()
        if len(reviewers) > 1:
            raise UserError(_('Parent assemblies have different reviewers. Select this Part reviewer explicitly.'))
        if reviewers:
            self.write({'tdb_reviewer_id': reviewers.pop()})
            self._tdb_history_event('reviewer_inherited_from_parent')


class ProductReviewActions(models.Model):
    _inherit = 'product.product'

    def unlink(self):
        return super(ProductReviewActions, self.with_context(_tdb_discard_revision=_DISCARD_REVISION)).unlink()

    def unlinkRestorePreviousComponent(self):
        return self.product_tmpl_id._tdb_restore_after_discard()

    def NewRevision(self):
        """CAD-compatible result using the guarded template revision routine.

        The legacy variant implementation writes Under Modify on the variant
        first. Its write overload also injects a description on Released data,
        which correctly trips the content lock. Template copy avoids that write.
        """
        self.ensure_one()
        self.check_access('write')
        if not self.engineering_code:
            raise UserError(_('An engineering code is required to create a Part revision.'))
        latest = self.product_tmpl_id.sudo().get_latest_version().with_env(self.env)
        latest._tdb_lock()
        if latest.engineering_state != 'released':
            raise UserError(_('Release the latest Part revision before creating a new revision.'))
        latest._tdb_refuse_checked_out()
        revision = latest.with_context(new_revision=True)._new_version({
            'linkeddocuments': [], 'engineering_writable': True,
            'engineering_revision_user': self.env.uid, 'engineering_revision_date': fields.Datetime.now(),
            'engineering_release_user': False, 'engineering_release_date': False,
            'engineering_workflow_user': False, 'engineering_workflow_date': False,
        })
        variant = revision.product_variant_id
        latest.message_post(body=_('Created new revision %s.', revision.engineering_revision))
        return variant.id, revision.engineering_revision

    @api.model
    def createFromProps(self, productAttribute):
        # The CAD property dialog may echo readonly fields from its last read.
        # Those values are never workflow commands; only explicit actions can
        # change state, ownership, decisions or the active activity.
        readonly = PROTECTED | WORKFLOW_METADATA | {'tdb_actor_level', 'tdb_actor_owns_task'}
        attributes = {key: value for key, value in productAttribute.items()
                      if key not in readonly and key.removeprefix('plm_m2o_') not in readonly}
        return super().createFromProps(attributes)

    def write(self, vals):
        if CONTENT.intersection(vals) or any(
            key.startswith(('engineering_', 'std_', 'tmp_')) and key not in WORKFLOW_METADATA for key in vals):
            self.product_tmpl_id._tdb_assert_content_editable()
        result = super().write(vals)
        self.product_tmpl_id._tdb_notify_live()
        return result

    def action_confirm(self):
        for part in self:
            if part.tdb_review_stage == 'forward':
                part.product_tmpl_id._tdb_forward(part.tdb_reviewer_id)
            elif part.tdb_redo_chain:
                raise UserError(_('Complete the redo activity before confirming.'))
            elif review_level(self.env.user) == 3:
                part.commonWFAction('released', ['draft', 'confirmed'], recursive=True)
            else:
                super(ProductReviewActions, part).action_confirm()
        return True

    def action_tdb_approve(self):
        if review_level(self.env.user) == 3:
            for part in self:
                part.product_tmpl_id._tdb_check_review_actor()
                part.action_release()
            return True
        return self.product_tmpl_id.action_tdb_approve()

    def action_tdb_manager_approve(self):
        return self.action_tdb_approve()

    def action_tdb_approve_submit(self):
        self.ensure_one()
        return self.product_tmpl_id.action_tdb_approve_submit()

    def action_tdb_submit_review(self):
        self.ensure_one()
        return self.product_tmpl_id.action_tdb_submit_review()

    def action_tdb_reject(self):
        self.ensure_one()
        return self.product_tmpl_id.action_tdb_reject()

    def action_tdb_forward_review(self):
        self.ensure_one()
        return self.product_tmpl_id.action_tdb_forward_review()

    def action_tdb_delegate_redo(self):
        self.ensure_one()
        return self.product_tmpl_id.action_tdb_delegate_redo()

    def action_tdb_complete_redo(self):
        return self.product_tmpl_id.action_tdb_complete_redo()

    def action_tdb_recover_review(self):
        return self.product_tmpl_id.action_tdb_recover_review()


class RejectWizard(models.TransientModel):
    _name = 'tdb.plm.reject.wizard'
    _description = 'Reject PLM Review (legacy RPC compatibility)'
    target_model = fields.Selection([('ir.attachment', 'Document'), ('product.template', 'Component')], required=True)
    target_id = fields.Integer(required=True)
    reason = fields.Text(required=True)
    assignee_id = fields.Many2one('res.users', default=lambda self: self.env.user, required=True)


class TaskWizard(models.TransientModel):
    _name = 'tdb.plm.task.wizard'
    _description = 'PLM Review / Redo Assignment'
    target_model = fields.Selection([('ir.attachment', 'Document'), ('product.template', 'Component')], required=True)
    target_id = fields.Integer(required=True)
    target_ids = fields.Json(readonly=True)
    operation = fields.Selection([('submit', 'Submit Review'), ('forward', 'Submit to Higher Level'),
                                 ('approve_forward', 'Approve and Submit to Level 3'), ('reject', 'Reject and Assign Redo'),
                                 ('delegate', 'Delegate Redo'), ('recover', 'Recover Review')], required=True)
    requester_id = fields.Many2one('res.users', string='Review Request Owner')
    user_id = fields.Many2one('res.users', string='Assign To', required=True)
    reason = fields.Text(string='Reason / Instructions')
    date_deadline = fields.Date(default=fields.Date.context_today, required=True)

    def action_apply(self):
        self.ensure_one()
        records = self.env[self.target_model].browse(self.target_ids or [self.target_id]).exists().sorted('id')
        if not records:
            raise UserError(_('The workflow record no longer exists.'))
        if self.operation == 'recover':
            for record in records:
                record._tdb_recover_review(self.requester_id, self.user_id, self.reason, self.date_deadline)
            return {'type': 'ir.actions.act_window_close'}
        records.ensure_one()
        record = records
        if self.operation == 'submit':
            record._tdb_submit_for_review(self.user_id, self.date_deadline)
        elif self.operation == 'approve_forward':
            record._tdb_approve_and_submit(self.user_id, self.date_deadline)
        elif self.operation == 'forward':
            record._tdb_forward(self.user_id, self.date_deadline)
        elif self.operation == 'delegate':
            record._tdb_delegate_redo(self.user_id, self.reason, self.date_deadline)
        else:
            raise UserError(_('Unsupported workflow operation.'))
        return {'type': 'ir.actions.act_window_close'}
