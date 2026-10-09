"""WP-03 ``cmc_standard_cost`` - standard cost cards, predetermined rates, MO variances, absorption, standard push.

Capabilities: STD-01 (card per product per standard version, roll-up, types, versions), STD-04 (MO variances:
material price/quantity, mix/yield, labour rate/efficiency, overhead spending/efficiency/volume), FC-08
(predetermined rates = budget / normal or practical capacity, over/under-absorbed overhead per center and period),
FC-09 (publish unit costs to ``cmc.rate``, push to ``product.standard_price`` through the core revaluation,
card-vs-valuation reconciliation). Binding decisions: AD-102 (depends ``cmc_budget`` + ``cmc_mrp`` only), AD-103
(reuse core: BoM explosion / operation time of ``mrp``/``mrp_account``, ``standard_price`` write = core revaluation
creating ``product.value``; CMC never posts journal entries, AD-111 inv. 4), AD-104 (cards and rate tables are
INPUT lines of a ``cmc.plan.version`` of type ``standard``, locked when the version is approved), AD-105 (month
grain), AD-108 (rates published in ``cmc.rate``), AD-112 (stored natural sign, variance positive = favourable),
AD-113 (variance engine = ``cmc.run.mixin`` run, snapshot, usage recorded on the version), AD-114 (grouped reads,
query count independent of MOs/products), AD-115 (security), AD-118 (WP-03 Must).

CONTRACT
========
Sign: card figures, prices and rates are POSITIVE unit figures (like ``standard_price``). Every ``*_amount`` and
``*_variance`` of the variance engine is NATURAL sign (cost negative); variance = actual - standard: positive =
favourable (AD-112).

1. ``cmc.plan.version`` (``_inherit`` in cmc_standard_cost):
   ``standard_type`` Selection ``basic`` / ``ideal`` / ``attainable`` (default ``attainable``, STD-01 types);
   O2m (copy=True, so the base ``copy()`` duplicates them into the new draft = updated/kaizen version):
   ``std_price_ids`` -> ``cmc.standard.price``, ``std_rate_ids`` -> ``cmc.standard.rate``, ``card_ids`` ->
   ``cmc.standard.card``. Std models refuse a version whose ``version_type`` != ``standard`` (ValidationError).
   - ``action_cmc_std_prefill_rates(budget_version, basis="practical")`` (draft only, else UserError): replaces
     the rate lines of the version from the cost budget lines (``cmc.budget.line``, kind cost) of
     ``budget_version`` in the version span, per center:
       * ``overhead`` line: ``budget_fixed`` / ``budget_variable`` = sum of the fixed / variable figures whose
         element is NOT MP (``cmc_base.cmc_element_mp``) nor MOD (``cmc_base.cmc_element_mod``);
       * ``labour`` line (only when the center has MOD figures): ``budget_variable`` = MOD figures, fixed 0;
       * capacity = the ``cmc.capacity.analysis`` of the center covering the version span (date_from <= version
         start, date_to >= version end): ``driver_id`` = its driver, ``capacity_qty`` = ``capacity_practical`` or
         ``capacity_normal`` (``basis``), ``budget_qty`` = ``capacity_budget``. A center with figures and no such
         analysis -> UserError naming the center (fail loud, no rate 0).
   - ``action_cmc_std_rollup(products)`` (draft only, else UserError): one card per product AND per sub-assembly
     met in their BoMs (any component with a BoM, recursively), replacing the existing cards of those products.
     The roll-up also EXTENDS UPWARD (RN-002): every card of the version that uses a re-rolled card as
     sub-assembly (``child_card_id``, recursively up to the top) is re-rolled too, so a parent never keeps a stale
     price nor loses the link: its line ``child_card_id`` points to the NEW child card, ``price`` = the new child
     unit_cost, and its element split (material / labour / overhead / unit_fixed) is consistent with it.
   - approval (base ``action_approve``, manager only) of a ``standard`` version also PUBLISHES ``cmc.rate`` rows
     (origin = the version, one per month of the span), replacing the version-origin rates of the same keys
     (re-approval after reset never collides with the unique index):
       * per ``overhead`` rate line: holder = center, driver = line driver, cost element False, components
         ``fixed`` = fixed_rate, ``variable`` = variable_rate, ``total`` = rate;
       * per ``labour`` rate line: holder = center, driver = line driver, cost element = MOD, component ``total``;
       * per card: holder = ``cmc.cost.object`` of the product (company of the version), driver =
         ``cmc_mrp.cmc_measure_produced_qty``, components ``total`` = unit_cost, ``variable`` = unit_variable,
         ``fixed`` = unit_fixed.
   - ``action_cmc_std_push_prices()`` (FC-09): ``group_cmc_manager`` only (AccessError), APPROVED standard version
     only (UserError): for each card whose product has cost method ``standard`` in the version company, writes
     ``product.with_company(company).standard_price = card.unit_cost`` through the core ``write`` (core creates the
     ``product.value`` revaluation row; sudo allowed with a # why); other cost methods are skipped untouched.
   - ``_cmc_std_reconciliation() -> list[dict]`` (FC-09), one dict per card: ``product`` (record),
     ``card_unit_cost``, ``valuation_price`` (= current company ``standard_price``), ``difference`` (=
     valuation_price - card_unit_cost), ``qty_available``, ``value_difference`` (= difference x qty_available).

2. ``cmc.standard.price`` (run-line-mixin, ``_cmc_run_field = "version_id"``): ``version_id`` (required, cascade),
   ``company_id`` (related, stored), ``product_id`` (required), ``price`` Float (planned purchase price per product
   UoM). One per (version, product): unique index.

3. ``cmc.standard.rate`` (run-line-mixin on ``version_id``) - the FC-08 predetermined rate table: ``version_id``,
   ``company_id`` (related, stored), ``center_id`` (required, check_company), ``driver_id`` (cmc.cost.driver),
   ``rate_kind`` Selection ``labour`` / ``overhead``, ``capacity_basis`` Selection ``practical`` (default) /
   ``normal`` / ``manual``, ``budget_fixed``, ``budget_variable``, ``capacity_qty``, ``budget_qty`` (Float),
   ``fixed_rate``, ``variable_rate`` (stored compute, readonly=False: with basis != manual fixed_rate =
   budget_fixed / capacity_qty, variable_rate = budget_variable / (budget_qty or capacity_qty), 0 without qty;
   with ``manual`` the typed rates stay), ``rate`` = fixed_rate + variable_rate. One per (version, center,
   rate_kind): unique index.

4. ``cmc.standard.card`` (run-line-mixin on ``version_id``; STD-01): ``version_id``, ``company_id`` (related,
   stored), ``product_id`` (required), ``bom_id``, ``standard_type`` (related version), ``line_ids`` O2m
   ``cmc.standard.card.line`` (copy=True); unit figures (stored, per 1 product UoM): ``unit_material``,
   ``unit_labour``, ``unit_overhead``, ``unit_overhead_fixed``, ``unit_cost`` (= material + labour + overhead),
   ``unit_fixed`` (= fixed overhead), ``unit_variable`` (= unit_cost - unit_fixed). One card per (version,
   product): unique index. Card lines (also locked with the version): ``card_id``, ``line_type`` Selection
   ``material`` / ``labour`` / ``overhead``, ``product_id`` (component, material), ``center_id`` (labour/overhead),
   ``driver_id``, ``behavior`` (``fixed``/``variable``, overhead), ``quantity`` (per 1 finished unit), ``price``,
   ``amount`` (= quantity x price), ``price_source`` Selection ``planned`` (``cmc.standard.price`` of the version)
   / ``product`` (fallback: company ``standard_price`` at roll-up) / ``card`` (sub-assembly: unit cost of its card),
   ``child_card_id`` (sub-assembly card).
   Roll-up (reuse core, AD-103): BoM = ``mrp.bom._bom_find(product)``; per BoM line (``_skip_bom_line`` honoured)
   quantity = line qty converted to the component UoM / ``bom.product_qty``. Per operation: hours per unit =
   ``operation.time_total`` / 60 / ``bom.product_qty`` (core); center = the cmc center of
   ``workcenter.analytic_distribution``; ``labour`` line = hours x labour rate of that center (no labour rate line
   -> UserError naming the center); when the center has an ``overhead`` rate line, two ``overhead`` lines (fixed:
   hours x fixed_rate, variable: hours x variable_rate) on its driver. Sub-assembly line amount = qty x child
   unit_cost and the card's ELEMENT totals include qty x the child's element totals (material/labour/overhead
   stay distinguishable across levels). With planned prices = ``standard_price`` and labour rate =
   ``workcenter.costs_hour`` and no overhead, ``unit_cost`` = core ``product._compute_bom_price(bom)``.

5. ``cmc.standard.variance.run`` (cmc.run.mixin; STD-04, FC-08): inputs ``name``, ``company_id``, ``version_id``
   (approved standard version, check_company; non standard type -> ValidationError), ``date_from``, ``date_to``
   (inside the version span, else ValidationError). Outputs (cleared on reset): ``std_amount_total``,
   ``actual_amount_total``, ``variance_total`` (= actual - std over ``line_ids``), ``skipped_count`` (in-scope MOs
   whose product has no card in the version). Results: ``line_ids`` ``cmc.standard.variance.line``,
   ``absorption_ids`` ``cmc.standard.absorption.line`` (run-result-mixin, read-only for users).
   Compute: the version must be APPROVED (UserError); usage recorded (AD-002: the version cannot be reset while
   the run is computed). Scope = done MOs of the company with ``cmc_center_id``, ``date_finished`` in the period.
   Actuals: MP = cmc_mrp analytic lines (``cmc_mrp_move_id.production_id`` = MO; qty = ``unit_amount``, amount =
   ``amount``); MOD = workcenter analytic lines of the MO work orders (hours = ``unit_amount``, amount); output =
   done finished quantity CONVERTED TO THE PRODUCT UoM (RN-001: an MO of 1 Dozen = 12 units; cards are per 1 product
   UoM) and NET of unbuilds (done moves of ``mrp.unbuild`` orders on the MO taking the finished product back,
   consistent with the cmc_mrp ``mrp_produced_qty`` feeder, AD-111); standard quantities and hours = card per unit
   x that output. Snapshot: a later posting changes nothing until reset + compute.
   ``cmc.standard.variance.line`` per MO x component (``line_type`` material) and per MO x center (labour):
   ``production_id``, ``product_id`` (finished), ``component_id``, ``center_id``, ``output_qty``, ``std_qty`` (=
   card qty x output), ``actual_qty``, ``std_price``, ``actual_price`` (= actual amount / actual qty, positive),
   ``std_amount`` (= -std_price x std_qty), ``actual_amount``, ``price_variance`` (= (std_price - actual_price) x
   actual_qty; labour = rate variance), ``quantity_variance`` (= (std_qty - actual_qty) x std_price; labour =
   efficiency), ``mix_variance`` / ``yield_variance`` (material, over the material lines of the MO: m_i =
   std_qty_i / sum std_qty; mix_i = (m_i x sum actual_qty - actual_qty_i) x std_price_i; yield_i = (std_qty_i -
   m_i x sum actual_qty) x std_price_i; mix + yield = quantity; labour 0), ``total_variance`` (= actual_amount -
   std_amount = price + quantity).
   ``cmc.standard.absorption.line`` per ``overhead`` rate line of the version (FC-08): ``center_id``,
   ``driver_id``, ``fixed_rate``, ``variable_rate``, ``actual_qty`` (AH = actual MOD hours of the in-scope MOs on
   the center), ``standard_qty`` (SH = card hours x output), ``capacity_qty`` (D = rate capacity_qty x share),
   ``budget_fixed`` (= -budget_fixed x share), share = months of the run / months of the version span;
   ``actual_fixed`` / ``actual_variable`` = indirect (``cmc_directness`` indirect, not excluded) analytic cost of the
   center in the period by behavior, natural sign; ``actual_amount`` = sum; ``applied_actual`` = -rate x AH;
   ``applied_standard`` = -rate x SH; ``over_under_absorbed`` = actual_amount - applied_actual (> 0 over-absorbed);
   ``spending_variable`` = actual_variable + variable_rate x AH; ``efficiency_variable`` = (SH - AH) x
   variable_rate; ``spending_fixed`` = actual_fixed - budget_fixed; ``volume_variance`` = (SH - D) x fixed_rate;
   identity: actual_amount - applied_standard = the four variances.
   Reads grouped (AD-114): query count of Compute independent of the number of MOs.

6. Security (AD-115): every new model has ``company_id`` + global rule ``company_id parent_of company_ids``
   (not noupdate). ``group_cmc_user``: r/w/c/u on draft price/rate/card lines, prefill and roll-up, create and
   compute variance runs (results read-only); ``group_cmc_manager``: approve/reset versions, push prices.
   Outsider (base.group_user only): AccessError.

Fixture (company = env.company, year Y = current year, period = current month, share = 1/12):
  centers Z (WC1, costs_hour 22 = ACTUAL labour rate), Y (WC2, costs_hour 30); driver "hours".
  Budget version (type budget): Z rent fixed 1200, Z energy variable 600, Z MOD 2000; Y MOD 3000.
  Capacity (hours, year Y): Z theoretical 150 / practical 120 / normal 96 / budget 100; Y 120 / 100 / 100 / 100.
  => Z overhead fixed_rate 1200/120 = 10 (normal: 12.5), variable_rate 600/100 = 6; Z labour 2000/100 = 20;
     Y labour 3000/100 = 30.
  Raw materials (standard cost method, periodic): A standard_price 4.5, B 1.8, C 1.5; planned prices A 4, B 2.
  F: BoM 1 = 2 A + 3 B + op 30 min on WC1 => card material 8 + 6 = 14, labour 0.5 x 20 = 10, overhead fixed
     0.5 x 10 = 5 + variable 0.5 x 6 = 3 => unit_cost 32, unit_fixed 5, unit_variable 27.
  G: BoM 1 = 1 F + 2 C + op 6 min on WC2 => material 2 x 1.5 = 3 (+ F 14) = 17, labour 0.1 x 30 = 3 (+ F 10) =
     13, overhead (F) 8 => unit_cost 38 (F line 1 x 32 + C 3 + labour 3), unit_fixed 5.
  K: BoM 2 = 4 C + op 12 min on WC2 => per unit 2 x 1.5 + 0.1 x 30 = 6 (= core _compute_bom_price).
  MO of F (center Z): 10 produced, consumed A 22 (value 99), B 27 (48.6), work order 360 min (6 h x 22 = 132).
     A: std 20 x 4 = 80, price (4 - 4.5) x 22 = -11, qty (20 - 22) x 4 = -8, mix (0.4 x 49 - 22) x 4 = -9.6,
        yield (20 - 19.6) x 4 = 1.6, total -19.
     B: std 30 x 2 = 60, price (2 - 1.8) x 27 = 5.4, qty (30 - 27) x 2 = 6, mix (29.4 - 27) x 2 = 4.8,
        yield (30 - 29.4) x 2 = 1.2, total 11.4.
     Labour Z: std 5 h x 20 = 100, rate (20 - 22) x 6 = -12, efficiency (5 - 6) x 20 = -20, total -32.
     Totals: std -240, actual -279.6, variance -39.6.
  Indirect actuals on Z in the period: fixed 105, variable 40 (+ a fixed 999 one year earlier, out of period).
     Absorption Z: AH 6, SH 5, D 10, budget_fixed -100, actual -145, applied_actual -96, applied_standard -80,
     over_under -49, spending_variable -4, efficiency_variable -6, spending_fixed -5, volume -50 (sum -65).
  RN-001 (in the test): MO of 1 Dozen of F consuming exactly A 24 / B 36, 360 min => output 12, std A 24 / B 36,
     quantity variances 0, labour SH 6 = AH 6 (efficiency 0). Unbuild of 2 F from the MO of F => output 8.
  RN-002 (in the test): draft version F, G, K; A planned 5 then roll-up of F alone => F material 2 x 5 + 6 = 16,
     unit_cost 34; G re-rolled: F line 1 x 34, material 3 + 16 = 19, labour 13, overhead 8, unit_fixed 5,
     unit_cost 40.
"""

from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

from odoo import Command, fields
from odoo.tests.common import TransactionCase, new_test_user

TODAY = fields.Date.today()
Y = TODAY.year
Y_FROM = date(Y, 1, 1)
Y_TO = date(Y, 12, 31)
P_FROM = TODAY.replace(day=1)
P_TO = P_FROM + relativedelta(months=1) - timedelta(days=1)


class StdCase(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.Version = cls.env["cmc.plan.version"]
        cls.AA = cls.env["account.analytic.account"]
        cls.AL = cls.env["account.analytic.line"]
        cls.mp = cls.env.ref("cmc_base.cmc_element_mp")
        cls.mod = cls.env.ref("cmc_base.cmc_element_mod")
        cls.produced = cls.env.ref("cmc_mrp.cmc_measure_produced_qty")
        cls.plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        cls.manager = new_test_user(
            cls.env, login="std_mgr", groups="base.group_user,cmc_base.group_cmc_manager,mrp.group_mrp_user",
            company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)],
        )
        cls.user = new_test_user(
            cls.env, login="std_user", groups="base.group_user,cmc_base.group_cmc_user,mrp.group_mrp_user",
            company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)],
        )
        cls.outsider = new_test_user(cls.env, login="std_outsider", groups="base.group_user")
        Element = cls.env["cmc.cost.element"]
        cls.rent = Element.create({"name": "STD Rent", "code": "STDRENT", "company_id": cls.company.id})
        cls.energy = Element.create({"name": "STD Energy", "code": "STDNRG", "company_id": cls.company.id})
        cls.hours = cls.env["cmc.cost.driver"].create({"name": "STD hours", "code": "STDH", "company_id": cls.company.id})
        cls.z = cls._center("STD Z")
        cls.y = cls._center("STD Y")
        cls.wc1 = cls._workcenter("STD WC1", cls.z, 22.0)
        cls.wc2 = cls._workcenter("STD WC2", cls.y, 30.0)
        cls.categ = cls.env["product.category"].create(
            {"name": "STD standard", "property_cost_method": "standard", "property_valuation": "periodic"}
        )
        cls.a = cls._product("STD A", 4.5)
        cls.b = cls._product("STD B", 1.8)
        cls.c = cls._product("STD C", 1.5)
        cls.f = cls._product("STD F")
        cls.g = cls._product("STD G")
        cls.k = cls._product("STD K")
        cls.bom_f = cls._bom(cls.f, [(cls.a, 2), (cls.b, 3)], [(cls.wc1, 30.0)])
        cls.bom_g = cls._bom(cls.g, [(cls.f, 1), (cls.c, 2)], [(cls.wc2, 6.0)])
        cls.bom_k = cls._bom(cls.k, [(cls.c, 4)], [(cls.wc2, 12.0)], qty=2.0)
        # ---- budget (cmc_budget) and capacity, source of the FC-08 rate table
        cls.budget = cls.Version.create(
            {"name": "STD budget", "version_type": "budget", "company_id": cls.company.id, "date_from": Y_FROM, "date_to": Y_TO}
        )
        cls._budget_line(cls.z, cls.rent, "fixed", 1200)
        cls._budget_line(cls.z, cls.energy, "variable", 600)
        cls._budget_line(cls.z, cls.mod, "variable", 2000)
        cls._budget_line(cls.y, cls.mod, "variable", 3000)
        cls.cap_z = cls._capacity(cls.z, theoretical=150, practical=120, normal=96, budget=100)
        cls.cap_y = cls._capacity(cls.y, theoretical=120, practical=100, normal=100, budget=100)
        # ---- actuals: one MO of F through the real cmc_mrp path + indirect overhead of Z
        cls.mo_f = cls._done_mo(cls.f, cls.bom_f, 10.0, {cls.a: 22.0, cls.b: 27.0}, duration=360.0)
        cls._indirect(cls.z, 105, "fixed")
        cls._indirect(cls.z, 40, "variable")
        cls._indirect(cls.z, 999, "fixed", day=TODAY - relativedelta(years=1))
        cls._indirect(cls.y, 77, "fixed")

    # ---- builders ---------------------------------------------------------

    @classmethod
    def _center(cls, name, company=None):
        return cls.AA.create(
            {"name": name, "plan_id": cls.plan.id, "cmc_center_type": "final", "company_id": (company or cls.company).id}
        )

    @classmethod
    def _workcenter(cls, name, center, costs_hour, company=None):
        return cls.env["mrp.workcenter"].create({
            "name": name, "costs_hour": costs_hour, "analytic_distribution": {str(center.id): 100},
            "company_id": (company or cls.company).id,
        })

    @classmethod
    def _product(cls, name, price=0.0, categ=None):
        product = cls.env["product.product"].create(
            {"name": name, "is_storable": True, "categ_id": (categ or cls.categ).id}
        )
        if price:
            product.with_company(cls.company).standard_price = price
        return product

    @classmethod
    def _bom(cls, product, components, operations=(), qty=1.0, company=None):
        return cls.env["mrp.bom"].create({
            "product_tmpl_id": product.product_tmpl_id.id, "product_qty": qty, "type": "normal",
            "company_id": (company or cls.company).id,
            "bom_line_ids": [Command.create({"product_id": comp.id, "product_qty": q}) for comp, q in components],
            "operation_ids": [
                Command.create({"name": "Op %s" % wc.name, "workcenter_id": wc.id, "time_cycle_manual": minutes})
                for wc, minutes in operations
            ],
        })

    @classmethod
    def _budget_line(cls, center, element, behavior, figure, version=None):
        return cls.env["cmc.budget.line"].create({
            "version_id": (version or cls.budget).id, "kind": "cost", "center_id": center.id, "element_id": element.id,
            "behavior": behavior, "driver_id": cls.hours.id if behavior == "variable" else False,
            "date": Y_FROM, "figure": figure,
        })

    @classmethod
    def _capacity(cls, center, theoretical, practical, normal, budget, driver=None):
        return cls.env["cmc.capacity.analysis"].create({
            "center_id": center.id, "company_id": center.company_id.id, "date_from": Y_FROM, "date_to": Y_TO,
            "driver_id": (driver or cls.hours).id, "capacity_theoretical": theoretical,
            "capacity_practical": practical, "capacity_normal": normal, "capacity_budget": budget,
        })

    @classmethod
    def _indirect(cls, center, cost, behavior, day=TODAY):
        return cls.AL.create({
            "name": "STD overhead", "account_id": center.id, "amount": -cost, "company_id": center.company_id.id,
            "cmc_cost_element_id": cls.rent.id, "cmc_behavior": behavior, "cmc_directness": "indirect", "date": day,
        })

    @classmethod
    def _done_mo(cls, product, bom, qty, consumption=None, duration=None, center=None, uom=None):
        """A done MO through the real mrp / cmc_mrp path: MP and MOD analytic lines are booked by the bridge.

        ``qty`` is in ``uom`` (the MO UoM, default the product UoM); ``consumption`` in the raw move UoM.
        """
        consumption = consumption or {}
        mo = cls.env["mrp.production"].create({
            "product_id": product.id, "product_qty": qty, "bom_id": bom.id, "cmc_center_id": (center or cls.z).id,
            **({"product_uom_id": uom.id} if uom else {}),
        })
        mo.action_confirm()
        if duration is not None:
            mo.workorder_ids.duration = duration
        mo.qty_producing = qty
        for move in mo.move_raw_ids:
            move.quantity = consumption.get(move.product_id, move.product_uom_qty)
            move.picked = True
        mo.with_context(skip_consumption=True).button_mark_done()
        mo.invalidate_recordset()
        return mo

    # ---- standard version ---------------------------------------------------

    def _std_version(self, name="STD standard", approve=True, basis="practical", products=None, prices=None):
        """Draft standard version: planned prices A 4 / B 2, rate table prefilled from the budget, cards rolled up."""
        version = self.Version.create(
            {"name": name, "version_type": "standard", "company_id": self.company.id, "date_from": Y_FROM, "date_to": Y_TO}
        )
        prices = prices if prices is not None else {self.a: 4.0, self.b: 2.0}
        self.env["cmc.standard.price"].create(
            [{"version_id": version.id, "product_id": product.id, "price": price} for product, price in prices.items()]
        )
        version.action_cmc_std_prefill_rates(self.budget, basis=basis)
        version.action_cmc_std_rollup(products if products is not None else (self.f | self.g | self.k))
        if approve:
            version.with_user(self.manager).action_approve()
        return version

    @staticmethod
    def _card(version, product):
        return version.card_ids.filtered(lambda card: card.product_id == product)

    @staticmethod
    def _rate_line(version, center, kind):
        return version.std_rate_ids.filtered(lambda line: line.center_id == center and line.rate_kind == kind)

    def _run(self, version, compute=True, user=None, **vals):
        Run = self.env["cmc.standard.variance.run"]
        if user:
            Run = Run.with_user(user)
        run = Run.create({
            "name": "STD variances", "company_id": self.company.id, "version_id": version.id,
            "date_from": P_FROM, "date_to": P_TO, **vals,
        })
        if compute:
            run.action_compute()
        return run

    @staticmethod
    def _vline(run, production, line_type, component=None, center=None):
        return run.line_ids.filtered(
            lambda line: line.production_id == production and line.line_type == line_type
            and (not component or line.component_id == component) and (not center or line.center_id == center)
        )
