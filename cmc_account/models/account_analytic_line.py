from odoo import api, fields, models

CLASSIFICATION_FIELDS = {"cmc_behavior", "cmc_directness", "cmc_cost_element_id"}


class AccountAnalyticLine(models.Model):
    _inherit = "account.analytic.line"

    cmc_classified_manually = fields.Boolean(
        string="Manually Classified",
        copy=False,
        help="Set when a user classifies the line by hand; rules never overwrite it.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        # a classification provided explicitly at create time = manual
        if not self.env.context.get("cmc_auto_classify"):
            for line, vals in zip(lines, vals_list):
                if vals.keys() & CLASSIFICATION_FIELDS:
                    line.cmc_classified_manually = True
        lines._cmc_auto_classify()
        return lines

    def write(self, vals):
        # a user write to classification fields marks the line as manual
        if (vals.keys() & CLASSIFICATION_FIELDS) and not self.env.context.get(
            "cmc_auto_classify"
        ):
            vals.setdefault("cmc_classified_manually", True)
        return super().write(vals)

    def _cmc_auto_classify(self):
        """Fill-only auto-classification: derive the element from the financial
        account mapping and apply the first matching rule. Never touches manually
        classified lines; never creates new lines. Runs the rule lookup as a
        system derivation (sudo) so any user posting a cost line can trigger it
        without read access to the CMC config models."""
        Rule = self.env["cmc.classification.rule"]
        rules_by_company = {}
        for line in self:
            if line.cmc_classified_manually:
                continue
            company = line.company_id or self.env.company
            if company.id not in rules_by_company:
                rules_by_company[company.id] = Rule._rules_for_company(company)
            vals = {}
            account = line.general_account_id.sudo()
            if not line.cmc_cost_element_id and account.cmc_cost_element_id:
                vals["cmc_cost_element_id"] = account.cmc_cost_element_id.id
            center = line.cmc_center_id  # non-stored compute: read once per line
            rule = next(
                (r for r in rules_by_company[company.id] if r._matches(line, center=center)),
                False,
            )
            if rule:
                if rule.cmc_behavior and not line.cmc_behavior:
                    vals["cmc_behavior"] = rule.cmc_behavior
                # fill-only: treat the bare 'indirect' default as "unset" so a
                # rule does not clobber a value a bridge deliberately set (MOD/MP
                # write 'direct' before this engine runs last in the MRO).
                if rule.cmc_directness and line.cmc_directness in (False, "indirect"):
                    vals["cmc_directness"] = rule.cmc_directness
                if (
                    rule.cmc_cost_element_id
                    and "cmc_cost_element_id" not in vals
                    and not line.cmc_cost_element_id
                ):
                    vals["cmc_cost_element_id"] = rule.cmc_cost_element_id.id
            if vals:
                line.with_context(cmc_auto_classify=True).write(vals)
        return True
