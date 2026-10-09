from odoo import fields, models

from .cmc_abc_extend import LEVELS


class CmcAbcActivityCost(models.Model):
    _name = "cmc.abc.activity.cost"
    _inherit = ["cmc.run.result.mixin"]
    _description = "ABC Activity Cost (stage results)"
    _order = "process_id, activity_id"
    _check_company_auto = True

    run_id = fields.Many2one("cmc.abc.run", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="run_id.company_id", store=True)
    currency_id = fields.Many2one(related="run_id.currency_id")
    activity_id = fields.Many2one("cmc.abc.activity", required=True, index=True)
    # snapshots: the dictionary can change after the run
    process_id = fields.Many2one("cmc.abc.activity", string="Process", index=True)
    cost_level = fields.Selection(LEVELS)
    driver_id = fields.Many2one("cmc.cost.driver")
    driver_type = fields.Selection(
        [("transaction", "Transaction"), ("duration", "Duration"), ("intensity", "Intensity")]
    )
    resource_cost = fields.Monetary(help="Stage 1: resources and tags.")
    received_cost = fields.Monetary(help="Cost reassigned by other activities.")
    total_cost = fields.Monetary()
    fixed_cost = fields.Monetary()
    variable_cost = fields.Monetary()
    used_qty = fields.Float()
    practical_capacity = fields.Float()
    rate = fields.Float(digits=(16, 6))
    fixed_rate = fields.Float(digits=(16, 6))
    variable_rate = fields.Float(digits=(16, 6))
    charged_cost = fields.Monetary(help="Rate x used quantity: what the consumers are charged.")
    unused_cost = fields.Monetary(help="Unused practical capacity x fixed rate: reported, never charged.")


class CmcAbcFlow(models.Model):
    _name = "cmc.abc.flow"
    _inherit = ["cmc.run.result.mixin"]
    _description = "ABC Cost Flow (drill-back)"
    _order = "run_id, id"
    _check_company_auto = True

    run_id = fields.Many2one("cmc.abc.run", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="run_id.company_id", store=True)
    currency_id = fields.Many2one(related="run_id.currency_id")
    stage = fields.Selection(
        [
            ("resource", "Resource to activity"),
            ("tag", "Activity tag to activity"),
            ("trace", "Direct trace to object"),
            ("activity", "Activity to activity"),
            ("object", "To object"),
            ("unused", "Unused capacity"),
            ("sustaining", "Business-sustaining"),
            ("unallocated", "Unallocated"),
        ],
        required=True, index=True,
    )
    source_resource_id = fields.Many2one("cmc.abc.resource")
    cost_element_id = fields.Many2one("cmc.cost.element")
    source_activity_id = fields.Many2one("cmc.abc.activity", index=True)
    source_object_id = fields.Many2one("cmc.cost.object")
    target_activity_id = fields.Many2one("cmc.abc.activity", index=True)
    target_object_id = fields.Many2one("cmc.cost.object", index=True)
    driver_qty = fields.Float()
    rate = fields.Float(digits=(16, 6))
    amount = fields.Monetary()
    fixed_amount = fields.Monetary()
    variable_amount = fields.Monetary()
