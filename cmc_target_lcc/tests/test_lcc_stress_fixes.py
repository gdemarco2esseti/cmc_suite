"""Stress test 2026-10 (corsia B) - FX-018 (calendar years) and FX-017 (it_IT statusbar) on cmc_target_lcc.

CONTRACT (binding for the developer)
====================================
FX-018: list / form / kanban views of the module showing a CALENDAR year (``cmc.lcc.model`` ``start_year``,
  ``breakeven_year``; ``cmc.lcc.year.result.calendar_year``) set ``options="{'enable_formatting': false}"``
  ("Anno di pareggio 2029", not "2.029"). Relative years (0 = first year) are out of scope.
FX-017: ``cmc.lcc.model.state`` reads "Bozza / Calcolato" in it_IT.
FX-007 (reset confirm) is covered by the suite-wide arch test of lane A, not here.
"""

from lxml import etree

from odoo.tests.common import TransactionCase, tagged
from odoo.tools.safe_eval import safe_eval

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


YEAR_FIELDS = {
    "cmc.lcc.model": {"start_year", "breakeven_year"},
    "cmc.lcc.year.result": {"calendar_year"},
}


@tagged("post_install", "-at_install")
class TestLccStressFixes(TransactionCase):
    def test_fx018_calendar_years_without_thousands_separator(self):
        # FX-018: LCC start and breakeven years read "2029", not "2.029"
        self.assertEqual(unformatted_year_offenders(self.env, "cmc_target_lcc", YEAR_FIELDS), [])

    def test_fx017_lcc_statusbar_italian(self):
        # FX-017: "Bozza / Calcolato" on the LCC model as on ABC and closing
        selection = dict(self.env["cmc.lcc.model"].with_context(lang="it_IT").fields_get(["state"])["state"]["selection"])
        self.assertEqual(selection, {"draft": "Bozza", "done": "Calcolato"})
