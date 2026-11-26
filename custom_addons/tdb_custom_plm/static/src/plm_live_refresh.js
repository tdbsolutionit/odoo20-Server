/** @odoo-module **/

import { onMounted, onWillUnmount } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { patch } from "@web/core/utils/patch";
import { FormController } from "@web/views/form/form_controller";
import { ListController } from "@web/views/list/list_controller";
import { KanbanController } from "@web/views/kanban/kanban_controller";
import { LiveRefreshQueue } from "./live_refresh_queue";

const MODELS = new Set(["ir.attachment", "product.product", "product.template", "mrp.bom", "mail.activity"]);
const EVENT = "tdb_plm/changed";

function usePlmLiveRefresh(controller) {
    if (!MODELS.has(controller.props.resModel)) {
        return;
    }
    const bus = useService("bus_service");
    let mounted = false;
    let unsubscribe;
    let fallback;
    let loggedError = false;
    const queue = new LiveRefreshQueue({
        async canRefresh() {
            const root = controller.model?.root;
            if (!mounted || document.hidden || !root || root.isNew || root.editedRecord) {
                return false;
            }
            if (controller.quickCreateState?.isOpen || document.querySelector('.o_kanban_quick_create')) {
                return false;
            }
            // Do not interrupt inline edits, bulk selections, typing or focus in
            // an input whose pending value has not reached the model yet.
            if (root.selection?.length || root.selectedRecords?.length) {
                return false;
            }
            const focused = document.activeElement;
            // The search bar can keep focus after a filter is applied. An
            // empty search input has no pending text and can safely stay focused.
            const idleSearch = focused?.matches(".o_action_manager .o_searchview_input")
                && !focused.value;
            if (!idleSearch && focused?.closest(
                ".o_action_manager input, .o_action_manager textarea, .o_action_manager select, " +
                ".o_action_manager [contenteditable='true'], .modal"
            )) {
                return false;
            }
            if (typeof root.isDirty === "function" && await root.isDirty()) {
                return false;
            }
            return mounted && root === controller.model.root && !root.dirty;
        },
        async refresh() {
            // Reuses the model configuration: domain, filters, grouping and
            // pager are retained. No page reload, save or discard is invoked.
            await controller.model.load();
            loggedError = false;
        },
        onError(error) {
            if (!loggedError) {
                console.warn("PLM live refresh will retry after a failed reload.", error);
                loggedError = true;
            }
        },
    });
    const resume = () => queue.request();
    const resumePending = () => {
        if (queue.pending) {
            queue.request();
        }
    };
    onMounted(() => {
        mounted = true;
        unsubscribe = bus.subscribe(EVENT, (payload) => {
            if (payload.models?.includes(controller.props.resModel)) {
                queue.request();
            }
        });
        // Windows deployments may briefly lose the websocket. Polling and
        // refocusing catch missed changes; dirty records are still deferred.
        fallback = setInterval(resume, 15000);
        window.addEventListener("focus", resume);
        document.addEventListener("visibilitychange", resume);
        document.addEventListener("focusout", resumePending);
        bus.start().catch((error) => console.warn("PLM push unavailable; using fallback refresh.", error));
    });
    onWillUnmount(() => {
        mounted = false;
        queue.stop();
        unsubscribe?.();
        clearInterval(fallback);
        window.removeEventListener("focus", resume);
        document.removeEventListener("visibilitychange", resume);
        document.removeEventListener("focusout", resumePending);
    });
}

for (const Controller of [FormController, ListController, KanbanController]) {
    patch(Controller.prototype, {
        setup() {
            super.setup(...arguments);
            usePlmLiveRefresh(this);
        },
    });
}
