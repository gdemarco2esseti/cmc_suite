from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .cmc_utils import month_range


class CmcPlanVersion(models.Model):
    _name = "cmc.plan.version"
    _inherit = ["cmc.run.mixin"]
    _description = "Plan Version"
    _order = "date_from desc, id desc"
    _check_company_auto = True

    _cmc_input_fields = ("date_from", "date_to", "version_type", "company_id", "currency_id", "currency_rate")
    _cmc_rates_are_inputs = True

    # why: same keys as the run mixin (the lock logic reads them); only the label differs
    state = fields.Selection(selection=[("draft", "Draft"), ("done", "Approved")])
    name = fields.Char(required=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    version_type = fields.Selection(
        selection=[
            ("budget", "Budget"),
            ("forecast", "Forecast"),
            ("standard", "Standard"),
            ("simulation", "Simulation"),
        ],
        required=True,
        default="budget",
    )
    parent_id = fields.Many2one("cmc.plan.version", string="Copied From", copy=False, readonly=True, index=True)
    assumption_ids = fields.One2many("cmc.plan.version.assumption", "version_id", copy=True)
    currency_id = fields.Many2one(
        "res.currency",
        compute="_compute_currency_id",
        store=True,
        readonly=False,
        precompute=True,
        required=True,
    )
    currency_rate = fields.Float(
        default=1.0,
        digits=(16, 6),
        help="Company currency units per 1 unit of the version currency.",
    )

    @api.depends("company_id")
    def _compute_currency_id(self):
        for version in self:
            version.currency_id = version.company_id.currency_id

    @api.constrains("date_from", "date_to")
    def _check_dates_ordered(self):
        if any(v.date_to < v.date_from for v in self):
            raise ValidationError(self.env._("The end date cannot precede the start date."))

    @api.constrains("currency_rate")
    def _check_currency_rate(self):
        if any(v.currency_rate <= 0 for v in self):
            raise ValidationError(self.env._("The currency rate must be positive."))

    def copy(self, default=None):
        self.ensure_one()
        default = {"name": self.env._("%s (copy)", self.name), **(default or {}), "parent_id": self.id}
        new = super().copy(default)
        for model, domain, field in (
            ("cmc.measure.quantity", [("version_id", "=", self.id)], "version_id"),
            ("cmc.rate", [("origin_model", "=", self._name), ("origin_id", "=", self.id)], "origin_id"),
        ):
            Model = self.env[model]
            Model.create(Model.search(domain).copy_data({field: new.id}))
        return new

    def unlink(self):
        # the facts of the version go with it (cascade): never silently for an approved one
        self._cmc_check_editable()
        return super().unlink()

    def action_approve(self):
        return self.action_compute()

    def action_compute(self):
        self._cmc_check_manager()
        return super().action_compute()

    def action_reset_to_draft(self):
        self._cmc_check_manager()
        return super().action_reset_to_draft()

    def _cmc_compute(self):
        """An approved version has no results: approving only locks it."""

    def _cmc_check_manager(self):
        if not self.env.su and not self.env.user.has_group("cmc_base.group_cmc_manager"):
            raise AccessError(self.env._("Only a Controlling Manager approves or reopens a plan version."))

    def _cmc_to_company_currency(self, amount):
        self.ensure_one()
        return self.company_id.currency_id.round(amount * self.currency_rate)

    def _cmc_months(self):
        self.ensure_one()
        return month_range(self.date_from, self.date_to)

    def _cmc_scenario_evaluators(self):
        """Profit evaluators producers register with `super() + [...]` (AD-102); entries:
        {"key", "name", "prepare": callable(version) -> {"inputs": {path: float}, "evaluate": callable(values) ->
        {(center_id, element_id, month): signed amount}}}."""
        return []

    def _cmc_scenario_evaluator(self, key):
        for entry in self._cmc_scenario_evaluators():
            if entry["key"] == key:
                return entry
        raise UserError(self.env._("No module provides the scenario evaluator %s.", key))


class CmcPlanVersionAssumption(models.Model):
    _name = "cmc.plan.version.assumption"
    _description = "Plan Version Assumption"
    _order = "id"

    version_id = fields.Many2one("cmc.plan.version", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="version_id.company_id", store=True, index=True)
    name = fields.Char(required=True)
    note = fields.Text()
