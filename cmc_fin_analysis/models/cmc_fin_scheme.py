from odoo import api, fields, models
from odoo.exceptions import ValidationError

from .cmc_fin_utils import check_expression


class CmcFinScheme(models.Model):
    _name = "cmc.fin.scheme"
    _description = "Financial Analysis Scheme"
    _order = "sequence, id"
    _check_company_auto = True

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True)
    scheme_type = fields.Selection(
        [("balance_sheet", "Balance sheet"), ("income_statement", "Income statement"), ("cash_flow", "Cash flow")],
        required=True,
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, index=True)
    base_code = fields.Char(help="Line code taken as 100% for the common-size percentages.")
    line_ids = fields.One2many("cmc.fin.scheme.line", "scheme_id", copy=True)
    rule_ids = fields.One2many("cmc.fin.mapping.rule", "scheme_id")

    _unique_code = models.UniqueIndex("(code, COALESCE(company_id, 0))", "The scheme code must be unique per company.")


class CmcFinSchemeLine(models.Model):
    _name = "cmc.fin.scheme.line"
    _description = "Financial Analysis Scheme Line"
    _order = "scheme_id, sequence, id"
    _check_company_auto = True

    scheme_id = fields.Many2one("cmc.fin.scheme", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="scheme_id.company_id", store=True, index=True)
    sequence = fields.Integer(default=10)
    code = fields.Char(required=True)
    name = fields.Char(required=True, translate=True)
    line_type = fields.Selection([("account", "Accounts"), ("formula", "Formula")], required=True, default="account")
    formula = fields.Char(help="Expression over the line codes, prev_<code>, typed inputs and ratio codes.")
    sign = fields.Integer(default=1, help="Presentation sign: value = sign x natural balance (debit positive).")
    result_line = fields.Boolean(help="Balance sheet line receiving the result not yet closed to equity.")

    _unique_code = models.Constraint("UNIQUE(scheme_id, code)", "The line code must be unique per scheme.")
    _check_sign = models.Constraint("CHECK(sign IN (1, -1))", "The sign must be 1 or -1.")

    @api.constrains("code", "line_type", "formula")
    def _check_code_and_formula(self):
        for line in self:
            if not (line.code or "").isidentifier():
                raise ValidationError(self.env._("The line code %s must be a valid identifier.", line.code))
            if line.line_type == "formula":
                check_expression(self.env, line.formula, line.code)


class CmcFinMappingRule(models.Model):
    _name = "cmc.fin.mapping.rule"
    _description = "Financial Analysis Mapping Rule"
    _order = "scheme_id, sequence, id"
    _check_company_auto = True

    scheme_id = fields.Many2one("cmc.fin.scheme", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, index=True)
    sequence = fields.Integer(default=10)
    chart_template = fields.Char(help="Chart of accounts the rule applies to (company chart_template); empty = every chart.")
    line_id = fields.Many2one("cmc.fin.scheme.line", required=True, ondelete="cascade", check_company=True)
    account_id = fields.Many2one("account.account", check_company=True, ondelete="cascade")
    account_code_prefix = fields.Char()
    account_type = fields.Selection(selection=lambda self: self.env["account.account"]._fields["account_type"].selection)
    cmc_behavior = fields.Selection([("fixed", "Fixed"), ("variable", "Variable"), ("semi_variable", "Semi-variable")])

    @api.constrains("scheme_id", "line_id", "account_id", "account_code_prefix", "account_type", "cmc_behavior")
    def _check_rule(self):
        for rule in self:
            if not (rule.account_id or rule.account_code_prefix or rule.account_type or rule.cmc_behavior):
                raise ValidationError(self.env._("A mapping rule needs at least one criterion."))
            if rule.line_id.scheme_id != rule.scheme_id or rule.line_id.line_type != "account":
                raise ValidationError(self.env._("The target of a mapping rule must be an account line of its own scheme."))
