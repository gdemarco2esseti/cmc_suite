"""STD-04 - MO variance engine: material price/quantity, mix/yield, labour rate/efficiency (natural sign, + = favourable).

CONTRACT: common.py §5 (hand-computed MO of F in the fixture docstring). Actuals come from the real cmc_mrp path
(MP lines of the consumption moves, MOD workcenter lines), never rebuilt by hand.
"""

from datetime import timedelta

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from .common import Y_TO, StdCase


@tagged("post_install", "-at_install")
class TestStdVariance(StdCase):
    def test_precondition_actuals_from_bridge(self):
        # precondition of the whole file: the bridge booked MP at the actual valuation and MOD at the actual rate
        mp = self.mo_f.move_raw_ids.cmc_mrp_analytic_line_ids
        self.assertEqual(sorted(mp.mapped("amount")), [-99.0, -48.6])
        mod = self.mo_f.workorder_ids.wc_analytic_account_line_ids
        self.assertAlmostEqual(sum(mod.mapped("amount")), -132.0)
        self.assertAlmostEqual(sum(mod.mapped("unit_amount")), 6.0)

    def test_material_price_and_quantity_variance(self):
        # STD-04: price var = (Ps - Pa) x Qa, quantity var = (Qs - Qa) x Ps, positive = favourable
        run = self._run(self._std_version())
        self.assertRecordValues(self._vline(run, self.mo_f, "material", component=self.a), [{
            "product_id": self.f.id, "output_qty": 10.0, "std_qty": 20.0, "actual_qty": 22.0, "std_price": 4.0, "actual_price": 4.5,
            "std_amount": -80.0, "actual_amount": -99.0, "price_variance": -11.0, "quantity_variance": -8.0, "total_variance": -19.0,
        }])
        self.assertRecordValues(self._vline(run, self.mo_f, "material", component=self.b), [{
            "std_qty": 30.0, "actual_qty": 27.0, "std_price": 2.0, "actual_price": 1.8,
            "std_amount": -60.0, "actual_amount": -48.6, "price_variance": 5.4, "quantity_variance": 6.0, "total_variance": 11.4,
        }])

    def test_material_mix_and_yield_two_ingredients(self):
        # STD-04: mix_i = (m_i x sum Qa - Qa_i) x Ps_i, yield_i = (Qs_i - m_i x sum Qa) x Ps_i; mix + yield = quantity
        run = self._run(self._std_version())
        line_a = self._vline(run, self.mo_f, "material", component=self.a)
        line_b = self._vline(run, self.mo_f, "material", component=self.b)
        self.assertAlmostEqual(line_a.mix_variance, -9.6)
        self.assertAlmostEqual(line_a.yield_variance, 1.6)
        self.assertAlmostEqual(line_b.mix_variance, 4.8)
        self.assertAlmostEqual(line_b.yield_variance, 1.2)
        for line in line_a | line_b:
            self.assertAlmostEqual(line.mix_variance + line.yield_variance, line.quantity_variance)

    def test_single_component_mix_zero_yield_equals_quantity(self):
        # STD-04: with one ingredient there is no mix, the whole usage variance is yield
        mo_k = self._done_mo(self.k, self.bom_k, 2.0, {self.c: 5.0}, duration=12.0, center=self.y)
        run = self._run(self._std_version())
        line = self._vline(run, mo_k, "material", component=self.c)
        self.assertRecordValues(line, [{"std_qty": 4.0, "actual_qty": 5.0, "price_variance": 0.0, "quantity_variance": -1.5,
                                        "mix_variance": 0.0, "yield_variance": -1.5}])

    def test_labour_rate_and_efficiency_variance(self):
        # STD-04: rate var = (SR - AR) x AH, efficiency = (SH - AH) x SR on the center of the work center
        run = self._run(self._std_version())
        self.assertRecordValues(self._vline(run, self.mo_f, "labour", center=self.z), [{
            "std_qty": 5.0, "actual_qty": 6.0, "std_price": 20.0, "actual_price": 22.0, "std_amount": -100.0, "actual_amount": -132.0,
            "price_variance": -12.0, "quantity_variance": -20.0, "mix_variance": 0.0, "yield_variance": 0.0, "total_variance": -32.0,
        }])

    def test_run_totals_conserve(self):
        # AD-113 / INT-05: sum of actual - sum of standard = sum of the variances
        run = self._run(self._std_version())
        self.assertAlmostEqual(run.std_amount_total, -240.0)
        self.assertAlmostEqual(run.actual_amount_total, -279.6)
        self.assertAlmostEqual(run.variance_total, -39.6)
        self.assertAlmostEqual(sum(run.line_ids.mapped("total_variance")), run.variance_total)

    def test_mo_without_card_is_counted_as_skipped(self):
        # AD-113 (fail visibly): an MO of a product with no card is out of the variances but not silent
        product = self._product("STD no card")
        bom = self._bom(product, [(self.c, 1)])
        mo = self._done_mo(product, bom, 1.0)
        run = self._run(self._std_version())
        self.assertFalse(run.line_ids.filtered(lambda line: line.production_id == mo))
        self.assertEqual(run.skipped_count, 1)

    def test_mo_outside_period_or_without_center_ignored(self):
        # STD-04: only done MOs of the period with a CMC center are in scope
        mo = self._done_mo(self.f, self.bom_f, 1.0)
        mo.date_finished = mo.date_finished - timedelta(days=40)
        no_center = self.env["mrp.production"].create({"product_id": self.f.id, "product_qty": 1.0, "bom_id": self.bom_f.id})
        no_center.action_confirm()
        no_center.qty_producing = 1.0
        for move in no_center.move_raw_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        no_center.with_context(skip_consumption=True).button_mark_done()
        run = self._run(self._std_version())
        self.assertEqual(run.line_ids.production_id, self.mo_f)

    def test_draft_version_refused(self):
        # AD-113: variances are measured only against an approved (locked) standard
        with self.assertRaises(UserError):
            self._run(self._std_version(approve=False))

    def test_budget_version_refused(self):
        # AD-104: the standard of a variance run is a standard version
        with self.assertRaises(ValidationError):
            self._run(self.budget, compute=False)

    def test_period_outside_version_span_refused(self):
        # AD-105: the run period must fall inside the standard version span
        with self.assertRaises(ValidationError):
            self._run(self._std_version(), compute=False, date_to=Y_TO + timedelta(days=31))

    def test_version_cannot_be_reset_while_run_computed(self):
        # AD-002: the standard read by a computed run cannot be reopened under it
        version = self._std_version()
        run = self._run(version)
        with self.assertRaises(UserError):
            version.with_user(self.manager).action_reset_to_draft()
        run.action_reset_to_draft()
        self.assertFalse(run.line_ids)
        version.with_user(self.manager).action_reset_to_draft()
        self.assertEqual(version.state, "draft")

    def test_snapshot_ignores_later_postings(self):
        # AD-113: a computed run is a snapshot; a new MO shows only after reset + compute
        run = self._run(self._std_version())
        before = run.actual_amount_total
        self._done_mo(self.f, self.bom_f, 1.0)
        self.assertAlmostEqual(run.actual_amount_total, before)
        run.action_reset_to_draft()
        run.action_compute()
        self.assertEqual(len(run.line_ids.production_id), 2)

    def test_rn001_output_in_product_uom(self):
        # RN-001 / STD-04: an MO of 1 Dozen produces 12 units; the per-unit card is applied to 12, not to 1
        version = self._std_version()
        dozen = self.env.ref("uom.product_uom_dozen")
        mo = self._done_mo(self.f, self.bom_f, 1.0, duration=360.0, uom=dozen)
        self.assertEqual(sorted(mo.move_raw_ids.mapped("quantity")), [24.0, 36.0], "precondition: consumption exactly per BoM")
        run = self._run(version)
        material = self._vline(run, mo, "material")
        self.assertEqual(len(material), 2)
        for line in material:
            self.assertAlmostEqual(line.output_qty, 12.0)
            self.assertAlmostEqual(line.std_qty, line.actual_qty)
            self.assertAlmostEqual(line.quantity_variance, 0.0)
        self.assertAlmostEqual(self._vline(run, mo, "material", component=self.a).std_qty, 24.0)
        self.assertAlmostEqual(self._vline(run, mo, "material", component=self.b).std_qty, 36.0)
        labour = self._vline(run, mo, "labour", center=self.z)
        self.assertRecordValues(labour, [{"output_qty": 12.0, "std_qty": 6.0, "actual_qty": 6.0, "std_amount": -120.0, "quantity_variance": 0.0}])
        absorption = run.absorption_ids.filtered(lambda line: line.center_id == self.z)
        self.assertAlmostEqual(absorption.standard_qty, 5.0 + 6.0, msg="SH = MO of F 5 h + dozen MO 6 h")

    def test_rn001_output_net_of_unbuild(self):
        # RN-001 / AD-111: an unbuild after a done MO reduces its output, as the cmc_mrp produced-qty feeder does
        version = self._std_version()
        unbuild = self.env["mrp.unbuild"].create(
            {"mo_id": self.mo_f.id, "product_id": self.f.id, "bom_id": self.bom_f.id, "product_qty": 2.0}
        )
        unbuild.action_validate()
        run = self._run(version)
        line_a = self._vline(run, self.mo_f, "material", component=self.a)
        self.assertRecordValues(line_a, [{"output_qty": 8.0, "std_qty": 16.0}])
        labour = self._vline(run, self.mo_f, "labour", center=self.z)
        self.assertRecordValues(labour, [{"output_qty": 8.0, "std_qty": 4.0}])
