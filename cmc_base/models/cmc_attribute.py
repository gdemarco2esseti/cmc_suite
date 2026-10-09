from odoo import api, fields, models
from odoo.exceptions import ValidationError


class CmcAttribute(models.Model):
    _name = "cmc.attribute"
    _description = "Attribute"
    _order = "sequence, name, id"

    # coalesce: shipped attributes have no company and must stay unique too
    _code_company_uniq = models.UniqueIndex("(code, COALESCE(company_id, 0))")

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, index=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    target_model_ids = fields.Many2many("ir.model", string="Applies To", domain="[('transient', '=', False)]")
    value_ids = fields.One2many("cmc.attribute.value", "attribute_id", string="Values")

    @api.constrains("target_model_ids")
    def _check_target_model_ids(self):
        mixin = self.env.registry["cmc.attribute.mixin"]
        for attribute in self:
            for model in attribute.target_model_ids:
                if model.model not in self.env or not isinstance(self.env[model.model], mixin):
                    raise ValidationError(self.env._("Model %s cannot carry attributes.", model.name))


class CmcAttributeValue(models.Model):
    _name = "cmc.attribute.value"
    _description = "Attribute Value"
    _order = "attribute_id, sequence, id"

    attribute_id = fields.Many2one("cmc.attribute", required=True, ondelete="cascade", index=True)
    name = fields.Char(required=True, translate=True)
    code = fields.Char()
    sequence = fields.Integer(default=10)
    weight = fields.Float()
    company_id = fields.Many2one(related="attribute_id.company_id", store=True)
