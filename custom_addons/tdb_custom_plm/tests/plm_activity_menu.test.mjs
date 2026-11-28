import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const asModule = (source) => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const patchSource = await readFile(new URL('../../../addons/web/static/src/core/utils/patch.js', import.meta.url), 'utf8');
const { patch } = await import(asModule(patchSource));
const source = await readFile(new URL('../static/src/plm_activity_menu.js', import.meta.url), 'utf8');
const install = new Function('ActivityMenu', 'patch', source.replace(/^import .*;\r?\n/gm, ''));
class Menu {
    openActivityGroup(...args) { return { original: args }; }
}
install(Menu, patch);

test('clock links use durable assignment and date filters without transient IDs', () => {
    const menu = new Menu();
    const calls = [];
    let closed = 0;
    menu.dropdown = { close() { closed++; } };
    menu.action = { doAction(...args) { calls.push(args); } };
    const group = { tdb_plm_action_id: 123 };
    for (const [filter, dateFilter] of [['all', null], ['overdue', 'late'], ['today', 'today'], ['upcoming_all', 'future']]) {
        menu.openActivityGroup(group, filter, true);
        const [action, options] = calls.at(-1);
        assert.equal(action, 123);
        assert.equal(options.newWindow, true);
        const expected = { search_default_tdb_my_plm_activities: 1 };
        if (dateFilter) expected[`search_default_tdb_plm_${dateFilter}`] = 1;
        assert.deepEqual(options.additionalContext, expected);
    }
    assert.equal(closed, 4);
});

test('ordinary activity groups retain native routing', () => {
    const group = { model: 'res.partner' };
    assert.deepEqual(new Menu().openActivityGroup(group, 'today', false),
        { original: [group, 'today', false] });
});
