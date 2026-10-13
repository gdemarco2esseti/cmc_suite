import { Component, useState, useRef, useEffect, onWillStart, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { loadBundle } from "@web/core/assets";
import { Layout } from "@web/search/layout";
import { getColor } from "@web/core/colors/colors";
import { cookie } from "@web/core/browser/cookie";
import { Domain } from "@web/core/domain";
import { _t } from "@web/core/l10n/translation";
import { user } from "@web/core/user";

const { DateTime } = luxon;

const MAX_ALERTS = 5;

const PERIOD_KINDS = [
    { kind: "month", label: _t("Month") },
    { kind: "quarter", label: _t("Quarter") },
    { kind: "year", label: _t("Year") },
    { kind: "all", label: _t("All") },
];

const CARD_STATUS = {
    up_to_date: { label: _t("Up to date"), icon: "fa-check", cls: "text-bg-success" },
    outdated: { label: _t("Outdated"), icon: "fa-clock-o", cls: "text-bg-warning" },
    attention: { label: _t("Attention"), icon: "fa-exclamation-triangle", cls: "text-bg-danger" },
    empty: { label: _t("No computation"), icon: "fa-circle-o", cls: "text-bg-light border" },
};

const SEVERITY = {
    danger: { label: _t("Critical"), icon: "fa-times-circle text-danger" },
    warning: { label: _t("Warning"), icon: "fa-exclamation-triangle text-warning" },
    info: { label: _t("Info"), icon: "fa-info-circle text-info" },
};

const TREND_ICON = { up: "fa-arrow-up", down: "fa-arrow-down", flat: "fa-minus" };

export class CmcDashboard extends Component {
    static template = "cmc_base.CmcDashboard";
    static components = { Layout };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.rootRef = useRef("root");
        this.charts = [];
        this.requestId = 0;
        this.periodKinds = PERIOD_KINDS;
        this.state = useState({
            data: null,
            loading: true,
            period: { kind: "year", ref: null },
            showAllAlerts: false,
            tables: {},
        });

        onWillStart(async () => {
            await loadBundle("web.chartjs_lib");
            await this.load();
        });
        useEffect(
            () => this._renderCharts(),
            () => [this.state.data]
        );
        onWillUnmount(() => this._destroyCharts());
    }

    get display() {
        return { controlPanel: {} };
    }

    async load() {
        const requestId = ++this.requestId;
        this.state.loading = true;
        const { kind, ref } = this.state.period;
        const data = await this.orm.call("cmc.cockpit", "get_dashboard_data", [
            ref ? { kind, ref } : { kind },
        ]);
        if (requestId === this.requestId) {
            this.state.data = data;
            this.state.loading = false;
        }
    }

    setKind(kind) {
        if (kind !== this.state.period.kind) {
            this.state.period = { kind, ref: null };
            this.load();
        }
    }

    shift(ref) {
        if (ref) {
            this.state.period = { kind: this.state.period.kind, ref };
            this.load();
        }
    }

    get header() {
        return this.state.data?.header;
    }

    get rangeLabel() {
        const { kind, date_from: from, date_to: to, label } = this.header.period;
        if (kind === "all" || !from || !to) {
            return label;
        }
        const start = DateTime.fromISO(from);
        const end = DateTime.fromISO(to);
        const short = { day: "numeric", month: "short" };
        const startFmt = start.year === end.year ? short : { ...short, year: "numeric" };
        return `${start.toLocaleString(startFmt)} – ${end.toLocaleString({ ...short, year: "numeric" })}`;
    }

    get visibleAlerts() {
        const alerts = this.state.data?.alerts || [];
        return this.state.showAllAlerts ? alerts : alerts.slice(0, MAX_ALERTS);
    }

    get hiddenAlertsCount() {
        return Math.max((this.state.data?.alerts.length || 0) - MAX_ALERTS, 0);
    }

    get showAllLabel() {
        return _t("Show all (%(n)s)", { n: this.state.data.alerts.length });
    }

    cardsOf(area) {
        return this.state.data.cards.filter((card) => card.area === area.key);
    }

    cardStatus(card) {
        return CARD_STATUS[card.status] || CARD_STATUS.empty;
    }

    severity(alert) {
        return SEVERITY[alert.severity] || SEVERITY.info;
    }

    trendIcon(trend) {
        return TREND_ICON[trend.direction] || TREND_ICON.flat;
    }

    trendClass(trend) {
        if (trend.direction === "flat") {
            return "text-muted";
        }
        return trend.favourable ? "text-success" : "text-danger";
    }

    async openAction(xmlid, domain) {
        if (!xmlid) {
            return;
        }
        const act = await this.action.loadAction(xmlid);
        const extra = new Domain(domain || []);
        act.domain = Domain.and([act.domain || [], extra]).toList(user.context);
        return this.action.doAction(act);
    }

    openSetupGuide() {
        return this.action.doAction("cmc_base.action_cmc_setup_guide");
    }

    toggleTable(key) {
        this.state.tables[key] = !this.state.tables[key];
    }

    _destroyCharts() {
        this.charts.forEach((chart) => chart.destroy());
        this.charts = [];
    }

    _renderCharts() {
        this._destroyCharts();
        const root = this.rootRef.el;
        if (!root || !this.state.data) {
            return;
        }
        const scheme = cookie.get("color_scheme");
        const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        for (const spec of this.state.data.charts) {
            const canvas = root.querySelector(`canvas[data-chart="${spec.key}"]`);
            if (!canvas) {
                continue;
            }
            const horizontal = Boolean(spec.horizontal);
            const datasets = spec.datasets.map((ds, i) => ({
                label: ds.label,
                data: ds.data,
                formatted: ds.formatted,
                backgroundColor: spec.datasets.length > 1
                    ? getColor(i, scheme, "odoo")
                    : spec.labels.map((_l, j) => getColor(j, scheme, "odoo")),
                borderRadius: 4,
            }));
            // `Chart` is a global provided by the web.chartjs_lib bundle
            this.charts.push(
                new Chart(canvas, {
                    type: spec.type || "bar",
                    data: { labels: spec.labels, datasets },
                    options: {
                        indexAxis: horizontal ? "y" : "x",
                        maintainAspectRatio: false,
                        animation: reduced ? false : undefined,
                        plugins: {
                            legend: { display: spec.datasets.length > 1 },
                            tooltip: {
                                callbacks: {
                                    label: (ctx) => {
                                        const text = ctx.dataset.formatted?.[ctx.dataIndex] ?? ctx.formattedValue;
                                        return `${ctx.dataset.label}: ${text}`;
                                    },
                                },
                            },
                        },
                        scales: { [horizontal ? "x" : "y"]: { beginAtZero: true } },
                    },
                })
            );
        }
    }
}

registry.category("actions").add("cmc_controller_dashboard", CmcDashboard);
