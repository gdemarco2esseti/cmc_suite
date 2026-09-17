from odoo import api, fields, models
from odoo.exceptions import ValidationError


class CmcClassificationRule(models.Model):
    _name = "cmc.classification.rule"
    _description = "CMC Cost Classification Rule"
    _order = "sequence, id"
    _check_company_auto = True

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10, index=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company, index=True
    )

    # --- match criteria (blank = wildcard; AND across the set ones) ---
    #
    # ondelete="cascade" on every criterion is deliberate. With the ORM default
    # ('set null') PostgreSQL blanks the column without the ORM noticing, so
    # _check_has_criterion -- an @api.constrains -- never re-fires and the rule
    # survives with every criterion empty. _matches() then returns True for
    # EVERY line: deleting or merging one account would silently reclassify the
    # whole cost base. A rule whose only criterion is gone has no reason to live.
    general_account_id = fields.Many2one(
        "account.account", string="Financial Account", ondelete="cascade"
    )
    account_code_prefix = fields.Char(
        help="Match if the financial account code starts with this prefix."
    )
    product_category_id = fields.Many2one("product.category", ondelete="cascade")
    partner_id = fields.Many2one("res.partner", ondelete="cascade")
    center_id = fields.Many2one(
        "account.analytic.account", string="Cost Center", check_company=True,
        ondelete="cascade",
    )

    # --- assignments (fill-only on the line, see _cmc_auto_classify) ---
    cmc_behavior = fields.Selection(
        [("fixed", "Fixed"), ("variable", "Variable"), ("semi_variable", "Semi-variable")]
    )
    cmc_directness = fields.Selection([("direct", "Direct"), ("indirect", "Indirect")])
    cmc_cost_element_id = fields.Many2one("cmc.cost.element")

    @api.constrains(
        "general_account_id", "account_code_prefix",
        "product_category_id", "partner_id", "center_id",
    )
    def _check_has_criterion(self):
        for rule in self:
            if not any((
                rule.general_account_id, rule.account_code_prefix,
                rule.product_category_id, rule.partner_id, rule.center_id,
            )):
                raise ValidationError(self.env._(
                    "Classification rule '%s' must set at least one match "
                    "criterion, otherwise it matches every cost line.",
                    rule.name,
                ))

    def _matches(self, line, center=None):
        """Pure-Python match. ``center`` may be precomputed by the caller to
        avoid re-evaluating the non-stored ``cmc_center_id`` per rule."""
        self.ensure_one()
        if self.general_account_id and self.general_account_id != line.general_account_id:
            return False
        if self.account_code_prefix and not (
            line.general_account_id.code or ""
        ).startswith(self.account_code_prefix):
            return False
        if (
            self.product_category_id
            and self.product_category_id != line.product_id.categ_id
        ):
            return False
        if self.partner_id and self.partner_id != line.partner_id:
            return False
        if self.center_id:
            if center is None:
                center = line.cmc_center_id
            if self.center_id != center:
                return False
        return True

    @api.model
    def _rules_for_company(self, company):
        """Rules applicable to ``company``: its own plus its parents' plus the
        company-less ones.

        No sudo. Classification rules are non-sensitive configuration and every
        internal user has a read-only ACL row on them, so the record rule in
        security/cmc_account_security.xml stays the single security boundary
        and cannot drift from a hand-written sudo domain. The domain below is a
        BUSINESS filter (only the rules of the line's own company may classify
        it), not a security one.
        """
        return self.with_company(company).search(
            ["|", ("company_id", "=", False), ("company_id", "parent_of", company.id)]
        )
