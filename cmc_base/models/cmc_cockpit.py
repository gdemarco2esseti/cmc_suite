from odoo import api, models
from odoo.tools import formatLang
from odoo.tools.translate import LazyTranslate

# module-level labels: lazily translated so they are extractable AND
# resolved in the reader's language at render time
_lt = LazyTranslate(__name__)

# Foundation pipeline: mandatory, sequential, always present. Numbered at
# render time (1..N) so there are never gaps.
# (key, label, model, domain, action xml-id)
BASE_STEPS = [
    ("centers", _lt("Cost Centers"), "account.analytic.account",
     ["|", ("cmc_center_type", "!=", False), ("cmc_is_period", "=", True)],
     "cmc_base.action_cmc_cost_center"),
    ("costs", _lt("Cost Entry"), "account.analytic.line",
     [("cmc_behavior", "!=", False)], "cmc_base.action_cmc_analytic_lines"),
    ("services", _lt("Inter-Center Services"), "cmc.center.service",
     [], "cmc_base.action_cmc_center_service"),
    ("closing", _lt("Center Closing"), "cmc.closing.run",
     [("state", "=", "done")], "cmc_base.action_cmc_closing_run"),
]

# Costing methodologies: PARALLEL alternatives applied on the same cost base.
# Only the installed ones appear (runtime detection) — a deployment typically
# picks the methodology it needs, so no fixed numbering / no gaps.
METHOD_STEPS = [
    ("costing", _lt("Full Costing"), "cmc.costing.run",
     [("state", "=", "done")], "cmc_full_costing.action_cmc_costing_run"),
    ("cvr", _lt("Direct Costing / CVP"), "cmc.cvr.analysis",
     [], "cmc_direct_costing.action_cmc_cvr_analysis"),
    ("abc", _lt("Activity-Based Costing"), "cmc.abc.run",
     [("state", "=", "done")], "cmc_abc.action_cmc_abc_run"),
    ("joint", _lt("Joint Costs"), "cmc.joint.run",
     [("state", "=", "done")], "cmc_joint_costs.action_cmc_joint_run"),
    ("tdabc", _lt("Time-Driven ABC"), "cmc.tdabc.run",
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
            Model = self.env[model]
            if "company_id" in Model._fields:
                # shared (no company) records count for everyone
                domain = domain + [("company_id", "in", [False, self.env.company.id])]
            count = Model.search_count(domain)
            out.append({
                "key": key, "label": str(label), "count": count,
                "state": "done" if count else "todo", "action": action,
            })
        return out

    @api.model
    @api.readonly
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
        company_domain = [("company_id", "=", self.env.company.id)]

        # total booked cost (classified lines), as a magnitude
        cost_groups = AL._read_group(
            [("cmc_behavior", "!=", False)] + company_domain, aggregates=["amount:sum"]
        )
        total_cost = abs((cost_groups[0][0] or 0.0) if cost_groups else 0.0)

        all_steps = pipeline + methods
        runs_done = sum(s["count"] for s in all_steps if s["key"] in DONE_RUN_KEYS)
        products_costed = (
            self.env["cmc.costing.result"].search_count(company_domain)
            if "cmc.costing.result" in self.env else 0
        )
        centers = next((s["count"] for s in pipeline if s["key"] == "centers"), 0)

        kpis = [
            {"key": "centers", "label": self.env._("Cost Centers"),
             "value": str(centers)},
            {"key": "cost", "label": self.env._("Booked Costs"),
             "value": formatLang(self.env, total_cost, currency_obj=currency)},
            {"key": "runs", "label": self.env._("Computations"),
             "value": str(runs_done)},
            {"key": "products", "label": self.env._("Costed Products"),
             "value": str(products_costed)},
        ]

        # cost-by-element chart (stored FK -> _read_group is fine)
        el_groups = AL._read_group(
            [("cmc_cost_element_id", "!=", False)] + company_domain,
            groupby=["cmc_cost_element_id"], aggregates=["amount:sum"],
        )
        chart = {
            "labels": [el.display_name for el, _ in el_groups],
            "values": [abs(amount or 0.0) for _, amount in el_groups],
        }

        return {"pipeline": pipeline, "methods": methods,
                "kpis": kpis, "chart": chart}
