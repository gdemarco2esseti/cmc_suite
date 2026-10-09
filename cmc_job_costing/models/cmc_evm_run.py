from collections import defaultdict

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from odoo.addons.cmc_base.models import cmc_utils

INDEX_FIELDS = ("cpi", "spi", "tcpi")
MONEY_FIELDS = ("cv", "sv", "eac", "eac_atypical", "eac_composite", "etc", "vac")


class CmcEvmRun(models.Model):
    _name = "cmc.evm.run"
    _inherit = ["cmc.run.mixin"]
    _description = "EVM Status Report"
    _order = "status_date desc, id desc"
    _check_company_auto = True

    _cmc_input_fields = ("company_id", "project_id", "baseline_id", "job_run_id", "status_date", "control_limit")
    _cmc_result_fields = ("task_line_ids", "curve_ids")

    name = fields.Char(required=True, default=lambda self: self.env._("EVM status"))
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    project_id = fields.Many2one("project.project", string="Job", required=True, check_company=True, index=True)
    baseline_id = fields.Many2one("cmc.plan.version", required=True, check_company=True, domain="[('version_type', '=', 'baseline')]")
    job_run_id = fields.Many2one("cmc.job.cost.run", string="Job Cost Run", required=True, check_company=True, ondelete="restrict")
    status_date = fields.Date(required=True)
    control_limit = fields.Monetary(help="Band on the monthly cost variance. 0 = no limit.")
    bac = fields.Monetary(string="BAC", readonly=True, copy=False)
    pv = fields.Monetary(string="PV", readonly=True, copy=False)
    ev = fields.Monetary(string="EV", readonly=True, copy=False)
    ac = fields.Monetary(string="AC", readonly=True, copy=False)
    cv = fields.Monetary(string="CV", readonly=True, copy=False)
    sv = fields.Monetary(string="SV", readonly=True, copy=False)
    cpi = fields.Float(string="CPI", digits=(16, 4), readonly=True, copy=False)
    spi = fields.Float(string="SPI", digits=(16, 4), readonly=True, copy=False)
    tcpi = fields.Float(string="TCPI", digits=(16, 4), readonly=True, copy=False)
    eac = fields.Monetary(string="EAC", readonly=True, copy=False, help="BAC / CPI.")
    eac_atypical = fields.Monetary(string="EAC (atypical)", readonly=True, copy=False, help="AC + BAC - EV.")
    eac_composite = fields.Monetary(string="EAC (composite)", readonly=True, copy=False, help="AC + (BAC - EV) / (CPI x SPI).")
    etc = fields.Monetary(string="ETC", readonly=True, copy=False)
    vac = fields.Monetary(string="VAC", readonly=True, copy=False)
    rule_of_seven = fields.Boolean(readonly=True, copy=False, help="Seven consecutive months of variance on the same side.")
    out_of_control_count = fields.Integer(readonly=True, copy=False)
    task_line_ids = fields.One2many("cmc.evm.task.line", "run_id")
    curve_ids = fields.One2many("cmc.evm.curve", "run_id")

    @api.constrains("baseline_id", "project_id")
    def _check_baseline(self):
        for run in self:
            if run.baseline_id.version_type != "baseline" or run.baseline_id.project_id != run.project_id:
                raise ValidationError(self.env._("The baseline must be a baseline version of the job of the report."))

    def _cmc_reset_values(self):
        return dict.fromkeys((*MONEY_FIELDS, *INDEX_FIELDS, "bac", "pv", "ev", "ac", "out_of_control_count"), 0) | {"rule_of_seven": False}

    # ---- engine ---------------------------------------------------------

    def _cmc_compute(self):
        self.ensure_one()
        baseline, job_run = self.baseline_id, self.job_run_id
        sheet = self._cmc_check_sources()
        self._cmc_record_usage(baseline)
        self._cmc_record_usage(job_run)
        # why sudo: the engine reads the plan and the tasks of the run company and writes the result rows (read-only for users)
        engine = self.sudo()
        currency = self.company_id.currency_id
        rows = engine.env["cmc.evm.baseline.line"]._read_group([("version_id", "=", baseline.id)], ["task_id", "date:day"], ["planned_value:sum"])
        bac_by_task, pv_by_task, pv_by_month = defaultdict(float), defaultdict(float), defaultdict(float)
        for task, month, value in rows:
            bac_by_task[task.id] += value
            pv_by_month[month] += value
            if month <= self.status_date:  # the status month counts in full
                pv_by_task[task.id] += value
        tasks = engine.env["project.task"].browse(bac_by_task)
        progress = dict(zip(tasks.ids, tasks.mapped("cmc_progress"), strict=True))
        ev_by_task = {task: currency.round(bac * progress[task] / 100.0) for task, bac in bac_by_task.items()}
        pv, ev, ac = sum(pv_by_task.values()), sum(ev_by_task.values()), sheet.job_cost
        engine.env["cmc.evm.task.line"].create([
            {"run_id": self.id, "task_id": task, "bac": bac, "percent_complete": progress[task], "pv": pv_by_task[task],
             "ev": ev_by_task[task], "sv": ev_by_task[task] - pv_by_task[task]}
            for task, bac in bac_by_task.items()
        ])
        breaches, cvs = self._cmc_create_curve(engine, pv_by_month, ev, ac)
        indices = cmc_utils.evm_indices(baseline.bac, pv, ev, ac)
        self.write({
            "bac": baseline.bac, "pv": pv, "ev": ev, "ac": ac,
            **{key: currency.round(indices[key] or 0.0) for key in MONEY_FIELDS},
            **{key: indices[key] or 0.0 for key in INDEX_FIELDS},
            "rule_of_seven": cmc_utils.rule_of_seven(cvs), "out_of_control_count": len(breaches),
        })

    def _cmc_check_sources(self):
        """The job sheet of the project when the sources can be measured, else UserError."""
        if self.baseline_id.state != "done":
            raise UserError(self.env._("%s is not approved: measure against an approved baseline.", self.baseline_id.display_name))
        job_run = self.job_run_id
        if job_run.state != "done" or job_run.date_to != self.status_date:
            raise UserError(self.env._("The job cost run must be computed and end on the status date."))
        sheet = self.env["cmc.job.sheet"].search([("run_id", "=", job_run.id), ("project_id", "=", self.project_id.id)], limit=1)
        if not sheet:
            raise UserError(self.env._("%(run)s holds no cost sheet of %(job)s.", run=job_run.display_name, job=self.project_id.display_name))
        return sheet

    def _cmc_create_curve(self, engine, pv_by_month, ev, ac):
        """One row per month: cumulative PV/EV/AC (earlier months from the earlier reports) and monthly variance.
        Returns (out of control indexes, monthly variances up to the status month)."""
        currency = self.company_id.currency_id
        status_month = cmc_utils.month_start(self.status_date)
        # earlier computed reports of the same baseline: the latest of each month is its closing figure
        lineage, version = [], self.baseline_id
        while version:
            lineage.append(version.id)
            version = version.parent_id
        history = {
            cmc_utils.month_start(past.status_date): (past.ev, past.ac)
            for past in engine.search_fetch([
                ("state", "=", "done"), ("baseline_id", "in", lineage), ("project_id", "=", self.project_id.id),
                ("status_date", "<", status_month),
            ], ["status_date", "ev", "ac"], order="status_date, id")
        }
        months = cmc_utils.month_range(self.baseline_id.date_from, max(self.baseline_id.date_to, self.status_date))
        vals, cvs = [], []
        pv_cum = ev_cum = ac_cum = 0.0
        for month in months:
            forecast = month > status_month
            pv_cum += pv_by_month.get(month, 0.0)
            prev_ev, prev_ac = ev_cum, ac_cum
            if forecast:
                ev_cum = ac_cum = 0.0
            elif month == status_month:
                ev_cum, ac_cum = ev, ac
            elif month in history:
                ev_cum, ac_cum = history[month]
            month_ev, month_ac = (0.0, 0.0) if forecast else (ev_cum - prev_ev, ac_cum - prev_ac)
            cv = currency.round(month_ev - month_ac)
            if not forecast:
                cvs.append(cv)
            vals.append({
                "run_id": self.id, "date": month, "pv": pv_by_month.get(month, 0.0), "pv_cumulative": pv_cum,
                "ev_cumulative": ev_cum, "ac_cumulative": ac_cum, "ev": month_ev, "ac": month_ac, "cv": cv,
                "is_forecast": forecast, "out_of_control": False,
            })
        limit = self.control_limit
        breaches = cmc_utils.control_breaches(cvs, -limit, limit) if limit > 0 else []
        for index in breaches:
            vals[index]["out_of_control"] = True
        engine.env["cmc.evm.curve"].create(vals)
        return breaches, cvs


class CmcEvmTaskLine(models.Model):
    _name = "cmc.evm.task.line"
    _inherit = ["cmc.run.result.mixin"]
    _description = "EVM Task Line"
    _order = "run_id, task_id"

    run_id = fields.Many2one("cmc.evm.run", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="run_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="run_id.currency_id")
    task_id = fields.Many2one("project.task", required=True, ondelete="restrict")
    bac = fields.Monetary(string="BAC")
    percent_complete = fields.Float()
    pv = fields.Monetary(string="PV")
    ev = fields.Monetary(string="EV")
    sv = fields.Monetary(string="SV")


class CmcEvmCurve(models.Model):
    _name = "cmc.evm.curve"
    _inherit = ["cmc.run.result.mixin"]
    _description = "EVM Curve"
    _order = "run_id, date"

    run_id = fields.Many2one("cmc.evm.run", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="run_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="run_id.currency_id")
    date = fields.Date(required=True, help="First day of the month.")
    pv = fields.Monetary(string="PV")
    ev = fields.Monetary(string="EV")
    ac = fields.Monetary(string="AC")
    cv = fields.Monetary(string="CV")
    pv_cumulative = fields.Monetary(string="PV (cumulative)")
    ev_cumulative = fields.Monetary(string="EV (cumulative)")
    ac_cumulative = fields.Monetary(string="AC (cumulative)")
    is_forecast = fields.Boolean()
    out_of_control = fields.Boolean()
