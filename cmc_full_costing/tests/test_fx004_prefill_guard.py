"""FX-004 (DL-002) - "Prefill Lines" of a costing run never wipes the lines in silence.

CONTRACT (stress-test fix 2026-10, corsia C)
- ``cmc.costing.run.action_cmc_prefill_lines()`` on a DRAFT run whose period holds NO actual fact of an
  allocation measure (per final center x product cost object) -> ``UserError`` that says there are no driver
  quantities for the period and where to enter them (Cost base -> Driver quantities); the driver lines already
  on the run stay exactly as they were (nothing deleted, nothing added).
- When facts exist the prefill keeps replacing the lines (pinned by ``test_wp04_unit_cost``).
- The form button ``action_cmc_prefill_lines`` carries a ``confirm`` attribute (it replaces the current lines).
"""

from lxml import etree

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .wp04_common import D_FROM, D_TO, Wp04Case


@tagged("post_install", "-at_install")
class TestFx004CostingPrefillGuard(Wp04Case):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # a company of its own: a closed period with cost but NO measure fact at all
        cls.company_c = cls.env["res.company"].create({"name": "CMC FX004 C"})
        env = cls.env_c = cls._company_env(cls.company_c)
        cls.center_c = cls._center(env, "FX004 H", cls.company_c)
        cls._indirect(env, cls.center_c, 1000.0, "fixed")
        cls.closing_c = cls._closing(env, cls.company_c)
        cls.minutes_c = env["cmc.cost.driver"].create(
            {"name": "FX004 minutes", "code": "FX004MIN", "company_id": cls.company_c.id}
        )

    def _draft_with_manual_lines(self):
        return self.env_c["cmc.costing.run"].create({
            "name": "FX004 costing", "company_id": self.company_c.id, "closing_run_id": self.closing_c.id,
            "date_from": D_FROM, "date_to": D_TO, "base": "multiple",
            "line_ids": [
                (0, 0, {"center_id": self.center_c.id, "product_id": self.p1.id, "driver_id": self.minutes_c.id, "driver_qty": 10.0}),
                (0, 0, {"center_id": self.center_c.id, "product_id": self.p2.id, "driver_id": self.minutes_c.id, "driver_qty": 5.0}),
            ],
        })

    def test_fx004_prefill_without_facts_raises_and_keeps_manual_lines(self):
        # FX-004 / DL-002: no driver quantity in the period -> say so, never delete the lines typed by hand
        run = self._draft_with_manual_lines()
        before = run.line_ids
        with self.assertRaises(UserError):
            run.action_cmc_prefill_lines()
        run.invalidate_recordset()
        self.assertEqual(run.line_ids, before)
        self.assertEqual(sorted(run.line_ids.mapped("driver_qty")), [5.0, 10.0])

    def test_fx004_prefill_button_asks_confirmation(self):
        # FX-004 / DL-002: the prefill replaces the current lines -> the button warns before doing it
        arch = self.env["cmc.costing.run"].get_view(self.env.ref("cmc_full_costing.view_cmc_costing_run_form").id, "form")["arch"]
        buttons = etree.fromstring(arch).xpath("//button[@name='action_cmc_prefill_lines']")
        self.assertTrue(buttons)
        for button in buttons:
            self.assertTrue(button.get("confirm"), "the Prefill Lines button must carry a confirm attribute")
