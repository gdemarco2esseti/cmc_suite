"""DASH (controller dashboard) - contribution of ``cmc_target_lcc`` to ``cmc.cockpit.get_dashboard_data``.

Contract: scratchpad DASH-devlead.md section 2 (target_lcc), UX-002 (area "planning" Target/LCC card, "products above
target cost" row), DASH-ADR AD-002/003/005.
- Card ``target_lcc.target`` (planning, 40): latest DONE ``cmc.target.cost`` (date desc, id desc) cost_gap, + latest
  DONE ``cmc.lcc.model`` (id desc) lc_profit_npv; card date_to = target ``date`` (shared status helper); action
  ``cmc_target_lcc.action_cmc_target_cost``; nothing computed -> "empty".
- Alert ``target_lcc.above_target`` (info, scope records): done target costs, target_met False, date in period,
  company; invariant search_count(action domain AND alert domain) == count.
"""

from freezegun import freeze_time as _freeze_time

from odoo import Command
from odoo.fields import Domain
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools.safe_eval import safe_eval

from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE

CARD = "target_lcc.target"
ALERT = "target_lcc.above_target"
TARGET_ACTION = "cmc_target_lcc.action_cmc_target_cost"



class DashboardCase(TransactionCase):
    """Isolated company + a CMC user; helpers to read the payload by key."""
    # why: the running period is capped at today; a fixed clock keeps the 2026 fixtures full-calendar
    freeze_time = _freeze_time("2026-12-31 12:00:00")

    _login = "dash_tlc_user"

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
class TestTargetLccDashboard(DashboardCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product = cls.env["product.product"].create({"name": "DASH target product", "company_id": cls.company.id})
        cls.unit = cls.env["cmc.cost.driver"].create({
            "name": "DASH unit", "code": "DASHUNIT", "measure_kind": "statistical", "company_id": cls.company.id,
        })

    @classmethod
    def _target(cls, day, done=True, product=None, **figures):
        product = product or cls.product
        target = cls.env["cmc.target.cost"].create({
            "name": "DASH target %s" % day, "company_id": product.company_id.id, "product_id": product.id,
            "unit_driver_id": cls.unit.id, "date": day,
        })
        return cls._done(target, **figures) if done else target

    @classmethod
    def _lcc(cls, done=True, **figures):
        model = cls.env["cmc.lcc.model"].create({"name": "DASH LCC", "company_id": cls.company.id, "product_id": cls.product.id})
        return cls._done(model, **figures) if done else model

    def test_empty_card_without_done_run(self):
        # DASH 2 / UX-002: nothing computed -> empty card with the "Create" action, no alert
        self._target("2026-05-01", done=False, target_met=False, cost_gap=10.0)
        data = self._data()
        card = self._item(data, "cards", CARD)
        self.assertTrue(card)
        self.assertEqual(card["status"], "empty")
        self.assertEqual(card["area"], "planning")
        self.assertEqual(card["action"], TARGET_ACTION)
        self.assertFalse(self._keys(data, "alerts", "target_lcc."))
        self.assertFalse(self._keys(data, "kpis", "target_lcc."))

    def test_card_reads_latest_done_target_and_lcc(self):
        # AD-003: latest DONE target cost (date desc) and latest DONE LCC model; drafts ignored
        self._target("2026-02-01", cost_gap=50.0, target_met=False)
        latest = self._target("2026-05-01", cost_gap=125.0, target_met=False)
        self._target("2026-09-01", done=False, cost_gap=-999.0)
        lcc = self._lcc(lc_profit_npv=42000.0)
        self._lcc(done=False, lc_profit_npv=-1.0)
        card = self._item(self._data(), "cards", CARD)
        self.assertRawIn(latest.cost_gap, card)
        self.assertRawIn(lcc.lc_profit_npv, card)
        self.assertEqual(card["run_date_to"], "2026-05-01")

    def test_card_status_follows_target_date(self):
        # DASH 1 run_status with date_to = target date: covers the period end -> up to date, else outdated
        self._target("2026-05-31", cost_gap=125.0)
        self.assertEqual(self._item(self._data(kind="month", ref="2026-05-15"), "cards", CARD)["status"], "up_to_date")
        self.assertEqual(self._item(self._data(), "cards", CARD)["status"], "outdated")

    def test_above_target_alert_counts_done_targets_in_period(self):
        # UX-002: "{n} products above target cost" = done, target not met, date in period; same domain as the link
        self._target("2026-02-01", target_met=False, cost_gap=10.0)
        self._target("2026-04-01", target_met=False, cost_gap=20.0)
        self._target("2026-05-01", target_met=True, cost_gap=-5.0)
        self._target("2025-11-01", target_met=False, cost_gap=30.0)  # out of the period
        self._target("2026-06-01", done=False, target_met=False)  # draft: never an alert
        alert = self._item(self._data(), "alerts", ALERT)
        self.assertTrue(alert)
        self.assertEqual(alert["count"], 2)
        self.assertEqual(alert["severity"], "info")
        self.assertEqual(alert["action"], TARGET_ACTION)
        self._assert_records_invariant(alert)

    def test_above_target_alert_only_in_period(self):
        # AD-003: the alert follows the period
        self._target("2026-02-10", target_met=False, cost_gap=10.0)
        self.assertEqual(self._item(self._data(kind="month", ref="2026-02-15"), "alerts", ALERT)["count"], 1)
        self.assertIsNone(self._item(self._data(kind="month", ref="2026-03-15"), "alerts", ALERT))

    def test_no_alert_when_targets_are_met(self):
        # UX-002: an alert only when count > 0
        self._target("2026-02-01", target_met=True, cost_gap=-5.0)
        self.assertIsNone(self._item(self._data(), "alerts", ALERT))

    def test_keys_absent_without_read(self):
        # AD-005 / SC-082: target costs not readable -> card and alert omitted, no error
        self._target("2026-02-01", target_met=False, cost_gap=10.0)
        data = self._data(user=self.user)
        self.assertTrue(self._item(data, "cards", CARD), "positive control")
        self.assertTrue(self._item(data, "alerts", ALERT), "positive control")
        self._revoke_read("cmc.target.cost", "cmc.lcc.model")
        data = self._data(user=self.user)
        self.assertIsNone(self._item(data, "cards", CARD))
        self.assertIsNone(self._item(data, "alerts", ALERT))

    def test_other_company_targets_are_ignored(self):
        # AD-004: self.env.company only
        other = self.env["res.company"].create({"name": "DASH target other"})
        product = self.env["product.product"].create({"name": "DASH other product", "company_id": other.id})
        self._target("2026-02-01", product=product, target_met=False, cost_gap=10.0)
        data = self._data()
        self.assertEqual(self._item(data, "cards", CARD)["status"], "empty")
        self.assertIsNone(self._item(data, "alerts", ALERT))
