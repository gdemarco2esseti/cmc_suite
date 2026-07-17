/** @odoo-module **/
import { registry } from "@web/core/registry";
import { Component } from "@odoo/owl";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

export class CmcReclassTable extends Component {
    static template = "cmc_base.CmcReclassTable";
    static props = { ...standardFieldProps };

    get data() {
        return this.props.record.data[this.props.name] || null;
    }
    get ccy() {
        return (this.data && this.data.currency) || "";
    }
    fmt(value, kind) {
        if (value === null || value === undefined) {
            return "";
        }
        if (kind === "percent") {
            return value.toFixed(1).replace(".", ",") + "%";
        }
        const s = Math.abs(value).toLocaleString("it-IT", {
            minimumFractionDigits: 0,
            maximumFractionDigits: 0,
        });
        const sign = value < 0 ? "–" : "";
        return this.ccy ? `${sign}${this.ccy} ${s}` : `${sign}${s}`;
    }
}

export const cmcReclassTable = {
    component: CmcReclassTable,
    supportedTypes: ["json"],
};
registry.category("fields").add("cmc_reclass", cmcReclassTable);
