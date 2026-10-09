from collections import defaultdict

from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.fields import Domain

from odoo.addons.cmc_base.models.cmc_closing import CmcClosingRun as BaseClosingRun
from odoo.addons.cmc_base.models.cmc_utils import month_start


class CmcClosingRun(models.Model):
    _inherit = "cmc.closing.run"

    _cmc_input_fields = (*BaseClosingRun._cmc_input_fields, "allocation_basis", "gpk_version_id")

    allocation_basis = fields.Selection(
        [("actual", "Actual cost"), ("dual_rate", "Dual rate (GPK)")], required=True, default="actual",
        help="Dual rate charges the fixed budget on planned usage and the variable budget on actual usage; "
        "the difference to the actual cost stays on the support center.",
    )
    gpk_version_id = fields.Many2one(
        "cmc.plan.version", string="Budget Version", check_company=True,
        domain="[('version_type', 'in', ('budget', 'forecast')), ('state', '=', 'done')]",
    )

    @api.constrains("allocation_basis", "gpk_version_id")
    def _check_gpk_version(self):
        for run in self.filtered(lambda r: r.allocation_basis == "dual_rate"):
            if run.gpk_version_id.version_type not in ("budget", "forecast"):
                raise ValidationError(self.env._("A dual rate closing needs a budget or forecast version."))

    def _cmc_compute(self):
        if self.allocation_basis == "dual_rate":
            # raises unless the version is approved; locks it against a reset while this closing is computed
            self._cmc_record_usage(self.gpk_version_id)
        return super()._cmc_compute()

    def _run_sequential(self, to_close, initial, prop_full, prop_final):
        if self.allocation_basis != "dual_rate" or self.method != "direct":
            return super()._run_sequential(to_close, initial, prop_full, prop_final)
        charges = self._cmc_gpk_charges(to_close)
        # providers without a pooled budget line in the version keep the actual basis
        received, distributed = super()._run_sequential(to_close.filtered(lambda c: c.id not in charges), initial, prop_full, prop_final)
        for provider_id, shares in charges.items():
            distributed[provider_id] = sum(shares.values())
            for receiver_id, amount in shares.items():
                received[receiver_id] += amount
        return received, distributed

    def _build_cost_analysis(self, centers, to_close, final_ids, received):
        if self.allocation_basis != "dual_rate":
            return super()._build_cost_analysis(centers, to_close, final_ids, received)
        residual = {line.center_id.id: line.cost_residual for line in self.line_ids if line.center_id.id in to_close and line.cost_residual}
        # why: the matrix must conserve the total, so what the dual rate leaves on a support center shows in its post column
        kept = {cid: received.get(cid, 0.0) + amount for cid, amount in residual.items()}
        return super()._build_cost_analysis(centers, to_close, final_ids | set(residual), {**received, **kept})

    def _cmc_gpk_charges(self, to_close):
        """{provider id: {receiver id: charge}}: per pool, fixed budget x planned share + variable budget rate x actual service."""
        version, company = self.gpk_version_id, self.company_id
        pools = defaultdict(lambda: {"fixed": 0.0, "variable": 0.0})
        for provider, driver, behavior, amount in self.env["cmc.budget.line"]._read_group(
            Domain([
                ("version_id", "=", version.id), ("kind", "=", "cost"), ("center_id", "in", to_close.ids), ("driver_id", "!=", False),
                ("date", ">=", month_start(self.date_from)), ("date", "<=", self.date_to),
            ]),
            ["center_id", "driver_id", "behavior"], ["amount_company:sum"],
        ):
            pools[(provider, driver)][behavior] += -amount
        if not pools:
            return {}
        providers = self.env["account.analytic.account"].union(*(provider for provider, _driver in pools))
        drivers = self.env["cmc.cost.driver"].union(*(driver for _provider, driver in pools))
        planned = defaultdict(lambda: defaultdict(float))
        for driver, receiver, quantity in self.env["cmc.measure.quantity"]._read_group(
            Domain([
                ("version_id", "=", version.id), ("driver_id", "in", drivers.ids), ("center_id", "!=", False),
                ("date", ">=", month_start(self.date_from)), ("date", "<=", self.date_to),
            ]),
            ["driver_id", "center_id"], ["quantity:sum"],
        ):
            if receiver.cmc_center_type == "final":
                planned[driver][receiver] += quantity
        served = {
            (provider, receiver, driver): quantity
            for provider, receiver, driver, quantity in self.env["cmc.center.service"]._read_group(
                Domain([("provider_id", "in", providers.ids), ("driver_id", "in", drivers.ids), ("company_id", "in", [False, company.id])]),
                ["provider_id", "receiver_id", "driver_id"], ["quantity:sum"],
            )
        }
        currency = company.currency_id
        charges = defaultdict(lambda: defaultdict(float))
        for (provider, driver), pool in pools.items():
            total = sum(planned[driver].values())
            if not total:
                continue
            for receiver, planned_qty in planned[driver].items():
                charge = pool["fixed"] * planned_qty / total + pool["variable"] / total * served.get((provider, receiver, driver), 0.0)
                charges[provider.id][receiver.id] += currency.round(charge)
        return charges
