from collections import defaultdict

from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.fields import Domain


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
    cmc_responsibility_ids = fields.Many2many(
        "cmc.responsibility.center",
        "cmc_responsibility_center_account_rel",
        "center_id",
        "responsibility_id",
        string="Responsibility Centers",
        readonly=True,
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

    def _cmc_center_descendants(self):
        """Self + every CMC descendant: one search per tree level (bounded master data)."""
        tree = self
        level = self
        while level:
            # archived children keep their cost: same scope as the SQL roll-up view
            level = self.with_context(active_test=False).search([("cmc_parent_id", "in", level.ids)]) - tree
            tree |= level
        return tree

    def _cmc_center_subtrees(self):
        """{id: set of ids of the node and its descendants} for every record of self."""
        tree = self._cmc_center_descendants()
        children = defaultdict(list)
        for center in tree:
            children[center.cmc_parent_id.id].append(center.id)
        memo = {}

        def walk(node_id):
            if node_id not in memo:
                memo[node_id] = {node_id}.union(*(walk(child) for child in children[node_id]))
            return memo[node_id]

        return {center.id: walk(center.id) for center in self}

    def _cmc_cost_groups(self, date_from=False, date_to=False, behavior=False,
                         by_element=False, company=False, controllable=None):
        """Plan-aware aggregation of the COSTS (positive) of these centers.
        A center may live on any analytic plan, so its costs sit in that plan's
        column (not necessarily the default one). Returns {account_id: amount}
        or {(account_id, element_id): amount} when by_element. `controllable`
        keeps the lines whose element is (True) or is not (False) controllable;
        a line without element is not controllable."""
        Line = self.env["account.analytic.line"]
        base = Domain("cmc_excluded", "=", False)
        if company:
            # why sudo: a certified figure must not depend on who presses
            # Compute (timesheet rules show a user only their own lines); the
            # company domain below is the scope, the aggregate is read-only
            Line = Line.sudo()
            base &= Domain("company_id", "in", [False] + company.ids)
        if date_from:
            base &= Domain("date", ">=", date_from)
        if date_to:
            base &= Domain("date", "<=", date_to)
        if behavior:
            base &= Domain("cmc_behavior", "=", behavior)
        if controllable is True:
            base &= Domain("cmc_cost_element_id.cmc_controllable", "=", True)
        elif controllable is False:
            base &= Domain("cmc_cost_element_id", "=", False) | Domain("cmc_cost_element_id.cmc_controllable", "=", False)
        result = {}
        for plan, accounts in self.grouped("plan_id").items():
            if not plan:
                continue
            col = plan._column_name()
            groupby = [col, "cmc_cost_element_id"] if by_element else [col]
            groups = Line._cmc_cost_read_group(base & Domain(col, "in", accounts.ids), groupby=groupby)
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

    def _cmc_cost_rollup(self, date_from, date_to, company=False):
        """{id: cost of the node + all its descendants}: one cost read for the whole forest."""
        subtrees = self._cmc_center_subtrees()
        costs = self.browse(list(set().union(*subtrees.values())))._cmc_cost_groups(date_from, date_to, company=company)
        return {
            center_id: sum(costs.get(node_id, 0.0) for node_id in nodes)
            for center_id, nodes in subtrees.items()
        }

    def action_cmc_drilldown(self, date_from, date_to):
        """Cost-base lines of the subtree; the action runs as the user (no sudo)."""
        self.ensure_one()
        domain = Domain.OR(
            Domain(plan._column_name(), "in", accounts.ids)
            for plan, accounts in self._cmc_center_descendants().grouped("plan_id").items()
            if plan
        ) & Domain("cmc_excluded", "=", False) & Domain("date", ">=", date_from) & Domain("date", "<=", date_to)
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Cost lines of %(center)s", center=self.display_name),
            "res_model": "account.analytic.line",
            "view_mode": "list,form",
            "domain": list(domain),
        }

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
