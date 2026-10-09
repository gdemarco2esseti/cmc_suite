"""WP-10 - shared fixture of the ``cmc_direct_costing`` v2 red tests (decisions).

Capabilities: DC-03, DC-05, DC-08, DC-09, DIF-01, DIF-03, DIF-04, DIF-06 + G-1 (moved from WP-01).
Sources: gap register rows; Cinquini II.2-II.6, III.3, III.4 (make-or-buy, drop line, replacement
"120k a favore sostituzione", split-off); Cokins exec Ch.4/Ch.8 (rule-based quote, margin acceptance,
decision-dependent inclusion of sustaining/idle); LCC Ch.5-6 and PCM Ch.2/Ch.4 (NPV, IRR, payback,
inflation, r = 0); AD-101 (in place), AD-108 (rates), AD-113 (decision = run), AD-115, AD-118 (WP-10
must: no multi-constraint LP), AD-119 (pure maths in cmc_utils).

CONTRACT (whole WP-10; each test file repeats the part it covers)
``cmc.decision`` (new, persisted, inherits ``cmc.run.mixin``: compute from draft, locked when done,
reset clears outputs and result rows; company rule parent_of; ``_check_company_auto``)
- Header inputs (all in ``_cmc_input_fields``): ``name`` required, ``company_id`` required (default env
  company), ``currency_id`` related, ``date`` Date required (month used for rate lookups),
  ``decision_type`` Selection ``make_or_buy`` / ``drop_line`` / ``special_order`` / ``replace`` /
  ``generic`` / ``mix`` / ``investment`` required.
  make_or_buy: ``volume`` Float, ``buy_price_unit`` Monetary, ``opportunity_cost`` Monetary (added to the
  make = current column).
  special_order (quote, DIF-04): ``quote_price`` Monetary, ``target_margin_pct`` Float (percent),
  ``capital_charge`` Monetary, ``include_idle`` / ``include_sustaining`` Boolean,
  ``idle_burden_pct`` / ``sustaining_burden_pct`` Float (percent of the activity cost).
  mix (DC-03): ``resource_available`` Float (> 0 at Compute, else UserError), ``fixed_cost`` Monetary,
  ``mix_ranking`` Selection ``contribution`` (default, MC per unit of resource) / ``throughput`` (TOC).
  investment (DIF-06): ``discount_rate``, ``inflation_rate``, ``tax_rate`` Float (percent);
  ``rate_basis`` / ``flows_basis`` Selection ``nominal`` (default) / ``real``.
- Input lines (``cmc.run.line.mixin``, ``_cmc_run_field = "decision_id"``, ondelete cascade, company_id
  related stored + company rule):
  ``line_ids`` -> ``cmc.decision.line`` (DIF-01): ``name`` required, ``line_type`` Selection
  ``revenue`` / ``cost`` required, ``element_id`` M2o cmc.cost.element, ``cost_behavior`` Selection
  ``variable`` / ``fixed`` (default fixed), ``amount_current`` / ``amount_alternative`` Monetary,
  ``avoidable`` / ``sunk`` Boolean stored computed from ``element_id.cmc_avoidable`` / ``cmc_sunk``,
  readonly=False (CLS-04 flags, overridable per line), ``relevant`` Boolean stored computed = not sunk and
  the two columns differ.
  ``activity_ids`` -> ``cmc.decision.activity`` (DIF-03/04): ``name``, ``holder_model`` Char +
  ``holder_id`` Many2oneReference (any rate holder: activity, center, resource), ``driver_id`` required,
  ``quantity`` Float, ``side`` Selection ``current`` / ``alternative`` (default alternative); readonly
  ``rate_value`` Float and ``amount`` Monetary written by Compute (snapshot, AD-113).
  ``mix_line_ids`` -> ``cmc.decision.mix.line``: ``name`` required, ``product_id``, ``price_unit``,
  ``variable_unit``, ``material_unit`` Monetary, ``resource_per_unit`` Float (> 0: ValidationError),
  ``max_demand`` Float.
  ``cash_flow_ids`` -> ``cmc.decision.cash.flow``: ``year`` Integer (>= 0), ``amount`` Monetary (net
  pre-tax flow, year 0 investment negative), ``depreciation`` Monetary; one line per (decision, year).
- Results (``cmc.run.result.mixin``, written by Compute only): ``mix_result_ids`` ->
  ``cmc.decision.mix.result`` (``mix_line_id``, ``rank`` 1 = first served, ``mc_unit``,
  ``mc_per_resource``, ``throughput_per_resource``, ``planned_qty``, ``resource_used``, ``contribution``,
  ``throughput``); ``cash_flow_result_ids`` -> ``cmc.decision.cash.flow.result`` (``year``, ``amount``,
  ``tax``, ``net_flow``, ``discount_factor``, ``present_value``, ``cumulative_flow``, ``cumulative_pv``).
- Header outputs (readonly, written by Compute only - a user write is UserError -, cleared by reset):
  ``current_total`` / ``alternative_total`` (profit view of each column = revenues - costs of the non-sunk
  lines - activity costs of the side), ``differential`` =
  alternative - current, ``verdict`` Selection ``current`` / ``alternative`` / ``indifferent`` (sign at the
  currency step; False for mix), ``verdict_label`` Char (type wording, e.g. make_or_buy: "Make"/"Buy");
  make_or_buy: ``has_indifference``, ``indifference_qty``; special_order: ``activity_cost``,
  ``idle_burden``, ``sustaining_burden``, ``quote_cost``, ``quote_margin``, ``quote_margin_pct``;
  mix: ``mix_total_mc``, ``mix_total_throughput``, ``mix_result`` (= total MC - fixed cost),
  ``resource_used``; investment: ``effective_rate`` (percent), ``npv``, ``has_irr``, ``irr`` (percent),
  ``has_payback``, ``payback_years``, ``has_discounted_payback``, ``discounted_payback_years``, ``roi``
  (percent), ``cbr``.
- Float digits (assertRecordValues compares a Float without digits exactly): percents ``(16, 2)``
  (``quote_margin_pct``, ``mix``/burden pcts), ``irr`` / ``effective_rate`` / ``roi`` ``(16, 6)``, ``cbr``
  ``(16, 4)``, ``rate_value`` ``(16, 6)``, quantities (``indifference_qty``, ``planned_qty``,
  ``resource_used``) ``"Product Unit"``, ``mc_per_resource`` / ``throughput_per_resource`` ``(16, 4)``.
- Engine rules: see each test file. Rates: ``cmc.rate._cmc_lookup(holder, driver, decision.date,
  company=decision.company_id)``; missing rate -> UserError naming the line; the origins read are
  recorded with ``_cmc_record_usage`` (AD-002: the producer run cannot be reset while the decision is
  computed). Every Compute reads its rates in one batch (no per-line search, AD-114).
- The transient ``cmc.differential.analysis`` is superseded (AD-118 "replaces the transient wizard"):
  its 3 types live on in ``cmc.decision``.
Security: group_cmc_user read/write/create on decision + lines (no unlink of a decision), read on results;
manager full; plain internal users nothing.
"""

from datetime import date

from odoo.tests.common import TransactionCase, new_test_user

from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE

D_RATE = date(2026, 1, 1)
D_DECISION = date(2026, 1, 15)


class Wp10Case(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        cls.c_machining = cls._center("WP10 machining")
        cls.c_setup = cls._center("WP10 setup")
        cls.c_receiving = cls._center("WP10 receiving")
        cls.env["account.analytic.line"].create(
            {"name": "WP10 cost", "account_id": cls.c_machining.id, "amount": -900, "date": date(2026, 1, 10)}
        )
        Driver = cls.env["cmc.cost.driver"]
        cls.mh = Driver.create({"name": "WP10 machine hours", "code": "WP10MH"})
        cls.setups = Driver.create({"name": "WP10 setups", "code": "WP10SU"})
        cls.receipts = Driver.create({"name": "WP10 receipts", "code": "WP10RC"})
        Element = cls.env["cmc.cost.element"]
        cls.el_avoidable = Element.create({"name": "WP10 avoidable", "code": "WP10AV", "cmc_avoidable": True})
        cls.el_fixed = Element.create({"name": "WP10 committed", "code": "WP10FX", "cmc_avoidable": False})
        cls.el_sunk = Element.create({"name": "WP10 sunk", "code": "WP10SK", "cmc_sunk": True})
        # the producer of the rates: a computed run (ABC v2 in production, any run in the registry)
        cls.producer = cls.env["cmc.closing.run"].create(
            {"name": "WP10 producer", "date_from": D_RATE, "date_to": date(2026, 1, 31), "method": "direct"}
        )
        cls.producer.action_compute()
        cls.env["cmc.rate"].with_context(cmc_run_state=CMC_RUN_STATE)._cmc_publish(
            cls.producer,
            [
                {"holder": cls.c_machining, "driver": cls.mh, "date": D_RATE, "value": 120.0},
                {"holder": cls.c_setup, "driver": cls.setups, "date": D_RATE, "value": 150.0},
                {"holder": cls.c_receiving, "driver": cls.receipts, "date": D_RATE, "value": 25.0},
            ],
        )
        cls.user = new_test_user(cls.env, login="cmc_wp10_user", groups="base.group_user,cmc_base.group_cmc_user")
        cls.manager = new_test_user(cls.env, login="cmc_wp10_manager", groups="base.group_user,cmc_base.group_cmc_manager")
        cls.outsider = new_test_user(cls.env, login="cmc_wp10_outsider", groups="base.group_user")

    @classmethod
    def _center(cls, name, company=None):
        vals = {"name": name, "plan_id": cls.plan.id, "cmc_center_type": "final"}
        if company:
            vals["company_id"] = company.id
        return cls.env["account.analytic.account"].create(vals)

    def _decision(self, decision_type, user=None, **kw):
        vals = {"name": "WP10 %s" % decision_type, "decision_type": decision_type, "date": D_DECISION}
        vals.update(kw)
        model = self.env["cmc.decision"]
        if user:
            model = model.with_user(user)
        return model.create(vals)

    @staticmethod
    def _line(name, line_type, current=0.0, alternative=0.0, **kw):
        return (0, 0, {"name": name, "line_type": line_type, "amount_current": current, "amount_alternative": alternative, **kw})

    @staticmethod
    def _act(holder, driver, quantity, side="alternative", name=None):
        return (0, 0, {"name": name or "%s x %s" % (driver.name, quantity), "holder_model": holder._name,
                       "holder_id": holder.id, "driver_id": driver.id, "quantity": quantity, "side": side})
