"""WP-09 ``cmc_profitability`` - shared fixture and CONTRACT of the red tests.

Capabilities: DC-02, DIF-05, ALC-07, PRF-01..PRF-06, FIN-12 (moved here by AD-101/AD-118).
Sources: gap register rows; Kaplan & Cooper Ch.9-11, Ch.15 (layered product/customer P&L, 2x2 net margin vs
cost-to-serve, price waterfall, supplier TCO, ABC-level transfer pricing); Cokins Ch.3, Ch.5 (margin layers,
whale curve "last point = reported profit", unrealized profit, capital charge -> economic profit); Turney Ch.8-10
(price floor, reprice, minimum order); Fazzini 12.4-12.7 (MSLC, PN figurativo, FCN = MSLC - dCOIN, ROCE, g,
five transfer price criteria). AD-102 (depends), AD-106 (cost objects, unassigned object, snapshots), AD-108
(rates), AD-109 (attributes), AD-110 (actuals only), AD-111 (revenue = -balance of posted income items, credit
notes netted), AD-112 (natural sign), AD-113 (runs), AD-114 (fact grain), AD-115 (security), AD-118 (scope).

CONTRACT (whole WP-09; each test file repeats the part it pins)
Sign (AD-112, binding for every amount of this module): stored natural, revenue POSITIVE, cost NEGATIVE;
every margin is the signed sum (revenue + costs), positive = profit. Rates/prices/percentages are positive.
Run ``cmc.profit.run`` (``_inherit = ["cmc.run.mixin"]``, ``_check_company_auto``, company rule parent_of)
- inputs (``_cmc_input_fields``): ``name``, ``company_id`` (default env company), ``date_from``, ``date_to``
  (required; to < from -> ValidationError), ``abc_run_id`` (M2o cmc.abc.run, check_company, optional),
  ``cost_version_id`` (M2o cmc.plan.version, optional), ``unit_cost_driver_id`` (M2o cmc.cost.driver),
  ``capital_rate`` (Float, % per year), ``sbu_line_ids`` (O2m ``cmc.profit.sbu.input``, run.line mixin),
  ``fin_analysis_id`` (M2o cmc.fin.analysis, optional).
- Compute (UserError, nothing written): ``abc_run_id`` not computed, or its span not inside the run span;
  ``cost_version_id`` not approved; ``fin_analysis_id`` not computed. Records ``cmc.run.usage`` of the ABC run,
  the version and the fin analysis (AD-002: they cannot be reset while this run is computed).
- Revenue (AD-111): ONLY via the cmc_account revenue reader (posted, income/income_other, ALL move types),
  aggregated at the fact grain product x commercial partner x salesperson (``move_id.invoice_user_id``) x month
  (AD-114 (2)) - never line by line. Quantity is netted by document sign (a credit note of 2 units = -2).
  An income item without product and partner -> the fact with product False / partner False whose
  ``customer_object_id`` is the company ``unassigned`` cost object (res_model ``res.company``).
- Unit cost (AD-108): ``cmc.rate._cmc_lookup(product cost object, unit_cost_driver_id, month, origin=
  cost_version_id or None, component=...)`` "variable" -> ``variable_unit_cost``, "total" -> ``full_unit_cost``.
  A sold product without a variable rate: unit costs 0 and the product in ``missing_cost_product_ids``.
- ABC costs (from ``abc_run_id``, all read as costs, i.e. stored negative here):
  product-sustaining = ABC flows (stage ``activity``) whose source activity has ``cost_level = 'product'``,
  per target product object; spread over the facts of that product by quantity (largest remainder);
  cost-to-serve = ABC ``result_ids.amount`` of customer objects; channel cost = ABC ``result_ids.amount`` of
  channel objects (res_model ``cmc.attribute.value``), spread over the customers of the channel pro rata
  to their revenue; business-sustaining = ABC ``sustaining_cost`` (company level only, never on a customer);
  supplier activity cost = ABC ``result_ids.amount`` of supplier objects.
- Header outputs: ``revenue_total``, ``gl_revenue`` (reader total at Compute), ``variable_cost_total``,
  ``contribution_total`` (M1), ``product_sustaining_total``, ``cost_to_serve_total``, ``channel_cost_total``,
  ``business_sustaining_cost``, ``operating_profit`` (= sum of customer_profit + business_sustaining_cost),
  ``capital_charge_total``, ``economic_profit`` (= operating_profit + capital_charge_total),
  ``ledger_cost`` (analytic cost base of company x span, non-excluded, via the cmc_base cost reader, negative),
  ``assigned_cost`` (product-sustaining + cost-to-serve + channel + business-sustaining + supplier activity),
  ``unassigned_cost`` (= ledger_cost - assigned_cost; |assigned| > |ledger| -> UserError, fail loud),
  ``whale_total`` / ``whale_peak`` / ``whale_unrealized`` (= peak - total), ``quadrant_margin_threshold`` /
  ``quadrant_cost_threshold``, ``missing_cost_product_ids`` (M2m product.product).
- ``run._cmc_reconciliation()`` -> {"gl_revenue": live reader total now, "run_revenue": revenue_total,
  "difference": live - run}: a back-dated posting shows up here, never inside a computed run (AD-111 inv.3).
Results (``cmc.run.result.mixin``, in ``_cmc_result_fields``, company_id related stored, deleted by reset)
- ``fact_ids`` -> ``cmc.profit.fact``: ``date`` (month start), ``product_id``, ``partner_id`` (commercial),
  ``salesperson_id``, snapshots ``segment_value_id`` / ``channel_value_id`` (partner attributes SEGMENT /
  CHANNEL at Compute) and ``sbu_value_id`` (product template attribute SBU), ``product_object_id``,
  ``customer_object_id``, ``quantity``, ``revenue``, ``list_amount`` (product ``lst_price`` x quantity; =
  revenue without product), ``discount_amount`` (= revenue - list_amount), ``unit_price`` (revenue / qty),
  ``variable_unit_cost``, ``full_unit_cost``, ``variable_cost``, ``contribution_margin``,
  ``product_sustaining_cost``, ``floor_status``. RN-001: ``quantity`` is ALWAYS in the product UoM (an invoice
  line in a packaging UoM, e.g. 1 Dozen of a product in Units, counts 12), so ``list_amount`` = lst_price x 12,
  ``variable_cost`` = -12 x variable rate and ``unit_price`` / ``avg_price`` are per product unit (floor check on
  the same unit as the rate); ``action_cmc_open_journal_items()`` -> act_window on
  ``account.move.line`` whose domain is exactly the posted income items of the fact (run as the user, no sudo).
- ``product_ids`` -> ``cmc.profit.product`` (one per sold product + one product False): ``product_id``,
  ``cost_object_id``, ``sbu_value_id``, ``quantity``, ``revenue``, ``variable_cost``, ``contribution_margin``,
  ``product_sustaining_cost``, ``product_margin``, ``avg_price``, ``variable_unit_cost``, ``full_unit_cost``,
  ``floor_status`` (``below_variable`` price < variable unit cost / ``below_full`` price < full unit cost /
  ``ok``), ``rank``, ``cumulative_profit_pct`` (ranked by product_margin desc, product False rank 0).
- ``customer_ids`` -> ``cmc.profit.customer`` (one per commercial partner + one partner False = unassigned):
  ``partner_id``, ``cost_object_id``, ``segment_value_id``, ``channel_value_id``, ``revenue``,
  ``variable_cost``, ``contribution_margin`` (M1), ``product_sustaining_cost``, ``product_margin`` (M2),
  ``cost_to_serve``, ``served_margin`` (M3), ``channel_cost``, ``customer_profit`` (M4), ``receivable_balance``
  (posted receivable balance of the partner at date_to, cumulative), ``capital_charge`` (= -receivable x
  capital_rate/100 x months of span/12), ``economic_profit``, ``order_count`` (posted out_invoice in span),
  ``list_amount``, ``discount_amount``, ``off_invoice_amount``, ``pocket_amount`` (= revenue +
  off_invoice_amount), ``rank`` / ``cumulative_profit`` / ``cumulative_profit_pct`` (whale: partners ranked by
  customer_profit desc, unassigned rank 0), ``net_margin_pct`` (product_margin / revenue x 100),
  ``cost_to_serve_pct`` (-cost_to_serve / revenue x 100), ``quadrant`` ``passive`` (margin >= threshold, cts <
  threshold) / ``carriage`` (>=, >=) / ``bargain`` (<, <) / ``aggressive`` (<, >=); thresholds = the same ratios
  over all ranked customers. ``run._cmc_whatif_surcharge(surcharge, min_order_amount=0.0)`` -> {partner id:
  customer_profit + surcharge x number of posted out_invoice of the span with untaxed amount < min (0 = all)}.
- ``channel_ids`` -> ``cmc.profit.channel``: ``channel_value_id`` (False = none), ``revenue``,
  ``served_margin``, ``channel_cost``, ``channel_margin``.
- ``waterfall_ids`` -> ``cmc.profit.waterfall``: ``partner_id``, ``element_id``, ``amount`` (-balance).
  Config ``cmc.profit.price.element`` (manager): ``name``, ``code``, ``sequence``, ``company_id``,
  ``account_ids`` M2m account.account (an income/income_other account -> ValidationError: it is already in
  revenue). Amount = posted items of those accounts with that partner in span.
- ``supplier_ids`` -> ``cmc.profit.supplier`` (PRF-05): ``partner_id``, ``cost_object_id``, ``purchase_amount``
  (posted in_invoice/in_refund product lines of the span, negative), ``activity_cost``, ``tco``, ``tco_index``
  (tco / purchase_amount), ``rank`` (by tco_index asc).
- ``sbu_ids`` -> ``cmc.profit.sbu`` (FIN-12): ``sbu_value_id`` (False = corporate), ``revenue``,
  ``variable_cost``, ``product_sustaining_cost``, ``mslc`` (revenue + variable + product-sustaining),
  ``coin_open``, ``coin_close``, ``pfn``, ``equity`` (PN figurativo = coin_close - pfn), ``roce`` (mslc /
  coin_open x 100), ``growth`` ((close - open) / open x 100), ``cash_flow`` (FCN = mslc - (close - open)),
  ``sustainable`` (cash_flow > 0). ``cmc.profit.sbu.input``: ``run_id``, ``sbu_value_id``, ``coin_open``,
  ``coin_close``, ``pfn``, ``capital_key`` (%): with ``fin_analysis_id`` set the three amounts come from the
  fin run values ``coin`` (year of date_to and the year before) and ``pfn`` x capital_key / 100.
- ``run.action_cmc_generate_direct_costing(dimension="product"|"customer"|"segment")`` (DC-02) -> new
  ``cmc.direct.costing`` (mode ``evoluto``, positive figures as that model wants): one line per row of the
  dimension (+ "Unassigned"), specific fixed = the run costs of that row's own level (product: sustaining;
  customer/segment: sustaining + cost-to-serve + channel), common fixed = the rest, so its
  ``operating_result`` = the run ``operating_profit``.
Shipped data: ``cmc_profitability.cmc_attribute_sbu`` (cmc.attribute, code ``SBU``, company False, target
product.template). Security (AD-115): group_cmc_user computes runs and reads results, manager configures price
elements and transfer rules; results read-only; non-CMC users no access; rules not noupdate.
ALC-07 ``cmc.transfer.charge``: see test_transfer_pricing.py (RN-002: the provider cost read EXCLUDES the
analytic lines posted by any chargeback, ``cmc_transfer_charge_id`` set, so a second run on the same provider and
span reads the primary cost again; a provider cost of 0 -> UserError, nothing written).

MAIN FIXTURE (company A of AccountTestInvoicingCommon, March 2027, no taxes)
  Sales: INV1 C1 / S1: P1 10 @100 -10% = 900, P2 20 @50 = 1000; INV2 C2 / S2: P1 10 @100 = 1000;
  INV3 C3 / S2: P2 10 @50 = 500; RFD1 credit note to "C1 Buyer" (contact of C1) / S1: P2 2 @50 = -100;
  MISC1 bank / revenue 200 (no partner, no product). GL revenue 3500.
  Distractors: INV4 C1 2027-04-02 P1 1 @100; INV5 draft C2 March P1 9 @111.
  Purchases: VB1 V1 1000, VB2 V2 950 (expense). MISC2: freight out 60 (partner C1), rebates 40 (C3).
  Standard version (approved): P1 variable 40 / total 60, P2 20/30, P3 20/35, P4 10/30 (unit = WP09UNIT).
  Ledger (analytic, March): Z 340, S 1000, K 200, G 205, Q 200, W 160 (no resource) = 2105; Z 999 in April.
  ABC run (March, every resource 100% to one activity): PRODSUP (product) P1 10 / P2 7 -> 200 / 140;
  SERVE (customer) C1 3 / C2 2 / C3 5 -> 300 / 200 / 500; CHAN Direct 10 / Distributor 30 -> 50 / 150;
  PROC V1 1 / V2 3 -> 50 / 150; MGMT (facility) -> sustaining 205. Assigned 1945, unassigned 160.
  Attributes: C1 Direct / Key; C2, C3 Distributor / SME.
  Facts: F1 P1 C1 S1 qty 10 rev 900 list 1000 var -400 M1 500 ps -100; F2 P2 C1 S1 qty 18 rev 900 list 900
  var -360 M1 540 ps -90; F3 P1 C2 S2 qty 10 rev 1000 var -400 M1 600 ps -100; F4 P2 C3 S2 qty 10 rev 500
  var -200 M1 300 ps -50; F5 unassigned rev 200 M1 200.
  Customers  rev   var   M1    ps    M2   cts   M3    ch   M4   AR    cap  econ
  C1        1800  -760 1040  -190   850 -300   550  -50  500 1800  -18   482
  C2        1000  -400  600  -100   500 -200   300 -100  200 1000  -10   190
  C3         500  -200  300   -50   250 -500  -250  -50 -300  500   -5  -305
  unassigned 200     0  200     0   200    0   200    0  200    0    0   200
  Company: M4 600 - sustaining 205 = operating profit 395; capital -33; economic 362.
  Whale: C1 500 (125%), C2 700 (175%), C3 400 (100%): peak 700, total 400, unrealized 300.
  Quadrants: thresholds 1600/3300 = 48.48% margin, 1000/3300 = 30.30% cts: C1 bargain, C2 passive, C3 carriage.
  Waterfall: C1 list 1900 disc -100 invoice 1800 freight -60 pocket 1740; C3 500 rebate -40 pocket 460.
  TCO: V1 -1000 -50 = -1050 (1.05, rank 1); V2 -950 -150 = -1100 (1.1579, rank 2).
"""

from datetime import date

from odoo import Command
from odoo.tests.common import new_test_user

from odoo.addons.account.tests.common import AccountTestInvoicingCommon

D_FROM = date(2027, 3, 1)
D_TO = date(2027, 3, 31)
D_MID = date(2027, 3, 15)


class ProfitCase(AccountTestInvoicingCommon):
    @classmethod
    def get_default_groups(cls):
        # why: AccountTestInvoicingCommon runs as `accountman`; CMC readers and runs are CMC-gated
        return super().get_default_groups() | cls.env.ref("cmc_base.group_cmc_manager")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.company_data_b = cls.setup_other_company(name="CMC WP09 B")
        cls.company_b = cls.company_data_b["company"]
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=cls.company.ids))
        cls.plan = (
            cls.env["account.analytic.plan"]
            .search([("parent_id", "=", False)])
            .filtered(lambda p: p._column_name() == "account_id")[:1]
        )
        # ---- users
        cls.salesperson_1 = new_test_user(cls.env, login="wp09_sales_1", groups="base.group_user",
                                          company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)])
        cls.salesperson_2 = new_test_user(cls.env, login="wp09_sales_2", groups="base.group_user",
                                          company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)])
        cls.cmc_user = new_test_user(cls.env, login="wp09_cmc_user", groups="base.group_user,cmc_base.group_cmc_user",
                                     company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)])
        cls.cmc_user_b = new_test_user(cls.env, login="wp09_cmc_user_b", groups="base.group_user,cmc_base.group_cmc_user",
                                       company_id=cls.company_b.id, company_ids=[Command.set(cls.company_b.ids)])
        cls.outsider = new_test_user(cls.env, login="wp09_outsider", groups="base.group_user",
                                     company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)])
        # ---- attributes (AD-109, shipped by cmc_base)
        Value = cls.env["cmc.attribute.value"]
        channel = cls.env.ref("cmc_base.cmc_attribute_channel")
        segment = cls.env.ref("cmc_base.cmc_attribute_customer_segment")
        cls.ch_direct = Value.create({"attribute_id": channel.id, "name": "WP09 Direct", "code": "WP09DIR"})
        cls.ch_dist = Value.create({"attribute_id": channel.id, "name": "WP09 Distributor", "code": "WP09DIS"})
        cls.seg_key = Value.create({"attribute_id": segment.id, "name": "WP09 Key", "code": "WP09KEY"})
        cls.seg_sme = Value.create({"attribute_id": segment.id, "name": "WP09 SME", "code": "WP09SME"})
        Partner = cls.env["res.partner"]
        cls.c1 = Partner.create({"name": "WP09 C1", "is_company": True,
                                 "cmc_attribute_value_ids": [Command.set((cls.ch_direct | cls.seg_key).ids)]})
        cls.c1_buyer = Partner.create({"name": "WP09 C1 Buyer", "parent_id": cls.c1.id, "type": "invoice"})
        cls.c2 = Partner.create({"name": "WP09 C2", "is_company": True,
                                 "cmc_attribute_value_ids": [Command.set((cls.ch_dist | cls.seg_sme).ids)]})
        cls.c3 = Partner.create({"name": "WP09 C3", "is_company": True,
                                 "cmc_attribute_value_ids": [Command.set((cls.ch_dist | cls.seg_sme).ids)]})
        cls.v1 = Partner.create({"name": "WP09 V1", "is_company": True})
        cls.v2 = Partner.create({"name": "WP09 V2", "is_company": True})
        # ---- products (SBU attribute shipped by cmc_profitability: pinned in test_sbu.py)
        Product = cls.env["product.product"]
        cls.p1 = Product.create({"name": "WP09 P1", "list_price": 100.0})
        cls.p2 = Product.create({"name": "WP09 P2", "list_price": 50.0})
        cls.p3 = Product.create({"name": "WP09 P3", "list_price": 30.0})
        cls.p4 = Product.create({"name": "WP09 P4", "list_price": 40.0})
        cls.sbu_attribute = cls.env.ref("cmc_profitability.cmc_attribute_sbu", raise_if_not_found=False)
        if cls.sbu_attribute:
            cls.sbu_a = Value.create({"attribute_id": cls.sbu_attribute.id, "name": "WP09 SBU A", "code": "WP09A"})
            cls.sbu_b = Value.create({"attribute_id": cls.sbu_attribute.id, "name": "WP09 SBU B", "code": "WP09B"})
            cls.p1.product_tmpl_id.cmc_attribute_value_ids = [Command.set(cls.sbu_a.ids)]
            cls.p2.product_tmpl_id.cmc_attribute_value_ids = [Command.set(cls.sbu_b.ids)]
        # ---- cost objects (AD-106)
        Obj = cls.env["cmc.cost.object"]
        cls.obj_p1, cls.obj_p2, cls.obj_p3, cls.obj_p4 = Obj._cmc_ensure_objects(cls.p1 | cls.p2 | cls.p3 | cls.p4, company=cls.company)
        cls.obj_c1, cls.obj_c2, cls.obj_c3 = Obj._cmc_ensure_objects(cls.c1 | cls.c2 | cls.c3, company=cls.company, object_type="customer")
        cls.obj_v1, cls.obj_v2 = Obj._cmc_ensure_objects(cls.v1 | cls.v2, company=cls.company, object_type="supplier")
        cls.obj_direct, cls.obj_dist = Obj._cmc_ensure_objects(cls.ch_direct | cls.ch_dist, company=cls.company, object_type="channel")
        # ---- drivers (AD-107)
        cls.units = cls._driver("WP09UNIT", "transaction", kind="statistical")
        cls.prodcount = cls._driver("WP09PRD", "transaction", level="product")
        cls.orders = cls._driver("WP09ORD", "transaction", level="customer")
        cls.chan_units = cls._driver("WP09CHU", "transaction")
        cls.receipts = cls._driver("WP09RCV", "transaction")
        # ---- standard unit costs (AD-108: planned rates are inputs of a draft version, then approved)
        cls.version = cls.env["cmc.plan.version"].create({
            "name": "WP09 standard 2027", "version_type": "standard", "company_id": cls.company.id,
            "date_from": date(2027, 1, 1), "date_to": date(2027, 12, 31),
        })
        unit_costs = {cls.obj_p1: (40.0, 60.0), cls.obj_p2: (20.0, 30.0), cls.obj_p3: (20.0, 35.0), cls.obj_p4: (10.0, 30.0)}
        cls.env["cmc.rate"]._cmc_publish(cls.version, [
            {"holder": obj, "driver": cls.units, "component": component, "date": month, "value": value}
            for obj, (variable, total) in unit_costs.items()
            for month in (date(2027, 3, 1), date(2027, 4, 1))
            for component, value in (("variable", variable), ("total", total))
        ])
        cls.version.action_approve()
        # ---- ledger (analytic cost base, AD-005: cost = negative amount)
        cls.sal = cls.env["cmc.cost.element"].create({"name": "WP09 salaries", "code": "WP09SAL", "company_id": cls.company.id})
        cls.z = cls._center("WP09 Z product mgmt")
        cls.s = cls._center("WP09 S customer service")
        cls.k = cls._center("WP09 K channel")
        cls.g = cls._center("WP09 G management")
        cls.q = cls._center("WP09 Q procurement")
        cls.w = cls._center("WP09 W warehouse")
        for center, cost in ((cls.z, 340), (cls.s, 1000), (cls.k, 200), (cls.g, 205), (cls.q, 200), (cls.w, 160)):
            cls._cost(center, cost)
        cls._cost(cls.z, 999, day=date(2027, 4, 3))
        # ---- ABC run (WP-07 producer of cost-to-serve, product-sustaining, channel, supplier, sustaining)
        cls.abc_run = cls._abc_run()
        # ---- general ledger (AD-111)
        cls.revenue_account = cls.company_data["default_account_revenue"]
        cls.bank_account = cls.company_data["default_journal_bank"].default_account_id
        Account = cls.env["account.account"]
        cls.freight_account = Account.create({"name": "WP09 Freight out", "code": "609100", "account_type": "expense"})
        cls.rebate_account = Account.create({"name": "WP09 Customer rebates", "code": "609200", "account_type": "expense"})
        cls.inv1 = cls._invoice(cls.c1, [(cls.p1, 10, 100.0, 10.0), (cls.p2, 20, 50.0, 0.0)], date(2027, 3, 10), user=cls.salesperson_1)
        cls.inv2 = cls._invoice(cls.c2, [(cls.p1, 10, 100.0, 0.0)], date(2027, 3, 12), user=cls.salesperson_2)
        cls.inv3 = cls._invoice(cls.c3, [(cls.p2, 10, 50.0, 0.0)], date(2027, 3, 15), user=cls.salesperson_2)
        cls.rfd1 = cls._invoice(cls.c1_buyer, [(cls.p2, 2, 50.0, 0.0)], date(2027, 3, 20), move_type="out_refund", user=cls.salesperson_1)
        cls.misc1 = cls._entry(date(2027, 3, 25), [(cls.bank_account, 200.0, False), (cls.revenue_account, -200.0, False)])
        cls.inv4 = cls._invoice(cls.c1, [(cls.p1, 1, 100.0, 0.0)], date(2027, 4, 2), user=cls.salesperson_1)
        cls.inv5 = cls._invoice(cls.c2, [(cls.p1, 9, 111.0, 0.0)], date(2027, 3, 30), user=cls.salesperson_2, post=False)
        cls.vb1 = cls._invoice(cls.v1, [(cls.product_a, 1, 1000.0, 0.0)], date(2027, 3, 8), move_type="in_invoice")
        cls.vb2 = cls._invoice(cls.v2, [(cls.product_a, 1, 950.0, 0.0)], date(2027, 3, 9), move_type="in_invoice")
        cls.misc2 = cls._entry(date(2027, 3, 28), [
            (cls.freight_account, 60.0, cls.c1), (cls.rebate_account, 40.0, cls.c3), (cls.bank_account, -100.0, False),
        ])

    # ---- builders ---------------------------------------------------------

    @classmethod
    def _driver(cls, code, mtype, level=False, kind="allocation", company=None):
        company = company or cls.company
        env = cls.env(context=dict(cls.env.context, allowed_company_ids=company.ids))
        return env["cmc.cost.driver"].create({
            "name": "Driver %s" % code, "code": code, "measure_kind": kind, "measure_type": mtype,
            "cmc_abc_level": level, "company_id": company.id,
        })

    @classmethod
    def _center(cls, name, company=None, center_type="final"):
        company = company or cls.company
        return cls.env["account.analytic.account"].create(
            {"name": name, "plan_id": cls.plan.id, "cmc_center_type": center_type, "company_id": company.id}
        )

    @classmethod
    def _cost(cls, center, cost, day=D_MID, behavior="fixed", element=None):
        return cls.env["account.analytic.line"].create({
            "name": "wp09 cost", "account_id": center.id, "amount": -cost, "company_id": center.company_id.id,
            "cmc_cost_element_id": (element or cls.sal).id, "cmc_behavior": behavior, "date": day,
        })

    @classmethod
    def _abc_run(cls):
        Act = cls.env["cmc.abc.activity"]
        process = Act.create({"name": "WP09 Sell and serve", "code": "WP09PSS", "node_type": "process"})

        def activity(name, code, level, driver):
            return Act.create({"name": name, "code": code, "node_type": "activity", "parent_id": process.id,
                               "cost_level": level, "driver_id": driver.id if driver else False})

        cls.act_prodsup = activity("Maintain products", "WP09APS", "product", cls.prodcount)
        cls.act_serve = activity("Serve customers", "WP09ASV", "customer", cls.orders)
        cls.act_chan = activity("Support channels", "WP09ACH", False, cls.chan_units)
        cls.act_proc = activity("Procure", "WP09APR", False, cls.receipts)
        cls.act_mgmt = activity("Manage the business", "WP09AMG", "facility", False)
        Res = cls.env["cmc.abc.resource"]
        resource_lines = []
        for center, act, code in ((cls.z, cls.act_prodsup, "Z"), (cls.s, cls.act_serve, "S"), (cls.k, cls.act_chan, "K"),
                                  (cls.g, cls.act_mgmt, "G"), (cls.q, cls.act_proc, "Q")):
            res = Res.create({"name": "WP09 res %s" % code, "code": "WP09R%s" % code, "center_id": center.id,
                              "driver_basis": "percent"})
            resource_lines.append(Command.create({"resource_id": res.id, "activity_id": act.id, "percent": 100}))

        def edge(act, obj, qty):
            return Command.create({"activity_id": act.id, "cost_object_id": obj.id, "driver_id": act.driver_id.id, "driver_qty": qty})

        run = cls.env["cmc.abc.run"].create({
            "name": "WP09 ABC March", "company_id": cls.company.id, "date_from": D_FROM, "date_to": D_TO,
            "resource_line_ids": resource_lines,
            "line_ids": [
                edge(cls.act_prodsup, cls.obj_p1, 10), edge(cls.act_prodsup, cls.obj_p2, 7),
                edge(cls.act_serve, cls.obj_c1, 3), edge(cls.act_serve, cls.obj_c2, 2), edge(cls.act_serve, cls.obj_c3, 5),
                edge(cls.act_chan, cls.obj_direct, 10), edge(cls.act_chan, cls.obj_dist, 30),
                edge(cls.act_proc, cls.obj_v1, 1), edge(cls.act_proc, cls.obj_v2, 3),
            ],
        })
        run.action_compute()
        return run

    @classmethod
    def _invoice(cls, partner, lines, day, move_type="out_invoice", user=None, post=True, data=None):
        data = data or cls.company_data
        sale = move_type in ("out_invoice", "out_refund")
        account = data["default_account_revenue"] if sale else data["default_account_expense"]
        move = cls.env["account.move"].with_company(data["company"]).create({
            "move_type": move_type,
            "partner_id": partner.id,
            "invoice_date": day,
            "date": day,
            "invoice_user_id": user.id if user else False,
            "journal_id": data["default_journal_sale" if sale else "default_journal_purchase"].id,
            "invoice_line_ids": [
                Command.create({
                    "product_id": product.id, "quantity": qty, "price_unit": price, "discount": discount,
                    "account_id": account.id, "tax_ids": [Command.clear()],
                })
                for product, qty, price, discount in lines
            ],
        })
        if post:
            move.action_post()
        return move

    @classmethod
    def _entry(cls, day, lines, data=None):
        """Misc journal entry; lines = (account, balance (debit positive), partner)."""
        data = data or cls.company_data
        move = cls.env["account.move"].with_company(data["company"]).create({
            "move_type": "entry",
            "date": day,
            "journal_id": data["default_journal_misc"].id,
            "line_ids": [
                Command.create({
                    "name": "wp09 entry", "account_id": account.id, "partner_id": partner.id if partner else False,
                    "debit": balance if balance > 0 else 0.0, "credit": -balance if balance < 0 else 0.0,
                })
                for account, balance, partner in lines
            ],
        })
        move.action_post()
        return move

    @classmethod
    def _price_elements(cls):
        Element = cls.env["cmc.profit.price.element"]
        cls.el_freight = Element.create({"name": "Freight", "code": "FREIGHT", "sequence": 10, "company_id": cls.company.id,
                                         "account_ids": [Command.set(cls.freight_account.ids)]})
        cls.el_rebate = Element.create({"name": "Rebates", "code": "REBATE", "sequence": 20, "company_id": cls.company.id,
                                        "account_ids": [Command.set(cls.rebate_account.ids)]})
        return cls.el_freight | cls.el_rebate

    def _run(self, compute=True, env=None, elements=True, **vals):
        if elements and not self.env["cmc.profit.price.element"].search_count([("company_id", "=", self.company.id)]):
            self._price_elements()
        run = (env or self.env)["cmc.profit.run"].create({
            "name": "WP09 March",
            "company_id": self.company.id,
            "date_from": D_FROM,
            "date_to": D_TO,
            "abc_run_id": self.abc_run.id,
            "cost_version_id": self.version.id,
            "unit_cost_driver_id": self.units.id,
            "capital_rate": 12.0,
            **vals,
        })
        if compute:
            run.action_compute()
        return run

    def _packaging_sale(self):
        """RN-001 fixture: P5 in Units, list 10, own standard version variable 8 / total 9 (March); 1 Dozen @90 to C2."""
        self.p5 = self.env["product.product"].create({"name": "WP09 P5 packaged", "list_price": 10.0, "uom_id": self.env.ref("uom.product_uom_unit").id})
        obj_p5 = self.env["cmc.cost.object"]._cmc_ensure_objects(self.p5, company=self.company)
        version = self.env["cmc.plan.version"].create({
            "name": "WP09 RN-001 std", "version_type": "standard", "company_id": self.company.id,
            "date_from": date(2027, 1, 1), "date_to": date(2027, 12, 31),
        })
        self.env["cmc.rate"]._cmc_publish(version, [
            {"holder": obj_p5, "driver": self.units, "component": component, "date": D_FROM, "value": value}
            for component, value in (("variable", 8.0), ("total", 9.0))
        ])
        version.action_approve()
        move = self.env["account.move"].with_company(self.company).create({
            "move_type": "out_invoice", "partner_id": self.c2.id, "invoice_date": D_MID, "date": D_MID,
            "invoice_user_id": self.salesperson_2.id, "journal_id": self.company_data["default_journal_sale"].id,
            "invoice_line_ids": [Command.create({
                "product_id": self.p5.id, "quantity": 1.0, "product_uom_id": self.env.ref("uom.product_uom_dozen").id,
                "price_unit": 90.0, "account_id": self.revenue_account.id, "tax_ids": [Command.clear()],
            })],
        })
        move.action_post()
        return self._run(cost_version_id=version.id)

    # ---- row pickers --------------------------------------------------------

    @staticmethod
    def _fact(run, product, partner):
        return run.fact_ids.filtered(lambda f: f.product_id == product and f.partner_id == partner)

    @staticmethod
    def _customer(run, partner):
        return run.customer_ids.filtered(lambda r: r.partner_id == partner)

    @staticmethod
    def _product(run, product):
        return run.product_ids.filtered(lambda r: r.product_id == product)
