from odoo import api, fields, models
from odoo.exceptions import UserError

from .cmc_run_mixin import CMC_RUN_STATE
from .cmc_utils import month_start


class CmcRate(models.Model):
    _name = "cmc.rate"
    _inherit = ["cmc.month.mixin"]
    _description = "Rate"
    _order = "date desc, id"

    holder_model = fields.Char(required=True)
    holder_id = fields.Many2oneReference(model_field="holder_model", required=True)
    driver_id = fields.Many2one("cmc.cost.driver", string="Unit", index=True)
    cost_element_id = fields.Many2one("cmc.cost.element", index=True)
    component = fields.Selection(
        selection=[("fixed", "Fixed"), ("variable", "Variable"), ("total", "Total")],
        required=True,
        default="total",
    )
    value = fields.Float(digits=(16, 6))
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    origin_model = fields.Char(required=True)
    origin_id = fields.Many2oneReference(model_field="origin_model", required=True)

    # why COALESCE: NULLs are distinct in a unique index (same pattern as mail_alias)
    _rate_uniq = models.UniqueIndex(
        "(holder_model, holder_id, COALESCE(driver_id, 0), component, COALESCE(cost_element_id, 0),"
        " origin_model, origin_id, date)",
        "One rate per holder, unit, component, element, origin and month.",
    )

    # ---- lock -----------------------------------------------------------

    def _cmc_check_origins(self):
        """Run rates are result rows (engine only, sentinel); version rates are
        inputs, open while the version is a draft."""
        versions = self.filtered(lambda rate: rate.origin_model == "cmc.plan.version")
        if versions != self and self.env.context.get("cmc_run_state") is not CMC_RUN_STATE:
            raise UserError(self.env._("The rates of a run are written by Compute only."))
        # why sudo: the lock must hold whatever the user's rules show
        if versions and self.env["cmc.plan.version"].sudo().search_count(
            [("id", "in", versions.mapped("origin_id")), ("state", "!=", "draft")], limit=1
        ):
            raise UserError(self.env._("The rates of an approved plan version cannot change."))

    @api.model_create_multi
    def create(self, vals_list):
        rates = super().create(vals_list)
        rates._cmc_check_origins()
        return rates

    def write(self, vals):
        self._cmc_check_origins()
        res = super().write(vals)
        if "origin_model" in vals or "origin_id" in vals:
            self._cmc_check_origins()
        return res

    def unlink(self):
        self._cmc_check_origins()
        return super().unlink()

    # ---- API ------------------------------------------------------------

    @api.model
    def _cmc_publish(self, origin, rows):
        return self.create([
            {
                "holder_model": row["holder"]._name,
                "holder_id": row["holder"].id,
                "driver_id": row["driver"].id,
                "cost_element_id": row["cost_element"].id if row.get("cost_element") else False,
                "component": row.get("component", "total"),
                "date": row["date"],
                "value": row["value"],
                "origin_model": origin._name,
                "origin_id": origin.id,
                "company_id": origin.company_id.id,
            }
            for row in rows
        ])

    @api.model
    def _cmc_lookup(self, holder, driver, on_date, origin=None, component="total", company=None):
        """The rate of the month of `on_date`: the one of `origin`, else the most
        recently published by a run (version rates are plans, never actuals)."""
        domain = [
            ("holder_model", "=", holder._name),
            ("holder_id", "=", holder.id),
            ("driver_id", "=", driver.id),
            ("component", "=", component),
            ("date", "=", month_start(on_date)),
            ("company_id", "=", (company or self.env.company).id),
        ]
        if origin:
            domain += [("origin_model", "=", origin._name), ("origin_id", "=", origin.id)]
        else:
            domain.append(("origin_model", "!=", "cmc.plan.version"))
        return self.search(domain, order="id desc", limit=1)

    @api.model
    def _cmc_lookup_batch(self, keys, origin=None, component="total", company=None):
        """`_cmc_lookup` for many `(holder_model, holder_id, driver_id, date)` keys in ONE search: {key: rate}.
        A tuple `component` reads several at once and keys the result `(*key, component)`."""
        keys = set(keys)
        if not keys:
            return {}
        components = (component,) if isinstance(component, str) else tuple(component)
        slot = {key: (*key[:3], month_start(key[3])) for key in keys}
        domain = [
            ("holder_model", "in", list({k[0] for k in slot.values()})),
            ("holder_id", "in", list({k[1] for k in slot.values()})),
            ("driver_id", "in", list({k[2] for k in slot.values()})),
            ("component", "in", components),
            ("date", "in", list({k[3] for k in slot.values()})),
            ("company_id", "=", (company or self.env.company).id),
        ]
        if origin:
            domain += [("origin_model", "=", origin._name), ("origin_id", "=", origin.id)]
        else:
            domain.append(("origin_model", "!=", "cmc.plan.version"))
        latest = {}
        for rate in self.search(domain, order="id desc"):
            latest.setdefault((rate.holder_model, rate.holder_id, rate.driver_id.id, rate.date, rate.component), rate)
        found = {}
        for key, norm in slot.items():
            for comp in components:
                rate = latest.get((*norm, comp))
                if rate:
                    found[key if isinstance(component, str) else (*key, comp)] = rate
        return found
