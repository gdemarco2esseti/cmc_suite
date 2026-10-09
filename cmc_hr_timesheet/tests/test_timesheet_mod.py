import importlib.util
import os

from odoo.modules import get_module_path
from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, new_test_user, tagged


def _load_migrate():
    path = os.path.join(get_module_path("cmc_hr_timesheet"), "migrations", "19.0.1.2.0", "post-migrate.py")
    # why: the migrations folder is named after a version ("19.0.1.2.0"), not importable
    spec = importlib.util.spec_from_file_location("cmc_hr_timesheet_post_migrate_1_2_0", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.migrate


@tagged("post_install", "-at_install")
class TestTimesheetMod(TransactionCase):
    """Fase 1.5b — feed MOD (direct labor) from hr_timesheet into cost lines."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AL = cls.env["account.analytic.line"]
        cls.plan = cls.env["account.analytic.plan"].search(
            [("parent_id", "=", False)]
        ).filtered(lambda p: p._column_name() == "account_id")[:1]
        cls.center = cls.env["account.analytic.account"].create(
            {"name": "Reparto", "plan_id": cls.plan.id, "cmc_center_type": "final"}
        )
        cls.emp = cls.env["hr.employee"].create(
            {"name": "Mario", "hourly_cost": 30.0}
        )
        cls.project = cls.env["project.project"].create(
            {"name": "Commessa", "account_id": cls.center.id}
        )
        cls.mod = cls.env.ref("cmc_base.cmc_element_mod")

    def _ts(self, **kw):
        vals = {"name": "work", "project_id": self.project.id,
                "employee_id": self.emp.id, "unit_amount": 2.0}
        vals.update(kw)
        return self.AL.create(vals)

    # MOD line: element=MOD, behavior=variable, directness=direct
    def test_timesheet_mod_classified(self):
        line = self._ts()
        self.assertEqual(line.cmc_cost_element_id, self.mod)
        self.assertEqual(line.cmc_behavior, "variable")
        self.assertEqual(line.cmc_directness, "direct")

    # manual classification on a timesheet is never overwritten
    def test_manual_not_overwritten(self):
        line = self._ts(cmc_behavior="fixed")
        self.assertTrue(line.cmc_classified_manually)
        self.assertEqual(line.cmc_behavior, "fixed")

    # a non-timesheet analytic line (no project/employee) is not tagged MOD
    def test_non_timesheet_not_mod(self):
        line = self.AL.create(
            {"name": "x", "account_id": self.center.id, "amount": -50.0}
        )
        self.assertNotEqual(line.cmc_cost_element_id, self.mod)
        self.assertFalse(line.cmc_behavior)

    # no double imputation: classifying does not create extra lines
    def test_no_double_imputation(self):
        before = self.AL.search_count([("account_id", "=", self.center.id)])
        self._ts()
        after = self.AL.search_count([("account_id", "=", self.center.id)])
        self.assertEqual(after, before + 1)

    # item 18 — ore su un centro NON finale (ausiliario) restano manodopera
    # INDIRETTA: la direttezza e' una proprieta' dell'oggetto di costo, non
    # della presenza di progetto+dipendente+ore.
    def test_auxiliary_center_stays_indirect(self):
        aux = self.env["account.analytic.account"].create(
            {"name": "Amministrazione", "plan_id": self.plan.id,
             "cmc_center_type": "auxiliary"}
        )
        project = self.env["project.project"].create(
            {"name": "Overhead", "account_id": aux.id}
        )
        line = self._ts(project_id=project.id)
        self.assertEqual(line.cmc_directness, "indirect")
        self.assertNotEqual(line.cmc_cost_element_id, self.mod)
        self.assertFalse(line.cmc_behavior)

    # item 18 — anche un centro di periodo (costi commerciali/amministrativi)
    def test_period_center_stays_indirect(self):
        period = self.env["account.analytic.account"].create(
            {"name": "Commerciale", "plan_id": self.plan.id, "cmc_is_period": True}
        )
        project = self.env["project.project"].create(
            {"name": "Periodo", "account_id": period.id}
        )
        line = self._ts(project_id=project.id)
        self.assertEqual(line.cmc_directness, "indirect")
        self.assertNotEqual(line.cmc_cost_element_id, self.mod)

    # block 3 — the bridge withdraws what it set when the line stops being MOD
    def test_mod_withdrawn_when_hours_move_to_auxiliary_center(self):
        line = self._ts()
        self.assertEqual(line.cmc_bridge_source, "timesheet_mod:cmc_behavior,cmc_cost_element_id,cmc_directness")
        aux = self.env["account.analytic.account"].create(
            {"name": "Manutenzione", "plan_id": self.plan.id, "cmc_center_type": "auxiliary"}
        )
        line.account_id = aux
        self.assertFalse(line.cmc_bridge_source)
        self.assertFalse(line.cmc_cost_element_id)
        self.assertFalse(line.cmc_behavior)
        self.assertEqual(line.cmc_directness, "indirect")

    def test_mod_withdrawn_when_hours_zeroed(self):
        line = self._ts()
        line.unit_amount = 0.0
        self.assertFalse(line.cmc_bridge_source)
        self.assertFalse(line.cmc_cost_element_id)

    def test_withdrawal_keeps_a_rule_classification(self):
        aux = self.env["account.analytic.account"].create(
            {"name": "Officina", "plan_id": self.plan.id, "cmc_center_type": "auxiliary"}
        )
        self.env["cmc.classification.rule"].create(
            {"name": "aux fixed", "center_id": aux.id, "cmc_behavior": "fixed"}
        )
        line = self._ts()
        line.account_id = aux
        self.assertEqual(line.cmc_behavior, "fixed")
        self.assertFalse(line.cmc_bridge_source)

    def test_manual_line_is_not_withdrawn(self):
        line = self._ts()
        line.cmc_behavior = "semi_variable"  # by hand: the line is now manual
        self.assertTrue(line.cmc_classified_manually)
        self.assertFalse(line.cmc_bridge_source)
        line.unit_amount = 0.0
        self.assertEqual(line.cmc_behavior, "semi_variable")
        self.assertEqual(line.cmc_cost_element_id, self.mod)

    def test_withdrawal_spares_values_the_bridge_did_not_fill(self):
        # a rule classified the line first; when the bridge comes in later it
        # fills only element and directness, and withdraws only those
        aux = self.env["account.analytic.account"].create(
            {"name": "Diventa finale", "plan_id": self.plan.id, "cmc_center_type": "auxiliary"}
        )
        rule = self.env["cmc.classification.rule"].create(
            {"name": "aux variable", "center_id": aux.id, "cmc_behavior": "variable"}
        )
        project = self.env["project.project"].create({"name": "P2", "account_id": aux.id})
        line = self._ts(project_id=project.id)
        self.assertEqual(line.cmc_behavior, "variable")
        aux.cmc_center_type = "final"
        line.unit_amount = 3.0
        self.assertEqual(line.cmc_bridge_source, "timesheet_mod:cmc_cost_element_id,cmc_directness")
        rule.active = False
        line.unit_amount = 0.0
        self.assertEqual(line.cmc_behavior, "variable")
        self.assertFalse(line.cmc_cost_element_id)
        self.assertEqual(line.cmc_directness, "indirect")

    def test_missing_mod_element_does_not_break_timesheets(self):
        self.mod.unlink()
        with self.assertLogs("odoo.addons.cmc_account.models.account_analytic_line", level="WARNING"):
            line = self._ts()
        self.assertEqual(line.cmc_behavior, "variable")
        self.assertFalse(line.cmc_cost_element_id)

    def test_bridge_source_not_writable_by_user(self):
        line = self._ts()
        user = new_test_user(self.env, login="cmc_ts_forger", groups="base.group_user,hr_timesheet.group_hr_timesheet_approver")
        with self.assertRaises(AccessError):
            line.with_user(user).write({"cmc_bridge_source": "timesheet_mod:cmc_behavior"})

    def test_controller_reclassifies_a_colleague_timesheet(self):
        # AD-007: the engine run by a CMC controller (Re-apply rules) writes the
        # bridge owner through the gate, on a timesheet that is not theirs
        aux = self.env["account.analytic.account"].create(
            {"name": "Presto finale", "plan_id": self.plan.id, "cmc_center_type": "auxiliary"}
        )
        project = self.env["project.project"].create({"name": "P", "account_id": aux.id})
        line = self._ts(project_id=project.id)
        self.assertFalse(line.cmc_bridge_source)
        aux.cmc_center_type = "final"
        controller = new_test_user(self.env, login="cmc_ts_controller", groups="base.group_user,cmc_base.group_cmc_user")
        line.with_user(controller)._cmc_auto_classify()
        self.assertEqual(line.cmc_cost_element_id, self.mod)
        self.assertTrue(line.cmc_bridge_source)

    # item 11 — la griglia timesheet crea la riga e scrive le ore DOPO:
    # la classificazione deve ri-innescarsi in scrittura.
    def test_classification_retriggered_when_hours_written_later(self):
        line = self._ts(unit_amount=0.0)
        self.assertFalse(line.cmc_behavior)
        line.unit_amount = 3.0
        self.assertEqual(line.cmc_cost_element_id, self.mod)
        self.assertEqual(line.cmc_behavior, "variable")
        self.assertEqual(line.cmc_directness, "direct")

    # ACL — un utente timesheet reale, SENZA diritti CMC, registra le ore e la
    # riga viene classificata lo stesso.
    def test_timesheet_user_without_cmc_rights(self):
        user = new_test_user(
            self.env, login="cmc_ts_user",
            groups="base.group_user,hr_timesheet.group_hr_timesheet_user",
        )
        self.assertFalse(user.has_group("cmc_base.group_cmc_user"))
        employee = self.env["hr.employee"].create(
            {"name": "Timesheet User", "user_id": user.id, "hourly_cost": 25.0}
        )
        line = self.AL.with_user(user).create({
            "name": "ore", "project_id": self.project.id,
            "employee_id": employee.id, "unit_amount": 2.0,
        })
        self.assertEqual(line.cmc_cost_element_id, self.mod)
        self.assertEqual(line.cmc_directness, "direct")

    # multi-company — una regola dell'azienda A non tocca una riga dell'azienda B
    def test_two_company_isolation(self):
        company_b = self.env["res.company"].create({"name": "TS Company B"})
        aux_b = self.env["account.analytic.account"].create({
            "name": "Ausiliario B", "plan_id": self.plan.id,
            "cmc_center_type": "auxiliary", "company_id": company_b.id,
        })
        project_b = self.env["project.project"].with_company(company_b).create(
            {"name": "Commessa B", "account_id": aux_b.id,
             "company_id": company_b.id}
        )
        employee_b = self.env["hr.employee"].with_company(company_b).create(
            {"name": "Bruno", "hourly_cost": 20.0, "company_id": company_b.id}
        )
        partner = self.env["res.partner"].create({"name": "Cliente MC"})
        Rule = self.env["cmc.classification.rule"]
        Rule.create({
            "name": "solo A", "company_id": self.env.company.id,
            "partner_id": partner.id, "cmc_behavior": "fixed",
        })
        line_b = self.AL.with_company(company_b).create({
            "name": "ore B", "project_id": project_b.id,
            "employee_id": employee_b.id, "unit_amount": 2.0,
            "partner_id": partner.id, "company_id": company_b.id,
        })
        self.assertFalse(
            line_b.cmc_behavior, "a company A rule classified a company B line"
        )
        Rule.create({
            "name": "solo B", "company_id": company_b.id,
            "partner_id": partner.id, "cmc_behavior": "semi_variable",
        })
        line_b2 = self.AL.with_company(company_b).create({
            "name": "ore B2", "project_id": project_b.id,
            "employee_id": employee_b.id, "unit_amount": 2.0,
            "partner_id": partner.id, "company_id": company_b.id,
        })
        self.assertEqual(line_b2.cmc_behavior, "semi_variable")

    # an excluded line (system twin) is never enriched as MOD
    def test_excluded_timesheet_is_not_classified(self):
        line = self._ts()
        line.write({
            "cmc_excluded": True, "cmc_behavior": False,
            "cmc_cost_element_id": False, "cmc_directness": "indirect",
            "cmc_classified_manually": False,
        })
        line._cmc_auto_classify()
        self.assertFalse(line.cmc_cost_element_id)
        self.assertFalse(line.cmc_behavior)
        self.assertEqual(line.cmc_directness, "indirect")

    def test_migration_tags_bridge_lines(self):
        bridged = self._ts()
        manual = self._ts(cmc_behavior="variable", cmc_directness="direct", cmc_cost_element_id=self.mod.id)
        self.env.flush_all()
        self.env.cr.execute("UPDATE account_analytic_line SET cmc_bridge_source = NULL WHERE id IN %s", ((bridged.id, manual.id),))
        with self.assertLogs("cmc_hr_timesheet_post_migrate_1_2_0", level="INFO"):
            _load_migrate()(self.env.cr, "19.0.1.1.0")
        self.env.invalidate_all()
        self.assertEqual(bridged.cmc_bridge_source, "timesheet_mod:cmc_behavior,cmc_cost_element_id,cmc_directness")
        self.assertFalse(manual.cmc_bridge_source)
