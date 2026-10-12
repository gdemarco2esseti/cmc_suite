from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestCmcSuiteInstall(TransactionCase):
    """The umbrella's whole job is installing the nine modules together.
    This pins that the cascade actually resolves: xmlid collisions and menu
    parent-resolution problems between siblings only show up at install time."""

    def test_every_suite_module_is_installed(self):
        expected = [
            "cmc_base",
            "cmc_abc",
            "cmc_tdabc",
            "cmc_full_costing",
            "cmc_direct_costing",
            "cmc_joint_costs",
            "cmc_account",
            "cmc_hr_timesheet",
            "cmc_mrp",
        ]
        modules = self.env["ir.module.module"].search([("name", "in", expected)])
        self.assertEqual(
            set(modules.mapped("name")), set(expected), "a suite module is missing"
        )
        not_installed = modules.filtered(lambda m: m.state != "installed")
        self.assertFalse(
            not_installed.mapped("name"),
            "installing cmc_suite must install the whole suite",
        )

    def test_root_menu_is_unique(self):
        """Every module hangs its section under the same root: a duplicate root
        would split the app in two in the Apps menu."""
        roots = self.env["ir.ui.menu"].search(
            [("parent_id", "=", False), ("name", "ilike", "Cost Management")]
        )
        self.assertEqual(len(roots), 1, "expected exactly one CMC root menu")
