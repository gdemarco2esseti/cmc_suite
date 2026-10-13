import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("cmc_controller_dashboard_tour", {
    url: "/odoo/action-cmc_base.action_cmc_cockpit_dashboard",
    steps: () => [
        {
            content: "Dashboard renders",
            trigger: ".o_cmc_dashboard .o_cmc_dash_period",
        },
        {
            content: "Pick the month period",
            trigger: ".o_cmc_dash_period button[data-kind='month']",
            run: "click",
        },
        {
            content: "Month segment is pressed",
            trigger: ".o_cmc_dash_period button[data-kind='month'][aria-pressed='true']",
        },
        {
            content: "Data reloaded",
            trigger: ".o_cmc_dash_body[aria-busy='false']",
        },
        {
            content: "Open the setup guide",
            trigger: ".o_cmc_dash_guide_btn",
            run: "click",
        },
        {
            content: "Setup guide renders",
            trigger: ".o_cmc_cockpit .o_cmc_body",
        },
        {
            content: "Go back to the dashboard",
            trigger: ".o_back_button",
            run: "click",
        },
        {
            content: "Dashboard is back",
            trigger: ".o_cmc_dashboard .o_cmc_dash_period",
        },
    ],
});
