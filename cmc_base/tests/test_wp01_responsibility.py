"""WP-01 / CEN-03 + CLS-04 - responsibility centers, decision flags on the cost element, controllable cost.

CONTRACT
- ``cmc.cost.element`` decision flags (AD-109: plain fields on the element, engines filter on them):
  ``cmc_controllable`` Boolean (default True), ``cmc_avoidable`` Boolean (default False), ``cmc_sunk`` Boolean
  (default False), ``cmc_commitment`` Selection ``committed`` / ``discretionary`` (optional). A sunk cost
  cannot be avoidable -> ValidationError.
- ``account.analytic.account._cmc_cost_groups(..., controllable=None)``: new keyword; ``True`` keeps only
  lines whose element is controllable, ``False`` only the others. A line WITHOUT element is NOT
  controllable (unclassified cost cannot be charged to a manager).
- Model ``cmc.responsibility.center`` (company rule ``parent_of``, ``_check_company_auto``; ACL
  group_cmc_responsible read, group_cmc_user read, group_cmc_manager full): ``name`` (required),
  ``company_id`` (required, default env.company), ``responsibility_type`` Selection ``cost`` (default) /
  ``revenue`` / ``profit`` / ``investment``, ``owner_id`` M2o res.users (required), ``center_ids`` M2M
  account.analytic.account (check_company; N:M: a center may serve several responsibility centers),
  ``capital_ids`` O2m ``cmc.responsibility.capital`` (``responsibility_id``, ``company_id`` related stored,
  ``date`` normalised to the month (cmc.month.mixin), ``amount`` Monetary = invested capital of that month).
- ``account.analytic.account.cmc_responsibility_ids`` = inverse M2M of ``center_ids``.
- ``_cmc_responsibility_summary(date_from, date_to)`` -> ``{resp.id: {"controllable_cost",
  "non_controllable_cost", "revenue", "result", "invested_capital", "roi"}}``: perimeter = ``center_ids`` and
  their CMC descendants (``_cmc_center_descendants``); cost = -amount of cost-base lines with amount < 0,
  revenue = amount of lines with amount > 0 (natural sign, AD-112); result = revenue - costs;
  invested_capital = average of the capital rows whose month falls in the span (0.0 when none);
  roi = result / invested_capital for ``investment`` centers with capital, else 0.0. Checks read access on
  the responsibility centers first (AccessError), then aggregates in sudo inside the perimeter.

Hand-computed (span Jan-Mar 2031), investment center "Plant" on Dept (child Sub):
  Dept energy (controllable) 3000; Sub depreciation (non-controllable) 5000; Sub no element 1000;
  Dept revenue +20000; Dept April cost 999 (outside). Controllable 3000, non-controllable 6000,
  revenue 20000, result 11000; capital Jan 100000, Feb 100000, Mar 130000 -> avg 110000 -> ROI 0.10.
"""

from datetime import date

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, new_test_user, tagged

SPAN = (date(2031, 1, 1), date(2031, 3, 31))


@tagged("post_install", "-at_install")
class TestDecisionFlags(TransactionCase):
    """CLS-04: flags for decisions live on the cost element."""

    def test_flag_defaults(self):
        element = self.env["cmc.cost.element"].create({"name": "WP01 flag default", "code": "WP01F_D"})
        self.assertRecordValues(element, [{
            "cmc_controllable": True, "cmc_avoidable": False, "cmc_sunk": False, "cmc_commitment": False,
        }])

    def test_commitment_selection(self):
        # CLS-04: committed vs discretionary fixed cost
        keys = dict(self.env["cmc.cost.element"]._fields["cmc_commitment"].selection)
        self.assertEqual(set(keys), {"committed", "discretionary"})

    def test_sunk_cannot_be_avoidable(self):
        # CLS-04: a sunk cost is by definition not avoidable
        with self.assertRaises(ValidationError):
            self.env["cmc.cost.element"].create(
                {"name": "WP01 bad flags", "code": "WP01F_B", "cmc_sunk": True, "cmc_avoidable": True}
            )


@tagged("post_install", "-at_install")
class TestResponsibilityCenter(TransactionCase):
    """CEN-03: each manager is reported on the costs he controls; ROI for investment centers."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AA = cls.env["account.analytic.account"]
        cls.AL = cls.env["account.analytic.line"]
        cls.Resp = cls.env["cmc.responsibility.center"]
        cls.company = cls.env.company
        cls.plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        cls.energy = cls.env["cmc.cost.element"].create({"name": "WP01 R energy", "code": "WP01R_EN"})
        cls.depreciation = cls.env["cmc.cost.element"].create(
            {"name": "WP01 R depreciation", "code": "WP01R_DP", "cmc_controllable": False, "cmc_commitment": "committed"}
        )
        cls.dept = cls.AA.create({"name": "WP01 R Dept", "plan_id": cls.plan.id, "cmc_center_type": "final",
                                  "company_id": cls.company.id})
        cls.sub = cls.AA.create({"name": "WP01 R Sub", "plan_id": cls.plan.id, "cmc_center_type": "final",
                                 "cmc_parent_id": cls.dept.id, "company_id": cls.company.id})
        cls.other = cls.AA.create({"name": "WP01 R Other", "plan_id": cls.plan.id, "cmc_center_type": "final",
                                   "company_id": cls.company.id})
        line = cls.AL.create
        line({"name": "energy", "account_id": cls.dept.id, "amount": -3000.0, "date": date(2031, 2, 10),
              "cmc_cost_element_id": cls.energy.id})
        line({"name": "depr", "account_id": cls.sub.id, "amount": -5000.0, "date": date(2031, 3, 10),
              "cmc_cost_element_id": cls.depreciation.id})
        line({"name": "unclassified", "account_id": cls.sub.id, "amount": -1000.0, "date": date(2031, 1, 10)})
        line({"name": "revenue", "account_id": cls.dept.id, "amount": 20000.0, "date": date(2031, 3, 20)})
        line({"name": "april", "account_id": cls.dept.id, "amount": -999.0, "date": date(2031, 4, 10),
              "cmc_cost_element_id": cls.energy.id})
        line({"name": "other", "account_id": cls.other.id, "amount": -7000.0, "date": date(2031, 2, 10),
              "cmc_cost_element_id": cls.energy.id})
        cls.plant_manager = new_test_user(cls.env, login="wp01_plant_mgr", groups="base.group_user,cmc_base.group_cmc_responsible")
        cls.plant = cls.Resp.create({
            "name": "WP01 Plant", "responsibility_type": "investment", "owner_id": cls.plant_manager.id,
            "center_ids": [(6, 0, cls.dept.ids)],
            "capital_ids": [(0, 0, {"date": date(2031, 1, 1), "amount": 100000.0}),
                            (0, 0, {"date": date(2031, 2, 15), "amount": 100000.0}),
                            (0, 0, {"date": date(2031, 3, 1), "amount": 130000.0}),
                            (0, 0, {"date": date(2031, 4, 1), "amount": 999999.0})],
        })

    def test_responsibility_types(self):
        # CEN-03: cost / revenue / profit / investment
        keys = dict(self.Resp._fields["responsibility_type"].selection)
        self.assertEqual(set(keys), {"cost", "revenue", "profit", "investment"})

    def test_center_mapping_is_many_to_many(self):
        # CEN-03: N:M mapping between responsibility centers and cost centers
        other_resp = self.Resp.create({"name": "WP01 Energy desk", "owner_id": self.env.user.id,
                                       "center_ids": [(6, 0, (self.dept | self.other).ids)]})
        self.assertEqual(self.dept.cmc_responsibility_ids, self.plant | other_resp)
        self.assertEqual(self.other.cmc_responsibility_ids, other_resp)

    def test_capital_month_normalised(self):
        # CEN-03: invested capital is typed per month
        months = self.plant.capital_ids.filtered(lambda c: c.amount == 100000.0).mapped("date")
        self.assertEqual(set(months), {date(2031, 1, 1), date(2031, 2, 1)})

    def test_controllable_cost_filter(self):
        # CLS-04: engines filter on the controllable flag; no element = not controllable
        centers = self.dept | self.sub
        ctrl = centers._cmc_cost_groups(*SPAN, company=self.company, controllable=True)
        non_ctrl = centers._cmc_cost_groups(*SPAN, company=self.company, controllable=False)
        self.assertAlmostEqual(ctrl.get(self.dept.id, 0.0), 3000.0)
        self.assertAlmostEqual(ctrl.get(self.sub.id, 0.0), 0.0)
        self.assertAlmostEqual(non_ctrl.get(self.sub.id, 0.0), 6000.0)

    def test_summary_controllable_cost_per_manager(self):
        # CEN-03: report each manager's controllable costs over his subtree
        summary = self.plant._cmc_responsibility_summary(*SPAN)[self.plant.id]
        self.assertAlmostEqual(summary["controllable_cost"], 3000.0)
        self.assertAlmostEqual(summary["non_controllable_cost"], 6000.0)
        self.assertAlmostEqual(summary["revenue"], 20000.0)
        self.assertAlmostEqual(summary["result"], 11000.0)

    def test_summary_roi_investment_center(self):
        # CEN-03: ROI = result / invested capital (average of the months of the span)
        summary = self.plant._cmc_responsibility_summary(*SPAN)[self.plant.id]
        self.assertAlmostEqual(summary["invested_capital"], 110000.0)
        self.assertAlmostEqual(summary["roi"], 0.10)

    def test_summary_cost_center_has_no_roi(self):
        # CEN-03: ROI only for investment centers
        cost_center = self.Resp.create({"name": "WP01 Cost only", "owner_id": self.env.user.id,
                                        "center_ids": [(6, 0, self.other.ids)]})
        summary = cost_center._cmc_responsibility_summary(*SPAN)[cost_center.id]
        self.assertAlmostEqual(summary["controllable_cost"], 7000.0)
        self.assertAlmostEqual(summary["roi"], 0.0)

    def test_summary_run_by_the_owner(self):
        # BUD-05: the owner (responsible group only) gets his own figures
        summary = self.plant.with_user(self.plant_manager)._cmc_responsibility_summary(*SPAN)
        self.assertAlmostEqual(summary[self.plant.id]["controllable_cost"], 3000.0)

    def test_center_of_other_company_refused(self):
        # AD-115: check_company on the mapped centers
        company_b = self.env["res.company"].create({"name": "WP01 R Co B"})
        foreign = self.AA.create({"name": "WP01 R foreign", "plan_id": self.plan.id,
                                  "cmc_center_type": "final", "company_id": company_b.id})
        with self.assertRaises(UserError):
            self.Resp.create({"name": "WP01 bad", "owner_id": self.env.user.id,
                              "center_ids": [(6, 0, foreign.ids)]})
