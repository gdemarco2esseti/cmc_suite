from odoo import api, fields, models
from odoo.tools import format_date


class CmcReclassMixin(models.AbstractModel):
    """Contract for the transposed 'riclassificato' report shared by every CMC
    methodology. Concrete run models inherit this and override _cmc_reclass()."""

    _name = "cmc.reclass.mixin"
    _description = "CMC reclassified (transposed) report contract"

    cmc_reclass_json = fields.Json(
        string="Reclassified", compute="_compute_cmc_reclass_json")

    def _compute_cmc_reclass_json(self):
        # Intentionally no @api.depends: the field is non-stored and recomputed on
        # every read. ABC's reclass depends on EXTERNAL analytic-line pools that
        # a depends could not track, so a stored/triggered field would go stale.
        # The web client reloads the record after action_compute() -> fresh read
        # -> fresh table. Do NOT convert this to a stored field.
        for rec in self:
            try:
                rec.cmc_reclass_json = rec._cmc_reclass()
            except NotImplementedError:
                rec.cmc_reclass_json = False

    def _cmc_reclass(self):
        """Return {title, subtitle, currency, columns:[{key,label}],
        rows:[{label,kind,values:{key:float}|None,total:float}]}."""
        raise NotImplementedError(
            "Every CMC run must implement _cmc_reclass()")

    # ---- shared helpers ----
    def _reclass_ccy(self):
        self.ensure_one()
        if "currency_id" in self._fields and self.currency_id:
            return self.currency_id.symbol or ""
        return ""

    def _reclass_period(self):
        self.ensure_one()
        df = self.date_from if "date_from" in self._fields else False
        dt = self.date_to if "date_to" in self._fields else False
        if df and dt:
            return "%s → %s" % (format_date(self.env, df), format_date(self.env, dt))
        return ""

    @api.model
    def _reclass_fmt(self, value, kind, ccy):
        """Server-side formatting shared by the PDF template (the OWL widget has
        its own JS mirror). Italian thousands, integer money, comma percent."""
        if value is None:
            return ""
        if kind == "percent":
            return ("%.1f%%" % value).replace(".", ",")
        s = "{:,.0f}".format(abs(value)).replace(",", ".")
        sign = "–" if value < 0 else ""
        return "%s%s %s" % (sign, ccy, s) if ccy else "%s%s" % (sign, s)
