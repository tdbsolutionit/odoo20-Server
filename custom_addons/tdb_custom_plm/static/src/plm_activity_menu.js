/** @odoo-module **/

import { ActivityMenu } from "@mail/core/web/activity_menu";
import { onWillDestroy } from "@odoo/owl";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { LiveRefreshQueue } from "./live_refresh_queue";

patch(ActivityMenu.prototype, {
    setup() {
        super.setup(...arguments);
        const bus = useService("bus_service");
        const queue = new LiveRefreshQueue({
            canRefresh: () => true,
            refresh: () => this.store.fetchStoreData("systray_get_activities"),
        });
        const unsubscribe = bus.subscribe("tdb_plm/activity_counter_changed", () => queue.request());
        const refreshWhenVisible = () => {
            if (!document.hidden) {
                queue.request();
            }
        };
        const interval = setInterval(refreshWhenVisible, 10000);
        document.addEventListener("visibilitychange", refreshWhenVisible);
        bus.start().catch(() => {
            // The foreground refresh also covers a disconnected websocket.
        });
        onWillDestroy(() => {
            unsubscribe?.();
            clearInterval(interval);
            document.removeEventListener("visibilitychange", refreshWhenVisible);
            queue.stop();
        });
    },

    openActivityGroup(group, filter = "all", newWindow) {
        if (!group.tdb_plm_action_id) {
            return super.openActivityGroup(group, filter, newWindow);
        }
        this.dropdown.close();
        const context = { search_default_tdb_my_plm_activities: 1 };
        if (filter === "overdue") {
            context.search_default_tdb_plm_late = 1;
        } else if (filter === "today") {
            context.search_default_tdb_plm_today = 1;
        } else if (filter === "upcoming_all") {
            context.search_default_tdb_plm_future = 1;
        }
        return this.action.doAction(group.tdb_plm_action_id, {
            newWindow,
            clearBreadcrumbs: true,
            additionalContext: context,
        });
    },
});
