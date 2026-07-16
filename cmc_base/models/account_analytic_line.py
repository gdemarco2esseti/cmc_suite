from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class AccountAnalyticLine(models.Model):
    _inherit = "account.analytic.line"

    cmc_behavior = fields.Selection(
        selection=[
            ("fixed", "Fixed"),
            ("variable", "Variable"),
            ("semi_variable", "Semi-variable"),
        ],
        string="Cost Behavior",
        index=True,
        help="Behavior of the cost with respect to the cost driver, within the "
        "relevant range.",
    )
    cmc_driver_id = fields.Many2one(
        "cmc.cost.driver", string="Cost Driver", check_company=True
    )
    cmc_cost_element_id = fields.Many2one(
        "cmc.cost.element", string="Cost Element", index=True,
        help="Nature of the cost (materials, direct labor, depreciation...).",
    )
    cmc_center_id = fields.Many2one(
        "account.analytic.account",
        string="Cost Center",
        compute="_compute_cmc_center_id",
        inverse="_inverse_cmc_center_id",
        store=False,
        domain="['|', ('cmc_center_type', '!=', False), ('cmc_is_period', '=', True)]",
        help="Convenience field: routes the cost to a cost center regardless of "
        "the analytic plan it lives on.",
    )
    cmc_relevance_min = fields.Float(string="Relevant Range Min")
    cmc_relevance_max = fields.Float(string="Relevant Range Max")
    cmc_directness = fields.Selection(
        selection=[("direct", "Direct"), ("indirect", "Indirect")],
        string="Imputation",
        default="indirect",
    )
    cmc_cost_object_ref = fields.Reference(
        selection="_selection_cost_object",
        string="Cost Object",
        help="The object this cost is referred to (product, line, project, cost "
        "center, customer, channel).",
    )
    cmc_cost_traceability = fields.Selection(
        selection=[("special", "Special"), ("common", "Common")],
        string="Traceability",
        compute="_compute_cmc_cost_traceability",
        store=False,
        help="Special vs common is RELATIVE to the cost object under evaluation, "
        "passed via the 'cmc_cost_object' context key.",
    )

    @api.constrains("cmc_relevance_min", "cmc_relevance_max")
    def _check_cmc_relevant_range(self):
        # A bound left at 0 (or unset) means "no bound": handles both the ORM
        # NULL and the form widget sending 0.0. Enforce only when BOTH are set.
        for line in self:
            lo, hi = line.cmc_relevance_min, line.cmc_relevance_max
            if lo and hi and hi < lo:
                raise ValidationError(
                    self.env._("Relevant range max must be greater than or equal to min.")
                )

    @api.model
    def _selection_cost_object(self):
        candidates = [
            ("product.product", "Product"),
            ("product.template", "Product Variant Template"),
            ("sale.order.line", "Sale Line"),
            ("project.project", "Project / Commessa"),
            ("account.analytic.account", "Cost Center"),
            ("res.partner", "Customer"),
        ]
        return [(model, label) for model, label in candidates if model in self.env]

    @api.depends(lambda self: self._get_plan_fnames())
    def _compute_cmc_center_id(self):
        for line in self:
            accounts = line._get_analytic_accounts()
            centers = accounts.filtered(
                lambda a: a.cmc_center_type or a.cmc_is_period
            )
            line.cmc_center_id = centers[:1].id if centers else False

    def _inverse_cmc_center_id(self):
        for line in self:
            center = line.cmc_center_id
            new_col = (
                center.plan_id._column_name() if center and center.plan_id else False
            )
            # set the new center first (so the line never has zero accounts)
            if new_col:
                line[new_col] = center.id
            # then clear any OTHER plan column holding a cost-center/period account
            for acc in line._get_analytic_accounts():
                if (acc.cmc_center_type or acc.cmc_is_period) and acc != center:
                    col = acc.plan_id._column_name()
                    if col != new_col:
                        line[col] = False

    def _is_directly_attributable_to(self, cost_object):
        """A cost is *special* for a cost object when it is directly and uniquely
        attributable to it; here, when the line points to that very object."""
        self.ensure_one()
        if len(cost_object) != 1 or not self.cmc_cost_object_ref:
            return False
        ref = self.cmc_cost_object_ref
        if not ref.exists() or not cost_object.exists():
            return False
        return ref._name == cost_object._name and ref.id == cost_object.id

    def cmc_classify_for(self, cost_object):
        """Public API: classify the line relative to an arbitrary cost object."""
        self.ensure_one()
        return "special" if self._is_directly_attributable_to(cost_object) else "common"

    @api.depends("cmc_cost_object_ref")
    @api.depends_context("cmc_cost_object")
    def _compute_cmc_cost_traceability(self):
        target = False
        ref = self.env.context.get("cmc_cost_object")
        if ref and isinstance(ref, str) and "," in ref:
            model, _sep, res_id = ref.partition(",")
            allowed = dict(self._selection_cost_object())
            if model in allowed and res_id.isdigit():
                target = self.env[model].browse(int(res_id)).exists()
        for line in self:
            if target:
                line.cmc_cost_traceability = line.cmc_classify_for(target)
            else:
                line.cmc_cost_traceability = False

    def cmc_ensure_classified(self):
        """Guard for downstream calculations: a cost without behavior
        classification must not silently feed a model."""
        for line in self:
            if not line.cmc_behavior:
                raise UserError(
                    self.env._(
                        "Cost line %(name)s has no behavior classification "
                        "(fixed/variable). Classify it before using it in cost "
                        "management calculations.",
                        name=line.display_name,
                    )
                )
        return True

    # ---- keep capacity analyses in sync with the underlying costs ----------

    def _cmc_capacity_rows(self):
        """Capacity analyses whose center+period covers these fixed lines."""
        CA = self.env["cmc.capacity.analysis"]
        accounts = self.account_id
        if not accounts:
            return CA
        candidates = CA.search([("center_id", "in", accounts.ids)])
        rows = CA
        for line in self:
            if line.cmc_behavior != "fixed" or not line.account_id or not line.date:
                continue
            for ca in candidates:
                if (
                    ca.center_id == line.account_id
                    and ca.date_from
                    and ca.date_to
                    and ca.date_from <= line.date <= ca.date_to
                ):
                    rows |= ca
        return rows

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines._cmc_capacity_rows()._compute_capacity()
        return lines

    def write(self, vals):
        rows_before = self._cmc_capacity_rows()
        res = super().write(vals)
        if vals.keys() & {"amount", "date", "cmc_behavior", "account_id"}:
            (rows_before | self._cmc_capacity_rows())._compute_capacity()
        return res

    def unlink(self):
        rows = self._cmc_capacity_rows()
        res = super().unlink()
        rows._compute_capacity()
        return res
