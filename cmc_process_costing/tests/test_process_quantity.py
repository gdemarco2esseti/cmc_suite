"""WP-06 / FC-07 - quantity schedule from MRP, spoilage from scrap, beginning WIP from the previous month.

CONTRACT (part, see common.py)
- ``action_cmc_prefill()`` refreshes the ``mrp_produced_qty`` and ``mrp_scrap_qty`` feeders and reads the facts of
  the department x product cost object x month: ``units_spoiled`` = scrap, ``units_completed`` = produced - scrap,
  ``units_started`` = previous department's completed (chained) or completed + spoiled + ending - beginning.
- ``cmc_mrp.cmc_measure_scrap_qty`` feeder (scrap moves of finished products of MOs with a CMC center); the produced
  feeder excludes scrap moves.
- ``previous_run_id`` gives the beginning WIP (units, % and cost per element = its ending); it must be computed.
- Compute refuses an unbalanced quantity schedule (UserError); normal / abnormal split from the % threshold.
"""

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import D1_TYPED, N_FROM, N_TO, P_FROM, P_TO, ProcessCase


@tagged("post_install", "-at_install")
class TestProcessQuantity(ProcessCase):
    def test_prefill_department_from_mrp(self):
        # FC-07 / BB 2.2: good units = produced - scrapped; starts balance the schedule of a first department
        run = self._d1(compute=False)
        self.assertRecordValues(run, [{
            "units_completed": 900.0, "units_spoiled": 100.0, "units_started": 1000.0,
            "beginning_units": 200.0, "ending_units": 200.0,
        }])

    def test_scrap_feeder_and_produced_net_of_scrap(self):
        # AD-107: scrap is a bridge measure; a scrapped finished unit is not a produced unit twice
        scrap_measure = self.env.ref("cmc_mrp.cmc_measure_scrap_qty")
        produced_measure = self.env.ref("cmc_mrp.cmc_measure_produced_qty")
        Fact = self.env["cmc.measure.quantity"]
        Fact._cmc_refresh_feeders(P_FROM, P_TO, company=self.company)
        facts = Fact.search([("center_id", "=", self.d1.id), ("version_id", "=", False), ("date", "=", P_FROM)])
        self.assertAlmostEqual(sum(facts.filtered(lambda f: f.driver_id == scrap_measure).mapped("quantity")), 100.0)
        self.assertAlmostEqual(sum(facts.filtered(lambda f: f.driver_id == produced_measure).mapped("quantity")), 1000.0)

    def test_prefill_chained_department_starts_from_upstream(self):
        # FC-07 / BB 3.3: units transferred in = units transferred out of the previous department
        d1 = self._d1()
        d2 = self._d2(d1, compute=False)
        self.assertRecordValues(d2, [{
            "units_started": 900.0, "units_completed": 800.0, "units_spoiled": 0.0, "ending_units": 100.0,
        }])

    def test_prefill_beginning_wip_from_previous_month(self):
        # FC-07: the ending WIP of the month is the beginning WIP of the next one (units, %, cost per element)
        prev = self._d1()
        nxt = self._run(typed={"ending_units": 0.0}, previous_run_id=prev.id, date_from=N_FROM, date_to=N_TO,
                        compute=False)
        self.assertRecordValues(nxt, [{
            "beginning_units": 200.0, "beginning_pct_materials": 100.0, "beginning_pct_conversion": 50.0,
            "beginning_cost_materials": 10200.0, "beginning_cost_conversion": 5900.0,
        }])

    def test_prefill_beginning_wip_from_previous_fifo_month(self):
        prev = self._d1(method="fifo")
        nxt = self._run(method="fifo", typed={"ending_units": 0.0}, previous_run_id=prev.id,
                        date_from=N_FROM, date_to=N_TO, compute=False)
        self.assertRecordValues(nxt, [{"beginning_cost_materials": 10400.0, "beginning_cost_conversion": 6000.0}])

    def test_prefill_refuses_draft_previous_month(self):
        # AD-113: a draft run is no certified source of beginning WIP
        prev = self._d1(compute=False)
        nxt = self._run(typed={}, previous_run_id=prev.id, date_from=N_FROM, date_to=N_TO, prefill=False, compute=False)
        with self.assertRaises(UserError):
            nxt.action_cmc_prefill()

    def test_compute_refuses_unbalanced_schedule(self):
        # FC-07 / BB 2.2: 200 + 1,100 started != 900 + 100 + 200: no invented balancing figure
        run = self._d1(compute=False)
        run.units_started = 1100.0
        with self.assertRaises(UserError):
            run.action_compute()

    def test_spoilage_normal_and_abnormal(self):
        # FC-07: normal spoilage = 10 % of 900 good units = 90, the other 10 are abnormal
        run = self._d1()
        self.assertRecordValues(run, [{"units_normal_spoiled": 90.0, "units_abnormal_spoiled": 10.0}])

    def test_spoilage_all_normal_under_threshold(self):
        run = self._d1(typed=dict(D1_TYPED, normal_spoilage_pct=20.0))
        self.assertRecordValues(run, [{"units_normal_spoiled": 100.0, "units_abnormal_spoiled": 0.0, "abnormal_loss": 0.0}])

    def test_typed_quantities_without_mrp(self):
        # FC-07: the schedule may be typed for a department without MOs (no prefill)
        run = self._run(center=self.d1, prefill=False, typed=dict(
            D1_TYPED, units_started=1000.0, units_completed=900.0, units_spoiled=100.0),
            closing_run_id=self.closing.id)
        self.assertAlmostEqual(run.transferred_out_cost, 108900.0)
