import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';

const source = await readFile(new URL('../static/src/live_refresh_queue.js', import.meta.url), 'utf8');
const { LiveRefreshQueue } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

test('bursts coalesce and unsaved edits defer rather than discard', async () => {
    let editable = false;
    let loads = 0;
    const queue = new LiveRefreshQueue({canRefresh: () => editable, refresh: async () => loads++, delay: 10000, retryDelay: 10000});
    try {
        queue.request(); queue.request(); queue.request();
        await queue.flush();
        assert.equal(loads, 0);
        assert.equal(queue.pending, true);
        editable = true;
        await queue.flush();
        assert.equal(loads, 1);
        assert.equal(queue.pending, false);
    } finally { queue.stop(); }
});

test('notifications arriving during a reload are not lost', async () => {
    let finish;
    let loads = 0;
    const queue = new LiveRefreshQueue({canRefresh: () => true, refresh: () => {loads++; return new Promise(resolve => {finish = resolve;});}, delay: 10000});
    try {
        queue.request();
        const first = queue.flush();
        await Promise.resolve();
        assert.equal(loads, 1);
        queue.request();
        await queue.flush();
        assert.equal(loads, 1);
        finish(); await first;
        assert.equal(queue.pending, true);
    } finally { queue.stop(); }
});

test('unmount cancels a reload awaiting permission to refresh', async () => {
    let permit;
    let loads = 0;
    const queue = new LiveRefreshQueue({canRefresh: () => new Promise(resolve => {permit = resolve;}), refresh: async () => loads++, delay: 10000});
    queue.request();
    const pending = queue.flush();
    queue.stop(); permit(true);
    await pending;
    assert.equal(loads, 0);
    assert.equal(queue.timer, null);
});

test('transient failures remain queued for retry', async () => {
    let fails = true;
    let errors = 0;
    const queue = new LiveRefreshQueue({canRefresh: () => true, refresh: async () => {if (fails) throw new Error('offline');}, onError: () => errors++, delay: 10000, retryDelay: 10000});
    try {
        queue.request(); await queue.flush();
        assert.equal(errors, 1);
        assert.equal(queue.pending, true);
        fails = false;
        await queue.flush();
        assert.equal(queue.pending, false);
    } finally { queue.stop(); }
});
