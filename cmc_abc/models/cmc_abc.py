from collections import defaultdict

from odoo import api, fields, models
from odoo.tools import float_is_zero, float_round

from odoo.addons.cmc_base.models.cmc_utils import largest_remainder

# Explicit precision for activity-driver quantities. A bare `if base` truthiness
# test lets a degenerate 1e-18 through the CHECK(driver_qty > 0) constraint and
# yields an astronomic coefficient; largest_remainder then walks its residual one
# `rounding` step at a time (tens of millions of iterations). Below this
# threshold the driver base counts as absent and the pool stays unallocated.
DRIVER_QTY_ROUNDING = 1e-6


class CmcAbcActivity(models.Model):
    _name = "cmc.abc.activity"
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

    _code_uniq = models.Constraint(
        "UNIQUE(code, company_id)",
        "Activity code must be unique per company.",
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
    line_ids = fields.One2many("cmc.abc.line", "run_id")
    result_ids = fields.One2many("cmc.abc.result", "run_id")
    activity_result_ids = fields.One2many("cmc.abc.activity.result", "run_id")
    unallocated_amount = fields.Monetary(
        compute="_compute_unallocated_amount",
        string="Unallocated Activity Cost",
        help="Cost tagged to activities the run has no driver line for. It is "
             "NOT part of the ABC cost of any object.",
    )

    _cmc_input_fields = ("company_id", "date_from", "date_to", "line_ids")
    _cmc_result_fields = ("result_ids", "activity_result_ids")

    @api.depends("activity_result_ids.amount", "activity_result_ids.unallocated")
    def _compute_unallocated_amount(self):
        for run in self:
            run.unallocated_amount = sum(
                run.activity_result_ids.filtered("unallocated").mapped("amount")
            )

    def _cmc_compute(self):
        """Stage 1: activity pools from tagged analytic lines. Stage 2: allocate
        each pool to the cost objects via the activity driver quantities, with
        largest-remainder conservation so no pool leaks on rounding. Everything
        the reclass needs is frozen into cmc.abc.activity.result here."""
        self.ensure_one()
        rounding = self.company_id.currency_id.rounding
        pools = self.env["account.analytic.line"]._cmc_activity_pools(
            self.date_from, self.date_to, self.company_id
        )
        line_ids_by_activity = defaultdict(list)
        for line in self.line_ids:
            line_ids_by_activity[line.activity_id.id].append(line.id)
        object_cost = {p.id: 0.0 for p in self.line_ids.product_id}
        snapshot = []
        for activity_id, line_ids in line_ids_by_activity.items():
            alines = self.env["cmc.abc.line"].browse(line_ids)
            pool = float_round(pools.get(activity_id, 0.0), precision_rounding=rounding)
            # UNIQUE(run, activity, product) guarantees one line per product
            qty_by_product = {line.product_id.id: line.driver_qty for line in alines}
            base = sum(qty_by_product.values())
            if float_is_zero(base, precision_rounding=DRIVER_QTY_ROUNDING):
                if not float_is_zero(pool, precision_rounding=rounding):
                    snapshot.append(self._unallocated_vals(activity_id, pool))
                continue
            coeff = pool / base
            raw = {p: coeff * q for p, q in qty_by_product.items()}
            shares = largest_remainder(pool, raw, rounding)
            for product_id, amount in shares.items():
                object_cost[product_id] += amount
                snapshot.append({
                    "run_id": self.id,
                    "activity_id": activity_id,
                    "product_id": product_id,
                    "driver_qty": qty_by_product[product_id],
                    "pool_amount": pool,
                    "amount": amount,
                })
        # Cost tagged to an activity nobody entered a driver row for would
        # silently DISAPPEAR (no residual, no warning). Freeze it as an explicit
        # unallocated row so it shows in the reclass and on the run.
        for activity_id, net_pool in pools.items():
            if activity_id in line_ids_by_activity:
                continue
            pool = float_round(net_pool, precision_rounding=rounding)
            if not float_is_zero(pool, precision_rounding=rounding):
                snapshot.append(self._unallocated_vals(activity_id, pool))
        self.result_ids.unlink()
        self.activity_result_ids.unlink()
        self.env["cmc.abc.result"].create([
            {
                "run_id": self.id,
                "product_id": product_id,
                "amount": float_round(amount, precision_rounding=rounding),
            }
            for product_id, amount in object_cost.items()
        ])
        self.env["cmc.abc.activity.result"].create(snapshot)

    def _unallocated_vals(self, activity_id, pool):
        return {
            "run_id": self.id,
            "activity_id": activity_id,
            "product_id": False,
            "driver_qty": 0.0,
            "pool_amount": pool,
            "amount": pool,
            "unallocated": True,
        }

    def _cmc_reclass(self):
        """Transposed report. Reads ONLY data frozen at compute time: querying
        the analytic lines again here would mix live pools with stored results,
        so tagging a line after the run would print activity rows that do not
        sum to the subtotal."""
        self.ensure_one()
        if self.state != "done":
            return False
        res = self.result_ids
        prods = res.mapped("product_id")
        cols = [{"key": "p%s" % p.id, "label": p.display_name} for p in prods]
        zero = {"p%s" % p.id: 0.0 for p in prods}
        by_activity = defaultdict(dict)
        totals = defaultdict(float)
        unallocated = []
        for ares in self.activity_result_ids:
            if ares.unallocated:
                unallocated.append(ares)
                continue
            key = "p%s" % ares.product_id.id
            by_activity[ares.activity_id][key] = (
                by_activity[ares.activity_id].get(key, 0.0) + ares.amount
            )
            totals[ares.activity_id] += ares.amount
        rows = []
        for activity, values in by_activity.items():
            rows.append({"label": activity.display_name, "kind": "plain",
                         "values": dict(zero, **values),
                         "total": totals[activity]})
        rows.append({"label": self.env._("Total ABC cost"), "kind": "subtotal",
                     "values": {"p%s" % r.product_id.id: r.amount for r in res},
                     "total": sum(res.mapped("amount"))})
        for ares in unallocated:
            rows.append({
                "label": self.env._(
                    "Unallocated · %(activity)s",
                    activity=ares.activity_id.display_name,
                ),
                "kind": "plain", "values": dict(zero), "total": ares.amount,
            })
        return {"title": self.env._("Cost per activity (ABC)"),
                "subtitle": "%s · %s" % (self.name, self._reclass_period()),
                "currency": self._reclass_ccy(), "columns": cols, "rows": rows}


class CmcAbcLine(models.Model):
    _name = "cmc.abc.line"
    _inherit = ["cmc.run.line.mixin"]
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
    _check_company_auto = True

    run_id = fields.Many2one(
        "cmc.abc.run", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(related="run_id.company_id", store=True)
    currency_id = fields.Many2one(related="run_id.currency_id")
    product_id = fields.Many2one(
        "product.product", required=True, index=True, check_company=True
    )
    amount = fields.Monetary(string="ABC Cost")


class CmcAbcActivityResult(models.Model):
    _name = "cmc.abc.activity.result"
    _description = "ABC Activity Pool Snapshot"
    _order = "activity_id, product_id"
    _check_company_auto = True

    run_id = fields.Many2one(
        "cmc.abc.run", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(related="run_id.company_id", store=True)
    currency_id = fields.Many2one(related="run_id.currency_id")
    activity_id = fields.Many2one("cmc.abc.activity", required=True, index=True)
    product_id = fields.Many2one("product.product", index=True, check_company=True)
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
