from dateutil.relativedelta import relativedelta

from odoo import api, models
from odoo.tools import format_date

BVA_ACTION = "cmc_budget.action_cmc_budget_bva"


class CmcCockpit(models.TransientModel):
    _inherit = "cmc.cockpit"

    @api.model
    def _cmc_dashboard_kpis(self, period):
        return super()._cmc_dashboard_kpis(period) + self._cmc_dashboard_budget_kpi(period)

    @api.model
    def _cmc_dashboard_cards(self, period):
        return super()._cmc_dashboard_cards(period) + self._cmc_dashboard_budget_card(period)

    @api.model
    def _cmc_dashboard_alerts(self, period):
        return super()._cmc_dashboard_alerts(period) + self._cmc_dashboard_budget_alert(period)

    @api.model
    def _cmc_dashboard_charts(self, period):
        return super()._cmc_dashboard_charts(period) + self._cmc_dashboard_budget_chart(period)

    @api.model
    def _cmc_dashboard_budget_domain(self, period, cost_only=False):
        Bva = self.env["cmc.budget.bva"]
        # the view keeps one latest approved version per company; the date filter keeps the scan bounded
        return self._cmc_dashboard_company_domain(Bva) + [("is_latest_version", "=", True)] + self._cmc_dashboard_period_domain(period, closed_months=True) + (
            [("kind", "=", "cost")] if cost_only else [])

    @api.model
    def _cmc_dashboard_budget(self, period):
        """Cost plan/actual by month (by year for "all") of the latest approved version; None when unreadable."""
        def compute():
            Bva, Version = self.env["cmc.budget.bva"], self.env["cmc.plan.version"]
            if not self._cmc_dashboard_readable(Bva, Version):
                return None
            groupby = "date:year" if period["kind"] == "all" else "date:month"
            # month-dated rows: in the running period only closed months are compared (the current month holds the
            # whole plan but only part of the actuals)
            cut = self._cmc_dashboard_closed_months_cut(period)
            no_closed = bool(cut) and cut <= period["date_from"]
            rows = [] if no_closed else Bva._read_group(
                # why: revenue rows would net against cost in a "favourable" variance, so only cost is compared
                self._cmc_dashboard_budget_domain(period, cost_only=True), [groupby], ["plan_amount:sum", "actual_amount:sum"], order=groupby)
            # an approved budget without rows in the period still shows a card/KPI ("—"); a company without one shows neither
            has_version = bool(rows) or bool(Version.search_count(
                self._cmc_dashboard_company_domain(Version) + [("version_type", "=", "budget"), ("state", "=", "done")], limit=1))
            through = cut and not no_closed and format_date(self.env, cut - relativedelta(months=1), date_format="MMM yyyy")
            return {"rows": rows, "has_version": has_version, "no_closed": no_closed, "through": through,
                    "plan": sum(r[1] for r in rows), "actual": sum(r[2] for r in rows)}
        return self._cmc_dashboard_memo(period, "budget.bva", compute)

    @api.model
    def _cmc_dashboard_budget_kpi(self, period):
        budget = self._cmc_dashboard_budget(period)
        if not budget or not budget["has_version"]:
            return []
        plan, actual = budget["plan"], budget["actual"]
        raw = round((actual - plan) / plan * 100, 2) if plan else None
        trend = None
        if raw is not None:
            direction = "up" if raw > 0 else "down" if raw < 0 else "flat"
            verdict = self.env._("Unfavourable") if direction == "up" else self.env._("Favourable")
            trend = {
                "text": "%s · %s" % (verdict, self.env._(
                    "Actual %(actual)s vs budget %(plan)s", actual=self._cmc_dashboard_money(actual, short=True),
                    plan=self._cmc_dashboard_money(plan, short=True))),
                "direction": direction,
                # a cost above plan is unfavourable
                "favourable": direction != "up",
            }
        return [self._cmc_dashboard_kpi(
            "budget", 10, "budget.variance", self.env._("Budget variance"), raw, "percent",
            self.env._("actual vs budget, through %s", budget["through"]) if budget["through"] else self.env._("actual vs budget"),
            sub=None if raw is not None else self.env._("Current month not closed yet") if budget["no_closed"]
            else self.env._("No data in this period"), trend=trend)]

    @api.model
    def _cmc_dashboard_budget_card(self, period):
        budget = self._cmc_dashboard_budget(period)
        if budget is None:
            return []
        name = "%s · %s" % (period["label"], self.env._("through %s", budget["through"])) if budget["through"] else period["label"]
        run = budget["has_version"] and {"name": name}
        no_rows = not budget["rows"]
        figures = run and [
            self._cmc_dashboard_figure("budget.actual", self.env._("Actual"), None if no_rows else budget["actual"], "monetary"),
            self._cmc_dashboard_figure("budget.plan", self.env._("Budget"), None if no_rows else budget["plan"], "monetary"),
        ]
        return [self._cmc_dashboard_card(
            "budget.bva", "planning", 10, self.env._("Budget vs actual"), run, period, figures, BVA_ACTION)]

    @api.model
    def _cmc_dashboard_budget_alert(self, period):
        budget = self._cmc_dashboard_budget(period)
        if not budget or not budget["has_version"] or budget["no_closed"]:
            return []
        # both kinds: a deviation is worth flagging either way
        domain = self._cmc_dashboard_budget_domain(period) + [("is_alert", "=", True)]
        count = self.env["cmc.budget.bva"].search_count(domain)
        if not count:
            return []
        return [self._cmc_dashboard_alert(
            "budget.over_threshold", "warning", self.env._("%s budget lines over threshold", count) if count > 1 else self.env._("1 budget line over threshold"), count, BVA_ACTION, domain,
            card="budget.bva")]

    @api.model
    def _cmc_dashboard_budget_chart(self, period):
        budget = self._cmc_dashboard_budget(period)
        if not budget or not budget["rows"] or period["kind"] == "month":
            return []
        labels = [
            str(date.year) if period["kind"] == "all" else format_date(self.env, date, date_format="MMM yyyy")
            for date, _plan, _actual in budget["rows"]
        ]
        series = (
            (self.env._("Budget"), [plan for _date, plan, _actual in budget["rows"]]),
            (self.env._("Actual"), [actual for _date, _plan, actual in budget["rows"]]),
        )
        return [{
            "key": "budget.bva_by_month", "type": "bar", "horizontal": False, "title": self.env._("Budget vs actual by month"),
            "labels": labels,
            "datasets": [{"label": label, "data": data, "formatted": [self._cmc_dashboard_money(v) for v in data]} for label, data in series],
        }]
