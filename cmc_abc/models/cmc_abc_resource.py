from collections import defaultdict

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class CmcAbcResource(models.Model):
    _name = "cmc.abc.resource"
    _description = "ABC Resource"
    _order = "code, id"
    _check_company_auto = True

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True, index=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    center_id = fields.Many2one("account.analytic.account", string="Cost Center", required=True, check_company=True, index=True)
    cost_element_ids = fields.Many2many(
        "cmc.cost.element", "cmc_abc_resource_element_rel", "resource_id", "element_id", string="Cost Elements",
        check_company=True, help="Empty: every cost element of the center.",
    )
    driver_basis = fields.Selection(
        [("percent", "Percent (survey)"), ("quantity", "Quantity (measured usage)")],
        required=True, default="percent", string="Driver Basis",
    )
    driver_id = fields.Many2one("cmc.cost.driver", string="Unit", check_company=True)

    _code_uniq = models.Constraint("UNIQUE(code, company_id)", "Resource code must be unique per company.")

    def _cmc_ledger_elements(self, centers):
        """{center id: cost element ids that have cost on it}: what 'every element' means today."""
        elements = defaultdict(set)
        # why sudo: the overlap rule must see the whole ledger, not the user's share of it
        Line = self.env["account.analytic.line"].sudo()
        for plan, accounts in centers.sudo().grouped("plan_id").items():
            col = plan._column_name()
            for center, element in Line._read_group([(col, "in", accounts.ids), ("cmc_excluded", "=", False)], [col, "cmc_cost_element_id"]):
                elements[center.id].add(element.id)
        return elements

    @api.constrains("center_id", "cost_element_ids", "active", "company_id")
    def _check_no_overlap(self):
        """One cost, one resource: two active resources of a center sharing an element would count it twice."""
        active = self.filtered("active")
        peers = self.search([
            ("active", "=", True), ("company_id", "in", active.company_id.ids), ("center_id", "in", active.center_id.ids),
        ])
        ledger = self._cmc_ledger_elements(peers.center_id)

        def covers(resource):
            return set(resource.cost_element_ids.ids) or ledger[resource.center_id.id]

        for resource in active:
            for peer in peers - resource:
                if peer.company_id == resource.company_id and peer.center_id == resource.center_id and (
                    not (resource.cost_element_ids or peer.cost_element_ids) or covers(resource) & covers(peer)
                ):
                    raise ValidationError(
                        self.env._(
                            "Resources %(a)s and %(b)s both cover cost of %(center)s: the same cost would be counted twice.",
                            a=resource.name, b=peer.name, center=resource.center_id.display_name,
                        )
                    )


class CmcAbcResourceLine(models.Model):
    _name = "cmc.abc.resource.line"
    _inherit = ["cmc.run.line.mixin"]
    _description = "ABC Resource Driver (resource -> activity)"
    _order = "run_id, resource_id, id"
    _check_company_auto = True

    run_id = fields.Many2one("cmc.abc.run", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="run_id.company_id", store=True)
    resource_id = fields.Many2one("cmc.abc.resource", required=True, check_company=True, index=True)
    activity_id = fields.Many2one("cmc.abc.activity", required=True, check_company=True, index=True)
    percent = fields.Float(help="Share of the resource used by the activity (percent-basis resources).")
    quantity = fields.Float(help="Measured usage of the resource by the activity (quantity-basis resources).")

    _weight_positive = models.Constraint(
        "CHECK(percent >= 0 AND quantity >= 0)", "Resource drivers cannot be negative."
    )

    @api.constrains("activity_id")
    def _check_activity_node(self):
        if any(line.activity_id.node_type != "activity" for line in self):
            raise ValidationError(self.env._("Resources are assigned to activities: a process or a task carries no cost."))


class CmcAbcCapacity(models.Model):
    _name = "cmc.abc.capacity"
    _inherit = ["cmc.run.line.mixin"]
    _description = "ABC Practical Capacity"
    _check_company_auto = True

    run_id = fields.Many2one("cmc.abc.run", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="run_id.company_id", store=True)
    activity_id = fields.Many2one("cmc.abc.activity", required=True, check_company=True)
    practical_capacity = fields.Float(required=True, help="Driver units the activity can supply in the period.")

    _activity_uniq = models.Constraint(
        "UNIQUE(run_id, activity_id)", "An activity has one practical capacity per run."
    )
    _capacity_positive = models.Constraint(
        "CHECK(practical_capacity > 0)", "Practical capacity must be greater than zero."
    )
