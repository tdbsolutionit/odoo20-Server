/** @odoo-module **/

import '@mail/chatter/web/form_compiler';
import '@mail/chatter/web/form_renderer';
import '@mail/chatter/web/form_controller';
import { patch } from '@web/core/utils/patch';
import { FormCompiler } from '@web/views/form/form_compiler';
import { FormRenderer } from '@web/views/form/form_renderer';
import { FormController } from '@web/views/form/form_controller';
import { getPlmChatterTarget } from './plm_chatter_target';

patch(FormRenderer.prototype, {
    get tdbChatterTarget() {
        return getPlmChatterTarget(this.props.record);
    },
    hasFile() {
        const target = this.tdbChatterTarget;
        if (target.model === this.props.record.resModel) {
            return super.hasFile(...arguments);
        }
        if (!this.mailStore || !target.id) {
            return false;
        }
        this.thread.set(this.mailStore['mail.thread'].insert(target));
        return this.thread().attachmentsInWebClientView.length > 0;
    },
});

patch(FormCompiler.prototype, {
    compile() {
        const result = super.compile(...arguments);
        for (const chatter of result.querySelectorAll("t[t-component='__comp__.mailComponents.Chatter']")) {
            chatter.setAttribute('threadId', '__comp__.tdbChatterTarget.id or undefined');
            chatter.setAttribute('threadModel', '__comp__.tdbChatterTarget.model');
        }
        return result;
    },
});

patch(FormController.prototype, {
    onWillLoadRoot(next) {
        const record = this.model.root;
        const target = getPlmChatterTarget(record);
        const sameRecord = record?.resId === next.resId && record?.resModel === next.resModel;
        const result = super.onWillLoadRoot(...arguments);
        if (sameRecord && target.id && target.model !== record.resModel) {
            this.env.bus.trigger('MAIL:RELOAD-THREAD', target);
        }
        return result;
    },
});
