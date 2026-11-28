import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const source = await readFile(new URL('../static/src/plm_chatter_target.js', import.meta.url), 'utf8');
const { getPlmChatterTarget } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

test('PLM variant form uses the existing template workflow thread', () => {
    assert.deepEqual(getPlmChatterTarget({
        resModel: 'product.product', resId: 42, context: { odooPLM: true },
        data: { engineering_code: 'PART-A', product_tmpl_id: { id: 17 } },
    }), { model: 'product.template', id: 17 });
});

test('ordinary variants and document forms retain their own threads', () => {
    for (const record of [
        { resModel: 'product.product', resId: 42, context: {}, data: { engineering_code: 'PART-A', product_tmpl_id: { id: 17 } } },
        { resModel: 'product.product', resId: 42, context: { odooPLM: true }, data: { engineering_code: false, product_tmpl_id: { id: 17 } } },
        { resModel: 'ir.attachment', resId: 99, context: { odooPLM: true }, data: {} },
    ]) {
        assert.deepEqual(getPlmChatterTarget(record), { model: record.resModel, id: record.resId });
    }
});

test('unsaved Parts do not attach messages to an existing template', () => {
    assert.deepEqual(getPlmChatterTarget({
        resModel: 'product.product', resId: false, context: { odooPLM: true },
        data: { engineering_code: 'NEW', product_tmpl_id: { id: 17 } },
    }), { model: 'product.product', id: false });
});
