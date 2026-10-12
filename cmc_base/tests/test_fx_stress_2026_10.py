"""Stress test 2026-10 (corsia A, cmc_base) - regression tests of the FX defects found at screen.

FX-001  the Controlling Manager (NOT system admin) opens Configuration > Attributes and saves an attribute
        that applies to a document type, through the same web_read / web_save calls of the web client.
FX-002  DL-001: a cost entry typed in the "Cost Entries" menu (the action context marks it as manual)
        is refused without cost center or behaviour; bridge lines (no such context) are unaffected.
FX-003  DL-001: the "Cost Entries" action no longer hides lines without behaviour; its search view
        offers a "to classify" filter that isolates them.
FX-006c an approved plan version refuses edits with a message about the VERSION being approved,
        not the generic "Run ... is computed".
FX-007  DL-003: every "Reset to draft" button (action_reset_to_draft) of every installed cmc_* view
        asks for confirmation. Red until lanes A/B/C add the missing confirm= (intended).
FX-008  a responsible-only user enters the app without access errors and is not offered Archive.
FX-017  center type labels of closing / cost analysis lines are translated like the center's own field.
FX-018  the ROI wizard keeps its dialog title after Compute (returned action has a name).
RN-001  the cost base (cockpit KPI/chart, "Cost Entries" action) excludes revenue lines (amount > 0, no behaviour).
"""

from datetime import date

from lxml import etree

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase, new_test_user, tagged
from odoo.tools import formatLang
from odoo.tools.safe_eval import safe_eval


def _plan_account_id(env):
    return env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
        lambda p: p._column_name() == "account_id"
    )[:1]


def _spec(fields_desc):
    """web_read specification as the web client builds it: relational fields read their display_name."""
    return {
        fname: ({"fields": {"display_name": {}}} if desc["type"] in ("many2one", "many2many", "one2many") else {})
        for fname, desc in fields_desc.items()
    }


# ---------------------------------------------------------------------------------------------------------
# FX-001
# ---------------------------------------------------------------------------------------------------------


@tagged("post_install", "-at_install")
class TestFx001AttributeManager(TransactionCase):
    """FX-001 / AD-115: the manager configures the suite master data, attributes included."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = new_test_user(
            cls.env, login="fx001_manager", groups="base.group_user,cmc_base.group_cmc_manager"
        )
        cls.Attr = cls.env["cmc.attribute"].with_user(cls.manager)
        cls.action = cls.env.ref("cmc_base.action_cmc_attribute")

    def _views(self):
        return self.Attr.get_views([(False, "list"), (False, "form"), (False, "search")])

    def _target_field(self):
        # the "Applies To" choice, whatever its technical shape (selection, char, own model...)
        fields_desc = self.Attr.fields_get()
        names = [fname for fname, desc in fields_desc.items() if desc.get("string") == "Applies To"]
        self.assertEqual(len(names), 1, "cmc.attribute must expose exactly one 'Applies To' field")
        return names[0], fields_desc[names[0]]

    def _target_value(self, desc, model_name):
        """The value the UI would send to make the attribute apply to ``model_name``."""
        if desc["type"] in ("many2many", "one2many", "many2one"):
            Comodel = self.env[desc["relation"]].with_user(self.manager)
            key = next(f for f in ("model", "code", "technical_name", "name") if f in Comodel._fields)
            target = Comodel.search([(key, "=", model_name)], limit=1)
            self.assertTrue(target, f"the manager must be able to pick {model_name} in 'Applies To'")
            if desc["type"] == "many2one":
                return target.id
            return [(6, 0, target.ids)]
        # selection / char (single or multi)
        return model_name

    def test_fx001_manager_opens_attribute_list_and_form(self):
        # FX-001 AC: as manager (no system rights) list and form read without AccessError
        views = self._views()
        fields_desc = views["models"]["cmc.attribute"]["fields"]
        spec = _spec(fields_desc)
        shipped = self.env.ref("cmc_base.cmc_attribute_value_added")
        rows = self.Attr.web_search_read([("id", "=", shipped.id)], spec)
        self.assertEqual(rows["length"], 1)
        record = self.Attr.browse(shipped.id).web_read(spec)
        self.assertEqual(record[0]["id"], shipped.id)

    def test_fx001_manager_creates_attribute_choosing_target(self):
        # FX-001 AC: the manager chooses the documents an attribute applies to and the save succeeds
        fname, desc = self._target_field()
        spec = _spec(self._views()["models"]["cmc.attribute"]["fields"])
        saved = self.Attr.web_save(
            {
                "name": "FX001 Region",
                "code": "FX001_REGION",
                fname: self._target_value(desc, "res.partner"),
                "value_ids": [(0, 0, {"name": "North", "code": "N"})],
            },
            spec,
        )
        attribute = self.env["cmc.attribute"].browse(saved[0]["id"])
        value = attribute.value_ids
        # the choice is effective: the value goes on a partner, not on a cost object
        # why: a CMC manager has no create right on res.partner; the point is that the choice is applied
        partner = self.env["res.partner"].create(
            {"name": "FX001 partner", "cmc_attribute_value_ids": [(6, 0, value.ids)]}
        )
        self.assertEqual(partner._cmc_attribute_value(attribute), value)
        cost_object = self.env["cmc.cost.object"]._cmc_ensure_objects(partner)
        with self.assertRaises(ValidationError):
            cost_object.cmc_attribute_value_ids = [(6, 0, value.ids)]


# ---------------------------------------------------------------------------------------------------------
# FX-002 / FX-003 (DL-001)
# ---------------------------------------------------------------------------------------------------------


@tagged("post_install", "-at_install")
class TestFx002CostEntryMandatory(TransactionCase):
    """FX-002 / FX-003 / DL-001 / RF-UX-002: a manual cost entry needs center and behaviour; bridges unchanged."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login="fx002_user", groups="base.group_user,cmc_base.group_cmc_user")
        cls.action = cls.env.ref("cmc_base.action_cmc_analytic_lines")
        cls.center = cls.env["account.analytic.account"].create(
            {"name": "FX002 center", "plan_id": _plan_account_id(cls.env).id, "cmc_center_type": "final"}
        )
        cls.AL = cls.env["account.analytic.line"]

    def _eval_ctx(self):
        return {
            "uid": self.user.id,
            "active_id": False,
            "active_ids": [],
            "active_model": False,
            "allowed_company_ids": self.env.companies.ids,
            "context_today": date.today,
        }

    def _action_context(self):
        return safe_eval(self.action.context or "{}", self._eval_ctx())

    def _manual(self):
        """The model as the "Cost Entries" menu calls it: the user, with the action context."""
        return self.AL.with_user(self.user).with_context(**self._action_context())

    def _action_search(self, extra=None):
        domain = safe_eval(self.action.domain or "[]", self._eval_ctx()) + (extra or [])
        return self.AL.with_user(self.user).with_context(**self._action_context()).search(domain)

    def test_fx002_manual_entry_without_center_refused(self):
        # FX-002 / DL-001: no cost center -> refused, nothing recorded
        with self.assertRaises(UserError):
            self._manual().create({"name": "FX002 no center", "cmc_cost_amount": 100.0, "cmc_behavior": "fixed"})
        self.assertFalse(self.AL.search([("name", "=", "FX002 no center")]))

    def test_fx002_manual_entry_without_behavior_refused(self):
        # FX-003 / DL-001 (a): no cost behaviour -> refused, nothing recorded
        with self.assertRaises(UserError):
            self._manual().create({"name": "FX002 no behavior", "cmc_cost_amount": 50.0, "cmc_center_id": self.center.id})
        self.assertFalse(self.AL.search([("name", "=", "FX002 no behavior")]))

    def test_fx002_manual_entry_complete_is_saved_and_listed(self):
        # FX-003 AC: a complete manual entry is saved and found by the menu's list
        line = self._manual().create(
            {"name": "FX002 complete", "cmc_cost_amount": 70.0, "cmc_center_id": self.center.id, "cmc_behavior": "fixed"}
        )
        self.assertIn(line, self._action_search([("name", "=", "FX002 complete")]))

    def test_fx002_form_marks_center_and_behavior_required(self):
        # FX-002 AC: the form highlights the center (and behaviour) as mandatory
        view = self.AL.with_user(self.user).get_view(self.env.ref("cmc_base.view_cmc_cost_entry_form").id, "form")
        arch = etree.fromstring(view["arch"])
        for fname in ("cmc_center_id", "cmc_behavior"):
            nodes = arch.xpath(f"//field[@name='{fname}']")
            self.assertTrue(nodes, fname)
            self.assertNotIn(nodes[0].get("required"), (None, "", "0", "False", "false"), f"{fname} must be required")

    def test_fx002_bridge_line_without_classification_unaffected(self):
        # DL-001: lines from the bridges (no manual context) keep their own classification rules
        line = self.AL.create({"name": "FX002 bridge", "account_id": self.center.id, "amount": -30.0})
        self.assertTrue(line.exists())
        self.assertFalse(line.cmc_behavior)

    def test_fx003_action_shows_unclassified_lines(self):
        # FX-003 / DL-001: the menu no longer hides lines without behaviour
        line = self.AL.create({"name": "FX003 unclassified", "account_id": self.center.id, "amount": -40.0})
        self.assertIn(line, self._action_search([("name", "=", "FX003 unclassified")]))

    def test_fx003_to_classify_filter_isolates_unclassified(self):
        # FX-003 / DL-001: a "to classify" filter of the menu's search view selects only unclassified lines
        unclassified = self.AL.create({"name": "FX003 todo", "account_id": self.center.id, "amount": -40.0})
        classified = self.AL.create(
            {"name": "FX003 done", "account_id": self.center.id, "amount": -40.0, "cmc_behavior": "fixed"}
        )
        both = unclassified | classified
        search_view_id = self.action.search_view_id.id or False
        arch = etree.fromstring(self.AL.with_user(self.user).get_view(search_view_id, "search")["arch"])
        matching = []
        for node in arch.xpath("//filter[@domain]"):
            try:
                domain = safe_eval(node.get("domain"), self._eval_ctx())
            except Exception:  # noqa: BLE001 - filters with client-only variables are not candidates
                continue
            if self._action_search(list(domain) + [("id", "in", both.ids)]) == unclassified:
                matching.append(node.get("name"))
        self.assertTrue(matching, "the Cost Entries search view must offer a 'to classify' filter")


# ---------------------------------------------------------------------------------------------------------
# FX-006c
# ---------------------------------------------------------------------------------------------------------


@tagged("post_install", "-at_install")
class TestFx006PlanVersionMessage(TransactionCase):
    """FX-006 (c) / RF-UX-010: the message names the object with its own type and says what to do."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = new_test_user(cls.env, login="fx006_manager", groups="base.group_user,cmc_base.group_cmc_manager")
        cls.driver = cls.env["cmc.cost.driver"].create({"name": "FX006 hours", "code": "FX006H"})
        cls.version = cls.env["cmc.plan.version"].create(
            {"name": "FX006 Budget", "date_from": date(2026, 1, 1), "date_to": date(2026, 12, 31)}
        )
        cls.fact = cls.env["cmc.measure.quantity"].create(
            {"driver_id": cls.driver.id, "date": date(2026, 3, 1), "quantity": 10.0, "version_id": cls.version.id}
        )
        cls.version.with_user(cls.manager).action_approve()

    def _assert_version_message(self, message):
        lowered = message.lower()
        self.assertIn("FX006 Budget", message)
        self.assertIn("approved", lowered, message)
        self.assertNotIn("is computed", lowered, message)
        self.assertFalse(message.startswith("Run "), message)

    def test_fx006c_approved_version_header_message(self):
        # FX-006 (c): editing an approved version speaks of the version, not of a computed run
        with self.assertRaises(UserError) as caught:
            self.version.with_user(self.manager).with_context(lang="en_US").write({"date_to": date(2026, 6, 30)})
        self._assert_version_message(str(caught.exception))

    def test_fx006c_approved_version_input_line_message(self):
        # FX-006 (c): editing a planned figure of an approved version says the same
        with self.assertRaises(UserError) as caught:
            self.fact.with_user(self.manager).with_context(lang="en_US").write({"quantity": 11.0})
        self._assert_version_message(str(caught.exception))


# ---------------------------------------------------------------------------------------------------------
# FX-007 (DL-003)
# ---------------------------------------------------------------------------------------------------------


@tagged("post_install", "-at_install")
class TestFx007ResetConfirm(TransactionCase):
    """FX-007 / DL-003 / AD-001: every reset that wipes results asks the same confirmation, suite-wide."""

    def test_fx007_every_reset_to_draft_button_has_confirm(self):
        # DL-003: scans the combined arch of every view touched by an installed cmc_* module
        imd = self.env["ir.model.data"].search([("model", "=", "ir.ui.view"), ("module", "=like", "cmc\\_%")])
        views = self.env["ir.ui.view"].browse(imd.mapped("res_id")).exists()
        roots = self.env["ir.ui.view"]
        for view in views.filtered(lambda v: "action_reset_to_draft" in (v.arch_db or "")):
            root = view
            while root.mode != "primary" and root.inherit_id:
                root = root.inherit_id
            roots |= root
        self.assertTrue(roots, "no cmc view with a reset button found: the scan is broken")
        missing = []
        for root in roots:
            arch = etree.fromstring(self.env[root.model].get_view(root.id, root.type)["arch"])
            for button in arch.xpath("//button[@name='action_reset_to_draft']"):
                if not button.get("confirm"):
                    missing.append(f"{root.xml_id or root.name} ({root.model})")
        self.assertFalse(missing, "reset buttons without confirm: " + ", ".join(sorted(set(missing))))


# ---------------------------------------------------------------------------------------------------------
# FX-008
# ---------------------------------------------------------------------------------------------------------


@tagged("post_install", "-at_install")
class TestFx008ResponsibleEntry(TransactionCase):
    """FX-008 / AD-115 / RF-UX-010: the read-only responsible enters the app without access errors."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.responsible = new_test_user(
            cls.env, login="fx008_resp", groups="base.group_user,cmc_base.group_cmc_responsible"
        )
        center = cls.env["account.analytic.account"].create(
            {"name": "FX008 center", "plan_id": _plan_account_id(cls.env).id, "cmc_center_type": "final"}
        )
        cls.resp_center = cls.env["cmc.responsibility.center"].create(
            {"name": "FX008 own", "owner_id": cls.responsible.id, "center_ids": [(6, 0, center.ids)]}
        )

    def test_fx008_cockpit_data_as_responsible(self):
        # FX-008 AC: the cockpit payload builds for a responsible without AccessError
        data = self.env["cmc.cockpit"].with_user(self.responsible).get_cockpit_data()
        self.assertIn("pipeline", data)

    def test_fx008_app_landing_action_readable(self):
        # FX-008 AC: the first screen the web client opens for the app is one the role can read
        menus = self.env["ir.ui.menu"].with_user(self.responsible).load_menus(False)
        root = self.env.ref("cmc_base.menu_cmc_root")
        self.assertIn(root.id, menus, "the responsible must see the app")

        def first_action(menu_id):
            menu = menus[menu_id]
            if menu.get("action_id"):
                return menu
            for child in menu["children"]:
                if found := first_action(child):
                    return found
            return None

        landing = first_action(root.id)
        self.assertTrue(landing, "the app must open on some action")
        action = self.env[landing["action_model"]].browse(landing["action_id"])
        if action._name == "ir.actions.act_window":
            self.assertTrue(self.env[action.res_model].with_user(self.responsible).has_access("read"), action.name)
            self.env[action.res_model].with_user(self.responsible).search([], limit=1)
        elif action._name == "ir.actions.client" and action.tag == "cmc_cockpit":
            self.env["cmc.cockpit"].with_user(self.responsible).get_cockpit_data()

    def test_fx008_responsible_cannot_archive(self):
        # FX-008 / AD-115: read-only role, archiving is refused
        with self.assertRaises(AccessError):
            self.resp_center.with_user(self.responsible).action_archive()
        self.assertTrue(self.resp_center.active)

    def test_fx008_archive_not_offered_to_responsible(self):
        # FX-008 AC: no edit action is proposed - the web client offers Archive when the views' "active"
        # field description is writable (list_controller / form_controller archiveEnabled)
        action = self.env.ref("cmc_base.action_cmc_responsibility_center")
        Resp = self.env["cmc.responsibility.center"].with_user(self.responsible)
        views = [(v.view_id.id, v.view_mode) for v in action.view_ids] or []
        modes = {mode for _vid, mode in views}
        views += [(False, mode) for mode in ("list", "form") if mode not in modes]
        views.append((action.search_view_id.id or False, "search"))
        result = Resp.get_views(views)
        fields_desc = result["models"]["cmc.responsibility.center"]["fields"]
        archive_enabled = "active" in fields_desc and not fields_desc["active"].get("readonly")
        self.assertFalse(archive_enabled, "Archive must not be offered to a read-only responsible")
        for mode in ("list", "form"):
            arch = etree.fromstring(result["views"][mode]["arch"])
            for attr in ("create", "edit", "delete"):
                self.assertIn(arch.get(attr), ("0", "False", "false"), f"{mode} {attr}")


# ---------------------------------------------------------------------------------------------------------
# FX-017
# ---------------------------------------------------------------------------------------------------------


@tagged("post_install", "-at_install")
class TestFx017CenterTypeLabels(TransactionCase):
    """FX-017: with an Italian user no center type label is in English."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("it_IT")

    def _labels(self, model, fname, lang):
        return dict(self.env[model].with_context(lang=lang).fields_get([fname])[fname]["selection"])

    def test_fx017_reference_translation_present(self):
        # guard: the center's own field IS translated, otherwise the tests below prove nothing
        it = self._labels("account.analytic.account", "cmc_center_type", "it_IT")
        en = self._labels("account.analytic.account", "cmc_center_type", "en_US")
        self.assertNotEqual(it["final"], en["final"])

    def test_fx017_closing_line_center_type_translated(self):
        # FX-017 AC: closing lines show the center type in the user's language
        expected = self._labels("account.analytic.account", "cmc_center_type", "it_IT")
        self.assertEqual(self._labels("cmc.closing.line", "center_type", "it_IT"), expected)

    def test_fx017_cost_analysis_line_center_type_translated(self):
        # FX-017 AC: cost analysis lines show the center type in the user's language
        expected = self._labels("account.analytic.account", "cmc_center_type", "it_IT")
        self.assertEqual(self._labels("cmc.cost.analysis.line", "center_type", "it_IT"), expected)


# ---------------------------------------------------------------------------------------------------------
# FX-018
# ---------------------------------------------------------------------------------------------------------


@tagged("post_install", "-at_install")
class TestFx018RoiWizardTitle(TransactionCase):
    """FX-018: the "Controllable Cost and ROI" dialog keeps its title after Compute."""

    def test_fx018_compute_returns_named_action(self):
        # FX-018 AC: an unnamed window action makes the dialog header fall back to "Odoo"
        manager = new_test_user(self.env, login="fx018_manager", groups="base.group_user,cmc_base.group_cmc_manager")
        wizard = self.env["cmc.responsibility.summary"].with_user(manager).create(
            {"date_from": date(2026, 1, 1), "date_to": date(2026, 12, 31)}
        )
        action = wizard.action_compute()
        self.assertTrue(action.get("name"), "the returned action must carry the dialog title")
        self.assertNotEqual(action.get("name"), "Odoo")


# ---------------------------------------------------------------------------------------------------------
# RN-001 - the cost base excludes revenue
# ---------------------------------------------------------------------------------------------------------


@tagged("post_install", "-at_install")
class TestRn001CostBaseExcludesRevenue(TransactionCase):
    """RN-001 / FX-003 / DL-001: once the action no longer filters on behaviour, revenue must not leak in.

    CONTRACT (binding for the developer): the "cost base" is the analytic lines with ``cmc_behavior`` set OR
    ``amount < 0``, not ``cmc_excluded``, of the company. Both the cockpit KPI ``cost`` ("Booked Costs") and its
    by-element chart, and the domain of ``cmc_base.action_cmc_analytic_lines`` ("Cost Entries"), read that base:
    a revenue line (amount > 0, no behaviour, e.g. from an invoice) is never part of it; an unclassified cost
    (amount < 0, no behaviour = "To Classify") is.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "RN001 Co"})
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=cls.company.ids))
        cls.AL = cls.env["account.analytic.line"]
        cls.user = new_test_user(
            cls.env, login="rn001_user", groups="base.group_user,cmc_base.group_cmc_user",
            company_id=cls.company.id, company_ids=[(6, 0, cls.company.ids)],
        )
        cls.center = cls.env["account.analytic.account"].create({
            "name": "RN001 center", "plan_id": _plan_account_id(cls.env).id, "cmc_center_type": "final",
            "company_id": cls.company.id,
        })
        cls.element = cls.env["cmc.cost.element"].create(
            {"name": "RN001 element", "code": "RN001E", "company_id": cls.company.id}
        )
        # a classified cost: Cost 8000 -> analytic amount -8000
        cls.cost = cls.AL.create({
            "name": "RN001 cost", "account_id": cls.center.id, "amount": -8000.0, "cmc_behavior": "fixed",
            "cmc_cost_element_id": cls.element.id, "company_id": cls.company.id,
        })
        # a revenue on the same center/element, as a customer invoice writes it: positive, no behaviour, no manual context
        cls.revenue = cls.AL.create({
            "name": "RN001 revenue", "account_id": cls.center.id, "amount": 10000.0,
            "cmc_cost_element_id": cls.element.id, "company_id": cls.company.id,
        })
        # an unclassified cost: the "To Classify" case of FX-003, it IS part of the cost base
        cls.unclassified = cls.AL.create({
            "name": "RN001 to classify", "account_id": cls.center.id, "amount": -100.0, "company_id": cls.company.id,
        })

    def _cockpit(self):
        return self.env["cmc.cockpit"].with_user(self.user).with_company(self.company).get_cockpit_data()

    def test_rn001_cockpit_booked_costs_exclude_revenue(self):
        # RN-001: "Booked Costs" = 8000 + 100 to classify, not 8100 - 10000 revenue
        data = self._cockpit()
        kpi = next((k for k in data["kpis"] if k["key"] == "cost"), None)
        self.assertTrue(kpi, "the cmc user must see the Booked Costs KPI")
        expected = formatLang(self.env, 8100.0, currency_obj=self.company.currency_id)
        self.assertEqual(kpi["value"], expected)

    def test_rn001_cockpit_chart_excludes_revenue(self):
        # RN-001: the element slice shows the cost 8000, the revenue on the same element does not net it away
        chart = self._cockpit()["chart"]
        values = dict(zip(chart["labels"], chart["values"], strict=True))
        self.assertIn(self.element.display_name, values, "the element with 8000 of cost must have a slice")
        self.assertAlmostEqual(values[self.element.display_name], 8000.0)

    def _action_search(self):
        action = self.env.ref("cmc_base.action_cmc_analytic_lines")
        eval_ctx = {
            "uid": self.user.id, "active_id": False, "active_ids": [], "active_model": False,
            "allowed_company_ids": self.company.ids, "context_today": date.today,
        }
        domain = safe_eval(action.domain or "[]", eval_ctx)
        ctx = safe_eval(action.context or "{}", eval_ctx)
        lines = self.cost | self.revenue | self.unclassified
        return self.AL.with_user(self.user).with_company(self.company).with_context(**ctx).search(
            list(domain) + [("id", "in", lines.ids)]
        )

    def test_rn001_cost_entries_action_hides_revenue(self):
        # RN-001: the "Cost Entries" menu does not list revenue lines
        self.assertNotIn(self.revenue, self._action_search())

    def test_rn001_cost_entries_action_keeps_unclassified_cost(self):
        # RN-001 / FX-003: the domain that hides revenue still lists costs to classify (and classified ones)
        self.assertEqual(self._action_search(), self.cost | self.unclassified)
