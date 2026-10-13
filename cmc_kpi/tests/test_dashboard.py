"""DASH (controller dashboard) - contribution of ``cmc_kpi`` to ``cmc.cockpit.get_dashboard_data``.

Contract: scratchpad DASH-devlead.md section 2 (kpi), UX-002 (KPI 5, "{n} KPIs in red" row, Performance card),
DASH-ADR AD-002/003/005.
- KPI ``kpi.scorecard`` (slot "scorecard"): ``cmc.kpi.scorecard.result`` with perspective False (overall score),
  run done, date <= period end, order date desc, limit 2 -> value = latest score, trend = delta vs the previous one.
  Never any result -> KPI not emitted; results but none up to the period end -> raw None.
- Card ``kpi.scorecard`` (performance, 10): ``_read_group`` of ``cmc.kpi.value`` by status (run done, date in period).
- Alert ``kpi.red`` (danger, scope records, action ``cmc_kpi.action_cmc_kpi_value``): done-run values, status red, date
  in period, company; invariant search_count(action domain AND alert domain) == count.
Result rows are written under the CMC_RUN_STATE sentinel, as the engine does.
"""

from freezegun import freeze_time as _freeze_time

from odoo import Command
from odoo.fields import Domain
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools.safe_eval import safe_eval

from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE

KPI = "kpi.scorecard"
CARD = "kpi.scorecard"
ALERT = "kpi.red"
VALUE_ACTION = "cmc_kpi.action_cmc_kpi_value"



class DashboardCase(TransactionCase):
    """Isolated company + a CMC user; helpers to read the payload by key."""
    # why: the running period is capped at today; a fixed clock keeps the 2026 fixtures full-calendar
    freeze_time = _freeze_time("2026-12-31 12:00:00")

    _login = "dash_kpi_user"

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
class TestKpiDashboard(DashboardCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.scorecard = cls.env["cmc.kpi.scorecard"].create({"name": "DASH scorecard", "company_id": cls.company.id})
        Kpi = cls.env["cmc.kpi"]
        cls.kpi_a = Kpi.create({"name": "DASH A", "code": "dash_a", "source_type": "cascade", "company_id": cls.company.id})
        cls.kpi_b = Kpi.create({"name": "DASH B", "code": "dash_b", "source_type": "cascade", "company_id": cls.company.id})

    @classmethod
    def _kpi_run(cls, date_from, date_to, done=True, scores=(), values=(), company=None):
        """A KPI run with overall/perspective scores [(date, score, perspective)] and values [(kpi, date, status)]."""
        kpi_run = cls.env["cmc.kpi.run"].create({
            "name": "DASH KPI run %s" % date_to, "company_id": (company or cls.company).id,
            "date_from": date_from, "date_to": date_to,
        })
        engine = cls.env(context=dict(cls.env.context, cmc_run_state=CMC_RUN_STATE))
        engine["cmc.kpi.scorecard.result"].create([{
            "run_id": kpi_run.id, "scorecard_id": cls.scorecard.id, "date": day, "score": score, "perspective": perspective,
            "computable": True,
        } for day, score, perspective in scores])
        engine["cmc.kpi.value"].create([{
            "run_id": kpi_run.id, "kpi_id": kpi.id, "date": day, "status": status, "computable": True,
        } for kpi, day, status in values])
        return cls._done(kpi_run) if done else kpi_run

    def test_nothing_computed_empty_card_no_kpi_no_alert(self):
        # UX-002: module installed, never any result -> empty card, no KPI tile, no alert
        self._kpi_run("2026-01-01", "2026-01-31", done=False, scores=[("2026-01-01", 10.0, False)],
                      values=[(self.kpi_a, "2026-01-01", "red")])
        data = self._data()
        card = self._item(data, "cards", CARD)
        self.assertTrue(card)
        self.assertEqual(card["status"], "empty")
        self.assertEqual(card["area"], "performance")
        self.assertEqual(card["action"], "cmc_kpi.action_cmc_kpi_scorecard_result")
        self.assertIsNone(self._item(data, "kpis", KPI))
        self.assertIsNone(self._item(data, "alerts", ALERT))

    def test_scorecard_kpi_latest_overall_score_and_delta(self):
        # UX-002 KPI 5: latest overall score (perspective empty) of a done run up to the period end, delta vs previous
        self._kpi_run("2026-03-01", "2026-03-31", scores=[("2026-03-01", 70.0, False)])
        self._kpi_run("2026-05-01", "2026-06-30", scores=[("2026-05-01", 76.0, False), ("2026-06-01", 99.0, "financial")])
        self._kpi_run("2026-06-01", "2026-06-30", done=False, scores=[("2026-06-01", 10.0, False)])  # draft
        self._kpi_run("2027-01-01", "2027-01-31", scores=[("2027-01-01", 50.0, False)])  # after the period end
        kpi = self._item(self._data(), "kpis", KPI)
        self.assertTrue(kpi)
        self.assertEqual(kpi["slot"], "scorecard")
        self.assertAlmostEqual(kpi["raw"], 76.0, places=2)
        self.assertTrue(kpi["trend"])
        self.assertEqual(kpi["trend"]["direction"], "up")
        self.assertTrue(kpi["trend"]["favourable"])

    def test_scorecard_kpi_dash_when_no_result_up_to_period_end(self):
        # UX-002: results exist but none up to the period end -> raw None, "—"
        self._kpi_run("2026-03-01", "2026-03-31", scores=[("2026-03-01", 70.0, False)])
        kpi = self._item(self._data(ref="2025-06-15"), "kpis", KPI)
        self.assertTrue(kpi)
        self.assertIsNone(kpi["raw"])
        self.assertEqual(kpi["value"], "—")

    def test_card_counts_values_by_status_in_period(self):
        # UX-002 Performance card: count green / amber / red of the period (done runs only)
        self._kpi_run("2026-02-01", "2026-04-30", scores=[("2026-04-01", 70.0, False)], values=[
            (self.kpi_a, "2026-02-01", "red"), (self.kpi_a, "2026-04-01", "red"), (self.kpi_b, "2026-04-01", "green"),
            (self.kpi_b, "2026-02-01", "amber"),
        ])
        card = self._item(self._data(), "cards", CARD)
        self.assertNotEqual(card["status"], "empty")
        self.assertRawIn(2, card)  # red
        self.assertRawIn(1, card)  # green / amber

    def test_red_alert_counts_done_values_in_period(self):
        # UX-002: "{n} KPIs in red" = done-run values, status red, date in period; same domain as the link
        self._kpi_run("2026-02-01", "2026-04-30", values=[
            (self.kpi_a, "2026-02-01", "red"), (self.kpi_a, "2026-04-01", "red"), (self.kpi_b, "2026-04-01", "green"),
        ])
        self._kpi_run("2025-12-01", "2025-12-31", values=[(self.kpi_a, "2025-12-01", "red")])  # out of the period
        self._kpi_run("2026-05-01", "2026-05-31", done=False, values=[(self.kpi_a, "2026-05-01", "red")])  # draft
        alert = self._item(self._data(), "alerts", ALERT)
        self.assertTrue(alert)
        self.assertEqual(alert["count"], 2)
        self.assertEqual(alert["severity"], "danger")
        self.assertEqual(alert["action"], VALUE_ACTION)
        self._assert_records_invariant(alert)

    def test_red_alert_only_in_period(self):
        # AD-003: the alert follows the period
        self._kpi_run("2026-02-01", "2026-04-30", values=[(self.kpi_a, "2026-02-01", "red"), (self.kpi_a, "2026-04-01", "red")])
        alert = self._item(self._data(kind="month", ref="2026-02-15"), "alerts", ALERT)
        self.assertEqual(alert["count"], 1)
        self._assert_records_invariant(alert)
        self.assertIsNone(self._item(self._data(kind="month", ref="2026-03-15"), "alerts", ALERT))

    def test_keys_absent_without_read(self):
        # AD-005 / UX-002 acceptance 7: no read on the KPI results -> no KPI 5, no red alert, no Performance card
        self._kpi_run("2026-02-01", "2026-04-30", scores=[("2026-04-01", 70.0, False)],
                      values=[(self.kpi_a, "2026-02-01", "red")])
        data = self._data(user=self.user)
        self.assertTrue(self._item(data, "kpis", KPI), "positive control")
        self.assertTrue(self._item(data, "alerts", ALERT), "positive control")
        self._revoke_read("cmc.kpi.scorecard.result", "cmc.kpi.value")
        data = self._data(user=self.user)
        self.assertIsNone(self._item(data, "kpis", KPI))
        self.assertIsNone(self._item(data, "alerts", ALERT))
        self.assertIsNone(self._item(data, "cards", CARD))
        self.assertNotIn("performance", [area["key"] for area in data["areas"]])

    def test_other_company_results_are_ignored(self):
        # AD-004: self.env.company only
        other = self.env["res.company"].create({"name": "DASH kpi other"})
        self._kpi_run("2026-02-01", "2026-04-30", company=other, scores=[("2026-04-01", 70.0, False)],
                      values=[(self.kpi_a, "2026-02-01", "red")])
        data = self._data()
        self.assertEqual(self._item(data, "cards", CARD)["status"], "empty")
        self.assertIsNone(self._item(data, "kpis", KPI))
        self.assertIsNone(self._item(data, "alerts", ALERT))
