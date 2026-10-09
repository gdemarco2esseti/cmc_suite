import ast

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, new_test_user, tagged

LABOUR_COST = 30.0  # 60 minutes logged x 30.0 / hour


@tagged("post_install", "-at_install")
class TestMrpLabourTwin(TransactionCase):
    """AC4 -- manufacturing labour must be counted ONCE in management control.

    mrp_account books workcenter time twice on purpose: an analytic line on the
    workcenter distribution (mrp_workorder._create_or_update_analytic_entry,
    category 'manufacturing_order') and a journal entry (_post_labour). When an
    account of that entry carries an analytic distribution (here: an
    account.analytic.distribution.model), posting it creates a SECOND analytic
    line on the same center. Core posts the entry (-> analytic lines) BEFORE it
    writes time_ids.account_move_line_id, so a detector that looks the twin up
    at create time finds nothing.

    Everything below runs the REAL flow: workorder duration logged by the
    operator, button_mark_done, core _post_labour. Nothing is hand-built.
    Material and finished-good values are zero on purpose, so that the only
    money landing on the center is labour.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AL = cls.env["account.analytic.line"]
        cls.AA = cls.env["account.analytic.account"]
        cls.company = cls.env.company
        cls.plan = cls.env["account.analytic.plan"].search(
            [("parent_id", "=", False)]
        ).filtered(lambda p: p._column_name() == "account_id")[:1]
        cls.center = cls.AA.create({
            "name": "Reparto Manodopera", "plan_id": cls.plan.id,
            "cmc_center_type": "final",
        })
        cls.mod = cls.env.ref("cmc_base.cmc_element_mod")
        cls.categ = cls.env["product.category"].create({
            "name": "LABOUR RT",
            "property_cost_method": "standard",
            # _post_labour skips anything not valued in real time
            # (mrp_account/models/mrp_production.py:108)
            "property_valuation": "real_time",
        })
        # account credited by _post_labour (wo.workcenter_id.expense_account_id,
        # mrp_production.py:116)
        cls.labour_account = cls.env["account.account"].create({
            "name": "Manodopera assorbita", "code": "691917",
            "account_type": "expense",
        })
        # account debited by _post_labour (production_location.valuation_account_id,
        # mrp_production.py:107,124); without it core posts nothing (line 108)
        cls.wip_account = cls.env["account.account"].create({
            "name": "WIP produzione", "code": "139917",
            "account_type": "asset_current",
        })
        cls.production_location = cls.env["product.product"].with_company(
            cls.company
        ).new({"categ_id": cls.categ.id}).property_stock_production
        cls.production_location.valuation_account_id = cls.wip_account
        cls.workcenter = cls.env["mrp.workcenter"].create({
            "name": "WC Manodopera",
            "costs_hour": 30.0,
            "expense_account_id": cls.labour_account.id,
            "analytic_distribution": {str(cls.center.id): 100},
        })

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _distribution_model(self, account):
        """The customer's analytic default: every journal item on `account`
        goes to the center."""
        return self.env["account.analytic.distribution.model"].create({
            "account_prefix": account.code,
            "analytic_distribution": {str(self.center.id): 100},
            "company_id": self.company.id,
        })

    def _mo(self, name):
        comp = self.env["product.product"].create({
            "name": "Comp %s" % name, "is_storable": True,
            "categ_id": self.categ.id, "standard_price": 0.0,
        })
        finished = self.env["product.product"].create({
            "name": "Fin %s" % name, "is_storable": True,
            "categ_id": self.categ.id, "standard_price": 0.0,
        })
        bom = self.env["mrp.bom"].create({
            "product_tmpl_id": finished.product_tmpl_id.id,
            "product_qty": 1.0, "type": "normal",
            "bom_line_ids": [(0, 0, {"product_id": comp.id, "product_qty": 1.0})],
            "operation_ids": [(0, 0, {
                "name": "Assemblaggio",
                "workcenter_id": self.workcenter.id,
                "time_cycle_manual": 60.0,
            })],
        })
        return self.env["mrp.production"].create({
            "product_id": finished.id, "product_qty": 1.0, "bom_id": bom.id,
        })

    def _complete(self, mo, user=None):
        """What the operator does: confirm, log 60 minutes on the work order,
        consume, mark done."""
        production = mo.with_user(user) if user else mo
        production.action_confirm()
        self.assertEqual(len(production.workorder_ids), 1)
        production.workorder_ids.duration = 60.0
        production.qty_producing = 1.0
        for move in production.move_raw_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        production.with_context(skip_consumption=True).button_mark_done()
        mo.invalidate_recordset()
        self.assertEqual(mo.state, "done")
        return mo

    def _labour_entry(self, mo):
        """The journal entry core posted for the labour (precondition guard:
        if core did not post it, the scenario does not reproduce the defect
        and the test must say so instead of passing vacuously)."""
        stamped = mo.workorder_ids.time_ids.account_move_line_id
        self.assertTrue(
            stamped,
            "precondition: core _post_labour must have posted the labour entry "
            "and stamped it on the work order times",
        )
        return stamped.move_id

    def _twins(self, mo):
        """Analytic lines generated by the labour journal entry."""
        return self.AL.search([
            ("move_line_id", "in", self._labour_entry(mo).line_ids.ids),
        ])

    def _center_total(self):
        return self.center._cmc_cost_groups().get(self.center.id, 0.0)

    def _unclassified_queue(self):
        action = self.env.ref("cmc_account.action_cmc_unclassified_lines")
        return self.AL.search(ast.literal_eval(action.domain))

    def _assert_scenario(self, mo):
        """Shared preconditions: the expected cost is the one core itself
        computes, and both bookings really exist."""
        wo = mo.workorder_ids
        self.assertAlmostEqual(wo._cal_cost(), LABOUR_COST, places=2)
        wc_lines = wo.wc_analytic_account_line_ids
        self.assertTrue(wc_lines, "precondition: workcenter analytic line booked")
        self.assertAlmostEqual(sum(wc_lines.mapped("amount")), -LABOUR_COST, places=2)
        self.assertTrue(
            self._twins(mo),
            "precondition: posting the labour entry must create an analytic twin",
        )
        return wc_lines

    # ------------------------------------------------------------------
    # AC4.1 -- labour counted ONCE on the center (numeric, independent of
    # any new field)
    # ------------------------------------------------------------------

    def test_ac41_labour_once_model_on_expense_account(self):
        # AC4.1: distribution model on the labour (credited) account -> the twin
        # is the line stamped on time_ids; the center must show -cost once.
        self._distribution_model(self.labour_account)
        mo = self._complete(self._mo("ac41a"))
        self._assert_scenario(mo)
        self.assertAlmostEqual(
            self._center_total(), -LABOUR_COST, places=2,
            msg="labour must be counted once on the center (= -wo._cal_cost())",
        )

    def test_ac41_labour_once_model_on_wip_account(self):
        # AC4.1: distribution model on the production-location (debited) account
        # -> the twin is the LAST line of the entry, which core never stamps on
        # time_ids; the center must still show -cost once.
        self._distribution_model(self.wip_account)
        mo = self._complete(self._mo("ac41b"))
        self._assert_scenario(mo)
        self.assertAlmostEqual(
            self._center_total(), -LABOUR_COST, places=2,
            msg="labour must be counted once on the center (= -wo._cal_cost())",
        )

    # ------------------------------------------------------------------
    # AC4.2 -- the twins are excluded, out of the queue, not classifiable
    # ------------------------------------------------------------------

    def test_ac42_twin_excluded_model_on_expense_account(self):
        # AC4.2: the twin generated by the stamped journal item is excluded.
        self._distribution_model(self.labour_account)
        mo = self._complete(self._mo("ac42a"))
        wc_lines = self._assert_scenario(mo)
        twins = self._twins(mo)
        stamped = mo.workorder_ids.time_ids.account_move_line_id
        self.assertTrue(twins.filtered(lambda line: line.move_line_id in stamped))
        self.assertTrue(all(twins.mapped("cmc_excluded")))
        self.assertFalse(any(wc_lines.mapped("cmc_excluded")))
        self.assertFalse(any(twins.mapped("cmc_classified_manually")))
        self.assertEqual(wc_lines.cmc_cost_element_id, self.mod)
        self.assertFalse(self._unclassified_queue() & twins)

    def test_ac42_twin_excluded_model_on_wip_account(self):
        # AC4.2: the twin of the debited (unstamped) journal item is a cost
        # (amount < 0) and would otherwise sit in the Unclassified queue.
        self._distribution_model(self.wip_account)
        mo = self._complete(self._mo("ac42b"))
        wc_lines = self._assert_scenario(mo)
        twins = self._twins(mo)
        self.assertTrue(all(twins.mapped("cmc_excluded")))
        self.assertFalse(any(wc_lines.mapped("cmc_excluded")))
        self.assertFalse(
            self._unclassified_queue() & twins,
            "the labour twin must not clog the Unclassified queue",
        )

    def test_ac42_twin_not_classified_even_if_a_rule_matches(self):
        # AC4.2 (ex test_labour_journal_twin_is_not_classified): a rule on the
        # entry's account must not pull the twin into the cost base.
        self._distribution_model(self.wip_account)
        self.env["cmc.classification.rule"].create({
            "name": "WIP produzione", "general_account_id": self.wip_account.id,
            "cmc_behavior": "fixed",
        })
        mo = self._complete(self._mo("ac42r"))
        self._assert_scenario(mo)
        twins = self._twins(mo)
        self.assertFalse(any(twins.mapped("cmc_behavior")))
        self.assertTrue(all(twins.mapped("cmc_excluded")))

    def test_ac42_manual_classification_of_twin_raises(self):
        # AC4.2: a controller cannot force the twin back into the cost base.
        self._distribution_model(self.wip_account)
        mo = self._complete(self._mo("ac42m"))
        self._assert_scenario(mo)
        twins = self._twins(mo)
        with self.assertRaises(UserError):
            twins.with_user(self._controller()).write({
                "cmc_classified_manually": True,
                "cmc_cost_element_id": self.mod.id,
            })

    def _controller(self):
        return new_test_user(
            self.env, login="cmc_twin_controller",
            groups="base.group_user,cmc_base.group_cmc_user,"
            "account.group_account_manager",
        )

    # ------------------------------------------------------------------
    # AC4.5 -- the exclusion survives every way core rebuilds the analytic
    # lines of the labour entry (the flag lives on the journal item too)
    # ------------------------------------------------------------------

    def _assert_labour_once(self, mo):
        entry = self._labour_entry(mo)
        lines = self.AL.search([("move_line_id", "in", entry.line_ids.ids)])
        self.assertTrue(lines)
        self.assertTrue(all(lines.mapped("cmc_excluded")))
        self.assertFalse(any(lines.mapped("cmc_behavior")))
        self.assertAlmostEqual(self._center_total(), -LABOUR_COST, places=2)

    def test_ac45_twin_stays_excluded_after_draft_and_repost(self):
        self._distribution_model(self.wip_account)
        mo = self._complete(self._mo("ac45a"))
        self._assert_scenario(mo)
        entry = self._labour_entry(mo)
        old = self._twins(mo)
        entry.button_draft()
        entry.action_post()
        self.assertFalse(old.exists(), "precondition: core rebuilt the lines")
        self._assert_labour_once(mo)

    def test_ac45_twin_excluded_when_distribution_added_to_posted_line(self):
        # no distribution model: the entry has no analytic line until the
        # controller adds the missing analytic account to the posted entry.
        mo = self._complete(self._mo("ac45b"))
        entry = self._labour_entry(mo)
        self.assertFalse(self.AL.search([("move_line_id", "in", entry.line_ids.ids)]))
        entry.line_ids.filtered(
            lambda line: line.account_id == self.labour_account
        ).write({"analytic_distribution": {str(self.center.id): 100}})
        self._assert_labour_once(mo)

    def test_ac45_reversal_of_the_labour_entry_is_excluded(self):
        self._distribution_model(self.wip_account)
        mo = self._complete(self._mo("ac45c"))
        entry = self._labour_entry(mo)
        reversal = entry._reverse_moves(
            [{"date": fields.Date.context_today(entry)}]
        )
        reversal._post()
        rev_lines = self.AL.search([("move_line_id", "in", reversal.line_ids.ids)])
        self.assertTrue(rev_lines, "precondition: the reversal has analytic lines")
        self.assertTrue(all(rev_lines.mapped("cmc_excluded")))
        self._assert_labour_once(mo)

    # ------------------------------------------------------------------
    # the exclusion flag is system-only, over RPC too
    # ------------------------------------------------------------------

    def test_excluded_flag_cannot_be_written_by_a_user(self):
        line = self.AL.create({
            "name": "costo", "account_id": self.center.id, "amount": -10.0,
        })
        with self.assertRaises(AccessError):
            line.with_user(self._controller()).write({"cmc_excluded": True})
        with self.assertRaises(AccessError):
            self.AL.with_user(self._controller()).create({
                "name": "costo", "account_id": self.center.id,
                "amount": -10.0, "cmc_excluded": True,
            })
        self.assertFalse(line.cmc_excluded)

    def test_excluded_flag_on_journal_item_is_system_only(self):
        self._distribution_model(self.wip_account)
        mo = self._complete(self._mo("rpc"))
        item = self._labour_entry(mo).line_ids[:1]
        self.assertTrue(item.cmc_excluded)
        with self.assertRaises(AccessError):
            item.with_user(self._controller()).write({"cmc_excluded": False})

    # ------------------------------------------------------------------
    # AC4.3 -- ordinary lines are not excluded
    # ------------------------------------------------------------------

    def test_ac43_non_mrp_line_not_excluded_by_default(self):
        # AC4.3: exclusion is reserved to the labour twins.
        line = self.AL.create({
            "name": "costo ordinario", "account_id": self.center.id,
            "amount": -10.0,
        })
        self.assertFalse(line.cmc_excluded)

    # ------------------------------------------------------------------
    # AC4.4 -- the shop floor closes the order without CMC rights
    # ------------------------------------------------------------------

    def test_ac44_shop_floor_user_completes_the_order(self):
        # AC4.4: the exclusion runs on a shop-floor close (no CMC/analytic rights).
        user = new_test_user(
            self.env, login="cmc_labour_shopfloor",
            groups="base.group_user,mrp.group_mrp_user",
        )
        self.assertFalse(user.has_group("cmc_base.group_cmc_user"))
        self._distribution_model(self.wip_account)
        mo = self._mo("ac44")
        self._complete(mo, user=user)
        self._assert_scenario(mo)
        self.assertTrue(all(self._twins(mo).mapped("cmc_excluded")))
        self.assertAlmostEqual(self._center_total(), -LABOUR_COST, places=2)

    # ------------------------------------------------------------------
    # access cost -- the twin detection runs on EVERY analytic line created
    # from a journal entry (a table that grows with usage): it must stay
    # batched, i.e. constant in the number of lines.
    # ------------------------------------------------------------------

    def _posted_entry_lines(self, count):
        journal = self.env["account.journal"].search(
            [("type", "=", "general"), ("company_id", "=", self.company.id)],
            limit=1,
        )
        entry = self.env["account.move"].create({
            "move_type": "entry", "journal_id": journal.id,
            "line_ids": [
                (0, 0, {"name": "wip %s" % i, "account_id": self.wip_account.id, "balance": 10.0})
                for i in range(count)
            ] + [(0, 0, {"name": "contra", "account_id": self.labour_account.id, "balance": -10.0 * count})],
        })
        entry._post()
        lines = self.AL.search([("move_line_id", "in", entry.line_ids.ids)])
        self.assertEqual(len(lines), count)
        return lines

    def _classification_queries(self, lines):
        lines.invalidate_recordset()
        before = self.env.cr.sql_log_count
        lines._cmc_auto_classify()
        lines.mapped("cmc_excluded")
        return self.env.cr.sql_log_count - before

    def test_twin_detection_cost_is_constant_in_line_count(self):
        # costo d'accesso: the exclusion check must not issue one query per line.
        self._distribution_model(self.wip_account)
        small = self._classification_queries(self._posted_entry_lines(5))
        large = self._classification_queries(self._posted_entry_lines(60))
        self.assertEqual(
            large, small,
            "twin detection/exclusion must be batched (N+1 on analytic lines)",
        )

    # ------------------------------------------------------------------
    # the twin shortcut must not become a way around ACL and record rules
    # ------------------------------------------------------------------

    def test_twin_creation_keeps_the_callers_create_rights(self):
        self._distribution_model(self.wip_account)
        item = self._labour_entry(self._complete(self._mo("acl"))).line_ids[:1]
        self.assertTrue(item.cmc_excluded)
        nobody = new_test_user(self.env, login="cmc_twin_nobody", groups="base.group_user")
        before = self.AL.search_count([])
        with self.assertRaises(AccessError):
            self.AL.with_user(nobody).create({
                "name": "forged", "account_id": self.center.id, "amount": -999.0,
                "move_line_id": item.id,
            })
        self.assertEqual(self.AL.search_count([]), before)
