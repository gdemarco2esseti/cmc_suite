"""Stress test 2026-10 (corsia B) - FX-018 (years, group totals) and FX-017 (it_IT selection) on cmc_fin_analysis.

CONTRACT (binding for the developer)
====================================
FX-018: every list / form / kanban view of the module showing a calendar-year Integer (``year``, ``year_from``,
  ``year_to`` of the ``cmc.fin.*`` models) sets ``options="{'enable_formatting': false}"`` ("2026", not "2.026");
  ``cmc.fin.ratio.value`` ``value`` and ``threshold`` have no aggregator (no group sum of ratios / thresholds).
FX-017: the callable selection ``cmc.fin.mapping.rule.account_type`` returns the translated labels of
  ``account.account.account_type``.
FX-019: a CMC user WITHOUT accounting groups opens an existing ``cmc.fin.analysis`` (``excluded_journal_ids`` filled
  by an accountant) through the web client calls (``get_views`` + ``web_read`` with the form field spec) without
  AccessError: the field is either hidden by ``groups`` (then absent from the spec) or readable for that user.
"""

from lxml import etree

from odoo import Command
from odoo.tests.common import TransactionCase, tagged
from odoo.tools.safe_eval import safe_eval

from .common import FinAnalysisCommon

YEAR_FIELDS = {"year", "year_from", "year_to"}
VIEW_TYPES = ("list", "form", "kanban")


def unformatted_year_offenders(env, module, year_fields):
    """[(view xmlid, model, field)] of calendar-year Integer fields shown with the thousands separator."""
    offenders = []
    views = env["ir.ui.view"].browse(
        env["ir.model.data"].search([("module", "=", module), ("model", "=", "ir.ui.view")]).mapped("res_id")
    )
    roots = env["ir.ui.view"]
    for view in views:
        root = view
        while root.mode == "extension" and root.inherit_id:
            root = root.inherit_id
        if root.type in VIEW_TYPES:
            roots |= root

    def walk(node, model, xmlid):
        for child in node:
            if not isinstance(child.tag, str):
                continue
            if child.tag == "field":
                field = model._fields.get(child.get("name"))
                if field and field.type == "integer" and field.name in year_fields.get(model._name, ()):
                    options = safe_eval(child.get("options") or "{}", {"false": False, "true": True})
                    if options.get("enable_formatting", True):
                        offenders.append((xmlid, model._name, field.name))
                if field and field.relational:
                    walk(child, env[field.comodel_name], xmlid)
                    continue
            walk(child, model, xmlid)

    for root in roots:
        arch = env[root.model].get_view(root.id, root.type)["arch"]
        walk(etree.fromstring(arch), env[root.model], root.xml_id)
    return offenders


@tagged("post_install", "-at_install")
class TestFinStressFixes(TransactionCase):
    def test_fx018_years_without_thousands_separator(self):
        # FX-018: "Anno da 2.025 / Anno a 2.026" and the year column of the ratio values read "2025", "2026"
        year_fields = {name: YEAR_FIELDS for name in self.env if name.startswith("cmc.fin.")}
        self.assertEqual(unformatted_year_offenders(self.env, "cmc_fin_analysis", year_fields), [])

    def test_fx018_ratio_values_and_thresholds_not_summed(self):
        # FX-018: grouped by area the ratio values must not show the sum of ratios and thresholds
        fields = self.env["cmc.fin.ratio.value"].fields_get(["value", "threshold"], ["aggregator"])
        self.assertFalse(fields["value"].get("aggregator"), fields)
        self.assertFalse(fields["threshold"].get("aggregator"), fields)

    def test_fx017_mapping_rule_account_type_translated(self):
        # FX-017: the callable selection carries the it_IT labels of the account types
        def selection(model, lang):
            return dict(self.env[model].with_context(lang=lang).fields_get(["account_type"])["account_type"]["selection"])

        account_it = selection("account.account", "it_IT")
        self.assertNotEqual(account_it, selection("account.account", "en_US"), "fixture: account has no it_IT labels")
        self.assertEqual(selection("cmc.fin.mapping.rule", "it_IT"), account_it)


def _web_spec(fields_desc):
    """web_read specification as the web client builds it: relational fields read their display_name."""
    return {
        fname: ({"fields": {"display_name": {}}} if desc["type"] in ("many2one", "many2many", "one2many") else {})
        for fname, desc in fields_desc.items()
    }


@tagged("post_install", "-at_install")
class TestFinFormNonAccountant(FinAnalysisCommon):
    """FX-019: the analysis form opens for a controller who has no accounting rights."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.run_record = cls._run(excluded_journal_ids=[(6, 0, cls.company_data["default_journal_misc"].ids)])

    def test_fx019_fixture_user_has_no_accounting_rights(self):
        # guard: the user is a pure CMC user, otherwise the test below proves nothing
        self.assertFalse(self.env["account.journal"].with_user(self.cmc_user).has_access("read"))

    def test_fx019_cmc_user_opens_analysis_form(self):
        # FX-019: get_views + web_read of the form as the web client does, no AccessError on the journals
        Analysis = self.env["cmc.fin.analysis"].with_user(self.cmc_user).with_company(self.company)
        views = Analysis.get_views([(self.env.ref("cmc_fin_analysis.view_cmc_fin_analysis_form").id, "form")])
        spec = _web_spec(views["models"]["cmc.fin.analysis"]["fields"])
        record = Analysis.browse(self.run_record.id).web_read(spec)
        self.assertEqual(record[0]["id"], self.run_record.id)


@tagged("post_install", "-at_install")
class TestFinCopyNonAccountant(FinAnalysisCommon):
    """RN-005 (ronin): a CMC user without accounting groups duplicates an analysis without losing its journal exclusions.

    CONTRACT: ``cmc.fin.analysis.with_user(cmc_user).copy()`` does not raise (no AccessError on the
    accounting-gated ``excluded_journal_ids``) and the copy carries the SAME ``excluded_journal_ids`` as the original
    (a duplicate silently re-including the year-end closing journal would change every figure of the copy).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.journals = cls.company_data["default_journal_misc"] | cls.company_data["default_journal_bank"]
        cls.run_record = cls._run(excluded_journal_ids=[Command.set(cls.journals.ids)])

    def test_rn005_fixture_user_has_no_accounting_rights(self):
        # guard: a pure CMC user, and the original really excludes journals
        self.assertFalse(self.env["account.journal"].with_user(self.cmc_user).has_access("read"))
        self.assertEqual(self.run_record.sudo().excluded_journal_ids, self.journals)

    def test_rn005_cmc_user_copies_analysis(self):
        # RN-005: "Duplicate" works for the controller without accounting rights
        copy = self.run_record.with_user(self.cmc_user).with_company(self.company).copy()
        self.assertTrue(copy)
        self.assertNotEqual(copy, self.run_record)

    def test_rn005_copy_keeps_excluded_journals(self):
        # RN-005: the duplicate keeps the journal exclusions it cannot even see
        copy = self.run_record.with_user(self.cmc_user).with_company(self.company).copy()
        self.assertEqual(copy.sudo().excluded_journal_ids, self.journals)
