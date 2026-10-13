"""DASH (controller dashboard) - contribution of ``cmc_profitability`` to ``cmc.cockpit.get_dashboard_data``.

Contract: scratchpad DASH-devlead.md section 1 (KPI per slot) + section 2 (profitability, direct_costing), UX-002
KPI 3 + reconciliation (KPI 3 = latest done IN period), DASH-ADR AD-003/005.
- Card ``profitability.run`` (decision, 50): latest DONE ``cmc.profit.run`` (date_to desc, id desc), revenue_total /
  contribution_total; action ``cmc_profitability.action_cmc_profit_run``; nothing computed -> "empty".
- KPI ``profitability.result`` (slot "result", priority 10): operating_profit of the latest done run in the period.
  ``direct_costing.result`` (priority 20) competes for the same slot: lowest priority WITH a value wins; a slot shows
  one KPI only. Profit runs unreadable -> profitability does not emit, direct costing takes the slot (SC-082).
- Alert ``profitability.unassigned`` (warning, scope run): unassigned_cost > 0 on the latest done run, domain
  [("id", "=", run id)], card "profitability.run" -> card status "attention".
"""

from freezegun import freeze_time as _freeze_time

from odoo import Command
from odoo.fields import Domain
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools.safe_eval import safe_eval

from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE

CARD = "profitability.run"
KPI = "profitability.result"
DC_KPI = "direct_costing.result"
ALERT = "profitability.unassigned"



class DashboardCase(TransactionCase):
    """Isolated company + a CMC user; helpers to read the payload by key."""
    # why: the running period is capped at today; a fixed clock keeps the 2026 fixtures full-calendar
    freeze_time = _freeze_time("2026-12-31 12:00:00")

    _login = "dash_profit_user"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "DASH company %s" % cls._login})
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=[cls.company.id]))
        cls.user = new_test_user(
            cls.env, login=cls._login, groups="base.group_user,cmc_base.group_cmc_user",
            company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)],
        )

    @classmethod
    def _done(cls, record, **vals):
        record.with_context(cmc_run_state=CMC_RUN_STATE).write({"state": "done", **vals})
        return record

    def _data(self, kind="year", ref="2026-06-15", user=None):
        cockpit = self.env["cmc.cockpit"]
        if user:
            cockpit = cockpit.with_user(user)
        return cockpit.get_dashboard_data({"kind": kind, "ref": ref})

    @staticmethod
    def _item(data, section, key):
        return next((item for item in data[section] if item["key"] == key), None)

    def _keys(self, data, section, prefix):
        return [item["key"] for item in data[section] if item["key"].startswith(prefix)]

    def assertRawIn(self, value, item):
        raws = [f["raw"] for f in item["figures"]]
        self.assertTrue(
            any(isinstance(r, (int, float)) and not isinstance(r, bool) and abs(r - value) < 0.005 for r in raws),
            "%s not among the figures %s of %s" % (value, raws, item["key"]),
        )

    def _assert_records_invariant(self, alert):
        # AD-002: the deep link opens action domain AND alert domain; it must select exactly the counted records
        action = self.env["ir.actions.actions"]._for_xml_id(alert["action"])
        action_domain = action.get("domain") or []
        if isinstance(action_domain, str):
            action_domain = safe_eval(action_domain, {"uid": self.env.uid, "context": {}})
        domain = Domain([tuple(t) if isinstance(t, list) else t for t in action_domain]) & Domain(
            [tuple(t) if isinstance(t, list) else t for t in alert["domain"]]
        )
        self.assertEqual(self.env[action["res_model"]].search_count(list(domain)), alert["count"], alert)

    def _revoke_read(self, *models):
        self.env["ir.model.access"].search([("model_id.model", "in", list(models))]).write({"perm_read": False})


@tagged("post_install", "-at_install")
class TestProfitabilityDashboard(DashboardCase):
    @classmethod
    def _profit(cls, date_from, date_to, done=True, company=None, **figures):
        profit = cls.env["cmc.profit.run"].create({
            "name": "DASH profit %s" % date_to, "company_id": (company or cls.company).id,
            "date_from": date_from, "date_to": date_to,
        })
        return cls._done(profit, **figures) if done else profit

    @classmethod
    def _direct(cls, date_from, date_to):
        return cls.env["cmc.direct.costing"].create({
            "name": "DASH DC", "company_id": cls.company.id, "mode": "simple", "date_from": date_from, "date_to": date_to,
            "common_fixed_cost": 1000.0,
            "line_ids": [Command.create({"name": "A", "revenue": 10000.0, "variable_cost": 7000.0})],
        })

    def _result_kpis(self, data):
        return [kpi for kpi in data["kpis"] if kpi.get("slot") == "result"]

    def test_empty_card_without_done_run(self):
        # DASH 2 / UX-002: nothing computed -> empty card with the "Create" action; never data -> no KPI, no alert
        self._profit("2026-01-01", "2026-12-31", done=False, operating_profit=1.0)
        data = self._data()
        card = self._item(data, "cards", CARD)
        self.assertTrue(card)
        self.assertEqual(card["status"], "empty")
        self.assertEqual(card["area"], "decision")
        self.assertEqual(card["action"], "cmc_profitability.action_cmc_profit_run")
        self.assertIsNone(self._item(data, "kpis", KPI))
        self.assertIsNone(self._item(data, "alerts", ALERT))

    def test_card_reads_latest_done_run_figures(self):
        # AD-003: latest DONE run; a newer draft is ignored
        self._profit("2026-01-01", "2026-06-30", revenue_total=1.0, contribution_total=1.0)
        latest = self._profit("2026-01-01", "2026-12-31", revenue_total=10000.0, contribution_total=4000.0, operating_profit=1500.0)
        self._profit("2026-01-01", "2027-06-30", done=False)
        card = self._item(self._data(), "cards", CARD)
        self.assertRawIn(latest.revenue_total, card)
        self.assertRawIn(latest.contribution_total, card)
        self.assertEqual(card["status"], "up_to_date")

    def test_result_kpi_from_latest_done_run_in_period(self):
        # UX-002 KPI 3: operating profit of the latest done run in the period, slot "result"
        self._profit("2026-01-01", "2026-03-31", operating_profit=300.0, revenue_total=3000.0)
        latest = self._profit("2026-04-01", "2026-06-30", operating_profit=1500.0, revenue_total=10000.0)
        kpi = self._item(self._data(), "kpis", KPI)
        self.assertTrue(kpi)
        self.assertEqual(kpi["slot"], "result")
        self.assertAlmostEqual(kpi["raw"], latest.operating_profit, places=2)

    def test_profitability_wins_the_result_slot_over_direct_costing(self):
        # DASH 1: lowest priority with a value wins the slot (profitability 10 < direct costing 20); one KPI per slot
        self._profit("2026-01-01", "2026-06-30", operating_profit=1500.0, revenue_total=10000.0)
        self._direct("2026-01-01", "2026-06-30")
        data = self._data()
        self.assertEqual([kpi["key"] for kpi in self._result_kpis(data)], [KPI])
        self.assertIsNone(self._item(data, "kpis", DC_KPI))

    def test_direct_costing_takes_the_slot_when_profit_runs_unreadable(self):
        # SC-082: profit runs unreadable -> profitability KPI and card omitted, the direct costing result fills the slot
        self._profit("2026-01-01", "2026-06-30", operating_profit=1500.0, revenue_total=10000.0)
        direct = self._direct("2026-01-01", "2026-06-30")
        self.assertEqual(self._item(self._data(user=self.user), "kpis", KPI)["raw"], 1500.0, "positive control")
        self._revoke_read("cmc.profit.run")
        data = self._data(user=self.user)
        self.assertIsNone(self._item(data, "kpis", KPI))
        self.assertIsNone(self._item(data, "cards", CARD))
        self.assertIsNone(self._item(data, "alerts", ALERT))
        kpi = self._item(data, "kpis", DC_KPI)
        self.assertTrue(kpi)
        self.assertEqual(kpi["slot"], "result")
        self.assertAlmostEqual(kpi["raw"], direct.operating_result, places=2)

    def test_direct_costing_takes_the_slot_when_no_profit_run_in_period(self):
        # DASH 1: profitability has data but none in the period (raw None) -> the direct costing value wins
        self._profit("2025-01-01", "2025-12-31", operating_profit=1500.0, revenue_total=10000.0)
        direct = self._direct("2026-01-01", "2026-06-30")
        data = self._data()
        self.assertEqual([kpi["key"] for kpi in self._result_kpis(data)], [DC_KPI])
        self.assertAlmostEqual(self._item(data, "kpis", DC_KPI)["raw"], direct.operating_result, places=2)

    def test_result_kpi_dash_when_no_run_in_period(self):
        # UX-002: data exists but none in the period and no other source -> "—", raw None, never 0
        self._profit("2025-01-01", "2025-12-31", operating_profit=1500.0, revenue_total=10000.0)
        kpi = self._item(self._data(), "kpis", KPI)
        self.assertTrue(kpi)
        self.assertIsNone(kpi["raw"])
        self.assertEqual(kpi["value"], "—")

    def test_unassigned_alert_on_latest_done_run(self):
        # UX-002: "Unassigned costs (profitability)" on the latest done run, scoped to that run; the card turns "attention"
        latest = self._profit("2026-01-01", "2026-12-31", operating_profit=1500.0, unassigned_cost=300.0)
        data = self._data()
        alert = self._item(data, "alerts", ALERT)
        self.assertTrue(alert)
        self.assertEqual(alert["severity"], "warning")
        self.assertEqual(alert["action"], "cmc_profitability.action_cmc_profit_run")
        self.assertEqual([list(term) for term in alert["domain"]], [["id", "=", latest.id]])
        self.assertAlmostEqual(alert["amount"], 300.0, places=2)
        self.assertEqual(alert["card"], CARD)
        self.assertEqual(self._item(data, "cards", CARD)["status"], "attention")

    def test_no_unassigned_alert_when_fully_assigned(self):
        # UX-002: an alert only when there is something to report
        self._profit("2026-01-01", "2026-12-31", operating_profit=1500.0, unassigned_cost=0.0)
        self.assertIsNone(self._item(self._data(), "alerts", ALERT))

    def test_no_unassigned_alert_when_negative(self):
        # a negative unassigned cost (over-assignment) is not an alert: only > 0
        self._profit("2026-01-01", "2026-12-31", operating_profit=1500.0, unassigned_cost=-424979.10)
        self.assertIsNone(self._item(self._data(), "alerts", ALERT))

    def test_card_does_not_repeat_headline_kpi(self):
        # UX-002: the operating result is the KPI tile, not a card figure
        self._profit("2026-01-01", "2026-12-31", revenue_total=10000.0, contribution_total=4000.0, operating_profit=1500.0)
        card = self._item(self._data(), "cards", CARD)
        self.assertNotIn(1500.0, [f["raw"] for f in card["figures"]])

    def test_other_company_run_is_ignored(self):
        # AD-004: self.env.company only
        other = self.env["res.company"].create({"name": "DASH profit other"})
        self._profit("2026-01-01", "2026-12-31", company=other, operating_profit=1500.0, unassigned_cost=300.0)
        data = self._data()
        self.assertEqual(self._item(data, "cards", CARD)["status"], "empty")
        self.assertIsNone(self._item(data, "kpis", KPI))
        self.assertIsNone(self._item(data, "alerts", ALERT))
