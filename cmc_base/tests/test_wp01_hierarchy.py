"""WP-01 / CEN-02 - center hierarchy roll-up and drill-down over ``cmc_parent_id`` (AD-118: not the plan tree).

CONTRACT
- ``account.analytic.account._cmc_center_descendants()`` -> recordset = self + every CMC descendant
  (transitive ``cmc_child_ids``), walked over bounded master data (AD-114 (6)).
- ``account.analytic.account._cmc_cost_rollup(date_from, date_to, company=False)`` -> ``{account_id: cost}``
  for every record of self: node value = own cost + sum of the costs of all its descendants (positive cost,
  cost base only, plan-aware: a descendant may live on ANY analytic plan). Aggregated with
  ``_cmc_cost_groups``/``_read_group``: the query count does not grow with the number of analytic lines
  nor with the breadth of the tree.
- ``account.analytic.account.action_cmc_drilldown(date_from, date_to)`` -> ``ir.actions.act_window`` dict on
  ``account.analytic.line`` whose ``domain`` selects the cost-base lines of the center and all its
  descendants in the span (plan-aware).
- Hierarchical cost pivot = SQL view ``cmc.center.cost.report`` (``_auto = False``, AD-113/114), one row per
  (booked center, rolled-up center, element, month): ``company_id``, ``center_id`` (center the cost is booked
  on), ``rollup_center_id`` (the center itself AND each of its ancestors), ``depth`` (0 = itself, 1 = parent
  ...), ``element_id``, ``date`` (first day of the month), ``cost`` (positive, ``aggregator="sum"``).
  Grouping by ``rollup_center_id`` gives the roll-up; company rule ``parent_of``; readable by
  group_cmc_user (all) and group_cmc_responsible (own centers, see test_wp01_responsible_security).

Hand-computed tree (March 2031): BU > Dept1 > {Sub1a 100, Sub1b 250}, Dept1 own 50; BU > Dept2 400
(Dept2 on another analytic plan). Roll-up: Sub1a 100, Dept1 400, Dept2 400, BU 800. A May line of 999 on
Sub1a is outside the span.
"""

from datetime import date

from odoo.tests.common import TransactionCase, tagged

SPAN = (date(2031, 3, 1), date(2031, 3, 31))


@tagged("post_install", "-at_install")
class TestCenterHierarchy(TransactionCase):
    """CEN-02: node value = sum of its children; no 'summary department' needed."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AA = cls.env["account.analytic.account"]
        cls.AL = cls.env["account.analytic.line"]
        cls.company = cls.env.company
        cls.plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        cls.alt_plan = cls.env["account.analytic.plan"].create({"name": "WP01 Plant plan"})
        cls.element = cls.env["cmc.cost.element"].create({"name": "WP01 H element", "code": "WP01H_E"})
        cls.bu = cls._center("WP01 BU", "final")
        cls.dept1 = cls._center("WP01 Dept1", "final", parent=cls.bu)
        cls.sub1a = cls._center("WP01 Sub1a", "final", parent=cls.dept1)
        cls.sub1b = cls._center("WP01 Sub1b", "final", parent=cls.dept1)
        cls.dept2 = cls._center("WP01 Dept2", "final", parent=cls.bu, plan=cls.alt_plan)
        cls._cost(cls.sub1a, 100.0, element=cls.element)
        cls._cost(cls.sub1b, 250.0)
        cls._cost(cls.dept1, 50.0)
        cls._cost(cls.dept2, 400.0)
        cls._cost(cls.sub1a, 999.0, day=date(2031, 5, 10))

    @classmethod
    def _center(cls, name, ctype, parent=None, plan=None):
        return cls.AA.create({"name": name, "plan_id": (plan or cls.plan).id, "cmc_center_type": ctype,
                              "cmc_parent_id": parent.id if parent else False, "company_id": cls.company.id})

    @classmethod
    def _cost(cls, center, amount, day=date(2031, 3, 15), element=None):
        col = center.plan_id._column_name()
        vals = {"name": "cost", "amount": -amount, "date": day, col: center.id,
                "cmc_cost_element_id": element.id if element else False}
        if col != "account_id":
            vals["account_id"] = False
        return cls.AL.create(vals)

    def test_descendants(self):
        # CEN-02: the subtree of a node, the node included
        self.assertEqual(self.dept1._cmc_center_descendants(), self.dept1 | self.sub1a | self.sub1b)
        self.assertEqual(self.bu._cmc_center_descendants(), self.bu | self.dept1 | self.sub1a | self.sub1b | self.dept2)

    def test_rollup_node_is_sum_of_children(self):
        # CEN-02: node value = own + sum of children, across analytic plans
        tree = self.bu | self.dept1 | self.sub1a | self.sub1b | self.dept2
        rollup = tree._cmc_cost_rollup(*SPAN, company=self.company)
        self.assertAlmostEqual(rollup[self.sub1a.id], 100.0)
        self.assertAlmostEqual(rollup[self.sub1b.id], 250.0)
        self.assertAlmostEqual(rollup[self.dept1.id], 400.0)
        self.assertAlmostEqual(rollup[self.dept2.id], 400.0)
        self.assertAlmostEqual(rollup[self.bu.id], 800.0)

    def test_rollup_respects_span(self):
        # CEN-02: the May line enters only a span that covers May
        rollup = self.bu._cmc_cost_rollup(date(2031, 1, 1), date(2031, 12, 31), company=self.company)
        self.assertAlmostEqual(rollup[self.bu.id], 1799.0)

    def test_drilldown_opens_subtree_lines(self):
        # CEN-02: drill-down from a node shows the lines of the whole subtree in the span
        action = self.dept1.action_cmc_drilldown(*SPAN)
        self.assertEqual(action["res_model"], "account.analytic.line")
        lines = self.AL.search(action["domain"])
        self.assertEqual(len(lines), 3)
        self.assertAlmostEqual(-sum(lines.mapped("amount")), 400.0)

    def test_pivot_view_rolls_up(self):
        # CEN-02: the hierarchical pivot groups by rolled-up center
        Report = self.env["cmc.center.cost.report"]
        tree = self.bu | self.dept1 | self.sub1a | self.sub1b | self.dept2
        rows = Report._read_group(
            [("rollup_center_id", "in", tree.ids), ("date", ">=", SPAN[0]), ("date", "<=", SPAN[1])],
            ["rollup_center_id"], ["cost:sum"],
        )
        totals = {center.id: cost for center, cost in rows}
        self.assertAlmostEqual(totals[self.bu.id], 800.0)
        self.assertAlmostEqual(totals[self.dept1.id], 400.0)
        self.assertAlmostEqual(totals[self.dept2.id], 400.0)
        self.assertAlmostEqual(totals[self.sub1a.id], 100.0)

    def test_pivot_view_depth_and_element(self):
        # CEN-02: depth 0 rows are the booked center itself; element survives the roll-up
        Report = self.env["cmc.center.cost.report"]
        own = Report.search([("center_id", "=", self.sub1a.id), ("depth", "=", 0), ("date", "=", SPAN[0])])
        self.assertEqual(own.rollup_center_id, self.sub1a)
        self.assertEqual(own.element_id, self.element)
        self.assertAlmostEqual(sum(own.mapped("cost")), 100.0)
        at_bu = Report.search([("center_id", "=", self.sub1a.id), ("rollup_center_id", "=", self.bu.id),
                               ("date", "=", SPAN[0])])
        self.assertEqual(set(at_bu.mapped("depth")), {2})

    def test_rollup_query_count_bounded(self):
        # AD-114: roll-up cost does not grow with lines nor with the breadth of the tree
        def build(prefix, children, lines_per_child):
            root = self._center(f"{prefix} root", "final")
            for i in range(children):
                child = self._center(f"{prefix} child {i}", "final", parent=root)
                for _j in range(lines_per_child):
                    self._cost(child, 10.0)
            return root

        small = build("WP01 small", 2, 1)
        large = build("WP01 large", 40, 3)
        counts = []
        for root in (small, large):
            self.env.invalidate_all()
            before = self.cr.sql_log_count
            rollup = root._cmc_cost_rollup(*SPAN, company=self.company)
            counts.append(self.cr.sql_log_count - before)
        self.assertAlmostEqual(rollup[large.id], 1200.0)
        self.assertLessEqual(counts[1], counts[0] + 2, f"query count grows with the tree: {counts}")
