/** @odoo-module **/

import { ActivityMenu } from "@mail/core/web/activity_menu";
import { patch } from "@web/core/utils/patch";

patch(ActivityMenu.prototype, {
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
