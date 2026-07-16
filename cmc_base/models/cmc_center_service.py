from odoo import api, fields, models
from odoo.exceptions import ValidationError


class CmcCenterService(models.Model):
    _name = "cmc.center.service"
    _description = "Inter-Center Service (provider -> receiver)"
    _check_company_auto = True
    _check_company_domain = models.check_company_domain_parent_of

    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True
    )
    provider_id = fields.Many2one(
        "account.analytic.account",
        string="Provider Center",
        required=True,
        check_company=True,
        index=True,
        domain="[('cmc_center_type', 'in', ('auxiliary', 'intermediate'))]",
    )
    receiver_id = fields.Many2one(
        "account.analytic.account",
        string="Receiver Center",
        required=True,
        check_company=True,
        index=True,
        domain="[('cmc_is_period', '=', False)]",
    )

    @api.constrains("receiver_id")
    def _check_receiver_not_period(self):
        for svc in self:
            if svc.receiver_id.cmc_is_period:
                raise ValidationError(
                    self.env._(
                        "A period cost center cannot receive a service: the "
                        "reallocated cost would be lost (it is outside the closing)."
                    )
                )
    driver_id = fields.Many2one("cmc.cost.driver", required=True, check_company=True)
    quantity = fields.Float(string="Units of Service", required=True)

    _provider_receiver_uniq = models.Constraint(
        "UNIQUE(provider_id, receiver_id, driver_id, company_id)",
        "A service line for this provider/receiver/driver already exists.",
    )
    _no_self_service = models.Constraint(
        "CHECK(provider_id != receiver_id)",
        "A cost center cannot provide a service to itself.",
    )
