"""WP-05 - job cost sheet PDF (smoke render), link from the project, WP-15 extension point.

CONTRACT (part)
- ``cmc_job_costing.action_report_job_cost_sheet``: qweb-pdf on ``cmc.job.cost.run``, bound to the model
  (Print menu); renders for a CMC user with one block per sheet (project name, cost lines, margin, status).
  In test mode the PDF falls back to HTML (no wkhtmltopdf).
- ``project.project.action_cmc_job_sheets()``: window action on ``cmc.job.sheet`` filtered on the project.
- AD-101/104: ``cmc.plan.version.version_type`` offers ``baseline`` (WP-15 EVM lands in this module).
"""

from odoo import Command
from odoo.tests.common import new_test_user, tagged

from .common import REPORT_XMLID, JobCase


@tagged("post_install", "-at_install")
class TestJobReport(JobCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_a = new_test_user(
            cls.env, login="wp05_report_user", groups="base.group_user,project.group_project_user,cmc_base.group_cmc_user",
            company_id=cls.company_a.id, company_ids=[Command.set(cls.company_a.ids)],
        )

    def test_report_action_bound_to_run(self):
        report = self.env.ref(REPORT_XMLID)
        self.assertRecordValues(report, [{"model": "cmc.job.cost.run", "report_type": "qweb-pdf"}])
        self.assertEqual(report.binding_model_id.model, "cmc.job.cost.run")

    def test_report_renders_sheets(self):
        # FC-06: the job cost sheet is printable by the controller
        run = self._run(env=self._env_a(self.user_a))
        content, _report_type = self.env["ir.actions.report"].with_user(self.user_a)._render_qweb_pdf(REPORT_XMLID, run.ids)
        html = content.decode() if isinstance(content, bytes) else str(content)
        for text in ("Commessa Alfa", "Commessa Beta", "WP05 March"):
            self.assertIn(text, html)
        self.assertNotIn("Commessa Gamma", html)

    def test_project_opens_its_sheets(self):
        # AD-103: build the job cost sheet, link it from the project
        run = self._run()
        action = self.j1.action_cmc_job_sheets()
        self.assertEqual(action["res_model"], "cmc.job.sheet")
        self.assertEqual(self.env["cmc.job.sheet"].search(action["domain"]), self._sheet(run, self.j1))

    def test_baseline_version_type_available(self):
        # AD-104: owning modules add their version types; WP-15 baselines live here
        selection = dict(self.env["cmc.plan.version"]._fields["version_type"].selection)
        self.assertIn("baseline", selection)
