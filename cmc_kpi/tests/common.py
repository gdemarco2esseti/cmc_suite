"""WP-14 ``cmc_kpi`` - KPI library, non-financial measures, balanced scorecard, monthly management pack.

Capabilities: KPI-01 (library: source, target per version, thresholds, traffic light, trend; incl. the EUR/h
center coefficient), KPI-02 (non-financial measures from feeders: scrap, yield, cycle time, OTD; MCE formula),
KPI-03 (BSC perspectives, weights = 100 per level, cascade parent/child, owner = responsibility center, links to
activities), KPI-04 (monthly management pack PDF: KPI table + BSC + BvA summary + key ratios).
Sources: gap register rows KPI-01..04; Kaplan-Cooper Ch4 (MCE = processing / (processing + inspection + move +
wait), first-pass yield, OTD), Ch5; Turney Ch4-5, Ch7 (measures per activity, target, trend), Ch14-15 (BSC,
activities tagged with the perspective); Fazzini 2.1.1, 13.2 (ratio dashboard with traffic light vs threshold).
Architecture: AD-101 (new module), AD-102 (reads ``cmc_budget`` + ``cmc_fin_analysis``; measures from the AD-107
catalogue, rates from the AD-108 registry, never from the producer modules), AD-104 (targets on a
``cmc.plan.version``), AD-105 (month grain), AD-107 (feeders = ``_cmc_feeder_profiles``), AD-113 (the measurement
is a run: snapshot, locked, reset deletes, AD-002 usages), AD-114 (grouped reads only), AD-115 (security),
AD-118 (WP-14 must: no spreadsheet dashboards, no real-time push).

CONTRACT (binding for the developer; tests are read-only)
=========================================================
Module ``cmc_kpi``, version ``19.0.1.0.0``, depends ``["cmc_budget", "cmc_fin_analysis", "stock"]`` (``stock``:
the KPI-02 feeders read ``stock.scrap`` / ``stock.picking``; see the report to the orchestrator). Every model has
``company_id`` (results/lines: related to the run/parent, stored), ``_check_company_auto = True``, and a global
``ir.rule`` ``[('company_id', 'parent_of', company_ids)]`` OUTSIDE noupdate.

1. KPI-02 feeders (``cmc.measure.quantity._cmc_feeder_profiles`` extended with ``super()``) and shipped measures
   (``cmc.cost.driver``, ``measure_kind = "statistical"``, ``source = "feeder"``, ``company_id`` False, noupdate):
   - ``cmc_kpi.cmc_measure_scrap_qty`` (feeder_key ``stock_scrap_qty``): ``stock.scrap`` in state ``done``,
     ``date_done`` month, sum of ``scrap_qty`` (``uom_field`` ``product_uom_id``), ``object_field`` ``product_id``.
   - ``cmc_kpi.cmc_measure_shipments`` (``stock_otd_shipped``): count of ``stock.picking`` ``done`` with
     ``picking_type_code = "outgoing"``, ``date_done`` month (receipts / internal / cancelled ignored).
   - ``cmc_kpi.cmc_measure_shipments_on_time`` (``stock_otd_on_time``): same domain AND ``cmc_on_time``.
   - ``stock.picking.cmc_on_time`` Boolean, computed STORED (False while not done), so the feeder stays a
     ``_read_group`` (query count independent of the number of pickings); rule in RN-001 below.
   - Both shipment measures exclude returns (``return_id`` set): a return to supplier is an outgoing transfer
     but not a delivery (RN-001).
   OTD % = KPI on ``shipments_on_time`` / ``shipments`` x 100; yield % = formula good / (good + scrap) x 100
   (good = produced units: the ``cmc_mrp`` produced measure when installed, or any manual measure).

2. ``cmc.kpi`` (KPI library, configuration): ``name`` (translate), ``code`` Char req (``str.isidentifier()`` else
   ValidationError; ``models.UniqueIndex`` (company_id, code) -> IntegrityError), ``company_id`` req (default env
   company), ``active``, ``sequence``, ``description``, ``unit`` Selection ``number`` (default) / ``percent`` /
   ``amount`` / ``days``, ``perspective`` Selection ``financial`` / ``customer`` / ``internal`` / ``learning``
   (default financial; labels "Financial", "Customer", "Internal Process", "Learning and Growth"),
   ``direction`` ``higher`` (default) / ``lower`` (is better), ``target_value`` Float (static target, default 0),
   ``amber_tolerance_pct`` Float (default 0.0), ``red_tolerance_pct`` Float (default 10.0; < amber ->
   ValidationError), ``responsibility_id`` (cmc.responsibility.center, check_company; the KPI owner),
   ``owner_id`` related ``responsibility_id.owner_id``, ``parent_id`` / ``child_ids`` (cascade),
   ``cascade_weight`` Float (weight of this KPI in its parent, default 0), ``activity_ref`` Reference whose
   selection lists ``cmc.abc.activity`` when that model is in the registry (links to activities, Turney Ch14),
   ``source_type`` Selection req:
   - ``measure``: ``driver_id`` req, ``aggregation`` ``sum`` (default) / ``avg``, optional filters ``center_id``
     / ``cost_object_id``, optional ``denominator_driver_id``, ``scale`` Float (default 1.0).
     value = agg(ACTUAL facts ``cmc.measure.quantity`` (version_id False) of the run company, month, driver,
     filters) [/ agg(facts of denominator_driver_id, same filters)] x scale. avg = mean of the fact ROWS;
     denominator 0 -> not computable. DL-010: NO fact of the driver in the month (sum or avg) -> status
     ``no_data``, computable False, score 0, reason "No data for the month".
   - ``ratio``: ``ratio_code`` Char req = a ``cmc.fin.ratio`` code. value = ``cmc.fin.ratio.value`` of the
     year of the month from the run ``fin_analysis_id`` or, when empty, the latest (id desc) COMPUTED
     ``cmc.fin.analysis`` of the run company with year_from <= year <= year_to; its usage is recorded (AD-002).
     No run / no row / status ``not_computable`` -> not computable.
   - ``variance``: ``variance_version_id`` (budget/forecast ``cmc.plan.version``, check_company) req,
     ``center_id`` req, optional ``element_id``, ``variance_basis`` ``month`` (default) / ``ytd``. value =
     ``variance_<basis>_pct`` of the row of ``variance_version_id._cmc_bva_summary(last day of month, groupby)``
     with groupby ``("center_id", "element_id")`` when ``element_id`` is set else ``("center_id",)`` (natural
     sign: positive = favourable, AD-112). No row or plan 0 -> not computable.
   - ``formula``: ``formula`` Char req (validated with ``odoo.tools.safe_eval.test_python_expr`` -> ValidationError),
     names = codes of the other active KPIs of the run; evaluated with ``safe_eval`` after the KPIs it reads.
     Compute checks BEFORE writing any row (RN-003 style): an unknown name -> UserError naming the KPI code and
     the name; a cycle -> UserError naming the KPIs. A name that is not computable that month, or
     ZeroDivisionError -> not computable (never a silent 0).
   - ``hook``: ``hook_key`` Char req. ``cmc.kpi.run._cmc_kpi_evaluators()`` returns ``{key: callable(run, kpis,
     month) -> {kpi.id: float | None}}`` (bridges extend it with ``super()``; called ONCE per key and month with
     all the KPIs of that key; None = not computable). Unknown key -> UserError naming the KPI and the key.
     Shipped key ``cmc_rate``: the ``cmc.rate`` of (``center_id``, ``driver_id``, month, company) read with the
     ``_cmc_lookup`` semantics (run-published only; EUR/h center coefficient, KPI-01) in ONE batch; missing ->
     not computable; the origin runs of the rates read are recorded as usages (AD-002).
   - ``cascade``: value = sum(child score x cascade_weight) / sum(cascade_weight) over the COMPUTABLE children of
     the month (none -> not computable); target fixed at 100, direction higher. Children ``cascade_weight`` not
     summing to 100 (0.01 tolerance) -> UserError at compute naming the parent.
   Missing source field for the type -> ValidationError.

3. ``cmc.kpi.target`` (``_inherit = ["cmc.month.mixin", "cmc.run.line.mixin"]``, ``_cmc_run_field = "version_id"``:
   an approved version refuses create/write/unlink -> UserError): ``version_id`` req (cascade), ``company_id``
   related stored, ``kpi_id`` req (check_company), ``date`` (month), ``target_value``. A date outside the version
   span -> ValidationError. ``cmc.plan.version.kpi_target_ids`` O2m (copy=True).

4. ``cmc.kpi.run`` (``cmc.run.mixin``; AD-113): ``name``, ``company_id``, ``date_from`` / ``date_to`` (months of
   the span; to < from -> ValidationError), ``version_id`` (target version, optional), ``fin_analysis_id``
   (optional), ``bva_version_id`` (optional, pack BvA summary), ``kpi_ids`` M2m (empty = every active KPI of the
   company), ``scorecard_ids`` M2m (empty = every active scorecard of the company). Results ``value_ids`` ->
   ``cmc.kpi.value`` and ``scorecard_result_ids`` -> ``cmc.kpi.scorecard.result`` (result mixin, written by
   Compute only, deleted by reset). Snapshot: a later fact/posting changes nothing until reset + compute.
   ``cmc.kpi.value``: ``run_id``, ``company_id``, ``kpi_id``, ``code`` / ``perspective`` / ``responsibility_id``
   (related stored), ``date`` (month), ``value``, ``computable``, ``target``, ``target_source`` ``version`` /
   ``static``, ``gap_pct``, ``status`` ``green`` / ``amber`` / ``red`` / ``none``, ``score``,
   ``previous_value``, ``trend_delta``, ``trend`` ``improving`` / ``worsening`` / ``stable`` / ``none``.
   One row per KPI x month (not computable included: value 0, status none, score 0, trend none).
   DL-010: ``status`` also ``no_data`` ("No data"): a measure KPI without facts in the month, or a value 0 against
   a target 0 -> ``no_data``, score 0, computable False, reason "No data for the month"; a real value against a
   0 target (variance KPIs) keeps the traffic light below.
   - target = ``cmc.kpi.target`` of (run version, kpi, month) -> ``version``, else ``kpi.target_value`` -> ``static``.
   - gap_pct = (value - target) / abs(target) x 100 (higher) or (target - value) / abs(target) x 100 (lower);
     target 0 -> the signed difference itself (value - target, or target - value). Positive = better.
   - status: gap >= -amber_tolerance -> green; gap >= -red_tolerance -> amber; else red.
   - score = min(max(100 + gap_pct, 0), 150).
   - previous_value = value of the previous month: the row of this run, else the latest computed
     ``cmc.kpi.value`` (another run, same company) of (kpi, month - 1); none -> trend ``none``.
     trend_delta = value - previous_value; trend improving / worsening by the sign of delta x direction
     (abs(delta) < 1e-6 -> stable).
   ``cmc.kpi.scorecard.result``: ``run_id``, ``company_id``, ``scorecard_id``, ``date``, ``perspective``
   (False = overall row), ``weight``, ``score``, ``computable``. One row per scorecard x month x weighted
   perspective + one overall row. Perspective score = sum(w x score) / sum(w) over its COMPUTABLE KPIs;
   overall = same over the computable perspectives with their weights (renormalised); none -> computable False.

5. ``cmc.kpi.scorecard`` (configuration): ``name``, ``company_id``, ``active``, ``responsibility_id``,
   ``perspective_ids`` -> ``cmc.kpi.scorecard.perspective`` (``scorecard_id``, ``perspective``, ``weight``),
   ``line_ids`` -> ``cmc.kpi.scorecard.line`` (``scorecard_id``, ``kpi_id`` check_company, ``perspective``
   related stored, ``weight``). At compute: perspective weights summing != 100, or the KPI weights of a
   weighted perspective summing != 100 (0.01 tolerance), or a KPI line whose perspective is not weighted ->
   UserError naming the scorecard.

6. KPI-04 ``cmc.kpi.run._cmc_pack_data(month=None)`` (ensure_one; default month = month of ``date_to``) ->
   ``{"month": date, "kpis": [{"kpi", "value", "computable", "target", "status", "trend", "score"}] (by
   perspective order, sequence, code), "scorecards": [{"scorecard", "overall", "perspectives": [{"perspective",
   "label", "weight", "score", "computable"}]}], "bva": bva_version_id._cmc_bva_summary(last day of month,
   ("center_id",)) or [], "ratios": [{"code", "name", "value", "status"}] of the fin analysis used for the year
   of the month or []}``. Report ``cmc_kpi.action_report_cmc_kpi_pack`` (qweb-pdf, model + binding
   ``cmc.kpi.run``) prints KPI names and traffic lights, scorecard names, BvA center names, ratio names.
   Query count of compute and pack independent of the number of facts / pickings (AD-114).
   The "bva" and "ratios" blocks are read from the run's own snapshot rows (RN-002), never live.

7. Security (ACL r,w,c,u): ``cmc.kpi`` / ``cmc.kpi.scorecard`` (+ lines, perspectives): responsible 1000 (kpi
   only), user 1000, manager 1111; ``cmc.kpi.target``: user 1111, manager 1111; ``cmc.kpi.run``: user 1110,
   manager 1111; ``cmc.kpi.value``: responsible 1000, user 1000, manager 1000; ``cmc.kpi.scorecard.result``:
   user 1000, manager 1000. Rules for ``group_cmc_responsible``: ``cmc.kpi`` and ``cmc.kpi.value`` whose
   ``responsibility_id.owner_id = user``; ``group_cmc_user``: all. Internal user without CMC group: AccessError.

8. Amendments from the ronin review (binding; they override the points above where they differ)
   RN-001 OTD. ``cmc_on_time`` = done AND day(``date_done``) <= day(``date_deadline`` or ``scheduled_date``),
     both days taken in the company timezone (``company_id.partner_id.tz``, UTC when empty). ``@api.depends``
     covers ``state``, ``date_done``, ``date_deadline``, ``scheduled_date``. A transfer created with the default
     scheduled date and validated seconds later is on time; the deadline, when set, wins over the scheduled
     date (later or earlier). Shipments (both measures) = done, outgoing, ``return_id`` False.
   RN-002 snapshot + usages. At compute the run copies into its own result rows (result mixin, deleted by reset,
     both in ``_cmc_result_fields``):
     - ``bva_ids`` -> ``cmc.kpi.run.bva`` (``run_id``, ``company_id`` related stored, ``date`` month,
       ``center_id``, ``plan_month``, ``actual_month``, ``variance_month``, ``variance_month_pct``, ``plan_ytd``,
       ``actual_ytd``, ``variance_ytd``, ``variance_ytd_pct``): one row per month of the span x row of
       ``bva_version_id._cmc_bva_summary(last day of month, ("center_id",))``;
     - ``ratio_ids`` -> ``cmc.kpi.run.ratio`` (``run_id``, ``company_id``, ``fin_analysis_id``, ``year``,
       ``code``, ``name``, ``value``, ``status``): every ``cmc.fin.ratio.value`` of each year of the span of the
       analysis used = ``fin_analysis_id``, else the analysis the ratio KPIs read for that year (none -> no row).
     ``_cmc_pack_data`` builds "bva" (dicts with the keys above, ``center_id`` a record) and "ratios" ONLY from
     these rows: a later posting / ratio change leaves the pack of a computed run as it was.
     Usages (AD-002, ``_cmc_record_usage``) recorded at compute on: ``fin_analysis_id`` (always, when set) and
     every analysis read; ``bva_version_id``; every ``variance_version_id`` read; ``version_id`` when approved
     (a draft target version stays allowed and records nothing). A ``variance`` KPI whose
     ``variance_version_id`` is draft -> UserError at compute naming the KPI code (checked before any row).
     The fixture budget is therefore APPROVED.
   RN-003 inputs. The KPIs a selected formula names (codes of active KPIs) and the children of a selected
     cascade are added to the run, transitively (rows written for them too). An ARCHIVED KPI named by an active
     formula of the run -> UserError naming the formula code and the archived code (before any row).
     ``cmc.kpi.value.reason`` Char: empty when computable; otherwise why, naming the KPI code whose data is
     missing (e.g. "no data for otd"; for a formula: the code of the input that is not computable).

Fixture (company A = env company, 2026; hand numbers in each test module docstring)
  good units (manual facts): Jan 500 + 450 = 950, Feb 900, Mar 980. scrap (stock.scrap done, real feeder): Jan
  50, Feb 100, Mar 20 -> yield 95.0 / 90.0 / 98.0. cycle time days (manual facts, avg): Jan 4.0 and 6.0 -> 5.0.
  Outgoing pickings done: Jan 4 (3 on time, 1 late), Feb 5 on time, Mar none (+ 1 receipt in Jan, ignored)
  -> OTD 75.0 / 100.0 / not computable. Financial analysis 2025-2026 from history: current_ratio 2025 = 4.0,
  2026 = 2.0. Budget 2026 center "KPI production", rent: plan 1,000 Jan/Feb/Mar, actual 900 / 1,200 / 1,050 ->
  BvA month +10% / -20% / -5%, YTD Feb -5% (budget approved, RN-002). Rate published by a computed closing run: center "KPI assembly"
  EUR/h (driver "KPI hours") Jan 45.0.
"""

from datetime import date, datetime

from freezegun import freeze_time

from odoo import Command
from odoo.tests.common import TransactionCase, new_test_user

from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE

JAN = date(2026, 1, 1)
FEB = date(2026, 2, 1)
MAR = date(2026, 3, 1)
DEC_2025 = date(2025, 12, 1)


def dt(month, day, hour=10):
    return datetime(2026, month, day, hour, 0)


class KpiCase(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        cls._users()
        cls._measures()
        cls._stock()
        cls._fin_analysis()
        cls._budget()
        cls._rates()

    # ---- fixture --------------------------------------------------------

    @classmethod
    def _users(cls):
        cls.manager = new_test_user(cls.env, login="kpi_manager", groups="base.group_user,cmc_base.group_cmc_manager")
        cls.user = new_test_user(cls.env, login="kpi_user", groups="base.group_user,cmc_base.group_cmc_user")
        cls.owner_ops = new_test_user(cls.env, login="kpi_owner_ops", groups="base.group_user,cmc_base.group_cmc_responsible")
        cls.owner_sales = new_test_user(cls.env, login="kpi_owner_sales", groups="base.group_user,cmc_base.group_cmc_responsible")
        cls.outsider = new_test_user(cls.env, login="kpi_outsider", groups="base.group_user")
        Resp = cls.env["cmc.responsibility.center"]
        cls.resp_ops = Resp.create({"name": "KPI operations", "owner_id": cls.owner_ops.id})
        cls.resp_sales = Resp.create({"name": "KPI sales", "owner_id": cls.owner_sales.id, "responsibility_type": "revenue"})

    @classmethod
    def _measures(cls):
        Driver = cls.env["cmc.cost.driver"]
        cls.good = Driver.create({"name": "KPI good units", "code": "KPIGOOD", "company_id": False, "measure_kind": "statistical"})
        cls.cycle = Driver.create({"name": "KPI cycle days", "code": "KPICYC", "company_id": False, "measure_kind": "statistical"})
        cls.hours = Driver.create({"name": "KPI hours", "code": "KPIHRS", "company_id": False})
        cls._fact(cls.good, JAN, 500.0)
        cls._fact(cls.good, JAN, 450.0)
        cls._fact(cls.good, FEB, 900.0)
        cls._fact(cls.good, MAR, 980.0)
        cls._fact(cls.cycle, JAN, 4.0)
        cls._fact(cls.cycle, JAN, 6.0)
        # a planned quantity is a plan, never an actual: must not be read
        cls.fact_version = cls.env["cmc.plan.version"].create(
            {"name": "KPI facts plan", "date_from": JAN, "date_to": date(2026, 12, 31), "version_type": "budget"}
        )
        cls._fact(cls.good, JAN, 99999.0, version_id=cls.fact_version.id)

    @classmethod
    def _fact(cls, driver, month, qty, **vals):
        return cls.env["cmc.measure.quantity"].create(
            {"driver_id": driver.id, "date": month, "quantity": qty, "company_id": cls.company.id, **vals}
        )

    @classmethod
    def _stock(cls):
        cls.warehouse = cls.env["stock.warehouse"].search([("company_id", "=", cls.company.id)], limit=1)
        cls.product = cls.env["product.product"].create({"name": "KPI widget", "type": "consu"})
        cls.customer = cls.env["res.partner"].create({"name": "KPI customer"})
        # Jan: 3 on time + 1 late; Feb: 5 on time; a receipt in Jan is not a delivery
        rows = [(dt(1, 10), dt(1, 8)), (dt(1, 10), dt(1, 10, 9)), (dt(1, 20), dt(1, 15)), (dt(1, 10), dt(1, 12))]
        rows += [(dt(2, 20), dt(2, d)) for d in (2, 5, 9, 12, 16)]
        cls.deliveries = cls._ship(rows)
        cls.receipt = cls._ship([(dt(1, 10), dt(1, 9))], picking_type=cls.warehouse.in_type_id)
        cls._scrap(50.0, dt(1, 14))
        cls._scrap(60.0, dt(2, 3))
        cls._scrap(40.0, dt(2, 18))
        cls._scrap(20.0, dt(3, 11))
        cls.env["cmc.measure.quantity"]._cmc_refresh_feeders(JAN, date(2026, 3, 31), company=cls.company)

    @classmethod
    def _ship(cls, rows, picking_type=None):
        """rows = [(scheduled_date, date_done[, date_deadline])]: real transfers, the dates set BEFORE validation
        and the validation run at ``date_done`` (frozen clock), never rewritten afterwards (RN-001)."""
        picking_type = picking_type or cls.warehouse.out_type_id
        src = picking_type.default_location_src_id or cls.env.ref("stock.stock_location_suppliers")
        dest = picking_type.default_location_dest_id or cls.env.ref("stock.stock_location_customers")
        pickings = cls.env["stock.picking"].create([{
            "picking_type_id": picking_type.id, "partner_id": cls.customer.id, "scheduled_date": row[0],
            "location_id": src.id, "location_dest_id": dest.id,
            "move_ids": [Command.create({
                "product_id": cls.product.id, "product_uom": cls.product.uom_id.id, "product_uom_qty": 1.0,
                "location_id": src.id, "location_dest_id": dest.id,
                **({"date_deadline": row[2]} if len(row) > 2 else {}),
            })],
        } for row in rows])
        pickings.action_confirm()
        for picking, row in zip(pickings, rows):
            cls._validate(picking, at=row[1])
        return pickings

    @classmethod
    def _validate(cls, picking, at=None):
        """Validate a transfer as a user would, the clock frozen at ``at`` (None = now)."""
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        if at is None:
            picking.button_validate()
            return picking
        with freeze_time(at):
            picking.button_validate()
        return picking

    @classmethod
    def _scrap(cls, qty, done):
        scrap = cls.env["stock.scrap"].create({"product_id": cls.product.id, "scrap_qty": qty})
        scrap.do_scrap()
        scrap.date_done = done
        return scrap

    @classmethod
    def _fin_analysis(cls):
        """History 2025 / 2026 mapped by the shipped type rules: current_ratio = assets_c / liab_c = 4.0 / 2.0."""
        Account = cls.env["account.account"].with_company(cls.company)
        accounts = {}
        for code, account_type in (("KPICASH", "asset_cash"), ("KPIREC", "asset_receivable"), ("KPIPAY", "liability_payable"),
                                   ("KPIEQ", "equity"), ("KPIREV", "income"), ("KPISER", "expense")):
            accounts[code] = Account.create({"code": code, "name": code, "account_type": account_type,
                                             "company_ids": [Command.set(cls.company.ids)]})
        history = {
            2025: {"KPICASH": 500, "KPIREC": 300, "KPIPAY": -200, "KPIEQ": -400, "KPIREV": -1000, "KPISER": 800},
            2026: {"KPICASH": 600, "KPIREC": 400, "KPIPAY": -500, "KPIEQ": -200, "KPIREV": -1200, "KPISER": 900},
        }
        cls.env["cmc.fin.history"].create([
            {"company_id": cls.company.id, "year": year, "account_id": accounts[code].id, "balance": balance}
            for year, rows in history.items() for code, balance in rows.items()
        ])
        cls.fin = cls.env["cmc.fin.analysis"].create(
            {"name": "KPI analysis", "company_id": cls.company.id, "year_from": 2025, "year_to": 2026}
        )
        cls.fin.action_compute()

    @classmethod
    def _budget(cls):
        cls.rent = cls.env["cmc.cost.element"].create({"name": "KPI rent", "code": "KPIRENT", "company_id": cls.company.id})
        cls.production = cls._center("KPI production")
        cls.budget = cls.env["cmc.plan.version"].create(
            {"name": "KPI budget 2026", "date_from": JAN, "date_to": date(2026, 12, 31), "version_type": "budget"}
        )
        cls.env["cmc.budget.line"].create([
            {"version_id": cls.budget.id, "kind": "cost", "center_id": cls.production.id, "element_id": cls.rent.id,
             "behavior": "fixed", "date": month, "figure": 1000.0}
            for month in (JAN, FEB, MAR)
        ])
        for day, amount in ((date(2026, 1, 10), 900.0), (date(2026, 2, 10), 1200.0), (date(2026, 3, 10), 1050.0)):
            cls._cost(cls.production, amount, day)
        # RN-002: a variance KPI / the pack read an approved budget only (and record their usage on it)
        cls.budget.action_approve()

    @classmethod
    def _cost(cls, center, amount, day):
        return cls.env["account.analytic.line"].create({
            "name": "KPI actual", "account_id": center.id, "amount": -amount, "date": day,
            "cmc_cost_element_id": cls.rent.id, "cmc_behavior": "fixed",
        })

    @classmethod
    def _rates(cls):
        cls.assembly = cls._center("KPI assembly")
        cls._cost(cls.assembly, 450.0, date(2026, 1, 12))
        cls.producer = cls.env["cmc.closing.run"].create(
            {"name": "KPI producer", "date_from": JAN, "date_to": date(2026, 1, 31), "method": "direct"}
        )
        cls.producer.action_compute()
        cls.env["cmc.rate"].with_context(cmc_run_state=CMC_RUN_STATE)._cmc_publish(
            cls.producer, [{"holder": cls.assembly, "driver": cls.hours, "date": JAN, "value": 45.0}]
        )

    @classmethod
    def _center(cls, name):
        return cls.env["account.analytic.account"].create(
            {"name": name, "plan_id": cls.plan.id, "cmc_center_type": "final", "company_id": cls.company.id}
        )

    # ---- helpers (cmc_kpi models: fail per test until they exist) -------

    def _kpi(self, code, source_type, **vals):
        return self.env["cmc.kpi"].create({"name": "KPI %s" % code, "code": code, "source_type": source_type, **vals})

    def _library(self):
        """The fixture library (one KPI per source type used by the scorecard)."""
        ref = self.env.ref
        k = {}
        k["good_qty"] = self._kpi("good_qty", "measure", driver_id=self.good.id, perspective="internal", unit="number")
        k["scrap_qty"] = self._kpi("scrap_qty", "measure", driver_id=ref("cmc_kpi.cmc_measure_scrap_qty").id,
                                   perspective="internal", direction="lower")
        k["yield_pct"] = self._kpi("yield_pct", "formula", formula="good_qty / (good_qty + scrap_qty) * 100",
                                   perspective="internal", unit="percent", target_value=90.0,
                                   amber_tolerance_pct=0.0, red_tolerance_pct=5.0, responsibility_id=self.resp_ops.id)
        k["cycle_time"] = self._kpi("cycle_time", "measure", driver_id=self.cycle.id, aggregation="avg",
                                    perspective="internal", unit="days", direction="lower", target_value=6.0,
                                    responsibility_id=self.resp_ops.id)
        k["otd"] = self._kpi("otd", "measure", driver_id=ref("cmc_kpi.cmc_measure_shipments_on_time").id,
                             denominator_driver_id=ref("cmc_kpi.cmc_measure_shipments").id, scale=100.0,
                             perspective="customer", unit="percent", target_value=95.0, red_tolerance_pct=10.0,
                             responsibility_id=self.resp_sales.id)
        k["current_ratio"] = self._kpi("current_ratio", "ratio", ratio_code="current_ratio", perspective="financial",
                                       unit="number", target_value=1.5)
        k["rent_bva"] = self._kpi("rent_bva", "variance", variance_version_id=self.budget.id,
                                  center_id=self.production.id, element_id=self.rent.id, perspective="financial",
                                  unit="percent", target_value=0.0, red_tolerance_pct=10.0)
        return k

    def _target_version(self, rows):
        """rows = [(kpi, month, target)] on a budget version of 2026."""
        version = self.env["cmc.plan.version"].create(
            {"name": "KPI targets 2026", "date_from": JAN, "date_to": date(2026, 12, 31), "version_type": "budget"}
        )
        self.env["cmc.kpi.target"].create(
            [{"version_id": version.id, "kpi_id": kpi.id, "date": month, "target_value": value} for kpi, month, value in rows]
        )
        return version

    def _run(self, date_from=JAN, date_to=None, user=None, compute=True, **vals):
        Run = self.env["cmc.kpi.run"]
        if user:
            Run = Run.with_user(user)
        run = Run.create({"name": "KPI run", "date_from": date_from, "date_to": date_to or date_from, **vals})
        if compute:
            run.action_compute()
        return run

    def _usages(self, run):
        """{(origin_model, origin_id)} recorded by the run (AD-002)."""
        usages = self.env["cmc.run.usage"].sudo().search([("consumer_model", "=", run._name), ("consumer_id", "=", run.id)])
        return {(usage.origin_model, usage.origin_id) for usage in usages}

    def _val(self, run, kpi, month):
        rows = run.value_ids.filtered(lambda row: row.kpi_id == kpi and row.date == month)
        self.assertEqual(len(rows), 1, "expected one value row for %s %s" % (kpi.code, month))
        return rows
