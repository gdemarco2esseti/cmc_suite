"""Stress test 2026-10 (corsia A) - FX-005: the CVR list shows "break-even exists" readably."""

from lxml import etree

from odoo.tests.common import TransactionCase, new_test_user, tagged

# widgets that render a boolean as a readable yes/no indicator
BOOLEAN_WIDGETS = (None, "boolean", "boolean_toggle", "boolean_icon", "boolean_favorite")


@tagged("post_install", "-at_install")
class TestFx005CvrBreakevenColumn(TransactionCase):
    """FX-005 / TS-CVR-001: the badge widget on a boolean renders raw checkbox HTML inside the badge."""

    def test_fx005_has_breakeven_not_badge_widget(self):
        # FX-005 AC: the "Break-even exists" column uses a boolean widget, readable in every language
        user = new_test_user(self.env, login="fx005_user", groups="base.group_user,cmc_base.group_cmc_user")
        Cvr = self.env["cmc.cvr.analysis"].with_user(user)
        self.assertEqual(Cvr._fields["has_breakeven"].type, "boolean")
        action = self.env.ref("cmc_direct_costing.action_cmc_cvr_analysis")
        list_view = action.view_ids.filtered(lambda v: v.view_mode == "list").view_id or action.view_id
        arch = etree.fromstring(Cvr.get_view(list_view.id or False, "list")["arch"])
        nodes = arch.xpath("//field[@name='has_breakeven']")
        self.assertTrue(nodes, "the CVR list must still show the break-even column")
        self.assertIn(nodes[0].get("widget"), BOOLEAN_WIDGETS)
