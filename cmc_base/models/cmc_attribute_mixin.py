from collections import defaultdict

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import SQL


class CmcAttributeMixin(models.AbstractModel):
    _name = "cmc.attribute.mixin"
    _description = "Attribute Assignment Mixin"

    # no explicit relation: each target model gets its own table (AD-109, joinable in read_group)
    cmc_attribute_value_ids = fields.Many2many("cmc.attribute.value", string="Attributes")

    @api.constrains("cmc_attribute_value_ids")
    def _check_cmc_attribute_value_ids(self):
        for record in self:
            values = record.cmc_attribute_value_ids
            if len(values.attribute_id) != len(values):
                raise ValidationError(self.env._("A record can carry only one value per attribute."))
            # a company-less record (or value) is shared: only two different companies clash
            if record.company_id and values.company_id - record.company_id:
                raise UserError(self.env._("Attribute values of another company cannot be assigned to this record."))
            for attribute in values.attribute_id:
                if self._name not in attribute.target_model_ids.mapped("model"):
                    raise ValidationError(self.env._("Attribute %s does not apply to this record type.", attribute.name))

    def _cmc_resolve_attribute(self, attribute):
        """The attribute (by record or code); a code means the one shared or of the current companies."""
        if not isinstance(attribute, str):
            return attribute
        # why sudo: superuser has no record rules, so the company is filtered explicitly
        return self.env["cmc.attribute"].sudo().search(
            [("code", "=", attribute), ("company_id", "in", [False] + self.env.companies.ids)], limit=1
        )

    def _cmc_attribute_value(self, attribute):
        self.ensure_one()
        attribute = self._cmc_resolve_attribute(attribute)
        return self.cmc_attribute_value_ids.filtered(lambda v: v.attribute_id == attribute)[:1]

    def _cmc_group_by_attribute(self, attribute):
        """Return {value_id: [record ids], False: [record ids without value]} in one query."""
        domain = [("attribute_id", "=", self._cmc_resolve_attribute(attribute).id)]
        # _search applies the caller's record rules on the values
        values = self.env["cmc.attribute.value"]._search(domain)
        field = self._fields["cmc_attribute_value_ids"]
        self.env.cr.execute(SQL(
            "SELECT %(rec)s, %(val)s FROM %(rel)s WHERE %(rec)s IN %(ids)s AND %(val)s IN %(values)s",
            rec=SQL.identifier(field.column1), val=SQL.identifier(field.column2), rel=SQL.identifier(field.relation),
            ids=tuple(self.ids) or (0,), values=values.subselect(),
        ))
        groups = defaultdict(list)
        for rec_id, value_id in self.env.cr.fetchall():
            groups[value_id].append(rec_id)
        assigned = {rec_id for ids in groups.values() for rec_id in ids}
        if missing := [rid for rid in self.ids if rid not in assigned]:
            groups[False] = missing
        return dict(groups)
