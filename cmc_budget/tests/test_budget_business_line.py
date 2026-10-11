"""BUD-01 (extra T-A) - master budget by business line / area: lines carry ``business_line_id``, BvA groups by it.

Hand-computed (center M, rent fixed, Feb 2033 only, natural sign): plan line "Doors" 600, plan line "Windows"
400, plan line without business line 100; actual on center M 900 (analytic lines carry no business line, the
cost reader is center based -> the actual sits in the business_line_id False group).
  _cmc_bva_summary(15 Feb, groupby=("business_line_id",)):
    Doors   plan_month -600  actual_month    0  variance_month +600  (+100.0 %)
    Windows plan_month -400  actual_month    0  variance_month +400  (+100.0 %)
    False   plan_month -100  actual_month -900  variance_month -800  (-800.0 %)
  groupby center: one row, plan -1,100, actual -900 (the business line does not split the center total).
  view (FX-009 / DL-006): ONE row center M x rent x Feb, business_line_id False (several on the plan), plan 1,100,
  actual 900, variance +200 (natural actual - natural plan: -900 - -1,100).
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
        # T-A / FX-009 / DL-004: one BvA row per center x element x month; business_line_id is informational,
        # False when the plan lines carry several (Doors + Windows + none); DL-006 costs presented positive
        Bva = self.env["cmc.budget.bva"]
        rows = Bva.search([("version_id", "=", self.version.id), ("date", "=", d(2)), ("center_id", "=", self.center.id)])
        self.assertRecordValues(rows, [{
            "business_line_id": False, "plan_amount": 1100.0, "actual_amount": 900.0, "variance": 200.0,
            "element_id": self.rent.id,
        }])

    def test_bva_view_single_business_line_is_shown(self):
        # T-A / FX-009: when every plan line of the row carries the same business line, the row shows it
        version = self._version(name="BUD bl single 2033")
        self._line(version, self.center, 3, 500.0, business_line_id=self.doors.id)
        self._cost(self.center, 450.0, d(3, 10))
        self._approve(version)
        self.env.flush_all()
        rows = self.env["cmc.budget.bva"].search([("version_id", "=", version.id), ("date", "=", d(3)), ("center_id", "=", self.center.id)])
        self.assertRecordValues(rows, [{"business_line_id": self.doors.id, "plan_amount": 500.0, "actual_amount": 450.0, "variance": 50.0}])

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
