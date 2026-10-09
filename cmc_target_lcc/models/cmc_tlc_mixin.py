from collections import defaultdict

from odoo import models
from odoo.exceptions import UserError
from odoo.tools import SQL


class CmcTlcRunMixin(models.AbstractModel):
    """Run of the target / life-cycle engines: header outputs written by Compute only, rates read in one batch."""

    _name = "cmc.tlc.run.mixin"
    _inherit = ["cmc.run.mixin"]
    _description = "CMC target / life-cycle run"

    # header outputs of the engine: written by Compute, cleared by Reset
    _tlc_outputs = ()

    def write(self, vals):
        if not self._cmc_state_unlocked() and not set(self._tlc_outputs).isdisjoint(vals):
            raise UserError(self.env._("The outcome of a run is written by Compute only."))
        return super().write(vals)

    def _cmc_reset_values(self):
        return {**super()._cmc_reset_values(), **dict.fromkeys(self._tlc_outputs, False)}

    def _tlc_rates(self, keys):
        """{(holder_model, holder_id, driver_id, month): rate} of the run company: run-published rates only, the latest
        of the month wins (plan-version rates are plans, never actuals). One search; the producers are pinned (AD-002)."""
        self.ensure_one()
        keys = set(keys)
        if not keys:
            return {}
        rates = self.env["cmc.rate"].search([
            ("holder_model", "in", list({k[0] for k in keys})),
            ("holder_id", "in", list({k[1] for k in keys})),
            ("driver_id", "in", list({k[2] for k in keys})),
            ("component", "=", "total"),
            ("date", "in", list({k[3] for k in keys})),
            ("company_id", "=", self.company_id.id),
            ("origin_model", "!=", "cmc.plan.version"),
        ], order="id desc")
        found = {}
        for rate in rates:
            key = (rate.holder_model, rate.holder_id, rate.driver_id.id, rate.date)
            if key in keys:
                found.setdefault(key, rate)
        origins = defaultdict(set)
        for rate in found.values():
            origins[rate.origin_model].add(rate.origin_id)
        for model, ids in origins.items():
            # why sudo: the producer is read whatever the user's rules show; the usage pins it computed
            self._cmc_record_usage(self.env[model].sudo().browse(ids))
        return found

    def _tlc_write_batch(self, records, rows):
        """Snapshot {record id: {column: value}} on `records` with ONE statement (a per-line write is N updates, AD-114)."""
        if not rows:
            return
        columns = list(next(iter(rows.values())))
        # why: raw UPDATE, not write(): the lines of a draft run are edited by the engine only here, and the ORM
        # would issue one UPDATE per distinct value
        self.env.flush_all()
        values = SQL(", ").join(
            SQL("(%s)", SQL(", ").join(SQL("%s", v) for v in (rid, *[vals[c] for c in columns]))) for rid, vals in rows.items()
        )
        self.env.cr.execute(SQL(
            "UPDATE %s AS t SET %s FROM (VALUES %s) AS v(id, %s) WHERE t.id = v.id",
            SQL.identifier(records._table),
            SQL(", ").join(SQL("%s = v.%s", SQL.identifier(c), SQL.identifier(c)) for c in columns),
            values,
            SQL(", ").join(SQL.identifier(c) for c in columns),
        ))
        records.invalidate_recordset(columns)
