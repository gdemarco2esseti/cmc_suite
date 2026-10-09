import logging
from collections import defaultdict
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.fields import Domain

from .cmc_utils import month_end, month_start

_logger = logging.getLogger(__name__)


class CmcMeasureQuantity(models.Model):
    _name = "cmc.measure.quantity"
    _inherit = ["cmc.month.mixin", "cmc.run.line.mixin"]
    _description = "Measure Quantity"
    _order = "date desc, id"
    _check_company_auto = True
    _cmc_run_field = "version_id"

    driver_id = fields.Many2one("cmc.cost.driver", required=True, index=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    version_id = fields.Many2one(
        "cmc.plan.version", string="Plan Version", ondelete="cascade", index=True, check_company=True,
        help="Empty = actual quantity.",
    )
    center_id = fields.Many2one("account.analytic.account", string="Cost Center", index=True, check_company=True)
    cost_object_id = fields.Many2one("cmc.cost.object", index=True, check_company=True)
    quantity = fields.Float(digits="Product Unit")
    feeder_key = fields.Char(index=True, help="Feeder that loaded the fact; empty = entered by hand.")

    _company_date_driver_idx = models.Index("(company_id, date, driver_id, version_id)")

    @api.constrains("date", "version_id")
    def _check_date_in_version(self):
        for fact in self.filtered("version_id"):
            version = fact.version_id
            if not month_start(version.date_from) <= fact.date <= version.date_to:
                raise ValidationError(
                    self.env._("A planned quantity must fall inside the span of its plan version.")
                )

    # ---- feeders --------------------------------------------------------

    @api.model
    def _cmc_feeder_profiles(self):
        """Declarative hook of the bridges: one dict per measure they load.

        ``key``, ``model``, ``domain``, ``date_field``, ``quantity`` ("__count" or
        a field summed), ``driver`` (xmlid or record), ``center_field`` /
        ``object_field`` (Many2one path or False), ``object_type`` and an
        optional ``sign`` (1/-1). Profiles sharing a key are summed."""
        return []

    @api.model
    def _cmc_cron_refresh_feeders(self):
        today = fields.Date.context_today(self)
        for company in self.env["res.company"].search([]):
            self._cmc_refresh_feeders(month_start(today - relativedelta(months=1)), today, company=company)

    @api.model
    def _cmc_refresh_feeders(self, date_from, date_to, company=None, keys=None):
        """Replace the actual facts of each feeder key for the company over the
        months of the span, aggregating the source with `_read_group` only."""
        # why sudo: engine; the source models are not the user's business
        self = self.sudo()
        company = company or self.env.company
        start, stop = month_start(date_from), month_end(date_to) + timedelta(days=1)
        profiles = [p for p in self._cmc_feeder_profiles() if keys is None or p["key"] in keys]
        totals = defaultdict(float)  # (key, driver, month, center, object) -> quantity
        objects = {}  # (object type, source model) -> source records
        for profile in profiles:
            driver = profile["driver"]
            if isinstance(driver, str):
                driver = self.env.ref(driver)
            groupby = [f"{profile['date_field']}:month"]
            groupby += [profile[name] for name in ("center_field", "object_field") if profile.get(name)]
            domain = Domain(profile["domain"]) & Domain.AND([
                [(profile["date_field"], ">=", start)],
                [(profile["date_field"], "<", stop)],
                [("company_id", "=", company.id)],
            ])
            quantity = "__count" if profile["quantity"] == "__count" else f"{profile['quantity']}:sum"
            for *group, qty in self.env[profile["model"]]._read_group(domain, groupby, [quantity]):
                group = iter(group)
                month = fields.Date.to_date(next(group))
                center = next(group) if profile.get("center_field") else False
                obj = next(group) if profile.get("object_field") else False
                totals[(profile["key"], driver, month, center, obj, profile.get("object_type"))] += (
                    (qty or 0.0) * profile.get("sign", 1)
                )
                if obj:
                    key = (profile.get("object_type"), obj._name)
                    objects[key] = objects.get(key, obj.browse()) | obj
        # one batch of cost objects per type, then one replace and one create
        object_ids = {}
        for (object_type, _model), records in objects.items():
            ensured = self.env["cmc.cost.object"]._cmc_ensure_objects(
                records, company=company, object_type=object_type or None
            )
            for record, cost_object in zip(records, ensured):
                object_ids[(object_type, record)] = cost_object.id
        self.search([
            ("feeder_key", "in", [p["key"] for p in profiles]),
            ("version_id", "=", False),
            ("company_id", "=", company.id),
            ("date", ">=", start),
            ("date", "<", stop),
        ]).unlink()
        self.create([
            {
                "driver_id": driver.id,
                "date": month,
                "company_id": company.id,
                "center_id": center.id if center else False,
                "cost_object_id": object_ids[(object_type, obj)] if obj else False,
                "quantity": qty,
                "feeder_key": key,
            }
            for (key, driver, month, center, obj, object_type), qty in totals.items()
        ])
