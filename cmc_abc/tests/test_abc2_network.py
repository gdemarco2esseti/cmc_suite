"""WP-07 / ABC-04, ABC-06, ABC-07, ABC-09 - multistage network R -> A -> A -> CO -> CO.

CONTRACT (see abc2_common for the whole WP)
- Edges (``cmc.abc.line``): exactly one sender (``activity_id`` | ``source_object_id``) and one receiver
  (``target_activity_id`` | ``cost_object_id`` | v1 ``product_id``) -> ValidationError otherwise.
- Each sender distributes 100% of its accumulated cost pro rata to its edges (``spread``), A->A first in
  topological order; a cycle (A->A or CO->CO) -> UserError at Compute, run stays draft (AD-118).
- ``cmc.abc.activity.cost``: ``resource_cost``, ``received_cost``, ``total_cost``, ``fixed_cost``,
  ``variable_cost``, ``used_qty``, ``rate`` (= total / used without capacity line), ``fixed_rate``,
  ``variable_rate``, ``charged_cost``, ``driver_type`` (snapshot of ``measure_type``).
- ``cmc.abc.result`` per cost object: ``traced_cost`` + ``activity_cost`` + ``object_cost`` = ``amount``;
  ``forwarded_amount`` (CO->CO out); ``fixed_amount`` + ``variable_amount`` = ``amount``; ``quantity``
  (``output_measure_id`` facts) and ``unit_cost`` = amount / quantity.
- Conservation (AD-113): total_resource_cost + total_traced_cost = total_object_cost + unused + sustaining
  + unallocated; per activity Σ in = total_cost = Σ out (flows).
- ``_cmc_bill_of_activities(object)`` / ``_cmc_bill_of_costs(activity)``: lists of dicts, Σ = node cost.
Hand numbers: see abc2_common MAIN FIXTURE (P1 7758, P2 5172, C1 700, C2 300).
"""

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from .abc2_common import Abc2Case


@tagged("post_install", "-at_install")
class TestAbc2Network(Abc2Case):
    def test_abc09_activity_to_activity(self):
        # ABC-09: support activity reassigned to primary activities (tickets 1/3 -> rate 250)
        run = self._main_run()
        admin = self._act(run, self.admin)
        self.assertAlmostEqual(admin.total_cost, 1000.0, places=2)
        self.assertAlmostEqual(admin.rate, 250.0, places=2)
        setup, mach = self._act(run, self.setup), self._act(run, self.mach)
        self.assertAlmostEqual(setup.received_cost, 250.0, places=2)
        self.assertAlmostEqual(setup.total_cost, 3650.0, places=2)
        self.assertAlmostEqual(mach.received_cost, 750.0, places=2)
        self.assertAlmostEqual(mach.total_cost, 7350.0, places=2)

    def test_abc04_activity_rates(self):
        # ABC-04: rate = pool / driver qty (no capacity line: used qty is the denominator)
        run = self._main_run()
        self.assertAlmostEqual(self._act(run, self.setup).rate, 365.0, places=4)  # 3650 / 10 setups
        self.assertAlmostEqual(self._act(run, self.qc).rate, 500.0, places=4)  # 2500 / 5 inspections
        self.assertAlmostEqual(self._act(run, self.cust).rate, 100.0, places=4)  # 1000 / 10 orders
        self.assertEqual(self._act(run, self.mach).driver_type, "duration")
        self.assertEqual(self._act(run, self.setup).driver_type, "transaction")

    def test_abc04_object_costs(self):
        # ABC-04: object = traced + Σ driver qty x rate
        run = self._main_run()
        self.assertAlmostEqual(self._res(run, self.obj_p1).amount, 7758.0, places=2)
        self.assertAlmostEqual(self._res(run, self.obj_p2).amount, 5172.0, places=2)
        self.assertAlmostEqual(self._res(run, self.obj_c1).amount, 700.0, places=2)
        self.assertAlmostEqual(self._res(run, self.obj_c2).amount, 300.0, places=2)
        # v1 surface kept: product rows still answer by product
        self.assertEqual(self._res(run, self.obj_p1).product_id, self.p1)
        self.assertFalse(self._res(run, self.obj_c1).product_id)

    def test_abc04_unit_cost(self):
        # ABC-04: unit cost = object cost / output units (facts of the span, April 999 ignored)
        run = self._main_run()
        p1, p2 = self._res(run, self.obj_p1), self._res(run, self.obj_p2)
        self.assertAlmostEqual(p1.quantity, 100.0, places=2)
        self.assertAlmostEqual(p1.unit_cost, 77.58, places=2)
        self.assertAlmostEqual(p2.unit_cost, 103.44, places=2)

    def test_abc07_direct_trace(self):
        # ABC-07: direct material is traced to the object, bypassing activities
        run = self._main_run()
        p1 = self._res(run, self.obj_p1)
        self.assertAlmostEqual(p1.traced_cost, 300.0, places=2)
        self.assertAlmostEqual(p1.activity_cost, 7458.0, places=2)
        self.assertAlmostEqual(run.total_traced_cost, 500.0, places=2)

    def test_abc07_trace_is_opt_in(self):
        # backward compat: without trace_direct the run is activity cost only (v1 semantics)
        run = self._main_run(trace_direct=False)
        self.assertAlmostEqual(self._res(run, self.obj_p1).amount, 7458.0, places=2)
        self.assertAlmostEqual(run.total_traced_cost, 0.0, places=2)

    def test_abc10_fixed_variable_carried_through(self):
        # ABC-10: F/V of the ledger reaches the object (P1 F5058 V2700; P2 F3372 V1800)
        run = self._main_run()
        p1, p2 = self._res(run, self.obj_p1), self._res(run, self.obj_p2)
        self.assertAlmostEqual(p1.fixed_amount, 5058.0, places=2)
        self.assertAlmostEqual(p1.variable_amount, 2700.0, places=2)
        self.assertAlmostEqual(p2.fixed_amount, 3372.0, places=2)
        self.assertAlmostEqual(p2.variable_amount, 1800.0, places=2)
        setup = self._act(run, self.setup)
        self.assertAlmostEqual(setup.fixed_cost, 2450.0, places=2)
        self.assertAlmostEqual(setup.variable_cost, 1200.0, places=2)

    def test_abc09_conservation(self):
        # ABC-09: Σ in = Σ out (16500 + 500 = 13930 + 1070 + 2000)
        run = self._main_run()
        self.assertAlmostEqual(run.total_object_cost, 13930.0, places=2)
        self.assertAlmostEqual(
            run.total_resource_cost + run.total_traced_cost,
            run.total_object_cost + run.unused_capacity_cost + run.sustaining_cost + run.unallocated_amount,
            places=2,
        )

    def test_abc09_every_activity_node_conserves(self):
        # ABC-09: each sender distributes 100% of what it accumulated - nothing created or lost
        run = self._main_run()
        for row in run.activity_cost_ids:
            inflow = sum(run.flow_ids.filtered(lambda f, a=row.activity_id: f.target_activity_id == a).mapped("amount"))
            outflow = sum(run.flow_ids.filtered(lambda f, a=row.activity_id: f.source_activity_id == a).mapped("amount"))
            self.assertAlmostEqual(inflow, row.total_cost, places=2, msg=row.activity_id.code)
            self.assertAlmostEqual(outflow, row.total_cost, places=2, msg=row.activity_id.code)

    def test_abc09_bill_of_activities(self):
        # ABC-04/09 drill-back: P1 = trace 300 + setups 6 x 365 + hours 60 x 62.8 + inspections 3 x 500
        run = self._main_run()
        rows = run._cmc_bill_of_activities(self.obj_p1)
        by_activity = {r["activity"]: r for r in rows if r["kind"] == "activity"}
        self.assertAlmostEqual(by_activity[self.setup]["driver_qty"], 6.0)
        self.assertAlmostEqual(by_activity[self.setup]["rate"], 365.0, places=4)
        self.assertAlmostEqual(by_activity[self.setup]["amount"], 2190.0, places=2)
        self.assertAlmostEqual(by_activity[self.mach]["amount"], 3768.0, places=2)
        self.assertAlmostEqual(by_activity[self.qc]["amount"], 1500.0, places=2)
        trace = [r for r in rows if r["kind"] == "trace"]
        self.assertAlmostEqual(sum(r["amount"] for r in trace), 300.0, places=2)
        self.assertAlmostEqual(sum(r["amount"] for r in rows), 7758.0, places=2)

    def test_abc09_bill_of_costs(self):
        # Turney Ch.5 drill-back: SETUP 3650 = R1 salaries 3000 + R2 depreciation 400 + IT support 250
        run = self._main_run()
        rows = run._cmc_bill_of_costs(self.setup)
        key = {(r["kind"], r["resource"], r["cost_element"], r["source_activity"]): r["amount"] for r in rows}
        Act = self.env["cmc.abc.activity"]
        Res = self.env["cmc.abc.resource"]
        Element = self.env["cmc.cost.element"]
        self.assertAlmostEqual(key[("resource", self.r1, self.sal, Act)], 3000.0, places=2)
        self.assertAlmostEqual(key[("resource", self.r2, self.dep, Act)], 400.0, places=2)
        self.assertAlmostEqual(key[("activity", Res, Element, self.admin)], 250.0, places=2)
        self.assertAlmostEqual(sum(r["amount"] for r in rows), 3650.0, places=2)

    def test_abc09_activity_cycle_fails_loud(self):
        # AD-118: reciprocal ABC is out of scope - a cycle must not be "solved" silently
        edges = self._edges() + [
            {"activity_id": self.setup.id, "target_activity_id": self.admin.id, "driver_id": self.tickets.id, "driver_qty": 1}
        ]
        run = self._main_run(compute=False, edges=edges)
        with self.assertRaises(UserError):
            run.action_compute()
        self.assertEqual(run.state, "draft")

    def test_abc09_edge_one_sender_one_receiver(self):
        # ABC-09: an edge is one path; two senders or no receiver is not a path
        run = self._main_run(compute=False)
        Line = self.env_a["cmc.abc.line"]
        with self.assertRaises(ValidationError):
            Line.create({"run_id": run.id, "activity_id": self.qc.id, "source_object_id": self.obj_p1.id,
                         "cost_object_id": self.obj_c1.id, "driver_qty": 1})
        with self.assertRaises(ValidationError):
            Line.create({"run_id": run.id, "activity_id": self.qc.id, "driver_qty": 1})


@tagged("post_install", "-at_install")
class TestAbc2ObjectChain(Abc2Case):
    """ABC-06/08/09: product -> customers (purchased qty) -> channel; supplier-driven cost to suppliers.

    R3 4000: PACK 50% (2000, product level) -> P1; RECV 50% (2000) -> S1 2 / S2 1 receipts.
    P1 -> C1 30 / C2 70 units bought -> 600 / 1400; C1, C2 -> channel RETAIL -> 2000.
    The v1 tag 500 on QC has no edge in this run -> unallocated 500 (v1 behaviour kept).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env_a
        cls.pack_drv = cls._driver(env, "WP07PACK", "transaction", level="product")
        cls.rcpt_drv = cls._driver(env, "WP07RCPT", "transaction")
        cls.units_drv = cls._driver(env, "WP07BUY", "transaction")
        cls.pack = cls._activity("Pack products", "WP07PACKA", cls.pr_mfg, "product", cls.pack_drv)
        cls.recv = cls._activity("Receive goods", "WP07RECV", cls.pr_sup, False, cls.rcpt_drv)
        retail = cls.env["cmc.attribute.value"].create(
            {"attribute_id": cls.env.ref("cmc_base.cmc_attribute_channel").id, "name": "WP07 Retail", "code": "WP07RET"}
        )
        Obj = env["cmc.cost.object"]
        cls.obj_retail = Obj._cmc_ensure_objects(retail, company=cls.company_a, object_type="channel")
        s1 = cls.env["res.partner"].create({"name": "WP07 Supplier 1"})
        s2 = cls.env["res.partner"].create({"name": "WP07 Supplier 2"})
        cls.obj_s1, cls.obj_s2 = Obj._cmc_ensure_objects(s1 | s2, company=cls.company_a, object_type="supplier")

    def _chain_run(self, extra_edges=(), compute=True):
        def co(src, dst, qty):
            return {"source_object_id": src.id, "cost_object_id": dst.id, "driver_id": self.units_drv.id, "driver_qty": qty}

        edges = [
            {"activity_id": self.pack.id, "cost_object_id": self.obj_p1.id, "driver_id": self.pack_drv.id, "driver_qty": 1},
            {"activity_id": self.recv.id, "cost_object_id": self.obj_s1.id, "driver_id": self.rcpt_drv.id, "driver_qty": 2},
            {"activity_id": self.recv.id, "cost_object_id": self.obj_s2.id, "driver_id": self.rcpt_drv.id, "driver_qty": 1},
            co(self.obj_p1, self.obj_c1, 30), co(self.obj_p1, self.obj_c2, 70),
            co(self.obj_c1, self.obj_retail, 1), co(self.obj_c2, self.obj_retail, 1),
            *extra_edges,
        ]
        return self._main_run(
            compute=compute, trace_direct=False, output_measure_id=False, capacity=[], edges=edges,
            resource_lines=[
                {"resource_id": self.r3.id, "activity_id": self.pack.id, "percent": 50},
                {"resource_id": self.r3.id, "activity_id": self.recv.id, "percent": 50},
            ],
        )

    def test_abc06_customer_cost_from_product_cost(self):
        # ABC-06 (Cokins MIW ch.4): customer cost = Σ purchased qty x product cost
        run = self._chain_run()
        p1 = self._res(run, self.obj_p1)
        self.assertAlmostEqual(p1.amount, 2000.0, places=2)
        self.assertAlmostEqual(p1.forwarded_amount, 2000.0, places=2)
        c1 = self._res(run, self.obj_c1)
        self.assertAlmostEqual(c1.object_cost, 600.0, places=2)
        self.assertAlmostEqual(self._res(run, self.obj_c2).amount, 1400.0, places=2)

    def test_abc06_channel_object(self):
        # ABC-06: channel object (attribute value, AD-109) collects its customers' cost
        run = self._chain_run()
        retail = self._res(run, self.obj_retail)
        self.assertEqual(self.obj_retail.object_type, "channel")
        self.assertAlmostEqual(retail.amount, 2000.0, places=2)
        self.assertAlmostEqual(retail.forwarded_amount, 0.0, places=2)

    def test_abc06_supplier_objects(self):
        # ABC-06: supplier-driven activity cost goes to suppliers, not to products (2000 by 2/1 receipts)
        run = self._chain_run()
        self.assertAlmostEqual(self._res(run, self.obj_s1).amount, 1333.33, places=2)
        self.assertAlmostEqual(self._res(run, self.obj_s2).amount, 666.67, places=2)

    def test_abc09_chain_conservation(self):
        # ABC-09: forwarded cost is counted once (final objects 2000 retail + 2000 suppliers)
        run = self._chain_run()
        self.assertAlmostEqual(run.total_object_cost, 4000.0, places=2)
        self.assertAlmostEqual(run.unallocated_amount, 500.0, places=2)  # QC tag, no edge in this run
        self.assertAlmostEqual(run.total_resource_cost, 4500.0, places=2)
        self.assertAlmostEqual(
            run.total_resource_cost + run.total_traced_cost,
            run.total_object_cost + run.unused_capacity_cost + run.sustaining_cost + run.unallocated_amount,
            places=2,
        )

    def test_abc09_object_cycle_fails_loud(self):
        # AD-118: C1 -> P1 closes a loop P1 -> C1 -> P1
        cycle = {"source_object_id": self.obj_c1.id, "cost_object_id": self.obj_p1.id,
                 "driver_id": self.units_drv.id, "driver_qty": 1}
        run = self._chain_run(extra_edges=[cycle], compute=False)
        with self.assertRaises(UserError):
            run.action_compute()
        self.assertEqual(run.state, "draft")
