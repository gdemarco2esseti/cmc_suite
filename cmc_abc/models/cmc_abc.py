import math
from collections import defaultdict

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Command, Domain
from odoo.tools import float_compare, float_round

from odoo.addons.cmc_base.models.cmc_utils import month_start, qty_rounding

from .cmc_abc_extend import LEVELS

# Explicit precision for activity-driver quantities. A bare `if base` truthiness
# test lets a degenerate 1e-18 through the CHECK(driver_qty > 0) constraint and
# yields an astronomic coefficient; largest_remainder then walks its residual one
# `rounding` step at a time (tens of millions of iterations). Below this
# threshold the driver base counts as absent and the pool stays unallocated.
DRIVER_QTY_ROUNDING = 1e-6


def _batches(units, batch_size):
    """A started batch is a whole batch."""
    return math.ceil(float_round(units / batch_size, precision_digits=6))


class CmcAbcActivity(models.Model):
    _name = "cmc.abc.activity"
    _inherit = ["cmc.attribute.mixin"]
    _description = "ABC Activity"
    _order = "code, name"
    _check_company_auto = True

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True, index=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company, index=True
    )
    driver_id = fields.Many2one(
        "cmc.cost.driver", string="Default Activity Driver", check_company=True,
        help="Cost driver that typically measures this activity's output.",
    )
    measure_line_ids = fields.One2many("cmc.abc.activity.measure", "activity_id", string="Performance Measures", copy=True)
    node_type = fields.Selection(
        [("process", "Process"), ("activity", "Activity"), ("task", "Task")],
        required=True, default="activity", index=True,
        help="Process > activity > task. Only activities carry cost: a process is the roll-up of its activities.",
    )
    parent_id = fields.Many2one("cmc.abc.activity", string="Parent", index=True, check_company=True, ondelete="restrict")
    child_ids = fields.One2many("cmc.abc.activity", "parent_id", string="Children")
    cost_level = fields.Selection(LEVELS, help="Cost hierarchy level (Kaplan & Cooper).")
    owner_id = fields.Many2one("res.users", string="Owner")
    is_sustaining = fields.Boolean(
        compute="_compute_is_sustaining", store=True, readonly=False, string="Business-sustaining",
        help="Its cost goes to the business-sustaining object, never to products or customers.",
    )

    _code_uniq = models.Constraint(
        "UNIQUE(code, company_id)",
        "Activity code must be unique per company.",
    )

    @api.depends("cost_level")
    def _compute_is_sustaining(self):
        for activity in self:
            activity.is_sustaining = activity.cost_level == "facility"

    @api.constrains("node_type", "parent_id")
    def _check_hierarchy(self):
        # the children are checked too: changing the type of a node can orphan them
        for activity in self | self.child_ids:
            parent_type = activity.parent_id.node_type
            if (
                (activity.node_type == "process" and activity.parent_id)
                or (activity.node_type == "activity" and activity.parent_id and parent_type != "process")
                or (activity.node_type == "task" and parent_type != "activity")
            ):
                raise ValidationError(
                    self.env._(
                        "%(name)s: a process has no parent, an activity sits under a process and a task under an activity.",
                        name=activity.display_name,
                    )
                )

    @api.constrains("driver_id", "cost_level")
    def _check_driver(self):
        for activity in self.filtered("driver_id"):
            driver = activity.driver_id
            if driver.measure_kind == "statistical":
                raise ValidationError(
                    self.env._(
                        "%(name)s: a statistical measure cannot allocate cost; use an allocation driver.",
                        name=activity.display_name,
                    )
                )
            if activity.cost_level and driver.cmc_abc_level and activity.cost_level != driver.cmc_abc_level:
                raise ValidationError(
                    self.env._(
                        "%(name)s is a %(level)s-level activity: its driver must measure the same level.",
                        name=activity.display_name,
                        level=activity.cost_level,
                    )
                )


class CmcAbcRun(models.Model):
    _name = "cmc.abc.run"
    _inherit = ["cmc.run.mixin", "cmc.reclass.mixin"]
    _description = "ABC Costing Run"
    _check_company_auto = True

    name = fields.Char(
        required=True, default=lambda self: self.env._("ABC Run")
    )
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company, index=True,
        required=True,
    )
    currency_id = fields.Many2one(related="company_id.currency_id")
    date_from = fields.Date()
    date_to = fields.Date()
    # ---- inputs
    resource_line_ids = fields.One2many("cmc.abc.resource.line", "run_id", string="Resource Drivers")
    line_ids = fields.One2many("cmc.abc.line", "run_id", string="Network Edges")
    capacity_line_ids = fields.One2many("cmc.abc.capacity", "run_id", string="Practical Capacity")
    trace_direct = fields.Boolean(
        string="Trace Direct Costs",
        help="Costs stamped direct on a cost object go straight to it, bypassing the activities.",
    )
    output_measure_id = fields.Many2one(
        "cmc.cost.driver", string="Output Measure", check_company=True,
        domain="[('measure_kind', '=', 'statistical')]",
        help="Statistical measure of the output units of each cost object (unit cost = cost / units).",
    )
    defect_measure_id = fields.Many2one(
        "cmc.cost.driver", string="Defect Measure", check_company=True,
        domain="[('measure_kind', '=', 'statistical')]",
        help="Statistical measure of the defective units of each cost object (cost of poor quality = defects x unit cost).",
    )
    traditional_basis = fields.Selection(
        [("volume", "Volume driver"), ("rate", "Approved rates")], string="Compare With",
        help="Traditional costing to compare the ABC cost with.",
    )
    traditional_driver_id = fields.Many2one(
        "cmc.cost.driver", string="Volume Driver", check_company=True,
        domain="[('measure_kind', '=', 'statistical')]",
    )
    traditional_version_id = fields.Many2one(
        "cmc.plan.version", string="Rate Version", check_company=True, domain="[('state', '=', 'done')]",
    )
    compute_burdens = fields.Boolean(
        help="Load idle capacity and business-sustaining cost on the objects as burdens (a view: the ABC cost is unchanged)."
    )
    # ---- outputs
    result_ids = fields.One2many("cmc.abc.result", "run_id")
    activity_result_ids = fields.One2many("cmc.abc.activity.result", "run_id")
    activity_cost_ids = fields.One2many("cmc.abc.activity.cost", "run_id")
    performance_ids = fields.One2many("cmc.abc.activity.performance", "run_id")
    flow_ids = fields.One2many("cmc.abc.flow", "run_id")
    total_resource_cost = fields.Monetary(readonly=True, copy=False, help="Resource pools and activity tags entering the model.")
    total_activity_cost = fields.Monetary(readonly=True, copy=False, help="Sum of the stage-1 cost of the activities.")
    total_traced_cost = fields.Monetary(readonly=True, copy=False)
    total_object_cost = fields.Monetary(readonly=True, copy=False, help="Cost that rests on the final cost objects.")
    unused_capacity_cost = fields.Monetary(readonly=True, copy=False)
    sustaining_cost = fields.Monetary(readonly=True, copy=False)
    copq_total = fields.Monetary(readonly=True, copy=False, string="Cost of Poor Quality", help="Defect units x ABC unit cost of the objects.")
    in_scope_cost = fields.Monetary(readonly=True, copy=False, help="Cost base of the resource centers in the period.")
    unassigned_cost = fields.Monetary(
        readonly=True, copy=False, help="In-scope cost that no resource or direct trace picked up: reported, never charged."
    )
    idle_burden_rate = fields.Float(readonly=True, copy=False, digits=(16, 4))
    sustaining_burden_rate = fields.Float(readonly=True, copy=False, digits=(16, 4))
    sustaining_object_id = fields.Many2one("cmc.cost.object", readonly=True, copy=False)
    unallocated_amount = fields.Monetary(
        compute="_compute_unallocated_amount",
        string="Unallocated Activity Cost",
        help="Cost tagged to activities the run has no driver line for. It is "
             "NOT part of the ABC cost of any object.",
    )

    _cmc_input_fields = (
        "company_id", "date_from", "date_to", "line_ids", "resource_line_ids", "capacity_line_ids", "trace_direct",
        "output_measure_id", "defect_measure_id", "traditional_basis", "traditional_driver_id", "traditional_version_id", "compute_burdens",
    )
    _cmc_result_fields = ("result_ids", "activity_result_ids", "activity_cost_ids", "flow_ids", "performance_ids")

    @api.depends("activity_result_ids.amount", "activity_result_ids.unallocated")
    def _compute_unallocated_amount(self):
        for run in self:
            run.unallocated_amount = sum(
                run.activity_result_ids.filtered("unallocated").mapped("amount")
            )

    def _cmc_reset_values(self):
        return {
            **super()._cmc_reset_values(),
            **dict.fromkeys(
                (
                    "total_resource_cost", "total_activity_cost", "total_traced_cost", "total_object_cost",
                    "unused_capacity_cost", "sustaining_cost", "in_scope_cost", "unassigned_cost", "copq_total",
                    "idle_burden_rate", "sustaining_burden_rate",
                ),
                0.0,
            ),
            "sustaining_object_id": False,
        }

    # ---- prefill ----------------------------------------------------------

    def _cmc_fact_domain(self, extra=()):
        # facts are monthly buckets (AD-105): the first month starts before date_from
        return Domain([
            ("version_id", "=", False),
            ("company_id", "=", self.company_id.id),
            ("date", ">=", month_start(self.date_from)),
            ("date", "<=", self.date_to),
        ]) & Domain(list(extra))

    def action_cmc_prefill_lines(self):
        """A->CO edges from the actual facts of the activity drivers (index drivers: sum weight x component)."""
        self.ensure_one()
        if self.state != "draft":
            raise UserError(self.env._("Edges can only be prefilled on a draft run."))
        if not (self.date_from and self.date_to):
            raise UserError(self.env._("Set the period of the run to prefill its edges from the measure facts."))
        # why sudo: engine; feeders and facts of the run company are not the user's business
        Fact = self.env["cmc.measure.quantity"].sudo()
        Fact._cmc_refresh_feeders(self.date_from, self.date_to, company=self.company_id)
        activities = self.env["cmc.abc.activity"].search([
            ("company_id", "in", [False] + self.company_id.ids), ("node_type", "=", "activity"),
            ("is_sustaining", "=", False), ("driver_id.measure_kind", "=", "allocation"),
        ])
        drivers = activities.driver_id
        measured = drivers.filtered(lambda d: not d.cmc_component_ids) | drivers.cmc_component_ids.component_driver_id
        facts = defaultdict(dict)  # driver id -> {cost object id: quantity}
        for driver, obj, qty in Fact._read_group(
            self._cmc_fact_domain([("driver_id", "in", measured.ids), ("cost_object_id", "!=", False)]),
            ["driver_id", "cost_object_id"], ["quantity:sum"],
        ):
            facts[driver.id][obj.id] = qty
        qty_step = qty_rounding(self.env)
        lines = []
        for activity in activities:
            driver = activity.driver_id
            quantities = defaultdict(float)
            if driver.cmc_component_ids:
                for comp in driver.cmc_component_ids:
                    for obj, qty in facts[comp.component_driver_id.id].items():
                        quantities[obj] += comp.weight * qty
            else:
                quantities.update(facts[driver.id])
            lines += [
                {"activity_id": activity.id, "cost_object_id": obj, "driver_id": driver.id, "driver_qty": qty}
                for obj, qty in quantities.items() if float_compare(qty, 0.0, precision_rounding=qty_step) > 0
            ]
        replaced = self.line_ids.filtered(lambda ln: ln.activity_id in activities and ln.cost_object_id)
        self.line_ids = [Command.delete(ln.id) for ln in replaced] + [Command.create(vals) for vals in lines]
        return True

    # ---- drill-back -------------------------------------------------------

    def _cmc_bill_of_activities(self, cost_object):
        """What a cost object consumed: [{kind trace|activity|object, activity, source_object, driver_qty, rate, amount}]."""
        self.ensure_one()
        rates = {cost.activity_id: cost.rate for cost in self.activity_cost_ids}
        return [
            {
                "kind": "trace" if flow.stage == "trace" else "activity" if flow.source_activity_id else "object",
                "activity": flow.source_activity_id,
                "source_object": flow.source_object_id,
                "driver_qty": flow.driver_qty,
                "rate": rates.get(flow.source_activity_id, flow.rate),
                "amount": flow.amount,
            }
            for flow in self.flow_ids.filtered(lambda f: f.target_object_id == cost_object and f.stage in ("trace", "object"))
        ]

    def _cmc_bill_of_costs(self, activity):
        """Where the cost of an activity comes from: [{kind resource|tag|activity, resource, cost_element, source_activity, ...}]."""
        self.ensure_one()
        return [
            {
                "kind": flow.stage,
                "resource": flow.source_resource_id,
                "cost_element": flow.cost_element_id,
                "source_activity": flow.source_activity_id,
                "amount": flow.amount,
                "fixed_amount": flow.fixed_amount,
                "variable_amount": flow.variable_amount,
            }
            for flow in self.flow_ids.filtered(lambda f: f.target_activity_id == activity and f.stage in ("resource", "tag", "activity"))
        ]

    def _cmc_process_costs(self):
        """{process id (False: no process): stage-1 cost of its activities}; the A->A reassignment is not counted twice."""
        self.ensure_one()
        costs = defaultdict(float)
        for row in self.activity_cost_ids:
            costs[row.process_id.id] += row.resource_cost
        return dict(costs)

    def _cmc_cost_by_level(self):
        """{hierarchy level (False: none): stage-1 cost}."""
        self.ensure_one()
        costs = defaultdict(float)
        for row in self.activity_cost_ids:
            costs[row.cost_level] += row.resource_cost
        return dict(costs)

    def _cmc_activity_cost_by_attribute(self, attribute):
        """{attribute value id (False: none): stage-1 cost of the activities carrying it}."""
        self.ensure_one()
        value_of = self._cmc_activity_values(attribute)
        costs = defaultdict(float)
        for row in self.activity_cost_ids:
            costs[value_of.get(row.activity_id.id, False)] += row.resource_cost
        return dict(costs)

    # ---- reclassified (transposed) report ---------------------------------

    @staticmethod
    def _cmc_column_key(product, cost_object):
        return "p%s" % product.id if product else "o%s" % cost_object.id

    def _cmc_reclass(self):
        """Transposed report. Reads ONLY data frozen at compute time: querying
        the analytic lines again here would mix live pools with stored results,
        so tagging a line after the run would print activity rows that do not
        sum to the subtotal."""
        self.ensure_one()
        if self.state != "done":
            return False
        res = self.result_ids
        key = self._cmc_column_key
        cols = [{"key": key(r.product_id, r.cost_object_id), "label": r.cost_object_id.display_name} for r in res]
        zero = {col["key"]: 0.0 for col in cols}
        by_activity = defaultdict(dict)
        totals = defaultdict(float)
        unallocated = []
        for ares in self.activity_result_ids:
            if ares.unallocated:
                unallocated.append(ares)
                continue
            col = key(ares.product_id, ares.cost_object_id)
            by_activity[ares.activity_id][col] = by_activity[ares.activity_id].get(col, 0.0) + ares.amount
            totals[ares.activity_id] += ares.amount
        rows = [
            {"label": activity.display_name, "kind": "plain", "values": dict(zero, **values), "total": totals[activity]}
            for activity, values in by_activity.items()
        ]
        for label, field in ((self.env._("Direct trace"), "traced_cost"), (self.env._("From other objects"), "object_cost")):
            if any(res.mapped(field)):
                rows.append({
                    "label": label, "kind": "plain", "total": sum(res.mapped(field)),
                    "values": {key(r.product_id, r.cost_object_id): r[field] for r in res},
                })
        rows.append({"label": self.env._("Total ABC cost"), "kind": "subtotal",
                     "values": {key(r.product_id, r.cost_object_id): r.amount for r in res},
                     "total": sum(res.mapped("amount"))})
        for ares in unallocated:
            rows.append({
                "label": self.env._(
                    "Unallocated · %(activity)s",
                    activity=ares.activity_id.display_name,
                ),
                "kind": "plain", "values": dict(zero), "total": ares.amount,
            })
        return self._reclass_payload(self.env._("Cost per activity (ABC)"), cols, rows)


class CmcAbcLine(models.Model):
    _name = "cmc.abc.line"
    _inherit = ["cmc.run.line.mixin"]
    _description = "ABC Network Edge (activity driver consumption)"
    _order = "run_id, id"
    _check_company_auto = True

    run_id = fields.Many2one(
        "cmc.abc.run", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(related="run_id.company_id", store=True)
    activity_id = fields.Many2one(
        "cmc.abc.activity", string="From Activity", check_company=True, index=True
    )
    source_object_id = fields.Many2one("cmc.cost.object", string="From Object", check_company=True, index=True)
    target_activity_id = fields.Many2one("cmc.abc.activity", string="To Activity", check_company=True)
    cost_object_id = fields.Many2one("cmc.cost.object", string="To Object", check_company=True, index=True)
    product_id = fields.Many2one(
        "product.product", string="Product", index=True, check_company=True,
        help="Shortcut for the cost object of a product (v1 input).",
    )
    driver_id = fields.Many2one("cmc.cost.driver", check_company=True)
    output_units = fields.Float(help="Units of output of the receiver; with the batch size gives the driver quantity.")
    batch_size = fields.Float()
    driver_qty = fields.Float(
        string="Driver Quantity", required=True, compute="_compute_driver_qty", store=True, readonly=False,
        help="Amount of the activity driver consumed by the receiver.",
    )

    _edge_uniq = models.UniqueIndex(
        "(run_id, COALESCE(activity_id, 0), COALESCE(source_object_id, 0),"
        " COALESCE(target_activity_id, 0), COALESCE(cost_object_id, 0))",
        "An edge of the network is entered once per run.",
    )
    _qty_positive = models.Constraint(
        "CHECK(driver_qty > 0)",
        "Driver quantity must be greater than zero.",
    )

    @api.depends("output_units", "batch_size")
    def _compute_driver_qty(self):
        for line in self:
            if line.output_units and line.batch_size > 0:
                line.driver_qty = _batches(line.output_units, line.batch_size)
            else:
                line.driver_qty = line.driver_qty

    @api.constrains("activity_id", "source_object_id", "target_activity_id", "cost_object_id")
    def _check_edge(self):
        for line in self:
            if bool(line.activity_id) + bool(line.source_object_id) != 1 or bool(line.target_activity_id) + bool(line.cost_object_id) != 1:
                raise ValidationError(
                    self.env._("An edge goes from exactly one activity or object to exactly one activity or object.")
                )

    def _cmc_complete_vals(self, vals_list):
        """vals with the cost object of their product (v1 lines name a product, the network speaks cost objects)
        and the batch count: a stored compute is not yet there for the NOT NULL insert."""
        vals_list = [dict(vals) for vals in vals_list]
        for vals in vals_list:
            if "driver_qty" not in vals and vals.get("output_units") and vals.get("batch_size", 0) > 0:
                vals["driver_qty"] = _batches(vals["output_units"], vals["batch_size"])
        pending = [vals for vals in vals_list if vals.get("product_id") and not vals.get("cost_object_id")]
        if pending:
            # why sudo: the run is read only to know its company
            runs = self.env["cmc.abc.run"].sudo().browse({vals["run_id"] for vals in pending if vals.get("run_id")})
            company_of = {run.id: run.company_id for run in runs}
            by_company = defaultdict(list)
            for vals in pending:
                by_company[company_of.get(vals.get("run_id"), self.env.company)].append(vals)
            for company, group in by_company.items():
                products = self.env["product.product"].browse([vals["product_id"] for vals in group])
                for vals, obj in zip(group, self.env["cmc.cost.object"]._cmc_ensure_objects(products, company=company)):
                    vals["cost_object_id"] = obj.id
        return vals_list

    @api.model_create_multi
    def create(self, vals_list):
        return super().create(self._cmc_complete_vals(vals_list))

    def write(self, vals):
        if vals.get("product_id") and "cost_object_id" not in vals:
            for company, lines in self.grouped(lambda ln: ln.run_id.company_id).items():
                product = self.env["product.product"].browse(vals["product_id"])
                obj = self.env["cmc.cost.object"]._cmc_ensure_objects(product, company=company)
                super(CmcAbcLine, lines).write({**vals, "cost_object_id": obj.id})
            return True
        return super().write(vals)


class CmcAbcResult(models.Model):
    _name = "cmc.abc.result"
    _inherit = ["cmc.run.result.mixin"]
    _description = "ABC Result (cost per object)"
    _order = "cost_object_id, id"
    _check_company_auto = True

    run_id = fields.Many2one(
        "cmc.abc.run", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(related="run_id.company_id", store=True)
    currency_id = fields.Many2one(related="run_id.currency_id")
    cost_object_id = fields.Many2one("cmc.cost.object", index=True, check_company=True)
    product_id = fields.Many2one("product.product", index=True, check_company=True)
    traced_cost = fields.Monetary(help="Direct costs stamped on the object.")
    activity_cost = fields.Monetary(help="Activities consumed: driver quantity x rate.")
    object_cost = fields.Monetary(help="Cost forwarded by other objects (customers from products, channels from customers).")
    amount = fields.Monetary(string="ABC Cost", help="Traced + activity + object cost.")
    forwarded_amount = fields.Monetary(help="Part of the cost passed on to other objects.")
    fixed_amount = fields.Monetary()
    variable_amount = fields.Monetary()
    quantity = fields.Float(help="Output units of the period (output measure).")
    unit_cost = fields.Monetary(currency_field="currency_id", help="ABC cost per output unit.")
    traditional_cost = fields.Monetary()
    distortion_percent = fields.Float(digits=(16, 2), help="(ABC - traditional) / traditional x 100.")
    costing_flag = fields.Selection(
        [("over", "Over-costed"), ("under", "Under-costed"), ("aligned", "Aligned")],
        help="What the traditional costing does to the object compared with ABC.",
    )
    idle_burden = fields.Monetary()
    sustaining_burden = fields.Monetary()
    full_cost = fields.Monetary(help="ABC cost + idle capacity and business-sustaining burdens.")
    nva_cost = fields.Monetary(string="NVA Cost", help="Part of the activity cost charged by non-value-added activities (waste).")
    defect_qty = fields.Float(help="Defective units of the period (defect measure).")
    copq_amount = fields.Monetary(string="Cost of Poor Quality", help="Defect units x ABC unit cost.")


class CmcAbcActivityResult(models.Model):
    _name = "cmc.abc.activity.result"
    _inherit = ["cmc.run.result.mixin"]
    _description = "ABC Activity Pool Snapshot"
    _order = "activity_id, product_id"
    _check_company_auto = True

    run_id = fields.Many2one(
        "cmc.abc.run", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(related="run_id.company_id", store=True)
    currency_id = fields.Many2one(related="run_id.currency_id")
    activity_id = fields.Many2one("cmc.abc.activity", required=True, index=True)
    # restrict: deleting a product must not blank a computed result
    product_id = fields.Many2one("product.product", index=True, check_company=True, ondelete="restrict")
    cost_object_id = fields.Many2one("cmc.cost.object", index=True, check_company=True)
    driver_qty = fields.Float(string="Driver Quantity")
    pool_amount = fields.Monetary(
        string="Activity Pool", help="Whole cost pool of the activity, as frozen "
                                     "when the run was computed."
    )
    amount = fields.Monetary(string="Allocated Cost")
    unallocated = fields.Boolean(
        help="Set when the activity carries cost but the run has no driver line "
             "for it: the pool could not be allocated to any cost object."
    )
