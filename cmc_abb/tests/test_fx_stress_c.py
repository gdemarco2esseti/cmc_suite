"""Stress-test fixes 2026-10, corsia C, on ``cmc_abb``: FX-004 (DL-002), FX-015, FX-017.

CONTRACT
- FX-004 / DL-002: ``cmc.abb.run.action_prefill_bill()`` on a DRAFT run whose calibration run yields NO
  activity -> cost object edge with a produced quantity (nothing to prefill) -> ``UserError`` saying where the
  data is missing; the bill lines already on the run stay exactly as they were. The form button
  ``action_prefill_bill`` carries a ``confirm`` attribute (it replaces the current bill).
- FX-015: actual cost of a resource in ``cmc.abb.variance.line.actual_cost`` = Σ cost booked on the resource
  center x its elements in the variance period, INCLUDING the lines tagged with an ABC activity
  (``cmc_activity_id`` set). The ABC pool rule of ``cmc_abc`` (``cmc.abc.run._cmc_ledger_pools``: tagged lines
  are NOT part of the resource pool, they are direct charges of the activity) is unchanged - an ABB-only
  reader, not a change of the ABC engine (existing guard: ``cmc_abc`` ``test_abc02_tag_pool_is_direct_charge``).
- FX-017: the ``state`` selection labels of ``cmc.abb.run`` and ``cmc.abb.variance.run`` are translated in
  it_IT: draft -> "Bozza", done -> "Calcolato".
"""

from datetime import date

from lxml import etree

from odoo.exceptions import UserError
from odoo.fields import Command
from odoo.tests.common import tagged

from .common import Y26_FROM, Y26_TO, AbbCase

Q1_FROM, Q1_TO = date(2027, 1, 1), date(2027, 3, 31)


@tagged("post_install", "-at_install")
class TestFxStressAbb(AbbCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env_a
        # FX-015: Q1 2027 cost on the technicians' center Z x salaries, ALL of it tagged with an ABC activity
        cls._tagged(env, cls.z, 100000.0, cls.sal, cls.setup, date(2027, 2, 15))
        cls._tagged(env, cls.z, 21800.0, cls.sal, cls.mach, date(2027, 3, 10))
        cls._tagged(env, cls.z, 7000.0, cls.dep, cls.mach, date(2027, 1, 20))  # machines resource, not technicians
        cls._tagged(env, cls.z, 5000.0, cls.sal, cls.setup, date(2027, 4, 15))  # outside Q1

    @classmethod
    def _tagged(cls, env, center, cost, element, activity, day):
        return env["account.analytic.line"].create({
            "name": "fx015 tagged cost", "account_id": center.id, "amount": -cost,  # AD-005: cost = negative amount
            "company_id": center.company_id.id, "cmc_cost_element_id": element.id, "cmc_behavior": "fixed",
            "cmc_activity_id": activity.id, "date": day,
        })

    def _variance_q1(self, abb):
        run = self.env_a["cmc.abb.variance.run"].create({
            "name": "FX015 Q1", "company_id": self.company_a.id, "abb_run_id": abb.id, "date_from": Q1_FROM, "date_to": Q1_TO,
        })
        run.action_compute()
        return run

    @staticmethod
    def _line(run, resource):
        return run.line_ids.filtered(lambda ln: ln.resource_id == resource)

    # ---- FX-004 -----------------------------------------------------------

    def _empty_calibration(self):
        """A computed ABC run of 2026 with resources but no activity -> cost object edge: nothing to read a bill from."""
        calib = self.env_a["cmc.abc.run"].create({
            "name": "FX004 calibration without edges", "company_id": self.company_a.id, "date_from": Y26_FROM, "date_to": Y26_TO,
            "output_measure_id": self.units.id,
            "resource_line_ids": [Command.create({"resource_id": self.tech.id, "activity_id": self.setup.id, "percent": 100})],
        })
        calib.action_compute()
        return calib

    def test_fx004_prefill_bill_without_source_raises_and_keeps_manual_lines(self):
        # FX-004 / DL-002: the calibration run gives no bill -> say so, never delete the bill lines typed by hand
        run = self._abb_run(compute=False, bill=self._bill()[:2], calibration_run_id=self._empty_calibration().id)
        before = run.bill_line_ids
        self.assertEqual(len(before), 2)
        with self.assertRaises(UserError):
            run.action_prefill_bill()
        run.invalidate_recordset()
        self.assertEqual(run.bill_line_ids, before)
        self.assertEqual(sorted(run.bill_line_ids.mapped("qty_per_unit")), [0.01, 0.5])

    def test_fx004_prefill_bill_button_asks_confirmation(self):
        # FX-004 / DL-002: the prefill replaces the current bill -> the button warns before doing it
        arch = self.env["cmc.abb.run"].get_view(self.env.ref("cmc_abb.view_cmc_abb_run_form").id, "form")["arch"]
        buttons = etree.fromstring(arch).xpath("//button[@name='action_prefill_bill']")
        self.assertTrue(buttons)
        for button in buttons:
            self.assertTrue(button.get("confirm"), "the Prefill Bill button must carry a confirm attribute")

    # ---- FX-015 -----------------------------------------------------------

    def test_fx015_actual_cost_includes_activity_tagged_lines(self):
        # FX-015: actual cost of a resource = every cost booked on its center x element in the period, tagged or not
        run = self._variance_q1(self._abb_run())
        tech = self._line(run, self.tech)
        self.assertAlmostEqual(tech.actual_cost, 121800.0, places=2)
        self.assertAlmostEqual(tech.spending_variance, tech.budget_cost - 121800.0, places=2)
        self.assertAlmostEqual(self._line(run, self.machr).actual_cost, 7000.0, places=2)

    def test_fx015_abc_pool_rule_unchanged(self):
        # FX-015: the fix is an ABB reader - in the ABC engine a tagged line stays a direct charge, never pooled
        window = self.env_a["cmc.abc.run"].new({"company_id": self.company_a.id, "date_from": Q1_FROM, "date_to": Q1_TO})
        pools = window._cmc_ledger_pools(self.tech | self.machr)[0]
        self.assertAlmostEqual(sum(sum(pair) for pair in pools.values()), 0.0, places=2)

    # ---- FX-017 -----------------------------------------------------------

    def test_fx017_state_labels_translated_in_italian(self):
        # FX-017: an Italian user reads "Bozza / Calcolato" on the statusbar of every run of the suite
        self.assertTrue(self.env["res.lang"]._lang_get("it_IT"), "it_IT must be installed in the test database")
        for model in ("cmc.abb.run", "cmc.abb.variance.run"):
            with self.subTest(model=model):
                selection = dict(self.env[model].with_context(lang="it_IT").fields_get(["state"])["state"]["selection"])
                self.assertEqual(selection["draft"], "Bozza")
                self.assertEqual(selection["done"], "Calcolato")
