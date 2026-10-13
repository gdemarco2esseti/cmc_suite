from odoo import api, models

MODEL = "cmc.tdabc.run"
RESULT = "cmc.tdabc.capacity.result"
ACTION = "cmc_tdabc.action_cmc_tdabc_run"
CARD = "tdabc.run"


class CmcCockpit(models.TransientModel):
    _inherit = "cmc.cockpit"

    @api.model
    def _cmc_dashboard_tdabc(self, period):
        """Latest done run + capacity totals of its chosen scenario (timesheet when present, else manual)."""
        def compute():
            Result = self.env[RESULT]
            run = self._cmc_dashboard_latest_run(self.env[MODEL], ["name", "date_from", "date_to"], "id desc")
            if not (run and Result.has_access("read")):
                return {"run": run, "capacity": None}
            rows = Result._read_group(
                [("run_id", "=", run["id"])], ["scenario", "over_capacity"],
                ["__count", "unused_cost:sum", "capacity_use_pct:avg"])
            by_scenario = {}
            for scenario, over, count, unused, use_pct in rows:
                total = by_scenario.setdefault(scenario, {"unused": 0.0, "weighted": 0.0, "count": 0, "over": 0})
                # why: over-capacity rows have a negative unused cost; they are the separate alert
                total["unused"] += 0.0 if over else (unused or 0.0)
                total["weighted"] += (use_pct or 0.0) * count
                total["count"] += count
                total["over"] += count if over else 0
            chosen = by_scenario.get("timesheet") or by_scenario.get("manual")
            capacity = chosen and {
                "unused": chosen["unused"], "use_pct": chosen["weighted"] / chosen["count"], "over": chosen["over"]}
            return {"run": run, "capacity": capacity or {"unused": None, "use_pct": None, "over": 0}}
        return self._cmc_dashboard_memo(period, CARD, compute)

    @api.model
    def _cmc_dashboard_tdabc_readable(self):
        return self._cmc_dashboard_readable(MODEL)

    @api.model
    def _cmc_dashboard_kpis(self, period):
        kpis = super()._cmc_dashboard_kpis(period)
        data = self._cmc_dashboard_tdabc_readable() and self._cmc_dashboard_tdabc(period)
        if data and data["run"] and data["capacity"]:  # results unreadable or no run: tile omitted
            raw = data["capacity"]["unused"]
            kpis.append(self._cmc_dashboard_kpi(
                "capacity", 10, "tdabc.capacity", self.env._("Unused capacity"), raw, "monetary", self.env._("from TDABC"),
                sub=None if raw is not None else self.env._("No data in this period")))
        return kpis

    @api.model
    def _cmc_dashboard_cards(self, period):
        cards = super()._cmc_dashboard_cards(period)
        if self._cmc_dashboard_tdabc_readable():
            data = self._cmc_dashboard_tdabc(period)
            capacity = data["capacity"]
            figures = capacity and [
                self._cmc_dashboard_figure("tdabc.use_pct", self.env._("Capacity use"), capacity["use_pct"], "percent")]
            cards.append(self._cmc_dashboard_card(
                CARD, "costing", 40, self.env._("Time-driven ABC"), data["run"], period, figures or [], ACTION))
        return cards

    @api.model
    def _cmc_dashboard_alerts(self, period):
        alerts = super()._cmc_dashboard_alerts(period)
        data = self._cmc_dashboard_tdabc_readable() and self._cmc_dashboard_tdabc(period)
        over = data and data["capacity"] and data["capacity"]["over"]
        if over:
            alerts.append(self._cmc_dashboard_alert(
                "tdabc.over_capacity", "warning", self.env._("%s resources over capacity", over) if over > 1 else self.env._("1 resource over capacity"), over, ACTION,
                [("id", "=", data["run"]["id"])], card=CARD, scope="run"))
        return alerts
