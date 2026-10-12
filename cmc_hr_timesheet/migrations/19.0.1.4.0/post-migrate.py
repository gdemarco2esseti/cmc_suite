import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger("cmc_hr_timesheet_post_migrate_1_4_0")


def migrate(cr, version):
    # why: timesheet lines booked before the live bridge (AD-106) carry no project cost object, so job costing
    # misses their hours. Set-based: objects ensured once per company, then one UPDATE for the whole history.
    env = api.Environment(cr, SUPERUSER_ID, {})
    cr.execute(
        """
        SELECT company_id, ARRAY_AGG(DISTINCT project_id)
          FROM account_analytic_line
         WHERE project_id IS NOT NULL AND cmc_cost_object_id IS NULL
         GROUP BY company_id
        """
    )
    pairs = []
    for company_id, project_ids in cr.fetchall():
        projects = env["project.project"].with_context(active_test=False).browse(project_ids).exists()
        objects = env["cmc.cost.object"]._cmc_ensure_objects(
            projects, company=env["res.company"].browse(company_id), object_type="project"
        )
        pairs += [(company_id, project.id, obj.id) for project, obj in zip(projects, objects, strict=True)]
    if not pairs:
        return
    companies, projects, objects = (list(col) for col in zip(*pairs, strict=True))
    cr.execute(
        """
        UPDATE account_analytic_line l
           SET cmc_cost_object_id = v.object_id
          FROM unnest(%s::int[], %s::int[], %s::int[]) AS v(company_id, project_id, object_id)
         WHERE l.company_id = v.company_id AND l.project_id = v.project_id AND l.cmc_cost_object_id IS NULL
        """,
        (companies, projects, objects),
    )
    _logger.info("Stamped the project cost object on %s timesheet lines.", cr.rowcount)
    env.invalidate_all()
