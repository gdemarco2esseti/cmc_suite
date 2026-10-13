"""UX menu reorganisation - top "tabs" of the Cost Management app.

The app root (cmc_base.menu_cmc_root) is split into workflow tabs:
Cockpit / Recording / Product Costing / Planning / Decision / Performance / Financial Analysis /
Configuration. The reorganisation is a pure menuitem move: it must NOT change what each role can
reach (the role visibility invariance test pins today's reachable actions per role).

Optional modules: sections/leaves of modules that are not installed are skipped through
env.ref(..., raise_if_not_found=False). The tabs themselves live in cmc_base and are mandatory.
"""

from odoo.tests.common import TransactionCase, new_test_user, tagged

from odoo.addons.cmc_base.migration_utils import reload_menu_translations

# Expected children of menu_cmc_root, in display order (sequence, id), with their groups.
TABS = [
    ("cmc_base.menu_cmc_cockpit", 1, "cmc_base.group_cmc_user"),
    ("cmc_base.menu_cmc_tab_recording", 10, "cmc_base.group_cmc_user"),
    ("cmc_base.menu_cmc_operations", 20, "cmc_base.group_cmc_user"),
    ("cmc_base.menu_cmc_tab_planning", 30, "cmc_base.group_cmc_user"),
    ("cmc_base.menu_cmc_tab_decision", 40, "cmc_base.group_cmc_user"),
    ("cmc_base.menu_cmc_responsibility", 50, "cmc_base.group_cmc_responsible"),
    ("cmc_base.menu_cmc_tab_fin", 60, "cmc_base.group_cmc_user"),
    ("cmc_base.menu_cmc_config", 90, "cmc_base.group_cmc_manager"),
]

# Menus that must sit DIRECTLY under the given parent.
DIRECT_PARENT = {
    "cmc_base.menu_cmc_tab_recording": [
        "cmc_base.menu_cmc_sec_base",
        "cmc_base.menu_cmc_sec_data_quality",
    ],
    "cmc_base.menu_cmc_sec_data_quality": [
        "cmc_account.menu_cmc_unclassified",
        "cmc_account.menu_cmc_coge_reconciliation",
        "cmc_base.menu_cmc_data_check",
    ],
    "cmc_base.menu_cmc_operations": [
        "cmc_full_costing.menu_cmc_sec_full",
        "cmc_job_costing.menu_cmc_sec_job_costing",
        "cmc_process_costing.menu_cmc_sec_process_costing",
        "cmc_standard_cost.menu_cmc_sec_standard",
        "cmc_abc.menu_cmc_sec_abc",
        "cmc_tdabc.menu_cmc_sec_tdabc",
        "cmc_joint_costs.menu_cmc_sec_joint",
    ],
    "cmc_base.menu_cmc_tab_planning": [
        "cmc_budget.menu_cmc_sec_budget",
        "cmc_abb.menu_cmc_sec_abb",
        "cmc_scenario.menu_cmc_sec_scenario",
        "cmc_target_lcc.menu_cmc_sec_target_lcc",
    ],
    "cmc_base.menu_cmc_tab_decision": [
        "cmc_direct_costing.menu_cmc_sec_direct",
        "cmc_profitability.menu_cmc_sec_profitability",
        "cmc_abc.menu_cmc_sec_abm",
        "cmc_base.menu_cmc_sec_analysis",
    ],
    "cmc_base.menu_cmc_sec_analysis": [
        "cmc_base.menu_cmc_capacity",
        "cmc_base.menu_cmc_cost_analysis",
        "cmc_base.menu_cmc_rate",
    ],
    # UX-001: Performance tab = Responsibility Accounting + KPI & Scorecard + Dashboards sections
    "cmc_base.menu_cmc_responsibility": [
        "cmc_base.menu_cmc_sec_responsibility",
        "cmc_kpi.menu_cmc_sec_kpi",
        "cmc_base.menu_cmc_sec_dashboards",
    ],
    "cmc_base.menu_cmc_sec_responsibility": [
        "cmc_base.menu_cmc_responsibility_center",
        "cmc_base.menu_cmc_responsibility_summary",
        "cmc_base.menu_cmc_center_cost_report",
        "cmc_kpi.menu_cmc_kpi_own",
    ],
    "cmc_base.menu_cmc_sec_dashboards": [
        "cmc_base.menu_dash_cost_center",
        "cmc_base.menu_dash_idle",
    ],
    # UX-002 / AD-006: the old step guide moved out of the Cockpit into Configuration
    "cmc_base.menu_cmc_config": [
        "cmc_base.menu_cmc_setup_guide",
    ],
}

# Menus that must have the given menu among their ancestors (any depth).
ANCESTOR = {
    "cmc_base.menu_cmc_tab_fin": [
        "cmc_fin_analysis.menu_cmc_fin_analysis",
        "cmc_fin_analysis.menu_cmc_fin_value",
        "cmc_fin_analysis.menu_cmc_fin_ratio_value",
        "cmc_fin_analysis.menu_cmc_fin_crisis",
        "cmc_fin_analysis.menu_cmc_fin_override",
        "cmc_fin_analysis.menu_cmc_fin_criteria_change",
        "cmc_fin_analysis.menu_cmc_fin_history",
        "cmc_fin_analysis.menu_cmc_fin_input",
        "cmc_fin_analysis.menu_cmc_fin_benchmark",
    ],
    # every leaf that was under cmc_base.menu_cmc_cfg_master before the reorganisation
    "cmc_base.menu_cmc_config": [
        "cmc_base.menu_cmc_cost_driver",
        "cmc_base.menu_cmc_cost_function",
        "cmc_base.menu_cmc_cost_element",
        "cmc_base.menu_cmc_cost_object",
        "cmc_base.menu_cmc_attribute",
        "cmc_abc.menu_cmc_abm_measure",
        "cmc_abc.menu_cmc_abc_activity",
        "cmc_abc.menu_cmc_abc_resource",
        "cmc_budget.menu_cmc_budget_seasonality",
        "cmc_target_lcc.menu_cmc_design_guideline",
        "cmc_tdabc.menu_cmc_tdabc_resource",
        "cmc_tdabc.menu_cmc_tdabc_activity",
        "cmc_tdabc.menu_cmc_tdabc_characteristic",
        "cmc_tdabc.menu_cmc_tdabc_char_value",
    ],
}

# Reachable actions per role, pinned from the menu tree BEFORE the reorganisation
# (db with every cmc_* module installed, computed through load_menus on 2026-10-13).
# Responsible-only: Budget vs Actual is NOT reachable today (its parent Operations is user-only): kept as is.
ACTIONS_RESPONSIBLE = {
    "cmc_base.action_cmc_center_cost_report",
    "cmc_base.action_cmc_responsibility_center",
    "cmc_base.action_cmc_responsibility_summary",
    "cmc_kpi.action_cmc_kpi_value",
}
ACTIONS_USER = ACTIONS_RESPONSIBLE | {
    "cmc_abb.action_cmc_abb_bottleneck",
    "cmc_abb.action_cmc_abb_capacity_state",
    "cmc_abb.action_cmc_abb_run",
    "cmc_abb.action_cmc_abb_variance_line",
    "cmc_abb.action_cmc_abb_variance_run",
    "cmc_abb.action_cmc_abb_whatif",
    "cmc_abc.action_cmc_abc_bill_of_activities",
    "cmc_abc.action_cmc_abc_bill_of_costs",
    "cmc_abc.action_cmc_abc_process_costs",
    "cmc_abc.action_cmc_abc_run",
    "cmc_abc.action_cmc_abc_traditional",
    "cmc_abc.action_cmc_abc_unused_capacity",
    "cmc_abc.action_cmc_abm_benchmark",
    "cmc_abc.action_cmc_abm_coq",
    "cmc_abc.action_cmc_abm_initiative",
    "cmc_abc.action_cmc_abm_matrix",
    "cmc_abc.action_cmc_abm_pareto",
    "cmc_abc.action_cmc_abm_performance",
    "cmc_abc.action_cmc_abm_process",
    "cmc_abc.action_cmc_abm_quadrant",
    "cmc_abc.action_cmc_abm_value_added",
    "cmc_abc.action_cmc_abm_waste",
    "cmc_account.action_cmc_coge_reconciliation",
    "cmc_account.action_cmc_unclassified_lines",
    "cmc_base.action_cmc_allocation",
    "cmc_base.action_cmc_analytic_lines",
    "cmc_base.action_cmc_capacity",
    "cmc_base.action_cmc_center_service",
    "cmc_base.action_cmc_closing_run",
    "cmc_base.action_cmc_cockpit_dashboard",
    "cmc_base.action_cmc_cost_analysis",
    "cmc_base.action_cmc_cost_center",
    "cmc_base.action_cmc_data_check_wizard",
    "cmc_base.action_cmc_measure_quantity",
    "cmc_base.action_cmc_plan_version",
    "cmc_base.action_cmc_rate",
    "cmc_base.action_dash_cost_center",
    "cmc_base.action_dash_idle",
    "cmc_budget.action_cmc_budget_bva",
    "cmc_budget.action_cmc_budget_cash_run",
    "cmc_budget.action_cmc_budget_line",
    "cmc_budget.action_cmc_budget_variance_run",
    "cmc_direct_costing.action_cmc_cvr_analysis",
    "cmc_direct_costing.action_cmc_decision",
    "cmc_direct_costing.action_cmc_direct_costing",
    "cmc_fin_analysis.action_cmc_fin_analysis",
    "cmc_fin_analysis.action_cmc_fin_benchmark",
    "cmc_fin_analysis.action_cmc_fin_crisis",
    "cmc_fin_analysis.action_cmc_fin_criteria_change",
    "cmc_fin_analysis.action_cmc_fin_history",
    "cmc_fin_analysis.action_cmc_fin_input",
    "cmc_fin_analysis.action_cmc_fin_override",
    "cmc_fin_analysis.action_cmc_fin_ratio_value",
    "cmc_fin_analysis.action_cmc_fin_value",
    "cmc_full_costing.action_cmc_costing_run",
    "cmc_full_costing.action_cmc_figurative",
    "cmc_job_costing.action_cmc_evm_baseline",
    "cmc_job_costing.action_cmc_evm_change_request",
    "cmc_job_costing.action_cmc_evm_curve_series",
    "cmc_job_costing.action_cmc_evm_run",
    "cmc_job_costing.action_cmc_job_cost_run",
    "cmc_job_costing.action_cmc_job_rate",
    "cmc_job_costing.action_cmc_job_sheet",
    "cmc_joint_costs.action_cmc_joint_run",
    "cmc_kpi.action_cmc_kpi",
    "cmc_kpi.action_cmc_kpi_run",
    "cmc_kpi.action_cmc_kpi_scorecard",
    "cmc_kpi.action_cmc_kpi_scorecard_result",
    "cmc_process_costing.action_cmc_process_run",
    "cmc_profitability.action_cmc_profit_channel",
    "cmc_profitability.action_cmc_profit_customer",
    "cmc_profitability.action_cmc_profit_fact",
    "cmc_profitability.action_cmc_profit_product",
    "cmc_profitability.action_cmc_profit_quadrant",
    "cmc_profitability.action_cmc_profit_run",
    "cmc_profitability.action_cmc_profit_sbu",
    "cmc_profitability.action_cmc_profit_supplier",
    "cmc_profitability.action_cmc_profit_waterfall_customer",
    "cmc_profitability.action_cmc_profit_whale",
    "cmc_profitability.action_cmc_transfer_charge",
    "cmc_scenario.action_cmc_scenario_comparison",
    "cmc_scenario.action_cmc_scenario_consolidation",
    "cmc_scenario.action_cmc_scenario_simulation",
    "cmc_standard_cost.action_cmc_standard_card",
    "cmc_standard_cost.action_cmc_standard_variance_run",
    "cmc_target_lcc.action_cmc_feature_costing",
    "cmc_target_lcc.action_cmc_kaizen",
    "cmc_target_lcc.action_cmc_lcc_model",
    "cmc_target_lcc.action_cmc_target_cost",
    "cmc_tdabc.action_cmc_tdabc_run",
}
# AD-006 (deliberate change): + the Setup Guide client action, reachable through the manager-only Configuration
# tab (the client action itself carries no group_ids in Odoo 19: gating is the menu). 114 -> 115 actions.
ACTIONS_MANAGER = ACTIONS_USER | {
    "cmc_base.action_cmc_setup_guide",
    "cmc_abc.action_cmc_abc_activity",
    "cmc_abc.action_cmc_abc_resource",
    "cmc_abc.action_cmc_abm_measure",
    "cmc_account.action_cmc_classification_rule",
    "cmc_base.action_cmc_attribute",
    "cmc_base.action_cmc_cost_driver",
    "cmc_base.action_cmc_cost_element",
    "cmc_base.action_cmc_cost_function",
    "cmc_base.action_cmc_cost_object",
    "cmc_budget.action_cmc_budget_seasonality",
    "cmc_fin_analysis.action_cmc_fin_mapping_rule",
    "cmc_fin_analysis.action_cmc_fin_ratio",
    "cmc_fin_analysis.action_cmc_fin_scheme",
    "cmc_profitability.action_cmc_profit_price_element",
    "cmc_standard_cost.action_cmc_standard_reconciliation",
    "cmc_target_lcc.action_cmc_design_guideline",
    "cmc_tdabc.action_cmc_tdabc_activity",
    "cmc_tdabc.action_cmc_tdabc_char_value",
    "cmc_tdabc.action_cmc_tdabc_characteristic",
    "cmc_tdabc.action_cmc_tdabc_resource",
}


# shells populated only by optional modules (budget/scenario, fin_analysis): empty on a minimal install
OPTIONAL_SHELLS = {"cmc_base.menu_cmc_tab_planning", "cmc_base.menu_cmc_tab_fin", "cmc_base.menu_cmc_cfg_planning"}


@tagged("post_install", "-at_install")
class TestMenuTabs(TransactionCase):
    """UX menu tabs: structure, ordering, names and role visibility invariance."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Menu = cls.env["ir.ui.menu"].sudo()
        cls.root = cls.env.ref("cmc_base.menu_cmc_root")
        cls.users = {
            "responsible": new_test_user(
                cls.env, login="menu_tabs_resp", groups="base.group_user,cmc_base.group_cmc_responsible"
            ),
            "user": new_test_user(cls.env, login="menu_tabs_user", groups="base.group_user,cmc_base.group_cmc_user"),
            "manager": new_test_user(
                cls.env, login="menu_tabs_mgr", groups="base.group_user,cmc_base.group_cmc_manager"
            ),
        }

    # ------------------------------------------------------------------------------------------------- helpers

    def _ref(self, xmlid):
        return self.env.ref(xmlid, raise_if_not_found=False)

    def _xmlid(self, record):
        return record.get_external_id().get(record.id) or f"{record._name},{record.id}"

    def _children(self, menu):
        return self.Menu.search([("parent_id", "=", menu.id)], order="sequence, id")

    def _descendants(self, menu):
        result = self.Menu.browse()
        todo = self._children(menu)
        while todo:
            result |= todo
            todo = self.Menu.search([("parent_id", "in", todo.ids)])
        return result

    def _ancestors(self, menu):
        ancestors = []
        parent = menu.parent_id
        while parent:
            ancestors.append(parent)
            parent = parent.parent_id
        return ancestors

    def _reachable_actions(self, user):
        menus = self.env["ir.ui.menu"].with_user(user).load_menus(False)
        if self.root.id not in menus:
            return set(), menus
        actions = set()

        def walk(menu_id):
            menu = menus[menu_id]
            if menu.get("action_id"):
                actions.add(self._xmlid(self.env[menu["action_model"]].browse(menu["action_id"])))
            for child_id in menu["children"]:
                walk(child_id)

        walk(self.root.id)
        return actions, menus

    # ------------------------------------------------------------------------------------------------- tests

    def test_top_level_tabs_order(self):
        # the app shows its tabs in the workflow order Cockpit > Recording > Costing > Planning > Decision
        # > Performance > Financial Analysis > Configuration, nothing else at top level
        expected = [xmlid for xmlid, _seq, _grp in TABS]
        actual = [self._xmlid(m) for m in self._children(self.root)]
        self.assertEqual(actual, expected)
        sequences = [m.sequence for m in self._children(self.root)]
        self.assertEqual(len(sequences), len(set(sequences)), f"duplicate tab sequences: {sequences}")

    def test_top_level_tabs_sequence_and_groups(self):
        # each tab has its contractual sequence and the role group that gates it
        for xmlid, sequence, group in TABS:
            with self.subTest(tab=xmlid):
                menu = self._ref(xmlid)
                self.assertTrue(menu, f"{xmlid} missing")
                self.assertEqual(menu.parent_id, self.root)
                self.assertEqual(menu.sequence, sequence)
                self.assertEqual({self._xmlid(g) for g in menu.group_ids}, {group})

    def test_sections_direct_parent(self):
        # every section lives in the tab of its workflow step
        for parent_xmlid, children in DIRECT_PARENT.items():
            parent = self._ref(parent_xmlid)
            for child_xmlid in children:
                child = self._ref(child_xmlid)
                if not child and not child_xmlid.startswith("cmc_base."):
                    continue  # optional module not installed
                with self.subTest(menu=child_xmlid):
                    self.assertTrue(parent, f"{parent_xmlid} missing")
                    self.assertTrue(child, f"{child_xmlid} missing")
                    self.assertEqual(self._xmlid(child.parent_id), parent_xmlid)

    def test_leaves_ancestor_tab(self):
        # fin analysis leaves end up in the Financial Analysis tab, master data stays in Configuration
        for ancestor_xmlid, leaves in ANCESTOR.items():
            ancestor = self._ref(ancestor_xmlid)
            for leaf_xmlid in leaves:
                leaf = self._ref(leaf_xmlid)
                if not leaf:
                    continue  # optional module not installed
                with self.subTest(menu=leaf_xmlid):
                    self.assertTrue(ancestor, f"{ancestor_xmlid} missing")
                    self.assertIn(ancestor, self._ancestors(leaf), f"{leaf_xmlid} not under {ancestor_xmlid}")

    def test_fin_section_under_fin_tab(self):
        # the Financial Statement Analysis operational section is no longer inside Operations
        section = self._ref("cmc_fin_analysis.menu_cmc_sec_fin_analysis")
        if not section:
            self.skipTest("cmc_fin_analysis not installed")
        self.assertIn(self._ref("cmc_base.menu_cmc_tab_fin"), [section, *self._ancestors(section)])
        self.assertNotIn(self._ref("cmc_base.menu_cmc_operations"), self._ancestors(section))

    def test_dashboard_items_moved_to_performance(self):
        # the two dashboard reports are user-gated items of the Performance tab
        for xmlid in ("cmc_base.menu_dash_cost_center", "cmc_base.menu_dash_idle"):
            with self.subTest(menu=xmlid):
                menu = self._ref(xmlid)
                self.assertTrue(menu, f"{xmlid} missing")
                self.assertEqual(menu.parent_id, self._ref("cmc_base.menu_cmc_sec_dashboards"))
                self.assertEqual({self._xmlid(g) for g in menu.group_ids}, {"cmc_base.group_cmc_user"})

    def test_no_sibling_sequence_collision(self):
        # siblings with the same sequence are ordered by id, i.e. by load order: the order must be explicit
        collisions = []
        for parent in self.root | self._descendants(self.root):
            children = self._children(parent)
            seen = {}
            for child in children:
                seen.setdefault(child.sequence, []).append(self._xmlid(child))
            for sequence, xmlids in seen.items():
                if len(xmlids) > 1:
                    collisions.append(f"{self._xmlid(parent)} seq {sequence}: {', '.join(xmlids)}")
        self.assertFalse(collisions, "sibling sequence collisions:\n" + "\n".join(collisions))

    def test_dashboard_menu_removed(self):
        # the "Dashboard" tab is dissolved: its items moved to Performance
        self.assertFalse(self._ref("cmc_base.menu_cmc_dashboard"))

    def test_role_visibility_invariance(self):
        # the reorganisation moves menus, it must not grant or remove any reachable action for any role
        expected_by_role = {
            "responsible": ACTIONS_RESPONSIBLE,
            "user": ACTIONS_USER,
            "manager": ACTIONS_MANAGER,
        }
        for role, expected in expected_by_role.items():
            with self.subTest(role=role):
                expected = {xmlid for xmlid in expected if self._ref(xmlid)}
                actual, _menus = self._reachable_actions(self.users[role])
                self.assertEqual(
                    actual,
                    expected,
                    f"{role}: gained {sorted(actual - expected)} / lost {sorted(expected - actual)}",
                )

    def test_no_visible_empty_folder(self):
        # no role sees a folder (tab/section without action) that opens on nothing
        for role, user in self.users.items():
            _actions, menus = self._reachable_actions(user)
            self.assertIn(self.root.id, menus, f"{role} must see the app")
            for menu_id, menu in menus.items():
                if menu_id == "root" or menu["app_id"] != self.root.id or menu.get("action_id"):
                    continue
                with self.subTest(role=role, menu=menu["xmlid"]):
                    self.assertTrue(menu["children"], f"{role}: empty folder {menu['xmlid']}")

    def test_no_empty_folder_in_tree(self):
        # structurally: every action-less menu of the app has at least one child (moves leave no orphan shell)
        # why: these cmc_base folders are filled only by optional modules (budget/abb/scenario/target_lcc,
        # fin_analysis); with cmc_base alone they are legitimately empty and the web client hides them
        optional_shells = OPTIONAL_SHELLS
        empty = [
            self._xmlid(menu)
            for menu in self._descendants(self.root)
            if not menu.action and not self._children(menu) and self._xmlid(menu) not in optional_shells
        ]
        self.assertFalse(empty, f"empty folders: {empty}")

    def test_tab_visible_to_its_role(self):
        # a tab gated on a role group is actually shown to that role (not silently empty for it)
        for role, user in self.users.items():
            _actions, menus = self._reachable_actions(user)
            user_groups = user.all_group_ids
            for xmlid, _seq, _grp in TABS:
                tab = self._ref(xmlid)
                if not tab or not tab.group_ids or not (tab.group_ids & user_groups):
                    continue
                # optional shells are filled only by optional modules: without them the tab is hidden by design
                if xmlid in OPTIONAL_SHELLS and not self.env["ir.ui.menu"].search_count([("id", "child_of", tab.id), ("action", "!=", False)]):
                    continue
                with self.subTest(role=role, tab=xmlid):
                    self.assertIn(tab.id, menus, f"{role} holds the group of {xmlid} but the tab is not shown")

    def test_reused_tab_names(self):
        # the two reused containers are renamed to their workflow step
        expected = {
            "cmc_base.menu_cmc_operations": "Product Costing",
            "cmc_base.menu_cmc_responsibility": "Performance",
        }
        for xmlid, name in expected.items():
            with self.subTest(menu=xmlid):
                self.assertEqual(self._ref(xmlid).with_context(lang="en_US").name, name)


@tagged("post_install", "-at_install")
class TestRenamedMenuTranslations(TransactionCase):
    """RN-001: on upgrade the .po does not overwrite existing translations, so the post-migrate
    scripts force them on the renamed menus only."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("it_IT")
        cls.operations = cls.env.ref("cmc_base.menu_cmc_operations")
        cls.cost_center = cls.env.ref("cmc_base.menu_cmc_cost_center")

    def _it_name(self, menu):
        menu.invalidate_recordset(["name"])
        return menu.with_context(lang="it_IT").name

    def test_renamed_menu_gets_new_translation(self):
        # an existing database still holds the label of the old "Operations" tab
        self.operations.with_context(lang="it_IT").name = "Operazioni"
        reload_menu_translations(self.env.cr, "cmc_base", ["menu_cmc_operations"])
        self.assertEqual(self._it_name(self.operations), "Calcolo costi")

    def test_other_menus_keep_customised_translation(self):
        # only the listed menus are overwritten: a customer's own wording elsewhere survives
        self.cost_center.with_context(lang="it_IT").name = "Centri (personalizzato)"
        reload_menu_translations(self.env.cr, "cmc_base", ["menu_cmc_operations"])
        self.assertEqual(self._it_name(self.cost_center), "Centri (personalizzato)")
