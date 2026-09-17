from odoo import api, fields, models
from odoo.exceptions import ValidationError


class AccountAnalyticAccount(models.Model):
    _inherit = "account.analytic.account"
    # NB: deliberately no _parent_name override — retargeting child_of /
    # parent_of for every reader of this core model buys nothing here
    # (no _parent_store, and the suite never queries the CMC hierarchy);
    # _check_cmc_center_cycle below guards the recursion explicitly.

    cmc_center_type = fields.Selection(
        selection=[
            ("auxiliary", "Auxiliary"),
            ("intermediate", "Productive - Intermediate"),
            ("final", "Final / Common"),
        ],
        string="Cost Center Type",
        index=True,
        help="Role of the cost center in the allocation flow: auxiliary and "
        "intermediate centers are closed onto final centers.",
    )
    cmc_parent_id = fields.Many2one(
        "account.analytic.account",
        string="Parent Cost Center",
        index="btree_not_null",
        check_company=True,
        ondelete="restrict",
    )
    cmc_child_ids = fields.One2many(
        "account.analytic.account", "cmc_parent_id", string="Sub Cost Centers"
    )
    cmc_is_period = fields.Boolean(
        string="Period Cost Center",
        help="Commercial/administrative period costs. Excluded from the closing "
        "(ribaltamento) pool; consumed only at the 'complessivo' cost level. "
        "A period center should NOT have a cost center type.",
    )

    @api.constrains("cmc_is_period", "cmc_center_type")
    def _check_period_no_type(self):
        for rec in self:
            if rec.cmc_is_period and rec.cmc_center_type:
                raise ValidationError(
                    self.env._(
                        "A period cost center must not also have a cost center "
                        "type (it would be double-counted in the ribaltamento)."
                    )
                )

    def _cmc_cost_groups(self, date_from=False, date_to=False, behavior=False,
                         by_element=False, company=False):
        """Plan-aware aggregation of analytic-line amounts for these centers.
        A center may live on any analytic plan, so its costs sit in that plan's
        column (not necessarily the default one). Returns {account_id: amount}
        or {(account_id, element_id): amount} when by_element."""
        Line = self.env["account.analytic.line"]
        base = []
        if company:
            base.append(("company_id", "in", [False] + company.ids))
        if date_from:
            base.append(("date", ">=", date_from))
        if date_to:
            base.append(("date", "<=", date_to))
        if behavior:
            base.append(("cmc_behavior", "=", behavior))
        result = {}
        for plan, accounts in self.grouped("plan_id").items():
            if not plan:
                continue
            col = plan._column_name()
            groupby = [col, "cmc_cost_element_id"] if by_element else [col]
            groups = Line._read_group(
                base + [(col, "in", accounts.ids)],
                groupby=groupby,
                aggregates=["amount:sum"],
            )
            for vals in groups:
                account = vals[0]
                if by_element:
                    element, amount = vals[1], vals[2]
                    key = (account.id, element.id if element else False)
                else:
                    amount = vals[1]
                    key = account.id
                result[key] = result.get(key, 0.0) + (amount or 0.0)
        return result

    @api.constrains("cmc_parent_id")
    def _check_cmc_center_cycle(self):
        for account in self:
            parent = account.cmc_parent_id
            seen = {account.id}
            while parent:
                if parent.id in seen:
                    raise ValidationError(
                        self.env._(
                            "A cost center cannot be its own ancestor "
                            "(cycle on %(name)s).",
                            name=account.display_name,
                        )
                    )
                seen.add(parent.id)
                parent = parent.cmc_parent_id
