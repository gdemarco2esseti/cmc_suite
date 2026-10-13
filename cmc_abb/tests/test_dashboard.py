"""DASH (controller dashboard) - contribution of ``cmc_abb`` to ``cmc.cockpit.get_dashboard_data``.

Contract: scratchpad DASH-devlead.md section 2 (abb) + F3, UX-002 (area "planning", ABB card), DASH-ADR AD-003/005.
- Card ``abb.run`` (planning, 20): latest DONE ``cmc.abb.run`` (id desc) with ``baseline_run_id = False`` (what-if
  scenarios are not the budget), figures total_budgeted_cost / total_assigned_cost / total_unused_cost; action
  ``cmc_abb.action_cmc_abb_run``. ABB runs carry no dates: the card is always "up_to_date" (shared helper,
  run_date_to None), whatever the period. No run -> "empty". No alert, no KPI.
"""

from freezegun import freeze_time as _freeze_time

from odoo import Command
from odoo.fields import Domain
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools.safe_eval import safe_eval

from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE

CARD = "abb.run"



class DashboardCase(TransactionCase):
    """Isolated company + a CMC user; helpers to read the payload by key."""
    # why: the running period is capped at today; a fixed clock keeps the 2026 fixtures full-calendar
    freeze_time = _freeze_time("2026-12-31 12:00:00")

    _login = "dash_abb_user"

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
class TestAbbDashboard(DashboardCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.version = cls.env["cmc.plan.version"].create({
            "name": "DASH ABB volumes", "company_id": cls.company.id, "date_from": "2026-01-01", "date_to": "2026-12-31",
        })
        cls.measure = cls.env["cmc.cost.driver"].create({
            "name": "DASH output", "code": "DASHOUT", "measure_kind": "statistical", "company_id": cls.company.id,
        })

    @classmethod
    def _abb(cls, done=True, baseline=None, version=None, measure=None, **figures):
        version = version or cls.version
        abb = cls.env["cmc.abb.run"].create({
            "name": "DASH ABB", "company_id": version.company_id.id, "source_version_id": version.id,
            "output_measure_id": (measure or cls.measure).id, "baseline_run_id": baseline.id if baseline else False,
        })
        return cls._done(abb, **figures) if done else abb

    def test_empty_card_without_done_run(self):
        # DASH 2 / UX-002: no computed ABB run -> empty card with the "Create" action
        self._abb(done=False, total_budgeted_cost=1.0)
        data = self._data()
        card = self._item(data, "cards", CARD)
        self.assertTrue(card)
        self.assertEqual(card["status"], "empty")
        self.assertEqual(card["area"], "planning")
        self.assertEqual(card["action"], "cmc_abb.action_cmc_abb_run")
        self.assertFalse(self._keys(data, "alerts", "abb."))
        self.assertFalse(self._keys(data, "kpis", "abb."))

    def test_card_reads_latest_done_run_figures(self):
        # AD-003: latest DONE run (id desc); a newer draft is ignored
        self._abb(total_budgeted_cost=100.0, total_assigned_cost=80.0, total_unused_cost=20.0)
        latest = self._abb(total_budgeted_cost=5000.0, total_assigned_cost=4200.0, total_unused_cost=800.0)
        self._abb(done=False)
        card = self._item(self._data(), "cards", CARD)
        self.assertRawIn(latest.total_budgeted_cost, card)
        self.assertRawIn(latest.total_assigned_cost, card)
        self.assertRawIn(latest.total_unused_cost, card)

    def test_what_if_run_is_ignored(self):
        # F3: a what-if run (baseline_run_id set) is not the budget, even when newer and computed
        base = self._abb(total_budgeted_cost=5000.0, total_assigned_cost=4200.0, total_unused_cost=800.0)
        self._abb(baseline=base, total_budgeted_cost=9999.0, total_assigned_cost=9000.0, total_unused_cost=999.0)
        card = self._item(self._data(), "cards", CARD)
        self.assertRawIn(5000.0, card)
        self.assertNotIn(9999.0, [f["raw"] for f in card["figures"]])

    def test_undated_run_always_up_to_date(self):
        # DASH 1 run_status: undated artefacts (run_date_to None) are up to date in any period
        self._abb(total_budgeted_cost=5000.0)
        for kind, ref in (("year", "2026-06-15"), ("month", "2030-01-15"), ("all", None)):
            card = self._item(self._data(kind=kind, ref=ref), "cards", CARD)
            self.assertEqual(card["status"], "up_to_date", kind)
            self.assertIsNone(card["run_date_to"])

    def test_card_absent_without_read(self):
        # AD-005 / SC-082: card omitted (not empty) when ABB runs are not readable
        self._abb(total_budgeted_cost=5000.0)
        self.assertTrue(self._item(self._data(user=self.user), "cards", CARD), "positive control")
        self._revoke_read("cmc.abb.run")
        self.assertIsNone(self._item(self._data(user=self.user), "cards", CARD))

    def test_other_company_run_is_ignored(self):
        # AD-004: self.env.company only
        other = self.env["res.company"].create({"name": "DASH abb other"})
        version = self.env["cmc.plan.version"].create({
            "name": "DASH ABB other", "company_id": other.id, "date_from": "2026-01-01", "date_to": "2026-12-31",
        })
        measure = self.env["cmc.cost.driver"].create({
            "name": "DASH output other", "code": "DASHOUTO", "measure_kind": "statistical", "company_id": other.id,
        })
        self._abb(version=version, measure=measure, total_budgeted_cost=5000.0)
        self.assertEqual(self._item(self._data(), "cards", CARD)["status"], "empty")
