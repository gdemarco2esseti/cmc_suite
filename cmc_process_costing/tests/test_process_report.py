"""WP-06 / FC-07 - cost of production report (PDF smoke render; HTML fallback in test mode).

CONTRACT (part): ``cmc_process_costing.action_report_cost_of_production`` qweb-pdf on ``cmc.process.run``, bound to
the model (Print menu); rendered by a CMC user: department, product, quantity schedule, EU, cost per EU,
cost assignment and control total.
"""

from odoo.tests.common import tagged

from .common import REPORT_XMLID, ProcessCase


@tagged("post_install", "-at_install")
class TestProcessReport(ProcessCase):
    def test_report_bound_to_run(self):
        report = self.env.ref(REPORT_XMLID)
        self.assertRecordValues(report, [{"model": "cmc.process.run", "report_type": "qweb-pdf"}])
        self.assertEqual(report.binding_model_id.model, "cmc.process.run")

    def test_report_renders_cost_of_production(self):
        # FC-07 / BB 1.3: one cost of production report per department and month
        # why: t-field formats numbers in the user language; pin it so the figures below are locale-independent
        self.env["res.lang"]._activate_lang("en_US")
        self.user.sudo().lang = "en_US"
        d1 = self._d1(env=self.env(user=self.user))
        d2 = self._d2(d1, env=self.env(user=self.user))
        content, _report_type = self.env["ir.actions.report"].with_user(self.user).with_context(lang="en_US")._render_qweb_pdf(  # why: number format is locale-dependent
            REPORT_XMLID, (d1 | d2).ids
        )
        html = content.decode() if isinstance(content, bytes) else str(content)
        for text in ("WP06 Assembly", "WP06 Finishing", "WP06 Semi S", "WP06 Finished F",
                     "126,100.00", "108,900.00", "16,100.00", "1,100.00", "124,800.00", "13,600.00"):
            self.assertIn(text, html)
