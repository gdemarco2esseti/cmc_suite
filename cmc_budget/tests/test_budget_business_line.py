"""BUD-01 (extra T-A) - master budget by business line / area: lines carry ``business_line_id``, BvA groups by it.

Hand-computed (center M, rent fixed, Feb 2033 only, natural sign): plan line "Doors" 600, plan line "Windows"
400, plan line without business line 100; actual on center M 900 (analytic lines carry no business line, the
cost reader is center based -> the actual sits in the business_line_id False group).
  _cmc_bva_summary(15 Feb, groupby=("business_line_id",)):
    Doors   plan_month -600  actual_month    0  variance_month +600  (+100.0 %)
    Windows plan_month -400  actual_month    0  variance_month +400  (+100.0 %)
    False   plan_month -100  actual_month -900  variance_month -800  (-800.0 %)
  groupby center: one row, plan -1,100, actual -900 (the business line does not split the center total).
"""

from odoo.tests.common import tagged

from .common import CmcBudgetCase, d


@tagged("post_install", "-at_install")
class TestBudgetBusinessLine(CmcBudgetCase):
    """BUD-01: the budget is also by business line / area, and the BvA can be read along that axis."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.center = cls._center("BUD bl M")
        # business lines are plain analytic accounts (any plan, not cmc centers)
        cls.doors = cls.AA.create({"name": "BUD BL Doors", "plan_id": cls.plan.id, "company_id": cls.company.id})
        cls.windows = cls.AA.create({"name": "BUD BL Windows", "plan_id": cls.plan.id, "company_id": cls.company.id})
        cls.version = cls._version(name="BUD bl 2033")
        cls.line_doors = cls._line(cls.version, cls.center, 2, 600.0, business_line_id=cls.doors.id)
        cls.line_windows = cls._line(cls.version, cls.center, 2, 400.0, business_line_id=cls.windows.id)
        cls.line_none = cls._line(cls.version, cls.center, 2, 100.0)
        cls._cost(cls.center, 900.0, d(2, 10))
        cls.version.with_user(cls.manager).action_approve()

    def test_line_accepts_business_line(self):
        # T-A / BUD-01: a budget line is keyed also by business line (an analytic account of any plan)
        self.assertRecordValues(self.line_doors | self.line_none, [
            {"business_line_id": self.doors.id, "center_id": self.center.id, "amount": -600.0},
            {"business_line_id": False, "center_id": self.center.id, "amount": -100.0},
        ])

    def test_bva_view_exposes_business_line(self):
        # T-A / BUD-01: the BvA SQL view carries business_line_id as a key (plan rows keep theirs)
        Bva = self.env["cmc.budget.bva"]
        rows = Bva.search([("version_id", "=", self.version.id), ("date", "=", d(2)), ("business_line_id", "=", self.doors.id)])
        self.assertRecordValues(rows, [{"plan_amount": -600.0, "actual_amount": 0.0, "center_id": self.center.id}])

    def test_summary_by_business_line(self):
        # T-A / BUD-01: plan / actual / variance per business line, actuals without line under False
        rows = self.version._cmc_bva_summary(d(2, 15), groupby=("business_line_id",))
        got = {
            row["business_line_id"]: (round(row["plan_month"], 2), round(row["actual_month"], 2),
                                      round(row["variance_month"], 2), round(row["variance_month_pct"], 2))
            for row in rows
        }
        # CONTRACT §3: groupby keys are records, False when empty
        self.assertEqual(got, {
            self.doors: (-600.0, 0.0, 600.0, 100.0),
            self.windows: (-400.0, 0.0, 400.0, 100.0),
            False: (-100.0, -900.0, -800.0, -800.0),
        })

    def test_business_line_does_not_split_center_total(self):
        # T-A / BUD-01: grouping by center still sums every business line of the center
        (row,) = self.version._cmc_bva_summary(d(2, 15), groupby=("center_id",))
        self.assertEqual((row["center_id"], round(row["plan_month"], 2), round(row["actual_month"], 2)),
                         (self.center, -1100.0, -900.0))
