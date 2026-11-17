/** @odoo-module **/

import { registry } from "@web/core/registry";

export const plmRealtimeReloadService = {
    dependencies: ["bus_service", "action"],
    start(env, { bus_service, action }) {
        const handlePlmUpdate = (payload) => {
            if (!payload || !payload.document_id) {
                return;
            }

            const current = action.currentController;
            if (!current) {
                return;
            }

            // 1. Neu dang xem Form view dung tai lieu nay
            const isViewingDoc = (
                current.props &&
                current.props.resModel === "ir.attachment" &&
                current.props.resId === payload.document_id
            );

            if (isViewingDoc) {
                // Tai lai du lieu form view tuc thi theo thoi gian thuc (real-time) khong can F5
                if (current.model && current.model.root && typeof current.model.root.load === "function") {
                    current.model.root.load();
                } else if (current.model && typeof current.model.load === "function") {
                    current.model.load();
                } else if (typeof action.restore === "function" && current.jsId) {
                    action.restore(current.jsId);
                }
            } else if (current.props && current.props.resModel === "ir.attachment") {
                // 2. Neu dang xem List view tai lieu
                if (current.model && typeof current.model.load === "function") {
                    current.model.load();
                }
            }
        };

        // Dang ky nhan su kien bus qua WebSocket / Longpolling
        if (bus_service && typeof bus_service.subscribe === "function") {
            try {
                bus_service.subscribe("plm_document_updated", handlePlmUpdate);
            } catch (e) {}
        }

        if (bus_service && typeof bus_service.addEventListener === "function") {
            bus_service.addEventListener("notification", ({ detail: notifications }) => {
                if (Array.isArray(notifications)) {
                    for (const notif of notifications) {
                        if (notif.type === "plm_document_updated") {
                            handlePlmUpdate(notif.payload);
                        }
                    }
                }
            });
        }

        if (bus_service && typeof bus_service.addChannel === "function") {
            try {
                bus_service.addChannel("plm_document_channel");
            } catch (e) {}
        }
    },
};

registry.category("services").add("plm_realtime_reload", plmRealtimeReloadService);
