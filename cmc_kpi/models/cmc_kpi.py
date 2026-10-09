from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools.safe_eval import test_python_expr

PERSPECTIVES = [
    ("financial", "Financial"),
    ("customer", "Customer"),
    ("internal", "Internal Process"),
    ("learning", "Learning and Growth"),
]
# the source field each type needs, checked by _check_source_fields
SOURCE_FIELDS = {
    "measure": ("driver_id",),
    "ratio": ("ratio_code",),
    "variance": ("variance_version_id", "center_id"),
    "formula": ("formula",),
    "hook": ("hook_key",),
    "cascade": (),
}


class CmcKpi(models.Model):
    _name = "cmc.kpi"
    _description = "KPI"
    _order = "sequence, code"
    _check_company_auto = True

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True, help="Name of the KPI in the formulas: a Python identifier.")
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    description = fields.Text()
    unit = fields.Selection(
        [("number", "Number"), ("percent", "Percent"), ("amount", "Amount"), ("days", "Days")], required=True, default="number"
    )
    perspective = fields.Selection(PERSPECTIVES, required=True, default="financial")
    direction = fields.Selection(
        [("higher", "Higher is better"), ("lower", "Lower is better")], required=True, default="higher"
    )
    target_value = fields.Float(help="Static target, used when the run version has none for the month.")
    amber_tolerance_pct = fields.Float(string="Amber Tolerance %", help="Gap beyond which the light turns amber.")
    red_tolerance_pct = fields.Float(string="Red Tolerance %", default=10.0, help="Gap beyond which the light turns red.")
    responsibility_id = fields.Many2one("cmc.responsibility.center", string="Responsibility Center", check_company=True)
    owner_id = fields.Many2one(related="responsibility_id.owner_id", string="Owner")
    parent_id = fields.Many2one("cmc.kpi", string="Parent KPI", check_company=True, index=True)
    child_ids = fields.One2many("cmc.kpi", "parent_id", string="Child KPIs")
    cascade_weight = fields.Float(help="Weight (percent) of this KPI in its parent.")
    activity_ref = fields.Reference(selection="_selection_activity_ref", string="Activity")
    target_ids = fields.One2many("cmc.kpi.target", "kpi_id", string="Targets")
    source_type = fields.Selection(
        [
            ("measure", "Measure"), ("ratio", "Financial ratio"), ("variance", "Budget variance"),
            ("formula", "Formula"), ("hook", "Engine"), ("cascade", "Cascade of child KPIs"),
        ],
        required=True,
    )
    driver_id = fields.Many2one("cmc.cost.driver", string="Measure", check_company=True)
    aggregation = fields.Selection([("sum", "Sum"), ("avg", "Average")], required=True, default="sum")
    center_id = fields.Many2one("account.analytic.account", string="Cost Center", check_company=True)
    cost_object_id = fields.Many2one("cmc.cost.object", check_company=True)
    denominator_driver_id = fields.Many2one("cmc.cost.driver", string="Divided By", check_company=True)
    scale = fields.Float(default=1.0, help="Multiplier of the value (100 for a percentage of two measures).")
    ratio_code = fields.Char(help="Code of a financial analysis ratio.")
    variance_version_id = fields.Many2one(
        "cmc.plan.version", string="Budget Version", check_company=True, domain=[("version_type", "in", ("budget", "forecast"))]
    )
    element_id = fields.Many2one("cmc.cost.element", string="Cost Element", check_company=True)
    variance_basis = fields.Selection([("month", "Month"), ("ytd", "Year to date")], default="month")
    formula = fields.Char(help="Python expression over the codes of the other KPIs, e.g. good_qty / (good_qty + scrap_qty) * 100.")
    hook_key = fields.Char(help="Key of the engine evaluator registered in cmc.kpi.run._cmc_kpi_evaluators.")

    _code_company_uniq = models.UniqueIndex("(company_id, code)", "A KPI code is unique in a company.")

    @api.model
    def _selection_activity_ref(self):
        # why: the link exists only when cmc_abc is installed; cmc_kpi does not depend on it
        return [("cmc.abc.activity", self.env._("Activity"))] if "cmc.abc.activity" in self.env else []

    @api.constrains("code")
    def _check_code(self):
        for kpi in self:
            if not kpi.code.isidentifier():
                raise ValidationError(self.env._("The code %s is not a valid identifier: it is the name of the KPI in the formulas.", kpi.code))

    @api.constrains("amber_tolerance_pct", "red_tolerance_pct")
    def _check_tolerances(self):
        for kpi in self:
            if kpi.red_tolerance_pct < kpi.amber_tolerance_pct:
                raise ValidationError(self.env._("%s: the red tolerance cannot be below the amber one.", kpi.code))

    @api.constrains("parent_id")
    def _check_parent(self):
        if self._has_cycle():
            raise ValidationError(self.env._("A KPI cannot be its own ancestor."))

    @api.constrains("source_type", *{name for names in SOURCE_FIELDS.values() for name in names})
    def _check_source_fields(self):
        for kpi in self:
            missing = [kpi._fields[name].string for name in SOURCE_FIELDS[kpi.source_type] if not kpi[name]]
            if missing:
                raise ValidationError(self.env._("%(code)s needs: %(fields)s", code=kpi.code, fields=", ".join(missing)))
            if kpi.source_type == "formula" and (error := test_python_expr(expr=kpi.formula.strip(), mode="eval")):
                raise ValidationError(self.env._("%(code)s: invalid formula: %(error)s", code=kpi.code, error=error))
