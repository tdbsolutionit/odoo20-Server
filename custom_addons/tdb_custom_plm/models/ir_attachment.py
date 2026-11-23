import threading

from odoo import api, models, _
from odoo.exceptions import UserError
from odoo.http import request


class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    def unlink(self):
        from .review_workflow import _DISCARD_REVISION

        return super(IrAttachment, self.with_context(_tdb_discard_revision=_DISCARD_REVISION)).unlink()

    def unlinkRestorePreviousDocument(self):
        return self._tdb_restore_after_discard()

    @api.model
    def NewRevision(self, docId=None, newBomDocumentRevision=True):
        # Combined CAD revision calls on a singleton; legacy RPC passes its ID.
        if docId is None:
            self.ensure_one()
            docId = self.id
        return super().NewRevision(docId, newBomDocumentRevision)

    def _tdb_prepare_reviewers(self):
        super()._tdb_prepare_reviewers()
        if self.tdb_reviewer_id:
            return
        components = self.sudo().linkedcomponents.product_tmpl_id.with_env(self.env)
        components.check_access('read')
        reviewers = set()
        for component in components:
            component._tdb_prepare_reviewers()
            if component.tdb_reviewer_id:
                reviewers.add(component.tdb_reviewer_id.id)
        if len(reviewers) > 1:
            raise UserError(_('Linked Parts have different reviewers. Select this Document reviewer explicitly.'))
        if reviewers:
            self.write({'tdb_reviewer_id': reviewers.pop()})

    def action_confirm(self):
        from .review_workflow import review_level
        for doc in self:
            if doc.tdb_review_stage == 'forward':
                doc._tdb_forward(doc.tdb_reviewer_id)
            elif doc.tdb_redo_chain:
                raise UserError(_('Complete the redo activity before confirming.'))
            elif review_level(self.env.user) == 3:
                doc.commonWFAction(False, 'released', False)
            else:
                super(IrAttachment, doc).action_confirm()
        return False

    def basePreview64Img(self):
        if self.preview:
            return 'data:image/png;base64,%s' % self.preview.to_base64()
        return super().basePreview64Img()

    def read(self, fields=None, load='_classic_read', *args, **kwargs):
        values = super().read(fields=fields or [], load=load, *args, **kwargs)
        # RPC dispatch clears the HTTP request before executing model methods.
        # Older CAD clients expect base64 strings, rather than Odoo 20's binary
        # dictionaries. Web reads must keep the native binary representation.
        if not request and getattr(
            threading.current_thread(), 'rpc_model_method', None
        ) == 'ir.attachment.read':
            for row in values:
                for name in ('preview', 'preview_related'):
                    value = row.get(name)
                    if isinstance(value, dict) and 'content' in value:
                        row[name] = value['content']
        return values
