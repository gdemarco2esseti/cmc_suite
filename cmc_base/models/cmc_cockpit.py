from odoo import api, models
from odoo.tools import formatLang

# Foundation pipeline: mandatory, sequential, always present. Numbered at
# render time (1..N) so there are never gaps.
# (key, label, model, domain, action xml-id)
BASE_STEPS = [
    ("centers", "Centri di costo", "account.analytic.account",
     ["|", ("cmc_center_type", "!=", False), ("cmc_is_period", "=", True)],
     "cmc_base.action_cmc_cost_center"),
    ("costs", "Registrazione costi", "account.analytic.line",
     [("cmc_behavior", "!=", False)], "cmc_base.action_cmc_analytic_lines"),
    ("services", "Servizi inter-centro", "cmc.center.service",
     [], "cmc_base.action_cmc_center_service"),
    ("closing", "Ribaltamento", "cmc.closing.run",
     [("state", "=", "done")], "cmc_base.action_cmc_closing_run"),
]

# Costing methodologies: PARALLEL alternatives applied on the same cost base.
# Only the installed ones appear (runtime detection) — a deployment typically
# picks the methodology it needs, so no fixed numbering / no gaps.
METHOD_STEPS = [
    ("costing", "Costo pieno", "cmc.costing.run",
     [("state", "=", "done")], "cmc_full_costing.action_cmc_costing_run"),
    ("cvr", "Direct costing / CVR", "cmc.cvr.analysis",
     [], "cmc_direct_costing.action_cmc_cvr_analysis"),
    ("abc", "Activity-Based Costing", "cmc.abc.run",
     [("state", "=", "done")], "cmc_abc.action_cmc_abc_run"),
    ("joint", "Costi congiunti", "cmc.joint.run",
     [("state", "=", "done")], "cmc_joint_costs.action_cmc_joint_run"),
    ("tdabc", "Time-Driven ABC", "cmc.tdabc.run",
     [("state", "=", "done")], "cmc_tdabc.action_cmc_tdabc_run"),
]

DONE_RUN_KEYS = {"closing", "costing", "abc", "joint", "tdabc"}


class CmcCockpit(models.TransientModel):
    _name = "cmc.cockpit"
    _description = "Cost Management Cockpit"

    def _cmc_collect(self, defs):
        """Resolve step definitions against installed models (runtime detection)."""
        out = []
        for key, label, model, domain, action in defs:
            if model not in self.env:
                continue  # capillary module not installed
            count = self.env[model].search_count(domain)
            out.append({
                "key": key, "label": label, "count": count,
                "state": "done" if count else "todo", "action": action,
            })
        return out

    @api.model
    def get_cockpit_data(self):
        """JSON-able payload for the OWL cockpit. The foundation `pipeline` is
        sequential and numbered at render time (no gaps); `methods` are the
        installed costing methodologies, shown as parallel alternatives applied
        on the same cost base. Runs with the caller's ACL/company scope — no
        sudo, only primitives returned."""
        pipeline = self._cmc_collect(BASE_STEPS)
        # sequential numbering + first-not-done = "next"
        for idx, step in enumerate(pipeline, start=1):
            step["num"] = idx
        for step in pipeline:
            if step["state"] == "todo":
                step["state"] = "next"
                break

        # methodologies: parallel; "done" if already used, else "available"
        methods = self._cmc_collect(METHOD_STEPS)
        base_ready = all(s["state"] == "done" for s in pipeline)
        for m in methods:
            if m["state"] == "todo":
                # unlocked once the cost base is built, otherwise greyed
                m["state"] = "available" if base_ready else "locked"

        currency = self.env.company.currency_id
        AL = self.env["account.analytic.line"]

        # total booked cost (classified lines), as a magnitude
        cost_groups = AL._read_group(
            [("cmc_behavior", "!=", False)], aggregates=["amount:sum"]
        )
        total_cost = abs((cost_groups[0][0] or 0.0) if cost_groups else 0.0)

        all_steps = pipeline + methods
        runs_done = sum(s["count"] for s in all_steps if s["key"] in DONE_RUN_KEYS)
        products_costed = (
            self.env["cmc.costing.result"].search_count([])
            if "cmc.costing.result" in self.env else 0
        )
        centers = next((s["count"] for s in pipeline if s["key"] == "centers"), 0)

        kpis = [
            {"key": "centers", "label": "Centri di costo", "value": str(centers)},
            {"key": "cost", "label": "Costi registrati",
             "value": formatLang(self.env, total_cost, currency_obj=currency)},
            {"key": "runs", "label": "Elaborazioni", "value": str(runs_done)},
            {"key": "products", "label": "Prodotti a costo",
             "value": str(products_costed)},
        ]

        # cost-by-element chart (stored FK -> _read_group is fine)
        el_groups = AL._read_group(
            [("cmc_cost_element_id", "!=", False)],
            groupby=["cmc_cost_element_id"], aggregates=["amount:sum"],
        )
        chart = {
            "labels": [el.display_name for el, _ in el_groups],
            "values": [abs(amount or 0.0) for _, amount in el_groups],
        }

        return {"pipeline": pipeline, "methods": methods,
                "kpis": kpis, "chart": chart}
