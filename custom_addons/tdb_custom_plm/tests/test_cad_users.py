import threading
from contextlib import contextmanager

from lxml import etree

from odoo import Command
from odoo.tests import TransactionCase, tagged


@contextmanager
def rpc_method(name):
    thread = threading.current_thread()
    previous = getattr(thread, 'rpc_model_method', None)
    thread.rpc_model_method = name
    try:
        yield
    finally:
        if previous is None:
            del thread.rpc_model_method
        else:
            thread.rpc_model_method = previous


@tagged('post_install', '-at_install', 'tdb_plm_cad_users')
class TestCadUserPicker(TransactionCase):
    def test_cad_selector_has_no_binary_columns(self):
        users = self.env['res.users'].with_context(odooPLM=True)
        result = users.koo_fields_view_get(view_type='list')
        root = etree.fromstring(result['arch'])
        self.assertEqual([node.get('name') for node in root.xpath('.//field')],
                         ['name', 'login', 'company_id'])
        self.assertEqual(set(result['fields']), {'name', 'login', 'company_id'})
        native = self.env['res.users'].get_view(view_type='list')
        self.assertIn('avatar_128', native['arch'])

    def test_engineer_can_select_and_save_assigned_reviewer(self):
        def make_user(login, group):
            return self.env['res.users'].with_context(no_reset_password=True).create({
                'name': login, 'login': login,
                'group_ids': [Command.set([
                    self.env.ref('base.group_user').id, self.env.ref(group).id,
                ])],
            })
        engineer = make_user('cad_picker_engineer', 'plm.group_plm_integration_user')
        manager = make_user('cad_picker_manager', 'tdb_custom_plm.group_review_manager')
        admin = make_user('cad_picker_admin', 'tdb_custom_plm.group_review_admin')
        product_model = self.env['product.product'].with_user(engineer).with_context(odooPLM=True)
        domain = product_model.fields_get(['tdb_reviewer_id'])['tdb_reviewer_id']['domain']
        users = self.env['res.users'].with_user(engineer).with_context(odooPLM=True)
        with rpc_method('res.users.search_read'):
            rows = users.search_read(domain, ['name', 'login', 'company_id'])
        self.assertIn(manager.id, [row['id'] for row in rows])
        self.assertEqual(users.browse(manager.id).name_get(), [(manager.id, manager.display_name)])
        product = product_model.createFromProps({
            'engineering_code': 'CAD-PICKER-TEST', 'name': 'CAD Picker Test',
            'tdb_reviewer_id': manager.id,
        })
        self.assertEqual(product.tdb_reviewer_id, manager)

    def test_cad_avatar_is_base64(self):
        user = self.env.user
        native = user.read(['name', 'avatar_128'])[0]
        self.assertIsInstance(native['avatar_128'], dict)
        with rpc_method('res.users.read'):
            legacy = user.read(['name', 'avatar_128'])[0]
        self.assertEqual(legacy['avatar_128'], native['avatar_128']['content'])
        self.assertEqual(legacy['name'], native['name'])

    def test_flattened_binary_columns(self):
        user = self.env.user
        native = user.read(['avatar_128'])[0]['avatar_128']
        with rpc_method('res.users.read'):
            row = user.read(['name', 'avatar_128.size', 'avatar_128.content'])[0]
        self.assertEqual(row['avatar_128.size'], native['size'])
        self.assertEqual(row['avatar_128.content'], native['content'])
        self.assertNotIn('avatar_128', row)

    def test_search_read_and_web_format(self):
        user = self.env.ref('base.user_admin')
        with rpc_method('res.users.search_read'):
            row = self.env['res.users'].search_read(
                [('id', '=', user.id)], ['name', 'avatar_128'],
            )[0]
            web = user.read(['avatar_128'], load='web')[0]
            metadata = self.env['res.users'].search_read(
                [('id', '=', user.id)], ['name', 'avatar_128.size'],
            )[0]
        self.assertIsInstance(row['avatar_128'], str)
        self.assertIsInstance(web['avatar_128'], dict)
        self.assertIn('size', web['avatar_128'])
        self.assertEqual(metadata['avatar_128.size'], web['avatar_128']['size'])

    def test_unknown_fields_are_not_ignored(self):
        with rpc_method('res.users.read'):
            with self.assertRaises(ValueError):
                self.env.user.read(['avatar_128.not_a_property'])
        with self.assertRaises(ValueError):
            self.env.user.read(['avatar_128.size'])
