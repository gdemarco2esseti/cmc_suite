from datetime import date

from freezegun import freeze_time as _freeze_time

from odoo.tests import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestDashboardTour(HttpCase):
    """UX-002 acceptance in the browser, as a controlling MANAGER who is not an administrator (screenshots
    and checks with the developer/admin group are a FAIL smell).

    Tour id ``cmc_controller_dashboard_tour`` - JS written by odoo-view-dev under
    ``cmc_base/static/tests/tours/`` (bundle ``web.assets_tests``), started from the existing action
    ``cmc_base.action_cmc_cockpit_dashboard`` (client tag ``cmc_controller_dashboard``). It must check:

    1. the dashboard loads: period selector (Mese/Trimestre/Anno/Tutto, default Anno), KPI row with the
       "Costi rilevati" tile, the "Da verificare" list, the "Stato per area" cards;
    2. clicking "Mese" switches the period: the range text changes to a single month and the layout stays;
    3. clicking the unclassified-cost alert row ("righe di costo non classificate") opens the analytic lines
       list (breadcrumb) with exactly the alert's count of rows; back, then clicking the "Chiusura periodo"
       card opens the closing runs list;
    4. "Guida configurazione" opens the setup guide (client tag ``cmc_setup_guide``: its pipeline is shown).

    The fixture below gives the user's company, in the current year, classified cost, two unclassified
    lines and a done closing, so every step has something to click.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "CMC Dash Tour"})
        cls.manager = new_test_user(
            cls.env,
            login="cmc_dash_tour_manager",
            groups="base.group_user,cmc_base.group_cmc_manager",
            company_id=cls.company.id,
            company_ids=[(6, 0, cls.company.ids)],
        )
        env = cls.env(context=dict(cls.env.context, allowed_company_ids=[cls.company.id]))
        plan = (
            env["account.analytic.plan"]
            .search([("parent_id", "=", False)])
            .filtered(lambda p: p._column_name() == "account_id")[:1]
        )
        center = env["account.analytic.account"].create(
            {"name": "Tour center", "plan_id": plan.id, "cmc_center_type": "final", "company_id": cls.company.id}
        )
        element = env["cmc.cost.element"].create({"name": "Tour materials", "code": "TOUR_MAT", "company_id": cls.company.id})
        day = date.today().replace(day=1)
        base = {"account_id": center.id, "company_id": cls.company.id, "date": day, "name": "tour cost"}
        env["account.analytic.line"].create(
            [
                {**base, "amount": -1000.0, "cmc_behavior": "fixed", "cmc_cost_element_id": element.id},
                {**base, "amount": -50.0, "cmc_behavior": "fixed"},
                {**base, "amount": -30.0},
            ]
        )
        closing = env["cmc.closing.run"].create(
            {"date_from": date(day.year, 1, 1), "date_to": date(day.year, 1, 31), "company_id": cls.company.id}
        )
        closing.action_compute()

    def test_controller_dashboard_tour(self):
        self.assertFalse(self.manager.has_group("base.group_system"), "the tour must not run as an administrator")
        self.start_tour(
            "/odoo/action-cmc_base.action_cmc_cockpit_dashboard",
            "cmc_controller_dashboard_tour",
            login=self.manager.login,
        )
