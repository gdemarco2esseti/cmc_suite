"""WP-16 / AD-115 - target, kaizen, feature and LCC runs as real CMC users, inside their company; results are
engine rows. CONTRACT: see common.py (ACL and record rules)."""

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import new_test_user, tagged

from .common import TlcCase


@tagged("post_install", "-at_install")
class TestSecurity(TlcCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_b = cls.env["res.company"].create({"name": "CMC WP16 Co B"})
        cls.user_b = new_test_user(cls.env, login="cmc_wp16_user_b", groups="base.group_user,cmc_base.group_cmc_user",
                                   company_id=cls.company_b.id, company_ids=[(6, 0, cls.company_b.ids)])

    def test_cmc_user_computes_target_sheet(self):
        # TGT-01: the controller who sets the target is a CMC user, deployment rows created by the engine
        target = self._target(user=self.user)
        self._deploy(target)
        target.action_compute()
        self.assertRecordValues(target, [{"state": "done", "allowable_cost": 150.0, "current_cost": 180.0}])
        self.assertEqual(len(target.result_ids), 4)

    def test_cmc_user_computes_feature_costing(self):
        # TGT-02: a CMC user (product engineer) costs design alternatives
        costing, _alts = self._feature({"A": [(self.assembly, self.parts, 10, None)]}, user=self.user)
        costing.action_compute()
        self.assertEqual(costing.result_ids.estimated_cost, 20.0)

    def test_cmc_user_computes_lcc(self):
        # LCC-01: a CMC user computes a life-cycle model
        model = self._base_lcc(user=self.user)
        model.action_compute()
        self.assertEqual(model.lc_cost, 49500.0)
        self.assertEqual(len(model.year_result_ids), 4)

    def test_cmc_user_computes_kaizen(self):
        # TGT-01 kaizen: a CMC user tracks the reduction target
        kaizen = self.env["cmc.kaizen.target"].with_user(self.user).create({
            "name": "WP16 kz", "basis": "unit", "product_id": self.k.id, "unit_driver_id": self.units.id,
            "base_date": self.producer.date_from, "date_from": self.producer.date_from, "date_to": self.producer.date_to,
            "reduction_pct": 6.0,
        })
        kaizen.action_compute()
        self.assertEqual(kaizen.target_unit_cost, 47.0)

    def test_user_cannot_write_results(self):
        # AD-001: a CMC user reads the deployment and the LC years, never types them
        target = self._target(user=self.user)
        target.action_compute()
        with self.assertRaises(AccessError):
            self.env["cmc.target.cost.result"].with_user(self.user).create({"target_id": target.id, "target_cost": 1.0})
        model = self._base_lcc(user=self.user)
        model.action_compute()
        with self.assertRaises(AccessError):
            model.year_result_ids[:1].with_user(self.user).write({"cost": 1.0})

    def test_user_cannot_write_header_outputs(self):
        # AD-113: the allowable cost is computed, not typed
        target = self._target(user=self.user)
        with self.assertRaises(UserError):
            target.allowable_cost = 99.0

    def test_user_cannot_delete_manager_can(self):
        # AD-115: deleting a run is a manager's call
        target = self._target(user=self.user)
        model = self._base_lcc(user=self.user)
        with self.assertRaises(AccessError):
            target.unlink()
        with self.assertRaises(AccessError):
            model.unlink()
        target.with_user(self.manager).unlink()
        model.with_user(self.manager).unlink()
        self.assertFalse(target.exists() or model.exists())

    def test_plain_user_has_no_access(self):
        # AD-115: target and life-cycle figures are CMC data
        with self.assertRaises(AccessError):
            self._target(user=self.outsider)
        with self.assertRaises(AccessError):
            self._base_lcc(user=self.outsider)
        target = self._target()
        with self.assertRaises(AccessError):
            target.with_user(self.outsider).read(["allowable_cost"])
        with self.assertRaises(AccessError):
            self.env["cmc.design.guideline"].with_user(self.outsider).search([])

    def test_other_company_cannot_see(self):
        # AD-115: a company B user neither finds nor reads company A sheets, models and their rows
        target = self._target(user=self.user)
        self._deploy(target)
        target.action_compute()
        model = self._base_lcc(user=self.user)
        model.action_compute()
        self.assertFalse(self.env["cmc.target.cost"].with_user(self.user_b).search([("id", "=", target.id)]))
        self.assertFalse(self.env["cmc.target.cost.result"].with_user(self.user_b).search([("target_id", "=", target.id)]))
        self.assertFalse(self.env["cmc.lcc.model"].with_user(self.user_b).search([("id", "=", model.id)]))
        self.assertFalse(self.env["cmc.lcc.cost.line"].with_user(self.user_b).search([("model_id", "=", model.id)]))
        self.assertFalse(self.env["cmc.lcc.year.result"].with_user(self.user_b).search([("model_id", "=", model.id)]))
        with self.assertRaises(AccessError):
            model.with_user(self.user_b).read(["lc_cost"])

    def test_rates_of_another_company_are_not_read(self):
        # AD-108 / AD-115: a company B sheet cannot take company A's unit cost as its current cost
        target = self.env["cmc.target.cost"].with_user(self.user_b).create({
            "name": "WP16 B", "product_id": self.p.id, "unit_driver_id": self.units.id, "date": self.producer.date_from,
            "target_price": 200.0, "target_margin_pct": 25.0,
        })
        with self.assertRaises(UserError):
            target.action_compute()
