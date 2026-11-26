/** @odoo-module **/

/** Coalesce bursts and defer reloads while an edit or another reload is active. */
export class LiveRefreshQueue {
    constructor({ canRefresh, refresh, onError = () => {}, delay = 400, retryDelay = 2000 }) {
        Object.assign(this, { canRefresh, refresh, onError, delay, retryDelay });
        this.pending = false;
        this.running = false;
        this.stopped = false;
        this.timer = null;
    }

    request() {
        if (!this.stopped) {
            this.pending = true;
            this.schedule(this.delay);
        }
    }

    schedule(delay) {
        if (!this.stopped && !this.running && this.timer === null) {
            this.timer = setTimeout(() => this.flush(), delay);
        }
    }

    async flush() {
        if (this.timer !== null) {
            clearTimeout(this.timer);
            this.timer = null;
        }
        if (this.stopped || this.running || !this.pending) {
            return;
        }
        this.running = true;
        try {
            if (!(await this.canRefresh()) || this.stopped) {
                return;
            }
            this.pending = false;
            await this.refresh();
        } catch (error) {
            this.pending = true;
            this.onError(error);
        } finally {
            this.running = false;
            if (this.pending) {
                this.schedule(this.retryDelay);
            }
        }
    }

    stop() {
        this.stopped = true;
        this.pending = false;
        clearTimeout(this.timer);
        this.timer = null;
    }
}
