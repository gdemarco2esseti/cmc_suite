from collections import defaultdict

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools import date_utils, float_is_zero, format_date, formatLang
from odoo.tools.translate import LazyTranslate

# module-level labels: lazily translated so they are extractable AND
# resolved in the reader's language at render time
_lt = LazyTranslate(__name__)

# Foundation pipeline: mandatory, sequential, always present. Numbered at
# render time (1..N) so there are never gaps.
# (key, label, model, domain, action xml-id)
BASE_STEPS = [
    ("centers", _lt("Cost Centers"), "account.analytic.account",
     ["|", ("cmc_center_type", "!=", False), ("cmc_is_period", "=", True)],
     "cmc_base.action_cmc_cost_center"),
    ("costs", _lt("Cost Entry"), "account.analytic.line",
     lambda AL: AL._cmc_cost_base_domain(), "cmc_base.action_cmc_analytic_lines"),
    ("services", _lt("Inter-Center Services"), "cmc.center.service",
     [], "cmc_base.action_cmc_center_service"),
    ("closing", _lt("Center Closing"), "cmc.closing.run",
     [("state", "=", "done")], "cmc_base.action_cmc_closing_run"),
]

# Costing methodologies: PARALLEL alternatives applied on the same cost base.
# Only the installed ones appear (runtime detection) — a deployment typically
# picks the methodology it needs, so no fixed numbering / no gaps.
METHOD_STEPS = [
    ("costing", _lt("Full Costing"), "cmc.costing.run",
     [("state", "=", "done")], "cmc_full_costing.action_cmc_costing_run"),
    ("cvr", _lt("Direct Costing / CVP"), "cmc.cvr.analysis",
     [], "cmc_direct_costing.action_cmc_cvr_analysis"),
    ("abc", _lt("Activity-Based Costing"), "cmc.abc.run",
     [("state", "=", "done")], "cmc_abc.action_cmc_abc_run"),
    ("joint", _lt("Joint Costs"), "cmc.joint.run",
     [("state", "=", "done")], "cmc_joint_costs.action_cmc_joint_run"),
    ("tdabc", _lt("Time-Driven ABC"), "cmc.tdabc.run",
     [("state", "=", "done")], "cmc_tdabc.action_cmc_tdabc_run"),
]

DONE_RUN_KEYS = {"closing", "costing", "abc", "joint", "tdabc"}

# Controller dashboard: areas mirror the menu tabs (key, sequence, label)
DASHBOARD_AREAS = [
    ("recording", 10, _lt("Cost Recording")),
    ("costing", 20, _lt("Product Costing")),
    ("planning", 30, _lt("Budget & Scenarios")),
    ("decision", 40, _lt("Analysis & Decisions")),
    ("performance", 50, _lt("Performance")),
    ("financial", 60, _lt("Financial Analysis")),
]
KPI_SLOTS = ("cost", "budget", "result", "capacity", "scorecard")
PERIOD_KINDS = ("month", "quarter", "year", "all")
SEVERITY_RANK = {"danger": 0, "warning": 1, "info": 2}
KPI_TOP_ELEMENTS = 8
DASH = "—"


class CmcCockpit(models.TransientModel):
    _name = "cmc.cockpit"
    _description = "Cost Management Cockpit"

    def _cmc_collect(self, defs):
        """Resolve step definitions against installed models (runtime detection)."""
        out = []
        for key, label, model, domain, action in defs:
            if model not in self.env:
                continue  # capillary module not installed
            Model = self.env[model]
            if callable(domain):
                domain = domain(Model)
            if not Model.has_access("read"):
                continue  # a role without read access simply does not see the step
            if "company_id" in Model._fields:
                # shared (no company) records count for everyone
                domain = domain + [("company_id", "in", [False, self.env.company.id])]
            count = Model.search_count(domain)
            out.append({
                "key": key, "label": str(label), "count": count,
                "state": "done" if count else "todo", "action": action,
            })
        return out

    @api.model
    @api.readonly
    def get_cockpit_data(self):
        """JSON-able payload for the OWL cockpit. The foundation `pipeline` is
        sequential and numbered at render time (no gaps); `methods` are the
        installed costing methodologies, shown as parallel alternatives applied
        on the same cost base. Runs with the caller's ACL/company scope — no
        sudo, only primitives returned."""
        pipeline = self._cmc_collect(BASE_STEPS)
        # sequential numbering + first-not-done = "next"
        for idx, step in enumerate(pipeline, start=1):
            step["num"] = idx
        for step in pipeline:
            if step["state"] == "todo":
                step["state"] = "next"
                break

        # methodologies: parallel; "done" if already used, else "available"
        methods = self._cmc_collect(METHOD_STEPS)
        base_ready = all(s["state"] == "done" for s in pipeline)
        for m in methods:
            if m["state"] == "todo":
                # unlocked once the cost base is built, otherwise greyed
                m["state"] = "available" if base_ready else "locked"

        currency = self.env.company.currency_id
        AL = self.env["account.analytic.line"]
        company_domain = [("company_id", "=", self.env.company.id)]

        can_read_cost = AL.has_access("read")
        # total booked cost (whole cost base), as a magnitude
        total_cost = 0.0
        if can_read_cost:
            cost_groups = AL._cmc_cost_read_group(AL._cmc_cost_base_domain() + company_domain)
            total_cost = cost_groups[0][-1] if cost_groups else 0.0

        all_steps = pipeline + methods
        runs_done = sum(s["count"] for s in all_steps if s["key"] in DONE_RUN_KEYS)
        Result = self.env.get("cmc.costing.result")
        can_read_products = Result is not None and Result.has_access("read")
        products_costed = Result.search_count(company_domain) if can_read_products else 0
        centers = next((s["count"] for s in pipeline if s["key"] == "centers"), 0)

        kpis = [
            {"key": "centers", "label": self.env._("Cost Centers"),
             "value": str(centers)},
            {"key": "cost", "label": self.env._("Booked Costs"),
             "value": formatLang(self.env, total_cost, currency_obj=currency)},
            {"key": "runs", "label": self.env._("Computations"),
             "value": str(runs_done)},
            {"key": "products", "label": self.env._("Costed Products"),
             "value": str(products_costed)},
        ]
        # a KPI the caller cannot read is omitted, not shown as a misleading zero
        kpis = [
            k for k in kpis
            if (k["key"] != "cost" or can_read_cost) and (k["key"] != "products" or can_read_products)
        ]

        # cost-by-element chart (stored FK -> _read_group is fine); a caller who cannot read elements gets no chart
        # at all, not an empty one that reads "no classified cost" (SC-082)
        can_read_chart = can_read_cost and self.env["cmc.cost.element"].has_access("read")
        el_groups = AL._cmc_cost_read_group(
            [("cmc_cost_element_id", "!=", False)] + AL._cmc_cost_base_domain() + company_domain,
            groupby=["cmc_cost_element_id"],
        ) if can_read_chart else []
        # why: a net-credit element has no slice in a doughnut
        el_groups = [(el, cost) for el, cost in el_groups if cost > 0]
        chart = {
            "labels": [el.display_name for el, _ in el_groups],
            "values": [cost for _, cost in el_groups],
        } if can_read_chart else False

        return {"pipeline": pipeline, "methods": methods,
                "kpis": kpis, "chart": chart}

    # ------------------------------------------------------------------ controller dashboard

    @api.model
    @api.readonly
    def get_dashboard_data(self, period=None):
        """Controller dashboard payload (JSON primitives). Runs with the caller's ACL and
        current company only; every contributor omits what the caller cannot read."""
        period = self._cmc_dashboard_period(period)
        cards = self._cmc_dashboard_cards(period)
        alerts = self._cmc_dashboard_alerts(period)
        kpis = self._cmc_dashboard_select_kpis(self._cmc_dashboard_kpis(period))
        charts = self._cmc_dashboard_charts(period)

        cards_by_key = {card["key"]: card for card in cards}
        for alert in alerts:
            card = cards_by_key.get(alert.get("card"))
            if card and card["status"] != "empty":
                card["status"] = "attention"
        alerts.sort(key=lambda a: (
            SEVERITY_RANK[a["severity"]],
            -(a["amount"] if a["amount"] is not None else a["count"]),
            a["key"],
        ))
        area_seq = {key: seq for key, seq, _label in DASHBOARD_AREAS}
        cards.sort(key=lambda c: (area_seq[c["area"]], c["sequence"], c["key"]))
        used = {card["area"] for card in cards}
        areas = [{"key": key, "label": str(label)} for key, _seq, label in DASHBOARD_AREAS if key in used]
        cost = next((k for k in kpis if k["key"] == "base.cost"), None)
        currency = self.env.company.currency_id
        return {
            "header": {
                "period": {
                    "kind": period["kind"],
                    "date_from": period["date_from"] and period["date_from"].isoformat(),
                    "date_to": period["date_to"] and period["date_to"].isoformat(),
                    "label": period["label"],
                    "prev_ref": period["prev_ref"],
                    "next_ref": period["next_ref"],
                },
                "currency": {"symbol": currency.symbol, "position": currency.position},
                "global_empty": all(c["status"] == "empty" for c in cards) and not (cost and cost["raw"]),
            },
            "kpis": kpis,
            "alerts": alerts,
            "cards": cards,
            "charts": charts,
            "areas": areas,
        }

    # contributor hooks: overrides call super(), append their items, return the list
    @api.model
    def _cmc_dashboard_kpis(self, period):
        return self._cmc_dashboard_base_cost_kpi(period) + self._cmc_dashboard_base_capacity_kpi(period)

    @api.model
    def _cmc_dashboard_alerts(self, period):
        return (
            self._cmc_dashboard_base_unclassified_alert(period)
            + self._cmc_dashboard_base_residual_alert(period)
            + self._cmc_dashboard_base_closing_missing_alert(period)
        )

    @api.model
    def _cmc_dashboard_cards(self, period):
        return self._cmc_dashboard_base_closing_card(period) + self._cmc_dashboard_base_capacity_card(period)

    @api.model
    def _cmc_dashboard_charts(self, period):
        return self._cmc_dashboard_base_cost_chart(period)

    # ------------------------------------------------------------------ shared helpers

    @api.model
    def _cmc_dashboard_period(self, period_in=None):
        period_in = period_in or {}
        if not isinstance(period_in, dict):
            raise UserError(self.env._("Invalid dashboard period."))
        kind = period_in.get("kind") or "year"
        if kind not in PERIOD_KINDS:
            raise UserError(self.env._("Invalid dashboard period."))
        try:
            ref = fields.Date.to_date(period_in.get("ref")) or fields.Date.context_today(self)
        except (ValueError, TypeError):
            raise UserError(self.env._("Invalid dashboard period.")) from None
        period = {"kind": kind, "memo": {}, "date_from": None, "date_to": None, "prev_date_from": None,
                  "prev_date_to": None, "prev_ref": None, "next_ref": None}
        if kind == "all":
            period["label"] = period["prev_label"] = self.env._("All")
            return period
        step = relativedelta(months=3) if kind == "quarter" else relativedelta(**{kind + "s": 1})
        prev_ref = ref - relativedelta(years=1)
        today = fields.Date.context_today(self)
        period.update(
            date_from=date_utils.start_of(ref, kind),
            date_to=date_utils.end_of(ref, kind),
            prev_date_from=date_utils.start_of(prev_ref, kind),
            prev_date_to=date_utils.end_of(prev_ref, kind),
            prev_ref=(ref - step).isoformat(),
            next_ref=(ref + step).isoformat(),
            label=self._cmc_dashboard_period_label(ref, kind),
            prev_label=self._cmc_dashboard_period_label(prev_ref, kind),
        )
        if period["date_from"] <= today <= period["date_to"]:
            # why: the running period is year-to-date, compared like-for-like with the same span of last year
            period.update(date_to=today, prev_date_to=today - relativedelta(years=1))
        return period

    @api.model
    def _cmc_dashboard_period_label(self, ref, kind):
        if kind == "month":
            return format_date(self.env, ref, date_format="LLLL yyyy")
        if kind == "quarter":
            return self.env._("Q%(quarter)s %(year)s", quarter=(ref.month - 1) // 3 + 1, year=ref.year)
        return str(ref.year)

    @api.model
    def _cmc_dashboard_period_domain(self, period, field="date", prev=False, closed_months=False):
        """Leaves restricting `field` to the period (or to the same period of the previous year).

        `closed_months`: for month-dated figures (dated the 1st), the running period stops before the current month."""
        date_from, date_to = (period["prev_date_from"], period["prev_date_to"]) if prev else (period["date_from"], period["date_to"])
        if not date_from:
            return []
        cut = None if prev else self._cmc_dashboard_closed_months_cut(period)
        if closed_months and cut:
            return [(field, ">=", date_from.isoformat()), (field, "<", cut.isoformat())]
        return [(field, ">=", date_from.isoformat()), (field, "<=", date_to.isoformat())]

    @api.model
    def _cmc_dashboard_closed_months_cut(self, period):
        """First day of the current month when the period contains today (the month is not closed yet), else None."""
        today = fields.Date.context_today(self)
        if period["kind"] != "all" and period["date_from"] <= today <= period["date_to"]:
            return date_utils.start_of(today, "month")
        return None

    @api.model
    def _cmc_dashboard_memo(self, period, key, compute):
        """Per-call cache: the same figure feeds KPI, card, alert and chart with one query."""
        memo = period["memo"]
        if key not in memo:
            memo[key] = compute()
        return memo[key]

    @api.model
    def _cmc_dashboard_company_domain(self, Model):
        field = Model._fields.get("company_id")
        if field is None:
            return []
        company_id = self.env.company.id
        if field.required:
            return [("company_id", "=", company_id)]
        return [("company_id", "in", [False, company_id])]

    @api.model
    def _cmc_dashboard_readable(self, *models):
        return all((self.env[m] if isinstance(m, str) else m).has_access("read") for m in models)

    @api.model
    def _cmc_dashboard_latest_run(self, Model, fields, order, domain=None):
        domain = list(domain or []) + self._cmc_dashboard_company_domain(Model)
        if "state" in Model._fields:
            domain.append(("state", "=", "done"))
        rows = Model.search_read(domain, fields, limit=1, order=order)
        return rows[0] if rows else None

    @api.model
    def _cmc_dashboard_in_period(self, date_from, date_to, period):
        if period["kind"] == "all":
            return True
        date_from, date_to = fields.Date.to_date(date_from), fields.Date.to_date(date_to)
        if not (date_from or date_to):
            return False
        return (date_from or date_to) <= period["date_to"] and (date_to or date_from) >= period["date_from"]

    @api.model
    def _cmc_dashboard_run_status(self, run_date_to, period):
        run_date_to = fields.Date.to_date(run_date_to)
        if period["kind"] == "all" or not run_date_to or run_date_to >= period["date_to"]:
            return "up_to_date"
        return "outdated"

    @api.model
    def _cmc_dashboard_money(self, amount, short=False):
        currency = self.env.company.currency_id
        if not (short and abs(amount) >= 1e6):
            return formatLang(self.env, amount, currency_obj=currency)
        number = "%s\u00a0Mln" % formatLang(self.env, amount / 1e6, digits=2)
        return "%s\u00a0%s" % ((number, currency.symbol) if currency.position == "after" else (currency.symbol, number))

    @api.model
    def _cmc_dashboard_format(self, raw, kind):
        if raw is None:
            return DASH
        if kind == "monetary":
            return self._cmc_dashboard_money(raw)
        if kind == "percent":
            return "%s %%" % formatLang(self.env, raw, digits=1)
        if kind == "count":
            return formatLang(self.env, raw, digits=0)
        if kind == "ratio":
            return formatLang(self.env, raw, digits=2)
        return str(raw)

    @api.model
    def _cmc_dashboard_figure(self, key, label, raw, kind, status="info"):
        return {"key": key, "label": str(label), "value": self._cmc_dashboard_format(raw, kind),
                "raw": raw, "kind": kind, "status": status}

    @api.model
    def _cmc_dashboard_kpi(self, slot, priority, key, label, raw, kind, caption, sub=None, trend=None, status="info"):
        kpi = self._cmc_dashboard_figure(key, label, raw, kind, status)
        kpi.update(slot=slot, priority=priority, caption=str(caption), sub=sub, trend=trend, value_full=kpi["value"])
        if kind == "monetary" and raw is not None:
            kpi["value"] = self._cmc_dashboard_money(raw, short=True)
        return kpi

    @api.model
    def _cmc_dashboard_select_kpis(self, kpis):
        """One tile per slot: the lowest priority with a value wins, else the first (shows the dash)."""
        by_slot = {}
        for kpi in sorted(kpis, key=lambda k: k["priority"]):
            best = by_slot.get(kpi["slot"])
            if best is None or (best["raw"] is None and kpi["raw"] is not None):
                by_slot[kpi["slot"]] = kpi
        return [by_slot[slot] for slot in KPI_SLOTS if slot in by_slot]

    @api.model
    def _cmc_dashboard_card(self, key, area, sequence, title, run, period, figures, action, date_to=None, date_from=None):
        card = {"key": key, "area": area, "sequence": sequence, "title": str(title), "subtitle": "",
                "status": "empty", "run_date_from": None, "run_date_to": None, "in_period": False,
                "figures": [], "action": action, "domain": []}
        if not run:
            card["subtitle"] = self.env._("No computation yet. Create the first one to see results here.")
            return card
        date_from = fields.Date.to_date(date_from or run.get("date_from"))
        date_to = fields.Date.to_date(date_to or run.get("date_to"))
        parts = [run.get("name"), date_to and format_date(self.env, date_to)]
        card.update(
            subtitle=" · ".join(p for p in parts if p),
            status=self._cmc_dashboard_run_status(date_to, period),
            run_date_from=date_from and date_from.isoformat(),
            run_date_to=date_to and date_to.isoformat(),
            # undated artefacts (no date at all) are never "out of period"
            in_period=self._cmc_dashboard_in_period(date_from, date_to, period) if (date_from or date_to) else True,
            figures=figures,
        )
        return card

    @api.model
    def _cmc_dashboard_alert(self, key, severity, label, count, action, domain, card=None, amount=None, scope="records"):
        return {"key": key, "severity": severity, "label": label, "count": count,
                "amount": None if amount is None else abs(amount), "action": action,
                "domain": domain, "card": card, "scope": scope}

    # ------------------------------------------------------------------ cmc_base contribution

    @api.model
    def _cmc_dashboard_classified_domain(self, AL):
        return AL._cmc_cost_base_domain() + [
            ("cmc_behavior", "!=", False),
            ("cmc_cost_element_id", "!=", False),
        ] + self._cmc_dashboard_company_domain(AL)

    @api.model
    def _cmc_dashboard_base_cost(self, period):
        """One query for classified cost by element, previous-year cost and unclassified lines
        (None when the caller cannot read the lines)."""
        def compute():
            AL = self.env["account.analytic.line"]
            if not AL.has_access("read"):
                return None
            dated = period["kind"] != "all"
            domain = AL._cmc_cost_base_domain() + self._cmc_dashboard_company_domain(AL)
            groupby = ["cmc_cost_element_id", "cmc_behavior"]
            if dated:
                cur, prev = (self._cmc_dashboard_period_domain(period, prev=p) for p in (False, True))
                domain += ["|", "&", *cur, "&", *prev]
                groupby.append("date:%s" % period["kind"])
            by_element, prev_cost, unclassified, unclassified_cost = defaultdict(float), 0.0, 0, 0.0
            for element, behavior, *rest in AL._read_group(domain, groupby, ["__count", "amount:sum"]):
                count, cost = rest[-2], -(rest[-1] or 0.0)
                if dated and rest[0] == period["prev_date_from"]:
                    prev_cost += cost if element and behavior else 0.0
                elif element and behavior:
                    by_element[element] += cost
                else:
                    unclassified, unclassified_cost = unclassified + count, unclassified_cost + cost
            return {"groups": list(by_element.items()), "total": sum(by_element.values()) if by_element else None,
                    "prev": prev_cost, "unclassified": unclassified, "unclassified_cost": unclassified_cost}
        return self._cmc_dashboard_memo(period, "base.cost", compute)

    @api.model
    def _cmc_dashboard_base_cost_kpi(self, period):
        cost = self._cmc_dashboard_base_cost(period)
        if cost is None:
            return []
        AL = self.env["account.analytic.line"]
        raw, prev, trend = cost["total"], cost["prev"], None
        if raw is None and not AL.search_count(self._cmc_dashboard_classified_domain(AL), limit=1):
            return []  # never any classified cost: the tile is omitted, not a dash
        if raw is not None and period["kind"] != "all" and prev > 0:
            pct = round((raw - prev) / prev * 100, 1)
            direction = "up" if pct > 0 else "down" if pct < 0 else "flat"
            trend = {
                "text": self.env._(
                    "%(pct)s %% vs same period %(period)s",
                    pct=("+" if pct > 0 else "") + formatLang(self.env, pct, digits=1),
                    period=period["prev_label"],
                ),
                "direction": direction,
                # a cost going up is unfavourable
                "favourable": direction != "up",
            }
        sub = self.env._("No data in this period") if raw is None else None
        return [self._cmc_dashboard_kpi(
            "cost", 10, "base.cost", self.env._("Booked costs"), raw, "monetary",
            self.env._("from Cost Entry"), sub=sub, trend=trend,
        )]

    @api.model
    def _cmc_dashboard_base_capacity(self, period):
        """Capacity analyses overlapping the period, aggregated; else the latest one (safety net)."""
        def compute():
            Cap = self.env["cmc.capacity.analysis"]
            if not Cap.has_access("read"):
                return None
            domain = self._cmc_dashboard_company_domain(Cap)
            if period["kind"] != "all":
                domain += [("date_from", "<=", period["date_to"].isoformat()), ("date_to", ">=", period["date_from"].isoformat())]
            # per center, so an over-used center (negative unused cost) never offsets the unused capacity of the others
            rows = Cap._read_group(
                domain, ["center_id"], ["__count", "unused_capacity_cost:sum", "fixed_cost:sum", "utilization_rate:avg",
                                        "date_from:min", "date_to:max"])
            count = sum(row[1] for row in rows)
            if count:
                return {"in_period": True, "count": count, "unused": sum(max(row[2] or 0.0, 0.0) for row in rows),
                        "fixed": sum(row[3] or 0.0 for row in rows),
                        "util": sum((row[4] or 0.0) * row[1] for row in rows) / count,
                        "date_from": min(row[5] for row in rows), "date_to": max(row[6] for row in rows)}
            last = self._cmc_dashboard_latest_run(
                Cap, ["date_from", "date_to", "unused_capacity_cost", "fixed_cost", "utilization_rate"], "date_to desc, id desc")
            return last and {"in_period": False, "count": 1, "unused": max(last["unused_capacity_cost"], 0.0), "fixed": last["fixed_cost"],
                             "util": last["utilization_rate"], "date_from": last["date_from"], "date_to": last["date_to"]}
        return self._cmc_dashboard_memo(period, "base.capacity", compute)

    @api.model
    def _cmc_dashboard_base_capacity_kpi(self, period):
        cap = self._cmc_dashboard_base_capacity(period)
        if not cap:
            return []
        in_period = cap["in_period"]
        return [self._cmc_dashboard_kpi(
            "capacity", 30, "base.capacity", self.env._("Unused capacity"), cap["unused"] if in_period else None, "monetary",
            self.env._("from Capacity Analysis"),
            sub=self.env._("Utilisation %s", self._cmc_dashboard_format(cap["util"], "percent")) if in_period
            else self.env._("No data in this period"),
        )]

    @api.model
    def _cmc_dashboard_base_capacity_card(self, period):
        cap = self._cmc_dashboard_base_capacity(period)
        if cap is None and not self._cmc_dashboard_readable("cmc.capacity.analysis"):
            return []
        args = ("base.capacity", "decision", 10, self.env._("Capacity"))
        if not cap:
            return [self._cmc_dashboard_card(*args, None, period, [], "cmc_base.action_cmc_capacity")]
        figures = [
            self._cmc_dashboard_figure("base.capacity.utilization", self.env._("Utilisation"), cap["util"], "percent"),
            self._cmc_dashboard_figure("base.capacity.fixed", self.env._("Fixed cost"), cap["fixed"], "monetary"),
        ]
        run = {"name": self.env._("%s analyses", cap["count"]), "date_from": cap["date_from"], "date_to": cap["date_to"]}
        return [self._cmc_dashboard_card(*args, run, period, figures, "cmc_base.action_cmc_capacity")]

    @api.model
    def _cmc_dashboard_base_closing(self, period):
        """Latest done closing + its distributed/residual totals (None when unreadable)."""
        def compute():
            Run, Line = self.env["cmc.closing.run"], self.env["cmc.closing.line"]
            if not self._cmc_dashboard_readable(Run, Line):
                return None
            run = self._cmc_dashboard_latest_run(Run, ["name", "date_from", "date_to"], "date_to desc, id desc")
            distributed = residual = 0.0
            if run:
                distributed, residual = Line._read_group(
                    [("run_id", "=", run["id"])], [], ["cost_distributed:sum", "cost_residual:sum"])[0]
            return {"run": run, "distributed": distributed or 0.0, "residual": residual or 0.0}
        return self._cmc_dashboard_memo(period, "base.closing", compute)

    @api.model
    def _cmc_dashboard_base_closing_card(self, period):
        closing = self._cmc_dashboard_base_closing(period)
        if closing is None:
            return []
        figures = [
            self._cmc_dashboard_figure("base.closing.distributed", self.env._("Cost distributed"), closing["distributed"], "monetary"),
            self._cmc_dashboard_figure("base.closing.residual", self.env._("Residual cost"), closing["residual"], "monetary"),
        ]
        return [self._cmc_dashboard_card(
            "base.closing", "recording", 10, self.env._("Period closing"), closing["run"], period, figures,
            "cmc_base.action_cmc_closing_run")]

    @api.model
    def _cmc_dashboard_base_unclassified_alert(self, period):
        cost = self._cmc_dashboard_base_cost(period)
        if not cost or not cost["unclassified"]:
            return []
        AL = self.env["account.analytic.line"]
        count, amount = cost["unclassified"], cost["unclassified_cost"]
        # the action's own domain is part of the alert domain, so the opened list counts exactly `count`
        domain = AL._cmc_cost_base_domain() + self._cmc_dashboard_company_domain(AL) + self._cmc_dashboard_period_domain(period) + [
            "|", ("cmc_behavior", "=", False), ("cmc_cost_element_id", "=", False),
        ]
        return [self._cmc_dashboard_alert(
            "base.unclassified", "warning",
            self.env._("%(count)s unclassified cost lines (%(amount)s)", count=count, amount=self._cmc_dashboard_money(amount))
            if count > 1 else self.env._("1 unclassified cost line (%(amount)s)", amount=self._cmc_dashboard_money(amount)),
            count, "cmc_base.action_cmc_analytic_lines", domain, amount=amount)]

    @api.model
    def _cmc_dashboard_base_residual_alert(self, period):
        closing = self._cmc_dashboard_base_closing(period)
        if not closing or not closing["run"]:
            return []
        residual = closing["residual"]
        if float_is_zero(residual, precision_rounding=self.env.company.currency_id.rounding):
            return []
        return [self._cmc_dashboard_alert(
            "base.residual", "warning",
            self.env._("Residual costs not allocated: %s", self._cmc_dashboard_money(residual)),
            1, "cmc_base.action_cmc_closing_run", [("id", "=", closing["run"]["id"])],
            card="base.closing", amount=residual, scope="run")]

    @api.model
    def _cmc_dashboard_base_closing_missing_alert(self, period):
        closing = self._cmc_dashboard_base_closing(period)
        if not closing or not closing["run"]:
            return []  # a company that never closes is not nagged
        prev_month = fields.Date.context_today(self) - relativedelta(months=1)
        month_end = date_utils.end_of(prev_month, "month").isoformat()
        # the latest done closing decides; a query is needed only for a closing that starts after the month end
        run = closing["run"]
        date_to, date_from = fields.Date.to_date(run["date_to"]), fields.Date.to_date(run["date_from"])
        end = date_utils.end_of(prev_month, "month")
        if date_to >= end and date_from <= end:
            return []
        if date_to > end:
            Run = self.env["cmc.closing.run"]
            if Run.search_count(self._cmc_dashboard_company_domain(Run) + [
                    ("state", "=", "done"), ("date_from", "<=", month_end), ("date_to", ">=", month_end)], limit=1):
                return []
        month = format_date(self.env, prev_month, date_format="LLLL yyyy")
        return [self._cmc_dashboard_alert(
            "base.closing_missing", "info", self.env._("%s closing not run", month), 1,
            "cmc_base.action_cmc_closing_run", [], scope="run")]

    @api.model
    def _cmc_dashboard_base_cost_chart(self, period):
        cost = self._cmc_dashboard_base_cost(period)
        if cost is None or not self.env["cmc.cost.element"].has_access("read"):
            return []
        # why: a net-credit element has no bar
        groups = sorted(((el, c) for el, c in cost["groups"] if c > 0), key=lambda g: (-g[1], g[0].id))
        if not groups:
            return []
        labels = [el.display_name for el, _c in groups[:KPI_TOP_ELEMENTS]]
        data = [c for _el, c in groups[:KPI_TOP_ELEMENTS]]
        if len(groups) > KPI_TOP_ELEMENTS:
            labels.append(self.env._("Others"))
            data.append(sum(c for _el, c in groups[KPI_TOP_ELEMENTS:]))
        return [{
            "key": "base.cost_by_element", "type": "bar", "horizontal": True,
            "title": self.env._("Costs by nature"), "labels": labels,
            "datasets": [{"label": self.env._("Cost"), "data": data,
                          "formatted": [self._cmc_dashboard_money(v) for v in data]}],
        }]
