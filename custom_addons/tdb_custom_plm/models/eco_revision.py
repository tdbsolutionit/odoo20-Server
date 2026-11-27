from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


_CANCEL_ECO = object()
_FINISH_CHILD = object()
_PLM_MANAGEMENT_FIELDS = {
    'summary', 'name', 'date_deadline', 'user_id', 'role_id', 'activity_type_id',
    'res_id', 'res_model_id', 'res_model', 'mail_parent_eco_activity_id',
    'children_ids', 'eco_child_ids', 'tdb_eco_revision_ids', 'is_eco',
}


class EcoActivity(models.Model):
    _inherit = 'mail.activity'

    eco_child_ids = fields.One2many(context={'active_test': False})
    tdb_eco_revision_ids = fields.One2many(
        'tdb.plm.eco.revision', 'eco_id', string='Affected Revisions', copy=False,
    )
    tdb_is_eco_order = fields.Boolean(compute='_compute_tdb_is_eco_order')
    tdb_form_only_done = fields.Boolean(compute='_compute_tdb_form_only_done')
    tdb_can_finish_eco_activity = fields.Boolean(compute='_compute_tdb_can_finish_eco_activity')
    tdb_can_cancel_plm_activity = fields.Boolean(compute='_compute_tdb_can_cancel_plm_activity')
    tdb_can_manage_plm_activity = fields.Boolean(compute='_compute_tdb_plm_action_access')
    tdb_can_start_plm_activity = fields.Boolean(compute='_compute_tdb_plm_action_access')
    tdb_can_edit_plm_activity = fields.Boolean(compute='_compute_tdb_plm_action_access')

    def _tdb_plm_parent(self):
        self.ensure_one()
        return self.mail_parent_eco_activity_id or (
            self.getParentECRActivity(self) if self.change_activity_type == 'request' else self.browse()
        )

    def _tdb_plm_action_access(self):
        self.ensure_one()
        if self.change_activity_type not in ('request', 'plm_activity') and not self.mail_parent_eco_activity_id:
            return True, True, True
        if not self.active or not self.has_access('write'):
            return False, False, False
        user = self.env.user
        own = self.user_id == user
        parent = self._tdb_plm_parent()
        coordinator = user.has_group('activity_validation.group_force_activity_validation_user') or user.has_group(
            'activity_validation.group_force_activity_validation_admin')
        if parent:
            manage = self.create_uid == user or any(
                order.create_uid == user or (coordinator and order.user_id == user) for order in parent
            )
        else:
            manage = self.create_uid == user or (coordinator and own)
        work = own and (bool(parent) or self.change_activity_type == 'plm_activity')
        return manage, manage or work, manage or work

    @api.depends('active', 'user_id', 'create_uid', 'change_activity_type', 'mail_parent_eco_activity_id',
                 'mail_parent_eco_activity_id.user_id', 'mail_parent_eco_activity_id.create_uid')
    @api.depends_context('uid')
    def _compute_tdb_plm_action_access(self):
        for activity in self:
            manage, start, edit = activity._tdb_plm_action_access()
            activity.tdb_can_manage_plm_activity = manage
            activity.tdb_can_start_plm_activity = start
            activity.tdb_can_edit_plm_activity = edit

    def _tdb_check_manage_plm(self):
        if any(not activity._tdb_plm_action_access()[0] for activity in self):
            raise AccessError(_('Only the activity creator or its coordinator can manage this activity.'))

    def _tdb_check_start_plm(self):
        if any(not activity._tdb_plm_action_access()[1] for activity in self):
            raise AccessError(_('Only the assigned worker or coordinator can start this activity.'))

    @api.depends('active', 'create_uid', 'change_activity_type', 'mail_parent_eco_activity_id',
                 'mail_parent_eco_activity_id.user_id', 'mail_parent_eco_activity_id.create_uid')
    @api.depends_context('uid')
    def _compute_tdb_can_cancel_plm_activity(self):
        for activity in self:
            activity.tdb_can_cancel_plm_activity = activity._tdb_can_cancel_plm()

    def _tdb_can_cancel_plm(self):
        self.ensure_one()
        if self.change_activity_type not in ('request', 'plm_activity') and not self.mail_parent_eco_activity_id:
            return True
        if not self.active or not self.has_access('write'):
            return False
        if self.create_uid == self.env.user:
            return True
        return self._tdb_plm_action_access()[0]

    def _tdb_check_plm_cancellation(self):
        if any(not activity._tdb_can_cancel_plm() for activity in self):
            raise AccessError(_('Only the activity creator or the coordinator of its parent request can cancel this activity.'))

    @api.depends('change_activity_type', 'mail_parent_eco_activity_id')
    def _compute_tdb_form_only_done(self):
        for activity in self:
            activity.tdb_form_only_done = (
                activity.change_activity_type == 'request' or bool(activity.mail_parent_eco_activity_id)
            )

    @api.depends('tdb_form_only_done', 'active', 'user_id', 'tdb_can_manage_plm_activity')
    @api.depends_context('uid')
    def _compute_tdb_can_finish_eco_activity(self):
        for activity in self:
            activity.tdb_can_finish_eco_activity = activity._tdb_can_finish_plm()

    def _tdb_can_finish_plm(self):
        self.ensure_one()
        if not self.tdb_form_only_done:
            return True
        if not self.active or self.user_id != self.env.user or not self.has_access('write'):
            return False
        return bool(self._tdb_plm_parent()) or self._tdb_plm_action_access()[0]

    def _tdb_check_eco_completion_route(self):
        scoped = self.filtered(lambda activity: activity.active and activity.tdb_form_only_done)
        if not scoped:
            return
        if self.env.context.get('_tdb_cancel_eco') is _CANCEL_ECO and all(
                activity.plm_state == 'cancel' for activity in scoped):
            return
        if self.env.context.get('_tdb_finish_eco_child') is not _FINISH_CHILD:
            raise UserError(_('Open the ECR or ECO activity form and use its Done button.'))
        if any(not activity._tdb_can_finish_plm() for activity in scoped):
            raise AccessError(_('Only the assigned user with completion rights can complete this ECR or ECO activity.'))

    @api.depends('is_eco', 'change_activity_type', 'has_parent', 'mail_parent_eco_activity_id')
    def _compute_tdb_is_eco_order(self):
        for activity in self:
            activity.tdb_is_eco_order = (
                activity.is_eco and activity.change_activity_type == 'request'
                and not activity.has_parent and not activity.mail_parent_eco_activity_id
            )

    def _tdb_check_eco_ready(self):
        orders = self.filtered(lambda activity: activity.active and activity.tdb_is_eco_order)
        if not orders:
            return
        orders.check_access('write')
        orders.lock_for_update(allow_referencing=True)
        for order in orders:
            if (self.env.context.get('_tdb_cancel_eco') is _CANCEL_ECO
                    and order.plm_state == 'cancel'):
                continue
            order.checkChildrenECODone(order)
            lines = order.tdb_eco_revision_ids
            if not lines:
                raise UserError(_('Add the affected revisions before completing the ECO.'))
            lines._check_revision_pair()
            pending = []
            for line in lines.filtered('release_required'):
                original, replacement = line._revision_pair()
                if not replacement:
                    pending.append(_('%(code)s rev %(revision)s: select a replacement revision.',
                                     code=original.engineering_code, revision=original.engineering_revision))
                elif replacement.engineering_state != 'released':
                    pending.append(_('%(code)s rev %(revision)s: %(state)s.',
                                     code=replacement.engineering_code, revision=replacement.engineering_revision,
                                     state=dict(replacement._fields['engineering_state']._description_selection(
                                         self.env))[replacement.engineering_state]))
            if pending:
                raise UserError(_('Release the required replacement revisions before completing the ECO:\n%s',
                                  '\n'.join(pending)))

    @api.model_create_multi
    def create(self, vals_list):
        default_parent = self.env.context.get('default_mail_parent_eco_activity_id')
        for vals in vals_list:
            parent_id = vals.get('mail_parent_eco_activity_id', default_parent)
            if not parent_id:
                continue
            parent = self.browse(parent_id).exists()
            if not parent:
                raise UserError(_('The parent ECO no longer exists.'))
            parent.check_access('write')
            if not parent.tdb_is_eco_order:
                continue
            parent._tdb_check_manage_plm()
            if not parent.active or parent.plm_state != 'eco':
                raise UserError(_('ECO activities can only be added to an open Change Order.'))
            if not parent.res_model_id or not parent.res_id:
                raise UserError(_('Link the Change Order to a Part before adding ECO activities.'))
            # The editable list may omit res_id. Every ECO child inherits the
            # exact Part and revision of the order, including in batch create.
            vals.update(res_model_id=parent.res_model_id.id, res_id=parent.res_id)
            if not vals.get('activity_type_id'):
                vals['activity_type_id'] = self.env.ref('plm.mail_activity_plm_activity').id
        return super().create(vals_list)

    def write(self, vals):
        if {'summary', 'date_deadline', 'user_id', 'mail_parent_eco_activity_id'}.intersection(vals):
            self._tdb_check_eco_child_editable()
        if _PLM_MANAGEMENT_FIELDS.intersection(vals) or vals.get('plm_state') in ('draft', 'eco', 'exception'):
            self._tdb_check_manage_plm()
        elif vals.get('plm_state') == 'in_progress':
            self._tdb_check_start_plm()
        if 'note' in vals and any(not activity._tdb_plm_action_access()[2] for activity in self):
            raise AccessError(_('Only the assigned worker or coordinator can edit the activity note.'))
        if vals.get('plm_state') == 'eco':
            for activity in self.filtered('tdb_form_only_done'):
                if activity.change_activity_type != 'request' or activity._tdb_plm_parent():
                    raise UserError(_('Only the original Change Request can be converted to an ECO.'))
                activity.checkChildrenECRDone(activity)
        if vals.get('plm_state') == 'cancel':
            self._tdb_check_plm_cancellation()
        orders = self.filtered('tdb_is_eco_order')
        if orders and (
            ('is_eco' in vals and not vals['is_eco'])
            or vals.get('mail_parent_eco_activity_id')
            or ('activity_type_id' in vals and any(order.activity_type_id.id != vals['activity_type_id'] for order in orders))
        ):
            raise UserError(_('An ECO must retain its activity type and remain a standalone order.'))
        if vals.get('plm_state') == 'done' or ('active' in vals and not vals['active']):
            self._tdb_check_eco_completion_route()
            if orders and 'tdb_eco_revision_ids' in vals:
                raise UserError(_('Save the affected revisions before completing the ECO.'))
            self._tdb_check_eco_ready()
        return super().write(vals)

    def unlink(self):
        self._tdb_check_eco_child_editable()
        self._tdb_check_manage_plm()
        return super().unlink()

    def _tdb_check_eco_child_editable(self):
        if any(not activity.active and activity.mail_parent_eco_activity_id.tdb_is_eco_order
               for activity in self):
            raise UserError(_('Completed or canceled ECO activities are read-only.'))

    def _action_done(self, feedback=False, attachment_ids=None):
        self._tdb_check_eco_completion_route()
        self._tdb_check_eco_ready()
        return super()._action_done(feedback=feedback, attachment_ids=attachment_ids)

    def _store_activity_fields(self, res):
        super()._store_activity_fields(res)
        res.extend(['tdb_form_only_done', 'tdb_can_edit_plm_activity', 'tdb_can_manage_plm_activity'])

    def action_in_progress(self):
        self._tdb_check_start_plm()
        return super().action_in_progress()

    def action_to_draft(self):
        self._tdb_check_manage_plm()
        return super().action_to_draft()

    def action_to_eco(self):
        self._tdb_check_manage_plm()
        return super().action_to_eco()

    def action_to_exception(self):
        self._tdb_check_manage_plm()
        return super().action_to_exception()

    def action_to_cancel(self):
        self._tdb_check_plm_cancellation()
        return super(EcoActivity, self.with_context(_tdb_cancel_eco=_CANCEL_ECO)).action_to_cancel()

    def action_to_done(self):
        return super(EcoActivity, self.with_context(_tdb_finish_eco_child=_FINISH_CHILD)).action_to_done()

    def getParentECOActivity(self, activity_id):
        parent = super().getParentECOActivity(activity_id)
        if self.env.context.get('_tdb_finish_eco_child') is _FINISH_CHILD:
            # Finishing the last child leaves the ECO open for explicit sign-off.
            return parent.filtered(lambda activity: not activity.tdb_is_eco_order)
        return parent


class EcrActivityLine(models.Model):
    _inherit = 'mail.activity.children.rel'

    @api.model_create_multi
    def create(self, vals_list):
        default_parent = self.env.context.get('default_mail_parent_activity_id')
        parent_ids = {vals.get('mail_parent_activity_id', default_parent) for vals in vals_list}
        self.env['mail.activity'].browse(parent_ids - {None, False})._tdb_check_manage_plm()
        return super().create(vals_list)

    def write(self, vals):
        parents = self.mail_parent_activity_id | self.env['mail.activity'].browse(vals.get('mail_parent_activity_id'))
        parents._tdb_check_manage_plm()
        return super().write(vals)

    def unlink(self):
        self.mail_parent_activity_id._tdb_check_manage_plm()
        return super().unlink()


class EcoRevision(models.Model):
    _name = 'tdb.plm.eco.revision'
    _description = 'Engineering Change Order Affected Revision'

    eco_id = fields.Many2one('mail.activity', required=True, ondelete='cascade', index=True)
    kind = fields.Selection([('part', 'Part'), ('document', 'CAD Document')], required=True, default='part')
    original_part_id = fields.Many2one('product.product', string='Original Part', ondelete='restrict')
    replacement_part_id = fields.Many2one('product.product', string='Replacement Part', ondelete='restrict')
    original_document_id = fields.Many2one('ir.attachment', string='Original Document', ondelete='restrict')
    replacement_document_id = fields.Many2one('ir.attachment', string='Replacement Document', ondelete='restrict')
    release_required = fields.Boolean(string='Release Required', default=True)
    original_code = fields.Char(compute='_compute_revision_info')
    original_revision = fields.Integer(compute='_compute_revision_info')
    replacement_revision = fields.Integer(compute='_compute_revision_info')
    replacement_status = fields.Selection(selection=[
        ('draft', 'Draft'), ('confirmed', 'Review'), ('released', 'Released'),
        ('undermodify', 'Under Modify'), ('obsoleted', 'Obsolete'),
    ], compute='_compute_revision_info')

    _unique_original_part = models.Constraint(
        'unique (eco_id, original_part_id)', 'This original Part revision is already on the ECO.')
    _unique_original_document = models.Constraint(
        'unique (eco_id, original_document_id)', 'This original document revision is already on the ECO.')

    def _revision_pair(self):
        self.ensure_one()
        if self.kind == 'part':
            return self.original_part_id, self.replacement_part_id
        return self.original_document_id, self.replacement_document_id

    @api.depends('kind', 'original_part_id.engineering_code', 'original_document_id.engineering_code',
                 'original_part_id.engineering_revision', 'original_document_id.engineering_revision',
                 'replacement_part_id.engineering_revision', 'replacement_document_id.engineering_revision',
                 'replacement_part_id.engineering_state', 'replacement_document_id.engineering_state')
    def _compute_revision_info(self):
        for line in self:
            original, replacement = line._revision_pair()
            line.original_code = original.engineering_code
            line.original_revision = original.engineering_revision
            line.replacement_revision = replacement.engineering_revision
            line.replacement_status = replacement.engineering_state

    @api.onchange('kind')
    def _onchange_kind(self):
        if self.kind == 'part':
            self.original_document_id = self.replacement_document_id = False
        else:
            self.original_part_id = self.replacement_part_id = False

    @api.constrains('kind', 'original_part_id', 'original_document_id',
                    'replacement_part_id', 'replacement_document_id')
    def _check_revision_pair(self):
        for line in self:
            original, replacement = line._revision_pair()
            if (not original
                    or (line.kind == 'part' and (line.original_document_id or line.replacement_document_id))
                    or (line.kind == 'document' and (line.original_part_id or line.replacement_part_id))):
                raise ValidationError(_('Select an original revision and use only fields for its record type.'))
            (original | replacement).check_access('read')
            if not original.engineering_code or (line.kind == 'document' and not original.is_plm):
                raise ValidationError(_('Select an engineering Part or a PLM document.'))
            if replacement and (
                replacement.engineering_code != original.engineering_code
                or replacement.company_id != original.company_id
                or replacement.engineering_branch_parent_id != original.engineering_branch_parent_id
                or replacement.engineering_revision <= original.engineering_revision
                or (line.kind == 'document' and not replacement.is_plm)
            ):
                raise ValidationError(_('The replacement must be a later revision of the same engineering record.'))

    def _check_editable_eco(self, orders):
        orders.check_access('write')
        orders._tdb_check_manage_plm()
        orders.lock_for_update(allow_referencing=True)
        if any(not order.tdb_is_eco_order or not order.active or order.plm_state in ('done', 'cancel')
               for order in orders):
            raise UserError(_('Affected revisions can only be edited on an open ECO.'))

    @api.model_create_multi
    def create(self, vals_list):
        default_order = self.env.context.get('default_eco_id')
        orders = self.env['mail.activity'].browse({vals.get('eco_id', default_order) for vals in vals_list} - {False, None})
        self._check_editable_eco(orders)
        return super().create(vals_list)

    def write(self, vals):
        if 'eco_id' in vals:
            raise UserError(_('Create a new line to add the revision to a different ECO.'))
        self._check_editable_eco(self.eco_id)
        return super().write(vals)

    def unlink(self):
        self._check_editable_eco(self.eco_id)
        return super().unlink()
