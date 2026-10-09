from odoo import api, models
from odoo.tools.translate import LazyTranslate

_lt = LazyTranslate(__name__)

# (code, name, measure xmlid or formula, unit, direction, perspective); a "." in the source = measure
LIBRARY = [
    ("mrp_produced", _lt("Produced quantity"), "cmc_mrp.cmc_measure_produced_qty", "number", "higher", "internal"),
    ("mrp_scrapped", _lt("Scrapped quantity"), "cmc_mrp.cmc_measure_scrap_qty", "number", "lower", "internal"),
    ("mrp_reworked", _lt("Reworked quantity"), "cmc_kpi_mrp.cmc_measure_rework_qty", "number", "lower", "internal"),
    ("mrp_wo_minutes", _lt("Work order minutes"), "cmc_kpi_mrp.cmc_measure_wo_minutes", "number", "lower", "internal"),
    ("mrp_value_added", _lt("Value-added minutes"), "cmc_kpi_mrp.cmc_measure_value_added_minutes", "number", "higher", "internal"),
    ("mrp_lead_minutes", _lt("Manufacturing lead minutes"), "cmc_kpi_mrp.cmc_measure_mo_lead_minutes", "number", "lower", "internal"),
    ("mrp_mo_done", _lt("Manufacturing orders done"), "cmc_kpi_mrp.cmc_measure_mo_done", "number", "higher", "internal"),
    ("mrp_first_pass", _lt("First-pass units"), "mrp_produced - mrp_reworked", "number", "higher", "internal"),
    ("mrp_cycle_time", _lt("Cycle time (min per unit)"), "mrp_wo_minutes / mrp_produced", "number", "lower", "internal"),
    ("mrp_lead_time", _lt("Manufacturing lead time (days)"), "mrp_lead_minutes / mrp_mo_done / 1440", "days", "lower", "internal"),
    ("mrp_ppm", _lt("Defective parts per million"), "mrp_scrapped / mrp_produced * 1000000", "number", "lower", "internal"),
    ("mrp_fpy", _lt("First-pass yield (%)"), "(mrp_first_pass - mrp_reworked - mrp_scrapped) / mrp_first_pass * 100", "percent", "higher", "internal"),
    ("mrp_mce", _lt("Manufacturing cycle efficiency (%)"), "mrp_value_added / mrp_lead_minutes * 100", "percent", "higher", "internal"),
    ("delivery_shipments", _lt("Shipments"), "cmc_kpi.cmc_measure_shipments", "number", "higher", "customer"),
    ("delivery_lead_minutes", _lt("Delivery lead minutes"), "cmc_kpi_mrp.cmc_measure_delivery_lead_minutes", "number", "lower", "customer"),
    ("delivery_lead_time", _lt("Delivery lead time (days)"), "delivery_lead_minutes / delivery_shipments / 1440", "days", "lower", "customer"),
]


class CmcKpi(models.Model):
    _inherit = "cmc.kpi"

    @api.model
    def _cmc_load_mrp_library(self, company=None):
        """Create the missing manufacturing / delivery KPIs of the company (by code) and return all of them."""
        company = company or self.env.company
        kpis = self.with_company(company).with_context(active_test=False)
        existing = {kpi.code: kpi for kpi in kpis.search([("company_id", "=", company.id), ("code", "in", [row[0] for row in LIBRARY])])}
        vals_list = []
        for sequence, (code, name, source, unit, direction, perspective) in enumerate(LIBRARY, start=1):
            if code in existing:
                continue
            vals = {"code": code, "name": str(name), "company_id": company.id, "unit": unit, "direction": direction,
                    "perspective": perspective, "sequence": 100 + sequence}
            if "." in source:
                vals.update(source_type="measure", driver_id=self.env.ref(source).id)
            else:
                vals.update(source_type="formula", formula=source)
            vals_list.append(vals)
        created = kpis.create(vals_list) if vals_list else kpis.browse()
        by_code = {**existing, **{kpi.code: kpi for kpi in created}}
        return self.browse().concat(*[by_code[row[0]] for row in LIBRARY])

    def action_cmc_load_mrp_library(self):
        self._cmc_load_mrp_library()
        return self.env["ir.actions.act_window"]._for_xml_id("cmc_kpi.action_cmc_kpi")
