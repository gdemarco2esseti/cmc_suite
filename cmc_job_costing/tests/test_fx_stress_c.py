"""Stress-test fixes 2026-10, corsia C, on ``cmc_job_costing``: FX-012, FX-013, FX-017.

CONTRACT
- FX-012 (S-curve):
  * forecast months (after the status month) keep ``ev_cumulative`` / ``ac_cumulative`` EMPTY (NULL in the
    database, never 0): pinned in ``test_evm_run.test_prj04_s_curve_cumulative_pv_ev_ac``;
  * ``cmc.evm.curve`` ``pv_cumulative`` / ``ev_cumulative`` / ``ac_cumulative`` have ``aggregator = "max"``
    (a cumulative figure is never summed across rows);
  * the menu action ``cmc_job_costing.action_cmc_evm_curve_run`` opens with a default filter
    (``search_default_<name>`` in its context) that is a ``<filter>`` of the curve search view and keeps ONE
    status report per project (its domain selects the rows of a single run - e.g. the latest computed one -
    or its context groups by ``run_id``), so the curves of several runs are not summed.
- FX-013: computing a job cost run where SEVERAL jobs have timesheet hours without a cost center raises ONE
  ``UserError`` whose message names EVERY such job with its hours, and says where to set the center (the
  analytic plan of the cost centers, here "WP05 centers", on the project).
- FX-017: the ``state`` selection labels of ``cmc.job.cost.run`` and ``cmc.evm.run`` are translated in
  it_IT: draft -> "Bozza", done -> "Calcolato".
"""

from datetime import date

from lxml import etree

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests.common import tagged
from odoo.tools.safe_eval import safe_eval

from .evm_common import FEB_END, EvmCase


@tagged("post_install", "-at_install")
class TestFxStressJob(EvmCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        su = cls.su
        # FX-013: two jobs whose project carries NO cost center: their timesheets have no center
        cls.aa_d = cls._job_analytic(su, "FX013 AA-D", cls.company_a)
        cls.aa_e = cls._job_analytic(su, "FX013 AA-E", cls.company_a)
        cls.job_d, cls.job_e = su["project.project"].create([
            {"name": name, "account_id": aa.id, "company_id": cls.company_a.id, "allow_timesheets": True}
            for name, aa in (("Commessa Delta", cls.aa_d), ("Commessa Epsilon", cls.aa_e))
        ])
        su["cmc.cost.object"]._cmc_ensure_objects(cls.job_d | cls.job_e, company=cls.company_a, object_type="project")
        cls._ts(su, cls.job_d, cls.anna, 13)
        cls._ts(su, cls.job_e, cls.bruno, 27)

    # ---- FX-012 -----------------------------------------------------------

    def test_fx012_cumulative_fields_aggregate_with_max(self):
        # FX-012: a cumulative figure is never summed across rows (several runs, several months)
        fields_ = self.env["cmc.evm.curve"]._fields
        for name in ("pv_cumulative", "ev_cumulative", "ac_cumulative"):
            with self.subTest(field=name):
                self.assertEqual(fields_[name].aggregator, "max")

    def test_fx012_menu_action_filters_one_run(self):
        # FX-012: the S-curve menu opens on one status report per job, never the sum of every report
        baseline = self._baseline()
        self._evm(baseline, self.job_feb, FEB_END, {self.t1: 100.0, self.t2: 20.0, self.t3: 0.0})
        self._history(baseline)
        runs = self.env["cmc.evm.run"].search([("project_id", "=", self.prj.id), ("state", "=", "done")])
        self.assertGreater(len(runs), 1)
        action = self.env["ir.actions.act_window"]._for_xml_id("cmc_job_costing.action_cmc_evm_curve_run")
        context = safe_eval(action.get("context") or "{}", {"uid": self.env.uid, "context": {}, "active_id": False})
        defaults = [key[len("search_default_"):] for key, value in context.items() if key.startswith("search_default_") and value]
        self.assertTrue(defaults, "the S-curve action must open with a default filter")
        arch = self.env["cmc.evm.curve"].get_view(action.get("search_view_id") and action["search_view_id"][0], "search")["arch"]
        filters = {node.get("name"): node for node in etree.fromstring(arch).iter("filter")}
        chosen = [filters[name] for name in defaults if name in filters]
        self.assertTrue(chosen, "the default must be a <filter> of the curve search view, got %s" % defaults)
        node = chosen[0]
        node_context = safe_eval(node.get("context") or "{}", {"uid": self.env.uid})
        group_by = node_context.get("group_by") or []
        if "run_id" in (group_by if isinstance(group_by, list) else [group_by]):
            return  # one series per run: nothing is summed
        domain = safe_eval(node.get("domain") or "[]", {"uid": self.env.uid, "context_today": date.today})
        rows = self.env["cmc.evm.curve"].search(domain + [("run_id", "in", runs.ids)])
        self.assertTrue(rows)
        self.assertEqual(len(rows.run_id), 1, "the default filter must keep the curve of one status report")

    # ---- FX-013 -----------------------------------------------------------

    def test_fx013_jobs_without_center_listed_in_one_message(self):
        # FX-013 / RF-UX-010: every job with hours but no center, its hours, and where to set the center
        run = self._run(compute=False, project_ids=[Command.set((self.job_d | self.job_e).ids)])
        with self.assertRaises(UserError) as caught:
            run.action_compute()
        message = str(caught.exception)
        for expected in ("Commessa Delta", "Commessa Epsilon", "13", "27", self.center_plan.name):
            with self.subTest(expected=expected):
                self.assertIn(expected, message)
        self.assertEqual(run.state, "draft")

    # ---- FX-017 -----------------------------------------------------------

    def test_fx017_state_labels_translated_in_italian(self):
        # FX-017: an Italian user reads "Bozza / Calcolato" on the statusbar of the job cost and EVM runs
        self.assertTrue(self.env["res.lang"]._lang_get("it_IT"), "it_IT must be installed in the test database")
        for model in ("cmc.job.cost.run", "cmc.evm.run"):
            with self.subTest(model=model):
                selection = dict(self.env[model].with_context(lang="it_IT").fields_get(["state"])["state"]["selection"])
                self.assertEqual(selection["draft"], "Bozza")
                self.assertEqual(selection["done"], "Calcolato")
