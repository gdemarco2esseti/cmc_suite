from collections import defaultdict

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Domain
from odoo.tools import float_compare, float_is_zero, float_round

from odoo.addons.cmc_base.models.cmc_utils import month_range, month_start, qty_rounding, spread


class CmcCostingRun(models.Model):
    _name = "cmc.costing.run"
    _inherit = ["cmc.run.mixin", "cmc.reclass.mixin"]
    _description = "Full Cost Run (single/multiple allocation base)"
    _order = "date_to desc, id desc"
    _check_company_auto = True

    name = fields.Char(required=True, default=lambda self: self.env._("Costing"))
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True
    )
    currency_id = fields.Many2one(related="company_id.currency_id")
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    closing_run_id = fields.Many2one(
        "cmc.closing.run",
        string="Closing Run",
        required=True,
        check_company=True,
        domain="[('state', '=', 'done')]",
        help="Source of the indirect cost landed on final centers.",
    )
    base = fields.Selection(
        selection=[("single", "Single base"), ("multiple", "Multiple base")],
        required=True,
        default="multiple",
    )
    single_driver_id = fields.Many2one(
        "cmc.cost.driver",
        string="Plant-wide Driver",
        check_company=True,
        help="Single allocation base used only with 'Single base'.",
    )
    product_ids = fields.Many2many(
        "product.product", string="Products in scope", check_company=True
    )
    prime_cost_source = fields.Selection(
        selection=[("standard", "Standard price"), ("actual", "Actual direct cost")],
        required=True,
        default="standard",
        help="Standard price: the prime cost is a per-unit standard reference (product standard price) and "
        "the direct lines already stamped on the final centers stay in the overhead pool, so the pool is "
        "spread whole (sum of overhead = sum of final-center residuals); standard cost x quantity plus "
        "variances is the standard-cost run, not this one.\n"
        "Actual direct cost: the prime cost is the direct cost lines of the period stamped with the product "
        "cost object, taken out of the overhead pool so each cost is counted once. A product without such "
        "lines falls back to its standard price x produced quantity.",
    )
    produced_measure_id = fields.Many2one(
        "cmc.cost.driver",
        string="Produced Quantity Measure",
        check_company=True,
        domain="[('measure_kind', '=', 'statistical')]",
        help="Statistical measure whose actual facts give the produced quantity of each product.",
    )
    capacity_basis = fields.Selection(
        selection=[("actual", "Actual driver"), ("practical", "Practical capacity")],
        required=True,
        default="actual",
        help="Actual driver: the whole center pool is spread over the driver used. Practical capacity: the "
        "fixed pool is charged at fixed cost / practical capacity per driver unit used; the idle part is "
        "reported apart (multiple base only).",
    )
    markup_percent = fields.Float(string="Mark-up %", help="Mark-up on the total unit cost for the cost-plus price.")
    unabsorbed_cost = fields.Monetary(readonly=True, copy=False, help="Fixed cost of the unused practical capacity.")
    line_ids = fields.One2many("cmc.costing.line", "run_id")
    result_ids = fields.One2many("cmc.costing.result", "run_id")
    capacity_ids = fields.One2many("cmc.costing.capacity", "run_id")

    _cmc_input_fields = (
        "company_id", "date_from", "date_to", "closing_run_id", "base",
        "single_driver_id", "product_ids", "line_ids",
        "prime_cost_source", "produced_measure_id", "capacity_basis", "markup_percent",
    )
    _cmc_result_fields = ("result_ids", "capacity_ids")

    _dates_ok = models.Constraint(
        "CHECK(date_to >= date_from)", "End date must be on/after start date."
    )

    # ---- constraints ----------------------------------------------------

    @api.constrains("date_from", "date_to", "closing_run_id", "prime_cost_source", "produced_measure_id")
    def _check_period_within_closing(self):
        """The costing window must sit inside the closing run window: a Q1
        closing must not be combined with Q3 period costs."""
        for run in self:
            closing = run.closing_run_id
            if run.date_from < closing.date_from:
                raise ValidationError(
                    self.env._(
                        "The costing period of %(name)s starts before its closing "
                        "run period (%(start)s).",
                        name=run.name,
                        start=closing.date_from,
                    )
                )
            if run.date_to > closing.date_to:
                raise ValidationError(
                    self.env._(
                        "The costing period of %(name)s ends after its closing "
                        "run period (%(end)s).",
                        name=run.name,
                        end=closing.date_to,
                    )
                )
            # why: actual lines / produced quantity of a part-period against a whole-closing residual double count
            if (run.prime_cost_source == "actual" or run.produced_measure_id) and (
                run.date_from != closing.date_from or run.date_to != closing.date_to
            ):
                raise ValidationError(
                    self.env._(
                        "Costing run %(name)s uses actual direct cost or a produced quantity: its period must equal "
                        "the closing run period (%(start)s - %(end)s).",
                        name=run.name,
                        start=closing.date_from,
                        end=closing.date_to,
                    )
                )

    @api.onchange("closing_run_id")
    def _onchange_closing_run_id(self):
        """Default the costing window from the closing run it reads from."""
        for run in self:
            if run.closing_run_id:
                run.date_from = run.date_from or run.closing_run_id.date_from
                run.date_to = run.date_to or run.closing_run_id.date_to

    # ---- reclassified (transposed) report --------------------------------

    def _cmc_reclass(self):
        self.ensure_one()
        res = self.result_ids
        cols = [{"key": "p%s" % r.product_id.id,
                 "label": r.product_id.display_name} for r in res]

        def vals(field):
            return {"p%s" % r.product_id.id: getattr(r, field) for r in res}

        def tot(field):
            return sum(res.mapped(field))

        rows = [
            {"label": self.env._("Prime cost"), "kind": "plain",
             "values": vals("cost_primo"), "total": tot("cost_primo")},
            {"label": self.env._("Industrial overhead"), "kind": "plain",
             "values": vals("cost_indiretto"), "total": tot("cost_indiretto")},
            {"label": self.env._("Industrial cost"), "kind": "subtotal",
             "values": vals("cost_industriale"), "total": tot("cost_industriale")},
            {"label": self.env._("Commercial & Administrative"), "kind": "plain",
             "values": vals("cost_comm_admin"), "total": tot("cost_comm_admin")},
            {"label": self.env._("Total cost"), "kind": "subtotal",
             "values": vals("cost_complessivo"), "total": tot("cost_complessivo")},
            {"label": self.env._("Figurative"), "kind": "plain",
             "values": vals("cost_figurativo"), "total": tot("cost_figurativo")},
            {"label": self.env._("Economic-technical cost"), "kind": "result",
             "values": vals("cost_economico_tecnico"),
             "total": tot("cost_economico_tecnico")},
        ]
        return self._reclass_payload(self.env._("Full cost per configuration"), cols, rows)

    # ---- engine ----------------------------------------------------------

    def _cmc_lock_rows(self):
        # closing rows first, then the costing rows: the order a closing reset
        # takes them (AD-002), so the two never deadlock
        self.closing_run_id._cmc_lock_rows()
        return super()._cmc_lock_rows()

    def _cmc_reset_values(self):
        return {**super()._cmc_reset_values(), "unabsorbed_cost": 0.0}

    def action_cmc_prefill_lines(self):
        """Driver lines from the actual facts of the allocation measures (one per final center, product, measure)."""
        self.ensure_one()
        if self.state != "draft":
            raise UserError(self.env._("Driver lines can only be prefilled on a draft run."))
        # why sudo: engine reads inside the run company perimeter
        groups = self.env["cmc.measure.quantity"].sudo()._read_group(
            self._cmc_fact_domain([
                ("driver_id.measure_kind", "=", "allocation"),
                ("center_id.cmc_center_type", "=", "final"),
                ("center_id", "!=", False),
                ("cost_object_id.res_model", "=", "product.product"),
            ]),
            ["center_id", "cost_object_id", "driver_id"],
            ["quantity:sum"],
        )
        product_of = self._cmc_object_products(obj.id for _c, obj, _d, _q in groups)
        wanted = set(self.product_ids.ids)
        qty_step = qty_rounding(self.env)
        lines = [
            (0, 0, {"center_id": center.id, "product_id": product_of[obj.id], "driver_id": driver.id, "driver_qty": qty})
            for center, obj, driver, qty in groups
            if float_compare(qty, 0.0, precision_rounding=qty_step) > 0 and (not wanted or product_of[obj.id] in wanted)
        ]
        if not lines:
            raise UserError(self.env._(
                "There are no driver quantities of the allocation measures for the period (and the products) of this run: "
                "enter them in Cost Base > Driver Quantities, then prefill again. The current lines are unchanged."
            ))
        self.line_ids = [(5, 0, 0)] + lines
        return True

    def _cmc_fact_domain(self, extra=()):
        # facts are monthly buckets (AD-105): the first month starts before date_from
        return Domain([
            ("version_id", "=", False),
            ("company_id", "=", self.company_id.id),
            ("date", ">=", month_start(self.date_from)),
            ("date", "<=", self.date_to),
        ]) & Domain(list(extra))

    def _cmc_object_products(self, object_ids):
        """{cost object id: product id} in one read."""
        # why sudo: the registry is system data, a user booking a cost does not own it
        rows = self.env["cmc.cost.object"].sudo().browse(set(object_ids)).read(["res_id"])
        return {row["id"]: row["res_id"] for row in rows}

    def _final_center_totals(self):
        self.ensure_one()
        lines = self.closing_run_id.line_ids.filtered(
            lambda l: l.center_type == "final"
        )
        return {l.center_id.id: l.cost_residual for l in lines}

    def _direct_cost(self, products):
        """Primo cost per product (direct materials + direct labor). Uses the
        product standard cost in the run company."""
        priced = products.with_company(self.company_id)
        return {p.id: p.standard_price for p in priced}

    def _direct_costs(self, center_ids):
        """{(center id, cost object id, behavior): cost} of the stamped direct lines of the period on these
        centers, product cost objects of the run company only: ONE grouped read per analytic plan."""
        # why sudo: a certified figure must not depend on whose lines the user can see; the company
        # and period domain is the scope
        centers = self.env["account.analytic.account"].sudo().browse(center_ids)
        Line = self.env["account.analytic.line"].sudo()
        base = Domain([
            ("cmc_excluded", "=", False),
            ("cmc_directness", "=", "direct"),
            ("company_id", "in", [False] + self.company_id.ids),
            ("date", ">=", self.date_from),
            ("date", "<=", self.date_to),
            ("cmc_cost_object_id.res_model", "=", "product.product"),
            ("cmc_cost_object_id.company_id", "=", self.company_id.id),
        ])
        costs = defaultdict(float)
        for plan, accounts in centers.grouped("plan_id").items():
            if not plan:
                continue
            col = plan._column_name()
            rows = Line._cmc_cost_read_group(base & Domain(col, "in", accounts.ids), [col, "cmc_cost_object_id", "cmc_behavior"])
            for center, obj, behavior, cost in rows:
                costs[(center.id, obj.id, behavior)] += cost
        return costs

    def _product_objects(self, products):
        """{product id: cost object id} of the EXISTING objects (never created at compute): one search."""
        # why sudo: see _cmc_object_products
        objects = self.env["cmc.cost.object"].sudo().search([
            ("res_model", "=", "product.product"),
            ("res_id", "in", products.ids),
            ("company_id", "=", self.company_id.id),
            ("object_type", "=", "product"),
        ])
        return {obj.res_id: obj.id for obj in objects}

    def _produced_quantities(self, objects):
        """{product id: produced quantity} = actual facts of the produced measure, all centers."""
        if not self.produced_measure_id or not objects:
            return {}
        groups = self.env["cmc.measure.quantity"].sudo()._read_group(
            self._cmc_fact_domain([
                ("driver_id", "=", self.produced_measure_id.id),
                ("cost_object_id", "in", list(objects.values())),
            ]),
            ["cost_object_id"],
            ["quantity:sum"],
        )
        by_object = {obj.id: qty for obj, qty in groups}
        return {p: by_object[obj] for p, obj in objects.items() if obj in by_object}

    def _cmc_compute(self):
        self.ensure_one()
        if self.closing_run_id.state != "done":
            raise UserError(
                self.env._(
                    "Closing run %(name)s is not computed: its final centers "
                    "carry no reliable cost yet.",
                    name=self.closing_run_id.display_name,
                )
            )
        # why sudo: feeders write engine facts of the run company; the user need not own them
        self.env["cmc.measure.quantity"].sudo()._cmc_refresh_feeders(self.date_from, self.date_to, company=self.company_id)
        totals = self._final_center_totals()
        direct = self._direct_costs(list(totals)) if self.prime_cost_source == "actual" else {}
        stamped = self._cmc_object_products(obj for _c, obj, _b in direct)
        # scope = declared products, any product consuming a driver (so no indirect quota is computed
        # without a matching result row: conservation) and any product with stamped direct cost
        products = self.product_ids | self.line_ids.product_id | self.env["product.product"].browse(set(stamped.values())).exists()
        objects = self._product_objects(products)
        produced = self._produced_quantities(objects)
        center_map = self._center_map(self._center_pools(totals, direct))
        capacity_vals = []
        if self.capacity_basis == "practical":
            center_map, capacity_vals = self._practical_capacity(center_map)
        indirect, indirect_variable, quotas = self._indirect_quotas(center_map)
        # units consumed per product (allocation base for period & figurative)
        prod_units = defaultdict(float)
        for ln in self.line_ids:
            prod_units[ln.product_id.id] += ln.driver_qty
        # TS-006: commercial/administrative period costs allocated to products
        period_total, period_variable = self._period_pool()
        comm_admin = self._spread_by_units(period_total, prod_units)
        comm_variable = self._spread_by_units(period_variable, prod_units)
        # TS-002: figurative costs (direct to product, or indirect by units)
        figurative = self._figurative_costs(products, prod_units)
        res_vals = self._result_vals(
            products, self._prime_costs(products, direct, stamped, produced), produced, indirect, indirect_variable,
            quotas, comm_admin, comm_variable, figurative, self._reference_prices(products),
        )
        # the engine is the only writer of the derived rows: users get read-only
        # access to them (see security/ir.model.access.csv)
        self.env["cmc.costing.result"].sudo().create(res_vals)
        self.env["cmc.costing.capacity"].sudo().create(capacity_vals)
        self.write({"unabsorbed_cost": sum(vals["unabsorbed_cost"] for vals in capacity_vals)})
        self._publish_rates(res_vals, objects)
        self._cmc_record_usage(self.closing_run_id)

    def _prime_costs(self, products, direct, stamped, produced):
        """{product id: (prime cost, variable part, origin)}."""
        standard = self._direct_cost(products)
        if self.prime_cost_source != "actual":
            # with a produced quantity the standard prime is a total, all variable
            qty = (lambda p: produced.get(p, 0.0)) if self.produced_measure_id else (lambda _p: 1.0)
            return {p: (price * qty(p), price * qty(p), "standard") for p, price in standard.items()}
        total, variable = defaultdict(float), defaultdict(float)
        for (_center, obj, behavior), cost in direct.items():
            total[stamped[obj]] += cost
            if behavior == "variable":
                variable[stamped[obj]] += cost
        prime = {}
        for p, price in standard.items():
            if p in total:
                prime[p] = (total[p], variable[p], "actual")
            else:
                fallback = price * produced.get(p, 0.0)
                prime[p] = (fallback, fallback, "standard_fallback")
        return prime

    def _center_pools(self, totals, direct):
        """{center id: (fixed pool, variable pool)} left to spread after the prime cost taken out in actual
        mode. Variable = the center's own variable cost; service allocations received count as fixed."""
        rounding = self.company_id.currency_id.rounding
        centers = self.env["account.analytic.account"].sudo().browse(list(totals))
        own_variable = centers._cmc_cost_groups(self.date_from, self.date_to, behavior="variable", company=self.company_id)
        direct_total, direct_variable = defaultdict(float), defaultdict(float)
        for (center, _obj, behavior), cost in direct.items():
            direct_total[center] += cost
            if behavior == "variable":
                direct_variable[center] += cost
        pools = {}
        for center, residual in totals.items():
            variable = own_variable.get(center, 0.0) - direct_variable[center]
            pools[center] = (
                float_round(residual - direct_total[center] - variable, precision_rounding=rounding),
                float_round(variable, precision_rounding=rounding),
            )
        return pools

    def _practical_capacity(self, center_map):
        """Charge the fixed pool of each center at fixed / practical capacity x driver used. Returns the
        map with the charged fixed pool and the capacity rows (idle cost apart, never negative)."""
        if self.base == "single":
            raise ValidationError(self.env._("Practical capacity needs the multiple base: a single plant-wide base has no center capacity."))
        rounding = self.company_id.currency_id.rounding
        costed = [c for c, (fixed, variable, _pq) in center_map.items() if fixed or variable]
        # why sudo: engine, scoped by company and period
        analyses = self.env["cmc.capacity.analysis"].sudo().search([
            ("company_id", "=", self.company_id.id),
            ("center_id", "in", costed),
            ("date_from", "<=", self.date_from),
            ("date_to", ">=", self.date_to),
        ])
        found = {center.id: records for center, records in analyses.grouped("center_id").items()}
        charged_map, rows = dict(center_map), []
        for center in costed:
            fixed, variable, pq = center_map[center]
            if len(found.get(center, ())) != 1:
                raise ValidationError(
                    self.env._(
                        "Cost center %(name)s needs exactly one capacity analysis covering %(start)s - %(end)s "
                        "for the practical capacity basis.",
                        name=self.env["account.analytic.account"].browse(center).display_name,
                        start=self.date_from,
                        end=self.date_to,
                    )
                )
            practical = found[center].capacity_practical
            driver = found[center].driver_id
            lines = self.line_ids.filtered(lambda ln, c=center, d=driver: ln.center_id.id == c and ln.driver_id == d)
            if not driver or not lines:
                raise ValidationError(
                    self.env._(
                        "Cost center %(name)s has no driver line on its capacity unit: the practical capacity "
                        "cannot be matched to the driver used.",
                        name=found[center].center_id.display_name,
                    )
                )
            used = sum(lines.mapped("driver_qty"))
            if fixed and practical <= 0:
                raise ValidationError(
                    self.env._(
                        "Cost center %(name)s has no practical capacity to charge its fixed cost on.",
                        name=found[center].center_id.display_name,
                    )
                )
            # used above practical: the whole pool is charged, there is no idle cost to report
            charged = fixed if used >= practical else float_round(fixed / practical * used, precision_rounding=rounding)
            charged_map[center] = (charged, variable, pq)
            rows.append({
                "run_id": self.id,
                "center_id": center,
                "fixed_cost": fixed,
                "variable_cost": variable,
                "capacity_practical": practical,
                "capacity_used": used,
                "fixed_rate": fixed / max(practical, used) if fixed else 0.0,
                "fixed_charged": charged,
                "unabsorbed_cost": float_round(fixed - charged, precision_rounding=rounding),
            })
        return charged_map, rows

    def _publish_rates(self, res_vals, objects):
        """Unit costs as rates on the product cost objects, one row per month covered (AD-108)."""
        if not self.produced_measure_id:
            return
        Object = self.env["cmc.cost.object"]
        months = month_range(self.date_from, self.date_to)
        components = (("total", "unit_cost_complessivo"), ("variable", "unit_cost_variable"), ("fixed", "unit_cost_fixed"))
        rows = [
            {"holder": Object.browse(objects[vals["product_id"]]), "driver": self.produced_measure_id,
             "component": component, "date": month, "value": vals[field]}
            for vals in res_vals
            if vals["produced_qty"] > 0
            for month in months
            for component, field in components
        ]
        # why sudo: rates of a run are engine rows (the context carries the run sentinel)
        self.env["cmc.rate"].sudo()._cmc_publish(self, rows)

    def _center_map(self, pools):
        """{center_id (False = plant-wide): (fixed pool, variable pool, {product_id: driver_qty})}.
        Fails loud on a cost without an allocation base."""
        rounding = self.company_id.currency_id.rounding
        qty_step = qty_rounding(self.env)
        consume = defaultdict(lambda: defaultdict(float))
        for ln in self.line_ids:
            consume[ln.center_id.id][ln.product_id.id] += ln.driver_qty
        if self.base == "single":
            if not self.single_driver_id:
                raise ValidationError(
                    self.env._("Single base requires a plant-wide driver.")
                )
            fixed = float_round(sum(f for f, _v in pools.values()), precision_rounding=rounding)
            variable = float_round(sum(v for _f, v in pools.values()), precision_rounding=rounding)
            prod_qty = defaultdict(float)
            for pq in consume.values():
                for p, q in pq.items():
                    prod_qty[p] += q
            # one virtual plant-wide center (key False)
            center_map = {False: (fixed, variable, dict(prod_qty))}
        else:
            center_map = {c: (*pools[c], consume.get(c, {})) for c in pools}
        for key, (fixed, variable, pq) in center_map.items():
            has_cost = not (float_is_zero(fixed, precision_rounding=rounding) and float_is_zero(variable, precision_rounding=rounding))
            has_base = not float_is_zero(sum(pq.values()), precision_rounding=qty_step)
            if has_cost and not has_base:
                center = self.env["account.analytic.account"].browse(key)
                raise ValidationError(
                    self.env._(
                        "Cost center %(name)s has cost %(amt)s but no allocation "
                        "base (no product consumes its driver).",
                        name=center.display_name if key else self.env._("Plant"),
                        amt=fixed + variable,
                    )
                )
        return center_map

    def _indirect_quotas(self, center_map):
        """Spread each center pools (fixed and variable apart) over its products by driver quantity.
        Returns ({product_id: indirect}, {product_id: variable part}, {product_id: [quota vals]})."""
        rounding = self.company_id.currency_id.rounding
        qty_step = qty_rounding(self.env)
        indirect = defaultdict(float)
        indirect_variable = defaultdict(float)
        quotas = defaultdict(list)
        for key, (fixed, variable, pq) in center_map.items():
            b = sum(pq.values())
            if float_is_zero(b, precision_rounding=qty_step):
                # no allocation base: a non-zero cost was already rejected
                continue
            fixed_part = spread(fixed, pq, rounding)
            variable_part = spread(variable, pq, rounding)
            for p in pq:
                amount = float_round(fixed_part[p] + variable_part[p], precision_rounding=rounding)
                indirect[p] += amount
                indirect_variable[p] += variable_part[p]
                quotas[p].append(
                    {
                        "center_id": key or False,
                        "coefficient": (fixed + variable) / b,
                        "driver_qty": pq[p],
                        "amount": amount,
                        "amount_fixed": fixed_part[p],
                        "amount_variable": variable_part[p],
                    }
                )
        return indirect, indirect_variable, quotas

    def _spread_by_units(self, pool, prod_units):
        """{product_id: share of pool by units}; empty without a pool. A pool
        with no driver units to spread it on fails loud, as a center cost
        without allocation base does: it would vanish from every product."""
        rounding = self.company_id.currency_id.rounding
        if float_is_zero(pool, precision_rounding=rounding):
            return {}
        if float_is_zero(sum(prod_units.values()), precision_rounding=qty_rounding(self.env)):
            raise ValidationError(
                self.env._(
                    "Costing run %(name)s has %(amt)s of period or figurative cost "
                    "but no driver consumption to spread it on.",
                    name=self.display_name,
                    amt=pool,
                )
            )
        return spread(pool, prod_units, rounding)

    def _result_vals(self, products, prime, produced, indirect, indirect_variable, quotas, comm_admin, comm_variable,
                     figurative, refs):
        """Result rows: the four cost configuration levels per product, plus the unit costs and the
        variable/fixed split of the total cost."""
        rounding = self.company_id.currency_id.rounding
        digits = self.env["decimal.precision"].precision_get("Product Price")
        source = {"standard": "standard_price", "actual": "direct_lines", "standard_fallback": "standard_fallback"}
        res_vals = []
        for product in products:
            p = product.id
            qty = produced.get(p, 0.0)

            def unit(amount, qty=qty):
                return float_round(amount / qty, precision_digits=digits) if qty > 0 else 0.0

            cost, cost_variable, origin = prime[p]
            primo = float_round(cost, precision_rounding=rounding)
            ind = float_round(indirect.get(p, 0.0), precision_rounding=rounding)
            industriale = float_round(primo + ind, precision_rounding=rounding)
            comm = float_round(comm_admin.get(p, 0.0), precision_rounding=rounding)
            complessivo = float_round(industriale + comm, precision_rounding=rounding)
            figur = float_round(figurative.get(p, 0.0), precision_rounding=rounding)
            eco = float_round(complessivo + figur, precision_rounding=rounding)
            variable = float_round(cost_variable + indirect_variable.get(p, 0.0) + comm_variable.get(p, 0.0), precision_rounding=rounding)
            ref = refs.get(p, 0.0)
            unit_complessivo = unit(complessivo)
            levels = (
                (10, "primo", primo, primo, source[origin]),
                (20, "industriale", industriale, ind, "pool"),
                (30, "complessivo", complessivo, comm, "period"),
                (40, "economico_tecnico", eco, figur, "figurative"),
            )
            res_vals.append(
                {
                    "run_id": self.id,
                    "product_id": p,
                    "cost_primo": primo,
                    "prime_cost_origin": origin,
                    "cost_indiretto": ind,
                    "cost_industriale": industriale,
                    "cost_comm_admin": comm,
                    "cost_complessivo": complessivo,
                    "cost_figurativo": figur,
                    "cost_economico_tecnico": eco,
                    "cost_variable": variable,
                    "cost_fixed": float_round(complessivo - variable, precision_rounding=rounding),
                    "produced_qty": qty,
                    "unit_cost_primo": unit(primo),
                    "unit_cost_industriale": unit(industriale),
                    "unit_cost_complessivo": unit_complessivo,
                    "unit_cost_economico_tecnico": unit(eco),
                    "unit_cost_variable": unit(variable),
                    "unit_cost_fixed": unit(complessivo - variable),
                    "price_cost_plus": float_round(unit_complessivo * (1 + self.markup_percent / 100), precision_rounding=rounding),
                    "standard_price_ref": float_round(ref, precision_rounding=rounding),
                    "std_price_delta": float_round(industriale - ref, precision_rounding=rounding),
                    "quota_ids": [(0, 0, q) for q in quotas.get(p, [])],
                    "config_ids": [
                        (0, 0, {"sequence": seq, "level": level, "amount": amount, "increment": increment,
                                "source": src, "unit_amount": unit(amount)})
                        for seq, level, amount, increment, src in levels
                    ],
                }
            )
        return res_vals

    def _period_pool(self):
        """Commercial/administrative period costs (total, variable part): analytic lines on centers
        flagged as period (outside the closing/ribaltamento pool)."""
        self.ensure_one()
        centers = self.env["account.analytic.account"].search(
            [("cmc_is_period", "=", True),
             ("company_id", "in", [False] + self.company_id.ids)]
        )
        if not centers:
            return 0.0, 0.0
        total = centers._cmc_cost_groups(self.date_from, self.date_to, company=self.company_id)
        variable = centers._cmc_cost_groups(self.date_from, self.date_to, behavior="variable", company=self.company_id)
        return sum(total.values()), sum(variable.values())

    def _figurative_costs(self, products, prod_units):
        """Figurative costs (economico-tecnico only): direct ones map to their
        product, indirect ones are spread by units. Conserves the pool."""
        self.ensure_one()
        domain = [("company_id", "in", [False] + self.company_id.ids)]
        # only figuratives overlapping the run period (undated = always applicable)
        if self.date_from:
            domain += ["|", ("date_to", "=", False), ("date_to", ">=", self.date_from)]
        if self.date_to:
            domain += ["|", ("date_from", "=", False), ("date_from", "<=", self.date_to)]
        figs = self.env["cmc.figurative.cost"].search(domain)
        result = defaultdict(float)
        indirect_pool = 0.0
        prod_ids = set(products.ids)
        for fc in figs:
            ref = fc.cost_object_ref
            if (
                fc.cmc_directness == "direct"
                and ref
                and ref._name == "product.product"
                and ref.id in prod_ids
            ):
                result[ref.id] += fc.amount
            else:
                indirect_pool += fc.amount
        for p, amount in self._spread_by_units(indirect_pool, prod_units).items():
            result[p] += amount
        return result

    def _reference_prices(self, products):
        """Reference unit price per product, resolved ONCE for the whole set
        (Community-safe; deepens to valuation only if stock is present).
        Keeping it out of the per-product loop avoids an N+1 quant read_group."""
        self.ensure_one()
        qty_step = qty_rounding(self.env)
        # why sudo: the stock valuation read needs stock/mrp ACLs a CMC user does not hold
        priced = products.sudo().with_company(self.company_id)
        refs = {p.id: p.standard_price for p in priced}
        if "total_value" in products._fields and "qty_available" in products._fields:
            for product in priced:
                qty = product.qty_available
                if not float_is_zero(qty, precision_rounding=qty_step):
                    refs[product.id] = product.total_value / qty
        return refs


class CmcCostingLine(models.Model):
    _name = "cmc.costing.line"
    _inherit = ["cmc.run.line.mixin"]
    _description = "Driver consumption per product per final center"
    _check_company_auto = True

    run_id = fields.Many2one(
        "cmc.costing.run", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(related="run_id.company_id", store=True)
    center_id = fields.Many2one(
        "account.analytic.account",
        string="Final Center",
        required=True,
        index=True,
        ondelete="restrict",
        check_company=True,
        domain="[('cmc_center_type', '=', 'final')]",
    )
    product_id = fields.Many2one(
        "product.product", required=True, index=True, ondelete="restrict"
    )
    driver_id = fields.Many2one(
        "cmc.cost.driver", required=True, ondelete="restrict", check_company=True
    )
    driver_qty = fields.Float(string="Driver Quantity", required=True)

    _uniq = models.Constraint(
        "UNIQUE(run_id, center_id, product_id, driver_id)",
        "One driver-consumption line per product/center/driver in a run.",
    )
    _qty_positive = models.Constraint(
        "CHECK(driver_qty > 0)",
        "Driver quantity must be greater than zero (a product must consume the "
        "driver to receive a share of the center cost).",
    )


class CmcCostingResult(models.Model):
    _name = "cmc.costing.result"
    _inherit = ["cmc.run.result.mixin"]
    _description = "Full cost per product (stored result)"
    _order = "run_id, product_id"

    run_id = fields.Many2one(
        "cmc.costing.run", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(related="run_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="run_id.currency_id")
    product_id = fields.Many2one(
        "product.product", required=True, index=True, ondelete="restrict"
    )
    cost_primo = fields.Monetary()
    prime_cost_origin = fields.Selection(
        selection=[("standard", "Standard price"), ("actual", "Actual direct cost"), ("standard_fallback", "Standard price (fallback)")],
        default="standard",
    )
    cost_indiretto = fields.Monetary()
    cost_industriale = fields.Monetary()
    cost_comm_admin = fields.Monetary(string="Commercial & Admin")
    cost_complessivo = fields.Monetary(string="Total Cost")
    cost_figurativo = fields.Monetary(string="Figurative")
    cost_economico_tecnico = fields.Monetary(string="Economic-Technical Cost")
    standard_price_ref = fields.Monetary(string="Std Price (ref)")
    std_price_delta = fields.Monetary(
        string="Industrial vs Std Delta",
        help="Indicative: total industrial cost minus the reference unit standard "
        "price. A true per-unit inventory reconciliation requires the produced "
        "quantity (not modelled here).",
    )
    cost_variable = fields.Monetary()
    cost_fixed = fields.Monetary()
    produced_qty = fields.Float(digits="Product Unit", help="Actual facts of the produced measure in the period (snapshot).")
    unit_cost_primo = fields.Float(digits="Product Price")
    unit_cost_industriale = fields.Float(digits="Product Price")
    unit_cost_complessivo = fields.Float(digits="Product Price")
    unit_cost_economico_tecnico = fields.Float(digits="Product Price")
    unit_cost_variable = fields.Float(digits="Product Price")
    unit_cost_fixed = fields.Float(digits="Product Price")
    price_cost_plus = fields.Monetary(help="Total unit cost plus the run mark-up.")
    quota_ids = fields.One2many("cmc.costing.quota", "result_id")
    config_ids = fields.One2many("cmc.costing.config", "result_id")

    def _cmc_unit_cost_curve(self, volumes):
        """Unit full cost at each volume: Cu = v + CF / Q."""
        self.ensure_one()
        points = []
        for quantity in volumes:
            if quantity <= 0:
                raise UserError(self.env._("A volume must be greater than zero."))
            fixed = self.cost_fixed / quantity
            points.append({
                "quantity": quantity,
                "unit_variable": self.unit_cost_variable,
                "unit_fixed": fixed,
                "unit_cost": self.unit_cost_variable + fixed,
            })
        return points

    def _cmc_bid_check(self, price):
        self.ensure_one()
        digits = self.env["decimal.precision"].precision_get("Product Price")
        return {
            "unit_full_cost": self.unit_cost_complessivo,
            "unit_variable_cost": self.unit_cost_variable,
            "margin": price - self.unit_cost_complessivo,
            "covers_full_cost": float_compare(price, self.unit_cost_complessivo, precision_digits=digits) >= 0,
            "covers_variable_cost": float_compare(price, self.unit_cost_variable, precision_digits=digits) >= 0,
        }


class CmcCostingQuota(models.Model):
    _name = "cmc.costing.quota"
    _inherit = ["cmc.run.result.mixin"]
    _description = "Indirect quota contributed by one center to one product"
    _order = "result_id, center_id"

    result_id = fields.Many2one(
        "cmc.costing.result", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(
        related="result_id.company_id", store=True, index=True
    )
    currency_id = fields.Many2one(related="result_id.currency_id")
    center_id = fields.Many2one(
        "account.analytic.account", string="Final Center", ondelete="restrict"
    )
    coefficient = fields.Float(string="Center Coefficient")
    driver_qty = fields.Float(string="Product Driver Qty")
    amount = fields.Monetary()
    amount_fixed = fields.Monetary()
    amount_variable = fields.Monetary()


class CmcCostingConfig(models.Model):
    _name = "cmc.costing.config"
    _inherit = ["cmc.run.result.mixin"]
    _description = "Cost configuration level per product (traceability)"
    _order = "result_id, sequence"

    result_id = fields.Many2one(
        "cmc.costing.result", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(
        related="result_id.company_id", store=True, index=True
    )
    currency_id = fields.Many2one(related="result_id.currency_id")
    sequence = fields.Integer(default=10)
    level = fields.Selection(
        selection=[
            ("primo", "Prime Cost"),
            ("industriale", "Industrial Cost"),
            ("complessivo", "Total Cost"),
            ("economico_tecnico", "Economic-Technical Cost"),
        ],
        required=True,
    )
    amount = fields.Monetary(string="Cumulative")
    increment = fields.Monetary(string="Increment")
    unit_amount = fields.Float(string="Cumulative per Unit", digits="Product Price")
    source = fields.Char()


class CmcCostingCapacity(models.Model):
    _name = "cmc.costing.capacity"
    _inherit = ["cmc.run.result.mixin"]
    _description = "Practical capacity charge per final center"
    _order = "run_id, center_id"

    run_id = fields.Many2one("cmc.costing.run", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="run_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="run_id.currency_id")
    center_id = fields.Many2one("account.analytic.account", string="Final Center", required=True, ondelete="restrict")
    fixed_cost = fields.Monetary()
    variable_cost = fields.Monetary()
    capacity_practical = fields.Float(digits="Product Unit")
    capacity_used = fields.Float(digits="Product Unit")
    fixed_rate = fields.Float(digits="Product Price", help="Fixed cost per unit of practical capacity.")
    fixed_charged = fields.Monetary()
    unabsorbed_cost = fields.Monetary(help="Fixed cost of the capacity not used, kept out of every product.")


class CmcCostingSimulate(models.TransientModel):
    _name = "cmc.costing.simulate"
    _description = "Bid and unit cost curve simulation"

    result_id = fields.Many2one("cmc.costing.result", required=True, ondelete="cascade")
    currency_id = fields.Many2one(related="result_id.currency_id")
    bid_price = fields.Monetary()
    volumes = fields.Char(help="Comma-separated volumes for the unit cost curve.")
    unit_full_cost = fields.Float(compute="_compute_bid_check", digits="Product Price")
    unit_variable_cost = fields.Float(compute="_compute_bid_check", digits="Product Price")
    margin = fields.Float(compute="_compute_bid_check", digits="Product Price")
    covers_full_cost = fields.Boolean(compute="_compute_bid_check")
    covers_variable_cost = fields.Boolean(compute="_compute_bid_check")
    curve_line_ids = fields.One2many("cmc.costing.simulate.line", "wizard_id")

    @api.depends("result_id", "bid_price")
    def _compute_bid_check(self):
        for wizard in self:
            check = wizard.result_id._cmc_bid_check(wizard.bid_price) if wizard.result_id else {}
            for fname in ("unit_full_cost", "unit_variable_cost", "margin", "covers_full_cost", "covers_variable_cost"):
                wizard[fname] = check.get(fname, 0.0)

    def action_cmc_curve(self):
        self.ensure_one()
        try:
            volumes = [float(token) for token in (self.volumes or "").split(",") if token.strip()]
        except ValueError as err:
            raise UserError(self.env._("Volumes must be numbers separated by commas.")) from err
        if not volumes:
            raise UserError(self.env._("Enter at least one volume."))
        self.curve_line_ids = [(5, 0, 0)] + [(0, 0, point) for point in self.result_id._cmc_unit_cost_curve(volumes)]
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }


class CmcCostingSimulateLine(models.TransientModel):
    _name = "cmc.costing.simulate.line"
    _description = "Point of the unit cost curve"
    _order = "quantity"

    wizard_id = fields.Many2one("cmc.costing.simulate", required=True, ondelete="cascade")
    quantity = fields.Float()
    unit_variable = fields.Float(digits="Product Price")
    unit_fixed = fields.Float(digits="Product Price")
    unit_cost = fields.Float(digits="Product Price")

