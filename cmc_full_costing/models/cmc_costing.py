from collections import defaultdict

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import SQL, float_is_zero, float_round

from odoo.addons.cmc_base.models.cmc_utils import largest_remainder


class CmcCostingRun(models.Model):
    _name = "cmc.costing.run"
    _inherit = ["cmc.reclass.mixin"]
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
    state = fields.Selection(
        selection=[("draft", "Draft"), ("done", "Computed")],
        default="draft",
        required=True,
    )
    product_ids = fields.Many2many(
        "product.product", string="Products in scope", check_company=True
    )
    line_ids = fields.One2many("cmc.costing.line", "run_id")
    result_ids = fields.One2many("cmc.costing.result", "run_id")

    _dates_ok = models.Constraint(
        "CHECK(date_to >= date_from)", "End date must be on/after start date."
    )

    # ---- constraints ----------------------------------------------------

    @api.constrains("date_from", "date_to", "closing_run_id")
    def _check_period_within_closing(self):
        """The costing window must sit inside the closing run window: a Q1
        closing must not be combined with Q3 period costs."""
        for run in self:
            closing = run.closing_run_id
            if closing.date_from and run.date_from < closing.date_from:
                raise ValidationError(
                    self.env._(
                        "The costing period of %(name)s starts before its closing "
                        "run period (%(start)s).",
                        name=run.name,
                        start=closing.date_from,
                    )
                )
            if closing.date_to and run.date_to > closing.date_to:
                raise ValidationError(
                    self.env._(
                        "The costing period of %(name)s ends after its closing "
                        "run period (%(end)s).",
                        name=run.name,
                        end=closing.date_to,
                    )
                )

    @api.onchange("closing_run_id")
    def _onchange_closing_run_id(self):
        """Default the costing window from the closing run it reads from."""
        for run in self:
            if run.closing_run_id.date_from and not run.date_from:
                run.date_from = run.closing_run_id.date_from
            if run.closing_run_id.date_to and not run.date_to:
                run.date_to = run.closing_run_id.date_to

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
        return {"title": self.env._("Full cost per configuration"),
                "subtitle": "%s · %s" % (self.name, self._reclass_period()),
                "currency": self._reclass_ccy(), "columns": cols, "rows": rows}

    # ---- engine ----------------------------------------------------------

    def _qty_rounding(self):
        """Rounding step for driver quantities (a quantity, never a currency)."""
        digits = self.env["decimal.precision"].precision_get(
            "Product Unit of Measure"
        )
        return 10 ** -digits

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

    def action_compute(self):
        if self.ids:
            # the lock reads committed rows, so push pending writes first
            self.env.flush_all()
            self.env.cr.execute(
                SQL(
                    "SELECT id FROM cmc_costing_run WHERE id IN %s FOR UPDATE",
                    tuple(self.ids),
                )
            )
        for run in self:
            run._compute_costing()
        return True

    def action_reset_to_draft(self):
        """Reopen a computed run so it can be recomputed deliberately."""
        self.state = "draft"
        return True

    def _compute_costing(self):
        self.ensure_one()
        if self.closing_run_id.state != "done":
            raise UserError(
                self.env._(
                    "Closing run %(name)s is not computed: its final centers "
                    "carry no reliable cost yet.",
                    name=self.closing_run_id.display_name,
                )
            )
        rounding = self.company_id.currency_id.rounding
        qty_rounding = self._qty_rounding()
        totals = self._final_center_totals()

        consume = defaultdict(lambda: defaultdict(float))
        for ln in self.line_ids:
            consume[ln.center_id.id][ln.product_id.id] += ln.driver_qty

        if self.base == "single":
            if not self.single_driver_id:
                raise ValidationError(
                    self.env._("Single base requires a plant-wide driver.")
                )
            pool = float_round(sum(totals.values()), precision_rounding=rounding)
            prod_qty = defaultdict(float)
            for pq in consume.values():
                for p, q in pq.items():
                    prod_qty[p] += q
            # one virtual plant-wide center (key False)
            center_map = {False: (pool, dict(prod_qty))}
        else:
            center_map = {c: (totals.get(c, 0.0), consume.get(c, {})) for c in totals}

        for key, (total, pq) in center_map.items():
            has_cost = not float_is_zero(total, precision_rounding=rounding)
            has_base = not float_is_zero(
                sum(pq.values()), precision_rounding=qty_rounding
            )
            if has_cost and not has_base:
                center = self.env["account.analytic.account"].browse(key)
                raise ValidationError(
                    self.env._(
                        "Cost center %(name)s has cost %(amt)s but no allocation "
                        "base (no product consumes its driver).",
                        name=center.display_name if key else self.env._("Plant"),
                        amt=total,
                    )
                )

        # scope = declared products plus any product consuming a driver, so no
        # indirect quota is computed without a matching result row (conservation)
        products = self.product_ids | self.line_ids.product_id
        direct = self._direct_cost(products)
        indirect = defaultdict(float)
        quotas = defaultdict(list)
        for key, (total, pq) in center_map.items():
            b = sum(pq.values())
            if float_is_zero(b, precision_rounding=qty_rounding):
                # no allocation base: a non-zero cost was already rejected above
                continue
            coeff = total / b
            raw = {p: coeff * q for p, q in pq.items()}
            shares = largest_remainder(
                float_round(total, precision_rounding=rounding), raw, rounding
            )
            for p, amount in shares.items():
                indirect[p] += amount
                quotas[p].append(
                    {
                        "center_id": key or False,
                        "coefficient": coeff,
                        "driver_qty": pq[p],
                        "amount": amount,
                    }
                )

        # units consumed per product (allocation base for period & figurative)
        prod_units = defaultdict(float)
        for ln in self.line_ids:
            prod_units[ln.product_id.id] += ln.driver_qty
        total_units = sum(prod_units.values())

        # TS-006: commercial/administrative period costs allocated to products
        period_pool = self._period_pool()
        if not float_is_zero(
            period_pool, precision_rounding=rounding
        ) and not float_is_zero(total_units, precision_rounding=qty_rounding):
            raw = {p: period_pool * u / total_units for p, u in prod_units.items()}
            comm_admin = largest_remainder(
                float_round(period_pool, precision_rounding=rounding), raw, rounding
            )
        else:
            comm_admin = {}

        # TS-002: figurative costs (direct to product, or indirect by units)
        figurative = self._figurative_costs(products, prod_units)
        refs = self._reference_prices(products)

        # the engine is the only writer of the derived rows: users get read-only
        # access to them (see security/ir.model.access.csv)
        self.result_ids.sudo().unlink()
        res_vals = []
        for product in products:
            p = product.id
            primo = float_round(direct.get(p, 0.0), precision_rounding=rounding)
            ind = float_round(indirect.get(p, 0.0), precision_rounding=rounding)
            industriale = float_round(primo + ind, precision_rounding=rounding)
            comm = float_round(comm_admin.get(p, 0.0), precision_rounding=rounding)
            complessivo = float_round(industriale + comm, precision_rounding=rounding)
            figur = float_round(figurative.get(p, 0.0), precision_rounding=rounding)
            eco = float_round(complessivo + figur, precision_rounding=rounding)
            ref = refs.get(p, 0.0)
            std_ref = float_round(ref, precision_rounding=rounding)
            variance = float_round(industriale - ref, precision_rounding=rounding)
            config = [
                (0, 0, {"sequence": 10, "level": "primo", "amount": primo,
                        "increment": primo, "source": "standard_price"}),
                (0, 0, {"sequence": 20, "level": "industriale", "amount": industriale,
                        "increment": ind, "source": "pool"}),
                (0, 0, {"sequence": 30, "level": "complessivo", "amount": complessivo,
                        "increment": comm, "source": "period"}),
                (0, 0, {"sequence": 40, "level": "economico_tecnico", "amount": eco,
                        "increment": figur, "source": "figurative"}),
            ]
            res_vals.append(
                {
                    "run_id": self.id,
                    "product_id": p,
                    "cost_primo": primo,
                    "cost_indiretto": ind,
                    "cost_industriale": industriale,
                    "cost_comm_admin": comm,
                    "cost_complessivo": complessivo,
                    "cost_figurativo": figur,
                    "cost_economico_tecnico": eco,
                    "standard_price_ref": std_ref,
                    "std_price_delta": variance,
                    "quota_ids": [(0, 0, q) for q in quotas.get(p, [])],
                    "config_ids": config,
                }
            )
        self.env["cmc.costing.result"].sudo().create(res_vals)
        self.state = "done"

    def _period_pool(self):
        """Commercial/administrative period costs: analytic lines on centers
        flagged as period (outside the closing/ribaltamento pool)."""
        self.ensure_one()
        centers = self.env["account.analytic.account"].search(
            [("cmc_is_period", "=", True),
             ("company_id", "in", [False] + self.company_id.ids)]
        )
        if not centers:
            return 0.0
        groups = centers._cmc_cost_groups(
            self.date_from, self.date_to, company=self.company_id
        )
        return sum(groups.values())

    def _figurative_costs(self, products, prod_units):
        """Figurative costs (economico-tecnico only): direct ones map to their
        product, indirect ones are spread by units. Conserves the pool."""
        self.ensure_one()
        rounding = self.company_id.currency_id.rounding
        qty_rounding = self._qty_rounding()
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
        total_units = sum(prod_units.values())
        if not float_is_zero(
            indirect_pool, precision_rounding=rounding
        ) and not float_is_zero(total_units, precision_rounding=qty_rounding):
            raw = {p: indirect_pool * u / total_units for p, u in prod_units.items()}
            for p, amount in largest_remainder(
                float_round(indirect_pool, precision_rounding=rounding), raw, rounding
            ).items():
                result[p] += amount
        return result

    def _reference_prices(self, products):
        """Reference unit price per product, resolved ONCE for the whole set
        (Community-safe; deepens to valuation only if stock is present).
        Keeping it out of the per-product loop avoids an N+1 quant read_group."""
        self.ensure_one()
        qty_rounding = self._qty_rounding()
        priced = products.with_company(self.company_id)
        refs = {p.id: p.standard_price for p in priced}
        if "total_value" in products._fields and "qty_available" in products._fields:
            for product in priced:
                qty = product.qty_available
                if not float_is_zero(qty, precision_rounding=qty_rounding):
                    refs[product.id] = product.total_value / qty
        return refs


class CmcCostingLine(models.Model):
    _name = "cmc.costing.line"
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
    quota_ids = fields.One2many("cmc.costing.quota", "result_id")
    config_ids = fields.One2many("cmc.costing.config", "result_id")


class CmcCostingQuota(models.Model):
    _name = "cmc.costing.quota"
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


class CmcCostingConfig(models.Model):
    _name = "cmc.costing.config"
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
    source = fields.Char()
