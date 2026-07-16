from collections import defaultdict

from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_round

from odoo.addons.cmc_base.models.cmc_utils import largest_remainder


class CmcCostingRun(models.Model):
    _name = "cmc.costing.run"
    _description = "Full Cost Run (base unica/multipla)"
    _order = "date_to desc, id desc"
    _check_company_auto = True

    name = fields.Char(required=True, default=lambda self: self.env._("Costing"))
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True
    )
    currency_id = fields.Many2one(related="company_id.currency_id")
    date_from = fields.Date()
    date_to = fields.Date()
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
    product_ids = fields.Many2many("product.product", string="Products in scope")
    line_ids = fields.One2many("cmc.costing.line", "run_id")
    result_ids = fields.One2many("cmc.costing.result", "run_id")

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
            self.env.cr.execute(
                "SELECT id FROM cmc_costing_run WHERE id IN %s FOR UPDATE",
                (tuple(self.ids),),
            )
        for run in self:
            run._compute_costing()
        return True

    def _compute_costing(self):
        self.ensure_one()
        rounding = self.company_id.currency_id.rounding
        totals = self._final_center_totals()

        base = defaultdict(float)
        consume = defaultdict(lambda: defaultdict(float))
        for ln in self.line_ids:
            base[ln.center_id.id] += ln.driver_qty
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
            if total and not sum(pq.values()):
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
            coeff = total / b if b else 0.0
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
        if period_pool and total_units:
            raw = {p: period_pool * u / total_units for p, u in prod_units.items()}
            comm_admin = largest_remainder(
                float_round(period_pool, precision_rounding=rounding), raw, rounding
            )
        else:
            comm_admin = {}

        # TS-002: figurative costs (direct to product, or indirect by units)
        figurative = self._figurative_costs(products, prod_units)

        self.result_ids.unlink()
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
            std_ref, variance = self._reconcile(product, industriale, rounding)
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
        self.env["cmc.costing.result"].create(res_vals)
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
        if indirect_pool and total_units:
            raw = {p: indirect_pool * u / total_units for p, u in prod_units.items()}
            for p, amount in largest_remainder(
                float_round(indirect_pool, precision_rounding=rounding), raw, rounding
            ).items():
                result[p] += amount
        return result

    def _reconcile(self, product, industriale, rounding):
        """Compare the computed industrial cost to the product standard cost
        (Community-safe; deepens to valuation only if stock is present)."""
        priced = product.with_company(self.company_id)
        ref = priced.standard_price
        if "total_value" in product._fields and "qty_available" in product._fields:
            qty = priced.qty_available
            if qty:
                ref = priced.total_value / qty
        return (
            float_round(ref, precision_rounding=rounding),
            float_round(industriale - ref, precision_rounding=rounding),
        )


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
        check_company=True,
        domain="[('cmc_center_type', '=', 'final')]",
    )
    product_id = fields.Many2one("product.product", required=True, index=True)
    driver_id = fields.Many2one("cmc.cost.driver", required=True, check_company=True)
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
    company_id = fields.Many2one(related="run_id.company_id", store=True)
    currency_id = fields.Many2one(related="run_id.currency_id")
    product_id = fields.Many2one("product.product", required=True, index=True)
    cost_primo = fields.Monetary()
    cost_indiretto = fields.Monetary()
    cost_industriale = fields.Monetary()
    cost_comm_admin = fields.Monetary(string="Commercial & Admin")
    cost_complessivo = fields.Monetary(string="Costo Complessivo")
    cost_figurativo = fields.Monetary(string="Figurative")
    cost_economico_tecnico = fields.Monetary(string="Costo Economico-Tecnico")
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
    currency_id = fields.Many2one(related="result_id.currency_id")
    center_id = fields.Many2one("account.analytic.account", string="Final Center")
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
    currency_id = fields.Many2one(related="result_id.currency_id")
    sequence = fields.Integer(default=10)
    level = fields.Selection(
        selection=[
            ("primo", "Costo Primo"),
            ("industriale", "Costo Industriale"),
            ("complessivo", "Costo Complessivo"),
            ("economico_tecnico", "Costo Economico-Tecnico"),
        ],
        required=True,
    )
    amount = fields.Monetary(string="Cumulative")
    increment = fields.Monetary(string="Increment")
    source = fields.Char()
