from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from lxml import etree

from .test_review_workflow import TestReviewWorkflow
from .test_cad_users import rpc_method


@tagged('post_install', '-at_install', 'tdb_plm_review')
class TestSelectiveReview(TransactionCase):
    def test_ecr_can_be_scheduled_from_draft_part_chatter(self):
        part, _doc = self.part('ECR-DRAFT')
        template = part.product_tmpl_id
        request_type = self.env.ref('activity_validation.mail_activity_change_request')
        scheduler = self.env['mail.activity.schedule'].with_user(self.engineer).with_context(
            active_model='product.template', active_ids=template.ids,
        ).create({
            'res_model': 'product.template', 'res_ids': str(template.ids),
            'activity_type_id': request_type.id, 'activity_user_id': self.engineer.id,
        })
        self.assertIn(request_type, scheduler.activity_type_available_ids)
        request = scheduler._action_schedule_activities()
        self.assertEqual((request.res_model, request.res_id), ('product.product', part.id))
        self.assertEqual(request.plm_state, 'draft')
        self.assertEqual(part.engineering_state, 'draft')
        for record in (scheduler, request):
            action = record.action_open_releted_ent()
            self.assertEqual((action['res_model'], action['res_id']), ('product.product', part.id))
            self.assertEqual(action['target'], 'current')
            self.assertTrue(action['context']['odooPLM'])
            self.assertEqual(action['views'], [(self.env.ref('tdb_custom_plm.engineering_part_form').id, 'form')])

    def test_ecr_progress_requires_and_preserves_child_assignment(self):
        validation_user = self.env.ref('activity_validation.group_force_activity_validation_user')
        self.manager.group_ids = [Command.link(validation_user.id)]
        part, _doc = self.part('ECR-CHILD-ASSIGNMENT')
        request_type = self.env.ref('activity_validation.mail_activity_change_request')
        scheduler = self.env['mail.activity.schedule'].with_user(self.manager).create({
            'res_model': 'product.product', 'res_ids': str(part.ids),
            'activity_type_id': request_type.id, 'activity_user_id': self.manager.id,
            'children_ids': [Command.create({'name': 'Check assembly fit'})],
        })
        with self.assertRaisesRegex(UserError, 'planned assignee'), self.cr.savepoint():
            scheduler.action_in_progress()
        request = scheduler._action_schedule_activities()
        self.assertFalse(request.children_ids.activity_user_id)
        with self.assertRaisesRegex(UserError, 'planned assignee'), self.cr.savepoint():
            request.action_in_progress()
        request.children_ids.user_id = self.engineer
        request.action_in_progress()
        child = request.children_ids.mail_children_activity_id
        self.assertEqual(child.user_id, self.engineer)
        self.assertEqual(child.summary, 'Check assembly fit')

    def test_eco_can_release_only_the_replacement_document(self):
        part, original_doc = self.part('ECO-CAD-ONLY', self.admin)
        part.action_confirm()
        part.with_user(self.admin).action_tdb_approve()
        eco = part.with_user(self.manager).activity_schedule(
            'activity_validation.mail_activity_change_request', user_id=self.manager.id)
        eco.action_in_progress()
        eco.action_to_eco()
        doc_id, revision = original_doc.NewRevision(original_doc.id)
        replacement = self.env['ir.attachment'].browse(doc_id)
        self.assertEqual(revision, 1)
        self.env['tdb.plm.eco.revision'].with_user(self.manager).create({
            'eco_id': eco.id, 'kind': 'document', 'original_document_id': original_doc.id,
            'replacement_document_id': replacement.id,
        })
        with self.assertRaisesRegex(UserError, 'ECO-CAD-ONLY.*rev 1'), self.cr.savepoint():
            eco.action_to_done()
        replacement.with_user(self.engineer).tdb_reviewer_id = self.admin
        replacement.with_user(self.engineer).action_confirm()
        replacement.with_user(self.admin).action_tdb_approve()
        eco.action_to_done()
        self.assertFalse(eco.active)
        self.assertEqual(eco.plm_state, 'done')
        self.assertEqual(part.engineering_revision, 0)
        self.assertEqual(part.engineering_state, 'released')

    def test_eco_waits_for_exact_part_and_cad_replacements(self):
        original_part, original_doc = self.part('ECO-SCOPE', self.admin)
        original_part.action_confirm()
        original_part.with_user(self.admin).action_tdb_approve()
        part_id, _ = original_part.NewRevision()
        doc_id, _ = original_doc.NewRevision(original_doc.id)
        part = self.env['product.product'].browse(part_id)
        doc = self.env['ir.attachment'].browse(doc_id)

        eco = original_part.with_user(self.manager).activity_schedule(
            'activity_validation.mail_activity_change_request', user_id=self.manager.id)
        eco.action_in_progress()
        eco.action_to_eco()
        view = eco.get_view(self.env.ref('mail.mail_activity_view_form_popup').id, 'form')
        create_button, = etree.fromstring(view['arch']).xpath("//field[@name='eco_child_ids']/list/control/create")
        self.assertEqual(create_button.get('string'), 'Add a line')
        with self.assertRaisesRegex(UserError, 'Add the affected revisions'), self.cr.savepoint():
            eco.action_to_done()
        Line = self.env['tdb.plm.eco.revision'].with_user(self.manager)
        with self.assertRaises(ValidationError), self.cr.savepoint():
            Line.create({'eco_id': eco.id, 'original_part_id': original_part.id,
                         'replacement_part_id': original_part.id})
        lines = Line.create([
            {'eco_id': eco.id, 'original_part_id': original_part.id, 'replacement_part_id': part.id},
            {'eco_id': eco.id, 'kind': 'document', 'original_document_id': original_doc.id},
        ])
        with self.assertRaisesRegex(UserError, 'select a replacement revision'), self.cr.savepoint():
            eco.action_to_done()
        lines.filtered(lambda line: line.kind == 'document').replacement_document_id = doc
        part.with_user(self.admin).action_tdb_submit_review()
        doc.with_user(self.engineer).write({'tdb_reviewer_id': self.admin.id})
        doc.with_user(self.engineer).action_confirm()
        self.assertEqual(part.engineering_state, 'released')
        self.assertEqual(doc.engineering_state, 'confirmed')
        with self.assertRaisesRegex(UserError, 'ECO-SCOPE.*rev 1'), self.cr.savepoint():
            eco.action_to_done()
        for close in (eco.action_done, lambda: eco.write({'active': False})):
            with self.assertRaisesRegex(UserError, 'use its Done button'), self.cr.savepoint():
                close()
        self.assertTrue(eco.active)
        self.assertEqual(eco.plm_state, 'eco')

        eco.write({'eco_child_ids': [Command.create({
            'summary': 'Verify CAD replacement', 'user_id': self.manager.id, 'res_id': False,
        })]})
        child = eco.eco_child_ids
        self.assertEqual((child.res_model, child.res_id), ('product.product', original_part.id))
        self.assertEqual(child.activity_type_id, self.env.ref('plm.mail_activity_plm_activity'))
        self.assertTrue(child.tdb_can_finish_eco_activity)
        self.assertFalse(child.with_user(self.engineer).tdb_can_finish_eco_activity)
        with self.assertRaisesRegex(AccessError, 'Only the assigned user'), self.cr.savepoint():
            child.with_user(self.engineer).action_to_done()
        with self.assertRaisesRegex(UserError, 'use its Done button'), self.cr.savepoint():
            child.action_done()
        doc.with_user(self.admin).action_tdb_approve()
        with self.assertRaisesRegex(UserError, 'pending ECO activities'), self.cr.savepoint():
            eco.action_to_done()
        child.action_to_done()
        self.assertTrue(eco.active)
        self.assertFalse(child.active)
        rows = eco.web_read({'eco_child_ids': {'fields': {'active': {}, 'plm_state': {}}}})[0]['eco_child_ids']
        self.assertIn({'id': child.id, 'active': False, 'plm_state': 'done'}, rows)
        with self.assertRaisesRegex(UserError, 'read-only'), self.cr.savepoint():
            child.write({'summary': 'Changed after completion'})
        with self.assertRaisesRegex(UserError, 'read-only'), self.cr.savepoint():
            child.unlink()
        eco.action_to_done()
        self.assertFalse(eco.active)
        self.assertEqual(eco.plm_state, 'done')
        self.assertEqual(set(eco.tdb_eco_revision_ids.ids), set(lines.ids))
        with self.assertRaises(UserError), self.cr.savepoint():
            lines.write({'release_required': False})

    def test_cancel_eco_without_replacement_does_not_require_release(self):
        part, _ = self.part('ECO-CANCEL', self.admin)
        eco = part.with_user(self.manager).activity_schedule(
            'activity_validation.mail_activity_change_request', user_id=self.manager.id)
        eco.action_in_progress()
        eco.action_to_eco()
        eco.action_to_cancel()
        self.assertFalse(eco.active)
        self.assertEqual(eco.plm_state, 'cancel')

    def test_assignee_cannot_cancel_a_plm_activity_created_by_someone_else(self):
        part, _doc = self.part('PLM-CANCEL-PERMISSION')
        for activity_type in ('activity_validation.mail_activity_change_request', 'plm.mail_activity_plm_activity'):
            task = part.with_user(self.admin).activity_schedule(activity_type, user_id=self.engineer.id)
            assigned = task.with_user(self.engineer)
            self.assertTrue(task.tdb_can_cancel_plm_activity)
            self.assertFalse(assigned.tdb_can_cancel_plm_activity)
            with self.assertRaises(AccessError), self.cr.savepoint():
                assigned.action_to_cancel()
            with self.assertRaises(AccessError), self.cr.savepoint():
                assigned.write({'plm_state': 'cancel'})
            self.assertTrue(task.active)
            task.action_to_cancel()
            self.assertFalse(task.active)

    def test_ecr_coordinator_manages_order_and_worker_only_handles_own_task(self):
        self.manager.group_ids = [Command.link(
            self.env.ref('activity_validation.group_force_activity_validation_user').id)]
        part, _doc = self.part('ECR-ROLE-PERMISSIONS')
        root = part.with_user(self.admin).activity_schedule(
            'activity_validation.mail_activity_change_request', user_id=self.manager.id)
        viewer = root.with_user(self.engineer)
        self.assertFalse(viewer.tdb_can_edit_plm_activity)
        for action in (viewer.action_in_progress, viewer.action_to_eco, viewer.action_to_exception,
                       lambda: viewer.write({'user_id': self.engineer.id})):
            with self.assertRaises(AccessError), self.cr.savepoint():
                action()
        coordinator = root.with_user(self.manager)
        self.assertTrue(coordinator.tdb_can_manage_plm_activity)
        coordinator.write({'children_ids': [Command.create({
            'name': 'Check assembly fit', 'user_id': self.engineer.id,
        })]})
        coordinator.action_in_progress()
        child = coordinator.children_ids.mail_children_activity_id.with_user(self.engineer)
        self.assertTrue(child.tdb_can_edit_plm_activity)
        self.assertTrue(child.tdb_can_start_plm_activity)
        self.assertFalse(child.tdb_can_manage_plm_activity)
        child.action_in_progress()
        child.note = 'Measured 0.5 mm deviation'
        for action in (child.action_to_eco, child.action_to_exception, child.action_to_cancel,
                       lambda: child.write({'date_deadline': '2099-12-31'})):
            with self.assertRaises(AccessError), self.cr.savepoint():
                action()
        child.action_to_done()
        self.assertEqual(child.plm_state, 'done')
        self.assertEqual(coordinator.plm_state, 'in_progress')
        coordinator.action_to_eco()
        self.assertTrue(coordinator.tdb_is_eco_order)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['ir.config_parameter'].sudo().set_str('ir_attachment.location', 'db')
        def user(login, group):
            return cls.env['res.users'].with_context(no_reset_password=True).create({
                'name': login, 'login': login,
                'group_ids': [Command.set([cls.env.ref('base.group_user').id, cls.env.ref(group).id])],
            })
        cls.engineer = user('selective_lv1', 'plm.group_plm_integration_user')
        cls.manager = user('selective_lv2', 'tdb_custom_plm.group_review_manager')
        cls.admin = user('selective_lv3', 'tdb_custom_plm.group_review_admin')

    document = TestReviewWorkflow.document

    def part(self, code, reviewer=None):
        doc = self.document(code + '.SLDPRT', reviewer=reviewer)
        part = self.env['product.product'].with_user(self.engineer).create({
            'name': code, 'engineering_code': code,
            'tdb_reviewer_id': (reviewer or self.manager).id,
            'linkeddocuments': [Command.link(doc.id)],
        })
        return part, doc

    def bom(self, parent, children):
        return self.env['mrp.bom'].with_user(self.engineer).create({
            'product_id': parent.id, 'product_tmpl_id': parent.product_tmpl_id.id,
            'bom_line_ids': [Command.create({'product_id': child.id, 'product_qty': 1})
                             for child in children],
        })

    def tree(self, reviewer=None):
        parent, doc = self.part('ASSEMBLY', reviewer)
        a, adoc = self.part('CHILD-A', reviewer)
        b, bdoc = self.part('CHILD-B', reviewer)
        self.bom(parent, a | b)
        parent.action_confirm()
        return parent, doc, a, adoc, b, bdoc

    def reject(self, record, actor=None):
        if record._name == 'product.product':
            record = record.product_tmpl_id
        return self.env['tdb.plm.task.wizard'].with_user(actor or self.manager).create({
            'target_model': record._name, 'target_id': record.id, 'operation': 'reject',
            'user_id': self.engineer.id, 'reason': 'Correct the failed geometry',
        }).action_apply()

    def test_recursive_reject_returns_assembly_children_and_files(self):
        parent, doc, a, adoc, b, bdoc = self.tree()
        self.reject(parent)
        for record in (parent, doc, a, adoc, b, bdoc):
            self.assertEqual(record.engineering_state, 'draft')
            self.assertEqual(record.tdb_review_stage, 'redo')
            self.assertEqual(record.tdb_task_activity_id.user_id, self.engineer)

    def test_cad_file_reject_does_not_change_parts_or_bom_children(self):
        parent, doc, a, adoc, b, bdoc = self.tree()
        self.reject(doc)
        self.assertEqual(doc.engineering_state, 'draft')
        for record in (parent, a, adoc, b, bdoc):
            self.assertEqual(record.engineering_state, 'confirmed')

    def test_wrong_child_reviewer_prevents_recursive_rejection_atomically(self):
        parent, doc, a, adoc, b, bdoc = self.tree()
        b.product_tmpl_id._tdb_internal_write({'tdb_reviewer_id': self.admin.id})
        with self.assertRaises((AccessError, UserError)), self.cr.savepoint():
            self.reject(parent)
        self.assertEqual(parent.engineering_state, 'confirmed')
        self.assertEqual(doc.engineering_state, 'confirmed')

    def test_shared_child_in_other_review_is_not_unlocked(self):
        parent, doc, a, adoc, b, bdoc = self.tree()
        other, odoc = self.part('OTHER-ASSEMBLY')
        self.bom(other, b)
        # Model overlapping reviews created before submission protection existed.
        other.product_tmpl_id._tdb_start_review(self.manager)
        odoc._tdb_start_review(self.manager)
        with self.assertRaisesRegex(UserError, 'Shared Part'), self.cr.savepoint():
            self.reject(parent)
        self.assertEqual(b.engineering_state, 'confirmed')
        self.assertEqual(parent.engineering_state, 'confirmed')
        self.assertEqual(other.engineering_state, 'confirmed')
        self.assertEqual(b.engineering_state, 'confirmed')

    def test_release_skips_draft_child_and_preserves_its_redo(self):
        parent, doc, a, adoc, b, bdoc = self.tree(self.admin)
        self.reject(b, actor=self.admin)
        task = b.tdb_task_activity_id
        parent.with_user(self.admin).action_tdb_approve()
        for record in (parent, doc, a, adoc):
            self.assertEqual(record.engineering_state, 'released')
        self.assertEqual(b.engineering_state, 'draft')
        self.assertEqual(bdoc.engineering_state, 'draft')
        self.assertEqual(b.tdb_task_activity_id, task)

    def test_ready_graph_releases_children_files_then_parent(self):
        parent, doc, a, adoc, b, bdoc = self.tree(self.admin)
        parent.with_user(self.admin).action_tdb_approve()
        for record in (parent, doc, a, adoc, b, bdoc):
            self.assertEqual(record.engineering_state, 'released')
            self.assertFalse(record.tdb_task_activity_id)

    def test_direct_parent_release_has_no_child_readiness_requirement(self):
        parent, doc, a, adoc, b, bdoc = self.tree(self.admin)
        parent.product_tmpl_id.with_user(self.admin).move_to_state('released')
        self.assertEqual(parent.engineering_state, 'released')
        self.assertEqual(b.engineering_state, 'confirmed')
        self.assertEqual(doc.engineering_state, 'confirmed')

    def test_recursive_release_of_reviewed_root_skips_draft_documents(self):
        root = self.document('ROOT.SLDASM', reviewer=self.admin)
        child = self.document('CHILD.SLDPRT', reviewer=self.admin)
        self.env['ir.attachment.relation'].create({
            'parent_id': root.id, 'child_id': child.id, 'link_kind': 'HiTree'})
        root.action_confirm()
        root.with_user(self.admin).action_release()
        self.assertEqual(child.engineering_state, 'draft')
        self.assertEqual(root.engineering_state, 'released')

    def test_cycle_is_reported_without_releasing_anything(self):
        parent, doc = self.part('CYCLE-ROOT')
        child, cdoc = self.part('CYCLE-CHILD')
        self.bom(parent, child)
        # Use file relations for a cycle; manufacturing normally disallows BOM cycles.
        self.env['ir.attachment.relation'].create([
            {'parent_id': doc.id, 'child_id': cdoc.id, 'link_kind': 'HiTree'},
            {'parent_id': cdoc.id, 'child_id': doc.id, 'link_kind': 'HiTree'},
        ])
        with self.assertRaisesRegex(UserError, 'Circular'), self.cr.savepoint():
            parent.with_user(self.admin).action_release()
        self.assertEqual(parent.engineering_state, 'draft')

    def test_release_does_not_bypass_another_reviewer(self):
        parent, doc, a, adoc, b, bdoc = self.tree(self.admin)
        b.product_tmpl_id._tdb_internal_write({'tdb_reviewer_id': self.manager.id})
        with self.assertRaises((AccessError, UserError)), self.cr.savepoint():
            parent.with_user(self.admin).action_release()
        for record in (parent, doc, a, adoc, b, bdoc):
            self.assertEqual(record.engineering_state, 'confirmed')

    def test_released_child_is_preserved_during_recursive_rejection(self):
        parent, doc, a, adoc, b, bdoc = self.tree(self.admin)
        a.with_user(self.admin).action_tdb_approve()
        self.reject(parent, actor=self.admin)
        self.assertEqual(a.engineering_state, 'released')
        self.assertEqual(adoc.engineering_state, 'released')
        self.assertEqual(b.engineering_state, 'draft')

    def test_rejection_form_has_no_child_selectors_or_scope_preview(self):
        parent, doc, a, adoc, b, bdoc = self.tree()
        view = self.env['tdb.plm.task.wizard'].with_user(self.manager).get_view(
            view_id=self.env.ref('tdb_custom_plm.view_task_wizard').id, view_type='form')
        arch = etree.fromstring(view['arch'])
        for field in ('failed_part_ids', 'failed_document_ids', 'rejection_scope'):
            self.assertFalse(arch.xpath("//field[@name='%s']" % field))
        self.assertTrue(arch.xpath('//form/div[contains(@class, "alert-info")]'))

    def test_legacy_dialog_cannot_unlock_shared_part_in_other_reviews(self):
        parent, doc, a, adoc, b, bdoc = self.tree()
        other, odoc = self.part('OTHER-ASSEMBLY')
        self.bom(other, b)
        other.product_tmpl_id._tdb_start_review(self.manager)
        odoc._tdb_start_review(self.manager)
        wizard = self.env['tdb.plm.reject.wizard'].with_user(self.manager).create({
            'target_model': 'product.template', 'target_id': b.product_tmpl_id.id,
            'reason': 'Change shared B', 'assignee_id': self.engineer.id,
        })
        with self.assertRaisesRegex(UserError, 'Shared Part'), self.cr.savepoint():
            wizard.action_reject()
        self.assertEqual(b.engineering_state, 'confirmed')

    def test_leaf_part_approval_does_not_require_its_file_already_released(self):
        part, doc = self.part('LEAF', self.admin)
        part.action_confirm()
        part.product_tmpl_id.with_user(self.admin).action_tdb_approve()
        self.assertEqual(part.engineering_state, 'released')
        self.assertEqual(doc.engineering_state, 'released')

    def test_leaf_part_self_redo_can_finish_before_linked_file_redo(self):
        part, doc = self.part('LEAF-REDO', self.admin)
        part.action_confirm()
        self.env['tdb.plm.task.wizard'].with_user(self.admin).create({
            'target_model': 'product.template', 'target_id': part.product_tmpl_id.id,
            'operation': 'reject', 'user_id': self.admin.id, 'reason': 'Correct leaf',
        }).action_apply()
        part.with_user(self.admin).action_tdb_complete_redo()
        self.assertEqual(part.engineering_state, 'released')
        self.assertEqual(doc.engineering_state, 'draft')
        self.assertTrue(doc.tdb_redo_chain)
        with self.assertRaises((AccessError, UserError)), self.cr.savepoint():
            doc.with_user(self.admin).action_release()

    def test_leaf_file_approval_recursively_releases_ready_drawing(self):
        doc = self.document('LEAF.SLDPRT', reviewer=self.admin)
        drawing = self.document('LEAF.SLDDRW', reviewer=self.admin)
        self.env['ir.attachment.relation'].create({
            'parent_id': doc.id, 'child_id': drawing.id, 'link_kind': 'LyTree'})
        doc.action_confirm()
        drawing.action_confirm()
        doc.with_user(self.admin).action_tdb_approve()
        self.assertEqual(doc.engineering_state, 'released')
        self.assertEqual(drawing.engineering_state, 'released')

    def test_assembly_template_approval_releases_entire_ready_graph(self):
        parent, doc, a, adoc, b, bdoc = self.tree(self.admin)
        parent.product_tmpl_id.with_user(self.admin).action_tdb_approve()
        for record in (parent, doc, a, adoc, b, bdoc):
            self.assertEqual(record.engineering_state, 'released')

    def test_part_release_leaves_reverted_document_and_its_redo_unchanged(self):
        part, doc = self.part('LEAF-BLOCKED', self.admin)
        part.action_confirm()
        self.reject(doc, actor=self.admin)
        task = doc.tdb_task_activity_id
        part.product_tmpl_id.with_user(self.admin).action_tdb_approve()
        self.assertEqual(part.engineering_state, 'released')
        self.assertEqual(doc.engineering_state, 'draft')
        self.assertEqual(doc.tdb_task_activity_id, task)
        self.assertTrue(doc.tdb_redo_chain)
        with self.assertRaises(UserError), self.cr.savepoint():
            doc.with_user(self.admin).action_release()

    def test_browser_part_activity_opens_full_form_with_chatter_and_workflow(self):
        part, doc = self.part('BROWSER-FORM')
        part.action_confirm()
        task = part.tdb_task_activity_id.with_user(self.manager).with_context(odooPLM=True)
        action = task.action_tdb_open_workflow()
        self.assertEqual(action['res_model'], 'product.product')
        self.assertEqual(action['res_id'], part.id)
        self.assertEqual(action['views'], [(self.env.ref('tdb_custom_plm.engineering_part_form').id, 'form')])
        view = part.with_user(self.manager).get_view(view_id=action['views'][0][0], view_type='form')
        arch = etree.fromstring(view['arch'])
        self.assertTrue(arch.xpath('//chatter'))
        self.assertTrue(arch.xpath("//button[@name='action_tdb_reject']"))
        self.assertTrue(arch.xpath("//field[@name='tdb_review_stage']"))

    def test_cad_part_activity_retains_cad_form(self):
        part, doc = self.part('CAD-FORM')
        part.action_confirm()
        with rpc_method('mail.activity.action_tdb_open_workflow'):
            action = part.tdb_task_activity_id.with_user(self.manager).action_tdb_open_workflow()
        self.assertEqual(action['views'], [(self.env.ref('plm.plm_component_base_form').id, 'form')])
        self.assertEqual(action['res_id'], part.id)

    def test_lv2_approve_submit_assigns_lv3_in_one_confirmation(self):
        part, doc = self.part('APPROVE-SUBMIT')
        part.action_confirm()
        for record in (part, doc):
            action = record.with_user(self.manager).action_tdb_approve_submit()
            self.assertEqual(record.tdb_reviewer_id, self.manager)
            wizard = self.env['tdb.plm.task.wizard'].with_user(self.manager).with_context(action['context']).create({
                'user_id': self.admin.id,
            })
            self.assertIn(self.admin, wizard.allowed_user_ids)
            self.assertNotIn(self.manager, wizard.allowed_user_ids)
            wizard.action_apply()
            self.assertEqual(record.engineering_state, 'confirmed')
            self.assertEqual(record.tdb_review_stage, 'review')
            self.assertEqual(record.tdb_requester_id, self.manager)
            self.assertEqual(record.tdb_reviewer_id, self.admin)
            self.assertEqual(record.tdb_task_activity_id.user_id, self.admin)
            decisions = [event['decision'] for event in record.tdb_review_history[-1]['events']]
            self.assertIn('level_2_approved', decisions)
            with self.assertRaises((AccessError, UserError)), self.cr.savepoint():
                wizard.action_apply()

    def test_lv2_approve_submit_validates_before_recording_approval(self):
        doc = self.document('INVALID-FINAL-REVIEWER')
        doc.action_confirm()
        task = doc.tdb_task_activity_id
        history = doc.tdb_review_history
        wizard = self.env['tdb.plm.task.wizard'].with_user(self.manager).create({
            'target_model': doc._name, 'target_id': doc.id,
            'operation': 'approve_forward', 'user_id': self.engineer.id,
        })
        with self.assertRaises(UserError), self.cr.savepoint():
            wizard.action_apply()
        self.assertEqual(doc.tdb_reviewer_id, self.manager)
        self.assertEqual(doc.tdb_task_activity_id, task)
        self.assertEqual(doc.tdb_review_history, history)
        with self.assertRaises((AccessError, UserError)), self.cr.savepoint():
            doc.with_user(self.admin).action_tdb_approve_submit()

    def test_lv1_submission_chooser_leaves_draft_until_confirmed(self):
        part, doc = self.part('SUBMIT-CHOOSER')
        for record in (part, doc):
            action = record.with_user(self.engineer).action_tdb_submit_review()
            self.assertEqual(action['context']['default_user_id'], self.manager.id)
            self.assertEqual(record.engineering_state, 'draft')
            self.assertFalse(record.tdb_task_activity_id)
            self.assertFalse(record.tdb_review_history)
        action = part.with_user(self.engineer).action_tdb_submit_review()
        wizard = self.env['tdb.plm.task.wizard'].with_user(self.engineer).with_context(action['context']).create({})
        self.assertIn(self.manager, wizard.allowed_user_ids)
        self.assertIn(self.admin, wizard.allowed_user_ids)
        self.assertNotIn(self.engineer, wizard.allowed_user_ids)
        wizard.action_apply()
        self.assertEqual(part.engineering_state, 'confirmed')
        self.assertEqual(doc.engineering_state, 'confirmed')
        self.assertEqual(part.tdb_reviewer_id, self.manager)
        self.assertEqual(part.tdb_task_activity_id.user_id, self.manager)
        with self.assertRaises(UserError), self.cr.savepoint():
            wizard.action_apply()

    def test_document_submission_chooser_can_select_lv3(self):
        doc = self.document('CHOOSE-LV3')
        doc.tdb_reviewer_id = False
        action = doc.action_tdb_submit_review()
        self.assertNotIn('default_user_id', action['context'])
        wizard = self.env['tdb.plm.task.wizard'].with_user(self.engineer).with_context(action['context']).create({
            'user_id': self.admin.id,
        })
        wizard.action_apply()
        self.assertEqual(doc.tdb_reviewer_id, self.admin)
        self.assertEqual(doc.tdb_task_activity_id.user_id, self.admin)

    def test_submit_chooser_rechecks_permissions_and_rejects_redo(self):
        doc = self.document('INVALID-SUBMIT')
        action = doc.action_tdb_submit_review()
        wizard = self.env['tdb.plm.task.wizard'].with_user(self.engineer).with_context(action['context']).create({
            'user_id': self.engineer.id,
        })
        with self.assertRaises(UserError), self.cr.savepoint():
            wizard.action_apply()
        self.assertEqual(doc.engineering_state, 'draft')
        self.assertEqual(doc.tdb_reviewer_id, self.manager)
        doc.action_confirm()
        self.reject(doc)
        with self.assertRaises(UserError), self.cr.savepoint():
            doc.action_tdb_submit_review()

    def test_lv2_submit_chooser_only_offers_lv3(self):
        doc = self.document('LV2-SUBMIT', actor=self.manager, reviewer=self.admin)
        action = doc.action_tdb_submit_review()
        wizard = self.env['tdb.plm.task.wizard'].with_user(self.manager).with_context(action['context']).create({})
        self.assertIn(self.admin, wizard.allowed_user_ids)
        self.assertNotIn(self.manager, wizard.allowed_user_ids)
        wizard.action_apply()
        self.assertEqual(doc.tdb_reviewer_id, self.admin)

    def test_admin_confirm_bypasses_submission_chooser(self):
        doc = self.document('ADMIN-CONFIRM', actor=self.admin)
        doc.action_tdb_submit_review()
        self.assertEqual(doc.engineering_state, 'released')

    def test_web_and_cad_forms_use_submission_chooser_button(self):
        for xmlid in ('plm.view_attachment_form_plm_hinerit', 'plm.view_attachment_client_workflow',
                      'tdb_custom_plm.engineering_part_form', 'plm.plm_component_base_form'):
            view = self.env.ref(xmlid)
            arch = etree.fromstring(self.env[view.model].with_user(self.engineer).get_view(
                view_id=view.id, view_type='form')['arch'])
            self.assertTrue(arch.xpath("//button[@name='action_tdb_submit_review']"), xmlid)
            for user, label, absent in ((self.engineer, 'Submit Review', 'Admin Confirm'),
                                       (self.manager, 'Submit Review', 'Admin Confirm'),
                                       (self.admin, 'Admin Confirm', 'Submit Review')):
                form = etree.fromstring(self.env[view.model].with_user(user).get_view(
                    view_id=view.id, view_type='form')['arch'])
                buttons = form.xpath("//button[@name='action_tdb_submit_review']")
                self.assertTrue(any(button.get('string') == label for button in buttons), xmlid)
                self.assertFalse(any(button.get('string') == absent for button in buttons), xmlid)

    def test_first_shared_submission_succeeds_second_lists_blockers(self):
        first, fdoc = self.part('FIRST-ASSEMBLY')
        second, sdoc = self.part('SECOND-ASSEMBLY')
        shared, shared_doc = self.part('SHARED-B')
        self.bom(first, shared)
        self.bom(second, shared)
        first.action_confirm()
        task = shared.tdb_task_activity_id
        for action in (second.action_confirm, second.action_tdb_submit_review,
                       sdoc.action_tdb_submit_review,
                       lambda: second.with_context(_tdb_submission_scope=True).action_confirm(),
                       lambda: second.product_tmpl_id.write({'engineering_state': 'confirmed'})):
            with self.assertRaisesRegex(UserError, 'SHARED-B'), self.cr.savepoint():
                action()
        self.assertEqual(second.engineering_state, 'draft')
        self.assertEqual(sdoc.engineering_state, 'draft')
        self.assertEqual(shared.tdb_task_activity_id, task)
        self.assertEqual(first.engineering_state, 'confirmed')

    def test_submission_rechecks_shared_reviews_after_chooser_opens(self):
        first, fdoc = self.part('FIRST-ASSEMBLY')
        second, sdoc = self.part('SECOND-ASSEMBLY')
        shared, shared_doc = self.part('SHARED-B')
        self.bom(first, shared)
        self.bom(second, shared)
        action = second.action_tdb_submit_review()
        wizard = self.env['tdb.plm.task.wizard'].with_user(self.engineer).with_context(action['context']).create({})
        first.action_confirm()
        with self.assertRaisesRegex(UserError, 'SHARED-B'), self.cr.savepoint():
            wizard.action_apply()
        self.assertEqual(second.engineering_state, 'draft')
        self.assertFalse(second.tdb_task_activity_id)

    def test_shared_cad_reference_review_blocks_document_submission(self):
        first = self.document('FIRST.SLDASM')
        second = self.document('SECOND.SLDASM')
        child = self.document('SHARED.SLDPRT')
        self.env['ir.attachment.relation'].create([
            {'parent_id': first.id, 'child_id': child.id, 'link_kind': 'HiTree'},
            {'parent_id': second.id, 'child_id': child.id, 'link_kind': 'HiTree'},
        ])
        child.action_confirm()
        with self.assertRaisesRegex(UserError, 'SHARED.SLDPRT'), self.cr.savepoint():
            second.action_confirm()
        self.assertEqual(second.engineering_state, 'draft')

    def test_nonshared_reviewed_child_is_not_a_shared_submission_conflict(self):
        parent, doc = self.part('ONLY-ASSEMBLY')
        child, child_doc = self.part('ONLY-CHILD')
        self.bom(parent, child)
        child.action_confirm()
        task = child.tdb_task_activity_id
        parent.action_confirm()
        self.assertEqual(parent.engineering_state, 'confirmed')
        self.assertEqual(child.tdb_task_activity_id, task)

    def test_recursive_document_rejection_preserves_linked_child_part(self):
        root = self.document('DOCUMENT-ASSEMBLY.SLDASM')
        child, child_doc = self.part('DOCUMENT-CHILD')
        self.env['ir.attachment.relation'].create({
            'parent_id': root.id, 'child_id': child_doc.id, 'link_kind': 'HiTree'})
        child.action_confirm()
        root.action_confirm()
        self.reject(root)
        for record in (root, child_doc):
            self.assertEqual(record.engineering_state, 'draft')
            self.assertEqual(record.tdb_redo_user_id, self.engineer)
        self.assertEqual(child.engineering_state, 'confirmed')

    def test_shared_draft_document_can_enter_review_but_cannot_be_rejected_alone(self):
        part, doc = self.part('SHARED-DOCUMENT')
        other, _doc = self.part('OTHER-LINKED-PART')
        other.write({'linkeddocuments': [Command.link(doc.id)]})
        part.action_confirm()
        self.assertEqual(doc.engineering_state, 'confirmed')
        task = doc.tdb_task_activity_id

        with self.assertRaisesRegex(UserError, 'Shared file'), self.cr.savepoint():
            self.reject(doc)

        self.assertEqual(doc.engineering_state, 'confirmed')
        self.assertEqual(doc.tdb_task_activity_id, task)
        self.assertTrue(task.active)

    def test_admin_direct_confirm_of_draft_part_includes_draft_files(self):
        part, doc = self.part('ADMIN-DRAFT')
        part.with_user(self.admin).action_confirm()
        self.assertEqual(part.engineering_state, 'released')
        self.assertEqual(doc.engineering_state, 'released')

    def test_activity_row_opens_linked_part_or_document(self):
        part, doc = self.part('ROW-NAVIGATION')
        part.action_confirm()
        for record, model, target in ((part, 'product.product', part.id),
                                     (doc, 'ir.attachment', doc.id)):
            action = record.tdb_task_activity_id.with_user(self.manager).action_tdb_open_activity_record()
            self.assertEqual(action['res_model'], model)
            self.assertEqual(action['res_id'], target)
            self.assertEqual(action['target'], 'current')

    def test_standard_plm_activity_row_opens_its_part(self):
        part, doc = self.part('STANDARD-ACTIVITY')
        task = part.activity_schedule('mail.mail_activity_data_todo', user_id=self.manager.id)
        action = task.with_user(self.manager).action_tdb_open_activity_record()
        self.assertEqual(action['res_model'], 'product.product')
        self.assertEqual(action['res_id'], part.id)
        self.assertEqual(action['views'][0][0], self.env.ref('tdb_custom_plm.engineering_part_form').id)

    def test_row_navigation_is_scoped_to_plm_activities_action(self):
        action = self.env.ref('activity_validation.plm_action_view_activities')
        binding = action.view_ids.filtered(lambda view: view.view_mode == 'list')
        self.assertEqual(binding.view_id, self.env.ref('tdb_custom_plm.workflow_activity_navigation'))
        Activity = self.env['mail.activity'].with_user(self.manager)
        view = Activity.get_view(view_id=binding.view_id.id, view_type='list')
        self.assertEqual(etree.fromstring(view['arch']).get('action'), 'action_tdb_open_activity_record')
        native = Activity.get_view(view_id=self.env.ref('mail.mail_activity_view_tree').id, view_type='list')
        self.assertNotEqual(etree.fromstring(native['arch']).get('action'), 'action_tdb_open_activity_record')

    def test_native_cad_new_revision_of_released_part(self):
        part, doc = self.part('CAD-REVISE-PART', self.admin)
        part.action_confirm()
        part.with_user(self.admin).action_tdb_approve()
        new_id, revision = part.with_user(self.engineer).with_context(odooPLM=True).NewRevision()
        new_part = self.env['product.product'].browse(new_id)
        self.assertEqual(revision, 1)
        self.assertEqual(new_part.engineering_state, 'draft')
        self.assertEqual(part.engineering_state, 'undermodify')
        self.assertFalse(new_part.linkeddocuments)
        self.assertFalse(new_part.tdb_task_activity_id)
        self.assertFalse(new_part.tdb_review_history)
        self.assertFalse(part.description)
        approver = part.tdb_admin_approved_by
        new_part.with_user(self.engineer).unlink()
        self.assertEqual(part.engineering_state, 'released')
        self.assertEqual(part.tdb_admin_approved_by, approver)

    def test_cad_revision_still_blocks_unreleased_parts(self):
        part, doc = self.part('CAD-NOT-RELEASED', self.admin)
        with self.assertRaises(UserError), self.cr.savepoint():
            part.NewRevision()
        part.action_confirm()
        with self.assertRaises(UserError), self.cr.savepoint():
            part.NewRevision()
        self.assertEqual(part.engineering_state, 'confirmed')

    def test_cad_revision_rejects_checked_out_released_document(self):
        part, doc = self.part('CAD-CHECKOUT', self.admin)
        part.action_confirm()
        part.with_user(self.admin).action_tdb_approve()
        self.env['plm.checkout'].create({
            'documentid': doc.id, 'userid': self.engineer.id, 'hostname': 'cad-revision'})
        doc.invalidate_recordset()
        with self.assertRaises(UserError), self.cr.savepoint():
            part.NewRevision()
        self.assertEqual(part.engineering_state, 'released')

    def test_native_cad_new_revision_of_released_document(self):
        doc = self.document('CAD-REVISE-DOC.SLDASM', reviewer=self.admin)
        doc.action_confirm()
        doc.with_user(self.admin).action_tdb_approve()
        result = doc.with_user(self.engineer).with_context(odooPLM=True).NewRevision(doc.id)
        self.assertTrue(result)
        revisions = self.env['ir.attachment'].search([
            ('engineering_code', '=', doc.engineering_code), ('engineering_revision', '=', 1)])
        self.assertEqual(len(revisions), 1)
        self.assertEqual(revisions.engineering_state, 'draft')
        self.assertEqual(doc.engineering_state, 'undermodify')
        approver = doc.tdb_admin_approved_by
        revisions.with_user(self.engineer).unlink()
        self.assertEqual(doc.engineering_state, 'released')
        self.assertEqual(doc.tdb_admin_approved_by, approver)

    def test_combined_cad_revision_accepts_linked_document_singleton(self):
        part, doc = self.part('CAD-REVISE-BOTH', self.admin)
        part.action_confirm()
        part.with_user(self.admin).action_tdb_approve()
        new_part_id, _ = part.with_user(self.engineer).with_context(odooPLM=True).NewRevision()
        new_doc_id, revision = doc.with_user(self.engineer).with_context(odooPLM=True).NewRevision()
        new_doc = self.env['ir.attachment'].browse(new_doc_id)
        new_part = self.env['product.product'].browse(new_part_id)
        new_doc.write({'linkedcomponents': [(5, 0, 0)]})
        new_part.write({'linkeddocuments': [(4, new_doc.id)]})
        self.assertEqual(revision, 1)
        self.assertEqual(new_doc.engineering_state, 'draft')
        self.assertEqual(new_part.linkeddocuments, new_doc)
        self.assertEqual(doc.engineering_state, 'undermodify')

    def test_recursive_rejection_checks_out_files_before_mutating(self):
        parent, doc, a, adoc, b, bdoc = self.tree()
        self.env['plm.checkout'].create({
            'documentid': bdoc.id, 'userid': self.engineer.id, 'hostname': 'review-workstation'})
        bdoc.invalidate_recordset()
        with self.assertRaises(UserError), self.cr.savepoint():
            self.reject(parent)
        for record in (parent, doc, a, adoc, b, bdoc):
            self.assertEqual(record.engineering_state, 'confirmed')

    def test_recursive_rejection_handles_shared_child_inside_one_scope_once(self):
        parent, doc = self.part('ONE-ASSEMBLY')
        left, left_doc = self.part('LEFT')
        right, right_doc = self.part('RIGHT')
        shared, shared_doc = self.part('INTERNAL-SHARED')
        self.bom(parent, left | right)
        self.bom(left, shared)
        self.bom(right, shared)
        parent.action_confirm()
        self.reject(parent)
        tasks = self.env['mail.activity'].search([
            ('tdb_plm_target_model', '=', 'product.template'),
            ('tdb_plm_target_id', '=', shared.product_tmpl_id.id),
            ('tdb_plm_kind', '=', 'redo'), ('active', '=', True),
        ])
        self.assertEqual(len(tasks), 1)
