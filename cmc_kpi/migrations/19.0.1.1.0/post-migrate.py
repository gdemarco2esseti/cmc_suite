from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    kpis = env["cmc.kpi"].with_context(active_test=False).search([("source_type", "=", "ratio")])
    # why: the unit of a ratio KPI now follows its financial ratio (ROS is percent, not number)
    env.add_to_compute(kpis._fields["unit"], kpis)
    env["cmc.kpi"].flush_model(["unit"])
