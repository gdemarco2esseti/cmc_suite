from odoo import fields, models


class CmcRunUsage(models.Model):
    """AD-002 register: which computed run (consumer) reads which origin (a run,
    a plan version). Written by `_cmc_record_usage` in sudo only."""

    _name = "cmc.run.usage"
    _description = "CMC run usage"

    consumer_model = fields.Char(required=True)
    consumer_id = fields.Many2oneReference(model_field="consumer_model", required=True, index=True)
    origin_model = fields.Char(required=True)
    origin_id = fields.Many2oneReference(model_field="origin_model", required=True, index=True)
    company_id = fields.Many2one("res.company", required=True, index=True)

    _usage_uniq = models.UniqueIndex(
        "(consumer_model, consumer_id, origin_model, origin_id)",
        "A run records each origin once.",
    )
