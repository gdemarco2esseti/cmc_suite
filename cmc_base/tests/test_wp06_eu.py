"""WP-06 / FC-07 - equivalent-unit maths of process costing (AD-119: pure, stdlib only, in ``cmc_utils``).

CONTRACT (pure functions in ``cmc_base/models/cmc_utils.py``; percentages are 0..100, never fractions)

- ``equivalent_units(method, completed, ending_units, ending_pct, beginning_units=0.0, beginning_pct=0.0,
  spoiled_units=0.0, spoiled_pct=100.0) -> float`` for ONE cost element:
    * ``"weighted_average"``: completed + ending_units x ending_pct/100 + spoiled_units x spoiled_pct/100
      (the beginning WIP is merged: beginning_pct is ignored);
    * ``"fifo"``: the weighted-average figure - beginning_units x beginning_pct/100 (Cinquini VI.3:
      V = P + alpha x Rsf - beta x Rsi);
    * any other method -> ``ValueError``.
  Spoiled units are detected at the inspection point (default end of process = 100 % for every element).
- ``spoilage_split(spoiled_units, good_units, normal_pct) -> (normal, abnormal)``: normal spoilage =
  min(spoiled, good_units x normal_pct/100) (tolerance as % of the good output), abnormal = the rest.
- ``process_cost_report(method, units, elements, rounding=None) -> dict`` - the cost of production report:
    ``units`` = {"beginning", "started", "completed", "ending", "normal_spoiled", "abnormal_spoiled"};
    quantity schedule: beginning + started must equal completed + ending + normal + abnormal, else ``ValueError``.
    ``elements`` = {key: {"beginning_cost", "period_cost", "beginning_pct", "ending_pct"}} (e.g.
    ``transferred_in`` at 100/100, ``materials``, ``conversion``).
    Per element (``result["elements"][key]``): ``eu`` (as ``equivalent_units``, spoiled at 100 %),
    ``cost_per_eu`` = (beginning_cost + period_cost) / eu for weighted average, period_cost / eu for FIFO,
    FULL precision (never rounded, Bookboon 2.3); ``cost_to_account`` = beginning_cost + period_cost;
    ``ending`` = ending_units x ending_pct/100 x cost_per_eu; ``abnormal`` = abnormal_spoiled x cost_per_eu;
    ``normal_spoilage`` = normal_spoiled x cost_per_eu (inspection at the end: absorbed by the good units
    transferred out); ``transferred`` = cost_to_account - ending - abnormal (WA: = (completed + normal) x
    cost_per_eu; FIFO: = beginning_cost + (completed - beginning x beginning_pct/100 + normal) x cost_per_eu).
    Totals at the top level: ``cost_to_account``, ``transferred``, ``ending``, ``abnormal``, ``normal_spoilage``.
    With ``rounding`` (currency step) the money parts of each element are rounded with ``largest_remainder``
    so that transferred + ending + abnormal = cost_to_account EXACTLY (control total, Bookboon 3.1).
    An element with cost but 0 equivalent units -> ``ValueError`` (cost that no unit can carry).

Textbook case (department 1, one month; hand-computed):
  BWIP 200 u (materials 100 %, conversion 60 %), cost materials 9,200, conversion 6,100.
  Started 1,000; completed 900; spoiled 100 (normal 10 % of good = 90, abnormal 10); EWIP 200 u (100 % / 50 %).
  Period cost: materials 52,000, conversion 58,800. Costs to account for 126,100.
  WA : EU 1,200 / 1,100 -> 51 / 59 per EU (110). Transferred 990 x 110 = 108,900 (normal spoilage 9,900),
       EWIP 200 x 51 + 100 x 59 = 16,100, abnormal 10 x 110 = 1,100.  108,900 + 16,100 + 1,100 = 126,100.
  FIFO: EU 1,000 / 980 -> 52 / 60 per EU (112). EWIP 10,400 + 6,000 = 16,400, abnormal 1,120,
       normal spoilage 10,080, transferred 15,300 + 80 x 60 + 700 x 112 + 10,080 = 108,580.
  Department 2 (chained, WA): transferred in 900 u / 108,900 at 100 %; materials added at the end (EWIP 0 %),
  conversion EWIP 50 %; completed 800, EWIP 100. Materials 4,000 / 800 = 5, conversion 25,500 / 850 = 30,
  transferred-in 108,900 / 900 = 121. Transferred out 800 x 156 = 124,800, EWIP 12,100 + 1,500 = 13,600.
"""

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.cmc_base.models import cmc_utils

DEPT1_UNITS = {
    "beginning": 200.0, "started": 1000.0, "completed": 900.0, "ending": 200.0,
    "normal_spoiled": 90.0, "abnormal_spoiled": 10.0,
}


def dept1_elements(conversion_period=58800.0):
    return {
        "materials": {"beginning_cost": 9200.0, "period_cost": 52000.0, "beginning_pct": 100.0, "ending_pct": 100.0},
        "conversion": {"beginning_cost": 6100.0, "period_cost": conversion_period, "beginning_pct": 60.0, "ending_pct": 50.0},
    }


@tagged("post_install", "-at_install")
class TestWp06EquivalentUnits(TransactionCase):
    def test_eu_weighted_average_bookboon_ending_wip(self):
        # FC-07 / BB 2.1: 100 units at 80 % materials / 60 % conversion = 80 / 60 equivalent units
        eu = cmc_utils.equivalent_units
        self.assertEqual(eu("weighted_average", 0.0, 100.0, 80.0), 80.0)
        self.assertEqual(eu("weighted_average", 0.0, 100.0, 60.0), 60.0)

    def test_eu_weighted_average_counts_spoiled_at_inspection(self):
        # FC-07: spoiled units inspected at the end carry 100 % of every element; BWIP % is irrelevant for WA
        eu = cmc_utils.equivalent_units(
            "weighted_average", 900.0, 200.0, 50.0, beginning_units=200.0, beginning_pct=60.0, spoiled_units=100.0
        )
        self.assertEqual(eu, 1100.0)

    def test_eu_fifo_subtracts_beginning_work(self):
        # FC-07 / Cinquini VI.3: V = P + alpha Rsf - beta Rsi -> 900 + 100 + 100 - 120 = 980
        eu = cmc_utils.equivalent_units(
            "fifo", 900.0, 200.0, 50.0, beginning_units=200.0, beginning_pct=60.0, spoiled_units=100.0
        )
        self.assertEqual(eu, 980.0)

    def test_eu_spoiled_at_intermediate_inspection_point(self):
        # FC-07: an inspection at 40 % of conversion gives the spoiled units 40 % of conversion only
        eu = cmc_utils.equivalent_units("weighted_average", 900.0, 0.0, 0.0, spoiled_units=100.0, spoiled_pct=40.0)
        self.assertEqual(eu, 940.0)

    def test_eu_unknown_method_raises(self):
        with self.assertRaises(ValueError):
            cmc_utils.equivalent_units("lifo", 10.0, 0.0, 0.0)

    def test_spoilage_split_normal_threshold(self):
        # FC-07: normal spoilage up to 10 % of the good units, the excess is abnormal (a loss of the period)
        split = cmc_utils.spoilage_split
        self.assertEqual(split(100.0, 900.0, 10.0), (90.0, 10.0))
        self.assertEqual(split(50.0, 900.0, 10.0), (50.0, 0.0))
        self.assertEqual(split(100.0, 900.0, 0.0), (0.0, 100.0))
        self.assertEqual(split(0.0, 900.0, 10.0), (0.0, 0.0))

    def test_report_weighted_average(self):
        # FC-07 / BB 2.3 + 3.1: WA cost per EU = (BWIP + period) / EU, allocation with control total
        rep = cmc_utils.process_cost_report("weighted_average", DEPT1_UNITS, dept1_elements(), rounding=0.01)
        mat, conv = rep["elements"]["materials"], rep["elements"]["conversion"]
        self.assertEqual((mat["eu"], conv["eu"]), (1200.0, 1100.0))
        self.assertAlmostEqual(mat["cost_per_eu"], 51.0)
        self.assertAlmostEqual(conv["cost_per_eu"], 59.0)
        self.assertAlmostEqual(mat["cost_to_account"], 61200.0)
        self.assertAlmostEqual(mat["transferred"], 50490.0)
        self.assertAlmostEqual(mat["ending"], 10200.0)
        self.assertAlmostEqual(mat["abnormal"], 510.0)
        self.assertAlmostEqual(conv["transferred"], 58410.0)
        self.assertAlmostEqual(conv["ending"], 5900.0)
        self.assertAlmostEqual(conv["abnormal"], 590.0)
        self.assertAlmostEqual(rep["cost_to_account"], 126100.0)
        self.assertAlmostEqual(rep["transferred"], 108900.0)
        self.assertAlmostEqual(rep["normal_spoilage"], 9900.0)
        self.assertAlmostEqual(rep["ending"], 16100.0)
        self.assertAlmostEqual(rep["abnormal"], 1100.0)
        self.assertAlmostEqual(rep["transferred"] + rep["ending"] + rep["abnormal"], rep["cost_to_account"])

    def test_report_fifo(self):
        # FC-07 / BB 3.5: FIFO cost per EU on the period cost only, BWIP cost kept apart and transferred first
        rep = cmc_utils.process_cost_report("fifo", DEPT1_UNITS, dept1_elements(), rounding=0.01)
        mat, conv = rep["elements"]["materials"], rep["elements"]["conversion"]
        self.assertEqual((mat["eu"], conv["eu"]), (1000.0, 980.0))
        self.assertAlmostEqual(mat["cost_per_eu"], 52.0)
        self.assertAlmostEqual(conv["cost_per_eu"], 60.0)
        # materials: 9,200 BWIP + 700 started-and-completed x 52 + 90 normal x 52
        self.assertAlmostEqual(mat["transferred"], 50280.0)
        self.assertAlmostEqual(mat["ending"], 10400.0)
        self.assertAlmostEqual(mat["abnormal"], 520.0)
        # conversion: 6,100 BWIP + (900 - 120) x 60 + 90 x 60
        self.assertAlmostEqual(conv["transferred"], 58300.0)
        self.assertAlmostEqual(conv["ending"], 6000.0)
        self.assertAlmostEqual(conv["abnormal"], 600.0)
        self.assertAlmostEqual(rep["transferred"], 108580.0)
        self.assertAlmostEqual(rep["normal_spoilage"], 10080.0)
        self.assertAlmostEqual(rep["ending"], 16400.0)
        self.assertAlmostEqual(rep["abnormal"], 1120.0)
        self.assertAlmostEqual(rep["cost_to_account"], 126100.0)

    def test_report_transferred_in_chaining(self):
        # FC-07 / BB 3.3-3.4: the cost transferred in from department 1 is an element of department 2 at 100 %
        units = {"beginning": 0.0, "started": 900.0, "completed": 800.0, "ending": 100.0,
                 "normal_spoiled": 0.0, "abnormal_spoiled": 0.0}
        elements = {
            "transferred_in": {"beginning_cost": 0.0, "period_cost": 108900.0, "beginning_pct": 100.0, "ending_pct": 100.0},
            "materials": {"beginning_cost": 0.0, "period_cost": 4000.0, "beginning_pct": 0.0, "ending_pct": 0.0},
            "conversion": {"beginning_cost": 0.0, "period_cost": 25500.0, "beginning_pct": 0.0, "ending_pct": 50.0},
        }
        rep = cmc_utils.process_cost_report("weighted_average", units, elements, rounding=0.01)
        ti = rep["elements"]["transferred_in"]
        self.assertEqual(ti["eu"], 900.0)
        self.assertAlmostEqual(ti["cost_per_eu"], 121.0)
        self.assertAlmostEqual(rep["elements"]["materials"]["cost_per_eu"], 5.0)
        self.assertAlmostEqual(rep["elements"]["conversion"]["cost_per_eu"], 30.0)
        self.assertAlmostEqual(rep["transferred"], 124800.0)
        self.assertAlmostEqual(rep["ending"], 13600.0)
        self.assertAlmostEqual(ti["ending"], 12100.0)

    def test_report_unbalanced_quantity_schedule_raises(self):
        # FC-07 / BB 2.2: units to account for must equal units accounted for
        units = dict(DEPT1_UNITS, started=1100.0)
        with self.assertRaises(ValueError):
            cmc_utils.process_cost_report("weighted_average", units, dept1_elements())

    def test_report_rounding_conserves_control_total(self):
        # FC-07: 59,000 / 980 = 60.2040816...: full precision per EU, cents conserved on the control total
        rep = cmc_utils.process_cost_report("fifo", DEPT1_UNITS, dept1_elements(59000.0), rounding=0.01)
        conv = rep["elements"]["conversion"]
        self.assertAlmostEqual(conv["cost_per_eu"], 59000.0 / 980.0, places=10)
        self.assertEqual(round(conv["transferred"] + conv["ending"] + conv["abnormal"], 2), 65100.0)
        for part in ("transferred", "ending", "abnormal"):
            self.assertEqual(round(conv[part], 2), conv[part])
        self.assertEqual(round(rep["transferred"] + rep["ending"] + rep["abnormal"], 2), 126300.0)

    def test_report_cost_without_units_raises(self):
        # FC-07: a cost that no equivalent unit can carry is an error, not a silent loss
        units = {"beginning": 0.0, "started": 0.0, "completed": 0.0, "ending": 0.0,
                 "normal_spoiled": 0.0, "abnormal_spoiled": 0.0}
        elements = {"materials": {"beginning_cost": 0.0, "period_cost": 500.0, "beginning_pct": 0.0, "ending_pct": 0.0}}
        with self.assertRaises(ValueError):
            cmc_utils.process_cost_report("weighted_average", units, elements)
