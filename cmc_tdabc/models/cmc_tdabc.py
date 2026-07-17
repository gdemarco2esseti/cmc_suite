from collections import defaultdict

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_round

from odoo.addons.cmc_base.models.cmc_utils import largest_remainder


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

    @api.depends("cost", "theoretical_time", "util_factor")
    def _compute_capacity(self):
        for r in self:
            practical = r.theoretical_time * (r.util_factor / 100.0)
            r.practical_capacity = practical
            r.rate = (r.cost / practical) if practical > 0 else 0.0


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

    activity_id = fields.Many2one(
        "cmc.tdabc.activity", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(
        related="activity_id.company_id", store=True, index=True
    )
    characteristic_id = fields.Many2one(
        "cmc.tdabc.characteristic", required=True
    )
    coefficient = fields.Float(string="Coefficient (min)")


SCENARIOS = [("manual", "Estimate (equation)"), ("timesheet", "Actual (timesheet)")]


class CmcTdabcRun(models.Model):
    _name = "cmc.tdabc.run"
    _inherit = ["cmc.reclass.mixin"]
    _description = "TDABC Costing Run"
    _check_company_auto = True

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
            return self.env._("(nessun oggetto)")

        cols, seen = [], set()
        for r in res:
            k = okey(r)
            if k not in seen:
                seen.add(k)
                cols.append({"key": k, "label": olabel(r)})

        labelmap = {"manual": "stima", "timesheet": "consuntivo"}
        scenarios = [s for s in ("manual", "timesheet")
                     if res.filtered(lambda x: x.scenario == s)]
        rows = []
        for s in scenarios:
            sres = res.filtered(lambda x: x.scenario == s)
            values = {}
            for r in sres:
                values[okey(r)] = values.get(okey(r), 0.0) + r.cost_absorbed
            rows.append({"label": "Costo assorbito · %s" % labelmap[s],
                         "kind": "plain", "values": values,
                         "total": sum(sres.mapped("cost_absorbed"))})
        if len(scenarios) > 1:
            tvalues = {}
            for r in res:
                tvalues[okey(r)] = tvalues.get(okey(r), 0.0) + r.cost_absorbed
            rows.append({"label": "Costo assorbito totale", "kind": "subtotal",
                         "values": tvalues,
                         "total": sum(res.mapped("cost_absorbed"))})
        for s in scenarios:
            cres = self.capacity_result_ids.filtered(lambda x: x.scenario == s)
            rows.append({"label": "Capacità inutilizzata · %s" % labelmap[s],
                         "kind": "subtract", "values": None,
                         "total": sum(cres.mapped("unused_cost"))})
        return {"title": "Costo assorbito TDABC (stima vs consuntivo)",
                "subtitle": "%s · %s" % (self.name, self._reclass_period()),
                "currency": self._reclass_ccy(), "columns": cols, "rows": rows}

    name = fields.Char(required=True, default=lambda self: self.env._("TDABC Run"))
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company, index=True, required=True
    )
    currency_id = fields.Many2one(related="company_id.currency_id")
    date_from = fields.Date()
    date_to = fields.Date()
    state = fields.Selection(
        [("draft", "Draft"), ("done", "Done")], default="draft", readonly=True
    )
    line_ids = fields.One2many("cmc.tdabc.run.line", "run_id")
    result_ids = fields.One2many("cmc.tdabc.result", "run_id")
    capacity_result_ids = fields.One2many("cmc.tdabc.capacity.result", "run_id")

    def action_compute(self):
        if self.ids:
            self.env.cr.execute(
                "SELECT id FROM cmc_tdabc_run WHERE id IN %s FOR UPDATE",
                (tuple(self.ids),),
            )
        for run in self:
            run._compute_tdabc()
        return True

    def _compute_tdabc(self):
        self.ensure_one()
        self.result_ids.unlink()
        self.capacity_result_ids.unlink()
        # Aggregate analytic data ONCE and thread it through every scenario
        # (avoids re-running the same _read_group per scenario / per method).
        hours = self.env["account.analytic.line"]._cmc_tdabc_hours(
            self.date_from, self.date_to, self.company_id
        )
        derived_volumes = self._derived_volumes()
        scenarios = ["manual"]
        if any(v for v in hours.values()):
            scenarios.append("timesheet")
        result_vals, cap_vals = [], []
        for scenario in scenarios:
            r, c = self._scenario_results(scenario, hours, derived_volumes)
            result_vals += r
            cap_vals += c
        self.env["cmc.tdabc.result"].create(result_vals)
        self.env["cmc.tdabc.capacity.result"].create(cap_vals)
        self.state = "done"

    def _unit_times(self, scenario, hours, derived_volumes):
        """{run_line_id: unit_time_minutes} for the scenario.
        manual: base_time + Σ coeff*char. timesheet: flat Σhours/Σvolume per activity."""
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
        return out

    def _resolve_volume(self, line, derived_volumes):
        if line.volume_derived:
            return derived_volumes.get((line.activity_id.id,) + line._object_key(), 0)
        return line.volume

    def _derived_volumes(self):
        """{(activity_id, obj_type, obj_id): count} from analytic lines tagged
        with the activity, grouped by product/partner. Empty if nothing tagged.
        product_id on account.analytic.line is only added by modules such as
        account/hr_timesheet/sale; runtime-soft: skip the product grouping when
        that field isn't installed rather than raising."""
        self.ensure_one()
        AL = self.env["account.analytic.line"]
        domain = AL._cmc_tdabc_domain(self.date_from, self.date_to, self.company_id)
        out = {}
        if "product_id" in AL._fields:
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
        with zero total volume get unit_time 0 (skipped, no division).
        Negative actual hours (correction timesheets) fail loud."""
        self.ensure_one()
        vol_by_activity = defaultdict(float)
        for line in self.line_ids:
            vol_by_activity[line.activity_id.id] += self._resolve_volume(
                line, derived_volumes
            )
        flat = {}
        for act_id, total_vol in vol_by_activity.items():
            minutes = hours.get(act_id, 0.0)
            rate = (minutes / total_vol) if total_vol else 0.0
            if rate < 0.0:
                raise UserError(self.env._(
                    "Activity id %(a)s has negative actual hours; its timesheet "
                    "unit time (%(t).2f min) is negative. Check the analytic lines.",
                    a=act_id, t=rate,
                ))
            flat[act_id] = rate
        return {line.id: flat.get(line.activity_id.id, 0.0) for line in self.line_ids}

    def _scenario_results(self, scenario, hours, derived_volumes):
        """Returns (result_vals, capacity_vals) for one scenario. Each resource's
        absorbed cost is conserved with largest-remainder; unused capacity is the
        EXACT residual (cost - absorbed), so the invariant
        Σ cost_absorbed + unused_cost == resource.cost always holds (no ±rounding
        drift from rounding the two parts independently)."""
        self.ensure_one()
        rounding = self.company_id.currency_id.rounding
        unit_times = self._unit_times(scenario, hours, derived_volumes)
        raw_cost = defaultdict(float)     # key -> cost
        time_by_key = defaultdict(float)  # key -> minutes (accumulated, mirrors raw_cost)
        obj_meta = {}                     # key -> run line (for metadata)
        required = defaultdict(float)     # resource_id -> minutes
        resources = self.env["cmc.tdabc.resource"]
        for line in self.line_ids:
            res = line.activity_id.resource_id
            resources |= res
            if res.practical_capacity <= 0.0:
                raise UserError(self.env._(
                    "Resource %(r)s has no practical capacity; cannot derive a rate.",
                    r=res.display_name,
                ))
            time_absorbed = unit_times[line.id] * self._resolve_volume(
                line, derived_volumes
            )
            key = (res.id,) + line._object_key() + (line.activity_id.id,)
            raw_cost[key] += time_absorbed * res.rate
            time_by_key[key] += time_absorbed
            required[res.id] += time_absorbed
            obj_meta[key] = line
        by_res = defaultdict(dict)
        for key, cost in raw_cost.items():
            by_res[key[0]][key] = cost
        result_vals = []
        for res in resources:
            target = float_round(
                required[res.id] * res.rate, precision_rounding=rounding
            )
            shares = largest_remainder(target, by_res[res.id], rounding)
            for key, cost in shares.items():
                line = obj_meta[key]
                result_vals.append({
                    "run_id": self.id, "scenario": scenario,
                    "activity_id": line.activity_id.id,
                    "cost_object_type": line.cost_object_type,
                    "product_id": line.product_id.id or False,
                    "partner_id": line.partner_id.id or False,
                    "time_absorbed": float_round(
                        time_by_key[key], precision_rounding=0.01
                    ),
                    "cost_absorbed": cost,
                })
        cap_vals = []
        for res in resources:
            req = required[res.id]
            practical = res.practical_capacity
            target = float_round(req * res.rate, precision_rounding=rounding)
            cap_vals.append({
                "run_id": self.id, "scenario": scenario, "resource_id": res.id,
                "required_time": float_round(req, precision_rounding=0.01),
                "unused_time": float_round(practical - req, precision_rounding=0.01),
                # residual plug: guarantees absorbed + unused == cost exactly
                "unused_cost": float_round(
                    res.cost - target, precision_rounding=rounding
                ),
                "capacity_use_pct": (req / practical * 100.0) if practical else 0.0,
                "over_capacity": req > practical,
            })
        return result_vals, cap_vals


class CmcTdabcRunLine(models.Model):
    _name = "cmc.tdabc.run.line"
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
    partner_id = fields.Many2one("res.partner", index=True)
    volume = fields.Integer()
    volume_derived = fields.Boolean(
        string="Derived Volume",
        help="If set and the activity tags analytic lines, volume is counted "
             "from those lines per cost object.",
    )

    _obj_set = models.Constraint(
        "CHECK((cost_object_type = 'product' AND product_id IS NOT NULL) "
        "OR (cost_object_type = 'customer' AND partner_id IS NOT NULL))",
        "Set the product (product line) or the customer (customer line).",
    )

    def _object_key(self):
        """('product', id) or ('customer', id) — stable cost-object identity."""
        self.ensure_one()
        if self.cost_object_type == "product":
            return ("product", self.product_id.id)
        return ("customer", self.partner_id.id)


class CmcTdabcResult(models.Model):
    _name = "cmc.tdabc.result"
    _description = "TDABC Result (cost per object per scenario)"
    _order = "scenario, activity_id"

    run_id = fields.Many2one(
        "cmc.tdabc.run", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(related="run_id.company_id", store=True)
    currency_id = fields.Many2one(related="run_id.currency_id")
    scenario = fields.Selection(SCENARIOS, required=True)
    activity_id = fields.Many2one("cmc.tdabc.activity")
    cost_object_type = fields.Selection(
        [("product", "Product"), ("customer", "Customer")]
    )
    product_id = fields.Many2one("product.product", index=True)
    partner_id = fields.Many2one("res.partner", index=True)
    time_absorbed = fields.Float(string="Time Absorbed (min)")
    cost_absorbed = fields.Monetary(string="Cost Absorbed")


class CmcTdabcCapacityResult(models.Model):
    _name = "cmc.tdabc.capacity.result"
    _description = "TDABC Capacity Result (per resource per scenario)"
    _order = "scenario, resource_id"

    run_id = fields.Many2one(
        "cmc.tdabc.run", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(related="run_id.company_id", store=True)
    currency_id = fields.Many2one(related="run_id.currency_id")
    scenario = fields.Selection(SCENARIOS, required=True)
    resource_id = fields.Many2one("cmc.tdabc.resource")
    required_time = fields.Float(string="Required Time (min)")
    unused_time = fields.Float(string="Unused Time (min)")
    unused_cost = fields.Monetary(string="Unused Capacity Cost")
    capacity_use_pct = fields.Float(string="Capacity Use %")
    over_capacity = fields.Boolean()
