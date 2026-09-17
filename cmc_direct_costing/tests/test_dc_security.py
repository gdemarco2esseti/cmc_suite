from datetime import date

from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestDirectCostingSecurity(TransactionCase):
    """Every other test in this module runs as superuser, so the ACL and
    record-rule surface is invisible to them. These run as real CMC users."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_a = cls.env["res.company"].create({"name": "CMC DC A"})
        cls.company_b = cls.env["res.company"].create({"name": "CMC DC B"})
        cls.user_a = cls._cmc_user("cmc_dc_user_a", cls.company_a)
        cls.user_b = cls._cmc_user("cmc_dc_user_b", cls.company_b)

    @classmethod
    def _cmc_user(cls, login, company):
        # v19: the m2m is `group_ids` (`groups_id` was renamed)
        return cls.env["res.users"].create(
            {
                "name": login,
                "login": login,
                "company_id": company.id,
                "company_ids": [(6, 0, company.ids)],
                "group_ids": [
                    (
                        6,
                        0,
                        [
                            cls.env.ref("base.group_user").id,
                            cls.env.ref("cmc_base.group_cmc_user").id,
                        ],
                    )
                ],
            }
        )

    def _cvr(self, company, user=None, **kw):
        model = self.env["cmc.cvr.analysis"]
        if user:
            model = model.with_user(user)
        vals = {
            "name": "cvr %s" % company.name,
            "company_id": company.id,
            "mode": "simulazione",
            "date_from": date(2026, 1, 1),
            "date_to": date(2026, 1, 31),
            "price_unit": 10,
            "variable_unit": 4,
            "fixed_cost": 90000,
        }
        vals.update(kw)
        return model.with_company(company).create(vals)

    # ---- the engine runs end to end for a plain CMC user ------------------

    def test_cvr_end_to_end_as_cmc_user(self):
        analysis = self._cvr(self.company_a, user=self.user_a)
        analysis.action_recompute()
        self.assertTrue(analysis.has_breakeven)
        self.assertEqual(analysis.bep_qty, 15000.0)
        self.assertEqual(len(analysis.curve_ids), 4)

    def test_direct_costing_end_to_end_as_cmc_user(self):
        analysis = (
            self.env["cmc.direct.costing"]
            .with_user(self.user_a)
            .with_company(self.company_a)
            .create(
                {
                    "name": "dc",
                    "company_id": self.company_a.id,
                    "date_from": date(2026, 1, 1),
                    "date_to": date(2026, 1, 31),
                    "common_fixed_cost": 200,
                    "line_ids": [
                        (0, 0, {"name": "A", "revenue": 1000,
                                "variable_cost": 400, "specific_fixed_cost": 150}),
                    ],
                }
            )
        )
        self.assertEqual(analysis.operating_result, 250.0)

    # ---- multi-company isolation (P0) ------------------------------------

    def test_curve_is_company_isolated(self):
        """cmc.cvr.curve had neither company_id nor an ir.rule: a plain
        search_read returned the break-even points of EVERY company."""
        analysis_a = self._cvr(self.company_a)
        analysis_a.action_recompute()
        curves_a = analysis_a.curve_ids
        self.assertTrue(curves_a)
        self.assertEqual(curves_a.mapped("company_id"), self.company_a)

        Curve = self.env["cmc.cvr.curve"]
        self.assertEqual(
            Curve.with_user(self.user_a).search([("id", "in", curves_a.ids)]),
            curves_a,
        )
        self.assertFalse(
            Curve.with_user(self.user_b).search([("id", "in", curves_a.ids)]),
            "company B must not see company A break-even points",
        )
        with self.assertRaises(AccessError):
            curves_a.with_user(self.user_b).read(["revenue"])

    def test_differential_is_user_and_company_scoped(self):
        """A TransientModel uses ACLs and record rules like a regular model:
        there is NO implicit create_uid ownership filter in v19."""
        Diff = self.env["cmc.differential.analysis"]
        mine = (
            Diff.with_user(self.user_a)
            .with_company(self.company_a)
            .create(
                {
                    "company_id": self.company_a.id,
                    "decision_type": "make_or_buy",
                    "volume": 1,
                    "make_unit_cost": 12,
                    "buy_unit_cost": 10,
                }
            )
        )
        self.assertEqual(mine.verdict, "Buy")
        self.assertEqual(
            Diff.with_user(self.user_a).search([("id", "=", mine.id)]), mine
        )
        self.assertFalse(
            Diff.with_user(self.user_b).search([("id", "=", mine.id)]),
            "another user's make-or-buy parameters must not be readable",
        )

    def test_cvr_analyses_do_not_mix(self):
        analysis_a = self._cvr(self.company_a)
        analysis_b = self._cvr(self.company_b, fixed_cost=60000)
        Cvr = self.env["cmc.cvr.analysis"]
        self.assertFalse(
            Cvr.with_user(self.user_b).search([("id", "=", analysis_a.id)])
        )
        self.assertEqual(
            Cvr.with_user(self.user_b).search([("id", "=", analysis_b.id)]),
            analysis_b,
        )
