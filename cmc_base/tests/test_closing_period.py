from datetime import date

import psycopg2

from odoo.tests.common import TransactionCase, tagged
from odoo.tools import mute_logger

# v19 ORM performs no Python pre-check for required fields: create() INSERTs
# right away and the NOT NULL column rejects the row (odoo/orm/models.py only
# turns NotNullViolation into a message at the RPC layer). Odoo's assertRaises
# does not accept a tuple of exceptions, hence a single class.
MISSING_PERIOD_ERROR = psycopg2.errors.NotNullViolation


@tagged("post_install", "-at_install")
class TestClosingPeriod(TransactionCase):
    """Refactor 2026-10, task 1 — a closing run always has a period, and only
    the analytic amounts inside that period are allocated."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AA = cls.env["account.analytic.account"]
        cls.AL = cls.env["account.analytic.line"]
        cls.Run = cls.env["cmc.closing.run"]
        cls.plan = (
            cls.env["account.analytic.plan"]
            .search([("parent_id", "=", False)])
            .filtered(lambda p: p._column_name() == "account_id")[:1]
        )
        cls.driver = cls.env["cmc.cost.driver"].create({"name": "Hours", "code": "H"})
        # fixed past year: deterministic, never overlaps "today" lines of other fixtures
        cls.year = 2019
        cls.q1_from = date(cls.year, 1, 1)
        cls.q1_to = date(cls.year, 3, 31)

    @classmethod
    def _center(cls, name, ctype):
        return cls.AA.create({"name": name, "plan_id": cls.plan.id, "cmc_center_type": ctype})

    def _cost(self, center, amount, on_date, element=False):
        vals = {"name": "cost", "account_id": center.id, "amount": -amount, "date": on_date}  # AD-005: cost = negative amount
        if element:
            vals["cmc_cost_element_id"] = element.id
        return self.AL.create(vals)

    def _service(self, provider, receiver, qty):
        return self.env["cmc.center.service"].create(
            {"provider_id": provider.id, "receiver_id": receiver.id, "driver_id": self.driver.id, "quantity": qty}
        )

    def _line(self, run, center):
        return run.line_ids.filtered(lambda line: line.center_id == center)

    # ---- AC1.1 — no period, no closing ------------------------------------

    # AC1.1: the period is a field-level requirement (form + ORM), not an optional filter
    def test_ac11_period_fields_are_required(self):
        self.assertTrue(self.Run._fields["date_from"].required, "cmc.closing.run.date_from must be required")
        self.assertTrue(self.Run._fields["date_to"].required, "cmc.closing.run.date_to must be required")

    # AC1.1: a closing created without any date would aggregate the whole history -> refused
    @mute_logger("odoo.sql_db")
    def test_ac11_create_without_dates_fails(self):
        with self.assertRaises(MISSING_PERIOD_ERROR), self.env.cr.savepoint():
            self.Run.create({"name": "No period", "method": "direct"})
            self.env.flush_all()

    # AC1.1: an open-ended period (start only) is still unbounded -> refused
    @mute_logger("odoo.sql_db")
    def test_ac11_create_without_date_to_fails(self):
        with self.assertRaises(MISSING_PERIOD_ERROR), self.env.cr.savepoint():
            self.Run.create({"name": "No end", "method": "direct", "date_from": self.q1_from})
            self.env.flush_all()

    # AC1.1: an open-ended period (end only) is still unbounded -> refused
    @mute_logger("odoo.sql_db")
    def test_ac11_create_without_date_from_fails(self):
        with self.assertRaises(MISSING_PERIOD_ERROR), self.env.cr.savepoint():
            self.Run.create({"name": "No start", "method": "direct", "date_to": self.q1_to})
            self.env.flush_all()

    # AC1.1: the period cannot be cleared afterwards either (write path)
    @mute_logger("odoo.sql_db")
    def test_ac11_clear_period_on_existing_closing_fails(self):
        run = self.Run.create({"name": "Q1", "method": "direct", "date_from": self.q1_from, "date_to": self.q1_to})
        with self.assertRaises(MISSING_PERIOD_ERROR), self.env.cr.savepoint():
            run.write({"date_from": False, "date_to": False})
            self.env.flush_all()

    # ---- AC1.2 — only the amounts inside the period are allocated ---------

    # AC1.2: a Q1 closing allocates only Q1 costs; Q2 costs on the same center stay out
    def test_ac12_q1_closing_ignores_q2_lines(self):
        aux = self._center("Maintenance", "auxiliary")
        z = self._center("Assembly Z", "final")
        y = self._center("Assembly Y", "final")
        # Q1 pool = 3600 (boundaries included), Q2 pool = 9000 (must be ignored)
        self._cost(aux, 1600, self.q1_from)
        self._cost(aux, 2000, self.q1_to)
        self._cost(aux, 5000, date(self.year, 4, 1))
        self._cost(aux, 4000, date(self.year, 5, 15))
        # a final center with direct cost in both quarters
        self._cost(z, 700, date(self.year, 2, 10))
        self._cost(z, 300, date(self.year, 6, 30))
        self._service(aux, z, 60)
        self._service(aux, y, 60)
        run = self.Run.create({"name": "Q1", "method": "direct", "date_from": self.q1_from, "date_to": self.q1_to})
        run.action_compute()
        self.assertRecordValues(
            self._line(run, aux),
            [{"cost_initial": 3600.0, "cost_distributed": 3600.0, "cost_residual": 0.0}],
        )
        self.assertRecordValues(
            self._line(run, z),
            [{"cost_initial": 700.0, "cost_received": 1800.0, "cost_residual": 2500.0}],
        )
        self.assertRecordValues(
            self._line(run, y),
            [{"cost_initial": 0.0, "cost_received": 1800.0, "cost_residual": 1800.0}],
        )

    # AC1.2: the cost-analysis matrix of the same closing ties out to the Q1 pool only
    def test_ac12_cost_analysis_limited_to_period(self):
        element = self.env["cmc.cost.element"].create({"name": "Labour", "code": "LAB"})
        aux = self._center("Maintenance", "auxiliary")
        z = self._center("Assembly Z", "final")
        self._cost(aux, 1200, date(self.year, 2, 1), element)
        self._cost(aux, 8800, date(self.year, 4, 1), element)  # Q2: out of window
        self._cost(z, 400, date(self.year, 3, 1), element)
        self._cost(z, 600, date(self.year - 1, 12, 31), element)  # previous year: out of window
        self._service(aux, z, 10)
        run = self.Run.create({"name": "Q1", "method": "direct", "date_from": self.q1_from, "date_to": self.q1_to})
        run.action_compute()
        pre = run.cost_analysis_ids.filtered(lambda line: line.state == "pre")
        self.assertAlmostEqual(sum(pre.mapped("amount")), 1600.0, places=2)
        # the post-allocation state lands the whole Q1 pool on the final center
        post_z = run.cost_analysis_ids.filtered(lambda line: line.state == "post" and line.center_id == z)
        self.assertAlmostEqual(sum(post_z.mapped("amount")), 1600.0, places=2)

    # AC1.2: two consecutive closings on the same center do not double count
    def test_ac12_consecutive_quarters_partition_the_cost(self):
        aux = self._center("Maintenance", "auxiliary")
        z = self._center("Assembly Z", "final")
        self._cost(aux, 1000, date(self.year, 3, 15))
        self._cost(aux, 2500, date(self.year, 4, 15))
        self._service(aux, z, 1)
        q1 = self.Run.create({"name": "Q1", "method": "direct", "date_from": self.q1_from, "date_to": self.q1_to})
        q2 = self.Run.create(
            {"name": "Q2", "method": "direct", "date_from": date(self.year, 4, 1), "date_to": date(self.year, 6, 30)}
        )
        (q1 | q2).action_compute()
        self.assertEqual(self._line(q1, z).cost_received, 1000.0)
        self.assertEqual(self._line(q2, z).cost_received, 2500.0)
