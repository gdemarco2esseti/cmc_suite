"""FX-012 - the S-curve shows PV, EV and AC TOGETHER, cumulative, one status report at a time.

The Odoo 19 graph view plots ONE measure; three measures need a LONG-FORMAT read model (one row per
month x series), the series being the graph column groupby.

CONTRACT (binding for the developer; tests are read-only)
- ``cmc.evm.curve.series``: ``_auto = False`` SQL view over ``cmc.evm.curve`` (read-only: no create/write).
  Fields: ``run_id`` M2o ``cmc.evm.run``, ``project_id`` M2o ``project.project``, ``month`` Date (= curve
  ``date``), ``series`` Selection ``pv`` "Planned Value (PV)" / ``ev`` "Earned Value (EV)" / ``ac`` "Actual
  Cost (AC)", ``value`` Monetary (or Float) = the CUMULATIVE figure, ``aggregator = "max"``, ``currency_id``,
  ``company_id``. One row per curve month x series; FORECAST months (``is_forecast``) repeat the status-month EV/AC next to the ``pv``
  row (flat after the status month, it never drops to 0). Values = ``pv_cumulative`` /
  ``ev_cumulative`` / ``ac_cumulative`` of the curve row.
- ``cmc.evm.run.is_latest`` Boolean (non-stored with search method allowed): True on the computed run with the
  latest status date of its project (one per project).
- Window action ``cmc_job_costing.action_cmc_evm_curve_series`` on the series model, graph FIRST in
  ``view_mode``; its graph view ``type="line"`` groups by ``month`` (row) and ``series``, measure ``value``;
  its context sets a ``search_default_<filter>`` that is a ``<filter>`` of the series search view whose domain
  uses ``run_id.is_latest``. The menu ``cmc_job_costing.menu_cmc_evm_curve`` opens this action.
- ACL: ``cmc_base.group_cmc_user`` read only; no CMC group -> AccessError; global ir.rule
  ``[('company_id', 'parent_of', company_ids)]``: a company-B user sees no row of company A.

Fixture: ``evm_common`` (three monthly status reports Jan/Feb/Mar 2026 on the Jan..Jun baseline).
March run cumulative: PV 2625 / 12950 / 20650 / 30275 / 32200 / 32200, EV 2625 / 9870 / 16800,
AC 3000 / 11000 / 17000; Apr..Jun forecast.
"""

from lxml import etree

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests.common import new_test_user, tagged
from odoo.tools.safe_eval import safe_eval

from .evm_common import APR, FEB, JAN, JUN, MAR, MAR_END, MAY, EvmCase

SERIES = "cmc.evm.curve.series"
ORDER = {"pv": 0, "ev": 1, "ac": 2}


@tagged("post_install", "-at_install")
class TestFx012CurveSeries(EvmCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groups_user = "base.group_user,project.group_project_user,cmc_base.group_cmc_user"
        cls.user_a = new_test_user(
            cls.env, login="fx012_cmc_a", groups=groups_user,
            company_id=cls.company_a.id, company_ids=[Command.set(cls.company_a.ids)],
        )
        cls.user_b = new_test_user(
            cls.env, login="fx012_cmc_b", groups=groups_user,
            company_id=cls.company_b.id, company_ids=[Command.set(cls.company_b.ids)],
        )
        cls.outsider = new_test_user(
            cls.env, login="fx012_outsider", groups="base.group_user,project.group_project_user",
            company_id=cls.company_a.id, company_ids=[Command.set(cls.company_a.ids)],
        )

    # ---- helpers ------------------------------------------------------------------------------------

    def _series_of(self, evm_run, env=None):
        env = env or self.env
        self.env.flush_all()
        rows = env[SERIES].search([("run_id", "=", evm_run.id)])
        return rows.sorted(lambda r: (r.month, ORDER[r.series]))

    def _action(self):
        return self.env["ir.actions.act_window"]._for_xml_id("cmc_job_costing.action_cmc_evm_curve_series")

    def _default_latest_filter(self, action):
        context = safe_eval(action.get("context") or "{}", {"uid": self.env.uid, "context": {}, "active_id": False})
        defaults = [key[len("search_default_"):] for key, value in context.items() if key.startswith("search_default_") and value]
        self.assertTrue(defaults, "the S-curve action must open with a default filter")
        search_id = action.get("search_view_id") and action["search_view_id"][0]
        arch = self.env[SERIES].get_view(search_id, "search")["arch"]
        filters = {node.get("name"): node for node in etree.fromstring(arch).iter("filter")}
        chosen = [filters[name] for name in defaults if name in filters]
        self.assertTrue(chosen, "the default must be a <filter> of the series search view, got %s" % defaults)
        return chosen[0]

    # ---- model shape --------------------------------------------------------------------------------

    def test_fx012_series_model_is_sql_view_with_contract_fields(self):
        # FX-012: a long-format read model (SQL view), one measure "value" split by "series"
        model = self.env[SERIES]
        self.assertFalse(model._auto, "cmc.evm.curve.series must be a SQL view (_auto = False)")
        fields_ = model._fields
        self.assertEqual(fields_["run_id"].comodel_name, "cmc.evm.run")
        self.assertEqual(fields_["project_id"].comodel_name, "project.project")
        self.assertEqual(fields_["company_id"].comodel_name, "res.company")
        self.assertEqual(fields_["currency_id"].comodel_name, "res.currency")
        self.assertEqual(fields_["month"].type, "date")
        self.assertIn(fields_["value"].type, ("monetary", "float"))
        # a cumulative figure is never summed across rows (months of several runs)
        self.assertEqual(fields_["value"].aggregator, "max")
        selection = dict(fields_["series"]._description_selection(self.env))
        self.assertEqual(selection, {"pv": "Planned Value (PV)", "ev": "Earned Value (EV)", "ac": "Actual Cost (AC)"})

    # ---- rows ---------------------------------------------------------------------------------------

    def test_fx012_three_series_up_to_status_month(self):
        # FX-012 AC: the March report shows PV, EV and AC cumulative, each non-decreasing, Jan..Mar
        _jan, _feb, mar = self._history(self._baseline())
        rows = self._series_of(mar).filtered(lambda r: r.month <= MAR)
        self.assertRecordValues(rows, [
            {"month": JAN, "series": "pv", "value": 2625.0, "run_id": mar.id, "project_id": self.prj.id, "company_id": self.company_a.id},
            {"month": JAN, "series": "ev", "value": 2625.0, "run_id": mar.id, "project_id": self.prj.id, "company_id": self.company_a.id},
            {"month": JAN, "series": "ac", "value": 3000.0, "run_id": mar.id, "project_id": self.prj.id, "company_id": self.company_a.id},
            {"month": FEB, "series": "pv", "value": 12950.0, "run_id": mar.id, "project_id": self.prj.id, "company_id": self.company_a.id},
            {"month": FEB, "series": "ev", "value": 9870.0, "run_id": mar.id, "project_id": self.prj.id, "company_id": self.company_a.id},
            {"month": FEB, "series": "ac", "value": 11000.0, "run_id": mar.id, "project_id": self.prj.id, "company_id": self.company_a.id},
            {"month": MAR, "series": "pv", "value": 20650.0, "run_id": mar.id, "project_id": self.prj.id, "company_id": self.company_a.id},
            {"month": MAR, "series": "ev", "value": 16800.0, "run_id": mar.id, "project_id": self.prj.id, "company_id": self.company_a.id},
            {"month": MAR, "series": "ac", "value": 17000.0, "run_id": mar.id, "project_id": self.prj.id, "company_id": self.company_a.id},
        ])
        self.assertEqual(rows.currency_id, self.company_a.currency_id)

    def test_fx012_forecast_months_hold_status_figures(self):
        # FX-012 (DL 2026-10-11): after the status month EV/AC stay flat at the March figure - never a fall to 0
        _jan, _feb, mar = self._history(self._baseline())
        rows = self._series_of(mar).filtered(lambda r: r.month > MAR)
        self.assertRecordValues(rows, [
            {"month": APR, "series": "pv", "value": 30275.0},
            {"month": APR, "series": "ev", "value": 16800.0},
            {"month": APR, "series": "ac", "value": 17000.0},
            {"month": MAY, "series": "pv", "value": 32200.0},
            {"month": MAY, "series": "ev", "value": 16800.0},
            {"month": MAY, "series": "ac", "value": 17000.0},
            {"month": JUN, "series": "pv", "value": 32200.0},
            {"month": JUN, "series": "ev", "value": 16800.0},
            {"month": JUN, "series": "ac", "value": 17000.0},
        ])

    def test_fx012_each_series_is_non_decreasing(self):
        # FX-012 AC: every series of one report is a cumulative line, never a bell
        _jan, _feb, mar = self._history(self._baseline())
        rows = self._series_of(mar)
        for code in ("pv", "ev", "ac"):
            with self.subTest(series=code):
                values = rows.filtered(lambda r, c=code: r.series == c).sorted("month").mapped("value")
                self.assertTrue(values)
                self.assertEqual(values, sorted(values))
                self.assertNotIn(0.0, values[1:])

    def test_fx012_values_mirror_curve_cumulative_columns(self):
        # FX-012: the read model is a pure reshaping of cmc.evm.curve (same figures, one row per month x series)
        _jan, feb, mar = self._history(self._baseline())
        for evm_run in (feb, mar):
            expected, last = [], None
            for curve in evm_run.curve_ids.sorted("date"):
                if not curve.is_forecast:
                    last = curve
                expected.append({"month": curve.date, "series": "pv", "value": curve.pv_cumulative})
                if last:
                    expected.append({"month": curve.date, "series": "ev", "value": last.ev_cumulative})
                    expected.append({"month": curve.date, "series": "ac", "value": last.ac_cumulative})
            with self.subTest(run=evm_run.status_date):
                self.assertRecordValues(self._series_of(evm_run), expected)

    def test_fx012_series_follow_the_run_lifecycle(self):
        # FX-012: a SQL view, not a copy: resetting a run removes its series rows
        mar = self._evm(self._baseline(), self.job_mar, MAR_END, {self.t1: 100.0, self.t2: 50.0, self.t3: 0.0})
        self.assertTrue(self._series_of(mar))
        mar.action_reset_to_draft()
        self.assertFalse(self._series_of(mar))

    # ---- latest run ---------------------------------------------------------------------------------

    def test_fx012_is_latest_marks_one_run_per_project(self):
        # FX-012: the default S-curve is the latest computed status report of each job
        jan, feb, mar = self._history(self._baseline())
        self.assertRecordValues(jan | feb | mar, [{"is_latest": False}, {"is_latest": False}, {"is_latest": True}])
        latest = self.env["cmc.evm.run"].search([("is_latest", "=", True), ("project_id", "=", self.prj.id)])
        self.assertEqual(latest, mar)

    # ---- action, graph view, menu -------------------------------------------------------------------

    def test_fx012_action_opens_line_graph_by_month_and_series(self):
        # FX-012: one chart, three lines: graph line, rows = month, series = PV/EV/AC, measure = value
        action = self._action()
        self.assertEqual(action["res_model"], SERIES)
        self.assertEqual(action["view_mode"].split(",")[0], "graph")
        graph_id = next((view_id for view_id, view_type in action.get("views") or [] if view_type == "graph"), False)
        arch = etree.fromstring(self.env[SERIES].get_view(graph_id, "graph")["arch"])
        self.assertEqual(arch.tag, "graph")
        self.assertEqual(arch.get("type"), "line")
        fields_ = {node.get("name"): node for node in arch.iter("field")}
        self.assertIn("month", fields_)
        self.assertIn(fields_["month"].get("type"), (None, "row"))
        self.assertIn("series", fields_)
        self.assertIn(fields_["series"].get("type"), ("row", "col"))
        self.assertEqual(fields_.get("value") is not None and fields_["value"].get("type"), "measure")

    def test_fx012_action_default_filter_is_latest_run(self):
        # FX-012: several reports of the same job are never summed: the default filter keeps the latest one
        baseline = self._baseline()
        _jan, _feb, mar = self._history(baseline)
        node = self._default_latest_filter(self._action())
        self.assertIn("is_latest", node.get("domain") or "")
        domain = safe_eval(node.get("domain") or "[]", {"uid": self.env.uid})
        self.env.flush_all()
        rows = self.env[SERIES].search(domain + [("project_id", "=", self.prj.id)])
        self.assertEqual(rows.run_id, mar)
        self.assertEqual(len(rows), 18)  # 6 PV + 6 EV + 6 AC (EV/AC flat after the status month)

    def test_fx012_grouped_like_the_graph_never_sums_runs(self):
        # FX-012: even WITHOUT the default filter, the graph aggregation (max) of three reports keeps the cumulative
        self._history(self._baseline())
        self.env.flush_all()
        aggregator = self.env[SERIES]._fields["value"].aggregator
        groups = self.env[SERIES]._read_group(
            [("project_id", "=", self.prj.id), ("month", "=", JAN)], ["series"], ["value:%s" % aggregator],
        )
        by_series = dict(groups)
        self.assertEqual(by_series, {"pv": 2625.0, "ev": 2625.0, "ac": 3000.0})

    def test_fx012_menu_opens_series_action(self):
        # FX-012: the S-curve menu entry opens the three-series chart
        menu = self.env.ref("cmc_job_costing.menu_cmc_evm_curve")
        self.assertEqual(menu.action, self.env.ref("cmc_job_costing.action_cmc_evm_curve_series"))

    # ---- security -----------------------------------------------------------------------------------

    def test_fx012_cmc_user_reads_the_series(self):
        # FX-012 / AD-115: the controller opens the S-curve with their own user
        _jan, _feb, mar = self._history(self._baseline())
        rows = self._series_of(mar, env=self._env_a(self.user_a))
        self.assertEqual(len(rows), 18)
        self.assertEqual(rows.mapped("value")[:3], [2625.0, 2625.0, 3000.0])

    def test_fx012_series_is_read_only_for_cmc_user(self):
        # FX-012: a report, never an input: a CMC user cannot create rows
        with self.assertRaises(AccessError):
            self._env_a(self.user_a)[SERIES].create({"month": JAN, "series": "pv", "value": 1.0})

    def test_fx012_no_cmc_group_no_access(self):
        # FX-012 / AD-115: without a CMC group the S-curve is not readable
        self._history(self._baseline())
        self.env.flush_all()
        with self.assertRaises(AccessError):
            self._env_a(self.outsider)[SERIES].search([])

    def test_fx012_other_company_user_sees_nothing(self):
        # FX-012 / AD-115: a CMC user of company B sees none of company A's S-curve rows
        _jan, _feb, mar = self._history(self._baseline())
        self.env.flush_all()
        env_b = self.env(user=self.user_b, context=dict(self.env.context, allowed_company_ids=self.company_b.ids))
        self.assertFalse(env_b[SERIES].search([("run_id", "=", mar.id)]))
        self.assertFalse(env_b[SERIES].search([]).filtered(lambda r: r.company_id == self.company_a))
