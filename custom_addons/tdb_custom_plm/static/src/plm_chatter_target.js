/** @odoo-module **/

export function getPlmChatterTarget(record) {
    const templateId = record?.data?.product_tmpl_id?.id;
    if (record?.resModel === 'product.product' && record.context?.odooPLM
        && record.resId && record.data.engineering_code && templateId) {
        return { model: 'product.template', id: templateId };
    }
    return { model: record?.resModel, id: record?.resId };
}
