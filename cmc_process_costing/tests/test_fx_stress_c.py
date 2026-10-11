"""Stress-test fixes 2026-10, corsia C, on ``cmc_process_costing``: FX-004 (DL-002), FX-017.

CONTRACT
- FX-004 / DL-002: ``cmc.process.run.action_cmc_prefill()`` on a DRAFT run with NOTHING to prefill from - no
  MRP fact (produced / scrap feeders, after the refresh) for the department x product x month AND no source
  run (``previous_run_id`` / ``previous_dept_run_id`` empty) -> ``UserError`` saying there are no MRP
  quantities for the month and where they come from; the quantity schedule typed by hand on the run
  (``units_started``, ``units_completed``, ``units_spoiled``) stays exactly as it was.
  A run with a source run but no MRP fact still prefills from the source (``test_process_quantity``:
  beginning WIP from the previous month).
- The form button ``action_cmc_prefill`` carries a ``confirm`` attribute (it overwrites the typed schedule).
- FX-017: the ``state`` selection labels of ``cmc.process.run`` are translated in it_IT: draft -> "Bozza",
  done -> "Calcolato".
"""

from lxml import etree

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import N_FROM, N_TO, ProcessCase


@tagged("post_install", "-at_install")
class TestFxStressProcess(ProcessCase):
    def test_fx004_prefill_without_mrp_facts_raises_and_keeps_typed_schedule(self):
        # FX-004 / DL-002: next month has no MO of the department -> say so, never zero the schedule typed by hand
        run = self._run(
            typed={"units_started": 600.0, "units_completed": 500.0, "units_spoiled": 20.0, "ending_units": 80.0},
            date_from=N_FROM, date_to=N_TO, prefill=False, compute=False,
        )
        with self.assertRaises(UserError):
            run.action_cmc_prefill()
        run.invalidate_recordset()
        self.assertRecordValues(run, [{"units_started": 600.0, "units_completed": 500.0, "units_spoiled": 20.0, "ending_units": 80.0}])

    def test_fx004_prefill_button_asks_confirmation(self):
        # FX-004 / DL-002: the prefill overwrites the typed schedule -> the button warns before doing it
        arch = self.env["cmc.process.run"].get_view(self.env.ref("cmc_process_costing.view_cmc_process_run_form").id, "form")["arch"]
        buttons = etree.fromstring(arch).xpath("//button[@name='action_cmc_prefill']")
        self.assertTrue(buttons)
        for button in buttons:
            self.assertTrue(button.get("confirm"), "the Prefill from MRP button must carry a confirm attribute")

    def test_fx017_state_labels_translated_in_italian(self):
        # FX-017: an Italian user reads "Bozza / Calcolato" on the statusbar of the process run
        self.assertTrue(self.env["res.lang"]._lang_get("it_IT"), "it_IT must be installed in the test database")
        selection = dict(self.env["cmc.process.run"].with_context(lang="it_IT").fields_get(["state"])["state"]["selection"])
        self.assertEqual(selection["draft"], "Bozza")
        self.assertEqual(selection["done"], "Calcolato")
