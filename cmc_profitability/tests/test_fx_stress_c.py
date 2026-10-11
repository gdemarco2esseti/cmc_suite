"""Stress-test fixes 2026-10, corsia C, on ``cmc_profitability``: FX-014, FX-006b, FX-017.

CONTRACT
- FX-014: computing a ``cmc.profit.run`` linked to a computed ABC run whose results assign NO cost to the
  product (product-level activity flows), customer or channel objects - so every margin layer M1..M4 would be
  equal - raises ``UserError`` whose message names the ABC run (its display name), and nothing is written
  (run stays draft, no fact). An ABC run that assigns cost to at least one of those levels computes as before
  (the main fixture, pinned by ``test_layered_margin``); no ABC run at all computes as before (only M1).
- FX-006b: the source check of the ABC run tells apart "the ABC run has no dates" from "the ABC run lies
  outside the span of the run": the two messages differ, and neither repeats the word ("ABC ABC") when the
  ABC run name itself starts with "ABC".
- FX-017: the ``state`` selection labels of ``cmc.profit.run`` are translated in it_IT: draft -> "Bozza",
  done -> "Calcolato".
Fixture: ``ProfitCase`` (March 2027); ledger G 205 is the business-sustaining cost of the "Manage the business"
(facility) activity.
"""

from datetime import date

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import D_FROM, D_TO, ProfitCase


@tagged("post_install", "-at_install")
class TestFxStressProfit(ProfitCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # the fixture's own resource of center G (one resource per center x element: no double count)
        cls.res_g = cls.env["cmc.abc.resource"].search([("code", "=", "WP09RG"), ("center_id", "=", cls.g.id)])

    def _sustaining_only_abc(self, name, date_from=D_FROM, date_to=D_TO):
        """A computed ABC run whose whole cost goes to the business-sustaining object: nothing reaches a product,
        a customer or a channel."""
        abc = self.env["cmc.abc.run"].create({
            "name": name, "company_id": self.company.id, "date_from": date_from, "date_to": date_to,
            "resource_line_ids": [Command.create({"resource_id": self.res_g.id, "activity_id": self.act_mgmt.id, "percent": 100})],
        })
        abc.action_compute()
        return abc

    def _compute_error(self, abc):
        run = self._run(compute=False, abc_run_id=abc.id)
        with self.assertRaises(UserError) as caught:
            run.action_compute()
        self.assertEqual(run.state, "draft")
        self.assertFalse(run.fact_ids)
        return str(caught.exception)

    # ---- FX-014 -----------------------------------------------------------

    def test_fx014_abc_without_product_customer_channel_cost_refused(self):
        # FX-014: a linked ABC run that serves no cost to products / customers / channels flattens M1..M4 -> fail loud
        abc = self._sustaining_only_abc("FX014 sustaining only")
        self.assertGreater(abc.sustaining_cost, 0.0)
        message = self._compute_error(abc)
        self.assertIn(abc.display_name, message)

    # ---- FX-006b ----------------------------------------------------------

    def test_fx006b_abc_without_dates_has_its_own_message(self):
        # FX-006b: an ABC run with empty dates is told apart from one outside the span; no doubled "ABC ABC"
        undated = self._sustaining_only_abc("ABC January", date_from=False, date_to=False)
        outside = self._sustaining_only_abc("ABC January", date_from=date(2027, 2, 15), date_to=D_TO)
        missing_dates = self._compute_error(undated)
        out_of_span = self._compute_error(outside)
        self.assertNotEqual(missing_dates, out_of_span)
        self.assertNotIn("ABC ABC", missing_dates)
        self.assertNotIn("ABC ABC", out_of_span)

    # ---- FX-017 -----------------------------------------------------------

    def test_fx017_state_labels_translated_in_italian(self):
        # FX-017: an Italian user reads "Bozza / Calcolato" on the statusbar of the profitability run
        self.assertTrue(self.env["res.lang"]._lang_get("it_IT"), "it_IT must be installed in the test database")
        selection = dict(self.env["cmc.profit.run"].with_context(lang="it_IT").fields_get(["state"])["state"]["selection"])
        self.assertEqual(selection["draft"], "Bozza")
        self.assertEqual(selection["done"], "Calcolato")
