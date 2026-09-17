from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools import SQL, float_compare, float_is_zero, float_round

from odoo.addons.cmc_base.models.cmc_utils import largest_remainder

# why: the allocation base of the physical-units method is a quantity, not an
# amount, so it must be tested against the quantity precision.
QTY_PRECISION_NAME = "Product Unit"


class CmcJointRun(models.Model):
    _name = "cmc.joint.run"
    _description = "Joint Cost Allocation Run"
    _check_company_auto = True

    name = fields.Char(required=True, default=lambda self: self.env._("Joint Run"))
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company, index=True,
        required=True,
    )
    currency_id = fields.Many2one(related="company_id.currency_id")
    joint_cost = fields.Monetary(
        string="Joint Cost", help="Common cost incurred up to the split-off "
        "point, to be allocated across the joint products.",
    )
    method = fields.Selection(
        selection=[
            ("physical", "Physical Units"),
            ("sales_value", "Sales Value at Split-off"),
            ("nrv", "Net Realizable Value"),
            ("constant_gm", "Constant Gross Margin"),
        ],
        required=True, default="physical",
    )
    date_from = fields.Date()
    date_to = fields.Date()
    state = fields.Selection(
        [("draft", "Draft"), ("done", "Done")], default="draft", readonly=True
    )
    line_ids = fields.One2many("cmc.joint.line", "run_id")
    result_ids = fields.One2many("cmc.joint.result", "run_id")

    _joint_cost_non_negative = models.Constraint(
        "CHECK(joint_cost >= 0)",
        "The joint cost pool cannot be negative.",
    )

    def action_compute(self):
        if self.ids:
            # why: flush first, otherwise the row lock is taken on a state the
            # current transaction has not written yet and a concurrent update
            # can slip in between.
            self.env.flush_all()
            self.env.cr.execute(SQL(
                "SELECT id FROM cmc_joint_run WHERE id IN %s FOR UPDATE",
                tuple(self.ids),
            ))
        for run in self:
            run._compute_joint()
        return True

    def action_reset_to_draft(self):
        """Drop the computed results and reopen the run for editing."""
        self.result_ids.unlink()
        self.write({"state": "draft"})
        return True

    def _invalidate_results(self):
        """Results no longer match their inputs: throw them away and reopen."""
        stale = self.filtered(lambda run: run.state == "done")
        if stale:
            stale.action_reset_to_draft()

    def _compute_joint(self):
        """By-products are recognised at their NRV, which reduces the joint cost
        pool; the net pool is then allocated to the main joint products by the
        chosen method, conserved to the cent."""
        self.ensure_one()
        rounding = self.company_id.currency_id.rounding
        mains = self.line_ids.filtered(lambda l: not l.is_byproduct)
        byproducts = self.line_ids.filtered(lambda l: l.is_byproduct)
        byproduct_nrv = {
            line: max(line.quantity * line.unit_sales_value - line.separable_cost, 0.0)
            for line in byproducts
        }
        net_joint = self.joint_cost - sum(byproduct_nrv.values())
        if float_compare(net_joint, 0.0, precision_rounding=rounding) < 0:
            raise UserError(self.env._(
                "By-product NRV (%(nrv)s) exceeds the joint cost pool (%(pool)s); "
                "the excess is a gain, not a negative cost on the main products.",
                nrv=sum(byproduct_nrv.values()), pool=self.joint_cost,
            ))
        if not mains and not float_is_zero(net_joint, precision_rounding=rounding):
            raise UserError(self.env._(
                "Add at least one main (non by-product) product to absorb the "
                "joint cost."
            ))

        alloc = self._allocate(net_joint, mains, rounding)

        self.result_ids.unlink()
        vals = []
        for line in mains:
            vals.append(self._result_vals(line, alloc.get(line, 0.0), rounding, False))
        for line in byproducts:
            vals.append(self._result_vals(
                line, byproduct_nrv[line], rounding, True))
        self.env["cmc.joint.result"].create(vals)
        self.state = "done"

    def _allocate(self, net_joint, mains, rounding):
        """Return {line: allocated_joint_cost}, summing exactly to net_joint."""
        method = self.method
        if method == "constant_gm":
            return self._allocate_constant_gm(net_joint, mains, rounding)
        base_rounding = rounding
        if method == "physical":
            weights = {line: line.quantity for line in mains}
            base_rounding = 10 ** -self.env["decimal.precision"].precision_get(
                QTY_PRECISION_NAME
            )
        elif method == "sales_value":
            weights = {line: line.quantity * line.unit_sales_value for line in mains}
        else:  # nrv: clamp per-line NRV at 0 (consistent with by-product NRV)
            weights = {
                line: max(
                    line.quantity * line.unit_sales_value - line.separable_cost, 0.0
                )
                for line in mains
            }
        base = sum(weights.values())
        if float_compare(base, 0.0, precision_rounding=base_rounding) <= 0:
            if float_is_zero(net_joint, precision_rounding=rounding):
                return {}
            raise UserError(self.env._(
                "No positive allocation base for the '%s' method: the joint cost "
                "cannot be split.", method,
            ))
        coeff = net_joint / base
        raw = {line: coeff * w for line, w in weights.items()}
        return largest_remainder(
            float_round(net_joint, precision_rounding=rounding), raw, rounding
        )

    def _allocate_constant_gm(self, net_joint, mains, rounding):
        """Each product keeps the same gross-margin %; the joint cost assigned is
        sales*(1-gm) - separable. Sums to net_joint analytically; the rounding
        residual is placed on the largest-sales product."""
        total_sales = sum(line.quantity * line.unit_sales_value for line in mains)
        if float_compare(total_sales, 0.0, precision_rounding=rounding) <= 0:
            if float_is_zero(net_joint, precision_rounding=rounding):
                return {}
            raise UserError(self.env._(
                "Constant-margin allocation needs a positive total sales value."
            ))
        total_sep = sum(line.separable_cost for line in mains)
        total_cost = net_joint + total_sep
        gm = (total_sales - total_cost) / total_sales
        rounded = {}
        for line in mains:
            sales = line.quantity * line.unit_sales_value
            rounded[line] = float_round(
                sales * (1 - gm) - line.separable_cost, precision_rounding=rounding
            )
        # why: gm is unbounded below; a product whose separable cost is large
        # relative to its sales value would absorb a NEGATIVE share of the pool,
        # which is not a cost allocation at all.
        offenders = [
            line.product_id.display_name for line in mains
            if float_compare(rounded[line], 0.0, precision_rounding=rounding) < 0
        ]
        if offenders:
            raise UserError(self.env._(
                "Constant-margin allocation would assign a negative joint cost to "
                "%(products)s: the total cost (joint pool %(pool)s plus separable "
                "costs %(separable)s) exceeds the total sales value "
                "%(sales)s. Use the Net Realizable Value or Sales Value at "
                "Split-off method instead.",
                products=", ".join(offenders),
                pool=net_joint, separable=total_sep, sales=total_sales,
            ))
        residual = float_round(
            net_joint - sum(rounded.values()), precision_rounding=rounding
        )
        if mains and not float_is_zero(residual, precision_rounding=rounding):
            # why: product_id as tie-break keeps the placement stable instead of
            # letting it depend on the order the lines happen to be created in.
            top = max(
                mains,
                key=lambda l: (l.quantity * l.unit_sales_value, l.product_id.id),
            )
            rounded[top] = float_round(
                rounded[top] + residual, precision_rounding=rounding
            )
        return rounded

    def _result_vals(self, line, allocated, rounding, is_byproduct):
        total = float_round(allocated + line.separable_cost, precision_rounding=rounding)
        # quantity > 0 is guaranteed by the _qty_positive CHECK on cmc.joint.line
        return {
            "run_id": self.id,
            "product_id": line.product_id.id,
            "allocated_joint_cost": float_round(allocated, precision_rounding=rounding),
            "separable_cost": line.separable_cost,
            "total_cost": total,
            "unit_cost": total / line.quantity,
            "is_byproduct": is_byproduct,
        }


class CmcJointLine(models.Model):
    _name = "cmc.joint.line"
    _description = "Joint Product Input Line"
    _check_company_auto = True

    run_id = fields.Many2one(
        "cmc.joint.run", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(related="run_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="run_id.currency_id")
    product_id = fields.Many2one(
        "product.product", required=True, index=True, check_company=True
    )
    quantity = fields.Float(required=True, default=1.0)
    unit_sales_value = fields.Monetary(
        string="Unit Sales Value",
        help="Selling price per unit (at split-off for the sales-value method, "
        "final price for NRV / constant-margin).",
    )
    separable_cost = fields.Monetary(
        string="Separable Cost",
        help="Cost to process this product beyond the split-off point.",
    )
    is_byproduct = fields.Boolean(
        string="By-product",
        help="By-products are valued at NRV and netted off the joint cost pool "
        "instead of receiving an allocation.",
    )

    # fields whose change makes an already computed result obsolete
    _INPUT_FIELDS = frozenset({
        "product_id", "quantity", "unit_sales_value", "separable_cost",
        "is_byproduct",
    })

    _uniq = models.Constraint(
        "UNIQUE(run_id, product_id)",
        "One line per product in a joint run.",
    )
    _qty_positive = models.Constraint(
        "CHECK(quantity > 0)",
        "Quantity must be greater than zero.",
    )
    _sales_value_non_negative = models.Constraint(
        "CHECK(unit_sales_value >= 0)",
        "The unit sales value cannot be negative.",
    )
    _separable_cost_non_negative = models.Constraint(
        "CHECK(separable_cost >= 0)",
        "The separable cost cannot be negative.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines.run_id._invalidate_results()
        return lines

    def write(self, vals):
        res = super().write(vals)
        if not self._INPUT_FIELDS.isdisjoint(vals):
            self.run_id._invalidate_results()
        return res

    def unlink(self):
        runs = self.run_id
        res = super().unlink()
        runs._invalidate_results()
        return res


class CmcJointResult(models.Model):
    _name = "cmc.joint.result"
    _description = "Joint Cost Result"
    _order = "is_byproduct, product_id"

    run_id = fields.Many2one(
        "cmc.joint.run", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(related="run_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="run_id.currency_id")
    product_id = fields.Many2one("product.product", required=True, index=True)
    allocated_joint_cost = fields.Monetary()
    separable_cost = fields.Monetary()
    total_cost = fields.Monetary()
    # why: a unit cost is a rate, not an amount; the currency's 2 decimals would
    # collapse small unit costs (a 4200 pool over 1M units) to zero.
    unit_cost = fields.Float(digits="Product Price")
    is_byproduct = fields.Boolean()
