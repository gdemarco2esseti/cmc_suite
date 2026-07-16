from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestTdabc(TransactionCase):
    """Time-Driven ABC."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Char = cls.env["cmc.tdabc.characteristic"]
        cls.Res = cls.env["cmc.tdabc.resource"]
        cls.Act = cls.env["cmc.tdabc.activity"]
        cls.Run = cls.env["cmc.tdabc.run"]
        cls.p1 = cls.env["product.product"].create({"name": "P1"})
        cls.p2 = cls.env["product.product"].create({"name": "P2"})
        cls.cust = cls.env["res.partner"].create({"name": "Cliente A"})

    def _res_default(self):
        return self.Res.create({
            "name": "R", "cost": 1000.0,
            "theoretical_time": 2000.0, "util_factor": 100.0,
        })

    def _tag_hours(self, activity, hours, center):
        self.env["account.analytic.line"].create({
            "name": "act", "account_id": center.id,
            "unit_amount": hours, "cmc_tdabc_activity_id": activity.id,
        })

    @classmethod
    def _center(cls):
        plan = cls.env["account.analytic.plan"].search(
            [("parent_id", "=", False)]
        ).filtered(lambda p: p._column_name() == "account_id")[:1]
        return cls.env["account.analytic.account"].create(
            {"name": "C", "plan_id": plan.id, "cmc_center_type": "final"}
        )

    def test_char_value_on_product(self):
        hazmat = self.Char.create({"code": "HAZMAT", "name": "Hazardous", "ttype": "bool"})
        self.p1.product_tmpl_id.tdabc_char_value_ids = [(0, 0, {
            "characteristic_id": hazmat.id, "value": 1.0,
        })]
        vals = self.p1.product_tmpl_id.tdabc_char_value_ids
        self.assertEqual(vals.characteristic_id, hazmat)

    def test_char_value_on_partner(self):
        vip = self.Char.create({"code": "VIP", "name": "VIP", "ttype": "bool"})
        self.cust.tdabc_char_value_ids = [(0, 0, {"characteristic_id": vip.id})]
        self.assertEqual(self.cust.tdabc_char_value_ids.characteristic_id, vip)

    def test_resource_rate(self):
        res = self.Res.create({
            "name": "Magazzino", "cost": 80000.0,
            "theoretical_time": 211200.0, "util_factor": 75.0,
        })
        # practical = 211200 * 0.75 = 158400 ; rate = 80000/158400
        self.assertAlmostEqual(res.practical_capacity, 158400.0, places=2)
        self.assertAlmostEqual(res.rate, 80000.0 / 158400.0, places=6)

    def test_resource_zero_capacity_rate(self):
        res = self.Res.create({
            "name": "Vuoto", "cost": 1000.0,
            "theoretical_time": 0.0, "util_factor": 75.0,
        })
        self.assertEqual(res.practical_capacity, 0.0)
        self.assertEqual(res.rate, 0.0)

    def test_equation_multi_term(self):
        hazmat = self.Char.create({"code": "HAZMAT", "name": "Haz", "ttype": "bool"})
        weight = self.Char.create({"code": "WEIGHT", "name": "Weight", "ttype": "number"})
        act = self.Act.create({
            "name": "Pick", "code": "PICK",
            "resource_id": self._res_default().id, "base_time": 0.5,
            "term_ids": [
                (0, 0, {"characteristic_id": hazmat.id, "coefficient": 8.5}),
                (0, 0, {"characteristic_id": weight.id, "coefficient": 2.0}),
            ],
        })
        # product with hazmat=1 and weight=3 -> 0.5 + 8.5*1 + 2.0*3 = 15.0
        tmpl = self.p1.product_tmpl_id
        tmpl.tdabc_char_value_ids = [
            (0, 0, {"characteristic_id": hazmat.id, "value": 1.0}),
            (0, 0, {"characteristic_id": weight.id, "value": 3.0}),
        ]
        char_map = act._char_map(self.p1, self.env["res.partner"])
        self.assertEqual(act._unit_time(char_map), 15.0)

    def test_equation_missing_char_is_zero(self):
        act = self.Act.create({
            "name": "Empty", "code": "E",
            "resource_id": self._res_default().id, "base_time": 1.0,
        })
        self.assertEqual(act._unit_time({}), 1.0)

    def test_run_line_cost_object_product(self):
        act = self.Act.create({
            "name": "A", "code": "A", "resource_id": self._res_default().id,
            "base_time": 1.0,
        })
        run = self.Run.create({
            "name": "R", "line_ids": [(0, 0, {
                "activity_id": act.id, "cost_object_type": "product",
                "product_id": self.p1.id, "volume": 10,
            })],
        })
        self.assertEqual(run.line_ids.product_id, self.p1)
        self.assertEqual(run.line_ids.cost_object_type, "product")

    def _simple_run(self, base_time=15.0, volume=8000, util=100.0,
                    cost=79200.0, theo=158400.0):
        res = self.Res.create({
            "name": "WH", "cost": cost, "theoretical_time": theo, "util_factor": util,
        })
        act = self.Act.create({
            "name": "Pick", "code": "PICK", "resource_id": res.id, "base_time": base_time,
        })
        run = self.Run.create({
            "name": "R", "line_ids": [(0, 0, {
                "activity_id": act.id, "cost_object_type": "product",
                "product_id": self.p1.id, "volume": volume,
            })],
        })
        run.action_compute()
        return run, res, act

    def _man_res(self, run, product):
        return run.result_ids.filtered(
            lambda r: r.scenario == "manual" and r.product_id == product
        )

    def _man_cap(self, run, res):
        return run.capacity_result_ids.filtered(
            lambda c: c.scenario == "manual" and c.resource_id == res
        )

    def test_manual_absorbed_cost(self):
        # rate = 79200/158400 = 0.5 ; time = 15*8000 = 120000 ; cost = 60000
        run, res, act = self._simple_run()
        self.assertEqual(res.rate, 0.5)
        r = self._man_res(run, self.p1)
        self.assertEqual(r.time_absorbed, 120000.0)
        self.assertEqual(r.cost_absorbed, 60000.0)
        self.assertEqual(run.state, "done")

    def test_manual_unused_capacity(self):
        # required 120000 of 158400 -> unused 38400 * 0.5 = 19200 ; use 75.75%
        run, res, act = self._simple_run()
        cap = self._man_cap(run, res)
        self.assertEqual(cap.required_time, 120000.0)
        self.assertEqual(cap.unused_time, 38400.0)
        self.assertEqual(cap.unused_cost, 19200.0)
        self.assertFalse(cap.over_capacity)
        self.assertAlmostEqual(cap.capacity_use_pct, 75.7575, places=3)

    def test_manual_conservation(self):
        # Σ cost_absorbed + unused_cost == resource cost
        run, res, act = self._simple_run()
        absorbed = sum(run.result_ids.filtered(
            lambda r: r.scenario == "manual").mapped("cost_absorbed"))
        unused = self._man_cap(run, res).unused_cost
        self.assertEqual(absorbed + unused, res.cost)

    def test_over_capacity(self):
        # volume 12000 -> time 180000 > practical 158400
        run, res, act = self._simple_run(volume=12000)
        cap = self._man_cap(run, res)
        self.assertTrue(cap.over_capacity)
        self.assertLess(cap.unused_time, 0.0)
        self.assertEqual(run.state, "done")

    def test_practical_zero_raises(self):
        from odoo.exceptions import UserError
        res = self.Res.create({
            "name": "Z", "cost": 1000.0, "theoretical_time": 0.0, "util_factor": 100.0,
        })
        act = self.Act.create({
            "name": "A", "code": "A", "resource_id": res.id, "base_time": 1.0,
        })
        run = self.Run.create({
            "name": "R", "line_ids": [(0, 0, {
                "activity_id": act.id, "cost_object_type": "product",
                "product_id": self.p1.id, "volume": 5,
            })],
        })
        with self.assertRaises(UserError):
            run.action_compute()

    def test_unit_time_negative_raises(self):
        from odoo.exceptions import UserError
        neg = self.Char.create({"code": "NEG", "name": "Neg", "ttype": "number"})
        res = self.Res.create({
            "name": "R", "cost": 1000.0, "theoretical_time": 2000.0, "util_factor": 100.0,
        })
        act = self.Act.create({
            "name": "A", "code": "A", "resource_id": res.id, "base_time": 1.0,
            "term_ids": [(0, 0, {"characteristic_id": neg.id, "coefficient": -10.0})],
        })
        self.p1.product_tmpl_id.tdabc_char_value_ids = [
            (0, 0, {"characteristic_id": neg.id, "value": 5.0})]  # 1 - 50 = -49
        run = self.Run.create({
            "name": "R", "line_ids": [(0, 0, {
                "activity_id": act.id, "cost_object_type": "product",
                "product_id": self.p1.id, "volume": 1,
            })],
        })
        with self.assertRaises(UserError):
            run.action_compute()

    def test_cost_object_customer(self):
        res = self.Res.create({
            "name": "R", "cost": 1000.0, "theoretical_time": 2000.0, "util_factor": 100.0,
        })
        act = self.Act.create({
            "name": "Serve", "code": "S", "resource_id": res.id, "base_time": 2.0,
        })
        run = self.Run.create({
            "name": "R", "line_ids": [(0, 0, {
                "activity_id": act.id, "cost_object_type": "customer",
                "partner_id": self.cust.id, "volume": 10,
            })],
        })
        run.action_compute()
        r = run.result_ids.filtered(
            lambda x: x.scenario == "manual" and x.partner_id == self.cust)
        self.assertEqual(r.time_absorbed, 20.0)  # 2 * 10
        self.assertEqual(r.cost_absorbed, 10.0)  # rate 0.5 * 20

    def test_recompute_idempotent(self):
        run, res, act = self._simple_run()
        n = len(run.result_ids)
        run.action_compute()
        self.assertEqual(len(run.result_ids), n)
        self.assertEqual(self._man_res(run, self.p1).cost_absorbed, 60000.0)

    def test_timesheet_flat_unit_time(self):
        center = self._center()
        res = self.Res.create({
            "name": "R", "cost": 1000.0, "theoretical_time": 20000.0, "util_factor": 100.0,
        })
        act = self.Act.create({
            "name": "Pick", "code": "P", "resource_id": res.id, "base_time": 15.0,
        })
        # actual 100 hours = 6000 min for the activity, total volume 8000 -> flat 0.75 min/unit
        self._tag_hours(act, 100.0, center)
        run = self.Run.create({
            "name": "R", "line_ids": [(0, 0, {
                "activity_id": act.id, "cost_object_type": "product",
                "product_id": self.p1.id, "volume": 8000,
            })],
        })
        run.action_compute()
        ts = run.result_ids.filtered(
            lambda r: r.scenario == "timesheet" and r.product_id == self.p1)
        self.assertAlmostEqual(ts.time_absorbed, 6000.0, places=2)  # 0.75*8000
        man = run.result_ids.filtered(
            lambda r: r.scenario == "manual" and r.product_id == self.p1)
        self.assertEqual(man.time_absorbed, 120000.0)  # 15*8000

    def test_dual_scenario_present(self):
        center = self._center()
        res = self.Res.create({
            "name": "R", "cost": 1000.0, "theoretical_time": 20000.0, "util_factor": 100.0,
        })
        act = self.Act.create({
            "name": "A", "code": "A", "resource_id": res.id, "base_time": 1.0,
        })
        self._tag_hours(act, 10.0, center)
        run = self.Run.create({
            "name": "R", "line_ids": [(0, 0, {
                "activity_id": act.id, "cost_object_type": "product",
                "product_id": self.p1.id, "volume": 100,
            })],
        })
        run.action_compute()
        scen = set(run.result_ids.mapped("scenario"))
        self.assertEqual(scen, {"manual", "timesheet"})

    def test_timesheet_zero_volume_skips_activity(self):
        center = self._center()
        res = self.Res.create({
            "name": "R", "cost": 1000.0, "theoretical_time": 20000.0, "util_factor": 100.0,
        })
        act = self.Act.create({
            "name": "A", "code": "A", "resource_id": res.id, "base_time": 1.0,
        })
        self._tag_hours(act, 10.0, center)
        # line volume 0 -> Σvolume 0 -> timesheet unit_time 0, no crash
        run = self.Run.create({
            "name": "R", "line_ids": [(0, 0, {
                "activity_id": act.id, "cost_object_type": "product",
                "product_id": self.p1.id, "volume": 0,
            })],
        })
        run.action_compute()
        ts = run.result_ids.filtered(
            lambda r: r.scenario == "timesheet" and r.product_id == self.p1)
        self.assertEqual(ts.time_absorbed, 0.0)

    def test_derived_volume(self):
        # Uses a CUSTOMER cost object because partner_id (unlike product_id) is
        # always present on account.analytic.line without the `account` module.
        center = self._center()
        res = self.Res.create({
            "name": "R", "cost": 1000.0, "theoretical_time": 20000.0, "util_factor": 100.0,
        })
        act = self.Act.create({
            "name": "A", "code": "A", "resource_id": res.id, "base_time": 2.0,
        })
        # 3 tagged analytic lines for customer cust -> derived volume 3
        for _ in range(3):
            self.env["account.analytic.line"].create({
                "name": "act", "account_id": center.id, "unit_amount": 1.0,
                "cmc_tdabc_activity_id": act.id, "partner_id": self.cust.id,
            })
        run = self.Run.create({
            "name": "R", "line_ids": [(0, 0, {
                "activity_id": act.id, "cost_object_type": "customer",
                "partner_id": self.cust.id, "volume": 999, "volume_derived": True,
            })],
        })
        run.action_compute()
        man = run.result_ids.filtered(
            lambda r: r.scenario == "manual" and r.partner_id == self.cust)
        self.assertEqual(man.time_absorbed, 6.0)  # 2 * 3 (derived), not 999

    def test_conservation_non_exact_rate(self):
        # cost 100 / practical 7 -> non-representable rate; unused is the residual
        # plug so Σ cost_absorbed + unused_cost == cost EXACTLY across ≥2 objects.
        res = self.Res.create({
            "name": "NX", "cost": 100.0, "theoretical_time": 7.0, "util_factor": 100.0,
        })
        act = self.Act.create({
            "name": "A", "code": "A", "resource_id": res.id, "base_time": 1.0,
        })
        run = self.Run.create({
            "name": "R", "line_ids": [
                (0, 0, {"activity_id": act.id, "cost_object_type": "product",
                        "product_id": self.p1.id, "volume": 2}),
                (0, 0, {"activity_id": act.id, "cost_object_type": "product",
                        "product_id": self.p2.id, "volume": 1}),
            ],
        })
        run.action_compute()
        absorbed = sum(run.result_ids.filtered(
            lambda r: r.scenario == "manual").mapped("cost_absorbed"))
        unused = self._man_cap(run, res).unused_cost
        self.assertEqual(absorbed + unused, res.cost)

    def test_char_map_company_isolation(self):
        # a char value scoped to another company must NOT feed a run in this one
        c2 = self.env["res.company"].create({"name": "TDABC C2"})
        haz = self.Char.create({
            "code": "HZ", "name": "H", "ttype": "number", "company_id": False,
        })
        self.p1.product_tmpl_id.tdabc_char_value_ids = [(0, 0, {
            "characteristic_id": haz.id, "value": 9.0, "company_id": c2.id,
        })]
        act = self.Act.create({
            "name": "A", "code": "A", "resource_id": self._res_default().id,
            "base_time": 1.0,
            "term_ids": [(0, 0, {"characteristic_id": haz.id, "coefficient": 1.0})],
        })
        # scoped to this company: c2 value excluded -> unit_time = base 1.0
        cmap = act._char_map(self.p1, self.env["res.partner"], self.env.company)
        self.assertEqual(act._unit_time(cmap), 1.0)
        # unscoped: value applies -> 1 + 1*9 = 10.0
        cmap_all = act._char_map(self.p1, self.env["res.partner"])
        self.assertEqual(act._unit_time(cmap_all), 10.0)
