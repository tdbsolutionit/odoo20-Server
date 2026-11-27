from odoo.tools.safe_eval import safe_eval
from lxml import etree
from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'tdb_plm_review')
class TestReviewWorkflow(TransactionCase):
    def test_schedule_activity_excludes_workflow_only_types(self):
        scheduler = self.env['mail.activity.schedule'].new({'res_model': 'product.template'})
        available_ids = scheduler.activity_type_available_ids.ids
        self.assertIn(self.env.ref('mail.mail_activity_data_todo').id, available_ids)
        for kind in ('review', 'forward', 'redo'):
            self.assertNotIn(self.env.ref('tdb_custom_plm.activity_type_' + kind).id, available_ids)

    def test_clock_search_survives_without_navigation_context(self):
        doc = self.document(reviewer=self.manager)
        doc.action_confirm()
        Activity = self.env['mail.activity'].with_user(self.manager)
        view = Activity.get_view(
            view_id=self.env.ref('activity_validation.plm_activity_search').id,
            view_type='search')
        arch = etree.fromstring(view['arch'])
        self.assertFalse(arch.xpath("//filter[@name='tdb_clock_selection']"))
        assignment = arch.xpath("//filter[@name='tdb_my_plm_activities']")[0]
        # No transient context or activity IDs are needed after restoring the action.
        domain = safe_eval(assignment.get('domain'), {'uid': self.manager.id})
        action_domain = safe_eval(self.env.ref('activity_validation.plm_action_view_activities').domain)
        self.assertIn(doc.tdb_task_activity_id, Activity.search(action_domain + domain))
        for name in ('late', 'today', 'future'):
            date_filter = arch.xpath("//filter[@name='tdb_plm_%s']" % name)[0]
            date_domain = safe_eval(date_filter.get('domain'))
            matches = Activity.search(action_domain + domain + date_domain)
            self.assertEqual(doc.tdb_task_activity_id in matches, name == 'today')

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['ir.config_parameter'].sudo().set_str('ir_attachment.location', 'db')
        def user(login, group):
            return cls.env['res.users'].with_context(no_reset_password=True).create({
                'name': login, 'login': login,
                'group_ids': [Command.set([cls.env.ref('base.group_user').id, cls.env.ref(group).id])],
            })
        cls.engineer = user('hierarchy_lv1', 'plm.group_plm_integration_user')
        cls.engineer2 = user('hierarchy_other_lv1', 'plm.group_plm_integration_user')
        cls.manager = user('hierarchy_lv2', 'tdb_custom_plm.group_review_manager')
        cls.manager2 = user('hierarchy_other_lv2', 'tdb_custom_plm.group_review_manager')
        cls.admin = user('hierarchy_lv3', 'tdb_custom_plm.group_review_admin')
        cls.admin2 = user('hierarchy_other_lv3', 'tdb_custom_plm.group_review_admin')

    def document(self, name='HIERARCHY-DOC', reviewer=None, actor=None):
        return self.env['ir.attachment'].with_user(actor or self.engineer).create({
            'name': name, 'engineering_code': name, 'is_plm': True,
            'tdb_reviewer_id': (reviewer or self.manager).id,
        })

    def reject(self, doc, reviewer, worker, reason='Correct the dimensions'):
        self.env['tdb.plm.task.wizard'].with_user(reviewer).create({
            'target_model': 'ir.attachment', 'target_id': doc.id, 'operation': 'reject',
            'user_id': worker.id, 'reason': reason,
        }).action_apply()

    def test_level_1_to_2_then_explicit_level_3_review(self):
        doc = self.document()
        doc.action_confirm()
        self.assertEqual(doc.engineering_state, 'confirmed')
        self.assertEqual(doc.tdb_task_activity_id.user_id, self.manager)
        self.assertEqual(doc.tdb_task_activity_id.tdb_plm_kind, 'review')
        doc.with_user(self.manager).action_tdb_approve()
        self.assertEqual(doc.tdb_review_stage, 'forward')
        self.assertEqual(doc.tdb_task_activity_id.user_id, self.manager)
        self.assertFalse(doc.tdb_reviewer_id)
        with self.assertRaises((AccessError, UserError)), self.cr.savepoint():
            doc.with_user(self.manager).action_release()
        self.env['tdb.plm.task.wizard'].with_user(self.manager).create({
            'target_model': 'ir.attachment', 'target_id': doc.id, 'operation': 'forward',
            'user_id': self.admin.id,
        }).action_apply()
        self.assertEqual(doc.tdb_request_level, 2)
        self.assertEqual(doc.tdb_reviewer_id, self.admin)
        doc.with_user(self.admin).action_tdb_approve()
        self.assertEqual(doc.engineering_state, 'released')
        decisions = [event['decision'] for event in doc.tdb_review_history[0]['events']]
        self.assertIn('level_2_approved', decisions)
        self.assertEqual(decisions[-1], 'approved_and_released')
        self.assertFalse(doc.tdb_task_activity_id)

    def test_level_1_can_choose_level_3_directly(self):
        doc = self.document(reviewer=self.admin)
        doc.action_confirm()
        doc.with_user(self.admin).action_tdb_approve()
        self.assertEqual(doc.engineering_state, 'released')
        self.assertFalse(doc.tdb_manager_approved_by)

    def test_level_2_can_submit_only_to_level_3(self):
        doc = self.document(actor=self.manager, reviewer=self.manager2)
        with self.assertRaises(UserError), self.cr.savepoint():
            doc.action_confirm()
        doc.tdb_reviewer_id = self.admin
        doc.action_confirm()
        self.assertEqual(doc.tdb_request_level, 2)

    def test_admin_confirms_draft_without_any_reviewer(self):
        doc = self.document(actor=self.admin)
        doc.tdb_reviewer_id = False
        doc.action_confirm()
        self.assertEqual(doc.engineering_state, 'released')
        self.assertEqual(doc.tdb_admin_approved_by, self.admin)
        self.assertEqual(doc.tdb_review_history[-1]['events'][-1]['decision'], 'admin_direct_confirm')

    def test_lower_levels_cannot_direct_release_or_approve_others(self):
        doc = self.document()
        with self.assertRaises(AccessError), self.cr.savepoint():
            doc.with_context(check=False, tdb_skip_review=True).write({'engineering_state': 'released'})
        doc.action_confirm()
        for user in (self.engineer, self.manager2, self.admin):
            with self.assertRaises(AccessError), self.cr.savepoint():
                doc.with_user(user).action_tdb_approve()

    def test_review_assignment_must_be_higher(self):
        for user in (self.engineer, self.engineer2):
            doc = self.document(name='LOWER-' + str(user.id), reviewer=user)
            with self.assertRaises(UserError), self.cr.savepoint():
                doc.action_confirm()
        doc = self.document(name='NO-REVIEWER')
        doc.tdb_reviewer_id = False
        with self.assertRaises(UserError), self.cr.savepoint():
            doc.action_confirm()

    def test_reject_requires_reason_and_self_or_lower_assignee(self):
        doc = self.document()
        doc.action_confirm()
        for worker, reason in ((self.engineer, '  '), (self.manager2, 'Fix'), (self.admin, 'Fix')):
            with self.assertRaises(UserError), self.cr.savepoint():
                self.reject(doc, self.manager, worker, reason)
        self.reject(doc, self.manager, self.engineer)
        self.assertEqual(doc.engineering_state, 'draft')
        self.assertEqual(doc.tdb_review_stage, 'redo')
        self.assertEqual(doc.tdb_redo_user_id, self.engineer)

    def test_redo_editing_is_limited_to_active_worker(self):
        doc = self.document()
        doc.action_confirm()
        self.reject(doc, self.manager, self.engineer)
        for user in (self.manager, self.admin, self.engineer2):
            with self.assertRaises(AccessError), self.cr.savepoint():
                doc.with_user(user).write({'description': 'Not their task'})
        doc.write({'description': 'Corrected by the worker'})
        with self.assertRaises(UserError), self.cr.savepoint():
            doc.action_confirm()
        doc.action_tdb_complete_redo()
        self.assertEqual(doc.engineering_state, 'confirmed')
        self.assertEqual(doc.tdb_reviewer_id, self.manager)
        self.assertEqual(doc.tdb_requester_id, self.engineer)

    def test_redo_delegation_returns_through_assignment_chain(self):
        doc = self.document(reviewer=self.admin)
        doc.action_confirm()
        self.reject(doc, self.admin, self.manager)
        parent_activity = doc.tdb_task_activity_id
        self.env['tdb.plm.task.wizard'].with_user(self.manager).create({
            'target_model': 'ir.attachment', 'target_id': doc.id, 'operation': 'delegate',
            'user_id': self.engineer.id, 'reason': 'Update dimensions',
        }).action_apply()
        self.assertEqual(len(doc.tdb_redo_chain), 2)
        self.assertEqual(doc.tdb_task_activity_id.user_id, self.engineer)
        with self.assertRaises(AccessError), self.cr.savepoint():
            doc.with_user(self.manager).action_tdb_complete_redo()
        doc.action_tdb_complete_redo()
        self.assertEqual(doc.engineering_state, 'draft')
        self.assertEqual(doc.tdb_redo_user_id, self.manager)
        self.assertEqual(doc.tdb_task_activity_id, parent_activity)
        doc.with_user(self.manager).action_tdb_complete_redo()
        self.assertEqual(doc.engineering_state, 'confirmed')
        self.assertEqual(doc.tdb_reviewer_id, self.admin)
        self.assertEqual(doc.tdb_request_level, 2)
        doc.with_user(self.admin).action_tdb_approve()
        self.assertEqual(doc.engineering_state, 'released')

    def test_admin_can_redo_and_confirm_own_work(self):
        doc = self.document(reviewer=self.admin)
        doc.action_confirm()
        self.reject(doc, self.admin, self.admin)
        doc.with_user(self.admin).write({'description': 'Admin correction'})
        doc.with_user(self.admin).action_tdb_complete_redo()
        self.assertEqual(doc.engineering_state, 'released')
        self.assertEqual(doc.tdb_admin_approved_by, self.admin)
        self.assertFalse(doc.tdb_redo_chain)

    def test_level_2_self_redo_requires_level_3(self):
        doc = self.document()
        doc.action_confirm()
        self.reject(doc, self.manager, self.manager)
        doc.with_user(self.manager).write({'description': 'Manager correction'})
        doc.with_user(self.manager).action_tdb_complete_redo()
        self.assertEqual(doc.tdb_review_stage, 'forward')
        self.assertFalse(doc.tdb_manager_approved_by)
        with self.assertRaises((AccessError, UserError)), self.cr.savepoint():
            doc.with_user(self.manager).action_tdb_approve()
        doc.with_user(self.manager)._tdb_forward(self.admin)
        doc.with_user(self.admin).action_tdb_approve()
        self.assertEqual(doc.engineering_state, 'released')

    def test_delegation_cannot_move_sideways_or_up(self):
        doc = self.document(reviewer=self.admin)
        doc.action_confirm()
        self.reject(doc, self.admin, self.manager)
        for worker in (self.manager, self.manager2, self.admin):
            with self.assertRaises(UserError), self.cr.savepoint():
                doc.with_user(self.manager)._tdb_delegate_redo(worker, 'Wrong level')

    def test_marking_activity_done_is_not_an_approval(self):
        doc = self.document()
        doc.action_confirm()
        activity = doc.tdb_task_activity_id.with_user(self.manager)
        for action in (lambda: activity.action_feedback(), lambda: activity.action_to_done(),
                       lambda: activity.with_context(_tdb_task_action=True)._action_done(),
                       lambda: activity.write({'user_id': self.admin.id}), lambda: activity.unlink()):
            with self.assertRaises((AccessError, UserError)), self.cr.savepoint():
                action()
        self.assertEqual(doc.tdb_review_stage, 'review')
        self.assertEqual(doc.engineering_state, 'confirmed')

    def test_forged_task_and_workflow_data_are_rejected(self):
        doc = self.document()
        with self.assertRaises(AccessError), self.cr.savepoint():
            doc.write({'tdb_review_history': []})
        with self.assertRaises(AccessError), self.cr.savepoint():
            self.env['mail.activity'].create({'tdb_plm_kind': 'review'})
        with self.assertRaises(AccessError), self.cr.savepoint():
            self.env['ir.attachment'].with_context(default_tdb_review_stage='released').create({'name': 'Forged'})
        with self.assertRaises(UserError), self.cr.savepoint():
            doc.write({'engineering_state': False})

    def test_document_inherits_one_part_reviewer(self):
        doc = self.document()
        doc.tdb_reviewer_id = False
        self.env['product.product'].with_user(self.engineer).create({
            'name': 'PART-DEFAULT', 'engineering_code': 'PART-DEFAULT',
            'tdb_reviewer_id': self.manager.id, 'linkeddocuments': [Command.link(doc.id)],
        })
        doc.action_confirm()
        self.assertEqual(doc.tdb_reviewer_id, self.manager)

    def test_document_ambiguous_part_reviewers_are_blocked(self):
        doc = self.document()
        doc.tdb_reviewer_id = False
        for index, reviewer in enumerate((self.manager, self.admin)):
            self.env['product.product'].with_user(self.engineer).create({
                'name': 'PART-' + str(index), 'engineering_code': 'PART-' + str(index),
                'tdb_reviewer_id': reviewer.id, 'linkeddocuments': [Command.link(doc.id)],
            })
        with self.assertRaises(UserError), self.cr.savepoint():
            doc.action_confirm()

    def test_recursive_component_submission_inherits_reviewer(self):
        Product = self.env['product.product'].with_user(self.engineer)
        parent = Product.create({'name': 'PARENT', 'engineering_code': 'PARENT', 'tdb_reviewer_id': self.manager.id})
        child = Product.create({'name': 'CHILD', 'engineering_code': 'CHILD'})
        self.env['mrp.bom'].with_user(self.engineer).create({
            'product_id': parent.id, 'product_tmpl_id': parent.product_tmpl_id.id,
            'bom_line_ids': [Command.create({'product_id': child.id, 'product_qty': 1})],
        })
        parent.action_confirm()
        self.assertEqual(parent.engineering_state, 'confirmed')
        self.assertEqual(child.engineering_state, 'confirmed')
        self.assertEqual(child.tdb_reviewer_id, self.manager)
        self.assertEqual(child.tdb_task_activity_id.user_id, self.manager)

    def test_cad_activity_inbox_includes_review_task(self):
        import json
        doc = self.document()
        doc.action_confirm()
        headers, rows = json.loads(self.env['product.product'].with_user(self.manager).get_user_activities())
        row = next(row for row in rows if row['id'] == doc.tdb_task_activity_id.id)
        self.assertFalse(row['action_tdb_open_workflow'])
        self.assertTrue(row['action_to_done'])
        self.assertIn('action_tdb_open_workflow', [header['key'] for header in headers])

    def test_legacy_review_can_be_reset_without_fabricating_approval(self):
        doc = self.document()
        self.cr.execute("UPDATE ir_attachment SET engineering_state='confirmed', tdb_review_stage='manager' WHERE id=%s", [doc.id])
        doc.invalidate_recordset()
        self.reject(doc, self.admin, self.engineer)
        self.assertEqual(doc.tdb_review_stage, 'redo')
        self.assertFalse(doc.tdb_admin_approved_by)

    def test_review_content_and_assignments_stay_locked(self):
        doc = self.document()
        doc.action_confirm()
        for vals in ({'description': 'Changed'}, {'tdb_reviewer_id': self.admin.id}, {'engineering_state': 'draft'}):
            with self.assertRaises((AccessError, UserError)), self.cr.savepoint():
                doc.write(vals)

    def test_activity_type_cannot_be_scheduled_as_a_fake_review(self):
        doc = self.document()
        with self.assertRaises(UserError), self.cr.savepoint():
            doc.activity_schedule('tdb_custom_plm.activity_type_review', user_id=self.manager.id)

    def test_assignment_wizard_offers_only_appropriate_levels(self):
        Wizard = self.env['tdb.plm.task.wizard']
        doc = self.document()
        values = {'target_model': 'ir.attachment', 'target_id': doc.id}
        forward = Wizard.with_user(self.manager).create(dict(values, operation='forward', user_id=self.admin.id))
        self.assertIn(self.admin, forward.allowed_user_ids)
        self.assertNotIn(self.manager2, forward.allowed_user_ids)
        reject = Wizard.with_user(self.manager).create(dict(values, operation='reject', user_id=self.manager.id))
        self.assertIn(self.manager, reject.allowed_user_ids)
        self.assertIn(self.engineer, reject.allowed_user_ids)
        self.assertNotIn(self.admin, reject.allowed_user_ids)
        delegate = Wizard.with_user(self.manager).create(dict(values, operation='delegate', user_id=self.engineer.id))
        self.assertNotIn(self.manager, delegate.allowed_user_ids)

    def test_part_approval_does_not_bypass_its_document_review(self):
        doc = self.document(reviewer=self.manager)
        part = self.env['product.product'].with_user(self.engineer).create({
            'name': 'PART-WITH-DOC', 'engineering_code': 'PART-WITH-DOC',
            'tdb_reviewer_id': self.admin.id, 'linkeddocuments': [Command.link(doc.id)],
        })
        part.action_confirm()
        with self.assertRaises(AccessError), self.cr.savepoint():
            part.with_user(self.admin).action_tdb_approve()
        self.assertEqual(part.engineering_state, 'confirmed')
        doc.with_user(self.manager).action_tdb_approve()
        doc.with_user(self.manager)._tdb_forward(self.admin)
        part.with_user(self.admin).action_tdb_approve()
        self.assertEqual(part.engineering_state, 'released')
        self.assertEqual(doc.engineering_state, 'released')

    def test_new_revision_does_not_copy_decisions_or_tasks(self):
        doc = self.document(reviewer=self.admin)
        doc.action_confirm()
        doc.with_user(self.admin).action_tdb_approve()
        revision = doc.copy({'engineering_code': doc.engineering_code, 'engineering_revision': 1})
        self.assertFalse(revision.tdb_review_history)
        self.assertFalse(revision.tdb_task_activity_id)
        self.assertFalse(revision.tdb_redo_chain)
        self.assertEqual(revision.tdb_reviewer_id, self.admin)
        self.assertEqual(doc.engineering_state, 'undermodify')

    def test_cad_redo_save_cannot_reset_task_metadata(self):
        Product = self.env['product.product'].with_user(self.engineer)
        part = Product.create({'name': 'CAD-REDO', 'engineering_code': 'CAD-REDO',
                               'tdb_reviewer_id': self.manager.id})
        part.action_confirm()
        part.product_tmpl_id.with_user(self.manager)._tdb_reject('Fix the part', self.engineer)
        activity = part.tdb_task_activity_id
        Product.createFromProps({
            'engineering_code': part.engineering_code, 'engineering_revision': part.engineering_revision,
            'name': 'CAD corrected part', 'engineering_state': 'released',
            'tdb_review_stage': 'released', 'tdb_redo_chain': [], 'tdb_task_activity_id': False,
            'tdb_reviewer_id': False,
        })
        self.assertEqual(part.name, 'CAD corrected part')
        self.assertEqual(part.engineering_state, 'draft')
        self.assertEqual(part.tdb_review_stage, 'redo')
        self.assertEqual(part.tdb_task_activity_id, activity)

    def test_checked_out_work_cannot_be_delegated_or_returned(self):
        doc = self.document(reviewer=self.admin)
        doc.action_confirm()
        self.reject(doc, self.admin, self.manager)
        self.env['plm.checkout'].create({
            'documentid': doc.id, 'userid': self.manager.id, 'hostname': 'redo-workstation',
        })
        doc.invalidate_recordset()
        for action in (
            lambda: doc.with_user(self.manager)._tdb_delegate_redo(self.engineer, 'Please finish'),
            lambda: doc.with_user(self.manager).action_tdb_complete_redo(),
        ):
            with self.assertRaises(UserError), self.cr.savepoint():
                action()
        self.assertEqual(doc.tdb_redo_user_id, self.manager)

    def test_forwarding_rechecks_current_level(self):
        doc = self.document()
        doc.action_confirm()
        doc.with_user(self.manager).action_tdb_approve()
        self.manager.write({'group_ids': [Command.set([
            self.env.ref('base.group_user').id,
            self.env.ref('plm.group_plm_integration_user').id,
        ])]})
        with self.assertRaises(AccessError), self.cr.savepoint():
            doc.with_user(self.manager)._tdb_forward(self.admin)
        self.assertEqual(doc.tdb_review_stage, 'forward')

    def test_return_notifies_assigner_without_new_task_or_email(self):
        self.manager.write({'email': 'manager@example.test', 'notification_type': 'email'})
        doc = self.document(reviewer=self.admin)
        doc.action_confirm()
        self.reject(doc, self.admin, self.manager)
        parent = doc.tdb_task_activity_id
        doc.with_user(self.manager)._tdb_delegate_redo(self.engineer, 'Correct dimensions')
        doc.with_user(self.engineer).action_tdb_complete_redo()
        self.assertEqual(doc.tdb_task_activity_id, parent)
        messages = self.env['mail.message'].search([
            ('model', '=', doc._name), ('res_id', '=', doc.id),
            ('subject', 'like', 'Verify returned work')])
        self.assertEqual(len(messages), 1)
        notifications = self.env['mail.notification'].search([('mail_message_id', '=', messages.id)])
        self.assertEqual(notifications.res_partner_id, self.manager.partner_id)
        self.assertEqual(notifications.notification_type, 'inbox')
        self.assertFalse(self.env['mail.mail'].search([('mail_message_id', '=', messages.id)]))
        with self.assertRaises((AccessError, UserError)), self.cr.savepoint():
            doc.with_user(self.engineer).action_tdb_complete_redo()

    def test_recover_keeps_review_and_preserves_history(self):
        doc = self.document()
        doc.action_confirm()
        old_task = doc.tdb_task_activity_id
        old_history = doc.tdb_review_history
        submitted = doc.tdb_submitted_by
        self.cr.execute("UPDATE ir_attachment SET tdb_review_stage='manager', tdb_task_activity_id=NULL WHERE id=%s", [doc.id])
        doc.invalidate_recordset()
        self.assertTrue(doc.tdb_needs_recovery)
        with self.assertRaises(AccessError), self.cr.savepoint():
            doc.with_user(self.manager)._tdb_recover_review(self.engineer, self.manager, 'Restore task')
        doc.with_user(self.admin)._tdb_recover_review(self.engineer, self.manager, 'Restore task')
        self.assertEqual(doc.engineering_state, 'confirmed')
        self.assertEqual(doc.tdb_review_stage, 'review')
        self.assertEqual(doc.tdb_submitted_by, submitted)
        self.assertEqual(doc.tdb_reviewer_id, self.manager)
        self.assertEqual(doc.tdb_task_activity_id.user_id, self.manager)
        self.assertNotEqual(doc.tdb_task_activity_id, old_task)
        self.assertEqual(doc.tdb_review_history[:-1], old_history)
        self.assertFalse(doc.tdb_needs_recovery)
        self.assertFalse(old_task.exists().filtered(lambda activity: not activity.date_done))
        with self.assertRaises((AccessError, UserError)), self.cr.savepoint():
            doc.with_user(self.admin).action_tdb_recover_review()

    def test_bulk_recovery_and_search(self):
        docs = self.document('RECOVERY-A') | self.document('RECOVERY-B')
        self.cr.execute("UPDATE ir_attachment SET engineering_state='confirmed', tdb_review_stage='manager' WHERE id IN %s", [tuple(docs.ids)])
        docs.invalidate_recordset()
        valid = self.document('VALID-REVIEW')
        valid.action_confirm()
        matches = self.env['ir.attachment'].with_user(self.admin).search([('tdb_needs_recovery', '=', True)])
        self.assertTrue(docs <= matches)
        self.assertNotIn(valid, matches)
        action = docs.with_user(self.admin).action_tdb_recover_review()
        wizard = self.env['tdb.plm.task.wizard'].with_user(self.admin).with_context(action['context']).create({
            'requester_id': self.engineer.id, 'user_id': self.manager.id, 'reason': 'Restore legacy reviews'})
        wizard.action_apply()
        self.assertEqual(docs.mapped('tdb_review_stage'), ['review', 'review'])
        self.assertEqual(len(docs.mapped('tdb_task_activity_id')), 2)
        self.assertFalse(any(docs.mapped('tdb_needs_recovery')))

    def test_part_recovery_filter_and_role_validation(self):
        part = self.env['product.product'].with_user(self.engineer).create({
            'name': 'RECOVERY-PART', 'engineering_code': 'RECOVERY-PART'})
        self.cr.execute("UPDATE product_template SET engineering_state='confirmed', tdb_review_stage='admin' WHERE id=%s", [part.product_tmpl_id.id])
        part.product_tmpl_id.invalidate_recordset()
        part.invalidate_recordset()
        matches = self.env['product.product'].with_user(self.admin).search([('tdb_needs_recovery', '=', True)])
        self.assertIn(part, matches)
        target = part.product_tmpl_id.with_user(self.admin)
        with self.assertRaises(UserError), self.cr.savepoint():
            target._tdb_recover_review(self.manager, self.manager2, 'Invalid level')
        with self.assertRaises(UserError), self.cr.savepoint():
            target._tdb_recover_review(self.engineer, self.manager, ' ')
        action = part.with_user(self.admin).action_tdb_recover_review()
        self.assertEqual(action['context']['default_target_model'], 'product.template')
        target._tdb_recover_review(self.manager, self.admin, 'Restore review')
        self.assertEqual(part.engineering_state, 'confirmed')
        self.assertEqual(part.tdb_reviewer_id, self.admin)

    def test_plm_activities_include_review_redo_and_return(self):
        action = self.env.ref('activity_validation.plm_action_view_activities')
        domain = safe_eval(action.domain)
        doc = self.document(reviewer=self.admin)
        doc.action_confirm()
        Activity = self.env['mail.activity'].with_user(self.admin)
        self.assertIn(doc.tdb_task_activity_id, Activity.search(domain))
        self.reject(doc, self.admin, self.manager)
        parent = doc.tdb_task_activity_id
        self.assertIn(parent, Activity.search(domain))
        doc.with_user(self.manager)._tdb_delegate_redo(self.engineer, 'Fix this')
        doc.with_user(self.engineer).action_tdb_complete_redo()
        self.assertEqual(doc.tdb_task_activity_id, parent)
        self.assertIn('Verify returned work', parent.summary)
        self.assertIn(self.engineer.display_name, parent.summary)
        self.assertIn(doc.name, parent.summary)
        self.assertIn(parent, Activity.with_user(self.manager).search(domain))
        opened = parent.with_user(self.manager).action_tdb_open_workflow()
        self.assertEqual(opened['res_id'], doc.id)
        self.assertEqual(opened['res_model'], 'ir.attachment')

    def test_clock_merges_plm_models_and_keeps_other_tasks(self):
        doc = self.document(reviewer=self.manager)
        doc.action_confirm()
        part = self.env['product.product'].with_user(self.engineer).create({
            'name': 'CLOCK-PART', 'engineering_code': 'CLOCK-PART',
            'tdb_reviewer_id': self.manager.id})
        part.action_confirm()
        # An unrelated activity on the same document must retain its normal group.
        ordinary = doc.activity_schedule('mail.mail_activity_data_todo', user_id=self.manager.id)
        tasks = doc.tdb_task_activity_id | part.tdb_task_activity_id | ordinary
        groups = self.env['res.users'].with_user(self.manager)._divide_activities_in_groups(tasks)
        plm_group = next(group for group in groups if group['name'] == 'PLM Activities')
        self.assertEqual(set(plm_group['activity_ids']), set((tasks - ordinary).ids))
        self.assertEqual(plm_group['today_count'], 2)
        self.assertEqual(plm_group['tdb_plm_action_id'], self.env.ref('activity_validation.plm_action_view_activities').id)
        normal = next(group for group in groups if group['model'] == 'ir.attachment')
        self.assertEqual(normal['activity_ids'], ordinary.ids)
        self.assertNotIn('product.template', [group['model'] for group in groups])
        other_groups = self.env['res.users'].with_user(self.engineer2)._get_activity_groups()
        self.assertFalse(any(group.get('tdb_plm_action_id') for group in other_groups))
