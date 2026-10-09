from collections import defaultdict

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.fields import Domain
from odoo.tools import float_is_zero

from .cmc_utils import month_range, month_start


class CmcDataCheck(models.AbstractModel):
    _name = "cmc.data.check"
    _description = "CMC data checks"

    @api.model
    def _cmc_check_driver_volumes(self, company, date_from, date_to, tolerance=0.05):
        """Drivers whose typed service volume differs from the actual facts by more than `tolerance`."""
        # the gate is the access of the data audited
        self.env["cmc.measure.quantity"].check_access("read")
        # why sudo: totals of the whole company, whatever the caller's rules show
        env = self.sudo().env
        typed = defaultdict(float, env["cmc.center.service"]._read_group(
            Domain("company_id", "in", [False, company.id]), ["driver_id"], ["quantity:sum"],
        ))
        system = env["cmc.measure.quantity"]._read_group(
            Domain("version_id", "=", False) & Domain("company_id", "=", company.id)
            & Domain("date", ">=", month_start(date_from)) & Domain("date", "<=", date_to),
            ["driver_id"],
            ["quantity:sum"],
        )
        return [
            {"driver": driver, "typed": typed[driver], "system": quantity, "difference": typed[driver] - quantity}
            for driver, quantity in system
            if quantity and abs(typed[driver] - quantity) / abs(quantity) > tolerance
        ]

    @api.model
    def _cmc_check_prior_period(self, company, date_from, date_to, threshold=0.5):
        """CMC centers whose cost moves by more than `threshold` vs the prior span of equal length."""
        self.env["cmc.measure.quantity"].check_access("read")
        # why sudo: costs of the whole company, whatever the caller's rules show
        centers = self.env["account.analytic.account"].sudo().search([
            ("company_id", "in", [False, company.id]),
            "|", ("cmc_center_type", "!=", False), ("cmc_is_period", "=", True),
        ])
        start = month_start(date_from)
        prior_start = start - relativedelta(months=len(month_range(date_from, date_to)))
        current = centers._cmc_cost_groups(start, date_to, company=company)
        prior = centers._cmc_cost_groups(prior_start, start - relativedelta(days=1), company=company)
        rounding = company.currency_id.rounding
        warnings = []
        for center in centers:
            cur, pri = current.get(center.id, 0.0), prior.get(center.id, 0.0)
            if float_is_zero(pri, precision_rounding=rounding):
                change = False
                if float_is_zero(cur, precision_rounding=rounding):
                    continue
            else:
                change = (cur - pri) / pri
                if abs(change) <= threshold:
                    continue
            warnings.append({"center": center, "current": cur, "prior": pri, "change": change})
        return warnings


class CmcDataCheckWizard(models.TransientModel):
    _name = "cmc.data.check.wizard"
    _description = "Data Check"

    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    tolerance = fields.Float(default=0.05, help="Relative gap between typed and system driver volumes.")
    threshold = fields.Float(default=0.5, help="Relative change of a center cost vs the prior period.")
    line_ids = fields.One2many("cmc.data.check.wizard.line", "wizard_id")

    def action_run(self):
        self.ensure_one()
        Check = self.env["cmc.data.check"]
        lines = [
            {
                "check_type": "driver_volume", "driver_id": w["driver"].id, "value_current": w["typed"],
                "value_reference": w["system"], "difference": w["difference"], "warning": True,
                "message": self.env._("Typed volume differs from the system volume."),
            }
            for w in Check._cmc_check_driver_volumes(self.company_id, self.date_from, self.date_to, self.tolerance)
        ] + [
            {
                "check_type": "prior_period", "center_id": w["center"].id, "value_current": w["current"],
                "value_reference": w["prior"], "difference": w["current"] - w["prior"],
                "change_pct": (w["change"] or 0.0) * 100, "warning": True,
                "message": self.env._("Cost moved more than the threshold vs the prior period."),
            }
            for w in Check._cmc_check_prior_period(self.company_id, self.date_from, self.date_to, self.threshold)
        ]
        share = self.env["cmc.allocation"]._cmc_allocated_share(self.company_id, self.date_from, self.date_to)
        lines.append({
            "check_type": "allocated_share", "value_current": share["allocated"], "value_reference": share["total_overhead"],
            "change_pct": share["share"], "warning": share["warning"],
            "message": self.env._("Share of the indirect cost spread by convention (guardrail 10%).") if share["warning"] else "",
        })
        self.line_ids = [fields.Command.clear()] + [fields.Command.create(vals) for vals in lines]
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }


class CmcDataCheckWizardLine(models.TransientModel):
    _name = "cmc.data.check.wizard.line"
    _description = "Data Check Result"

    wizard_id = fields.Many2one("cmc.data.check.wizard", required=True, ondelete="cascade")
    check_type = fields.Selection(
        selection=[("driver_volume", "Driver volume"), ("prior_period", "Prior period"), ("allocated_share", "Allocated share")],
        required=True,
    )
    driver_id = fields.Many2one("cmc.cost.driver")
    center_id = fields.Many2one("account.analytic.account")
    value_current = fields.Float()
    value_reference = fields.Float()
    difference = fields.Float()
    change_pct = fields.Float(string="Change %")
    warning = fields.Boolean()
    message = fields.Char()
