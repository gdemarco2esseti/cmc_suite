from collections import defaultdict

from odoo import api, fields, models
from odoo.fields import Domain
from odoo.exceptions import AccessError, UserError, ValidationError

# why a sentinel: the context travels over RPC, so a plain True under
# ``cmc_auto_classify`` could be forged by any client. An ``object()`` cannot be
# serialised, hence only server code can put it there.
CMC_AUTO = object()

CAPACITY_DERIVED_FIELDS = (
    "fixed_cost",
    "fixed_cost_coefficient",
    "imputed_fixed_cost",
    "unused_capacity_cost",
    "utilization_rate",
)


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
        "cmc.cost.element", string="Cost Element", index=True, check_company=True,
        help="Nature of the cost (materials, direct labor, depreciation...).",
    )
    cmc_center_id = fields.Many2one(
        "account.analytic.account",
        string="Cost Center",
        compute="_compute_cmc_center_id",
        inverse="_inverse_cmc_center_id",
        search="_search_cmc_center_id",
        store=False,
        domain="['|', ('cmc_center_type', '!=', False), ('cmc_is_period', '=', True)]",
        help="Convenience field: routes the cost to a cost center regardless of "
        "the analytic plan it lives on.",
    )
    cmc_cost_amount = fields.Monetary(
        string="Cost",
        compute="_compute_cmc_cost_amount",
        inverse="_inverse_cmc_cost_amount",
        currency_field="currency_id",
        readonly=False,
        help="Cost as a positive figure; stored as a negative analytic amount, "
        "the Odoo sign of a cost.",
    )
    cmc_excluded = fields.Boolean(
        string="Excluded from Cost Base",
        copy=False,
        readonly=True,
        help="Excluded from the cost base: a system twin of a cost already "
        "counted elsewhere.",
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

    @api.depends("amount")
    def _compute_cmc_cost_amount(self):
        for line in self:
            line.cmc_cost_amount = -line.amount

    def _inverse_cmc_cost_amount(self):
        by_cost = defaultdict(set)
        for line in self:
            by_cost[line.cmc_cost_amount].add(line.id)
        for cost, ids in by_cost.items():
            self.browse(ids).amount = -cost

    @api.model
    def _cmc_check_one_amount(self, vals):
        if "amount" in vals and "cmc_cost_amount" in vals:
            raise UserError(self.env._("Set either Amount or Cost, not both: Cost is the Amount with the opposite sign."))

    @api.model
    def _cmc_cost_read_group(self, domain, groupby=()):
        """``_read_group`` whose last aggregate is the COST (positive) of the
        group: the one place where the analytic sign (cost < 0) is flipped."""
        return [
            (*row[:-1], -(row[-1] or 0.0))
            for row in self._read_group(domain, groupby=groupby, aggregates=["amount:sum"])
        ]

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

    def _search_cmc_center_id(self, operator, value):
        """The field is the first column of the Cost Entry screen: without a
        search implementation it cannot be filtered, grouped or sorted."""
        if operator in Domain.NEGATIVE_OPERATORS:
            return NotImplemented
        return Domain.OR(
            [[(fname, operator, value)] for fname in self._get_plan_fnames()]
        )

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
        """Capacity analyses whose center+period covers these fixed lines.

        Runs sudo on purpose: anybody may book an analytic line (timesheets,
        vendor bills, manufacturing), while capacity rows are CMC-only data.
        The author of the line must not need access to them just to keep the
        derived figures in sync.
        """
        CA = self.env["cmc.capacity.analysis"]
        fixed = self.filtered(
            lambda line: line.cmc_behavior == "fixed" and line.date and not line.cmc_excluded
        )
        if not fixed:
            return CA.sudo()
        # plan-aware: the center may live on any analytic plan, not just the
        # default column read by account_id.
        # core's _get_analytic_accounts() is a singleton API: ask line by line
        by_line = {line.id: line._get_analytic_accounts() for line in fixed}
        accounts = self.env["account.analytic.account"].union(*by_line.values())
        if not accounts:
            return CA.sudo()
        dates = fixed.mapped("date")
        candidates = CA.sudo().search(
            [
                ("center_id", "in", accounts.ids),
                ("date_from", "<=", max(dates)),
                ("date_to", ">=", min(dates)),
            ]
        )
        matched = set()
        for line in fixed:
            line_accounts = by_line[line.id]
            for ca in candidates:
                if (
                    ca.center_id in line_accounts
                    and ca.date_from <= line.date <= ca.date_to
                ):
                    matched.add(ca.id)
        return CA.sudo().browse(matched)

    def _cmc_touch_capacity(self, rows):
        """Let the ORM recompute the derived figures.

        Calling _compute_capacity() imperatively turns every assignment into a
        separate write() with full access checks — five writes per capacity row
        on every analytic-line CRUD. Scheduling the recompute batches them.
        """
        if not rows:
            return
        for fname in CAPACITY_DERIVED_FIELDS:
            self.env.add_to_compute(rows._fields[fname], rows)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and any("cmc_excluded" in vals for vals in vals_list):
            raise AccessError(self.env._("The cost base exclusion is set by the system only."))
        for vals in vals_list:
            self._cmc_check_one_amount(vals)
        lines = super().create(vals_list)
        lines._cmc_touch_capacity(lines._cmc_capacity_rows())
        return lines

    def write(self, vals):
        self._cmc_check_one_amount(vals)
        if "cmc_excluded" in vals and not self.env.su:
            raise AccessError(self.env._("The cost base exclusion is set by the system only."))
        # a twin cannot be forced back into the cost base by hand; only system
        # (superuser) writes may touch it
        if (
            not self.env.su
            and any(
                vals.get(fname)
                for fname in (
                    "cmc_cost_element_id",
                    "cmc_behavior",
                    "cmc_directness",
                    "cmc_classified_manually",
                )
            )
            and any(self.mapped("cmc_excluded"))
        ):
            raise UserError(
                self.env._("An excluded cost line cannot be classified: it is a twin of a cost already counted elsewhere.")
            )
        watched = {"amount", "date", "cmc_behavior", "cmc_excluded"} | set(self._get_plan_fnames())
        if not (vals.keys() & watched):
            return super().write(vals)
        rows_before = self._cmc_capacity_rows()
        res = super().write(vals)
        self._cmc_touch_capacity(rows_before | self._cmc_capacity_rows())
        return res

    def unlink(self):
        rows = self._cmc_capacity_rows()
        res = super().unlink()
        self._cmc_touch_capacity(rows)
        return res
