from odoo import models
from odoo.fields import Domain

from odoo.addons.cmc_base.models.cmc_utils import month_start


class CmcPlanVersion(models.Model):
    _inherit = "cmc.plan.version"

    def _cmc_scenario_evaluators(self):
        entry = {"key": "budget_lines", "name": self.env._("Budget lines"), "versioned": True, "prepare": self._cmc_budget_scenario_prepare}
        return super()._cmc_scenario_evaluators() + [entry]

    def _cmc_budget_scenario_prepare(self, version):
        """Inputs = the figures of the budget lines per center / element / month / kind; amounts signed (revenue +, cost -)."""
        groups = version.env["cmc.budget.line"]._read_group(
            Domain("version_id", "=", version.id), ["center_id", "element_id", "kind", "date:month"], ["figure:sum"]
        )
        inputs, cells = {}, {}
        for center, element, kind, month, figure in groups:
            path = "%s/%s/%s/%s" % (center.id, element.id, month_start(month).strftime("%Y-%m"), kind)
            inputs[path] = figure
            cells[path] = ((center.id or False, element.id or False, month), 1.0 if kind == "revenue" else -1.0)

        def evaluate(values):
            out = {}
            for path, (key, sign) in cells.items():
                out[key] = out.get(key, 0.0) + sign * values.get(path, 0.0)
            return out

        return {"inputs": inputs, "evaluate": evaluate}
