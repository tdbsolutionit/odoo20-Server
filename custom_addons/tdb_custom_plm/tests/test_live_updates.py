from unittest.mock import patch

from odoo import Command
from odoo.tests import TransactionCase, tagged

from ..models.live_updates import EVENT, PENDING, VIEW_MODELS


@tagged('post_install', '-at_install', 'tdb_plm_live')
class TestPlmLiveUpdates(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['ir.config_parameter'].sudo().set_str('ir_attachment.location', 'db')

    def test_changes_are_batched_without_record_data(self):
        live = self.env['tdb.plm.live']
        live._notify()
        live._notify()
        self.assertEqual(self.cr.precommit.data[PENDING], VIEW_MODELS)
        with patch.object(type(self.env['res.users']), '_bus_send', autospec=True) as send:
            live._flush_notifications()
            count = send.call_count
            self.assertGreater(count, 0)
            for call in send.call_args_list:
                recipient, event, payload = call.args
                self.assertTrue(recipient.has_group('plm.group_plm_view_user'))
                self.assertEqual(event, EVENT)
                self.assertEqual(payload, {'models': sorted(VIEW_MODELS)})
            live._flush_notifications()
            self.assertEqual(send.call_count, count)

    def test_non_plm_users_do_not_receive_signals(self):
        user = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Live test ordinary user', 'login': 'live_ordinary_user',
            'group_ids': [Command.set([self.env.ref('base.group_user').id])],
        })
        live = self.env['tdb.plm.live']
        live._notify()
        with patch.object(type(user), '_bus_send', autospec=True) as send:
            live._flush_notifications()
            self.assertNotIn(user.id, [call.args[0].id for call in send.call_args_list])

    def test_document_and_reviewer_writes_queue_updates(self):
        doc = self.env['ir.attachment'].create({
            'name': 'Live test CAD file', 'engineering_code': 'LIVE-TEST', 'is_plm': True,
        })
        self.cr.precommit.data[PENDING] = set()
        doc.write({'description': 'Saved from CAD'})
        self.assertEqual(self.cr.precommit.data[PENDING], VIEW_MODELS)
        self.cr.precommit.data[PENDING] = set()
        doc._tdb_internal_write({'tdb_review_stage': 'manager'})
        self.assertEqual(self.cr.precommit.data[PENDING], VIEW_MODELS)
