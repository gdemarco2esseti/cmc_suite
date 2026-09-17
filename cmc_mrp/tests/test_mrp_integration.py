from unittest.mock import patch

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestMrpBridgeIntegration(TransactionCase):
    """Fase 1.5c — doppio conteggio, data di competenza, multi-company e ACL."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AL = cls.env["account.analytic.line"]
        cls.AA = cls.env["account.analytic.account"]
        cls.plan = cls.env["account.analytic.plan"].search(
            [("parent_id", "=", False)]
        ).filtered(lambda p: p._column_name() == "account_id")[:1]
        cls.center = cls.AA.create({
            "name": "Reparto INT", "plan_id": cls.plan.id,
            "cmc_center_type": "final",
        })
        cls.mp = cls.env.ref("cmc_base.cmc_element_mp")

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _category(self, name, valuation="periodic"):
        return self.env["product.category"].create({
            "name": name,
            "property_cost_method": "standard",
            "property_valuation": valuation,
        })

    def _mo(self, name, categ, qty=3.0, price=7.0, center=None, company=None):
        env = self.env
        if company:
            env = env["mrp.production"].with_company(company).env
        comp = env["product.product"].create({
            "name": "Comp %s" % name, "is_storable": True,
            "categ_id": categ.id, "standard_price": price,
        })
        finished = env["product.product"].create({
            "name": "Fin %s" % name, "is_storable": True, "categ_id": categ.id,
        })
        bom = env["mrp.bom"].create({
            "product_tmpl_id": finished.product_tmpl_id.id,
            "product_qty": 1.0, "type": "normal",
            "bom_line_ids": [(0, 0, {"product_id": comp.id, "product_qty": qty})],
        })
        vals = {
            "product_id": finished.id, "product_qty": 1.0, "bom_id": bom.id,
            "cmc_center_id": (center or self.center).id,
        }
        if company:
            vals["company_id"] = company.id
        return env["mrp.production"].create(vals)

    def _mark_done(self, mo, user=None):
        production = mo.with_user(user) if user else mo
        production.action_confirm()
        production.qty_producing = 1.0
        for move in production.move_raw_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        production.with_context(skip_consumption=True).button_mark_done()
        return production

    # ------------------------------------------------------------------
    # item 1 — nessuna riga parallela quando il core ne ha gia' creata una
    # ------------------------------------------------------------------

    def test_core_analytic_line_is_adopted_not_duplicated(self):
        """stock_account crea gia' una riga analitica per un movimento done con
        distribuzione analitica. Il ponte deve CLASSIFICARLA, non affiancarne
        una seconda."""
        categ = self._category("RT", valuation="real_time")
        mo = self._mo("adopt", categ)
        distribution = {str(self.center.id): 100}
        StockMove = type(self.env["stock.move"])
        with patch.object(
            StockMove, "_get_analytic_distribution",
            lambda move: dict(distribution),
        ):
            self._mark_done(mo)

        move = mo.move_raw_ids
        self.assertEqual(len(move), 1)
        core_lines = move.analytic_account_line_ids
        self.assertTrue(core_lines, "core must have booked its own analytic line")
        on_center = self.AL.search([
            ("account_id", "=", self.center.id),
            ("cmc_mrp_move_id", "=", move.id),
        ])
        self.assertEqual(
            len(on_center), 1,
            "exactly ONE cost line must land on the center for one consumption",
        )
        self.assertEqual(on_center, core_lines[:1], "core's line must be adopted")
        self.assertEqual(on_center.cmc_cost_element_id, self.mp)
        self.assertEqual(on_center.cmc_behavior, "variable")
        self.assertEqual(on_center.cmc_directness, "direct")

    # ------------------------------------------------------------------
    # item 2 — competenza: la data viene dall'ordine, non da oggi
    # ------------------------------------------------------------------

    def test_material_line_date_comes_from_the_order(self):
        categ = self._category("DATE")
        mo = self._mo("date", categ)
        mo.action_confirm()
        last_month = fields.Datetime.now() - relativedelta(months=1)
        # date_start too: date_finished is a stored compute keyed on date_start,
        # and mrp recomputes it while the order is still not 'done'.
        mo.write({"date_start": last_month, "date_finished": last_month})
        mo.qty_producing = 1.0
        for move in mo.move_raw_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        mo.with_context(skip_consumption=True).button_mark_done()

        line = mo.move_raw_ids.cmc_mrp_analytic_line_ids
        self.assertTrue(line)
        self.assertEqual(line.date, last_month.date())
        self.assertNotEqual(line.date, fields.Date.context_today(self.env.user))

    # ------------------------------------------------------------------
    # item 3 — nessuna fuga di costo cross-company
    # ------------------------------------------------------------------

    def test_center_of_another_company_is_refused(self):
        company_b = self.env["res.company"].create({"name": "MRP Company B"})
        center_b = self.AA.create({
            "name": "Reparto B", "plan_id": self.plan.id,
            "cmc_center_type": "final", "company_id": company_b.id,
        })
        categ = self._category("XC")
        with self.assertRaises(ValidationError):
            self._mo("xcompany", categ, center=center_b)

    def test_center_outside_root_plan_is_refused(self):
        other_plan = self.env["account.analytic.plan"].create({"name": "Piano 2"})
        center = self.AA.create({
            "name": "Fuori piano", "plan_id": other_plan.id,
            "cmc_center_type": "final",
        })
        categ = self._category("PLAN")
        with self.assertRaises(ValidationError):
            self._mo("plan", categ, center=center)

    def test_two_company_order_books_on_its_own_company(self):
        company_b = self.env["res.company"].create({"name": "MRP Company C"})
        self.env.user.company_ids = [(4, company_b.id)]
        center_b = self.AA.create({
            "name": "Reparto C", "plan_id": self.plan.id,
            "cmc_center_type": "final", "company_id": company_b.id,
        })
        categ = self._category("MC")
        mo = self._mo("mc", categ, center=center_b, company=company_b)
        self._mark_done(mo)
        line = mo.move_raw_ids.cmc_mrp_analytic_line_ids
        self.assertTrue(line)
        self.assertEqual(line.company_id, company_b)
        self.assertEqual(line.account_id, center_b)

    # ------------------------------------------------------------------
    # ACL — l'operatore di reparto chiude l'ordine senza diritti analitici
    # ------------------------------------------------------------------

    def test_shop_floor_user_can_close_the_order(self):
        user = new_test_user(
            self.env, login="cmc_shopfloor",
            groups="base.group_user,mrp.group_mrp_user",
        )
        self.assertFalse(user.has_group("cmc_base.group_cmc_user"))
        self.assertFalse(user.has_group("analytic.group_analytic_accounting"))
        categ = self._category("ACL")
        mo = self._mo("acl", categ)
        self._mark_done(mo, user=user)
        self.assertEqual(mo.state, "done")
        line = mo.move_raw_ids.cmc_mrp_analytic_line_ids
        self.assertTrue(line, "the bridge must post the cost through sudo")
        self.assertEqual(line.cmc_cost_element_id, self.mp)

    # ------------------------------------------------------------------
    # item 8 — la manodopera non si conta due volte
    # ------------------------------------------------------------------

    def test_labour_journal_twin_is_not_classified(self):
        """_post_labour registra le ore anche in prima nota. Se quel conto ha una
        distribuzione analitica nasce una SECONDA riga analitica per le stesse
        ore: deve restare fuori dalla base di costo."""
        account = self.env["account.account"].create({
            "name": "Costi di produzione", "code": "690900",
            "account_type": "expense",
        })
        self.env["cmc.classification.rule"].create({
            "name": "produzione", "general_account_id": account.id,
            "cmc_behavior": "fixed",
        })
        journal = self.env["account.journal"].search(
            [("type", "=", "general"), ("company_id", "=", self.env.company.id)],
            limit=1,
        )
        entry = self.env["account.move"].create({
            "move_type": "entry", "journal_id": journal.id,
            "line_ids": [
                (0, 0, {"name": "labour", "account_id": account.id, "balance": 100.0}),
                (0, 0, {"name": "labour", "account_id": account.id, "balance": -100.0}),
            ],
        })
        move_line = entry.line_ids[0]
        workcenter = self.env["mrp.workcenter"].create(
            {"name": "WC test", "costs_hour": 30.0}
        )
        self.env["mrp.workcenter.productivity"].create({
            "workcenter_id": workcenter.id,
            "loss_id": self.env.ref("mrp.block_reason7").id,
            "date_start": fields.Datetime.now(),
            "date_end": fields.Datetime.now(),
            "account_move_line_id": move_line.id,
        })
        twin = self.AL.create({
            "name": "labour twin", "account_id": self.center.id,
            "general_account_id": account.id, "move_line_id": move_line.id,
            "amount": -100.0,
        })
        self.assertFalse(
            twin.cmc_behavior,
            "the labour journal twin must not enter the cost base",
        )

    # ------------------------------------------------------------------
    # item 6 — limite noto: nessuno storno (documentato nel README)
    # ------------------------------------------------------------------

    def test_unbuild_does_not_reverse_material_cost_yet(self):
        """KNOWN LIMITATION (deferred, see README).

        A disassembly puts the components back in stock but the material cost
        stays on the cost center: the counter-entry is not implemented. This
        test PINS the current behaviour so nobody mistakes it for a bug report;
        when the reversal is built, the assertion below flips to 0.0 and this
        docstring goes away.

        (``unittest.expectedFailure`` is not an option: Odoo's own TestCase.run
        -- odoo/tests/case.py:192 -- never looks at ``expecting_failure``, so a
        test marked that way is simply reported as failed.)
        """
        categ = self._category("UNB")
        mo = self._mo("unbuild", categ)
        self._mark_done(mo)
        booked = sum(mo.move_raw_ids.cmc_mrp_analytic_line_ids.mapped("amount"))
        self.assertAlmostEqual(booked, -21.0, places=2)

        unbuild = self.env["mrp.unbuild"].create({
            "mo_id": mo.id, "product_qty": 1.0,
            "product_id": mo.product_id.id,
            "bom_id": mo.bom_id.id,
        })
        unbuild.action_validate()

        total = sum(self.AL.search(
            [("account_id", "=", self.center.id), ("ref", "=", mo.name)]
        ).mapped("amount"))
        self.assertAlmostEqual(
            total, -21.0, places=2,
            msg="TARGET once the counter-entry is implemented: 0.0",
        )
