from odoo import api, fields, models
from odoo.exceptions import ValidationError


class CmcFinOverride(models.Model):
    _name = "cmc.fin.override"
    _description = "Financial Analysis Choice (override)"
    _order = "year desc, id"
    _check_company_auto = True

    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    scheme_id = fields.Many2one("cmc.fin.scheme", required=True, ondelete="cascade", check_company=True)
    year = fields.Integer(required=True, aggregator=False)
    account_id = fields.Many2one("account.account", required=True, check_company=True, ondelete="cascade")
    line_id = fields.Many2one("cmc.fin.scheme.line", required=True, ondelete="cascade", check_company=True)
    amount = fields.Float(help="Absolute part of the account balance moved; 0 moves the whole balance.")
    reason = fields.Text(required=True)

    @api.constrains("reason", "scheme_id", "line_id")
    def _check_reason_and_target(self):
        for override in self:
            if not (override.reason or "").strip():
                raise ValidationError(self.env._("A choice needs a reason."))
            if override.line_id.scheme_id != override.scheme_id or override.line_id.line_type != "account":
                raise ValidationError(self.env._("The target must be an account line of the scheme."))


class CmcFinHistory(models.Model):
    _name = "cmc.fin.history"
    _description = "Financial Analysis Historical Balance"
    _order = "year desc, account_id"
    _check_company_auto = True

    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    year = fields.Integer(required=True, aggregator=False)
    account_id = fields.Many2one("account.account", required=True, check_company=True, ondelete="cascade")
    balance = fields.Float(help="Natural sign (debit positive), pre-closing trial balance of the year.")

    _unique_account_year = models.UniqueIndex("(company_id, year, account_id)", "One balance per account and year.")


class CmcFinInput(models.Model):
    _name = "cmc.fin.input"
    _description = "Financial Analysis Reference Input"
    _order = "year desc, code"
    _check_company_auto = True

    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    year = fields.Integer(required=True, aggregator=False)
    code = fields.Char(required=True)
    value = fields.Float()

    _unique_code_year = models.UniqueIndex("(company_id, year, code)", "One value per code and year.")


class CmcFinBenchmark(models.Model):
    _name = "cmc.fin.benchmark"
    _description = "Financial Analysis Sector Benchmark"
    _order = "year desc, ateco_code"
    _check_company_auto = True

    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, index=True)
    ateco_code = fields.Char(required=True)
    year = fields.Integer(required=True, aggregator=False)
    ratio_id = fields.Many2one("cmc.fin.ratio", required=True, ondelete="cascade", check_company=True)
    value = fields.Float(aggregator="avg")
