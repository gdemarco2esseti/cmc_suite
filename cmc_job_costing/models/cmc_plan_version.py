from collections import defaultdict

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from odoo.addons.cmc_base.models import cmc_utils

# locked once approved; kept apart from _cmc_input_fields, which other modules redefine whole
EVM_INPUTS = ("project_id", "cmc_rate_driver_id", "management_reserve_percent")


class CmcPlanVersion(models.Model):
    _inherit = "cmc.plan.version"

    version_type = fields.Selection(selection_add=[("baseline", "Baseline")], ondelete={"baseline": "set default"})
    project_id = fields.Many2one("project.project", string="Job", check_company=True, index=True)
    cmc_rate_driver_id = fields.Many2one("cmc.cost.driver", string="Rate Card Unit", help="The unit of the hourly rates of the rate card.")
    management_reserve_percent = fields.Float(help="Percent of the cost baseline, kept outside it (PCM).")
    estimate_line_ids = fields.One2many("cmc.evm.estimate.line", "version_id", copy=True)
    baseline_line_ids = fields.One2many("cmc.evm.baseline.line", "version_id")
    risk_ids = fields.One2many("cmc.evm.risk", "version_id", copy=True)
    change_request_ids = fields.One2many("cmc.evm.change.request", "baseline_id")
    estimate_expected = fields.Monetary(compute="_compute_estimate_totals", store=True)
    estimate_sigma = fields.Monetary(compute="_compute_estimate_totals", store=True)
    estimate_low = fields.Monetary(compute="_compute_estimate_totals", store=True)
    estimate_high = fields.Monetary(compute="_compute_estimate_totals", store=True)
    bac = fields.Monetary(string="BAC", compute="_compute_bac", store=True, help="Budget at completion of the work packages.")
    contingency_reserve = fields.Monetary(compute="_compute_contingency_reserve", store=True)
    cost_baseline = fields.Monetary(compute="_compute_reserves", store=True)
    management_reserve = fields.Monetary(compute="_compute_reserves", store=True)
    project_budget = fields.Monetary(compute="_compute_reserves", store=True)

    @api.depends(
        "estimate_line_ids.cost_optimistic", "estimate_line_ids.cost_most_likely", "estimate_line_ids.cost_pessimistic",
    )
    def _compute_estimate_totals(self):
        for version in self:
            lines = version.estimate_line_ids
            triples = zip(lines.mapped("cost_optimistic"), lines.mapped("cost_most_likely"), lines.mapped("cost_pessimistic"), strict=True)
            version.estimate_expected, version.estimate_sigma, version.estimate_low, version.estimate_high = cmc_utils.pert_total(triples)

    @api.depends("baseline_line_ids.planned_value")
    def _compute_bac(self):
        for version in self:
            version.bac = sum(version.baseline_line_ids.mapped("planned_value"))

    @api.depends("risk_ids.emv")
    def _compute_contingency_reserve(self):
        for version in self:
            version.contingency_reserve = sum(version.risk_ids.mapped("emv"))

    @api.depends("bac", "contingency_reserve", "management_reserve_percent")
    def _compute_reserves(self):
        for version in self:
            version.cost_baseline = version.bac + version.contingency_reserve
            version.management_reserve = version.currency_id.round(version.cost_baseline * version.management_reserve_percent / 100.0)
            version.project_budget = version.cost_baseline + version.management_reserve

    @api.constrains("version_type", "project_id")
    def _check_cmc_project(self):
        if any(v.version_type == "baseline" and not v.project_id for v in self):
            raise ValidationError(self.env._("A baseline is the plan of one job."))

    @api.constrains("management_reserve_percent")
    def _check_management_reserve_percent(self):
        if any(v.management_reserve_percent < 0 for v in self):
            raise ValidationError(self.env._("The management reserve cannot be negative."))

    def write(self, vals):
        if not set(EVM_INPUTS).isdisjoint(vals):
            self._cmc_check_editable()
        return super().write(vals)

    def action_approve(self):
        self._cmc_check_manager()
        for baseline in self.filtered(lambda v: v.version_type == "baseline" and v.state == "draft"):
            if self.search_count([("parent_id", "=", baseline.id), ("version_type", "=", "baseline")]):
                raise UserError(self.env._("%s has a newer re-baseline: approve that one.", baseline.display_name))
            if not baseline.estimate_line_ids:
                raise UserError(self.env._("%s has no estimate line: nothing to baseline.", baseline.display_name))
            baseline._cmc_evm_plan()
        return super().action_approve()

    def action_cmc_evm_plan(self):
        self._cmc_check_editable()
        for baseline in self:
            baseline._cmc_evm_plan()
        return True

    def _cmc_evm_plan(self):
        """Rate the hours from the rate card, then replace the time-phased baseline (PV per task and month)."""
        self.ensure_one()
        lines = self.estimate_line_ids
        self._cmc_evm_rate_lines(lines.filtered("hours_pessimistic"))
        currency = self.currency_id
        phased = defaultdict(float)
        for line in lines:
            parts = cmc_utils.phase_by_month(currency.round(line.cost_expected), line.date_start, line.date_end, currency.rounding)
            for month, amount in parts.items():
                phased[line.task_id.id, month] += amount
        # why sudo: the baseline rows are written by the engine only, whoever prepares the estimate
        self.baseline_line_ids.sudo().unlink()
        self.env["cmc.evm.baseline.line"].sudo().create([
            {"version_id": self.id, "task_id": task_id, "date": month, "planned_value": amount}
            for (task_id, month), amount in phased.items()
        ])

    def _cmc_evm_rate_lines(self, lines):
        """Set the hourly rate of `lines` from the rate card of THIS version (one search for all of them)."""
        if not lines:
            return
        rates = self.env["cmc.rate"].search([
            ("holder_model", "=", "account.analytic.account"), ("holder_id", "in", lines.center_id.ids),
            ("driver_id", "=", self.cmc_rate_driver_id.id), ("component", "=", "total"),
            ("origin_model", "=", self._name), ("origin_id", "=", self.id), ("company_id", "=", self.company_id.id),
        ], order="id")
        card = {(rate.holder_id, rate.date): rate.value for rate in rates}
        slots = {line: (line.center_id.id, cmc_utils.month_start(line.date_start)) for line in lines}
        for line, slot in slots.items():
            if slot not in card:
                raise UserError(self.env._(
                    "No rate on the rate card for %(center)s in %(month)s.", center=line.center_id.display_name, month=slot[1],
                ))
        for rate, group in lines.grouped(lambda line: card[slots[line]]).items():
            group.hourly_rate = rate
