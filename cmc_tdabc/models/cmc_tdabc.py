from collections import defaultdict

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare, float_is_zero, float_round

from odoo.addons.cmc_base.models.cmc_utils import largest_remainder

# Times are kept in minutes throughout the module; one hundredth of a minute is
# the smallest amount that is still meaningful. Used both to round stored times
# and as the explicit precision of every "is this divisor zero?" guard: a bare
# `> 0` lets a practical capacity of 1e-12 through and yields a rate of 1e12,
# which then makes largest_remainder walk millions of rounding steps.
MINUTE_ROUNDING = 0.01
# Volumes are counts (integers or derived line counts).
VOLUME_ROUNDING = 1e-6


class CmcTdabcCharacteristic(models.Model):
    _name = "cmc.tdabc.characteristic"
    _description = "TDABC Cost-Object Characteristic"
    _order = "code, name"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True, index=True)
    ttype = fields.Selection(
        [("bool", "Boolean flag"), ("number", "Numeric")],
        required=True, default="bool", string="Type",
    )
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company, index=True
    )

    _code_uniq = models.Constraint(
        "UNIQUE(code, company_id)",
        "Characteristic code must be unique per company.",
    )


class CmcTdabcCharValue(models.Model):
    _name = "cmc.tdabc.char.value"
    _description = "TDABC Characteristic Value on a Cost Object"
    _check_company_auto = True

    characteristic_id = fields.Many2one(
        "cmc.tdabc.characteristic", required=True, check_company=True
    )
    value = fields.Float(default=1.0)
    product_tmpl_id = fields.Many2one("product.template", index=True, ondelete="cascade")
    partner_id = fields.Many2one("res.partner", index=True, ondelete="cascade")
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company, index=True
    )

    _one_owner = models.Constraint(
        "CHECK((product_tmpl_id IS NULL) != (partner_id IS NULL))",
        "A characteristic value belongs to exactly one cost object (product or partner).",
    )
    # _char_map() builds {characteristic_id: value}: two rows for the same
    # characteristic on the same owner would silently drop one, with the winner
    # decided by _order. One partial index per owner branch (the other owner is
    # always NULL); COALESCE keeps company-shared rows (company_id IS NULL) from
    # escaping the uniqueness through SQL's "NULLs are distinct" rule.
    _product_char_uniq = models.UniqueIndex(
        "(characteristic_id, product_tmpl_id, COALESCE(company_id, 0)) "
        "WHERE product_tmpl_id IS NOT NULL",
        "This characteristic is already set on this product.",
    )
    _partner_char_uniq = models.UniqueIndex(
        "(characteristic_id, partner_id, COALESCE(company_id, 0)) "
        "WHERE partner_id IS NOT NULL",
        "This characteristic is already set on this partner.",
    )

    @api.constrains("value", "characteristic_id")
    def _check_bool_value(self):
        """`ttype` is declared required and documented as bool/number but the
        value column is a plain Float: without this a 'bool' characteristic with
        value 7.5 would multiply its time-equation coefficient by 7.5."""
        for rec in self:
            if rec.characteristic_id.ttype != "bool":
                continue
            is_flag = float_is_zero(
                rec.value, precision_rounding=VOLUME_ROUNDING
            ) or float_is_zero(rec.value - 1.0, precision_rounding=VOLUME_ROUNDING)
            if not is_flag:
                raise ValidationError(self.env._(
                    "Characteristic %(char)s is a boolean flag: its value must "
                    "be 0 or 1, not %(value)s.",
                    char=rec.characteristic_id.display_name, value=rec.value,
                ))


class CmcTdabcResource(models.Model):
    _name = "cmc.tdabc.resource"
    _description = "TDABC Resource (capacity supplier)"
    _order = "name"

    name = fields.Char(required=True, translate=True)
    cost = fields.Monetary(string="Period Cost")
    theoretical_time = fields.Float(string="Theoretical Time (min)")
    util_factor = fields.Float(string="Utilization %", default=80.0)
    practical_capacity = fields.Float(
        compute="_compute_capacity", store=True, string="Practical Capacity (min)"
    )
    rate = fields.Float(
        compute="_compute_capacity", store=True, string="Cost per Minute", digits=(16, 6)
    )
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company, index=True
    )
    currency_id = fields.Many2one(related="company_id.currency_id")

    _util_factor_positive = models.Constraint(
        "CHECK(util_factor >= 0)",
        "Utilization % cannot be negative.",
    )

    @api.depends("cost", "theoretical_time", "util_factor")
    def _compute_capacity(self):
        for r in self:
            practical = r.theoretical_time * (r.util_factor / 100.0)
            r.practical_capacity = practical
            r.rate = 0.0 if float_compare(
                practical, 0.0, precision_rounding=MINUTE_ROUNDING
            ) <= 0 else (r.cost / practical)


class CmcTdabcActivity(models.Model):
    _name = "cmc.tdabc.activity"
    _description = "TDABC Activity"
    _order = "code, name"
    _check_company_auto = True

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True, index=True)
    resource_id = fields.Many2one(
        "cmc.tdabc.resource", required=True, check_company=True
    )
    base_time = fields.Float(string="Base Time (min)")
    term_ids = fields.One2many("cmc.tdabc.term", "activity_id")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company, index=True
    )

    def _char_map(self, product, partner, company=None):
        """{characteristic_id: value} for the cost object. bool present => its
        value (default 1.0); number => its value. Missing => absent (treated 0).
        When `company` is given, only that company's (or company-shared) values
        apply, so a run never absorbs another company's characteristic values."""
        self.ensure_one()
        values = (
            product.product_tmpl_id.tdabc_char_value_ids if product
            else partner.tdabc_char_value_ids
        )
        if company:
            values = values.filtered(
                lambda v: not v.company_id or v.company_id == company
            )
        return {v.characteristic_id.id: v.value for v in values}

    def _unit_time(self, char_map):
        """base_time + Σ coeff * char_value. Structured equation, no eval."""
        self.ensure_one()
        t = self.base_time
        for term in self.term_ids:
            t += term.coefficient * char_map.get(term.characteristic_id.id, 0.0)
        return t


class CmcTdabcTerm(models.Model):
    _name = "cmc.tdabc.term"
    _description = "TDABC Time-Equation Term"
    _check_company_auto = True

    activity_id = fields.Many2one(
        "cmc.tdabc.activity", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(
        related="activity_id.company_id", store=True, index=True
    )
    characteristic_id = fields.Many2one(
        "cmc.tdabc.characteristic", required=True, check_company=True
    )
    coefficient = fields.Float(string="Coefficient (min)")


SCENARIOS = [("manual", "Estimate (equation)"), ("timesheet", "Actual (timesheet)")]


class CmcTdabcRun(models.Model):
    _name = "cmc.tdabc.run"
    _inherit = ["cmc.run.mixin", "cmc.reclass.mixin"]
    _description = "TDABC Costing Run"
    _check_company_auto = True

    name = fields.Char(required=True, default=lambda self: self.env._("TDABC Run"))
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company, index=True, required=True
    )
    currency_id = fields.Many2one(related="company_id.currency_id")
    date_from = fields.Date()
    date_to = fields.Date()
    line_ids = fields.One2many("cmc.tdabc.run.line", "run_id")
    result_ids = fields.One2many("cmc.tdabc.result", "run_id")
    capacity_result_ids = fields.One2many("cmc.tdabc.capacity.result", "run_id")
    scenario_warning = fields.Text(
        string="Scenario Warnings", readonly=True,
        help="Activities left out of a scenario by the last computation, and why.",
    )

    _cmc_input_fields = ("company_id", "date_from", "date_to", "line_ids")
    _cmc_result_fields = ("result_ids", "capacity_result_ids")

    def _cmc_reset_values(self):
        return {**super()._cmc_reset_values(), "scenario_warning": False}

    def write(self, vals):
        # an engine output: only Compute and Reset (under the sentinel) write it
        if "scenario_warning" in vals and not self._cmc_state_unlocked():
            raise UserError(self.env._("The results of a run are written by Compute only."))
        return super().write(vals)

    def _cmc_compute(self):
        self.ensure_one()
        # Aggregate analytic data ONCE and thread it through every scenario
        # (avoids re-running the same _read_group per scenario / per method).
        hours = self.env["account.analytic.line"]._cmc_tdabc_hours(
            self.date_from, self.date_to, self.company_id
        )
        derived_volumes = self._derived_volumes()
        scenarios = ["manual"]
        if any(not float_is_zero(v, precision_rounding=MINUTE_ROUNDING)
               for v in hours.values()):
            scenarios.append("timesheet")
        result_vals, cap_vals, warnings = [], [], []
        for scenario in scenarios:
            r, c, w = self._scenario_results(scenario, hours, derived_volumes)
            result_vals += r
            cap_vals += c
            warnings += w
        self.env["cmc.tdabc.result"].create(result_vals)
        self.env["cmc.tdabc.capacity.result"].create(cap_vals)
        self.scenario_warning = "\n".join(warnings) or False

    def _unit_times(self, scenario, hours, derived_volumes):
        """({run_line_id: unit_time_minutes}, [warning]) for the scenario.
        manual: base_time + Σ coeff*char. timesheet: flat Σhours/Σvolume per
        activity. A line absent from the mapping is not part of the scenario."""
        self.ensure_one()
        if scenario == "timesheet":
            return self._timesheet_unit_times(hours, derived_volumes)
        out = {}
        for line in self.line_ids:
            char_map = line.activity_id._char_map(
                line.product_id, line.partner_id, self.company_id
            )
            ut = line.activity_id._unit_time(char_map)
            if ut < 0.0:
                raise UserError(self.env._(
                    "Activity %(a)s yields a negative unit time (%(t).2f min) for a "
                    "cost object. Check the equation coefficients.",
                    a=line.activity_id.display_name, t=ut,
                ))
            out[line.id] = ut
        return out, []

    def _resolve_volume(self, line, derived_volumes):
        if line.volume_derived:
            return derived_volumes.get((line.activity_id.id,) + line._object_key(), 0)
        return line.volume

    def _derived_volumes(self):
        """{(activity_id, obj_type, obj_id): count} from analytic lines tagged
        with the activity, grouped by product/partner. Empty if nothing tagged.
        product_id on account.analytic.line is only added by modules such as
        account/hr_timesheet/sale."""
        self.ensure_one()
        AL = self.env["account.analytic.line"]
        has_product = "product_id" in AL._fields
        if not has_product and any(
            line.volume_derived and line.cost_object_type == "product"
            for line in self.line_ids
        ):
            # Silently returning 0 here would DISCARD the volume the user typed.
            raise UserError(self.env._(
                "Derived Volume on a product line needs the Product field on "
                "analytic lines, which none of the installed modules provides. "
                "Install Invoicing (account), Timesheets (hr_timesheet) or Sales "
                "(sale), or untick Derived Volume and enter the volume manually."
            ))
        domain = AL._cmc_tdabc_domain(self.date_from, self.date_to, self.company_id)
        # why sudo: the same scope as _cmc_tdabc_hours (all the company's lines,
        # whoever presses Compute): minutes over a narrower volume inflate rates
        AL = AL.sudo()
        out = {}
        if has_product:
            prod = AL._read_group(
                domain + [("product_id", "!=", False)],
                groupby=["cmc_tdabc_activity_id", "product_id"], aggregates=["__count"],
            )
            for act, product, count in prod:
                out[(act.id, "product", product.id)] = count
        part = AL._read_group(
            domain + [("partner_id", "!=", False)],
            groupby=["cmc_tdabc_activity_id", "partner_id"], aggregates=["__count"],
        )
        for act, partner, count in part:
            out[(act.id, "customer", partner.id)] = count
        return out

    def _timesheet_unit_times(self, hours, derived_volumes):
        """Flat empirical unit time per activity: Σ actual minutes / Σ volume.
        Same value applied to every cost object of that activity. Activities
        with zero total volume get unit_time 0 (skipped, no division)."""
        self.ensure_one()
        vol_by_activity = defaultdict(float)
        activity_by_id = {}
        for line in self.line_ids:
            vol_by_activity[line.activity_id.id] += self._resolve_volume(
                line, derived_volumes
            )
            activity_by_id[line.activity_id.id] = line.activity_id
        flat, excluded, warnings = {}, set(), []
        for act_id, total_vol in vol_by_activity.items():
            minutes = hours.get(act_id, 0.0)
            rate = 0.0 if float_is_zero(
                total_vol, precision_rounding=VOLUME_ROUNDING
            ) else (minutes / total_vol)
            if rate < 0.0:
                # A net-negative period (an in-period reversal) is real data.
                # Raising here would roll back the whole transaction: the user
                # would lose the previous results AND the `manual` estimate
                # scenario, which has nothing to do with timesheets.
                excluded.add(act_id)
                warnings.append(self.env._(
                    "Activity %(a)s has negative net actual hours (%(m).2f min) "
                    "over the period: excluded from the timesheet scenario. The "
                    "estimate scenario is unaffected.",
                    a=activity_by_id[act_id].display_name, m=minutes,
                ))
                continue
            flat[act_id] = rate
        unit_times = {
            line.id: flat[line.activity_id.id] for line in self.line_ids
            if line.activity_id.id not in excluded
        }
        return unit_times, warnings

    def _scenario_results(self, scenario, hours, derived_volumes):
        """Returns (result_vals, capacity_vals, warnings) for one scenario. Each
        resource's absorbed cost is conserved with largest-remainder; unused
        capacity is the EXACT residual (cost - absorbed), so the invariant
        Σ cost_absorbed + unused_cost == resource.cost always holds (no ±rounding
        drift from rounding the two parts independently)."""
        self.ensure_one()
        unit_times, warnings = self._unit_times(scenario, hours, derived_volumes)
        absorbed = self._absorbed_time(unit_times, derived_volumes)
        result_vals = self._result_rows(scenario, absorbed)
        cap_vals = self._capacity_rows(scenario, absorbed["resources"], absorbed["required"])
        return result_vals, cap_vals, warnings

    def _absorbed_time(self, unit_times, derived_volumes):
        """Time and raw cost absorbed per (resource, cost object, activity) by
        the lines in the scenario (``unit_times``). Fails loud on a resource
        without practical capacity."""
        raw_cost = defaultdict(float)     # key -> cost
        time_by_key = defaultdict(float)  # key -> minutes (accumulated, mirrors raw_cost)
        obj_meta = {}                     # key -> run line (for metadata)
        required = defaultdict(float)     # resource_id -> minutes
        resource_ids = {}                 # ordered set of resource ids
        for line in self.line_ids:
            res = line.activity_id.resource_id
            resource_ids[res.id] = None
            if float_compare(
                res.practical_capacity, 0.0, precision_rounding=MINUTE_ROUNDING
            ) <= 0:
                raise UserError(self.env._(
                    "Resource %(r)s has no practical capacity; cannot derive a rate.",
                    r=res.display_name,
                ))
            if line.id not in unit_times:
                continue  # activity not part of this scenario
            time_absorbed = unit_times[line.id] * self._resolve_volume(
                line, derived_volumes
            )
            key = (res.id,) + line._object_key() + (line.activity_id.id,)
            raw_cost[key] += time_absorbed * res.rate
            time_by_key[key] += time_absorbed
            required[res.id] += time_absorbed
            obj_meta[key] = line
        return {
            "resources": self.env["cmc.tdabc.resource"].browse(list(resource_ids)),
            "raw_cost": raw_cost, "time_by_key": time_by_key,
            "obj_meta": obj_meta, "required": required,
        }

    def _result_rows(self, scenario, absorbed):
        """Result rows: each resource's absorbed cost conserved to the cent."""
        rounding = self.company_id.currency_id.rounding
        required, obj_meta, time_by_key = absorbed["required"], absorbed["obj_meta"], absorbed["time_by_key"]
        by_res = defaultdict(dict)
        for key, cost in absorbed["raw_cost"].items():
            by_res[key[0]][key] = cost
        result_vals = []
        for res in absorbed["resources"]:
            target = float_round(
                required[res.id] * res.rate, precision_rounding=rounding
            )
            shares = largest_remainder(target, by_res[res.id], rounding)
            for key, cost in shares.items():
                line = obj_meta[key]
                is_product = line.cost_object_type == "product"
                result_vals.append({
                    "run_id": self.id, "scenario": scenario,
                    "activity_id": line.activity_id.id,
                    "cost_object_type": line.cost_object_type,
                    # write ONLY the column that matches cost_object_type: a row
                    # carrying both makes _cmc_reclass pick the product and the
                    # partner's cost silently vanishes from the report.
                    "product_id": line.product_id.id if is_product else False,
                    "partner_id": False if is_product else line.partner_id.id,
                    "time_absorbed": float_round(
                        time_by_key[key], precision_rounding=MINUTE_ROUNDING
                    ),
                    "cost_absorbed": cost,
                })
        return result_vals

    def _capacity_rows(self, scenario, resources, required):
        """Capacity rows: unused cost is the exact residual of the absorbed."""
        rounding = self.company_id.currency_id.rounding
        cap_vals = []
        for res in resources:
            req = required[res.id]
            practical = res.practical_capacity
            target = float_round(req * res.rate, precision_rounding=rounding)
            cap_vals.append({
                "run_id": self.id, "scenario": scenario, "resource_id": res.id,
                "required_time": float_round(req, precision_rounding=MINUTE_ROUNDING),
                "unused_time": float_round(
                    practical - req, precision_rounding=MINUTE_ROUNDING
                ),
                # residual plug: guarantees absorbed + unused == cost exactly
                "unused_cost": float_round(
                    res.cost - target, precision_rounding=rounding
                ),
                "capacity_use_pct": 0.0 if float_is_zero(
                    practical, precision_rounding=MINUTE_ROUNDING
                ) else (req / practical * 100.0),
                "over_capacity": req > practical,
            })
        return cap_vals

    def _cmc_reclass(self):
        self.ensure_one()
        res = self.result_ids

        def okey(r):
            return ("p%s" % r.product_id.id) if r.product_id \
                else ("c%s" % r.partner_id.id)

        def olabel(r):
            if r.product_id:
                return r.product_id.display_name
            if r.partner_id:
                return r.partner_id.display_name
            return self.env._("(no cost object)")

        cols, seen = [], set()
        for r in res:
            k = okey(r)
            if k not in seen:
                seen.add(k)
                cols.append({"key": k, "label": olabel(r)})

        labelmap = {"manual": self.env._("estimate"),
                    "timesheet": self.env._("actual")}
        by_scenario = defaultdict(lambda: defaultdict(float))
        total_values = defaultdict(float)
        for r in res:
            by_scenario[r.scenario][okey(r)] += r.cost_absorbed
            total_values[okey(r)] += r.cost_absorbed
        unused = defaultdict(float)
        for cres in self.capacity_result_ids:
            unused[cres.scenario] += cres.unused_cost
        scenarios = [s for s in ("manual", "timesheet") if s in by_scenario]
        rows = [{"label": self.env._("Absorbed cost · %(scenario)s", scenario=labelmap[s]),
                 "kind": "plain", "values": dict(by_scenario[s]),
                 "total": sum(by_scenario[s].values())} for s in scenarios]
        if len(scenarios) > 1:
            rows.append({"label": self.env._("Total absorbed cost"),
                         "kind": "subtotal", "values": dict(total_values),
                         "total": sum(total_values.values())})
        rows += [{"label": self.env._("Unused capacity · %(scenario)s", scenario=labelmap[s]),
                  "kind": "subtract", "values": None, "total": unused[s]} for s in scenarios]
        return self._reclass_payload(self.env._("TDABC absorbed cost (estimate vs actual)"), cols, rows)


class CmcTdabcRunLine(models.Model):
    _name = "cmc.tdabc.run.line"
    _inherit = ["cmc.run.line.mixin"]
    _description = "TDABC Input Line"
    _check_company_auto = True

    run_id = fields.Many2one(
        "cmc.tdabc.run", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(related="run_id.company_id", store=True)
    activity_id = fields.Many2one(
        "cmc.tdabc.activity", required=True, check_company=True
    )
    cost_object_type = fields.Selection(
        [("product", "Product"), ("customer", "Customer")],
        required=True, default="product",
    )
    product_id = fields.Many2one("product.product", index=True, check_company=True)
    partner_id = fields.Many2one("res.partner", index=True, check_company=True)
    volume = fields.Integer()
    volume_derived = fields.Boolean(
        string="Derived Volume",
        help="If set and the activity tags analytic lines, volume is counted "
             "from those lines per cost object.",
    )

    # Only the column matching cost_object_type may be set: a product line that
    # also carries a partner produces a result row with both columns filled.
    _obj_set = models.Constraint(
        "CHECK((cost_object_type = 'product' AND product_id IS NOT NULL "
        "AND partner_id IS NULL) "
        "OR (cost_object_type = 'customer' AND partner_id IS NOT NULL "
        "AND product_id IS NULL))",
        "Set the product (product line) or the customer (customer line), not both.",
    )
    _volume_positive = models.Constraint(
        "CHECK(volume >= 0)",
        "Volume cannot be negative.",
    )

    def _object_key(self):
        """('product', id) or ('customer', id) — stable cost-object identity."""
        self.ensure_one()
        if self.cost_object_type == "product":
            return ("product", self.product_id.id)
        return ("customer", self.partner_id.id)


class CmcTdabcResult(models.Model):
    _name = "cmc.tdabc.result"
    _inherit = ["cmc.run.result.mixin"]
    _description = "TDABC Result (cost per object per scenario)"
    _order = "scenario, activity_id"

    run_id = fields.Many2one(
        "cmc.tdabc.run", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(related="run_id.company_id", store=True)
    currency_id = fields.Many2one(related="run_id.currency_id")
    scenario = fields.Selection(SCENARIOS, required=True)
    # restrict: deleting master data must not blank computed results
    activity_id = fields.Many2one("cmc.tdabc.activity", ondelete="restrict")
    cost_object_type = fields.Selection(
        [("product", "Product"), ("customer", "Customer")]
    )
    product_id = fields.Many2one("product.product", index=True, ondelete="restrict")
    partner_id = fields.Many2one("res.partner", index=True, ondelete="restrict")
    time_absorbed = fields.Float(string="Time Absorbed (min)")
    cost_absorbed = fields.Monetary(string="Cost Absorbed")

    _obj_single = models.Constraint(
        "CHECK(product_id IS NULL OR partner_id IS NULL)",
        "A result row carries a product or a partner, never both.",
    )


class CmcTdabcCapacityResult(models.Model):
    _name = "cmc.tdabc.capacity.result"
    _inherit = ["cmc.run.result.mixin"]
    _description = "TDABC Capacity Result (per resource per scenario)"
    _order = "scenario, resource_id"

    run_id = fields.Many2one(
        "cmc.tdabc.run", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(related="run_id.company_id", store=True)
    currency_id = fields.Many2one(related="run_id.currency_id")
    scenario = fields.Selection(SCENARIOS, required=True)
    resource_id = fields.Many2one("cmc.tdabc.resource", ondelete="restrict")
    required_time = fields.Float(string="Required Time (min)")
    unused_time = fields.Float(string="Unused Time (min)")
    unused_cost = fields.Monetary(string="Unused Capacity Cost")
    capacity_use_pct = fields.Float(string="Capacity Use %")
    over_capacity = fields.Boolean()
