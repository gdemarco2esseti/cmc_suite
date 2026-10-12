from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Domain
from odoo.tools import float_compare, format_date

from odoo.addons.cmc_base.models.cmc_utils import month_end, process_cost_report, spoilage_split

ELEMENTS = [("transferred_in", "Transferred In"), ("materials", "Materials"), ("conversion", "Conversion")]
PCT_FIELDS = (
    "normal_spoilage_pct", "beginning_pct_materials", "beginning_pct_conversion", "ending_pct_materials", "ending_pct_conversion",
)
UNIT_FIELDS = ("beginning_units", "units_started", "units_completed", "units_spoiled", "ending_units")
SCRAP_KEY, PRODUCED_KEY = "mrp_scrap_qty", "mrp_produced_qty"


class CmcProcessRun(models.Model):
    """Cost of production report of one department (CMC cost center) and one calendar month (FC-07).

    Deviation from AD-112: the amounts of the run and of its lines are POSITIVE costs (WIP inventory values,
    reporting convention as in the job cost sheet); the analytic sign (cost < 0) is flipped once, in the reader."""

    _name = "cmc.process.run"
    _inherit = ["cmc.run.mixin"]
    _description = "Process Costing Run"
    _order = "date_from desc, id desc"
    _check_company_auto = True

    _cmc_input_fields = (
        "company_id", "center_id", "product_id", "date_from", "date_to", "method", "previous_run_id", "previous_dept_run_id",
        "closing_run_id", "normal_spoilage_pct", "beginning_units", "beginning_pct_materials", "beginning_pct_conversion",
        "beginning_cost_transferred_in", "beginning_cost_materials", "beginning_cost_conversion", "units_started", "units_completed",
        "units_spoiled", "ending_units", "ending_pct_materials", "ending_pct_conversion",
    )
    _cmc_result_fields = ("line_ids",)

    name = fields.Char(required=True, default=lambda self: self.env._("Process costing"))
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    center_id = fields.Many2one("account.analytic.account", string="Department", required=True, check_company=True, index=True)
    product_id = fields.Many2one("product.product", string="Process Output", required=True, check_company=True)
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    method = fields.Selection([("weighted_average", "Weighted Average"), ("fifo", "FIFO")], default="weighted_average", required=True)
    previous_run_id = fields.Many2one(
        "cmc.process.run", string="Previous Month Run", check_company=True, copy=False,
        help="Same department and product, an earlier month: source of the beginning WIP.",
    )
    previous_dept_run_id = fields.Many2one(
        "cmc.process.run", string="Upstream Department Run", check_company=True, copy=False,
        help="Another department, same month: source of the transferred-in units and cost.",
    )
    closing_run_id = fields.Many2one(
        "cmc.closing.run", string="Closing Run", check_company=True, copy=False,
        help="Adds the overhead received from the service centers to the conversion cost.",
    )
    normal_spoilage_pct = fields.Float(string="Normal Spoilage %", help="Normal spoilage as % of the good units completed.")
    beginning_units = fields.Float(digits="Product Unit")
    beginning_pct_materials = fields.Float(string="Beginning % Materials")
    beginning_pct_conversion = fields.Float(string="Beginning % Conversion")
    beginning_cost_transferred_in = fields.Monetary()
    beginning_cost_materials = fields.Monetary()
    beginning_cost_conversion = fields.Monetary()
    units_started = fields.Float(digits="Product Unit", help="Started in the department or transferred in.")
    units_completed = fields.Float(digits="Product Unit", help="Good units completed and transferred out.")
    units_spoiled = fields.Float(digits="Product Unit")
    ending_units = fields.Float(digits="Product Unit")
    ending_pct_materials = fields.Float(string="Ending % Materials")
    ending_pct_conversion = fields.Float(string="Ending % Conversion")
    units_normal_spoiled = fields.Float(digits="Product Unit", readonly=True, copy=False)
    units_abnormal_spoiled = fields.Float(digits="Product Unit", readonly=True, copy=False)
    cost_to_account = fields.Monetary(readonly=True, copy=False)
    transferred_out_cost = fields.Monetary(readonly=True, copy=False, help="Includes the normal spoilage.")
    normal_spoilage_cost = fields.Monetary(readonly=True, copy=False)
    ending_wip_cost = fields.Monetary(readonly=True, copy=False)
    abnormal_loss = fields.Monetary(readonly=True, copy=False)
    unit_cost_transferred = fields.Float(digits=False, readonly=True, copy=False)
    control_difference = fields.Monetary(readonly=True, copy=False, help="Costs to account for minus costs accounted for: always zero.")
    line_ids = fields.One2many("cmc.process.run.line", "run_id")

    # ---- constraints ----------------------------------------------------

    @api.constrains("date_from", "date_to")
    def _check_one_month(self):
        for run in self:
            if run.date_from.day != 1 or run.date_to != month_end(run.date_from):
                raise ValidationError(self.env._("A process run covers exactly one calendar month."))

    @api.constrains(*PCT_FIELDS)
    def _check_percentages(self):
        for run in self:
            if any(not 0.0 <= run[name] <= 100.0 for name in PCT_FIELDS):
                raise ValidationError(self.env._("Percentages must be between 0 and 100."))

    @api.constrains(*UNIT_FIELDS)
    def _check_units(self):
        for run in self:
            if any(run[name] < 0.0 for name in UNIT_FIELDS):
                raise ValidationError(self.env._("Units cannot be negative."))

    @api.constrains("previous_run_id", "previous_dept_run_id", "center_id", "product_id", "date_from", "date_to")
    def _check_sources(self):
        for run in self:
            prev, dept = run.previous_run_id, run.previous_dept_run_id
            if prev and (prev.center_id != run.center_id or prev.product_id != run.product_id or prev.date_to >= run.date_from):
                raise ValidationError(self.env._("The previous month run must be of the same department and product, in an earlier month."))
            if dept and (dept.center_id == run.center_id or dept.date_from != run.date_from):
                raise ValidationError(self.env._("The upstream run must be of another department, in the same month."))

    # ---- CRUD -----------------------------------------------------------

    def unlink(self):
        if self.filtered(lambda run: run.state == "done"):
            raise UserError(self.env._("Reset a computed run to draft before deleting it."))
        return super().unlink()

    def _cmc_reset_values(self):
        return dict.fromkeys(
            ("units_normal_spoiled", "units_abnormal_spoiled", "cost_to_account", "transferred_out_cost", "normal_spoilage_cost",
             "ending_wip_cost", "abnormal_loss", "unit_cost_transferred", "control_difference"),
            0.0,
        )

    # ---- prefill --------------------------------------------------------

    def action_cmc_prefill(self):
        self.check_access("write")
        if self.filtered(lambda run: run.state != "draft"):
            raise UserError(self.env._("Prefill works on draft runs only."))
        for run in self:
            run.write(run._cmc_prefill_values())
        return True

    def _cmc_prefill_values(self):
        """Quantity schedule from MRP (produced and scrap feeders), beginning WIP and starts from the source runs."""
        self.ensure_one()
        # why sudo: the feeders and the facts are engine data; the CMC user needs no MRP rights (AD-115)
        Fact = self.env["cmc.measure.quantity"].sudo()
        Fact._cmc_refresh_feeders(self.date_from, self.date_to, company=self.company_id, keys=[PRODUCED_KEY, SCRAP_KEY])
        produced, scrap = (self.env.ref("cmc_mrp.cmc_measure_produced_qty"), self.env.ref("cmc_mrp.cmc_measure_scrap_qty"))
        cost_object = Fact.env["cmc.cost.object"]._cmc_ensure_objects(self.product_id, company=self.company_id)
        totals = {
            driver.id: qty
            for driver, qty in Fact._read_group(
                [("company_id", "=", self.company_id.id), ("version_id", "=", False), ("date", "=", self.date_from),
                 ("center_id", "=", self.center_id.id), ("cost_object_id", "=", cost_object.id), ("driver_id", "in", (produced | scrap).ids)],
                ["driver_id"], ["quantity:sum"],
            )
        }
        if not (totals or self.previous_run_id or self.previous_dept_run_id):
            raise UserError(self.env._(
                "No MRP quantity of %(product)s in %(center)s for %(month)s and no source run: produced and scrapped quantities "
                "come from the manufacturing orders of the month, or link a previous run. The typed quantities are unchanged.",
                product=self.product_id.display_name, center=self.center_id.display_name,
                month=format_date(self.env, self.date_from, date_format="MMMM yyyy"),
            ))
        spoiled = totals.get(scrap.id, 0.0)
        vals = {"units_spoiled": spoiled, "units_completed": totals.get(produced.id, 0.0) - spoiled}
        if self.previous_run_id:
            vals.update(self._cmc_beginning_values(self.previous_run_id))
        beginning = vals.get("beginning_units", self.beginning_units)
        if self.previous_dept_run_id:
            self._cmc_require_computed(self.previous_dept_run_id)
            vals["units_started"] = self.previous_dept_run_id.units_completed
        else:
            vals["units_started"] = max(0.0, vals["units_completed"] + spoiled + self.ending_units - beginning)
        return vals

    def _cmc_beginning_values(self, previous):
        self._cmc_require_computed(previous)
        cost = {line.element: line.ending_cost for line in previous.sudo().line_ids}  # why sudo: engine rows, read-only for users
        return {
            "beginning_units": previous.ending_units,
            "beginning_pct_materials": previous.ending_pct_materials,
            "beginning_pct_conversion": previous.ending_pct_conversion,
            "beginning_cost_transferred_in": cost.get("transferred_in", 0.0),
            "beginning_cost_materials": cost.get("materials", 0.0),
            "beginning_cost_conversion": cost.get("conversion", 0.0),
        }

    def _cmc_require_computed(self, source):
        if source.state != "done":
            raise UserError(self.env._("%(run)s is not computed: a computed run is the only source.", run=source.display_name))

    # ---- engine ---------------------------------------------------------

    def _cmc_compute(self):
        self.ensure_one()
        # why sudo: analytic lines, closing and source runs are read inside the run company whoever presses Compute (AD-115);
        # the result lines are written by the engine, read-only for users
        engine = self.sudo()
        engine._cmc_check_schedule()
        engine._cmc_check_closing()
        origins = engine.previous_run_id | engine.previous_dept_run_id
        if origins:
            self._cmc_record_usage(origins)
        if engine.closing_run_id:
            self._cmc_record_usage(engine.closing_run_id)
        normal, abnormal = spoilage_split(engine.units_spoiled, engine.units_completed, engine.normal_spoilage_pct)
        units = {
            "beginning": engine.beginning_units, "started": engine.units_started, "completed": engine.units_completed,
            "ending": engine.ending_units, "normal_spoiled": normal, "abnormal_spoiled": abnormal,
        }
        elements = engine._cmc_element_costs()
        currency = engine.currency_id
        try:
            report = process_cost_report(engine.method, units, elements, rounding=currency.rounding)
        except ValueError as err:
            raise UserError(self.env._("The cost of production cannot be computed: %s", err)) from err
        # AD-119 guarantees it; a failure here is a bug, never a balancing figure
        if currency.round(report["cost_to_account"] - report["transferred"] - report["ending"] - report["abnormal"]):
            raise UserError(self.env._("The control total of the cost of production does not close."))
        engine.env["cmc.process.run.line"].create([
            {
                "run_id": self.id, "element": key, **elements[key], "equivalent_units": row["eu"], "cost_per_eu": row["cost_per_eu"],
                "cost_to_account": row["cost_to_account"], "transferred_cost": row["transferred"], "normal_spoilage_cost": row["normal_spoilage"],
                "ending_cost": row["ending"], "abnormal_cost": row["abnormal"],
            }
            for key, row in report["elements"].items()
        ])
        self.write({
            "units_normal_spoiled": normal, "units_abnormal_spoiled": abnormal, "cost_to_account": report["cost_to_account"],
            "transferred_out_cost": report["transferred"], "normal_spoilage_cost": report["normal_spoilage"],
            "ending_wip_cost": report["ending"], "abnormal_loss": report["abnormal"],
            "unit_cost_transferred": report["transferred"] / engine.units_completed if engine.units_completed else 0.0,
            "control_difference": 0.0,
        })

    def _cmc_check_schedule(self):
        to_account = self.beginning_units + self.units_started
        accounted = self.units_completed + self.units_spoiled + self.ending_units
        if float_compare(to_account, accounted, precision_digits=self.env["decimal.precision"].precision_get("Product Unit")):
            raise UserError(
                self.env._(
                    "Units to account for (%(to_account)s) differ from units accounted for (%(accounted)s).",
                    to_account=to_account, accounted=accounted,
                )
            )

    def _cmc_check_closing(self):
        closing = self.closing_run_id
        if closing and (closing.company_id != self.company_id or closing.date_from != self.date_from or closing.date_to != self.date_to):
            raise UserError(self.env._("The closing run must be of the same company and of the same period as the run."))

    def _cmc_element_costs(self):
        """{element: beginning/period cost and WIP percentages}; the transferred-in element only when chained."""
        materials, conversion = self._cmc_read_period_costs()
        elements = {}
        if self.previous_dept_run_id or self.beginning_cost_transferred_in:
            elements["transferred_in"] = {
                "beginning_cost": self.beginning_cost_transferred_in, "period_cost": self.previous_dept_run_id.transferred_out_cost,
                "beginning_pct": 100.0, "ending_pct": 100.0,
            }
        elements["materials"] = {
            "beginning_cost": self.beginning_cost_materials, "period_cost": materials,
            "beginning_pct": self.beginning_pct_materials, "ending_pct": self.ending_pct_materials,
        }
        elements["conversion"] = {
            "beginning_cost": self.beginning_cost_conversion, "period_cost": conversion,
            "beginning_pct": self.beginning_pct_conversion, "ending_pct": self.ending_pct_conversion,
        }
        return elements

    def _cmc_read_period_costs(self):
        """(materials, conversion) cost of the department in the month, positive, with grouped reads (AD-114)."""
        Line = self.env["account.analytic.line"]
        mp = self.env.ref("cmc_base.cmc_element_mp")
        domain = Domain([
            ("company_id", "in", [False, self.company_id.id]), ("date", ">=", self.date_from), ("date", "<=", self.date_to),
            ("cmc_excluded", "=", False), (self.center_id.plan_id._column_name(), "=", self.center_id.id),
        ])
        materials = conversion = 0.0
        for element, cost in Line._cmc_cost_read_group(domain, ["cmc_cost_element_id"]):
            if element == mp:
                materials += cost
            else:
                conversion += cost
        upstream = self.previous_dept_run_id
        if upstream:
            # the consumption of the upstream product IS the transferred-in cost: counting it twice is the classic error
            materials -= Line._cmc_cost_read_group(
                domain & Domain([("cmc_cost_element_id", "=", mp.id), ("product_id", "=", upstream.product_id.id)])
            )[0][0]
        if self.closing_run_id:
            conversion += self.env["cmc.closing.line"]._read_group(
                [("run_id", "=", self.closing_run_id.id), ("center_id", "=", self.center_id.id)], [], ["cost_received:sum"]
            )[0][0] or 0.0
        return materials, conversion


class CmcProcessRunLine(models.Model):
    _name = "cmc.process.run.line"
    _inherit = ["cmc.run.result.mixin"]
    _description = "Process Costing Run Line"
    _order = "run_id, id"

    run_id = fields.Many2one("cmc.process.run", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="run_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="run_id.currency_id")
    element = fields.Selection(ELEMENTS, required=True)
    beginning_cost = fields.Monetary()
    period_cost = fields.Monetary()
    cost_to_account = fields.Monetary()
    beginning_pct = fields.Float()
    ending_pct = fields.Float()
    equivalent_units = fields.Float(digits="Product Unit")
    cost_per_eu = fields.Float(digits=False, help="Full precision, never rounded.")
    transferred_cost = fields.Monetary()
    normal_spoilage_cost = fields.Monetary()
    ending_cost = fields.Monetary()
    abnormal_cost = fields.Monetary()
