from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Command

from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE

from .cmc_abc_resource import SPENDING_PROFILES

QTY = (16, 6)
TIME_MODES = [
    ("standby", "Standby"),
    ("maintenance", "Maintenance"),
    ("changeover", "Changeover"),
    ("training", "Training"),
]
OUTPUT_FIELDS = (
    "total_budgeted_cost", "total_assigned_cost", "total_unused_cost", "mix_capacity_factor", "delta_cost", "predicted_cost",
)


class CmcAbbRun(models.Model):
    _name = "cmc.abb.run"
    _inherit = ["cmc.run.mixin"]
    _description = "Activity-Based Budget Run"
    _order = "id desc"
    _check_company_auto = True
    _check_company_domain = models.check_company_domain_parent_of

    name = fields.Char(required=True, default=lambda self: self.env._("Activity-based budget"))
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    source_version_id = fields.Many2one(
        "cmc.plan.version", string="Volume Version", required=True, check_company=True,
        domain="[('state', '=', 'done')]", help="Approved version holding the planned volumes.",
    )
    target_version_id = fields.Many2one(
        "cmc.plan.version", string="Budget Version", check_company=True, domain="[('state', '=', 'draft')]",
        help="Draft version receiving the budget lines and the prospective rates. Empty: analysis only.",
    )
    output_measure_id = fields.Many2one(
        "cmc.cost.driver", string="Output Measure", required=True, check_company=True,
        domain="[('measure_kind', '=', 'statistical')]", help="Statistical measure of the planned volume of each cost object.",
    )
    calibration_run_id = fields.Many2one(
        "cmc.abc.run", string="Calibration Run", check_company=True, domain="[('state', '=', 'done')]",
        help="Computed ABC run the bill of activities and the extrapolation warnings come from.",
    )
    calibration_tolerance = fields.Float(default=20.0, help="Percent deviation from the calibration that raises a warning.")
    baseline_run_id = fields.Many2one(
        "cmc.abb.run", string="Baseline", check_company=True, copy=False, domain="[('state', '=', 'done')]",
        help="Computed run a what-if scenario is compared with.",
    )
    # ---- inputs
    bill_line_ids = fields.One2many("cmc.abb.bill.line", "run_id", string="Bill of Activities", copy=True)
    usage_line_ids = fields.One2many("cmc.abb.usage.line", "run_id", string="Resource Usage", copy=True)
    driver_override_ids = fields.One2many("cmc.abb.driver.override", "run_id", string="Driver Overrides", copy=True)
    time_state_ids = fields.One2many("cmc.abb.time.state", "run_id", string="Non-productive Time", copy=True)
    # ---- outputs
    total_budgeted_cost = fields.Monetary(readonly=True, copy=False)
    total_assigned_cost = fields.Monetary(readonly=True, copy=False)
    total_unused_cost = fields.Monetary(readonly=True, copy=False)
    extrapolation_warning = fields.Boolean(readonly=True, copy=False)
    bottleneck_resource_id = fields.Many2one("cmc.abc.resource", readonly=True, copy=False)
    mix_capacity_factor = fields.Float(readonly=True, copy=False, digits=QTY, help="How far the planned mix can scale before the first resource binds.")
    delta_cost = fields.Monetary(readonly=True, copy=False, help="Budgeted cost minus the baseline's.")
    predicted_cost = fields.Monetary(readonly=True, copy=False, help="Forecast driver quantity x the baseline rates.")
    activity_result_ids = fields.One2many("cmc.abb.activity.result", "run_id")
    resource_result_ids = fields.One2many("cmc.abb.resource.result", "run_id")
    object_result_ids = fields.One2many("cmc.abb.object.result", "run_id")
    capacity_state_ids = fields.One2many("cmc.abb.capacity.state", "run_id")
    warning_ids = fields.One2many("cmc.abb.warning", "run_id")
    budget_line_ids = fields.One2many("cmc.budget.line", "abb_run_id", readonly=True, copy=False)

    _cmc_input_fields = (
        "company_id", "source_version_id", "target_version_id", "output_measure_id", "calibration_run_id",
        "calibration_tolerance", "baseline_run_id", "bill_line_ids", "usage_line_ids", "driver_override_ids", "time_state_ids",
    )
    _cmc_result_fields = (
        "activity_result_ids", "resource_result_ids", "object_result_ids", "capacity_state_ids", "warning_ids",
    )

    @api.constrains("calibration_tolerance")
    def _check_tolerance(self):
        if any(run.calibration_tolerance < 0 for run in self):
            raise ValidationError(self.env._("The calibration tolerance cannot be negative."))

    def _cmc_reset_values(self):
        return {
            **super()._cmc_reset_values(), **dict.fromkeys(OUTPUT_FIELDS, 0.0),
            "extrapolation_warning": False, "bottleneck_resource_id": False,
        }

    # ---- lifecycle: the budget lines and rates live outside the run ----------

    def unlink(self):
        self._cmc_check_no_dependents()
        self._cmc_drop_target()
        return super().unlink()

    def action_reset_to_draft(self):
        self.check_access("write")
        self._cmc_check_no_dependents()
        self._cmc_drop_target()
        return super().action_reset_to_draft()

    def _cmc_drop_target(self):
        """Delete what the runs wrote in their target version; an approved target raises (version lock)."""
        # why sudo: rates and lines belong to the run, whoever wrote them; the sentinel opens the lines' guard
        self.env["cmc.rate"].sudo().search([("abb_run_id", "in", self.ids)]).unlink()
        self.env["cmc.budget.line"].sudo().with_context(cmc_run_state=CMC_RUN_STATE).search([("abb_run_id", "in", self.ids)]).unlink()

    def _cmc_rate_specs(self):
        """[(holder, driver, component, value)] the run publishes as prospective rates, read from its results."""
        self.ensure_one()
        results = self.sudo()
        specs = []
        for act in results.activity_result_ids:
            specs += [(act.activity_id, act.driver_id, comp, act[name]) for comp, name in
                      (("total", "rate"), ("fixed", "fixed_rate"), ("variable", "variable_rate"))]
        specs += [(res.resource_id, res.driver_id, "total", res.rate) for res in results.resource_result_ids]
        specs += [(obj.cost_object_id, self.output_measure_id, "total", obj.unit_cost) for obj in results.object_result_ids]
        return specs

    # ---- actions ---------------------------------------------------------------

    def action_prefill_bill(self):
        """Bill of activities from the calibration run: edge driver quantity / produced quantity of the object."""
        self.ensure_one()
        calibration = self.calibration_run_id
        if self.state != "draft" or not calibration:
            raise UserError(self.env._("Pick a calibration run on a draft run to prefill the bill of activities."))
        # why sudo: engine; the calibration results are certified figures, not the user's rows
        produced = {
            obj.id: qty for obj, qty in self.env["cmc.abc.result"].sudo()._read_group(
                [("run_id", "=", calibration.id), ("cost_object_id", "!=", False)], ["cost_object_id"], ["quantity:sum"]
            )
        }
        edges = calibration.sudo().line_ids.filtered(lambda e: e.activity_id and e.cost_object_id).read(
            ["activity_id", "cost_object_id", "driver_qty"], load=False
        )
        self.bill_line_ids = [Command.clear()] + [
            Command.create({
                "cost_object_id": e["cost_object_id"], "activity_id": e["activity_id"],
                "qty_per_unit": e["driver_qty"] / produced[e["cost_object_id"]],
            })
            for e in edges if produced.get(e["cost_object_id"])
        ]
        return True

    def action_what_if(self):
        """A draft scenario copying the inputs of this computed run; it never writes budget lines nor rates."""
        self.ensure_one()
        if self.state != "done":
            raise UserError(self.env._("Compute the run first: a scenario is compared with computed figures."))

        def copy(field):
            lines = self[field]
            return [Command.create({k: v for k, v in vals.items() if k != "id"}) for vals in lines.read(list(lines._cmc_copy_fields), load=False)]

        return self.create({
            "name": self.env._("%s (what-if)", self.name), "company_id": self.company_id.id,
            "source_version_id": self.source_version_id.id, "output_measure_id": self.output_measure_id.id,
            "calibration_run_id": self.calibration_run_id.id, "calibration_tolerance": self.calibration_tolerance,
            "baseline_run_id": self.id, "target_version_id": False,
            **{field: copy(field) for field in ("bill_line_ids", "usage_line_ids", "driver_override_ids", "time_state_ids")},
        })

    def action_open_what_if(self):
        what_if = self.action_what_if()
        return {
            "type": "ir.actions.act_window", "res_model": self._name, "res_id": what_if.id, "view_mode": "form",
            "views": [(self.env.ref("cmc_abb.view_cmc_abb_whatif_form").id, "form")],
        }

    def action_open_rates(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("cmc_base.action_cmc_rate")
        action["domain"] = [
            ("origin_model", "=", "cmc.plan.version"), ("origin_id", "=", self.target_version_id.id),
            ("holder_model", "in", ("cmc.abc.activity", "cmc.abc.resource", "cmc.cost.object")),
        ]
        return action

    def _cmc_unused_by_responsibility(self):
        """{responsibility center id (False: nobody): cost of the non-productive and idle capacity}."""
        self.ensure_one()
        groups = self.env["cmc.abb.capacity.state"]._read_group(
            [("run_id", "=", self.id), ("state", "!=", "productive")], ["responsibility_id"], ["cost:sum"]
        )
        return {resp.id or False: cost for resp, cost in groups}


# ---- inputs -----------------------------------------------------------------------


class CmcAbbInput(models.AbstractModel):
    _name = "cmc.abb.input"
    _inherit = ["cmc.run.line.mixin"]
    _description = "ABB run input line"
    _check_company_auto = True
    _check_company_domain = models.check_company_domain_parent_of
    # fields copied into a what-if scenario
    _cmc_copy_fields = ()

    run_id = fields.Many2one("cmc.abb.run", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="run_id.company_id", store=True, index=True)


class CmcAbbBillLine(models.Model):
    _name = "cmc.abb.bill.line"
    _inherit = ["cmc.abb.input"]
    _description = "ABB Bill of Activities Line"
    _order = "run_id, cost_object_id, activity_id"
    _cmc_copy_fields = ("cost_object_id", "activity_id", "qty_per_unit")

    cost_object_id = fields.Many2one("cmc.cost.object", required=True, check_company=True, index=True)
    activity_id = fields.Many2one("cmc.abc.activity", required=True, check_company=True, index=True)
    qty_per_unit = fields.Float(string="Activity Units per Output Unit", digits=QTY)

    _bill_uniq = models.UniqueIndex("(run_id, cost_object_id, activity_id)", "A cost object consumes an activity once per run.")

    @api.constrains("qty_per_unit")
    def _check_qty_per_unit(self):
        if any(line.qty_per_unit <= 0 for line in self):
            raise ValidationError(self.env._("The activity quantity per output unit must be greater than zero."))


class CmcAbbUsageLine(models.Model):
    _name = "cmc.abb.usage.line"
    _inherit = ["cmc.abb.input"]
    _description = "ABB Resource Usage Line"
    _order = "run_id, activity_id, resource_id"
    _cmc_copy_fields = ("activity_id", "resource_id", "usage_per_unit")

    activity_id = fields.Many2one("cmc.abc.activity", required=True, check_company=True, index=True)
    resource_id = fields.Many2one("cmc.abc.resource", required=True, check_company=True, index=True)
    usage_per_unit = fields.Float(string="Resource Units per Activity Unit", digits=QTY)

    _usage_uniq = models.UniqueIndex("(run_id, activity_id, resource_id)", "An activity uses a resource once per run.")

    @api.constrains("usage_per_unit")
    def _check_usage_per_unit(self):
        if any(line.usage_per_unit <= 0 for line in self):
            raise ValidationError(self.env._("The resource quantity per activity unit must be greater than zero."))


class CmcAbbDriverOverride(models.Model):
    _name = "cmc.abb.driver.override"
    _inherit = ["cmc.abb.input"]
    _description = "ABB Activity Driver Override"
    _order = "run_id, activity_id"
    _cmc_copy_fields = ("activity_id", "quantity")

    activity_id = fields.Many2one("cmc.abc.activity", required=True, check_company=True, index=True)
    quantity = fields.Float(string="Planned Driver Quantity", digits=QTY, help="Replaces the demand computed from the planned volume.")

    _override_uniq = models.UniqueIndex("(run_id, activity_id)", "An activity has one driver override per run.")

    @api.constrains("quantity")
    def _check_quantity(self):
        if any(line.quantity < 0 for line in self):
            raise ValidationError(self.env._("The planned driver quantity cannot be negative."))


class CmcAbbTimeState(models.Model):
    _name = "cmc.abb.time.state"
    _inherit = ["cmc.abb.input"]
    _description = "ABB Non-productive Time"
    _order = "run_id, resource_id, mode"
    _cmc_copy_fields = ("resource_id", "mode", "quantity", "responsibility_id")

    resource_id = fields.Many2one("cmc.abc.resource", required=True, check_company=True, index=True)
    mode = fields.Selection(TIME_MODES, required=True)
    quantity = fields.Float(string="Resource Units", digits=QTY)
    responsibility_id = fields.Many2one(
        "cmc.responsibility.center", check_company=True, help="Owner of this time. Empty: the non-productive owner of the resource."
    )

    @api.constrains("quantity")
    def _check_quantity(self):
        if any(line.quantity <= 0 for line in self):
            raise ValidationError(self.env._("A non-productive time must be greater than zero."))


# ---- results ----------------------------------------------------------------------


class CmcAbbResult(models.AbstractModel):
    _name = "cmc.abb.result"
    _inherit = ["cmc.run.result.mixin"]
    _description = "ABB result row"

    run_id = fields.Many2one("cmc.abb.run", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="run_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="run_id.currency_id")


class CmcAbbActivityResult(models.Model):
    _name = "cmc.abb.activity.result"
    _inherit = ["cmc.abb.result"]
    _description = "ABB Activity Demand and Rate"
    _order = "run_id, activity_id"

    activity_id = fields.Many2one("cmc.abc.activity", required=True, index=True)
    driver_id = fields.Many2one("cmc.cost.driver")
    demand_qty = fields.Float(digits=QTY)
    calibration_qty = fields.Float(digits=QTY, help="Used quantity of the calibration run, scaled to the months of the span.")
    deviation_percent = fields.Float(digits=(16, 4))
    rate = fields.Float(digits=QTY)
    fixed_rate = fields.Float(digits=QTY)
    variable_rate = fields.Float(digits=QTY)
    assigned_cost = fields.Monetary()
    capacity_qty = fields.Float(digits=QTY, help="Driver units the activity can still supply before its first constraining resource binds.")
    constraining_resource_id = fields.Many2one("cmc.abc.resource")


class CmcAbbResourceResult(models.Model):
    _name = "cmc.abb.resource.result"
    _inherit = ["cmc.abb.result"]
    _description = "ABB Resource Supply"
    _order = "run_id, resource_id"

    resource_id = fields.Many2one("cmc.abc.resource", required=True, index=True)
    driver_id = fields.Many2one("cmc.cost.driver")
    center_id = fields.Many2one("account.analytic.account", string="Cost Center")
    element_id = fields.Many2one("cmc.cost.element", string="Cost Element")
    spending_profile = fields.Selection(SPENDING_PROFILES)
    demand_qty = fields.Float(digits=QTY)
    nonproductive_qty = fields.Float(digits=QTY)
    required_units = fields.Float(digits=QTY, help="Supply units the need calls for (FTE for people).")
    supply_units = fields.Float(digits=QTY)
    supply_qty = fields.Float(digits=QTY)
    unused_qty = fields.Float(digits=QTY)
    committed_units = fields.Float(digits=QTY)
    shortage_units = fields.Float(digits=QTY)
    excess_units = fields.Float(digits=QTY)
    budgeted_cost = fields.Monetary()
    assigned_cost = fields.Monetary()
    unused_cost = fields.Monetary()
    rate = fields.Float(digits=QTY)
    delta_supply_units = fields.Float(digits=QTY)
    delta_cost = fields.Monetary()


class CmcAbbObjectResult(models.Model):
    _name = "cmc.abb.object.result"
    _inherit = ["cmc.abb.result"]
    _description = "ABB Cost Object Pro-forma"
    _order = "run_id, cost_object_id"

    cost_object_id = fields.Many2one("cmc.cost.object", required=True, index=True)
    planned_qty = fields.Float(digits=QTY)
    unit_cost = fields.Float(digits=QTY)
    amount = fields.Monetary()


class CmcAbbCapacityState(models.Model):
    _name = "cmc.abb.capacity.state"
    _inherit = ["cmc.abb.result"]
    _description = "ABB Capacity Time State"
    _order = "run_id, resource_id, state, mode"

    resource_id = fields.Many2one("cmc.abc.resource", required=True, index=True)
    state = fields.Selection(
        [("productive", "Productive"), ("nonproductive", "Non-productive"), ("idle", "Idle")], required=True, index=True
    )
    mode = fields.Selection(TIME_MODES)
    quantity = fields.Float(digits=QTY)
    cost = fields.Monetary()
    responsibility_id = fields.Many2one("cmc.responsibility.center", index=True)


class CmcAbbWarning(models.Model):
    _name = "cmc.abb.warning"
    _inherit = ["cmc.abb.result"]
    _description = "ABB Extrapolation Warning"
    _order = "run_id, warning_type, id"

    warning_type = fields.Selection([("volume", "Volume"), ("mix", "Mix")], required=True)
    activity_id = fields.Many2one("cmc.abc.activity")
    cost_object_id = fields.Many2one("cmc.cost.object")
    calibration_qty = fields.Float(digits=QTY)
    planned_qty = fields.Float(digits=QTY)
    deviation_percent = fields.Float(digits=(16, 4))

