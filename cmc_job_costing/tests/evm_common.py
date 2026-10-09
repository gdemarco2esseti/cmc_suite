"""WP-15 project control / earned value in ``cmc_job_costing``: PRJ-01..PRJ-04 (AD-101/104/105/108/118/119).

CONTRACT (binding for the developer; tests are read-only)
=========================================================
Same module, same depends (``cmc_hr_timesheet``). Every new model: ``company_id`` (stored, indexed),
``_check_company_auto = True``, global ``ir.rule`` ``[('company_id', 'parent_of', company_ids)]`` (AD-115).
ACL: ``cmc_base.group_cmc_user`` read/write/create on the baseline inputs (estimate lines, risks), on
change requests and on the EVM run; READ-ONLY on the result models (``cmc.evm.task.line``,
``cmc.evm.curve``) and on ``cmc.evm.baseline.line`` (written by the engine only); ``group_cmc_manager``
full; no access without a CMC group (AccessError). Pure maths (PERT, EMV, EVM indices, control limits,
rule of seven) live in ``cmc_base/models/cmc_utils.py`` (AD-119, tested in ``cmc_base/tests/test_wp15_pert.py``)
and the engines call them: no copy of the formulas here.

Task progress (``project.task``, ``_inherit``)
- ``cmc_progress`` Float = physical % complete 0..100 (outside -> ValidationError). EV reads THIS field,
  never the timesheet-based core ``progress``.

PRJ-01/02 Baseline = ``cmc.plan.version`` with ``version_type = 'baseline'`` (AD-104, ``_inherit`` here)
- ``project_id`` M2o ``project.project`` (check_company; required for a baseline -> ValidationError, ignored
  by the other types); ``cmc_rate_driver_id`` M2o ``cmc.cost.driver`` = the unit of the RATE CARD.
  Rate card = ``cmc.rate`` rows published on THE VERSION (``cmc.rate._cmc_publish(version, rows)``, holder =
  resource center, driver = ``cmc_rate_driver_id``, month; AD-108 planned rates, copied with the version).
- ``management_reserve_percent`` Float (>= 0).
- O2m ``estimate_line_ids`` (``cmc.evm.estimate.line``), ``baseline_line_ids`` (``cmc.evm.baseline.line``),
  ``risk_ids`` (``cmc.evm.risk``), ``change_request_ids`` (``cmc.evm.change.request``, ``baseline_id``).
- Stored totals (company currency of the version): ``estimate_expected`` = sum of ``cost_expected``;
  ``estimate_sigma`` = sqrt(sum ``cost_sigma``^2); ``estimate_low`` / ``estimate_high`` = expected -/+ 2 sigma
  (``cmc_utils.pert_total``); ``bac`` = sum of ``baseline_line_ids.planned_value`` (budget at completion of the
  work packages, the EVM BAC); ``contingency_reserve`` = sum of the risks' ``emv``; ``cost_baseline`` = bac +
  contingency; ``management_reserve`` = cost_baseline x percent / 100; ``project_budget`` = cost_baseline +
  management reserve (PCM: MR outside the baseline).
- ``action_cmc_evm_plan()`` (draft only, UserError on an approved version): (1) sets ``hourly_rate`` of every
  estimate line with hours from the version rate card at the month of ``date_start``
  (``cmc.rate._cmc_lookup(center, cmc_rate_driver_id, date_start, origin=version)``; missing -> UserError naming
  the center; a rate of a run or of another version never counts); (2) REPLACES the baseline lines: each
  estimate line's ``cost_expected`` (rounded to the currency) phased evenly over the months of
  [date_start, date_end] with ``cmc_utils.phase_by_month`` (parts re-sum exactly), one row per task x month
  (rows of the same task and month are summed).
- ``action_approve()`` of a baseline runs the plan first (always consistent), then locks. No estimate line ->
  UserError. Approved = locked inputs (estimate lines / risks are ``cmc.run.line.mixin`` lines of the version:
  create/write/unlink -> UserError). Only ``group_cmc_manager`` approves (AD-104).
- ``cmc.evm.estimate.line`` (WBS work package estimate; ``_cmc_run_field = "version_id"``): ``version_id``,
  ``name``, ``task_id`` (required, task of the version project -> else ValidationError), ``center_id``
  (resource center, required when hours > 0 -> ValidationError), ``hours_optimistic`` / ``hours_most_likely`` /
  ``hours_pessimistic`` Float (0 <= O <= M <= P -> else ValidationError), ``material_cost`` Monetary,
  ``date_start`` / ``date_end`` Date (start <= end, both inside the version span -> else ValidationError),
  ``hourly_rate`` Float (set by the plan), stored computes: ``hours_expected`` = (O+4M+P)/6,
  ``cost_optimistic`` / ``cost_most_likely`` / ``cost_pessimistic`` = hours x rate + material,
  ``cost_expected`` = PERT of the three costs, ``cost_sigma`` = (cost P - cost O)/6.
- ``cmc.evm.baseline.line`` (time-phased PV, AD-105 month grain): ``version_id``, ``task_id``, ``date``
  (first day of month), ``planned_value`` Monetary. BAC of a task = sum of its rows.
- ``cmc.evm.risk`` (risk register, line of the version): ``name``, ``task_id`` (optional), ``probability``
  Float PERCENT 0..100 (outside -> ValidationError), ``impact`` Monetary (negative = opportunity), stored
  ``emv`` = probability/100 x impact (``cmc_utils.emv``); ``_order = "emv desc, id"`` (risk ranking).

PRJ-03 Change requests, tracked OUTSIDE the baseline, and re-baseline
- ``cmc.evm.change.request``: ``name``, ``baseline_id`` (required, a baseline version), ``company_id``
  (related stored), ``task_id`` (task of the baseline project -> else ValidationError), ``date`` (inside the
  baseline span -> else ValidationError), ``cost_impact`` Monetary, ``state`` ``draft`` (default) /
  ``approved`` / ``rejected``, ``new_baseline_id`` (readonly). A draft CR never changes its baseline.
- ``action_approve()``: manager only (AccessError); CR must be draft and its baseline approved (UserError).
  Copies the baseline (``copy()``: new DRAFT baseline, ``parent_id`` = old, estimate lines, risks and rate
  card copied), adds ONE estimate line to the copy (``name`` = CR name, task, no hours, ``material_cost`` =
  cost impact, ``date_start = date_end`` = first day of the CR month), runs the plan on the copy, sets ``new_baseline_id``
  and ``state = 'approved'``. The old baseline is untouched. ``action_reject()``: draft -> rejected.

PRJ-03/04 EVM status run ``cmc.evm.run`` (``_inherit = ["cmc.run.mixin"]``)
- Inputs (``_cmc_input_fields``, locked when computed): ``name``, ``company_id``, ``project_id``, ``baseline_id`` (baseline version of the same project ->
  ValidationError otherwise), ``job_run_id`` (``cmc.job.cost.run``), ``status_date``, ``control_limit``
  Monetary (+/- band on the monthly cost variance; 0 = no limit).
- Compute (UserError when: the baseline is not approved; the job run is not computed; its ``date_to`` is
  not the status date; it has no sheet for the project). Records usage (AD-002) of the baseline and of the
  job run: neither can be reset while the EVM run is computed.
  BAC = baseline ``bac``; PV = sum of baseline rows with ``date <= status_date`` (the status month counts in
  full); EV = sum over tasks of round(task BAC x ``cmc_progress`` / 100) (progress snapshotted at Compute);
  AC = ``job_cost`` (job-to-date) of the job run sheet of the project. Indices from
  ``cmc_utils.evm_indices``: ``cv``, ``sv``, ``cpi``, ``spi``, ``eac`` (BAC/CPI), ``eac_atypical``
  (AC + BAC - EV), ``eac_composite`` (AC + (BAC - EV)/(CPI x SPI)), ``etc``, ``vac``, ``tcpi``; an undefined
  index is stored as 0.0. Header outputs: ``bac``, ``pv``, ``ev``, ``ac`` + indices + ``rule_of_seven``
  Boolean + ``out_of_control_count`` Integer (cleared on reset).
- Results (``_cmc_result_fields`` = ``task_line_ids``, ``curve_ids``):
  ``cmc.evm.task.line``: ``run_id``, ``task_id``, ``bac``, ``percent_complete``, ``pv``, ``ev``, ``sv``.
  ``cmc.evm.curve`` (S-curve + planned vs actual per period), one row per month of the baseline span:
  ``date``, ``pv`` (month), ``pv_cumulative``, ``ev_cumulative``, ``ac_cumulative``, ``ev``, ``ac``,
  ``cv`` = ev - ac of the month, ``is_forecast`` (month after the status month: EV/AC 0), ``out_of_control``
  (|cv| > control_limit, limit > 0; ``cmc_utils.control_breaches``). EV/AC cumulative of an earlier month =
  the COMPUTED EVM run of the same project and baseline with the latest status date in that month (carry the
  previous value forward when a month has none, 0 before the first); the status month = this run.
  ``rule_of_seven`` = ``cmc_utils.rule_of_seven`` over the monthly cv up to the status month.

Fixture (company A; hand numbers, 2026)
  Project P "Commessa EVM" (analytic AA-EVM, center Z), tasks T1 Design, T2 Build, T3 Test; project Q with task
  TQ (distractor). Rate card on the baseline (driver "WP15 hour"): Z Jan 50, Y Feb 80, Z Apr 50.
  Baseline Jan..Jun 2026, management reserve 10 %:
    T1 Z, hours 80/100/150, Jan-Feb      -> E 105 h, cost 4000/5000/7500 -> E 5250, sigma 583.33
    T2 Y, hours 200/240/340 + mat. 3100, Feb-Apr -> E 250 h, cost 19100/22300/30300 -> E 23100, sigma 1866.67
    T3 Z, hours 40/60/110 + mat. 600, Apr-May    -> E 65 h, cost 2600/3600/6100 -> E 3850, sigma 583.33
    project E 32200, sigma sqrt(4165000) = 2040.83, band 28118.33 .. 36281.67.
    PV: T1 Jan 2625, Feb 2625; T2 Feb/Mar/Apr 7700; T3 Apr/May 1925. Monthly 2625, 10325, 7700, 9625, 1925, 0;
    cumulative 2625, 12950, 20650, 30275, 32200, 32200; BAC 32200.
    Risks: R1 40 % x 5000 = 2000, R2 25 % x 4000 = 1000, R3 50 % x -1200 = -600 -> CR 2400; cost baseline
    34600; MR 3460; project budget 38060.
  Actual cost (direct MP lines stamped with P's cost object): Jan 3000, Feb 8000, Mar 6000 (+ Apr 5000 after
  every status date); job runs (overhead 0) Jan / Feb / Mar -> job-to-date AC 3000 / 11000 / 17000.
  Status reports: Jan 31 (T1 50 %) EV 2625; Feb 28 (T1 100, T2 20) EV 9870, PV 12950;
    Mar 31 (T1 100, T2 50, T3 0) EV 16800, PV 20650, AC 17000 -> CV -200, SV -3850, CPI 0.9882, SPI 0.8136,
    EAC 32583.33, ETC 15583.33, VAC -383.33, EAC atypical 32400, EAC composite 36154.51, TCPI 1.0132.
    Monthly cv: Jan -375, Feb -755, Mar +930 -> limit 800: only March out of control; no rule of seven.
  Change request on T2, April, +1500 -> new baseline BAC 33700 (T2 April 9200).
"""

from datetime import date

from odoo import Command

from .common import JobCase

B_FROM = date(2026, 1, 1)
B_TO = date(2026, 6, 30)
JAN, FEB, MAR, APR, MAY, JUN = (date(2026, m, 1) for m in range(1, 7))
JAN_END, FEB_END, MAR_END = date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)


class EvmCase(JobCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        su = cls.su
        cls.hour = su["cmc.cost.driver"].create({"name": "WP15 hour", "code": "WP15H"})
        cls.aa_p = cls._job_analytic(su, "WP15 AA-EVM", cls.company_a)
        cls.aa_q = cls._job_analytic(su, "WP15 AA-Q", cls.company_a)
        cls.prj = cls._project(su, "Commessa EVM", cls.aa_p, cls.z, cls.company_a)
        cls.prj_q = cls._project(su, "Commessa Q", cls.aa_q, cls.z, cls.company_a)
        Task = su["project.task"]
        cls.t1, cls.t2, cls.t3 = Task.create([{"name": n, "project_id": cls.prj.id} for n in ("Design", "Build", "Test")])
        cls.tq = Task.create({"name": "Other job task", "project_id": cls.prj_q.id})
        cls.obj_p = su["cmc.cost.object"]._cmc_ensure_objects(cls.prj, company=cls.company_a, object_type="project")
        for day, cost in ((date(2026, 1, 15), 3000), (date(2026, 2, 15), 8000), (date(2026, 3, 15), 6000), (date(2026, 4, 15), 5000)):
            cls._direct(su, cls.aa_p, cls.obj_p, cost, cls.mp, day=day)
        env_a = cls.env(context=dict(cls.env.context, allowed_company_ids=cls.company_a.ids))
        cls.job_runs = env_a["cmc.job.cost.run"].create([
            {"name": "WP15 job %s" % d_to, "company_id": cls.company_a.id, "date_from": d_from, "date_to": d_to,
             "project_ids": [Command.set(cls.prj.ids)], "overhead_rate": 0.0, "overhead_base": "hours"}
            for d_from, d_to in ((JAN, JAN_END), (FEB, FEB_END), (MAR, MAR_END))
        ])
        for run in cls.job_runs:
            run.action_compute()
        cls.job_jan, cls.job_feb, cls.job_mar = cls.job_runs

    # ---- builders -----------------------------------------------------------------------------------

    def _estimate_vals(self):
        return [
            {"name": "Design", "task_id": self.t1.id, "center_id": self.z.id, "hours_optimistic": 80, "hours_most_likely": 100,
             "hours_pessimistic": 150, "material_cost": 0.0, "date_start": JAN, "date_end": FEB_END},
            {"name": "Build", "task_id": self.t2.id, "center_id": self.y.id, "hours_optimistic": 200, "hours_most_likely": 240,
             "hours_pessimistic": 340, "material_cost": 3100.0, "date_start": FEB, "date_end": date(2026, 4, 30)},
            {"name": "Test", "task_id": self.t3.id, "center_id": self.z.id, "hours_optimistic": 40, "hours_most_likely": 60,
             "hours_pessimistic": 110, "material_cost": 600.0, "date_start": APR, "date_end": date(2026, 5, 31)},
        ]

    def _baseline(self, approve=True, env=None, estimates=None, risks=True, rate_card=None, **vals):
        """The real producers: the version, its rate card published through cmc.rate._cmc_publish, then lines."""
        env = env or self._env_a()
        version = env["cmc.plan.version"].create({
            "name": "WP15 baseline", "company_id": self.company_a.id, "date_from": B_FROM, "date_to": B_TO,
            "version_type": "baseline", "project_id": self.prj.id, "cmc_rate_driver_id": self.hour.id,
            "management_reserve_percent": 10.0, **vals,
        })
        card = rate_card if rate_card is not None else ((self.z, JAN, 50.0), (self.y, FEB, 80.0), (self.z, APR, 50.0))
        env["cmc.rate"]._cmc_publish(version, [{"holder": c, "driver": self.hour, "date": d, "value": v} for c, d, v in card])
        env["cmc.evm.estimate.line"].create([{**v, "version_id": version.id} for v in (self._estimate_vals() if estimates is None else estimates)])
        if risks:
            env["cmc.evm.risk"].create([
                {"version_id": version.id, "name": "Supplier delay", "task_id": self.t2.id, "probability": 40.0, "impact": 5000.0},
                {"version_id": version.id, "name": "Rework", "probability": 25.0, "impact": 4000.0},
                {"version_id": version.id, "name": "Cheaper steel", "probability": 50.0, "impact": -1200.0},
            ])
        if approve:
            version.action_approve()
        return version

    def _progress(self, values):
        for task, pct in values.items():
            task.sudo().cmc_progress = pct

    def _evm(self, baseline, job_run, status_date, progress=None, compute=True, env=None, **vals):
        if progress is not None:
            self._progress(progress)
        env = env or self._env_a()
        run = env["cmc.evm.run"].create({
            "name": "WP15 status %s" % status_date, "company_id": self.company_a.id, "project_id": self.prj.id,
            "baseline_id": baseline.id, "job_run_id": job_run.id, "status_date": status_date, "control_limit": 800.0,
            **vals,
        })
        if compute:
            run.action_compute()
        return run

    def _history(self, baseline, env=None):
        """Three monthly status reports; the progress changes between them like a real project."""
        jan = self._evm(baseline, self.job_jan, JAN_END, {self.t1: 50.0, self.t2: 0.0, self.t3: 0.0}, env=env)
        feb = self._evm(baseline, self.job_feb, FEB_END, {self.t1: 100.0, self.t2: 20.0}, env=env)
        mar = self._evm(baseline, self.job_mar, MAR_END, {self.t1: 100.0, self.t2: 50.0, self.t3: 0.0}, env=env)
        return jan, feb, mar
