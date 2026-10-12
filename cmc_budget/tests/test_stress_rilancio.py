"""Stress test 2026-10 (rilancio) - default BvA view and annual wizard form on cmc_budget.

- the menu action ``cmc_budget.action_cmc_budget_bva`` opens grouped by version on the latest approved version;
- the annual budget wizard hides the seasonality profile for a fixed cost (spread evenly, no profile to choose).
"""

from lxml import etree

from odoo.tests.common import TransactionCase, tagged
from odoo.tools.safe_eval import safe_eval


@tagged("post_install", "-at_install")
class TestBudgetStressRilancio(TransactionCase):
    def test_bva_action_defaults_group_and_latest_version(self):
        # FX-009 bis: the default BvA compares against ONE version and groups by version
        action = self.env.ref("cmc_budget.action_cmc_budget_bva")
        context = safe_eval(action.context or "{}")
        self.assertEqual(context.get("search_default_group_version"), 1)
        self.assertEqual(context.get("search_default_latest_version"), 1)

    def test_bva_default_filters_exist_in_search_view(self):
        # FX-009 bis: the search_default_* keys name filters that the search view really has (else silently ignored)
        action = self.env.ref("cmc_budget.action_cmc_budget_bva")
        search_view = action.search_view_id or self.env["ir.ui.view"].browse(self.env["cmc.budget.bva"].get_views([(False, "search")])["views"]["search"]["id"])
        arch = etree.fromstring(self.env["cmc.budget.bva"].get_view(search_view.id, "search")["arch"])
        names = set(arch.xpath("//filter/@name"))
        self.assertIn("group_version", names)
        self.assertIn("latest_version", names)

    def test_annual_wizard_hides_seasonality_for_fixed(self):
        # FX-016 bis: a fixed cost has no seasonality to choose: the field is hidden when behavior is 'fixed'
        view = self.env.ref("cmc_budget.view_cmc_budget_annual_wizard_form")
        arch = etree.fromstring(self.env["cmc.budget.annual.wizard"].get_view(view.id, "form")["arch"])
        fields = arch.xpath("//field[@name='seasonality_id']")
        self.assertEqual(len(fields), 1)
        self.assertIn("behavior == 'fixed'", fields[0].get("invisible") or "")
