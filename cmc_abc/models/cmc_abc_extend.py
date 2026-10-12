from odoo import api, fields, models
from odoo.exceptions import ValidationError

LEVELS = [
    ("unit", "Unit"),
    ("batch", "Batch"),
    ("product", "Product"),
    ("customer", "Customer"),
    ("facility", "Facility"),
]


class CmcCostDriver(models.Model):
    _inherit = "cmc.cost.driver"

    cmc_abc_level = fields.Selection(LEVELS, string="ABC Level", help="Hierarchy level this driver measures.")
    cmc_component_ids = fields.One2many(
        "cmc.abc.driver.component", "driver_id", string="Index Components", copy=True,
        help="Weighted index driver: quantity = sum of weight x quantity of each component.",
    )

    @api.constrains("cmc_abc_level", "measure_kind")
    def _check_cmc_abc_activities(self):
        # why: an activity validated its driver when it was saved; the driver can change under it
        self.env["cmc.abc.activity"].search([("driver_id", "in", self.ids)])._check_driver()


class CmcAbcDriverComponent(models.Model):
    _name = "cmc.abc.driver.component"
    _description = "ABC Index Driver Component"
    _check_company_auto = True

    driver_id = fields.Many2one("cmc.cost.driver", required=True, ondelete="cascade", index=True)
    component_driver_id = fields.Many2one(
        "cmc.cost.driver", string="Component", required=True, check_company=True, ondelete="restrict"
    )
    weight = fields.Float(default=1.0)
    company_id = fields.Many2one(related="driver_id.company_id", store=True, index=True)

    _component_uniq = models.Constraint(
        "UNIQUE(driver_id, component_driver_id)", "A component is listed once per index driver."
    )

    @api.constrains("driver_id", "component_driver_id")
    def _check_component(self):
        if any(row.driver_id == row.component_driver_id for row in self):
            raise ValidationError(self.env._("A driver cannot be a component of itself."))


class CmcCostObject(models.Model):
    _inherit = "cmc.cost.object"

    object_type = fields.Selection(
        selection_add=[("channel", "Channel"), ("order", "Order"), ("sustaining", "Business-sustaining")],
        ondelete={"channel": "cascade", "order": "cascade", "sustaining": "cascade"},
    )
