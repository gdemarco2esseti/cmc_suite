import { Component, useState, useRef, onWillStart, onMounted, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { loadBundle } from "@web/core/assets";
import { Layout } from "@web/search/layout";
import { getColor } from "@web/core/colors/colors";
import { cookie } from "@web/core/browser/cookie";

export class CmcCockpit extends Component {
    static template = "cmc_base.CmcCockpit";
    static components = { Layout };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.canvasRef = useRef("chartCanvas");
        this.state = useState({
            loading: true,
            data: { pipeline: [], methods: [], kpis: [], chart: {} },
        });

        onWillStart(async () => {
            // Chart.js ships as a lazy bundle in v19 — load before use
            await loadBundle("web.chartjs_lib");
            this.state.data = await this.orm.call("cmc.cockpit", "get_cockpit_data", []);
            this.state.loading = false;
        });
        onMounted(() => this._renderChart());
        onWillUnmount(() => this.chart?.destroy());
    }

    get display() {
        // inherit the standard control panel (breadcrumb + chrome)
        return { controlPanel: {} };
    }

    get hasChart() {
        return (this.state.data.chart?.values || []).some((v) => v > 0);
    }

    openStep(xmlid) {
        return this.action.doAction(xmlid);
    }

    _renderChart() {
        if (this.state.loading || !this.hasChart || !this.canvasRef.el) {
            return;
        }
        this.chart?.destroy();
        const scheme = cookie.get("color_scheme");
        const chart = this.state.data.chart;
        // `Chart` is a global provided by the web.chartjs_lib bundle
        this.chart = new Chart(this.canvasRef.el, {
            type: "bar",
            data: {
                labels: chart.labels,
                datasets: [{
                    data: chart.values,
                    backgroundColor: chart.values.map((_, i) => getColor(i, scheme, "odoo")),
                    borderRadius: 4,
                }],
            },
            options: {
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: { y: { beginAtZero: true } },
            },
        });
    }
}

registry.category("actions").add("cmc_cockpit", CmcCockpit);
