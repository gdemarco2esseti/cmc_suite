"""DASH (controller dashboard) - contribution of ``cmc_fin_analysis`` to ``cmc.cockpit.get_dashboard_data``.

Contract: scratchpad DASH-devlead.md section 2 (fin_analysis) + F2, UX-002 (area "financial", "financial ratios on
alert" row), DASH-ADR AD-002/003/005.
- Card ``fin_analysis.analysis`` (financial, 10): latest DONE ``cmc.fin.analysis`` (year_to desc, id desc) +
  ``_read_group`` of ``cmc.fin.ratio.value`` (run_id, year = year_to) by status; card date_to = 31 Dec year_to;
  action ``cmc_fin_analysis.action_cmc_fin_analysis``; nothing computed -> "empty".
- Alert ``fin_analysis.ratio_alert`` (warning, scope records, action ``cmc_fin_analysis.action_cmc_fin_ratio_value``):
  ratio values of the latest done analysis, year = year_to, status alert; invariant
  search_count(action domain AND alert domain) == count.
Ratio values are result rows: written under the CMC_RUN_STATE sentinel, as the engine does.
"""

from freezegun import freeze_time as _freeze_time

from odoo import Command
from odoo.fields import Domain
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools.safe_eval import safe_eval

from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE

CARD = "fin_analysis.analysis"
ALERT = "fin_analysis.ratio_alert"



class DashboardCase(TransactionCase):
    """Isolated company + a CMC user; helpers to read the payload by key."""
    # why: the running period is capped at today; a fixed clock keeps the 2026 fixtures full-calendar
    freeze_time = _freeze_time("2026-12-31 12:00:00")

    _login = "dash_fin_user"

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
class TestFinAnalysisDashboard(DashboardCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ratios = cls.env["cmc.fin.ratio"].search([], limit=3)
        assert len(cls.ratios) == 3, "the shared ratio catalogue (cmc_fin_analysis data) is expected"

    @classmethod
    def _analysis(cls, year_to, done=True, values=(), company=None):
        """An analysis year_to-2..year_to with ratio values [(ratio index, year, status)]."""
        analysis = cls.env["cmc.fin.analysis"].create({
            "name": "DASH analysis %s" % year_to, "company_id": (company or cls.company).id,
            "year_from": year_to - 2, "year_to": year_to,
        })
        engine = cls.env(context=dict(cls.env.context, cmc_run_state=CMC_RUN_STATE))
        engine["cmc.fin.ratio.value"].create([{
            "run_id": analysis.id, "ratio_id": cls.ratios[index].id, "code": cls.ratios[index].code, "year": year,
            "status": status, "value": 1.0,
        } for index, year, status in values])
        return cls._done(analysis) if done else analysis

    def test_empty_card_without_done_analysis(self):
        # DASH 2 / UX-002: nothing computed -> empty card with the "Create" action, no alert
        self._analysis(2026, done=False, values=[(0, 2026, "alert")])
        data = self._data()
        card = self._item(data, "cards", CARD)
        self.assertTrue(card)
        self.assertEqual(card["status"], "empty")
        self.assertEqual(card["area"], "financial")
        self.assertEqual(card["action"], "cmc_fin_analysis.action_cmc_fin_analysis")
        self.assertIsNone(self._item(data, "alerts", ALERT))
        self.assertFalse(self._keys(data, "kpis", "fin_analysis."))

    def test_card_reads_latest_done_analysis(self):
        # AD-003 / F2: latest DONE analysis by year_to; ratios of year_to by status; a newer draft is ignored
        self._analysis(2025, values=[(0, 2025, "ok")])
        self._analysis(2026, values=[(0, 2026, "alert"), (1, 2026, "alert"), (2, 2026, "ok"), (0, 2025, "alert")])
        self._analysis(2027, done=False, values=[(0, 2027, "alert")])
        card = self._item(self._data(), "cards", CARD)
        self.assertEqual(card["run_date_to"], "2026-12-31")
        self.assertEqual(card["status"], "up_to_date")
        self.assertRawIn(2, card)  # ratios on alert
        self.assertRawIn(1, card)  # ratios ok

    def test_card_outdated_when_year_ends_before_period_end(self):
        # DASH 1 run_status with date_to = 31 Dec year_to
        self._analysis(2025, values=[(0, 2025, "ok")])
        self.assertEqual(self._item(self._data(), "cards", CARD)["status"], "outdated")

    def test_ratio_alert_counts_alert_ratios_of_latest_analysis(self):
        # UX-002: "{n} financial ratios on alert" = latest done analysis, year_to, status alert; same domain as the link
        self._analysis(2025, values=[(0, 2025, "alert"), (1, 2025, "alert"), (2, 2025, "alert")])
        self._analysis(2026, values=[(0, 2026, "alert"), (1, 2026, "alert"), (2, 2026, "ok"), (0, 2025, "alert")])
        self._analysis(2027, done=False, values=[(0, 2027, "alert")])
        alert = self._item(self._data(), "alerts", ALERT)
        self.assertTrue(alert)
        self.assertEqual(alert["count"], 2)
        self.assertEqual(alert["severity"], "warning")
        self.assertEqual(alert["action"], "cmc_fin_analysis.action_cmc_fin_ratio_value")
        self._assert_records_invariant(alert)

    def test_no_alert_when_ratios_ok(self):
        # UX-002: an alert only when count > 0
        self._analysis(2026, values=[(0, 2026, "ok"), (1, 2025, "alert")])
        self.assertIsNone(self._item(self._data(), "alerts", ALERT))

    def test_keys_absent_without_read(self):
        # AD-005 / SC-082: analyses not readable -> card and alert omitted, no error
        self._analysis(2026, values=[(0, 2026, "alert")])
        data = self._data(user=self.user)
        self.assertTrue(self._item(data, "cards", CARD), "positive control")
        self.assertTrue(self._item(data, "alerts", ALERT), "positive control")
        self._revoke_read("cmc.fin.analysis", "cmc.fin.ratio.value")
        data = self._data(user=self.user)
        self.assertIsNone(self._item(data, "cards", CARD))
        self.assertIsNone(self._item(data, "alerts", ALERT))
        self.assertNotIn("financial", [area["key"] for area in data["areas"]])

    def test_other_company_analysis_is_ignored(self):
        # AD-004: self.env.company only
        other = self.env["res.company"].create({"name": "DASH fin other"})
        self._analysis(2026, company=other, values=[(0, 2026, "alert")])
        data = self._data()
        self.assertEqual(self._item(data, "cards", CARD)["status"], "empty")
        self.assertIsNone(self._item(data, "alerts", ALERT))
