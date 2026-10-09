from odoo import api, fields, models

from .cmc_standard_mixin import PRECISE


class CmcStandardCard(models.Model):
    _name = "cmc.standard.card"
    _inherit = ["cmc.standard.line.mixin"]
    _description = "Standard Cost Card"
    _order = "version_id, product_id"
    _rec_name = "product_id"

    product_id = fields.Many2one("product.product", required=True, check_company=True, ondelete="restrict")
    bom_id = fields.Many2one("mrp.bom", ondelete="set null")
    standard_type = fields.Selection(related="version_id.standard_type")
    line_ids = fields.One2many("cmc.standard.card.line", "card_id", copy=True)
    unit_material = fields.Float(digits=PRECISE, compute="_compute_units", store=True, recursive=True)
    unit_labour = fields.Float(digits=PRECISE, compute="_compute_units", store=True, recursive=True)
    unit_overhead = fields.Float(digits=PRECISE, compute="_compute_units", store=True, recursive=True)
    unit_overhead_fixed = fields.Float(digits=PRECISE, compute="_compute_units", store=True, recursive=True)
    unit_cost = fields.Float(digits=PRECISE, compute="_compute_units", store=True)
    unit_fixed = fields.Float(digits=PRECISE, compute="_compute_units", store=True)
    unit_variable = fields.Float(digits=PRECISE, compute="_compute_units", store=True)
    valuation_price = fields.Float(digits=PRECISE, compute="_compute_valuation", help="Current standard_price of the product in the company.")
    valuation_difference = fields.Float(digits=PRECISE, compute="_compute_valuation", help="Valuation price minus card unit cost.")
    qty_available = fields.Float(compute="_compute_valuation")
    value_difference = fields.Float(compute="_compute_valuation", help="Difference x quantity on hand.")

    _version_product_uniq = models.UniqueIndex("(version_id, product_id)", "One standard cost card per product in a version.")

    @api.depends(
        "line_ids.line_type", "line_ids.behavior", "line_ids.amount", "line_ids.quantity",
        "line_ids.child_card_id.unit_material", "line_ids.child_card_id.unit_labour",
        "line_ids.child_card_id.unit_overhead", "line_ids.child_card_id.unit_overhead_fixed",
    )
    def _compute_units(self):
        # why sorted: a card reads the units of its sub-assemblies, which are protected (not yet set) if computed after it
        for card in self.sorted(lambda card: card._cmc_std_depth()):
            material = labour = overhead = fixed = 0.0
            for line in card.line_ids:
                child = line.child_card_id
                if child:
                    # a sub-assembly keeps its element split in the parent card
                    material += line.quantity * child.unit_material
                    labour += line.quantity * child.unit_labour
                    overhead += line.quantity * child.unit_overhead
                    fixed += line.quantity * child.unit_overhead_fixed
                elif line.line_type == "material":
                    material += line.amount
                elif line.line_type == "labour":
                    labour += line.amount
                else:
                    overhead += line.amount
                    fixed += line.amount if line.behavior == "fixed" else 0.0
            card.unit_material, card.unit_labour, card.unit_overhead = material, labour, overhead
            card.unit_overhead_fixed = card.unit_fixed = fixed
            card.unit_cost = material + labour + overhead
            card.unit_variable = card.unit_cost - fixed

    def _cmc_std_depth(self):
        children = self.line_ids.child_card_id
        return 1 + max((child._cmc_std_depth() for child in children), default=0)

    @api.depends("product_id.standard_price", "unit_cost", "company_id")
    def _compute_valuation(self):
        for company, cards in self.grouped("company_id").items():
            # why sudo: the controller reads stock quantities and valuation for the reconciliation, not the stock app
            products = cards.product_id.sudo().with_company(company)
            by_id = {product.id: product for product in products}
            for card in cards:
                product = by_id[card.product_id.id]
                card.valuation_price = product.standard_price
                card.valuation_difference = card.valuation_price - card.unit_cost
                card.qty_available = product.qty_available
                card.value_difference = card.valuation_difference * card.qty_available


class CmcStandardCardLine(models.Model):
    _name = "cmc.standard.card.line"
    _inherit = ["cmc.run.line.mixin"]
    _description = "Standard Cost Card Line"
    _order = "card_id, line_type, id"
    _cmc_run_field = "version_id"

    card_id = fields.Many2one("cmc.standard.card", required=True, ondelete="cascade", index=True)
    version_id = fields.Many2one(related="card_id.version_id", store=True, index=True)
    company_id = fields.Many2one(related="card_id.company_id", store=True, index=True)
    line_type = fields.Selection([("material", "Material"), ("labour", "Labour"), ("overhead", "Overhead")], required=True)
    product_id = fields.Many2one("product.product", string="Component", ondelete="restrict")
    center_id = fields.Many2one("account.analytic.account", string="Cost Center", ondelete="restrict")
    driver_id = fields.Many2one("cmc.cost.driver", ondelete="restrict")
    behavior = fields.Selection([("fixed", "Fixed"), ("variable", "Variable")])
    quantity = fields.Float(digits=PRECISE, help="Per unit of the finished product.")
    price = fields.Float(digits=PRECISE, help="A sub-assembly is priced at its card cost at roll-up time.")
    amount = fields.Float(digits=PRECISE, compute="_compute_amount", store=True)
    price_source = fields.Selection([("planned", "Planned price"), ("product", "Product cost"), ("card", "Sub-assembly card")])
    child_card_id = fields.Many2one("cmc.standard.card", string="Sub-assembly Card", copy=False, ondelete="set null")

    @api.depends("quantity", "price")
    def _compute_amount(self):
        for line in self:
            line.amount = line.quantity * line.price
