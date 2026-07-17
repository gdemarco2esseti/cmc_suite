from collections import defaultdict

from odoo import api, fields, models
from odoo.tools import float_round

from odoo.addons.cmc_base.models.cmc_utils import largest_remainder


class CmcAbcActivity(models.Model):
    _name = "cmc.abc.activity"
    _description = "ABC Activity"
    _order = "code, name"
    _check_company_auto = True

    name = fields.Char(required=True, translate=True)
    code = fields.Char(index=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company, index=True
    )
    driver_id = fields.Many2one(
        "cmc.cost.driver", string="Default Activity Driver", check_company=True,
        help="Cost driver that typically measures this activity's output.",
    )


class CmcAbcRun(models.Model):
    _name = "cmc.abc.run"
    _inherit = ["cmc.reclass.mixin"]
    _description = "ABC Costing Run"
    _check_company_auto = True

    def _cmc_reclass(self):
        self.ensure_one()
        res = self.result_ids
        prods = res.mapped("product_id")
        cols = [{"key": "p%s" % p.id, "label": p.display_name} for p in prods]
        pools = self.env["account.analytic.line"]._cmc_activity_pools(
            self.date_from, self.date_to, self.company_id)
        # single pass over the input lines: driver qty per (activity, product)
        # and total driver qty per activity (avoids nested .filtered per read).
        qty_ap = defaultdict(float)
        qty_a = defaultdict(float)
        for line in self.line_ids:
            qty_ap[(line.activity_id.id, line.product_id.id)] += line.driver_qty
            qty_a[line.activity_id.id] += line.driver_qty
        rows = []
        for act in self.line_ids.mapped("activity_id"):
            # abs(): pools carry the signed analytic amount (costs are negative);
            # the engine (_compute_abc) uses abs() too, so the reclass reconciles
            # with the positive `Costo ABC totale` subtotal built from res.amount.
            pool = abs(pools.get(act.id, 0.0))
            tot_qty = qty_a[act.id]
            values = {}
            for p in prods:
                qty = qty_ap.get((act.id, p.id), 0.0)
                values["p%s" % p.id] = (pool * qty / tot_qty) if tot_qty else 0.0
            rows.append({"label": act.display_name, "kind": "plain",
                         "values": values, "total": pool})
        rows.append({"label": "Costo ABC totale", "kind": "subtotal",
                     "values": {"p%s" % r.product_id.id: r.amount for r in res},
                     "total": sum(res.mapped("amount"))})
        return {"title": "Costo per attività (ABC)",
                "subtitle": "%s · %s" % (self.name, self._reclass_period()),
                "currency": self._reclass_ccy(), "columns": cols, "rows": rows}

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
    state = fields.Selection(
        [("draft", "Draft"), ("done", "Done")], default="draft", readonly=True
    )
    line_ids = fields.One2many("cmc.abc.line", "run_id")
    result_ids = fields.One2many("cmc.abc.result", "run_id")

    def action_compute(self):
        if self.ids:
            self.env.cr.execute(
                "SELECT id FROM cmc_abc_run WHERE id IN %s FOR UPDATE",
                (tuple(self.ids),),
            )
        for run in self:
            run._compute_abc()
        return True

    def _compute_abc(self):
        """Stage 1: activity pools from tagged analytic lines. Stage 2: allocate
        each pool to the cost objects via the activity driver quantities, with
        largest-remainder conservation so no pool leaks on rounding."""
        self.ensure_one()
        rounding = self.company_id.currency_id.rounding
        pools = self.env["account.analytic.line"]._cmc_activity_pools(
            self.date_from, self.date_to, self.company_id
        )
        lines_by_activity = defaultdict(lambda: self.env["cmc.abc.line"])
        for line in self.line_ids:
            lines_by_activity[line.activity_id] += line
        object_cost = {p.id: 0.0 for p in self.line_ids.product_id}
        for activity, alines in lines_by_activity.items():
            pool = abs(pools.get(activity.id, 0.0))
            # UNIQUE(run, activity, product) guarantees one line per product
            qty_by_product = {line.product_id.id: line.driver_qty for line in alines}
            base = sum(qty_by_product.values())
            coeff = pool / base if base else 0.0
            raw = {p: coeff * q for p, q in qty_by_product.items()}
            shares = largest_remainder(
                float_round(pool, precision_rounding=rounding), raw, rounding
            )
            for product_id, amount in shares.items():
                object_cost[product_id] += amount
        self.result_ids.unlink()
        self.env["cmc.abc.result"].create([
            {
                "run_id": self.id,
                "product_id": product_id,
                "amount": float_round(amount, precision_rounding=rounding),
            }
            for product_id, amount in object_cost.items()
        ])
        self.state = "done"


class CmcAbcLine(models.Model):
    _name = "cmc.abc.line"
    _description = "ABC Input Line (activity driver consumption)"
    _check_company_auto = True

    run_id = fields.Many2one(
        "cmc.abc.run", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(related="run_id.company_id", store=True)
    activity_id = fields.Many2one(
        "cmc.abc.activity", required=True, check_company=True
    )
    product_id = fields.Many2one(
        "product.product", required=True, index=True, check_company=True
    )
    driver_id = fields.Many2one("cmc.cost.driver", check_company=True)
    driver_qty = fields.Float(
        string="Driver Quantity", required=True,
        help="Amount of the activity driver consumed by this product.",
    )

    _uniq = models.Constraint(
        "UNIQUE(run_id, activity_id, product_id)",
        "One input line per activity/product in a run.",
    )
    _qty_positive = models.Constraint(
        "CHECK(driver_qty > 0)",
        "Driver quantity must be greater than zero.",
    )


class CmcAbcResult(models.Model):
    _name = "cmc.abc.result"
    _description = "ABC Result (cost per object)"
    _order = "product_id"

    run_id = fields.Many2one(
        "cmc.abc.run", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(related="run_id.company_id", store=True)
    currency_id = fields.Many2one(related="run_id.currency_id")
    product_id = fields.Many2one("product.product", required=True, index=True)
    amount = fields.Monetary(string="ABC Cost")
