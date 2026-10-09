"""WP-05 ``cmc_job_costing`` - job-order costing (commesse): FC-06, CEN-04 (AD-101/102/106/108/113/118).

CONTRACT (binding for the developer; tests are read-only)
=========================================================
Module ``cmc_job_costing``, depends ``["cmc_hr_timesheet"]`` only (AD-102: rates are READ from ``cmc.rate``,
never from the producer modules). Every model below has ``company_id``, ``_check_company_auto = True`` and a
global ``ir.rule`` ``[('company_id', 'parent_of', company_ids)]`` (AD-115). ACL: ``cmc_base.group_cmc_user``
read/write/create on the run and on ``cmc.job.rate``, READ-ONLY on the result models; ``group_cmc_manager``
full; no access without a CMC group (AccessError). WP-15 (EVM) will be added to this same module (AD-101):
``cmc.plan.version.version_type`` gets ``("baseline", "Baseline")`` via ``selection_add`` here (AD-104).

Project (``project.project``, ``_inherit``) - the job
- ``cmc_job_closed`` Boolean + ``cmc_job_close_date`` Date (close-to-FG flag/date). Closed without a date ->
  ValidationError. Only ``group_cmc_manager`` (or superuser) may write these two fields -> AccessError otherwise
  (even for a project manager). ``action_cmc_job_sheets()`` -> window action on ``cmc.job.sheet`` whose domain
  is ``[('project_id', '=', project.id)]`` (AD-103: link from the project, the core profitability panel stays).

CEN-04 resource-center rate table ``cmc.job.rate`` (``_inherit = ["cmc.month.mixin"]``: ``date`` = first day
of the month)
- ``company_id``, ``center_id`` M2o ``account.analytic.account`` (required), ``category_id`` M2o
  ``hr.employee.category`` (optional = every employee of the center), ``date``,
  ``rate_mode`` Selection ``manual`` (default) / ``computed``,
  ``hourly_rate`` Float (manual: as entered; computed: stored compute
  ``(special_cost + common_cost_share) / net_hours``), ``special_cost``, ``common_cost_share``, ``gross_hours``,
  ``absence_hours`` Float, ``net_hours`` = ``gross_hours - absence_hours`` (stored compute).
  ``computed`` with ``net_hours <= 0`` -> ValidationError. Negative ``hourly_rate`` -> ValidationError.
- UniqueIndex (company, center, COALESCE(category, 0), date) -> IntegrityError.
- ``action_cmc_load_center_cost()``: ``special_cost`` = cost (positive, AD-112) of the NON-excluded analytic
  lines of ``center_id`` (any plan column, plan-aware) dated in the month, read with ``_cmc_cost_read_group``.

Run ``cmc.job.cost.run`` (``_inherit = ["cmc.run.mixin"]``, AD-113) - one job cost sheet per project and period
- Inputs (``_cmc_input_fields``, locked when computed): ``name``, ``company_id``, ``date_from``, ``date_to``
  (to < from -> ValidationError), ``project_ids`` M2m ``project.project`` (empty = every project of the company
  with job activity in the period = stamped cost lines or timesheets), ``labour_rate_source`` ``table``
  (default) / ``registry``, ``labour_driver_id`` M2o ``cmc.cost.driver`` (required for ``registry`` ->
  ValidationError), ``overhead_source`` ``manual`` (default) / ``registry``, ``overhead_base`` ``hours``
  (default) / ``direct_cost``, ``overhead_rate`` Float (manual; per hour, or per currency unit of prime cost;
  negative -> ValidationError), ``overhead_driver_id`` M2o ``cmc.cost.driver`` (required for ``registry``),
  ``overhead_center_ids`` M2m ``account.analytic.account`` (the overhead pool). ``overhead_source =
  'registry'`` with ``overhead_base = 'direct_cost'`` -> ValidationError (registry rates are per center hour).
- Results (``_cmc_result_fields`` = ``sheet_ids``, ``labour_ids``; outputs cleared on reset):
  ``overhead_applied`` (= sum of sheets), ``overhead_actual``, ``overhead_variance`` = applied - actual
  (> 0 over-applied, < 0 under-applied), ``wip_total`` / ``completed_total`` (sum of ``job_cost`` of the sheets
  by status; ``wip_total + completed_total`` = sum of ``job_cost``, INT-05 conservation).
- ``cmc.job.sheet`` (``cmc.run.result.mixin``), one per project: ``run_id``, ``company_id``, ``project_id``,
  ``cost_object_id`` (``cmc.cost.object`` type ``project``), ``hours``, ``direct_material_cost`` (element
  ``cmc_base.cmc_element_mp``), ``other_direct_cost`` (any other element), ``direct_cost`` (= both),
  ``labour_cost``, ``prime_cost`` (= direct + labour), ``overhead_base_qty``, ``overhead_applied``,
  ``job_cost`` (= prime + overhead), ``revenue``, ``margin`` (= revenue - job_cost), ``margin_percent``
  (margin / revenue x 100, 0 without revenue), ``job_status`` ``wip`` / ``completed``, ``close_date``.
- ``cmc.job.sheet.labour`` (result), grain center x category x month: ``run_id``, ``sheet_id``, ``center_id``,
  ``category_id`` (the category of the table row used, False for a center-level row or a registry rate),
  ``date`` (month), ``hours``, ``rate``, ``amount``, ``rate_origin`` ``table`` / ``registry``.
Compute (one engine, set-based, AD-114):
- Direct costs = cost (-amount, AD-005/112) of the non-excluded analytic lines dated in the period, stamped with
  the job cost object (AD-106), EXCLUDING timesheet lines (``project_id`` set): hours are valued at the
  resource rate, never at the employee cost (no double count).
- Hours = ``unit_amount`` of the timesheet lines stamped with the job cost object in the period; the center of
  an hour is the ``cmc_center_id`` of the line. Labour = sum over center x month of hours x rate:
  ``table``: the ``cmc.job.rate`` of (center, month) whose ``category_id`` is one of the employee's
  ``category_ids``, else the center-level row (no category); two matching category rows -> UserError naming the
  employee; ``registry``: ``cmc.rate._cmc_lookup(center, labour_driver_id, month)`` (run-published rates only).
  No rate -> UserError naming the center (never a silent fallback to ``hr.employee.hourly_cost``).
- Overhead applied: ``manual``: ``overhead_rate x base`` (base = hours, or prime cost); ``registry``: sum over
  center x month of hours x ``_cmc_lookup(center, overhead_driver_id, month)`` (missing -> UserError).
- Overhead actual = cost of the INDIRECT (``cmc_directness = 'indirect'``) non-excluded analytic lines of
  ``overhead_center_ids`` dated in the period.
- Revenue = GL revenue of the period (AD-111 ``cmc_account`` reader rules: posted, income accounts,
  ``-balance``, credit notes net) of the journal items whose ``analytic_distribution`` holds the project
  analytic account (``project.account_id``), weighted by its percentage (a 40% share gives 40% of the line).
- Status: ``completed`` when the project is closed with ``cmc_job_close_date <= date_to`` (``close_date``
  snapshotted), else ``wip``. CMC never posts WIP/FG entries (AD-111 inv. 4, AD-118 skip).
- Registry rates read -> ``_cmc_record_usage`` of their origins (AD-002/108): the producer cannot be reset
  while the job run is computed. With ``labour_rate_source = 'table'`` and a ``labour_driver_id``, Compute
  publishes (``cmc.rate._cmc_publish``) the center-level table rates it applied (holder = center, driver =
  ``labour_driver_id``, month, component ``total``); reset/unlink removes them (mixin).
- Runs as a CMC user: reads lines/timesheets/GL/projects in sudo inside the run company (AD-115).
- PDF ``cmc_job_costing.action_report_job_cost_sheet`` (qweb-pdf, model ``cmc.job.cost.run``, bound to it).

Fixture (company A = ``company_data``, March 2026; hand-computed numbers)
  Plans: root project plan (``account_id``) holds the job analytics AA1/AA2/AA3/AA-OH; a second plan "WP05
  centers" holds the final centers Z, Y, OH (plan-aware ``cmc_center_id``).
  Employees: Anna (category Senior, cost 30/h), Bruno (no category, 25/h). Rates (cmc.job.rate, March):
  Z/- 40, Z/Senior 55, Y/- 60; April Z/- 42.
  J1 "Commessa Alfa" (AA1, center Z, open): hours Anna 10 Z, Bruno 20 Z, Bruno 5 Y (= 35 h; labour
  550 + 800 + 300 = 1650); direct MP 1000 + subcontracting 200 = 1200; revenue 5000 - 500 (credit note)
  + 40% of 1000 = 4900. Distractors: April MP 999, April Bruno 4 h Z, unstamped AA1 cost 777, draft invoice 9999,
  April invoice 3000.
  J2 "Commessa Beta" (AA2, center Z, closed 2026-03-25): Bruno 8 h Z (320); MP 400; revenue 60% of 1000 +
  1500 = 2100.
  J3 "Commessa Gamma" (AA3): no activity in March.
  Overhead pool OH: indirect 700 fixed + 50 variable in March (= 750), April 999 distractor.
  Manual overhead 15/h on hours: J1 525 -> job cost 3375, margin 1525 (31.12%); J2 120 -> 840, margin 1260
  (60%); applied 645 vs actual 750 -> variance -105 (under-applied). On prime cost at 0.25: J1 712.50,
  J2 180 -> applied 892.50, variance +142.50 (over-applied).
Company B (``setup_other_company``): project JB (AB, center ZB), employee Carla 10/h, 3 h on ZB at rate 70
  (210), MP 90 -> job cost 300 with overhead 0.
"""

from datetime import date

from odoo import Command

from odoo.addons.account.tests.common import AccountTestInvoicingCommon

D_FROM = date(2026, 3, 1)
D_TO = date(2026, 3, 31)
D_MID = date(2026, 3, 15)
D_APR = date(2026, 4, 2)

REPORT_XMLID = "cmc_job_costing.action_report_job_cost_sheet"


class JobCase(AccountTestInvoicingCommon):
    @classmethod
    def get_default_groups(cls):
        # why: AccountTestInvoicingCommon runs as `accountman`; the GL reader is CMC-gated
        return super().get_default_groups() | cls.env.ref("cmc_base.group_cmc_manager")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_a = cls.company_data["company"]
        cls.company_data_b = cls.setup_other_company(name="CMC WP05 B")
        cls.company_b = cls.company_data_b["company"]
        # why su: projects, employees and timesheets are master data of other apps, not the subject here
        cls.su = cls.env(su=True, context=dict(cls.env.context, allowed_company_ids=cls.company_a.ids))
        cls.su_b = cls.env(su=True, context=dict(cls.env.context, allowed_company_ids=cls.company_b.ids))
        cls.mp = cls.env.ref("cmc_base.cmc_element_mp")
        cls.sub = cls.su["cmc.cost.element"].create(
            {"name": "WP05 Subcontracting", "code": "WP05SUB", "cmc_kind": "real", "company_id": False}
        )
        cls.project_plan = (
            cls.su["account.analytic.plan"]
            .search([("parent_id", "=", False)])
            .filtered(lambda p: p._column_name() == "account_id")[:1]
        )
        cls.center_plan = cls.su["account.analytic.plan"].create({"name": "WP05 centers"})
        cls.ccol = cls.center_plan._column_name()
        # ---- company A
        su = cls.su
        cls.z = cls._center(su, "WP05 Z", cls.company_a)
        cls.y = cls._center(su, "WP05 Y", cls.company_a)
        cls.oh = cls._center(su, "WP05 OH", cls.company_a)
        cls.aa1 = cls._job_analytic(su, "WP05 AA1", cls.company_a)
        cls.aa2 = cls._job_analytic(su, "WP05 AA2", cls.company_a)
        cls.aa3 = cls._job_analytic(su, "WP05 AA3", cls.company_a)
        cls.aa_oh = cls._job_analytic(su, "WP05 AA-OH", cls.company_a)
        cls.j1 = cls._project(su, "Commessa Alfa", cls.aa1, cls.z, cls.company_a)
        cls.j2 = cls._project(su, "Commessa Beta", cls.aa2, cls.z, cls.company_a)
        cls.j3 = cls._project(su, "Commessa Gamma", cls.aa3, cls.z, cls.company_a)
        cls.j2.write({"cmc_job_closed": True, "cmc_job_close_date": date(2026, 3, 25)})
        cls.obj_j1, cls.obj_j2, cls.obj_j3 = su["cmc.cost.object"]._cmc_ensure_objects(
            cls.j1 | cls.j2 | cls.j3, company=cls.company_a, object_type="project"
        )
        cls.senior = su["hr.employee.category"].create({"name": "WP05 Senior"})
        cls.anna = cls._employee(su, "WP05 Anna", 30.0, cls.company_a, categories=cls.senior)
        cls.bruno = cls._employee(su, "WP05 Bruno", 25.0, cls.company_a)
        cls.rate_z = cls._rate(su, cls.z, 40.0)
        cls.rate_z_senior = cls._rate(su, cls.z, 55.0, category=cls.senior)
        cls.rate_y = cls._rate(su, cls.y, 60.0)
        cls.rate_z_apr = cls._rate(su, cls.z, 42.0, day=D_APR)
        # J1
        cls._ts(su, cls.j1, cls.anna, 10)
        cls._ts(su, cls.j1, cls.bruno, 20)
        cls._ts(su, cls.j1, cls.bruno, 5, center=cls.y)
        cls._ts(su, cls.j1, cls.bruno, 4, day=D_APR)
        cls._direct(su, cls.aa1, cls.obj_j1, 1000, cls.mp)
        cls._direct(su, cls.aa1, cls.obj_j1, 200, cls.sub)
        cls._direct(su, cls.aa1, cls.obj_j1, 999, cls.mp, day=D_APR)
        # an analytic cost on the job account WITHOUT the cost-object stamp: the stamp is the key (AD-106)
        su["account.analytic.line"].create(
            {"name": "unstamped", "account_id": cls.aa1.id, "amount": -777.0, "company_id": cls.company_a.id,
             "cmc_directness": "direct", "cmc_behavior": "variable", "date": D_MID}
        )
        # J2
        cls._ts(su, cls.j2, cls.bruno, 8)
        cls._direct(su, cls.aa2, cls.obj_j2, 400, cls.mp)
        # overhead pool
        cls._indirect(su, cls.oh, 700, "fixed")
        cls._indirect(su, cls.oh, 50, "variable")
        cls._indirect(su, cls.oh, 999, "fixed", day=D_APR)
        # revenue (GL)
        cls._invoice(cls.company_data, [(5000.0, {cls.aa1: 100})], date(2026, 3, 10))
        cls._invoice(cls.company_data, [(500.0, {cls.aa1: 100})], date(2026, 3, 20), move_type="out_refund")
        cls._invoice(cls.company_data, [(1000.0, {cls.aa1: 40, cls.aa2: 60})], date(2026, 3, 12))
        cls._invoice(cls.company_data, [(1500.0, {cls.aa2: 100})], date(2026, 3, 18))
        cls._invoice(cls.company_data, [(9999.0, {cls.aa1: 100})], date(2026, 3, 19), post=False)
        cls._invoice(cls.company_data, [(3000.0, {cls.aa1: 100})], date(2026, 4, 3))
        # ---- company B
        su = cls.su_b
        cls.zb = cls._center(su, "WP05 ZB", cls.company_b)
        cls.ab = cls._job_analytic(su, "WP05 AB", cls.company_b)
        cls.jb = cls._project(su, "Commessa B", cls.ab, cls.zb, cls.company_b)
        cls.obj_jb = su["cmc.cost.object"]._cmc_ensure_objects(cls.jb, company=cls.company_b, object_type="project")
        cls.carla = cls._employee(su, "WP05 Carla", 10.0, cls.company_b)
        cls._rate(su, cls.zb, 70.0)
        cls._ts(su, cls.jb, cls.carla, 3)
        cls._direct(su, cls.ab, cls.obj_jb, 90, cls.mp)

    # ---- builders -----------------------------------------------------------------------------------

    @classmethod
    def _center(cls, env, name, company):
        return env["account.analytic.account"].create(
            {"name": name, "plan_id": cls.center_plan.id, "cmc_center_type": "final", "company_id": company.id}
        )

    @classmethod
    def _job_analytic(cls, env, name, company):
        return env["account.analytic.account"].create(
            {"name": name, "plan_id": cls.project_plan.id, "company_id": company.id}
        )

    @classmethod
    def _project(cls, env, name, analytic, center, company):
        return env["project.project"].create(
            {"name": name, "account_id": analytic.id, cls.ccol: center.id, "company_id": company.id,
             "allow_timesheets": True}
        )

    @classmethod
    def _employee(cls, env, name, cost, company, categories=None):
        vals = {"name": name, "hourly_cost": cost, "company_id": company.id}
        if categories:
            vals["category_ids"] = [Command.set(categories.ids)]
        return env["hr.employee"].create(vals)

    @classmethod
    def _rate(cls, env, center, value, category=None, day=D_MID, **vals):
        row = {"center_id": center.id, "category_id": category.id if category else False, "date": day,
               "company_id": center.company_id.id, **vals}
        if vals.get("rate_mode") != "computed":
            # a computed rate is never entered: passing it would override the compute
            row["hourly_rate"] = value
        return env["cmc.job.rate"].create(row)

    @classmethod
    def _ts(cls, env, project, employee, hours, center=None, day=D_MID):
        # the real producer: hr_timesheet values the line, cmc_hr_timesheet stamps the project cost object
        vals = {"name": "work", "project_id": project.id, "employee_id": employee.id, "unit_amount": hours,
                "date": day}
        if center:
            vals[cls.ccol] = center.id
        return env["account.analytic.line"].create(vals)

    @classmethod
    def _direct(cls, env, analytic, cost_object, cost, element, day=D_MID):
        """A direct cost of the job (vendor bill / stock issue) as a classification rule stamps it: the row
        is the same whoever set the stamp (AD-106)."""
        return env["account.analytic.line"].create(
            {"name": "direct", "account_id": analytic.id, "amount": -cost, "company_id": analytic.company_id.id,
             "cmc_cost_element_id": element.id, "cmc_cost_object_id": cost_object.id,
             "cmc_directness": "direct", "cmc_behavior": "variable", "date": day}
        )

    @classmethod
    def _indirect(cls, env, center, cost, behavior, day=D_MID):
        return env["account.analytic.line"].create(
            {"name": "overhead", "account_id": cls.aa_oh.id, cls.ccol: center.id, "amount": -cost,
             "company_id": center.company_id.id, "cmc_behavior": behavior, "cmc_directness": "indirect",
             "date": day}
        )

    @classmethod
    def _invoice(cls, data, lines, day, move_type="out_invoice", post=True):
        move = cls.env["account.move"].with_company(data["company"]).create({
            "move_type": move_type,
            "partner_id": cls.partner_a.id,
            "invoice_date": day,
            "date": day,
            "journal_id": data["default_journal_sale"].id,
            "invoice_line_ids": [
                Command.create({
                    "name": "job revenue",
                    "quantity": 1.0,
                    "price_unit": amount,
                    "account_id": data["default_account_revenue"].id,
                    "tax_ids": [Command.clear()],
                    "analytic_distribution": {str(aa.id): pct for aa, pct in distribution.items()},
                })
                for amount, distribution in lines
            ],
        })
        if post:
            move.action_post()
        return move

    # ---- runs ---------------------------------------------------------------------------------------

    def _env_a(self, user=None):
        return self.env(user=user or self.env.user, context=dict(self.env.context, allowed_company_ids=self.company_a.ids))

    def _run(self, compute=True, env=None, **vals):
        env = env or self._env_a()
        run = env["cmc.job.cost.run"].create({
            "name": "WP05 March",
            "company_id": self.company_a.id,
            "date_from": D_FROM,
            "date_to": D_TO,
            "overhead_rate": 15.0,
            "overhead_base": "hours",
            "overhead_center_ids": [Command.set(self.oh.ids)],
            **vals,
        })
        if compute:
            run.action_compute()
        return run

    @staticmethod
    def _sheet(run, project):
        return run.sheet_ids.filtered(lambda s: s.project_id == project)
