"""FX-004 (DL-002) - "Prefill Edges from Facts" of an ABC run never wipes the edges in silence.

CONTRACT (stress-test fix 2026-10, corsia C)
- ``cmc.abc.run.action_cmc_prefill_lines()`` on a DRAFT run when the span holds NO actual fact of any
  activity driver (after the feeder refresh) -> ``UserError`` that says there are no driver quantities for the
  period and where to enter them; the A->CO edges already on the run stay exactly as they were.
- With facts the prefill keeps replacing the A->CO edges of the measured activities (``test_abc2_drivers``).
- The form button ``action_cmc_prefill_lines`` carries a ``confirm`` attribute (it replaces the current edges).
Fixture: ``Abc2Case`` (March 2027) holds facts of the statistical "units" and of DLH only - no activity of the
company is driven by them, so the activity drivers have no fact at all.
"""

from lxml import etree

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .abc2_common import Abc2Case


@tagged("post_install", "-at_install")
class TestFx004AbcPrefillGuard(Abc2Case):
    def _edge(self, activity, cost_object, qty):
        return {"activity_id": activity.id, "cost_object_id": cost_object.id, "driver_id": activity.driver_id.id, "driver_qty": qty}

    def test_fx004_prefill_without_facts_raises_and_keeps_manual_edges(self):
        # FX-004 / DL-002: no activity-driver fact in the span -> say so, never delete the edges typed by hand
        run = self._main_run(compute=False, edges=[self._edge(self.setup, self.obj_p1, 6), self._edge(self.mach, self.obj_p1, 60)])
        before = run.line_ids
        self.assertEqual(len(before), 2)
        with self.assertRaises(UserError):
            run.action_cmc_prefill_lines()
        run.invalidate_recordset()
        self.assertEqual(run.line_ids, before)
        self.assertEqual(sorted(run.line_ids.mapped("driver_qty")), [6.0, 60.0])

    def test_fx004_prefill_button_asks_confirmation(self):
        # FX-004 / DL-002: the prefill replaces the current edges -> the button warns before doing it
        arch = self.env["cmc.abc.run"].get_view(self.env.ref("cmc_abc.view_cmc_abc_run_form").id, "form")["arch"]
        buttons = etree.fromstring(arch).xpath("//button[@name='action_cmc_prefill_lines']")
        self.assertTrue(buttons)
        for button in buttons:
            self.assertTrue(button.get("confirm"), "the Prefill Edges button must carry a confirm attribute")
