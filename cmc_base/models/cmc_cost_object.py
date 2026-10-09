from collections import defaultdict

from odoo import api, fields, models
from odoo.exceptions import UserError

DEFAULT_TYPES = {
    "product.product": "product",
    "res.partner": "customer",
    "project.project": "project",
}


class CmcCostObject(models.Model):
    _name = "cmc.cost.object"
    _description = "Cost Object"
    _order = "object_type, id"

    object_type = fields.Selection(
        selection=[
            ("product", "Product"),
            ("customer", "Customer"),
            ("supplier", "Supplier"),
            ("project", "Project"),
            ("unassigned", "Unassigned"),
        ],
        required=True,
        index=True,
    )
    res_model = fields.Char(required=True)
    res_id = fields.Many2oneReference(model_field="res_model", required=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)

    _object_uniq = models.UniqueIndex(
        "(object_type, res_model, res_id, company_id)",
        "A cost object is registered once per type, record and company.",
    )

    @api.depends("res_model", "res_id")
    def _compute_display_name(self):
        by_model = defaultdict(list)
        for obj in self:
            by_model[obj.res_model].append(obj.res_id)
        names = {}
        for model, ids in by_model.items():
            # why sudo: the name of a product/partner shown on a cost report
            # must not need access to the product or contact apps
            names[model] = dict(self.env[model].sudo().browse(ids).exists().mapped(lambda r: (r.id, r.display_name)))
        for obj in self:
            obj.display_name = names[obj.res_model].get(obj.res_id) or "%s,%s" % (obj.res_model, obj.res_id)

    @api.model
    def _cmc_ensure_objects(self, records, company=None, object_type=None):
        """Cost objects of `records`, aligned with them; the missing ones are
        created lazily in one batch (AD-106)."""
        if not records:
            return self.browse()
        object_type = object_type or DEFAULT_TYPES.get(records._name)
        if not object_type:
            raise UserError(self.env._("Give the object type for %(model)s records.", model=records._name))
        company = company or self.env.company
        # why sudo: the registry is written by the system for any user booking a cost
        registry = self.sudo()
        domain = [
            ("object_type", "=", object_type),
            ("res_model", "=", records._name),
            ("res_id", "in", records.ids),
            ("company_id", "=", company.id),
        ]
        by_res_id = {obj.res_id: obj.id for obj in registry.search(domain)}
        missing = [rid for rid in dict.fromkeys(records.ids) if rid not in by_res_id]
        if missing:
            created = registry.create([
                {"object_type": object_type, "res_model": records._name, "res_id": rid, "company_id": company.id}
                for rid in missing
            ])
            by_res_id.update({obj.res_id: obj.id for obj in created})
        return self.browse([by_res_id[rid] for rid in records.ids])
