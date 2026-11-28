/** @odoo-module **/

import '@mail/chatter/web/form_compiler';
import '@mail/chatter/web/form_renderer';
import '@mail/chatter/web/form_controller';
import '@mail/chatter/web/chatter_patch';
import { Chatter } from '@mail/chatter/web_portal_project/chatter';
import { Thread } from '@mail/core/common/thread';
import { compareDatetime } from '@mail/utils/common/misc';
import { signal } from '@odoo/owl';
import { patch } from '@web/core/utils/patch';
import { FormCompiler } from '@web/views/form/form_compiler';
import { FormRenderer } from '@web/views/form/form_renderer';
import { FormController } from '@web/views/form/form_controller';
import { getPlmChatterTarget } from './plm_chatter_target';

class PlmTimelineThread extends Thread {
    static template = 'tdb_custom_plm.PlmTimelineThread';

    get partCompletionMessages() {
        return this.env.tdbPartTimeline?.(this.props.thread) ?? [];
    }

    get orderedMessages() {
        const messages = super.orderedMessages;
        const completions = this.partCompletionMessages;
        if (!completions.length) {
            return messages;
        }
        const byId = new Map([...messages, ...completions].map((message) => [message.id, message]));
        const direction = this.props.order === 'asc' ? 1 : -1;
        return [...byId.values()].sort((a, b) =>
            direction * (compareDatetime(a.datetime, b.datetime) || a.id - b.id)
        );
    }
}

Object.assign(Chatter.components, { PlmTimelineThread });

patch(Chatter.prototype, {
    get subEnv() {
        return {
            ...super.subEnv,
            tdbPartTimeline: (thread) => thread.eq(this.state.thread) ? this.tdbPartCompletionMessages : [],
        };
    },

    setup() {
        super.setup(...arguments);
        this.tdbPartCompletion = signal(null);
        this.tdbCompletionRequest = 0;
    },

    get tdbPartActivityTarget() {
        const record = this.webChatterProps?.record;
        const template = getPlmChatterTarget(record);
        if (template.model === 'product.template' && record?.resModel === 'product.product'
            && this.state.thread?.model === template.model && this.state.thread.id === template.id) {
            return { model: record.resModel, id: record.resId };
        }
    },

    get activities() {
        const target = this.tdbPartActivityTarget;
        const part = target && this.store['mail.thread'].get(target);
        if (!part?.activities.length) {
            return super.activities;
        }
        return [...super.activities, ...part.activities].sort(
            (a, b) => compareDatetime(a.date_deadline, b.date_deadline) || a.id - b.id
        );
    },

    get tdbPartCompletionMessages() {
        const target = this.tdbPartActivityTarget;
        const completion = this.tdbPartCompletion();
        if (!target || completion?.partId !== target.id) {
            return [];
        }
        return completion.ids.map((id) => this.store['mail.message'].get(id)).filter(Boolean);
    },

    async tdbLoadPartActivities(part) {
        const request = ++this.tdbCompletionRequest;
        await Promise.all([
            part.fetchThreadData(['activities']),
            this.orm.call('product.product', 'tdb_get_activity_completion_messages', [[part.id]])
                .then(({ ids, store_data: storeData }) => {
                    if (request === this.tdbCompletionRequest && this.tdbPartActivityTarget?.id === part.id) {
                        this.store.insert(storeData);
                        this.tdbPartCompletion.set({ partId: part.id, ids });
                    }
                }),
        ]);
    },

    async load(thread, requestList) {
        const target = this.tdbPartActivityTarget;
        if (!target || !requestList.includes('activities')) {
            return super.load(...arguments);
        }
        const part = this.store['mail.thread'].insert(target);
        if (thread?.eq(part)) {
            return this.tdbLoadPartActivities(part);
        }
        if (thread?.eq(this.state.thread)) {
            await Promise.all([super.load(...arguments), this.tdbLoadPartActivities(part)]);
        }
    },
});

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
