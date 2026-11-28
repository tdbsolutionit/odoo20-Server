"""Run the Edge integration probe only against our isolated test database.

Usage from the repository root: .venv/Scripts/python.exe <this file>
"""
import logging
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import urllib.request


def main():
    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root))
    os.environ.pop('PYTZ_TZDATADIR', None)
    logging.disable(logging.WARNING)
    from odoo.tools import config
    from odoo import api, SUPERUSER_ID
    from odoo.modules.registry import Registry

    database = 'tdb_plm_review_test_v2'
    workflow_probe = os.environ.get('LIVE_TEST_MODE') == 'workflow'
    data = root / ('data/tdb_workflow_browser' if workflow_probe else 'data/tdb_hierarchy_check')
    config.parse_config(['-c', str(root / 'odoo.conf'), '-d', database,
                         '--data-dir=' + str(data)], setup_logging=False)
    password = secrets.token_urlsafe(24)
    with Registry(database).cursor() as cr:
        env = api.Environment(cr, SUPERUSER_ID, {})
        # Core asset streaming requires file storage when the attachment also
        # has a /web/assets URL; DB-only storage redirects to that same URL.
        env['ir.config_parameter'].sudo().set_str('ir_attachment.location', 'file')
        # This isolated database was exercised with several data directories.
        # Regenerate cached web assets instead of using missing old file blobs.
        env['ir.attachment'].search([('url', '=like', '/web/assets/%')]).unlink()
        env.ref('base.user_admin').write({'password': password})
        doc = env['ir.attachment'].create({
            'name': 'Live browser test', 'engineering_code': 'LIVE-BROWSER-' + secrets.token_hex(5),
            'is_plm': True,
        })
        doc_id = doc.id
        form_id = env.ref('plm.view_attachment_form_plm_hinerit').id
        task_id = None
        activity_action_id = env.ref('activity_validation.plm_action_view_activities').id
        login = 'admin'
        if workflow_probe:
            from odoo import Command
            login = 'workflow_browser_' + secrets.token_hex(4)
            reviewer = env['res.users'].with_context(no_reset_password=True).create({
                'name': 'Browser Workflow Reviewer', 'login': login, 'password': password,
                'group_ids': [Command.set([env.ref('base.group_user').id,
                                         env.ref('tdb_custom_plm.group_review_manager').id])],
            })
            engineer = env['res.users'].with_context(no_reset_password=True).create({
                'name': 'Browser Workflow Engineer', 'login': login + '_engineer',
                'group_ids': [Command.set([env.ref('base.group_user').id,
                                         env.ref('plm.group_plm_integration_user').id])],
            })
            part = env['product.product'].with_user(engineer).create({
                'name': 'Workflow browser Part', 'engineering_code': 'WORKFLOW-' + secrets.token_hex(4),
                'tdb_reviewer_id': reviewer.id, 'linkeddocuments': [Command.link(doc.id)],
            })
            doc.write({'tdb_reviewer_id': reviewer.id})
            part.action_confirm()
            task_id = part.tdb_task_activity_id.id
            part.product_tmpl_id.message_post(body='Timeline before Part completion')
            activity = part.activity_schedule(
                'plm.mail_activity_plm_activity', summary='Timeline Part completion')
            activity._action_done(feedback='Part completion feedback')
            part.product_tmpl_id.message_post(body='Timeline after Part completion')
            part.activity_schedule('activity_validation.mail_activity_change_request',
                                   summary='Browser ECR form-only completion', user_id=reviewer.id)
            part.activity_schedule('mail.mail_activity_data_todo',
                                   summary='Browser ordinary todo', user_id=reviewer.id)
        cr.commit()
    print('Isolated browser fixture ready', flush=True)
    server = subprocess.Popen([
        sys.executable, str(root / 'odoo-bin'), '-c', str(root / 'odoo.conf'),
        '-d', database, '--db-filter=^' + database + '$', '--data-dir=' + str(data),
        '--http-port=8097', '--max-cron-threads=0',
        '--logfile=' + str(root / 'logs/tdb_plm_live_browser.log'),
    ], cwd=root, creationflags=subprocess.CREATE_NO_WINDOW,
       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            if server.poll() is not None:
                raise RuntimeError('Browser probe server exited')
            try:
                urllib.request.urlopen('http://127.0.0.1:8097/web/login', timeout=2).close()
                break
            except OSError:
                time.sleep(0.3)
        else:
            raise RuntimeError('Browser probe server not ready')
        child_env = dict(os.environ, LIVE_TEST_URL='http://127.0.0.1:8097',
                         LIVE_TEST_PASSWORD=password, LIVE_TEST_DB=database,
                         LIVE_TEST_LOGIN=login, LIVE_TEST_TASK_ID=str(task_id or ''),
                         LIVE_TEST_ACTIVITY_ACTION_ID=str(activity_action_id),
                         LIVE_TEST_DOC_ID=str(doc_id), LIVE_TEST_FORM_ID=str(form_id),
                         LIVE_TEST_PROFILE=str(root / 'data/tdb_live_edge_profile'))
        node = subprocess.Popen(['node', str(Path(__file__).with_suffix('.mjs'))],
                                cwd=root, env=child_env)
        try:
            result = node.wait(timeout=180)
        except subprocess.TimeoutExpired:
            subprocess.run(['taskkill', '/PID', str(node.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            raise
        if result:
            raise RuntimeError('Browser integration probe failed')
    finally:
        server.terminate()
        server.wait(timeout=10)


if __name__ == '__main__':
    main()
