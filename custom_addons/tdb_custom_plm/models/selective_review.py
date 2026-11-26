"""Recursive review actions and shared-review submission protection."""
from odoo import fields, models, _
from odoo.exceptions import AccessError, UserError

from .review_workflow import review_level

_SUBMISSION_SCOPE = object()


class SelectiveReview(models.AbstractModel):
    _inherit = 'revision.plm.mixin'

    def action_tdb_approve(self):
        if review_level(self.env.user) == 3:
            for record in self:
                record._tdb_check_review_actor()
            return self._tdb_release_recursive()
        return super().action_tdb_approve()

    def _tdb_dependencies(self):
        self.ensure_one()
        if self._name == 'product.template':
            parts = self.bom_ids.bom_line_ids.product_id.product_tmpl_id
            return list(parts.filtered('engineering_code')) + list(
                self.linkeddocuments.filtered('is_plm'))
        if self._name == 'ir.attachment':
            return list(self.env['ir.attachment.relation'].search([
                ('parent_id', '=', self.id),
                ('link_kind', 'in', ['HiTree', 'LyTree', 'RfTree', 'PkgTree']),
            ]).child_id.filtered('is_plm'))
        return []

    def _tdb_dependency_order(self):
        """Return each record once, children first; reject circular structures."""
        ordered, visited, visiting = [], set(), set()

        def visit(record):
            key = (record._name, record.id)
            if key in visiting:
                raise UserError(_('Circular PLM dependency: %s', record.display_name))
            if key in visited:
                return
            record.check_access('read')
            visiting.add(key)
            for child in record._tdb_dependencies():
                visit(child)
            visiting.remove(key)
            visited.add(key)
            ordered.append(record)

        for record in self:
            visit(record)
        return ordered

    def _tdb_release_recursive(self, include_statuses=None):
        """Release the scope using each record's own workflow permissions."""
        if review_level(self.env.user) != 3:
            raise AccessError(_('Only Level 3 can confirm and release.'))
        ordered = self._tdb_dependency_order()
        # Lock in a stable order, including already released dependencies.
        for model in ('ir.attachment', 'product.template'):
            self.env[model].browse([r.id for r in ordered if r._name == model])._tdb_lock()
        if {(r._name, r.id) for r in self._tdb_dependency_order()} != {(r._name, r.id) for r in ordered}:
            raise UserError(_('The assembly structure changed. Refresh and retry release.'))
        roots = {(record._name, record.id) for record in self}
        eligible_states = set(include_statuses or ['confirmed'])
        ordered = [record for record in ordered
                   if (record._name, record.id) in roots or record.engineering_state in eligible_states]
        for record in ordered:
            if record.engineering_state == 'released':
                continue
            record._tdb_refuse_checked_out()
            record._tdb_check_release()
        for record in ordered:
            if record.engineering_state != 'released':
                # Normal ORM checks still enforce LV3, assignee, and active redo.
                record.move_to_state('released')
        return True

    def _tdb_review_scope(self, include_linked_parts=True):
        """Collect Parts, files and descendants without walking upward to other assemblies."""
        parts = self.env['product.template']
        docs = self.env['ir.attachment']
        pending, visited = list(self), set()
        while pending:
            record = pending.pop()
            key = (record._name, record.id)
            if key in visited:
                continue
            visited.add(key)
            record.check_access('read')
            if record._name == 'product.template':
                parts |= record
            elif record._name == 'ir.attachment':
                docs |= record
                if include_linked_parts:
                    pending.extend(record.linkedcomponents.product_tmpl_id.filtered('engineering_code'))
            pending.extend(record._tdb_dependencies())
        return parts, docs

    def _tdb_check_shared_reviews(self, lock=False):
        if self.env.context.get('_tdb_submission_scope') is _SUBMISSION_SCOPE:
            return
        parts, docs = self._tdb_review_scope()
        if lock:
            docs._tdb_lock()
            parts._tdb_lock()
            current_parts, current_docs = self._tdb_review_scope()
            if current_parts != parts or current_docs != docs:
                raise UserError(_('The assembly structure changed. Refresh and retry submission.'))
        shared_parts = self.env['product.template']
        for part in parts:
            parents = self.env['mrp.bom.line'].sudo().search([
                ('product_id.product_tmpl_id', '=', part.id),
            ]).bom_id.product_tmpl_id
            if len(parents) > 1:
                shared_parts |= part
        blockers = list(shared_parts.filtered(lambda part: part.engineering_state == 'confirmed'))
        for doc in docs.filtered(lambda doc: doc.engineering_state == 'confirmed'):
            parents = self.env['ir.attachment.relation'].sudo().search([('child_id', '=', doc.id)]).parent_id
            linked = doc.linkedcomponents.product_tmpl_id
            if len(parents) > 1 or len(linked) > 1 or linked & shared_parts:
                blockers.append(doc)
        if blockers:
            lines = ['- %s | %s | %s' % (record.engineering_code or record.display_name,
                     record.display_name, record.tdb_reviewer_id.display_name or _('No reviewer'))
                     for record in blockers]
            raise UserError(_('Cannot submit: these shared Parts/files are already in Review. '
                              'Finish or return their existing reviews first:\n%s', '\n'.join(lines)))

    def _tdb_reject_recursive(self, reason, assignee, deadline):
        self.ensure_one()
        self._tdb_check_reject_actor()
        parts, docs = self._tdb_review_scope(include_linked_parts=False)
        docs._tdb_lock()
        parts._tdb_lock()
        current_parts, current_docs = self._tdb_review_scope(include_linked_parts=False)
        if current_parts != parts or current_docs != docs:
            raise UserError(_('The assembly structure changed. Refresh and retry rejection.'))
        self._tdb_check_reject_actor()
        # Released references and existing Draft/redo work are never overwritten.
        parts = parts.filtered(lambda record: record.engineering_state == 'confirmed')
        docs = docs.filtered(lambda record: record.engineering_state == 'confirmed')
        # Do not silently unlock a shared revision submitted by another assembly.
        for part in parts:
            all_parents = self.env['mrp.bom.line'].sudo().search([
                ('product_id.product_tmpl_id', '=', part.id),
            ]).bom_id.product_tmpl_id
            parents = all_parents - parts
            if len(all_parents) > 1 and parents.filtered(lambda parent: parent.engineering_state == 'confirmed'):
                raise UserError(_('Shared Part %s is used by another assembly in Review. '
                    'Coordinate its rejection before including this shared Part in redo.', part.display_name))
        for doc in docs:
            all_parents = self.env['ir.attachment.relation'].sudo().search([
                ('child_id', '=', doc.id),
            ]).parent_id
            parents = all_parents - docs
            linked_parts = doc.sudo().linkedcomponents.product_tmpl_id
            other_parts = linked_parts - parts
            if ((len(all_parents) > 1 and parents.filtered(lambda d: d.engineering_state == 'confirmed'))
                    or (len(linked_parts) > 1 and other_parts.filtered(lambda p: p.engineering_state == 'confirmed'))):
                raise UserError(_('Shared file %s is used by another record in Review. '
                    'Coordinate its rejection before including it in redo.', doc.display_name))
        records = list(parts) + list(docs)
        for record in records:
            record._tdb_check_reject_actor()
            record._tdb_refuse_checked_out()
        for record in records:
            record._tdb_reject(reason, assignee, deadline)
        return True


class SelectiveProductRelease(models.Model):
    _inherit = 'product.product'

    def commonWFAction(self, status, include_statuses=None, recursive=False, check_in_check=True):
        if status == 'released':
            return self.product_tmpl_id._tdb_release_recursive(include_statuses)
        if status == 'confirmed':
            self.product_tmpl_id._tdb_check_shared_reviews(lock=True)
            return super(SelectiveProductRelease, self.with_context(
                _tdb_submission_scope=_SUBMISSION_SCOPE)).commonWFAction(
                    status, include_statuses or [], recursive, check_in_check)
        return super().commonWFAction(status, include_statuses or [], recursive, check_in_check)


class SelectiveDocumentRelease(models.Model):
    _inherit = 'ir.attachment'

    def commonWFAction(self, writable, state, check):
        if state == 'released':
            include_statuses = ['draft', 'confirmed'] if all(
                record.engineering_state == 'draft' for record in self) else ['confirmed']
            return self._tdb_release_recursive(include_statuses)
        if state == 'confirmed':
            self._tdb_check_shared_reviews(lock=True)
            return super(SelectiveDocumentRelease, self.with_context(
                _tdb_submission_scope=_SUBMISSION_SCOPE)).commonWFAction(writable, state, check)
        return super().commonWFAction(writable, state, check)


class RecursiveRejectWizard(models.TransientModel):
    _inherit = 'tdb.plm.task.wizard'

    def action_apply(self):
        self.ensure_one()
        if self.operation == 'reject':
            if self.target_ids and self.target_ids != [self.target_id]:
                raise UserError(_('Reject one assembly at a time.'))
            record = self.env[self.target_model].browse(self.target_id).exists()
            record.ensure_one()
            record._tdb_reject_recursive(self.reason, self.user_id, self.date_deadline)
            return {'type': 'ir.actions.act_window_close'}
        return super().action_apply()


class LegacySelectiveRejectWizard(models.TransientModel):
    _inherit = 'tdb.plm.reject.wizard'

    def action_reject(self):
        self.ensure_one()
        record = self.env[self.target_model].browse(self.target_id).exists()
        record.ensure_one()
        record._tdb_reject_recursive(self.reason, self.assignee_id, fields.Date.context_today(self))
        return {'type': 'ir.actions.act_window_close'}
