"""WP-16 TGT-01 - kaizen costing: a periodic reduction target on the actual unit cost, tracked with the rates
published later; Kaplan-Cooper Ch5 flexible-budget form. CONTRACT: see common.py."""

from datetime import date

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import APR, FEB, JAN, TlcCase


@tagged("post_install", "-at_install")
class TestKaizen(TlcCase):
    def _kaizen(self, **kw):
        vals = {"name": "WP16 kaizen", "basis": "unit", "product_id": self.k.id, "unit_driver_id": self.units.id,
                "base_date": JAN, "date_from": FEB, "date_to": APR, "reduction_pct": 6.0}
        vals.update(kw)
        return self.env["cmc.kaizen.target"].create(vals)

    def test_tgt01_kaizen_target_on_actual_unit_cost(self):
        # TGT-01 kaizen: base actual 50 (Jan) - 6% = 47 per unit
        kaizen = self._kaizen()
        kaizen.action_compute()
        self.assertRecordValues(kaizen, [{"state": "done", "base_unit_cost": 50.0, "target_unit_cost": 47.0}])

    def test_tgt01_kaizen_achieved_vs_target_from_later_rates(self):
        # TGT-01 kaizen: Feb 48.5 (3% achieved, -1.5 unfavourable), Mar 46.5 (7%, +0.5 met); Apr has no rate
        kaizen = self._kaizen()
        kaizen.action_compute()
        lines = kaizen.line_ids.sorted("date")
        self.assertRecordValues(lines, [
            {"date": FEB, "actual_unit_cost": 48.5, "achieved_pct": 3.0, "variance": -1.5, "met": False},
            {"date": date(2026, 3, 1), "actual_unit_cost": 46.5, "achieved_pct": 7.0, "variance": 0.5, "met": True},
        ])
        self.assertRecordValues(kaizen, [{"achieved_pct": 7.0, "variance": 0.5, "target_met": True}])

    def test_tgt01_kaizen_without_baseline_rate(self):
        # TGT-01 kaizen: no actual unit cost in the base month = no baseline, fail loud
        kaizen = self._kaizen(base_date=APR)
        with self.assertRaises(UserError):
            kaizen.action_compute()

    def test_tgt01_kaizen_flexible_budget(self):
        # Kaplan-Cooper Ch5: 0.94 x (120,000 fixed + 110,890 flexible variable) = 217,036.60 vs actual 220,000
        kaizen = self.env["cmc.kaizen.target"].create({
            "name": "WP16 kaizen team", "basis": "flexible", "reduction_pct": 6.0,
            "fixed_budget": 120000.0, "variable_budget": 110890.0, "actual_cost": 220000.0,
        })
        kaizen.action_compute()
        self.assertAlmostEqual(kaizen.target_total, 217036.60, places=2)
        self.assertAlmostEqual(kaizen.variance, -2963.40, places=2)
        self.assertFalse(kaizen.target_met)

    def test_tgt01_kaizen_reset_clears_tracking(self):
        # AD-113: reset deletes the monthly tracking and the outputs
        kaizen = self._kaizen()
        kaizen.action_compute()
        kaizen.action_reset_to_draft()
        self.assertFalse(kaizen.line_ids)
        self.assertRecordValues(kaizen, [{"state": "draft", "base_unit_cost": 0.0, "target_unit_cost": 0.0}])
