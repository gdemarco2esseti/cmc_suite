"""DASH (controller dashboard) - contribution of ``cmc_direct_costing`` to ``cmc.cockpit.get_dashboard_data``.

Contract: scratchpad DASH-devlead.md section 2 (direct_costing), UX-002 (KPI 3, area "decision"), DASH-ADR AD-001..005.
- Cards (area decision): ``direct_costing.cvr`` (20; ``cmc.cvr.analysis`` bep_revenue / mos_ratio / has_breakeven,
  no state), ``direct_costing.direct`` (30; ``cmc.direct.costing`` total_mc_semigross, no state),
  ``direct_costing.decision`` (40; latest DONE ``cmc.decision``, verdict / differential, date desc, id desc).
  Actions cmc_direct_costing.action_cmc_cvr_analysis / .action_cmc_direct_costing / .action_cmc_decision.
- KPI ``direct_costing.result`` (slot "result", priority 20): ``operating_result`` of the latest direct costing IN the
  period; never any direct costing -> KPI not emitted; data but none in period -> raw None, value "—".
  (cmc_profitability, priority 10, wins the slot when it has a value: tested in cmc_profitability.)
- Keys omitted when the model is not readable; current company only.
"""

from freezegun import freeze_time as _freeze_time

from odoo import Command
from odoo.fields import Domain
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools.safe_eval import safe_eval

from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE

CVR = "direct_costing.cvr"
DIRECT = "direct_costing.direct"
DECISION = "direct_costing.decision"
KPI = "direct_costing.result"



class DashboardCase(TransactionCase):
    """Isolated company + a CMC user; helpers to read the payload by key."""
    # why: the running period is capped at today; a fixed clock keeps the 2026 fixtures full-calendar
    freeze_time = _freeze_time("2026-12-31 12:00:00")

    _login = "dash_dc_user"

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
class TestDirectCostingDashboard(DashboardCase):
    @classmethod
    def _direct(cls, date_from, date_to, company=None):
        return cls.env["cmc.direct.costing"].create({
            "name": "DASH DC %s" % date_to, "company_id": (company or cls.company).id, "mode": "evoluto",
            "date_from": date_from, "date_to": date_to, "common_fixed_cost": 1500.0,
            "line_ids": [
                Command.create({"name": "A", "revenue": 10000.0, "variable_cost": 6000.0, "specific_fixed_cost": 500.0}),
                Command.create({"name": "B", "revenue": 4000.0, "variable_cost": 1000.0, "specific_fixed_cost": 200.0}),
            ],
        })

    @classmethod
    def _cvr(cls, date_from, date_to):
        return cls.env["cmc.cvr.analysis"].create({
            "name": "DASH CVR", "company_id": cls.company.id, "mode": "simulazione", "date_from": date_from,
            "date_to": date_to, "price_unit": 10.0, "variable_unit": 6.0, "fixed_cost": 4000.0, "volume": 2000.0,
        })

    @classmethod
    def _decision(cls, day, done=True, **figures):
        decision = cls.env["cmc.decision"].create({"name": "DASH decision %s" % day, "company_id": cls.company.id, "date": day})
        if done:
            return cls._done(decision, **figures)
        return decision

    def test_empty_cards_without_data(self):
        # DASH 2 / UX-002: module installed, nothing computed -> three empty cards with their "Create" action
        self._decision("2026-05-10", done=False)
        data = self._data()
        for key, action in (
            (CVR, "cmc_direct_costing.action_cmc_cvr_analysis"),
            (DIRECT, "cmc_direct_costing.action_cmc_direct_costing"),
            (DECISION, "cmc_direct_costing.action_cmc_decision"),
        ):
            card = self._item(data, "cards", key)
            self.assertTrue(card, key)
            self.assertEqual(card["status"], "empty", key)
            self.assertEqual(card["area"], "decision", key)
            self.assertEqual(card["action"], action, key)
        self.assertFalse(self._keys(data, "alerts", "direct_costing."))

    def test_kpi_not_emitted_when_never_any_direct_costing(self):
        # UX-002 reconciliation: module installed but never any data -> no KPI tile (the card carries the empty state)
        self.assertIsNone(self._item(self._data(), "kpis", KPI))

    def test_cvr_card_figures(self):
        # DASH 2: CVR card = break-even revenue + margin of safety of the latest analysis (raw == stored fields)
        cvr = self._cvr("2026-01-01", "2026-12-31")
        card = self._item(self._data(), "cards", CVR)
        self.assertNotEqual(card["status"], "empty")
        self.assertRawIn(cvr.bep_revenue, card)
        self.assertRawIn(cvr.mos_ratio, card)
        self.assertEqual(card["status"], "up_to_date")

    def test_direct_card_figures_and_outdated_status(self):
        # DASH 2 / AD-003: latest direct costing; ending before the period end -> "outdated"
        direct = self._direct("2026-01-01", "2026-06-30")
        card = self._item(self._data(), "cards", DIRECT)
        self.assertAlmostEqual(direct.total_mc_semigross, 6300.0)
        self.assertRawIn(direct.total_mc_semigross, card)
        self.assertEqual(card["status"], "outdated")
        self.assertEqual(card["run_date_to"], "2026-06-30")

    def test_decision_card_reads_latest_done_decision(self):
        # AD-003: latest DONE decision; a newer draft is ignored
        self._decision("2026-02-10", differential=100.0, verdict="current")
        latest = self._decision("2026-05-10", differential=1200.0, verdict="alternative")
        self._decision("2026-06-01", done=False)
        card = self._item(self._data(), "cards", DECISION)
        self.assertNotEqual(card["status"], "empty")
        self.assertRawIn(latest.differential, card)

    def test_result_kpi_in_slot_result(self):
        # UX-002 KPI 3 (fallback source): operating result of the latest direct costing in the period
        direct = self._direct("2026-02-01", "2026-04-30")
        kpi = self._item(self._data(), "kpis", KPI)
        self.assertTrue(kpi)
        self.assertEqual(kpi["slot"], "result")
        self.assertAlmostEqual(direct.operating_result, 4800.0)
        self.assertAlmostEqual(kpi["raw"], direct.operating_result, places=2)
        self.assertIsInstance(kpi["value"], str)
        self.assertTrue(kpi["value"])

    def test_result_kpi_dash_when_no_direct_costing_in_period(self):
        # UX-002: data exists but none in the period -> "—" with raw None, never 0
        self._direct("2025-02-01", "2025-04-30")
        kpi = self._item(self._data(), "kpis", KPI)
        self.assertTrue(kpi, "data exists: the KPI is emitted with an empty value")
        self.assertIsNone(kpi["raw"])
        self.assertEqual(kpi["value"], "—")

    def test_direct_keys_absent_without_read(self):
        # AD-005 / SC-082: no read on cmc.direct.costing -> its card and KPI omitted, the other cards stay
        self._direct("2026-02-01", "2026-04-30")
        self._cvr("2026-01-01", "2026-12-31")
        data = self._data(user=self.user)
        self.assertTrue(self._item(data, "kpis", KPI), "positive control: the user sees the KPI")
        self.assertTrue(self._item(data, "cards", DIRECT))
        self._revoke_read("cmc.direct.costing")
        data = self._data(user=self.user)
        self.assertIsNone(self._item(data, "kpis", KPI))
        self.assertIsNone(self._item(data, "cards", DIRECT))
        self.assertTrue(self._item(data, "cards", CVR))

    def test_other_company_data_is_ignored(self):
        # AD-004: self.env.company only
        other = self.env["res.company"].create({"name": "DASH dc other"})
        self._direct("2026-02-01", "2026-04-30", company=other)
        data = self._data()
        self.assertEqual(self._item(data, "cards", DIRECT)["status"], "empty")
        self.assertIsNone(self._item(data, "kpis", KPI))
