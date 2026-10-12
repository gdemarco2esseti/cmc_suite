"""WP-06 / FC-07 - cost of production per department: EU, cost per EU (WA / FIFO), assignment, chaining.

CONTRACT (part, see common.py)
- Period cost per element from the department's analytic lines in the month: materials = MP (minus the
  consumption of the upstream department's product), conversion = MOD + overhead + closing ``cost_received``.
- Outputs on the run and per element line (``cmc.process.run.line``), positive costs, control total exact.
- ``transferred_in`` = the upstream run's ``transferred_out_cost``; month-to-month BWIP from ``previous_run_id``.
- A computed run is a snapshot (AD-113): a later posting changes nothing until reset + compute.
"""

from odoo.tests.common import tagged

from .common import N_FROM, N_TO, TODAY, ProcessCase


@tagged("post_install", "-at_install")
class TestProcessCosting(ProcessCase):
    def test_weighted_average_totals(self):
        # FC-07 / BB 2.3, 3.1: WA = (BWIP + period) / EU; transferred + EWIP + abnormal = costs to account
        run = self._d1()
        self.assertRecordValues(run, [{
            "state": "done", "cost_to_account": 126100.0, "transferred_out_cost": 108900.0,
            "normal_spoilage_cost": 9900.0, "ending_wip_cost": 16100.0, "abnormal_loss": 1100.0,
            "control_difference": 0.0,
        }])
        self.assertAlmostEqual(run.unit_cost_transferred, 121.0)

    def test_weighted_average_lines(self):
        # FC-07 / BB 2.1-2.3: equivalent units and cost per EU per cost element (materials vs conversion)
        run = self._d1()
        self.assertFalse(self._line(run, "transferred_in"))
        self.assertRecordValues(self._line(run, "materials") | self._line(run, "conversion"), [
            {"element": "materials", "beginning_cost": 9200.0, "period_cost": 52000.0, "cost_to_account": 61200.0,
             "equivalent_units": 1200.0, "cost_per_eu": 51.0, "transferred_cost": 50490.0,
             "normal_spoilage_cost": 4590.0, "ending_cost": 10200.0, "abnormal_cost": 510.0},
            {"element": "conversion", "beginning_cost": 6100.0, "period_cost": 58800.0, "cost_to_account": 64900.0,
             "equivalent_units": 1100.0, "cost_per_eu": 59.0, "transferred_cost": 58410.0,
             "normal_spoilage_cost": 5310.0, "ending_cost": 5900.0, "abnormal_cost": 590.0},
        ])

    def test_fifo(self):
        # FC-07 / BB 3.5, Cinquini VI.3: FIFO EU = completed - BWIP x % + EWIP x % (+ spoiled); period cost only
        run = self._d1(method="fifo")
        self.assertRecordValues(run, [{
            "cost_to_account": 126100.0, "transferred_out_cost": 108580.0, "normal_spoilage_cost": 10080.0,
            "ending_wip_cost": 16400.0, "abnormal_loss": 1120.0, "control_difference": 0.0,
        }])
        self.assertAlmostEqual(run.unit_cost_transferred, 108580.0 / 900.0)
        self.assertRecordValues(self._line(run, "materials") | self._line(run, "conversion"), [
            {"element": "materials", "equivalent_units": 1000.0, "cost_per_eu": 52.0, "transferred_cost": 50280.0,
             "normal_spoilage_cost": 4680.0, "ending_cost": 10400.0, "abnormal_cost": 520.0},
            {"element": "conversion", "equivalent_units": 980.0, "cost_per_eu": 60.0, "transferred_cost": 58300.0,
             "normal_spoilage_cost": 5400.0, "ending_cost": 6000.0, "abnormal_cost": 600.0},
        ])

    def test_conversion_without_closing(self):
        # FC-07: without a closing the conversion cost is the department's own MOD + overhead (30,000 + 20,000)
        run = self._run()
        conv = self._line(run, "conversion")
        self.assertRecordValues(conv, [{"period_cost": 50000.0, "cost_to_account": 56100.0, "cost_per_eu": 51.0}])
        self.assertAlmostEqual(run.cost_to_account, 117300.0)

    def test_closing_residual_feeds_conversion(self):
        # FC-07: with the closing, materials + conversion = the department's closing residual (52,000 + 58,800)
        run = self._d1()
        residual = self.closing.line_ids.filtered(lambda cl: cl.center_id == self.d1).cost_residual
        self.assertAlmostEqual(self._line(run, "materials").period_cost + self._line(run, "conversion").period_cost, residual)

    def test_control_total_conserves_cents(self):
        # FC-07 / BB 3.1: 59,000 / 980 is not a round cost per EU, the control total still closes to the cent
        self._indirect(self.d1, 200.0)
        run = self._d1(method="fifo")
        conv = self._line(run, "conversion")
        self.assertAlmostEqual(conv.cost_per_eu, 59000.0 / 980.0, places=8)
        currency = run.currency_id
        self.assertEqual(currency.compare_amounts(conv.transferred_cost + conv.ending_cost + conv.abnormal_cost, 65100.0), 0)
        self.assertEqual(currency.compare_amounts(
            run.transferred_out_cost + run.ending_wip_cost + run.abnormal_loss, run.cost_to_account), 0)
        self.assertEqual(currency.compare_amounts(run.cost_to_account, 126300.0), 0)
        self.assertEqual(run.control_difference, 0.0)

    def test_chained_department_weighted_average(self):
        # FC-07 / BB 3.3-3.4: the cost transferred in from Assembly is an element of Finishing at 100 %
        d2 = self._d2(self._d1())
        self.assertRecordValues(d2, [{
            "cost_to_account": 138400.0, "transferred_out_cost": 124800.0, "ending_wip_cost": 13600.0,
            "abnormal_loss": 0.0, "control_difference": 0.0,
        }])
        self.assertAlmostEqual(d2.unit_cost_transferred, 156.0)
        self.assertRecordValues(self._line(d2, "transferred_in") | self._line(d2, "materials") | self._line(d2, "conversion"), [
            {"element": "transferred_in", "period_cost": 108900.0, "equivalent_units": 900.0, "cost_per_eu": 121.0,
             "transferred_cost": 96800.0, "ending_cost": 12100.0},
            {"element": "materials", "period_cost": 4000.0, "equivalent_units": 800.0, "cost_per_eu": 5.0,
             "transferred_cost": 4000.0, "ending_cost": 0.0},
            {"element": "conversion", "period_cost": 25500.0, "equivalent_units": 850.0, "cost_per_eu": 30.0,
             "transferred_cost": 24000.0, "ending_cost": 1500.0},
        ])

    def test_chained_materials_exclude_transferred_product(self):
        # FC-07: the 96,800 MP booked on Finishing for the consumption of S IS the transferred-in cost: not twice
        d2 = self._d2(self._d1())
        self.assertAlmostEqual(self._line(d2, "materials").period_cost, 4000.0)

    def test_chained_to_fifo_upstream(self):
        # FC-07: the transferred-in cost is whatever the upstream method assigned to its output
        d2 = self._d2(self._d1(method="fifo"))
        ti = self._line(d2, "transferred_in")
        self.assertAlmostEqual(ti.period_cost, 108580.0)
        self.assertAlmostEqual(ti.cost_per_eu, 108580.0 / 900.0)

    def _next_month(self, prev, method):
        self._indirect(self.d1, 1000.0, day=N_FROM)
        run = self._run(method=method, typed={"ending_units": 0.0}, previous_run_id=prev.id,
                        date_from=N_FROM, date_to=N_TO, compute=False)
        run.write({"units_started": 0.0, "units_completed": 200.0, "units_spoiled": 0.0})
        run.action_compute()
        return run

    def test_next_month_weighted_average(self):
        # FC-07: the BWIP carried from P is finished in P+1: (5,900 + 1,000) / 200 conversion EU
        run = self._next_month(self._d1(), "weighted_average")
        self.assertAlmostEqual(self._line(run, "conversion").cost_per_eu, 34.5)
        self.assertAlmostEqual(run.transferred_out_cost, 17100.0)
        self.assertAlmostEqual(run.unit_cost_transferred, 85.5)

    def test_next_month_fifo(self):
        # FC-07: FIFO keeps the 16,400 BWIP cost apart, 100 conversion EU complete it at 1,000 / 100
        run = self._next_month(self._d1(method="fifo"), "fifo")
        self.assertRecordValues(self._line(run, "conversion"), [{"equivalent_units": 100.0, "cost_per_eu": 10.0}])
        self.assertRecordValues(self._line(run, "materials"), [{"equivalent_units": 0.0, "period_cost": 0.0}])
        self.assertAlmostEqual(run.transferred_out_cost, 17400.0)

    def test_snapshot_until_reset(self):
        # AD-113: a posting after Compute does not move a computed run; reset + compute picks it up
        run = self._d1()
        self.AL.create({
            "name": "WP06 late MP", "account_id": self.d1.id, "amount": -1100.0, "company_id": self.company.id,
            "cmc_cost_element_id": self.mp.id, "cmc_behavior": "variable", "cmc_directness": "direct", "date": TODAY,
        })
        run.invalidate_recordset()
        self.assertAlmostEqual(run.cost_to_account, 126100.0)
        run.action_reset_to_draft()
        run.action_compute()
        self.assertAlmostEqual(run.cost_to_account, 127200.0)
        self.assertAlmostEqual(self._line(run, "materials").period_cost, 53100.0)
