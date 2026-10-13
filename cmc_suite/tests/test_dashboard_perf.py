from datetime import date

from odoo.tests.common import TransactionCase, new_test_user, tagged

from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE

PERIOD = {"kind": "year", "ref": "2026-06-15"}
QUERY_BUDGET = 45


@tagged("post_install", "-at_install")
class TestDashboardQueryBudget(TransactionCase):
    """AD-004 / F4: the WHOLE controller-dashboard payload, with every contributor installed, costs at most
    45 SQL queries (warm cache), whatever the data volume.

    Margin: the per-module allowances of DASH-devlead.md section 2 add up to 41 queries (base 9, account 1,
    full_costing 1, abc 2, joint_costs 1, tdabc 3, process_costing 1, standard_cost 1, job_costing 4,
    direct_costing 3, budget 3, abb 1, scenario 2, target_lcc 3, profitability 1 + 1 lazy, kpi 2,
    fin_analysis 2) -> 4 queries of headroom (~10%) for the ACL/record-rule lookups of the first read of a
    model in a transaction. Only the total is pinned (F4); allowances are guidance.

    The budget is a CEILING (fewer queries only logs). The volume fixture is what makes it meaningful: with
    one record per model any implementation is cheap, so the base cost data has many elements/lines/
    closings/capacity rows and the contributors have DONE runs (the data branches - latest-run reads,
    second queries on the latest run - are the expensive ones). An N+1 over elements, lines, closing lines
    or capacity rows turns this test red.

    Run as a controlling manager (non-superuser): assertQueryCount counts for ``self.env.user`` and the
    ACL checks (has_access) are part of the real cost.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "CMC Dash Perf"})
        cls.manager = new_test_user(
            cls.env,
            login="cmc_dash_perf_manager",
            groups="base.group_user,cmc_base.group_cmc_manager",
            company_id=cls.company.id,
            company_ids=[(6, 0, cls.company.ids)],
        )
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=[cls.company.id]))

    @classmethod
    def _volume_fixture(cls):
        env = cls.env
        company_id = cls.company.id
        plan = (
            env["account.analytic.plan"]
            .search([("parent_id", "=", False)])
            .filtered(lambda p: p._column_name() == "account_id")[:1]
        )
        centers = env["account.analytic.account"].create(
            [
                {"name": f"Perf center {i}", "plan_id": plan.id, "cmc_center_type": "final", "company_id": company_id}
                for i in range(10)
            ]
        )
        elements = env["cmc.cost.element"].create(
            [{"name": f"Perf element {i}", "code": f"PERF_{i:02d}", "company_id": company_id} for i in range(15)]
        )
        lines = []
        for year in (2025, 2026):
            for i in range(150):
                vals = {
                    "name": "perf cost",
                    "account_id": centers[i % 10].id,
                    "company_id": company_id,
                    "date": date(year, 1 + i % 12, 1 + i % 28),
                    "amount": -(10.0 + i),
                    "cmc_behavior": "fixed" if i % 5 else False,
                    "cmc_cost_element_id": elements[i % 15].id if i % 7 else False,
                }
                lines.append(vals)
        env["account.analytic.line"].create(lines)
        for month in (3, 6, 9):
            closing = env["cmc.closing.run"].create(
                {"date_from": date(2026, month, 1), "date_to": date(2026, month, 28), "company_id": company_id}
            )
            env["cmc.closing.line"].with_context(cmc_run_state=CMC_RUN_STATE).create(
                [
                    {"run_id": closing.id, "center_id": center.id, "cost_distributed": 100.0, "cost_residual": 1.0}
                    for center in centers
                ]
            )
            closing.with_context(cmc_run_state=CMC_RUN_STATE).write({"state": "done"})
        env["cmc.capacity.analysis"].create(
            [
                {
                    "center_id": center.id,
                    "company_id": company_id,
                    "date_from": date(2026, 6, 1),
                    "date_to": date(2026, 6, 30),
                    "capacity_practical": 100.0,
                    "capacity_actual": 70.0,
                }
                for center in centers
            ]
        )
        # contributors: done runs where the model can be created without a product/project network
        dates = {"date_from": date(2026, 1, 1), "date_to": date(2026, 6, 30)}
        run_specs = {
            "cmc.abc.run": {},
            "cmc.joint.run": {},
            "cmc.tdabc.run": {},
            "cmc.fin.analysis": {},
            "cmc.job.cost.run": dates,
            "cmc.profit.run": dates,
            "cmc.coge.reconciliation": {**dates, "name": "Perf reconciliation"},
            "cmc.costing.run": {"date_from": closing.date_from, "date_to": closing.date_to, "closing_run_id": closing.id},
            "cmc.decision": {"name": "Perf decision"},
            "cmc.plan.version": {**dates, "name": "Perf budget", "version_type": "budget"},
        }
        for model, vals in run_specs.items():
            for _i in range(3):
                run = env[model].create({**vals, "company_id": company_id})
                run.with_context(cmc_run_state=CMC_RUN_STATE).write({"state": "done"})
        env["cmc.cvr.analysis"].create([{"company_id": company_id} for _i in range(3)])
        env["cmc.direct.costing"].create([{"company_id": company_id} for _i in range(3)])

    def _assert_budget(self):
        self.env = self.env(user=self.manager)
        cockpit = self.env["cmc.cockpit"]
        cockpit.get_dashboard_data(PERIOD)  # warm the caches (ACL, record rules, translations)
        self.env.invalidate_all()
        with self.assertQueryCount(QUERY_BUDGET):
            data = cockpit.get_dashboard_data(PERIOD)
        return data

    def test_dashboard_query_budget_with_volume(self):
        self._volume_fixture()
        data = self._assert_budget()
        # sanity: the volume branches were exercised, not an empty dashboard
        self.assertFalse(data["header"]["global_empty"])
        self.assertTrue(any(card["key"] == "base.closing" and card["status"] != "empty" for card in data["cards"]))

    def test_dashboard_query_budget_empty_company(self):
        data = self._assert_budget()
        self.assertTrue(data["header"]["global_empty"])
