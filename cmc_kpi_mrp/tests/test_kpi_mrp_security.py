"""AD-115 security of cmc_kpi_mrp, every case executed AS the role it is about.

- CMC manager: loads the MRP KPI library. CMC user / internal user without CMC group: AccessError on the loader.
- CMC user WITHOUT MRP rights: refreshes the feeders (engine, sudo) and computes a run over the MRP KPIs.
- MRP user: flags an MO as rework (cmc_is_rework). A CMC-only user cannot touch MOs.
- Multi-company: each company loads its own library (codes unique per company) and reads its own facts.
"""

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import JAN, MAR_END, KpiMrpCase, dt


@tagged("post_install", "-at_install")
class TestKpiMrpSecurity(KpiMrpCase):
    def test_manager_loads_the_library(self):
        # AD-115: maintaining the KPI library is a manager task
        lib = self._library(user=self.manager)
        self.assertIn("mrp_fpy", lib)
        self.assertEqual(lib["mrp_fpy"].company_id, self.company)

    def test_cmc_user_cannot_load_the_library(self):
        # AD-115: a CMC user computes runs, he does not configure the library
        with self.assertRaises(AccessError):
            self._library(user=self.user)

    def test_outsider_cannot_load_the_library(self):
        # AD-115: an internal user without CMC group has no access
        with self.assertRaises(AccessError):
            self._library(user=self.outsider)

    def test_cmc_user_refreshes_and_computes_without_mrp_rights(self):
        # AD-115 / AD-107: the feeders run as engine; the controller needs no Manufacturing rights
        self.assertFalse(self.user.has_group("mrp.group_mrp_user"), "precondition: the CMC user has no MRP rights")
        lib = self._library(user=self.manager)
        self.Fact.with_user(self.user)._cmc_refresh_feeders(JAN, MAR_END, company=self.company)
        run = self._run(JAN, JAN, user=self.user, kpis=lib["mrp_cycle_time"] | lib["mrp_mce"])
        self.assertAlmostEqual(self._val(run, "mrp_cycle_time", JAN).with_user(self.user).value, 40.0, places=4)
        self.assertAlmostEqual(self._val(run, "mrp_mce", JAN).with_user(self.user).value, 15.0, places=4)

    def test_mrp_user_flags_rework(self):
        # contract: the rework marker is set on the shop floor by an MRP user
        mo = self.env["mrp.production"].create({"product_id": self.gear.id, "product_qty": 1.0, "bom_id": self.bom_plain.id,
                                                "cmc_center_id": self.rework_center.id})
        mo.with_user(self.mrp_user).cmc_is_rework = True
        self.assertTrue(mo.cmc_is_rework)

    def test_cmc_user_cannot_flag_rework(self):
        # AD-115: a CMC-only user has no write access to manufacturing orders
        with self.assertRaises(AccessError):
            self.mo1.with_user(self.user).write({"cmc_is_rework": True})

    def test_library_and_facts_per_company(self):
        # AD-115 multi-company: company B has its own library (same codes) and its own lead time
        company_b = self.env["res.company"].create({"name": "KPIMRP Sec B"})
        self.env.user.company_ids = [Command.link(company_b.id)]
        center_b = self._center("KPIMRP sec press B", company=company_b)
        bom_b = self.bom_plain.with_company(company_b).copy({"company_id": company_b.id})
        self._done_mo(4.0, dt(1, 6), dt(1, 9), center=center_b, bom=bom_b, company=company_b)
        self.Fact._cmc_refresh_feeders(JAN, MAR_END, company=self.company)
        self.Fact._cmc_refresh_feeders(JAN, MAR_END, company=company_b)
        lib_a = self._library()
        lib_b = self._library(company_b)
        self.assertEqual(lib_b["mrp_lead_time"].company_id, company_b)
        self.assertNotEqual(lib_a["mrp_lead_time"], lib_b["mrp_lead_time"])
        run_b = self._run(JAN, JAN, company=company_b, kpis=lib_b["mrp_lead_time"])
        self.assertAlmostEqual(self._val(run_b, "mrp_lead_time", JAN).value, 3.0, places=4)
        run_a = self._run(JAN, JAN, kpis=lib_a["mrp_lead_time"])
        self.assertAlmostEqual(self._val(run_a, "mrp_lead_time", JAN).value, 1.5, places=4)
