"""DASH (controller dashboard) - contribution of ``cmc_job_costing`` to ``cmc.cockpit.get_dashboard_data``.

Contract: scratchpad DASH-devlead.md section 2 (job_costing), UX-002 ("Da verificare" job rows), DASH-ADR AD-001..005.
- Card ``job_costing.jobs`` (area costing, seq 70): latest DONE ``cmc.job.cost.run`` (date_to desc, id desc), figures
  ``wip_total`` / ``completed_total``; action ``cmc_job_costing.action_cmc_job_cost_run``; no run -> status "empty".
- Alerts on ``cmc_job_costing.action_cmc_evm_run``, scope records, counted on ``cmc.evm.run`` (F5: status reports):
  ``job_costing.out_of_control`` (danger): done, status_date in period, company, out_of_control_count > 0;
  ``job_costing.cpi_below_one`` (warning): same + cpi < 1 + out_of_control_count = 0 (mutually exclusive).
  Invariant: search_count(action domain AND alert domain) == count.
- No KPI. Keys omitted (not 0) when the user cannot read the model; current company only.

Fixture figures are written on the run header under the CMC_RUN_STATE sentinel (the engine is not under test here:
the dashboard reads the stored header outputs).
"""

from freezegun import freeze_time as _freeze_time

from odoo import Command
from odoo.fields import Domain
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools.safe_eval import safe_eval

from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE

CARD = "job_costing.jobs"
OOC = "job_costing.out_of_control"
CPI = "job_costing.cpi_below_one"
CARD_ACTION = "cmc_job_costing.action_cmc_job_cost_run"
EVM_ACTION = "cmc_job_costing.action_cmc_evm_run"


class DashboardCase(TransactionCase):
    """Isolated company + a CMC user; helpers to read the payload by key."""
    # why: the running period is capped at today; a fixed clock keeps the 2026 fixtures full-calendar
    freeze_time = _freeze_time("2026-12-31 12:00:00")

    _login = "dash_job_user"

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
class TestJobCostingDashboard(DashboardCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.project = cls.env["project.project"].create({"name": "DASH job", "company_id": cls.company.id})
        cls.baseline = cls.env["cmc.plan.version"].create({
            "name": "DASH baseline", "company_id": cls.company.id, "version_type": "baseline",
            "project_id": cls.project.id, "date_from": "2025-01-01", "date_to": "2027-12-31",
        })
        # the job run the EVM reports point to; draft, so it never feeds the card
        cls.job_link = cls._job_run("2026-01-01", "2026-01-31", done=False)

    @classmethod
    def _job_run(cls, date_from, date_to, done=True, company=None, **figures):
        job_run = cls.env["cmc.job.cost.run"].create({
            "name": "DASH job run %s" % date_to, "company_id": (company or cls.company).id,
            "date_from": date_from, "date_to": date_to,
        })
        return cls._done(job_run, **figures) if done else job_run

    def _evm(self, status_date, ooc, cpi, done=True, job_run=None):
        evm = self.env["cmc.evm.run"].create({
            "name": "DASH EVM %s" % status_date, "company_id": self.company.id, "project_id": self.project.id,
            "baseline_id": self.baseline.id, "job_run_id": (job_run or self.job_link).id, "status_date": status_date,
        })
        figures = {"out_of_control_count": ooc, "cpi": cpi}
        if done:
            return self._done(evm, **figures)
        evm.with_context(cmc_run_state=CMC_RUN_STATE).write(figures)
        return evm

    def test_empty_card_without_done_run(self):
        # DASH 2 / UX-002: installed module without a computed run -> empty card with the "Create" action, no alert
        self._job_run("2026-01-01", "2026-12-31", done=False, wip_total=999.0)
        data = self._data()
        card = self._item(data, "cards", CARD)
        self.assertTrue(card, "the job costing card is always present when readable")
        self.assertEqual(card["status"], "empty")
        self.assertEqual(card["area"], "costing")
        self.assertEqual(card["action"], CARD_ACTION)
        self.assertFalse(self._keys(data, "alerts", "job_costing."))
        self.assertFalse(self._keys(data, "kpis", "job_costing."))

    def test_card_reads_latest_done_run_figures(self):
        # AD-003: the card shows the latest DONE run of the company; a newer draft is ignored
        self._job_run("2026-01-01", "2026-06-30", wip_total=100.0, completed_total=50.0)
        latest = self._job_run("2026-01-01", "2026-12-31", wip_total=1200.0, completed_total=800.0)
        self._job_run("2026-01-01", "2027-03-31", done=False)
        card = self._item(self._data(), "cards", CARD)
        self.assertAlmostEqual(latest.wip_total, 1200.0)
        self.assertRawIn(latest.wip_total, card)
        self.assertRawIn(latest.completed_total, card)
        self.assertEqual(card["status"], "up_to_date")
        self.assertEqual(card["run_date_to"], "2026-12-31")
        self.assertTrue(card["in_period"])
        money = next(f for f in card["figures"] if f["raw"] == latest.wip_total)
        self.assertIsInstance(money["value"], str)
        self.assertTrue(money["value"])

    def test_card_outdated_when_run_ends_before_period_end(self):
        # UX-002 status chip: latest done run ending before the period end -> "outdated"
        self._job_run("2026-01-01", "2026-03-31", wip_total=10.0)
        card = self._item(self._data(), "cards", CARD)
        self.assertEqual(card["status"], "outdated")

    def test_out_of_control_alert_counts_done_reports_in_period(self):
        # F5: "{n} EVM status reports out of control": done, in period, current company, out_of_control_count > 0
        self._evm("2026-03-31", ooc=2, cpi=0.9)
        self._evm("2026-05-31", ooc=1, cpi=1.1)
        self._evm("2025-12-31", ooc=1, cpi=0.8)  # out of the period
        self._evm("2026-04-30", ooc=3, cpi=0.7, done=False)  # draft: never an alert
        alert = self._item(self._data(), "alerts", OOC)
        self.assertTrue(alert)
        self.assertEqual(alert["count"], 2)
        self.assertEqual(alert["severity"], "danger")
        self.assertEqual(alert["action"], EVM_ACTION)
        self._assert_records_invariant(alert)

    def test_cpi_below_one_alert_excludes_out_of_control_reports(self):
        # UX-002: CPI < 1 counted only on reports not already out of control (mutually exclusive rows)
        self._evm("2026-03-31", ooc=2, cpi=0.9)  # out of control: counted there only
        self._evm("2026-06-30", ooc=0, cpi=0.95)
        self._evm("2026-02-28", ooc=0, cpi=0.5)
        self._evm("2026-04-30", ooc=0, cpi=1.05)
        self._evm("2025-11-30", ooc=0, cpi=0.5)  # out of the period
        data = self._data()
        alert = self._item(data, "alerts", CPI)
        self.assertTrue(alert)
        self.assertEqual(alert["count"], 2)
        self.assertEqual(alert["severity"], "warning")
        self.assertEqual(alert["action"], EVM_ACTION)
        self._assert_records_invariant(alert)
        self.assertEqual(self._item(data, "alerts", OOC)["count"], 1)

    def test_alerts_follow_the_period(self):
        # AD-003: flow alerts are period-filtered; a month without reports has none
        self._evm("2026-03-31", ooc=2, cpi=0.9)
        self.assertTrue(self._item(self._data(kind="month", ref="2026-03-10"), "alerts", OOC))
        self.assertFalse(self._keys(self._data(kind="month", ref="2026-04-10"), "alerts", "job_costing."))

    def test_no_alert_when_reports_are_in_control(self):
        # UX-002: an alert only when count > 0
        self._evm("2026-03-31", ooc=0, cpi=1.2)
        self.assertFalse(self._keys(self._data(), "alerts", "job_costing."))

    def test_alerts_absent_without_read_on_evm_runs(self):
        # AD-005 / SC-082: a role that cannot read EVM runs gets no EVM alert (no error), the job card stays
        self._evm("2026-03-31", ooc=2, cpi=0.9)
        self.assertTrue(self._item(self._data(user=self.user), "alerts", OOC), "positive control: the user sees it")
        self._revoke_read("cmc.evm.run")
        data = self._data(user=self.user)
        self.assertFalse(self._keys(data, "alerts", "job_costing."))
        self.assertTrue(self._item(data, "cards", CARD))

    def test_card_absent_without_read_on_job_runs(self):
        # AD-005: card omitted (not empty, not 0) when the job cost run is not readable
        self._job_run("2026-01-01", "2026-12-31", wip_total=1.0)
        self.assertTrue(self._item(self._data(user=self.user), "cards", CARD), "positive control")
        self._revoke_read("cmc.job.cost.run")
        self.assertIsNone(self._item(self._data(user=self.user), "cards", CARD))

    def test_other_company_data_is_ignored(self):
        # AD-004: self.env.company only
        other = self.env["res.company"].create({"name": "DASH job other"})
        self._job_run("2026-01-01", "2026-12-31", company=other, wip_total=5000.0)
        data = self._data()
        self.assertEqual(self._item(data, "cards", CARD)["status"], "empty")
        self.assertFalse(self._keys(data, "alerts", "job_costing."))
