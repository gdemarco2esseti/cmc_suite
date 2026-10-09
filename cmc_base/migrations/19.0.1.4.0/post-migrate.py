import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    # the ORM filled the new required column with today: an allocation is dated by its creation
    cr.execute("UPDATE cmc_allocation SET date = create_date::date")
    _logger.info("cmc_base: dated %d existing allocation(s) by creation (they stay in draft)", cr.rowcount)
    # the group block of cmc_security.xml is noupdate: an existing database never gets the new implied link
    user_group = env.ref("cmc_base.group_cmc_user")
    responsible = env.ref("cmc_base.group_cmc_responsible")
    if responsible not in user_group.implied_ids:
        user_group.implied_ids = [(4, responsible.id)]
        _logger.info("cmc_base: %s now implies %s", user_group.name, responsible.name)
