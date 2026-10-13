"""DASH (controller dashboard) - contribution of ``cmc_scenario`` to ``cmc.cockpit.get_dashboard_data``.

Contract: scratchpad DASH-devlead.md section 2 (scenario), UX-002 (area "planning": delta profit of the latest
comparison, P50), DASH-ADR AD-003/005.
- Card ``scenario.scenarios`` (planning, 30): latest DONE ``cmc.scenario.comparison`` (delta_profit) and latest DONE
  ``cmc.scenario.simulation`` (p50_output), id desc; action ``cmc_scenario.action_cmc_scenario_comparison``.
  Undated artefacts: always "up_to_date". Nothing computed -> "empty". No alert, no KPI.
"""

from freezegun import freeze_time as _freeze_time

from odoo import Command
from odoo.fields import Domain
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools.safe_eval import safe_eval

from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE

CARD = "scenario.scenarios"



class DashboardCase(TransactionCase):
    """Isolated company + a CMC user; helpers to read the payload by key."""
    # why: the running period is capped at today; a fixed clock keeps the 2026 fixtures full-calendar
    freeze_time = _freeze_time("2026-12-31 12:00:00")

    _login = "dash_scenario_user"

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
class TestScenarioDashboard(DashboardCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.version = cls.env["cmc.plan.version"].create({
            "name": "DASH scenario", "company_id": cls.company.id, "version_type": "simulation",
            "date_from": "2026-01-01", "date_to": "2026-12-31",
        })

    @classmethod
    def _comparison(cls, done=True, version=None, **figures):
        version = version or cls.version
        comparison = cls.env["cmc.scenario.comparison"].create({
            "name": "DASH comparison", "company_id": version.company_id.id, "evaluator_key": "dash", "scenario_id": version.id,
        })
        return cls._done(comparison, **figures) if done else comparison

    @classmethod
    def _simulation(cls, done=True, **figures):
        simulation = cls.env["cmc.scenario.simulation"].create({
            "name": "DASH simulation", "company_id": cls.company.id, "evaluator_key": "dash", "version_id": cls.version.id,
        })
        return cls._done(simulation, **figures) if done else simulation

    def test_empty_card_without_done_runs(self):
        # DASH 2 / UX-002: nothing computed -> empty card with the "Create" action
        self._comparison(done=False, delta_profit=1.0)
        self._simulation(done=False, p50_output=1.0)
        data = self._data()
        card = self._item(data, "cards", CARD)
        self.assertTrue(card)
        self.assertEqual(card["status"], "empty")
        self.assertEqual(card["area"], "planning")
        self.assertEqual(card["action"], "cmc_scenario.action_cmc_scenario_comparison")
        self.assertFalse(self._keys(data, "alerts", "scenario."))
        self.assertFalse(self._keys(data, "kpis", "scenario."))

    def test_card_reads_latest_done_comparison_and_simulation(self):
        # DASH 2: delta profit of the latest done comparison + P50 of the latest done simulation; drafts ignored
        self._comparison(delta_profit=100.0)
        latest = self._comparison(delta_profit=2500.0)
        self._comparison(done=False, delta_profit=-7777.0)
        simulation = self._simulation(p50_output=7000.0)
        self._simulation(done=False, p50_output=-1.0)
        card = self._item(self._data(), "cards", CARD)
        self.assertRawIn(latest.delta_profit, card)
        self.assertRawIn(simulation.p50_output, card)
        raws = [f["raw"] for f in card["figures"]]
        self.assertNotIn(-7777.0, raws)
        self.assertNotIn(-1.0, raws)

    def test_undated_card_always_up_to_date(self):
        # DASH 1 run_status: undated artefacts are up to date in any period
        self._comparison(delta_profit=2500.0)
        self._simulation(p50_output=7000.0)
        for kind, ref in (("year", "2026-06-15"), ("month", "2030-01-15"), ("all", None)):
            card = self._item(self._data(kind=kind, ref=ref), "cards", CARD)
            self.assertEqual(card["status"], "up_to_date", kind)

    def test_card_absent_without_read(self):
        # AD-005 / SC-082: card omitted when the scenario runs are not readable
        self._comparison(delta_profit=2500.0)
        self.assertTrue(self._item(self._data(user=self.user), "cards", CARD), "positive control")
        self._revoke_read("cmc.scenario.comparison", "cmc.scenario.simulation")
        self.assertIsNone(self._item(self._data(user=self.user), "cards", CARD))

    def test_other_company_run_is_ignored(self):
        # AD-004: self.env.company only
        other = self.env["res.company"].create({"name": "DASH scenario other"})
        version = self.env["cmc.plan.version"].create({
            "name": "DASH scenario other", "company_id": other.id, "version_type": "simulation",
            "date_from": "2026-01-01", "date_to": "2026-12-31",
        })
        self._comparison(version=version, delta_profit=2500.0)
        self.assertEqual(self._item(self._data(), "cards", CARD)["status"], "empty")
