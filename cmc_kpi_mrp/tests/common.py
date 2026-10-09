"""KPI-02 residual (gap register 10_gap_register.md section 5): manufacturing time and quality feeders for cmc_kpi.

Bridge ``cmc_kpi_mrp`` (depends ``["cmc_kpi", "cmc_mrp"]``, ``auto_install`` True): cycle time, manufacturing lead
time, PPM, first-pass yield, manufacturing cycle efficiency (MCE, Kaplan-Cooper Ch4: processing / throughput time)
and delivery lead time, from REAL done MOs / work orders / transfers, through the AD-107 feeder API.

CONTRACT (binding for the developer; tests are read-only)
=========================================================
Units: every time measure is in MINUTES (the unit of ``mrp.workorder.duration``); the shipped day KPIs divide by
1,440. Quantities in the PRODUCT UoM (``uom_field``). Grain of the MRP facts = (month, company,
``mrp.production.cmc_center_id``, product cost object of the MO product), as the ``cmc_mrp`` produced feeder, so that
a per-unit KPI divides two facts of the same grain. Only DONE MOs with ``cmc_center_id`` set feed (an MO outside
CMC product costing feeds nothing, an open MO feeds nothing even with logged time). Month of every MRP fact = month
of ``mrp.production.date_finished`` of the MO (its close), also for the work-order minutes logged earlier.

1. Fields
   - ``mrp.production.cmc_is_rework`` Boolean (default False, ``copy=False``), editable by MRP users: the MO
     re-processes units that failed the first pass (rework marker chosen over origin parsing: explicit, searchable).
   - ``mrp.production.cmc_date_released`` Datetime (``copy=False``, readonly): stamped with now by
     ``action_confirm`` (release to the shop floor), never rewritten afterwards.
   - ``mrp.production.cmc_lead_time`` Float, computed STORED, minutes = ``date_finished - start`` of a done MO,
     start = ``cmc_date_released``, or ``create_date`` when empty (MOs confirmed before the bridge existed);
     clamped at 0; 0 while not done. WHY NOT ``date_start``: core 19 rewrites it - to the close time when the
     operators log the minutes without pressing Start (``mrp.workorder.button_finish`` sets the first work order
     ``date_start`` and ``write`` propagates it to the MO), to the start press otherwise - so it is not a start.
   - ``stock.picking.cmc_delivery_lead_time`` Float, computed STORED, minutes = ``date_done - start`` of a done
     transfer, clamped at 0; 0 while not done. start = ``sale_id.date_order`` when ``sale_stock`` is installed
     (``sale_id`` in the fields) and the transfer has a sale order; otherwise ``create_date`` of the transfer.
   Stored so that every feeder stays one ``_read_group`` (AD-114).

2. Measures (``cmc.cost.driver``, ``measure_kind`` "statistical", ``source`` "feeder", ``company_id`` False,
   noupdate) and feeder profiles (``cmc.measure.quantity._cmc_feeder_profiles`` extended with ``super()``):
   xmlid ``cmc_kpi_mrp.``                 feeder_key                     value per fact
   ``cmc_measure_wo_minutes``             ``mrp_wo_minutes``             sum ``mrp.workorder.duration`` (all time)
   ``cmc_measure_value_added_minutes``    ``mrp_value_added_minutes``    sum of the PRODUCTIVE time only
                                                                         (``mrp.workcenter.productivity`` with
                                                                         ``loss_type`` "productive"; performance
                                                                         / availability / quality losses excluded)
   ``cmc_measure_mo_lead_minutes``        ``mrp_mo_lead_minutes``        sum ``mrp.production.cmc_lead_time``
   ``cmc_measure_mo_done``                ``mrp_mo_done``                count of done MOs
   ``cmc_measure_rework_qty``             ``mrp_rework_qty``             produced quantity of the MOs flagged
                                                                         ``cmc_is_rework`` (same moves and UoM
                                                                         rule as ``mrp_produced_qty``, NET of
                                                                         unbuilds)
   ``cmc_measure_delivery_lead_minutes``  ``stock_delivery_lead_minutes`` sum ``stock.picking.
                                                                         cmc_delivery_lead_time`` of the shipments
                                                                         of cmc_kpi (done, outgoing, ``return_id``
                                                                         False), month of ``date_done``, no center
                                                                         / object.
   An unbuild returns units, not time: it nets the produced and rework quantities, never minutes nor counts.
   Refresh idempotent per month and company (the engine replaces facts by feeder key).

3. KPI library: ``cmc.kpi._cmc_load_mrp_library(company=None)`` (``@api.model``, default env company) creates for
   the company the KPIs below if missing (by ``code``, idempotent) and returns ALL of them (recordset). Run as the
   caller (no sudo): only a CMC manager may load it (others: AccessError). Not called at install (the cmc_kpi runs
   with an empty KPI selection read every active KPI of the company: existing suites must not change).
   code                  type     source / formula                                          unit     dir.    persp.
   mrp_produced          measure  cmc_mrp.cmc_measure_produced_qty (sum)                    number   higher  internal
   mrp_scrapped          measure  cmc_mrp.cmc_measure_scrap_qty (sum)                       number   lower   internal
   mrp_reworked          measure  cmc_measure_rework_qty (sum)                              number   lower   internal
   mrp_wo_minutes        measure  cmc_measure_wo_minutes (sum)                              number   lower   internal
   mrp_value_added       measure  cmc_measure_value_added_minutes (sum)                     number   higher  internal
   mrp_lead_minutes      measure  cmc_measure_mo_lead_minutes (sum)                         number   lower   internal
   mrp_mo_done           measure  cmc_measure_mo_done (sum)                                 number   higher  internal
   mrp_first_pass        formula  produced - reworked (units that entered the first pass)   number   higher  internal
   mrp_cycle_time        formula  wo_minutes / produced (minutes per unit)                  number   lower   internal
   mrp_lead_time         formula  lead_minutes / mo_done / 1440 (average days per MO)       days     lower   internal
   mrp_ppm               formula  scrapped / produced x 1,000,000                           number   lower   internal
   mrp_fpy               formula  (first_pass - reworked - scrapped) / first_pass x 100     percent  higher  internal
   mrp_mce               formula  value_added / lead_minutes x 100                          percent  higher  internal
   delivery_shipments    measure  cmc_kpi.cmc_measure_shipments (sum)                       number   higher  customer
   delivery_lead_minutes measure  cmc_measure_delivery_lead_minutes (sum)                   number   lower   customer
   delivery_lead_time    formula  delivery_lead_minutes / delivery_shipments / 1440 (days)  days     lower   customer
   FPY: the output of a rework MO is a produced quantity (cmc_mrp) but not a first pass, hence the first-pass base.
   A month without production: the formulas divide by 0 -> not computable (cmc_kpi rule), never 0.

4. Security / multi-company: the feeders run as engine (sudo, cmc_base): a CMC user without MRP rights refreshes
   and computes a run. Facts and KPIs are per company; codes unique per company only.
5. Cost of access (AD-114): refresh of the new keys costs the same number of queries with 3 or 18 MOs.

Fixture (company A = env company, 2026; product P "KPIMRP gear" in Units, BoM with Cut 30 min/unit on WC cut and
Assemble 12 min/unit on WC assembly; times logged as an operator does, ``mrp.workorder.duration`` at close):
  (start = confirmation, frozen clock)
  MO1 press  12 P  start Jan 5 08:00  done Jan 8 08:00   (4,320)  Cut 300 (exp 360)  Assemble 172 (exp 144:
                                                                   core splits productive 144 + performance 28)
  MO2 press   8 P  start Jan 12 08:00 done Jan 13 08:00  (1,440)  Cut 240  Assemble 96   (all productive)
  MO3 rework  5 P  REWORK  start Jan 26 08:00 done Jan 26 20:00 (720)  Cut 132  Assemble 60 (all productive)
  scrap 1 P of MO2 on Jan 20 (stock.scrap with production_id, real core path)
  MO4 press  10 P  start Feb 2 08:00  done Feb 3 19:00   (2,100)  Cut 300  Assemble 120 (all productive)
  noise: MO without CMC center (Jan, 3 P, 90 + 36 min) and a confirmed MO with 50 min logged, never closed.
  Jan: produced 25, scrapped 1, reworked 5, wo 1,000 (press 808, rework 192), productive 972 (press 780, rework
    192), lead 6,480 (press 5,760, rework 720), MOs 3 (press 2, rework 1)
    -> cycle 1,000 / 25 = 40.0 min/unit; lead 6,480 / 3 / 1,440 = 1.5 days; PPM 1 / 25 x 1e6 = 40,000;
       first pass 25 - 5 = 20, FPY (20 - 5 - 1) / 20 = 70.0 %; MCE 972 / 6,480 = 15.0 %.
  Feb: produced 10, wo 420, productive 420, lead 2,100, MOs 1 -> cycle 42.0; lead 2,100 / 1,440 days; PPM 0;
    FPY 100.0; MCE 20.0.  Mar: nothing -> the ratios are not computable.
Delivery fixture (consumable product): outgoing D1 created Jan 10 08:00 done Jan 12 08:00 (2,880), D2 created
  Jan 14 08:00 done Jan 14 20:00 (720), D3 created Jan 30 08:00 done Feb 2 08:00 (4,320); a receipt in Jan
  (ignored) -> Jan 3,600 / 2 shipments / 1,440 = 1.25 days, Feb 4,320 / 1 = 3.0 days.
"""

from datetime import date, datetime

from freezegun import freeze_time

from odoo import Command
from odoo.tests.common import TransactionCase, new_test_user

JAN = date(2026, 1, 1)
FEB = date(2026, 2, 1)
MAR = date(2026, 3, 1)
MAR_END = date(2026, 3, 31)

MRP_KEYS = ["mrp_wo_minutes", "mrp_value_added_minutes", "mrp_mo_lead_minutes", "mrp_mo_done", "mrp_rework_qty"]
DELIVERY_KEY = "stock_delivery_lead_minutes"
MEASURES = {
    "cmc_kpi_mrp.cmc_measure_wo_minutes": "mrp_wo_minutes",
    "cmc_kpi_mrp.cmc_measure_value_added_minutes": "mrp_value_added_minutes",
    "cmc_kpi_mrp.cmc_measure_mo_lead_minutes": "mrp_mo_lead_minutes",
    "cmc_kpi_mrp.cmc_measure_mo_done": "mrp_mo_done",
    "cmc_kpi_mrp.cmc_measure_rework_qty": "mrp_rework_qty",
    "cmc_kpi_mrp.cmc_measure_delivery_lead_minutes": "stock_delivery_lead_minutes",
}


def dt(month, day, hour=8, minute=0):
    return datetime(2026, month, day, hour, minute)


class KpiMrpBase(TransactionCase):
    """Shared users, centers and helpers (no MRP / stock document)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.Fact = cls.env["cmc.measure.quantity"]
        cls.plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        cls.manager = new_test_user(cls.env, login="kpimrp_manager", groups="base.group_user,cmc_base.group_cmc_manager")
        cls.user = new_test_user(cls.env, login="kpimrp_user", groups="base.group_user,cmc_base.group_cmc_user")
        cls.mrp_user = new_test_user(cls.env, login="kpimrp_mrp", groups="base.group_user,mrp.group_mrp_user")
        cls.outsider = new_test_user(cls.env, login="kpimrp_outsider", groups="base.group_user")

    @classmethod
    def _center(cls, name, company=None):
        return cls.env["account.analytic.account"].create(
            {"name": name, "plan_id": cls.plan.id, "cmc_center_type": "final", "company_id": (company or cls.company).id}
        )

    # ---- reading helpers ------------------------------------------------

    def _refresh(self, date_from=JAN, date_to=MAR_END, company=None, keys=None):
        self.Fact._cmc_refresh_feeders(date_from, date_to, company=company or self.company, keys=keys)

    def _facts(self, key, company=None):
        """{(month, center): quantity} of the actual facts of a feeder key."""
        facts = self.Fact.search([("feeder_key", "=", key), ("version_id", "=", False),
                                  ("company_id", "=", (company or self.company).id)])
        totals = {}
        for fact in facts:
            totals[(fact.date, fact.center_id)] = totals.get((fact.date, fact.center_id), 0.0) + fact.quantity
        return totals

    def _by_month(self, key, company=None):
        totals = {}
        for (month, _center), qty in self._facts(key, company).items():
            totals[month] = totals.get(month, 0.0) + qty
        return totals

    def _library(self, company=None, user=None):
        Kpi = self.env["cmc.kpi"].with_user(user) if user else self.env["cmc.kpi"]
        kpis = Kpi._cmc_load_mrp_library(company or self.company)
        return {kpi.code: kpi for kpi in kpis}

    def _run(self, date_from=JAN, date_to=FEB, user=None, company=None, kpis=None):
        company = company or self.company
        kpis = kpis or self.env["cmc.kpi"].concat(*self._library(company).values())
        Run = self.env["cmc.kpi.run"].with_company(company)
        if user:
            Run = Run.with_user(user)
        run = Run.create({"name": "KPI MRP run", "company_id": company.id, "date_from": date_from, "date_to": date_to,
                          "kpi_ids": [Command.set(kpis.ids)]})
        run.action_compute()
        return run

    def _val(self, run, code, month):
        rows = run.value_ids.filtered(lambda row: row.kpi_id.code == code and row.date == month)
        self.assertEqual(len(rows), 1, "expected one value row for %s %s" % (code, month))
        return rows


class KpiMrpCase(KpiMrpBase):
    """Done MOs with work orders through the real mrp / cmc_mrp path (hand numbers in the module docstring)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.press = cls._center("KPIMRP press")
        cls.rework_center = cls._center("KPIMRP rework")
        cls.categ = cls.env["product.category"].create(
            {"name": "KPIMRP periodic", "property_cost_method": "standard", "property_valuation": "periodic"}
        )
        cls.gear = cls.env["product.product"].create({"name": "KPIMRP gear", "is_storable": True, "categ_id": cls.categ.id})
        cls.blank = cls.env["product.product"].create({"name": "KPIMRP blank", "is_storable": True, "categ_id": cls.categ.id})
        cls.wc_cut = cls.env["mrp.workcenter"].create({"name": "KPIMRP cut", "company_id": cls.company.id})
        cls.wc_assembly = cls.env["mrp.workcenter"].create({"name": "KPIMRP assembly", "company_id": cls.company.id})
        cls.bom = cls._bom(cls.gear, cls.blank, [(cls.wc_cut, "Cut", 30.0), (cls.wc_assembly, "Assemble", 12.0)])
        cls.bom_plain = cls._bom(cls.gear, cls.blank, [])
        cls.mo1 = cls._done_mo(12.0, dt(1, 5), dt(1, 8), (300.0, 172.0))
        cls.mo2 = cls._done_mo(8.0, dt(1, 12), dt(1, 13), (240.0, 96.0))
        cls.scrap = cls._scrap(cls.mo2, 1.0, dt(1, 20, 10))
        cls.mo3 = cls._done_mo(5.0, dt(1, 26), dt(1, 26, 20), (132.0, 60.0), center=cls.rework_center, rework=True)
        cls.mo4 = cls._done_mo(10.0, dt(2, 2), dt(2, 3, 19), (300.0, 120.0))
        # noise: outside CMC product costing (no center) and an MO never closed
        cls.mo_no_center = cls._done_mo(3.0, dt(1, 6), dt(1, 7), (90.0, 36.0), center=False)
        cls.mo_open = cls.env["mrp.production"].create(
            {"product_id": cls.gear.id, "product_qty": 4.0, "bom_id": cls.bom.id, "cmc_center_id": cls.press.id,
             "date_start": dt(1, 15)}
        )
        cls.mo_open.action_confirm()
        with freeze_time(dt(1, 16)):
            cls.mo_open.workorder_ids[:1].duration = 50.0

    @classmethod
    def _bom(cls, product, component, operations, company=None):
        return cls.env["mrp.bom"].create({
            "product_tmpl_id": product.product_tmpl_id.id, "product_qty": 1.0, "type": "normal",
            "company_id": (company or cls.company).id,
            "bom_line_ids": [Command.create({"product_id": component.id, "product_qty": 1.0})],
            "operation_ids": [
                Command.create({"name": name, "workcenter_id": wc.id, "time_cycle_manual": minutes})
                for wc, name, minutes in operations
            ],
        })

    @classmethod
    def _done_mo(cls, qty, start, done, durations=None, center=None, rework=False, bom=None, company=None, uom=None):
        """A done MO as the shop floor closes it: created with its start, confirmed, the operator logs the minutes of
        each work order (``duration``, in operation order) and marks the MO done at ``done`` (frozen clock)."""
        vals = {"product_id": cls.gear.id, "product_qty": qty, "bom_id": (bom or cls.bom).id, "date_start": start,
                "cmc_center_id": False if center is False else (center or cls.press).id}
        if rework:
            vals["cmc_is_rework"] = True
        if company:
            vals["company_id"] = company.id
        if uom:
            vals["product_uom_id"] = uom.id
        Production = cls.env["mrp.production"].with_company(company) if company else cls.env["mrp.production"]
        mo = Production.create(vals)
        with freeze_time(start):
            mo.action_confirm()
        with freeze_time(done):
            if durations:
                for workorder, minutes in zip(mo.workorder_ids.sorted(lambda wo: wo.operation_id.id), durations):
                    workorder.duration = minutes
            cls._close(mo, qty)
        return mo

    @classmethod
    def _close(cls, mo, qty):
        mo.qty_producing = qty
        for move in mo.move_raw_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        mo.with_context(skip_consumption=True).button_mark_done()
        mo.invalidate_recordset()

    @classmethod
    def _scrap(cls, mo, qty, when):
        """Scrap of finished units of a done MO through the core path (the real producer of the mrp_scrap_qty facts)."""
        with freeze_time(when):
            scrap = cls.env["stock.scrap"].create(
                {"production_id": mo.id, "product_id": cls.gear.id, "scrap_qty": qty, "company_id": mo.company_id.id}
            )
            scrap.do_scrap()
        return scrap


class KpiDeliveryCase(KpiMrpBase):
    """Real validated transfers (delivery lead time); dates in the module docstring."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.warehouse = cls.env["stock.warehouse"].search([("company_id", "=", cls.company.id)], limit=1)
        cls.product = cls.env["product.product"].create({"name": "KPIMRP parcel", "type": "consu"})
        cls.customer = cls.env["res.partner"].create({"name": "KPIMRP customer"})
        cls.d1 = cls._ship(dt(1, 10), dt(1, 12))
        cls.d2 = cls._ship(dt(1, 14), dt(1, 14, 20))
        cls.d3 = cls._ship(dt(1, 30), dt(2, 2))
        cls.receipt = cls._ship(dt(1, 10), dt(1, 11), picking_type=cls.warehouse.in_type_id)

    @classmethod
    def _ship(cls, created, done=None, picking_type=None):
        """A real transfer created at ``created`` and validated at ``done`` (None = left open)."""
        picking_type = picking_type or cls.warehouse.out_type_id
        src = picking_type.default_location_src_id or cls.env.ref("stock.stock_location_suppliers")
        dest = picking_type.default_location_dest_id or cls.env.ref("stock.stock_location_customers")
        with freeze_time(created):
            picking = cls.env["stock.picking"].create({
                "picking_type_id": picking_type.id, "partner_id": cls.customer.id, "scheduled_date": created,
                "location_id": src.id, "location_dest_id": dest.id,
                "move_ids": [Command.create({
                    "product_id": cls.product.id, "product_uom": cls.product.uom_id.id, "product_uom_qty": 1.0,
                    "location_id": src.id, "location_dest_id": dest.id,
                })],
            })
            picking.action_confirm()
        # why SQL: core stamps create_date with the transaction timestamp (cr.now(), not frozen by freezegun) and
        # refuses it in create/write; this is the only way to date the creation of a transfer in a test
        cls.env.cr.execute("UPDATE stock_picking SET create_date = %s WHERE id = %s", (created, picking.id))
        picking.invalidate_recordset(["create_date"])
        if done:
            cls._validate(picking, done)
        return picking

    @classmethod
    def _validate(cls, picking, at):
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        with freeze_time(at):
            picking.button_validate()
        return picking
