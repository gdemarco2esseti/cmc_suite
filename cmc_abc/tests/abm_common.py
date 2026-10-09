"""WP-08 - shared fixture of the ``cmc_abc`` ABM red tests (ABM-01..ABM-05, COQ-01).

Sources: gap register rows ABM-01..05 / COQ-01; AD-118 WP-08 scope row (process roll-up + activity x
department matrix; attribute roll-up, Pareto, quadrant as pivot; measures per activity + trend; initiatives
with savings / one-time cost / payback / freed resources; unit activity cost benchmark; COQ attribute +
trend + CoPQ = defect units x product ABC unit cost; out of scope: bubble widget, value multipliers);
AD-119 (pure helpers in cmc_base/models/cmc_utils.py). Kaplan & Cooper Ch.6, Ch.8 (process view,
activity x department matrix "respond to customer requests 117,000: 38,000 in customer service",
business case 583,300 / 808,291 = 0.72 y, COQ categories); Cokins MIW ch.3/ch.4 (process = activities
tagged to process codes, quadrant of two scores), Frost St.8 (savings only when spending falls: freed
capacity is not a saving); Turney Ch.4-5, Ch.7-9 (cost per output = pool / volume, cycle time, quality,
Pareto 20/80, realised vs target savings, CoPQ).

CONTRACT (whole WP-08; each test file repeats the part it covers). Everything lives in cmc_abc.
Common rule: the ABM figures of an activity are its STAGE-1 cost (``resource_cost``: resources + tags, the
A->A reassignment is not counted twice) unless stated otherwise; Σ over any split = ``total_activity_cost``.
"Month figure" rule (trends): one figure per calendar month = the most recent (highest id) ``done`` run of
the company whose ``date_from`` falls in that month; draft runs never count.

ABM-01 process view / matrix
- ``cmc.abc.flow.center_id`` M2o account.analytic.account, stored: the center the cost came from (stage
  ``resource`` = resource.center_id, ``tag`` / ``trace`` = analytic account of the ledger lines).
- ``run._cmc_activity_center_matrix(by="activity")`` -> {(activity id, center id): Σ stage-1 amount};
  ``by="process"`` -> {(process id or False, center id): amount}. Row sums = stage-1 cost of the activity
  (process roll-up ``_cmc_process_costs``), column sums = what the center fed into the model, Σ =
  ``total_activity_cost`` (horizontal view reconciles with vertical view).
ABM-02 attributes, Pareto, quadrant
- ``cmc.abc.activity.cost`` snapshots at compute (stable when the dictionary changes afterwards):
  ``va_class_value_id``, ``coq_value_id``, ``efficiency_value_id`` (M2o cmc.attribute.value of the shipped
  VA_CLASS / COQ / EFFICIENCY attributes); Pareto on ``resource_cost`` descending (tie: activity code asc):
  ``pareto_rank`` Integer (1 = costliest), ``cost_share`` (% of total_activity_cost), ``cumulative_share``
  (%), ``pareto_class`` Selection ``A`` (cumulative <= 80) / ``B`` (<= 95) / ``C``.
- ``run._cmc_activity_pareto()`` -> list of dicts ``{activity, cost, share, cumulative_share, rank,
  pareto_class}`` in rank order (same figures as the stored fields).
- ``run._cmc_activity_cost_quadrant(attr_x, attr_y)`` (attribute record or code) -> {(x value id or False,
  y value id or False): Σ stage-1 cost}; Σ = total_activity_cost.
- ``run._cmc_object_cost_by_attribute(attr)`` -> {cost object id: {value id or False: amount}} over the
  activity -> object flows (stage ``object`` with a source activity, and ``sustaining``); the value is the
  one of the activity that CHARGES the object (A->A reassigned cost takes the class of the receiver).
  Σ per object = result ``activity_cost``. ``cmc.abc.result.nva_cost`` stored = the NVA (VA_CLASS code
  ``NVA``) part of it ("waste cost per product").
ABM-03 measures per activity
- ``cmc.abc.activity.measure_line_ids`` O2m ``cmc.abc.activity.measure``: ``activity_id`` (required,
  node_type ``activity``), ``measure_id`` M2o cmc.cost.driver (required, ``measure_kind == 'statistical'``),
  ``role`` Selection ``output`` / ``cycle_time`` / ``quality``, ``target`` Float. ValidationError: allocation
  driver, process/task node, two lines with the same role on one activity.
- ``cmc.measure.quantity.activity_id`` M2o cmc.abc.activity (_inherit in cmc_abc): the fact grain of an
  activity measure. Only ACTUAL facts (no version) of the run company/span with that activity count.
- Run result ``performance_ids`` O2m ``cmc.abc.activity.performance`` (in ``_cmc_result_fields``), one row
  per activity with >= 1 measure line: ``activity_id``, ``month`` (month start of date_from),
  ``activity_cost`` (= activity ``total_cost``), ``output_qty`` (Σ output facts; no output line -> the
  activity ``used_qty``), ``cost_per_output`` = activity_cost / output_qty, ``cycle_time`` = Σ cycle-time
  facts / output_qty, ``defect_qty`` = Σ quality facts, ``quality_rate`` = defect_qty / output_qty x 100.
  Zero output -> per-output figures 0 (never ZeroDivisionError).
- ``activity._cmc_performance_trend(date_from, date_to)`` -> list of dicts ``{month, activity_cost,
  output_qty, cost_per_output, cycle_time, quality_rate}`` by month (month figure rule).
ABM-04 improvement initiatives
- ``cmc.abm.initiative`` (company rule, ``_check_company_auto``; cmc user read/write/create, manager full):
  ``name``, ``company_id`` (required, default env.company), ``activity_id`` (required, node_type
  ``activity``), ``project_type`` Selection ``eliminate`` / ``substitute`` / ``reduce`` / ``share``,
  ``owner_id``, ``baseline_run_id`` (required, ``done``), ``review_run_id`` (optional, ``done``, != baseline),
  ``expected_driver_reduction`` (%), ``baseline_annual_cost`` = activity total_cost in the baseline run x 12
  / months of the baseline span (``cmc_utils.month_range``), ``expected_annual_saving`` (stored compute =
  baseline_annual_cost x reduction / 100, readonly=False), ``one_time_cost``, ``payback_years`` =
  ``cmc_utils.simple_payback(one_time_cost, expected_annual_saving)`` (one_time / annual saving; 0.0 when
  the saving <= 0), ``payback_months`` = years x 12, ``realized_annual_saving`` = (baseline monthly total -
  review monthly total) x 12, ``realization_percent`` = realized / expected x 100, ``freed_capacity_qty`` =
  baseline used_qty - review used_qty, ``freed_capacity_cost`` = freed qty x baseline rate,
  ``resource_action`` Selection ``grow`` / ``redeploy`` / ``remove``, ``benefit_status`` Selection
  ``pending`` (no review) / ``not_realized`` (realized <= 0) / ``partial`` (< 100 %) / ``realized``.
  ValidationError: process/task activity, draft run, review == baseline.
ABM-05 benchmark
- ``cmc.abm.benchmark`` (``company_id`` optional: a benchmark may span companies; rule company False or
  parent_of): ``name``, ``run_ids`` M2m cmc.abc.run, ``line_ids`` O2m ``cmc.abm.benchmark.line``
  (``benchmark_id``, ``run_id``, ``company_id`` related run stored, ``period`` = run date_from, ``activity_code``,
  ``activity_id``, ``unit_cost`` = activity cost ``rate``, ``best_unit_cost`` = min unit_cost of the code over
  the lines, ``gap_percent`` = (unit - best) / best x 100, ``is_best``). Common dictionary = activity
  ``code``. ``action_compute()`` replaces the lines, one per activity cost with used_qty > 0 per run;
  a draft run -> UserError. Lines carry the company rule of their run.
COQ-01 cost of quality
- ``run._cmc_coq_split()`` -> {``prevention``, ``appraisal``, ``internal_failure``, ``external_failure``,
  ``error_free``, ``unclassified`` (no COQ value), ``conformance`` (prevention + appraisal),
  ``nonconformance`` (internal + external), ``coq_total`` (conformance + nonconformance), ``total`` (=
  total_activity_cost), ``coq_percent`` (coq_total / total x 100)} from the ``coq_value_id`` snapshot.
- ``env["cmc.abc.run"]._cmc_coq_trend(date_from, date_to)`` -> list of ``{month, **split}`` (month figure rule,
  current company).
- Run input ``defect_measure_id`` (statistical measure, in ``_cmc_input_fields``); results ``defect_qty`` (Σ
  actual facts of the span per cost object) and ``copq_amount`` = defect_qty x unit_cost; run
  ``copq_total`` = Σ copq_amount (reset to 0 by reset to draft).
Pure helper (AD-119, cmc_base/models/cmc_utils.py): ``simple_payback(one_time_cost, annual_saving)``.

ABM FIXTURE (on top of the WP-07 main fixture of abc2_common, March 2027, stage-1 costs:
  MACH 6600, SETUP 3400, QC 2500, MGMT 2000, ADMIN 1000, CUST 1000 = 16500)
  VA_CLASS: MACH VA; SETUP, QC NVA; ADMIN, CUST, MGMT BVA -> VA 6600 / NVA 5900 / BVA 4000.
  EFFICIENCY: MACH E4, SETUP E2, QC E2, ADMIN E3, CUST E4, MGMT none.
  COQ (labels chosen for arithmetic): SETUP prevention 3400, QC appraisal 2500, ADMIN internal failure
  1000, CUST external failure 1000, MACH error-free 6600, MGMT unclassified 2000.
  User attributes (company A) IMPORTANCE H/L, PERFORMANCE H/L: MACH H/H, SETUP H/L, QC L/L, ADMIN L/H,
  CUST H/H, MGMT H/none -> quadrant HH 7600, HL 3400, LL 2500, LH 1000, (H, False) 2000.
  Defects (statistical WP08DEF): P1 4, P2 2 in March (P1 7 on 2027-04-05 = distractor).
"""

from datetime import date

from .abc2_common import Abc2Case

APR_FROM = date(2027, 4, 1)
APR_TO = date(2027, 4, 30)


class AbmCase(Abc2Case):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env_a
        ref = cls.env.ref
        cls.va = ref("cmc_base.cmc_attribute_value_va")
        cls.bva = ref("cmc_base.cmc_attribute_value_bva")
        cls.nva = ref("cmc_base.cmc_attribute_value_nva")
        cls.e2 = ref("cmc_base.cmc_attribute_value_e2")
        cls.e3 = ref("cmc_base.cmc_attribute_value_e3")
        cls.e4 = ref("cmc_base.cmc_attribute_value_e4")
        cls.coq_prev = ref("cmc_base.cmc_attribute_value_coq_prevention")
        cls.coq_appr = ref("cmc_base.cmc_attribute_value_coq_appraisal")
        cls.coq_int = ref("cmc_base.cmc_attribute_value_coq_internal_failure")
        cls.coq_ext = ref("cmc_base.cmc_attribute_value_coq_external_failure")
        cls.coq_free = ref("cmc_base.cmc_attribute_value_coq_error_free")
        activity_model = cls.env.ref("cmc_abc.model_cmc_abc_activity")
        Attr = env["cmc.attribute"]
        cls.importance = Attr.create({"name": "Importance", "code": "WP08IMP", "company_id": cls.company_a.id,
                                      "target_model_ids": [(6, 0, activity_model.ids)]})
        cls.performance = Attr.create({"name": "Performance", "code": "WP08PERF", "company_id": cls.company_a.id,
                                       "target_model_ids": [(6, 0, activity_model.ids)]})
        Val = env["cmc.attribute.value"]
        cls.imp_h = Val.create({"attribute_id": cls.importance.id, "name": "High", "code": "H"})
        cls.imp_l = Val.create({"attribute_id": cls.importance.id, "name": "Low", "code": "L"})
        cls.perf_h = Val.create({"attribute_id": cls.performance.id, "name": "High", "code": "H"})
        cls.perf_l = Val.create({"attribute_id": cls.performance.id, "name": "Low", "code": "L"})
        for activity, values in (
            (cls.mach, cls.va | cls.e4 | cls.coq_free | cls.imp_h | cls.perf_h),
            (cls.setup, cls.nva | cls.e2 | cls.coq_prev | cls.imp_h | cls.perf_l),
            (cls.qc, cls.nva | cls.e2 | cls.coq_appr | cls.imp_l | cls.perf_l),
            (cls.admin, cls.bva | cls.e3 | cls.coq_int | cls.imp_l | cls.perf_h),
            (cls.cust, cls.bva | cls.e4 | cls.coq_ext | cls.imp_h | cls.perf_h),
            (cls.mgmt, cls.bva | cls.imp_h),
        ):
            activity.cmc_attribute_value_ids = [(6, 0, values.ids)]
        cls.defects = cls._driver(env, "WP08DEF", "transaction", kind="statistical")
        cls._fact(env, cls.defects, cls.obj_p1, 4)
        cls._fact(env, cls.defects, cls.obj_p2, 2)
        cls._fact(env, cls.defects, cls.obj_p1, 7, day=date(2027, 4, 5))

    # ---- extra runs -------------------------------------------------------

    def _april_run(self, compute=True, env=None):
        # April: only the 9999 Z SAL fixed line of 2027-04-02 -> R1 100 % to QC; QC 3 inspections -> rate 3333
        return self._main_run(
            compute=compute, env=env, name="WP08 April", date_from=APR_FROM, date_to=APR_TO,
            trace_direct=False, output_measure_id=False,
            resource_lines=[{"resource_id": self.r1.id, "activity_id": self.qc.id, "percent": 100}],
            edges=[{"activity_id": self.qc.id, "cost_object_id": self.obj_p1.id,
                    "driver_id": self.inspections.id, "driver_qty": 3}],
            capacity=[],
        )

    def _lines_with(self, **r1_percent):
        """Main resource lines with R1 percentages replaced (e.g. qc=12, mach=58)."""
        lines = self._resource_lines()
        acts = {"setup": self.setup, "mach": self.mach, "qc": self.qc}
        for line in lines:
            for key, pct in r1_percent.items():
                if line["resource_id"] == self.r1.id and line["activity_id"] == acts[key].id:
                    line["percent"] = pct
        return lines

    def _edges_qc(self, p1, p2):
        """Main edges with the QC inspections replaced."""
        edges = [e for e in self._edges() if e.get("activity_id") != self.qc.id or e.get("target_activity_id")]
        return edges + [
            {"activity_id": self.qc.id, "cost_object_id": self.obj_p1.id, "driver_id": self.inspections.id, "driver_qty": p1},
            {"activity_id": self.qc.id, "cost_object_id": self.obj_p2.id, "driver_id": self.inspections.id, "driver_qty": p2},
        ]
