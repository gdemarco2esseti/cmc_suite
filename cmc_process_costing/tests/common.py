"""WP-06 ``cmc_process_costing`` - process costing per department and month (FC-07).

Binding decisions: AD-101 (new module, ``cmc_mrp`` stays a feeder bridge), AD-102 (depends ``cmc_mrp`` only),
AD-105 (calendar month grain), AD-107 (quantities from the MRP feeders, copied into the run at prefill),
AD-111 (costs read once through the grouped analytic reader, ``sudo`` in the run company), AD-113 (the run is a
``cmc.run.mixin`` snapshot, usage of every source run recorded), AD-114 (grouped reads: query count independent of
lines / MOs), AD-115 (security), AD-118 (Must: quantity schedule from MRP, WIP % typed, EU WA + FIFO,
normal/abnormal spoilage from scrap, transferred-in chaining, control total, cost of production report; Skip:
multiple material-addition points beyond the per-element %), AD-119 (EU maths in ``cmc_base.cmc_utils``:
``equivalent_units`` / ``spoilage_split`` / ``process_cost_report``, see ``cmc_base/tests/test_wp06_eu.py``).

CONTRACT
========
Sign: every amount of the run and of its lines is a POSITIVE cost (a WIP inventory value, as in the job cost
sheet); the reader converts the analytic cost with ``-amount`` (AD-005). Percentages are 0..100.
Cost elements (Selection ``element``): ``transferred_in`` (always 100 % complete), ``materials``, ``conversion``.

1. ``cmc.process.run`` (``cmc.run.mixin``, ``_check_company_auto``) - one department x one calendar month.
   INPUTS (``_cmc_input_fields``, locked when computed):
   ``name``, ``company_id`` (default env company), ``currency_id`` (related), ``center_id`` (department = CMC cost
   center, required, check_company), ``product_id`` (process output, required), ``date_from`` / ``date_to``
   (required; exactly ONE calendar month, else ValidationError), ``method`` Selection ``weighted_average``
   (default) / ``fifo``, ``previous_run_id`` (same department and product, an EARLIER month, else ValidationError:
   source of the beginning WIP), ``previous_dept_run_id`` (an upstream department, SAME period, else
   ValidationError: source of the transferred-in units and cost), ``closing_run_id`` (``cmc.closing.run``),
   ``normal_spoilage_pct`` (normal spoilage as % of the good units completed), ``beginning_units``,
   ``beginning_pct_materials``, ``beginning_pct_conversion``, ``beginning_cost_transferred_in``,
   ``beginning_cost_materials``, ``beginning_cost_conversion``, ``units_started`` (started or transferred in),
   ``units_completed`` (good units completed and transferred out), ``units_spoiled``, ``ending_units``,
   ``ending_pct_materials``, ``ending_pct_conversion``. Any percentage outside 0..100 -> ValidationError.
   OUTPUTS (written by Compute only, cleared by reset): ``units_normal_spoiled``, ``units_abnormal_spoiled``
   (``spoilage_split(units_spoiled, units_completed, normal_spoilage_pct)``), ``cost_to_account``,
   ``transferred_out_cost`` (includes the normal spoilage), ``normal_spoilage_cost``, ``ending_wip_cost``,
   ``abnormal_loss``, ``unit_cost_transferred`` (= transferred_out_cost / units_completed), ``control_difference``
   (= cost_to_account - transferred_out_cost - ending_wip_cost - abnormal_loss, always 0: Compute raises
   UserError otherwise); ``line_ids`` O2m ``cmc.process.run.line`` (``_cmc_result_fields``).

   ``action_cmc_prefill()`` (draft only, else UserError) - the quantity schedule from MRP:
   * refreshes the ``mrp_produced_qty`` and ``mrp_scrap_qty`` feeders for the run period and company
     (``cmc.measure.quantity._cmc_refresh_feeders``), then reads the ACTUAL facts of the department center and of
     the product cost object in the month (grouped, AD-114):
     ``units_spoiled`` = scrap facts, ``units_completed`` = produced facts - scrap facts;
   * ``previous_run_id`` (must be computed, else UserError): ``beginning_units`` = its ``ending_units``,
     ``beginning_pct_*`` = its ``ending_pct_*``, ``beginning_cost_<element>`` = the ``ending_cost`` of its lines;
   * ``previous_dept_run_id`` (must be computed, else UserError): ``units_started`` = its ``units_completed``;
     otherwise ``units_started`` = max(0, units_completed + units_spoiled + ending_units - beginning_units) (the
     department's own starts; type ``ending_units`` first). Unit fields are never negative (ValidationError).
   ``cmc_mrp`` (AD-107: the bridge declares the scrap measure) adds: measure ``cmc_mrp.cmc_measure_scrap_qty``
   (statistical, feeder key ``mrp_scrap_qty``): done scrap moves (``scrap_id`` set) of the FINISHED product of
   MOs (``production_id.cmc_center_id`` set), center = that center, object = the product, in the product UoM;
   and the ``mrp_produced_qty`` feeder EXCLUDES scrap moves (core 19 stamps ``production_id`` on the scrap move
   of a finished product, mrp/models/stock_scrap.py ``_prepare_move_values``: today they count as produced).

   ``action_compute()`` (cmc.run.mixin):
   * quantity schedule beginning_units + units_started = units_completed + units_spoiled + ending_units, else
     UserError (no balancing figure is invented);
   * period cost per element, read once with grouped reads on the analytic lines of ``center_id`` in the period
     (not excluded, ``sudo`` in the run company): ``materials`` = lines of element ``cmc_base.cmc_element_mp``
     EXCEPT those whose ``product_id`` is the product of ``previous_dept_run_id`` (that consumption IS the
     transferred-in cost, counting it twice is the classic process-costing error); ``conversion`` = every other
     line (MOD ``cmc_base.cmc_element_mod`` + overhead) PLUS, when ``closing_run_id`` is set, the
     ``cost_received`` of the closing line of the center (overhead reallocated from service centers: with it
     materials + conversion = the closing residual of the department). The closing must be computed, same company,
     its period must contain the run period (UserError) and is recorded as used (AD-002);
   * ``transferred_in`` period cost = ``previous_dept_run_id.transferred_out_cost`` (usage recorded);
     ``previous_run_id`` usage recorded too (its reset is refused while this run is computed);
   * ``cmc_utils.process_cost_report(method, ...)`` with the currency rounding; spoiled units at 100 %.
   A ``transferred_in`` line exists only with ``previous_dept_run_id`` or a beginning transferred-in cost.

2. ``cmc.process.run.line`` (``cmc.run.result.mixin``, read-only for users): ``run_id`` (cascade), ``company_id``
   (related, stored), ``currency_id``, ``element``, ``beginning_cost``, ``period_cost``, ``cost_to_account``,
   ``beginning_pct``, ``ending_pct``, ``equivalent_units``, ``cost_per_eu`` (Float, full precision),
   ``transferred_cost``, ``normal_spoilage_cost``, ``ending_cost``, ``abnormal_cost`` (Monetary;
   transferred + ending + abnormal = cost_to_account per element, exactly).

3. Report ``cmc_process_costing.action_report_cost_of_production`` (qweb-pdf, bound to ``cmc.process.run``):
   cost of production report per run - quantity schedule, equivalent units, cost per EU, cost assignment with
   the control total.

4. Security (AD-115): ``company_id`` + global rule ``company_id parent_of company_ids`` (not noupdate) on both
   models; ``group_cmc_user``: create/write/unlink draft runs, prefill, compute, reset; lines read-only;
   no CMC group: AccessError; a CMC user of another company does not see the runs.

Fixture (company = env.company, period P = current month, MRP through the real ``cmc_mrp`` path):
  Raw material RM (standard 52), packaging PK (standard 5), semi-finished S (standard 121), finished F.
  D1 "Assembly" (final center, WC1 30/h): MO 1,000 S = 1,000 RM (MP 52,000) + 60,000 min (MOD 30,000);
    100 S of that MO scrapped after the MO is done (spoiled; good completed 900). Indirect overhead 20,000.
  MAINT (auxiliary) 8,800 served 100 % to D1 -> closing "direct" P: D1 cost_received 8,800.
    => D1 materials 52,000; conversion 30,000 + 20,000 + 8,800 = 58,800 (50,000 without the closing).
  D1 run P (typed BWIP): 200 u, 100 % / 60 %, cost 9,200 / 6,100; EWIP 200 u 100 % / 50 %; normal 10 %.
    WA  : EU 1,200 / 1,100, per EU 51 / 59; transferred 108,900 (normal spoilage 9,900), EWIP 16,100,
          abnormal 1,100; costs to account 126,100; unit cost transferred 121.
    FIFO: EU 1,000 / 980, per EU 52 / 60; transferred 108,580 (normal 10,080), EWIP 16,400, abnormal 1,120.
    WA without closing: conversion (6,100 + 50,000) / 1,100 = 51.
  D2 "Finishing" (final center, WC2 25/h): MO 800 F = 800 S (MP 96,800, = the transferred-in, excluded) + 800 PK
    (MP 4,000) + 48,000 min (MOD 20,000); indirect 5,500. D2 run P chained to D1 WA: transferred in 900 u /
    108,900; completed 800, EWIP 100 u (materials 0 %, conversion 50 %).
    EU 900 / 800 / 850, per EU 121 / 5 / 30; transferred out 800 x 156 = 124,800; EWIP 12,100 + 1,500 = 13,600.
  D1 run P+1 with previous_run_id = D1 run P (WA): BWIP 200 u, 100 % / 50 %, cost 10,200 / 5,900
    (FIFO P: 10,400 / 6,000).
"""

from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import Command, fields
from odoo.tests.common import TransactionCase, new_test_user

TODAY = fields.Date.today()
P_FROM = TODAY.replace(day=1)
P_TO = P_FROM + relativedelta(months=1) - timedelta(days=1)
N_FROM = P_FROM + relativedelta(months=1)
N_TO = N_FROM + relativedelta(months=1) - timedelta(days=1)
REPORT_XMLID = "cmc_process_costing.action_report_cost_of_production"

D1_TYPED = {
    "beginning_units": 200.0, "beginning_pct_materials": 100.0, "beginning_pct_conversion": 60.0,
    "beginning_cost_materials": 9200.0, "beginning_cost_conversion": 6100.0,
    "ending_units": 200.0, "ending_pct_materials": 100.0, "ending_pct_conversion": 50.0,
    "normal_spoilage_pct": 10.0,
}
D2_TYPED = {"ending_units": 100.0, "ending_pct_materials": 0.0, "ending_pct_conversion": 50.0}


class ProcessCase(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.AA = cls.env["account.analytic.account"]
        cls.AL = cls.env["account.analytic.line"]
        cls.mp = cls.env.ref("cmc_base.cmc_element_mp")
        cls.mod = cls.env.ref("cmc_base.cmc_element_mod")
        cls.plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        cls.user = new_test_user(
            cls.env, login="wp06_user", groups="base.group_user,cmc_base.group_cmc_user",
            company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)],
        )
        cls.overhead = cls.env["cmc.cost.element"].create(
            {"name": "WP06 Overhead", "code": "WP06OH", "company_id": cls.company.id}
        )
        cls.service_driver = cls.env["cmc.cost.driver"].create(
            {"name": "WP06 service", "code": "WP06SVC", "company_id": cls.company.id}
        )
        cls.d1 = cls._center("WP06 Assembly")
        cls.d2 = cls._center("WP06 Finishing")
        cls.maint = cls._center("WP06 Maintenance", center_type="auxiliary")
        cls.wc1 = cls._workcenter("WP06 WC1", cls.d1, 30.0)
        cls.wc2 = cls._workcenter("WP06 WC2", cls.d2, 25.0)
        cls.categ = cls.env["product.category"].create(
            {"name": "WP06 standard", "property_cost_method": "standard", "property_valuation": "periodic"}
        )
        cls.rm = cls._product("WP06 RM", 52.0)
        cls.pk = cls._product("WP06 PK", 5.0)
        cls.s = cls._product("WP06 Semi S", 121.0)
        cls.f = cls._product("WP06 Finished F")
        cls.bom_s = cls._bom(cls.s, [(cls.rm, 1)], [(cls.wc1, 60.0)])
        cls.bom_f = cls._bom(cls.f, [(cls.s, 1), (cls.pk, 1)], [(cls.wc2, 60.0)])
        # ---- actuals of period P through the real MRP / cmc_mrp path
        cls.mo_s = cls._done_mo(cls.s, cls.bom_s, 1000.0, cls.d1, duration=60000.0)
        cls.scrap_s = cls._scrap(cls.mo_s, cls.s, 100.0)
        cls.mo_f = cls._done_mo(cls.f, cls.bom_f, 800.0, cls.d2, duration=48000.0)
        cls._indirect(cls.d1, 20000.0)
        cls._indirect(cls.d2, 5500.0)
        cls._indirect(cls.maint, 8800.0)
        cls._indirect(cls.d1, 777.0, day=P_FROM - timedelta(days=1))  # previous month: out of period
        cls.env["cmc.center.service"].create({
            "company_id": cls.company.id, "provider_id": cls.maint.id, "receiver_id": cls.d1.id,
            "driver_id": cls.service_driver.id, "quantity": 1.0,
        })
        cls.closing = cls.env["cmc.closing.run"].create(
            {"name": "WP06 closing", "method": "direct", "company_id": cls.company.id, "date_from": P_FROM, "date_to": P_TO}
        )
        cls.closing.action_compute()

    # ---- builders ---------------------------------------------------------

    @classmethod
    def _center(cls, name, center_type="final", company=None):
        return cls.AA.create(
            {"name": name, "plan_id": cls.plan.id, "cmc_center_type": center_type, "company_id": (company or cls.company).id}
        )

    @classmethod
    def _workcenter(cls, name, center, costs_hour):
        return cls.env["mrp.workcenter"].create({
            "name": name, "costs_hour": costs_hour, "analytic_distribution": {str(center.id): 100},
            "company_id": center.company_id.id,
        })

    @classmethod
    def _product(cls, name, price=0.0):
        product = cls.env["product.product"].create({"name": name, "is_storable": True, "categ_id": cls.categ.id})
        if price:
            product.with_company(cls.company).standard_price = price
        return product

    @classmethod
    def _bom(cls, product, components, operations=()):
        return cls.env["mrp.bom"].create({
            "product_tmpl_id": product.product_tmpl_id.id, "product_qty": 1.0, "type": "normal",
            "company_id": cls.company.id,
            "bom_line_ids": [Command.create({"product_id": comp.id, "product_qty": q}) for comp, q in components],
            "operation_ids": [
                Command.create({"name": "Op %s" % wc.name, "workcenter_id": wc.id, "time_cycle_manual": minutes})
                for wc, minutes in operations
            ],
        })

    @classmethod
    def _done_mo(cls, product, bom, qty, center, duration=None):
        """A done MO through the real mrp / cmc_mrp path: MP and MOD analytic lines are booked by the bridge."""
        mo = cls.env["mrp.production"].create(
            {"product_id": product.id, "product_qty": qty, "bom_id": bom.id, "cmc_center_id": center.id}
        )
        mo.action_confirm()
        if duration is not None:
            mo.workorder_ids.duration = duration
        mo.qty_producing = qty
        for move in mo.move_raw_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        mo.with_context(skip_consumption=True).button_mark_done()
        mo.invalidate_recordset()
        return mo

    @classmethod
    def _scrap(cls, mo, product, qty):
        """Scrap of finished units of a done MO through the core path (the real producer of the spoiled units)."""
        scrap = cls.env["stock.scrap"].create(
            {"production_id": mo.id, "product_id": product.id, "scrap_qty": qty, "company_id": mo.company_id.id}
        )
        scrap.do_scrap()
        return scrap

    @classmethod
    def _indirect(cls, center, cost, day=None):
        return cls.AL.create({
            "name": "WP06 overhead", "account_id": center.id, "amount": -cost, "company_id": center.company_id.id,
            "cmc_cost_element_id": cls.overhead.id, "cmc_behavior": "fixed", "cmc_directness": "indirect",
            "date": day or TODAY,
        })

    # ---- runs -------------------------------------------------------------

    def _run(self, center=None, product=None, method="weighted_average", typed=None, prefill=True, compute=True,
             env=None, date_from=P_FROM, date_to=P_TO, **vals):
        """A process run; ``typed`` values are written BEFORE the MRP prefill (ending WIP drives the starts)."""
        Run = (env or self.env)["cmc.process.run"]
        center = center or self.d1
        run = Run.create({
            "name": "WP06 %s %s" % (center.name, method), "company_id": center.company_id.id,
            "center_id": center.id, "product_id": (product or self.s).id, "method": method,
            "date_from": date_from, "date_to": date_to,
            **(D1_TYPED if typed is None else typed), **vals,
        })
        if prefill:
            run.action_cmc_prefill()
        if compute:
            run.action_compute()
        return run

    def _d1(self, method="weighted_average", **kw):
        kw.setdefault("closing_run_id", self.closing.id)
        return self._run(method=method, **kw)

    def _d2(self, upstream, **kw):
        return self._run(center=self.d2, product=self.f, typed=D2_TYPED, previous_dept_run_id=upstream.id, **kw)

    @staticmethod
    def _line(run, element):
        return run.line_ids.filtered(lambda line: line.element == element)
