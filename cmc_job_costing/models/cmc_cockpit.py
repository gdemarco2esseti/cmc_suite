from odoo import api, models


class CmcCockpit(models.TransientModel):
    _inherit = "cmc.cockpit"

    @api.model
    def _cmc_dashboard_cards(self, period):
        return super()._cmc_dashboard_cards(period) + self._cmc_dashboard_job_card(period)

    @api.model
    def _cmc_dashboard_alerts(self, period):
        return super()._cmc_dashboard_alerts(period) + self._cmc_dashboard_job_evm_alerts(period)

    @api.model
    def _cmc_dashboard_job_card(self, period):
        Run = self.env["cmc.job.cost.run"]
        if not Run.has_access("read"):
            return []
        run = self._cmc_dashboard_latest_run(Run, ["name", "date_from", "date_to", "wip_total", "completed_total"], "date_to desc, id desc")
        figures = run and [
            self._cmc_dashboard_figure("job_costing.wip", self.env._("Work in progress"), run["wip_total"], "monetary"),
            self._cmc_dashboard_figure("job_costing.completed", self.env._("Completed jobs"), run["completed_total"], "monetary"),
        ]
        return [self._cmc_dashboard_card(
            "job_costing.jobs", "costing", 70, self.env._("Job costing"), run, period, figures, "cmc_job_costing.action_cmc_job_cost_run")]

    @api.model
    def _cmc_dashboard_job_evm_alerts(self, period):
        Evm = self.env["cmc.evm.run"]
        if not Evm.has_access("read"):
            return []
        base = self._cmc_dashboard_company_domain(Evm) + [("state", "=", "done")] + self._cmc_dashboard_period_domain(period, "status_date")
        out_of_control = base + [("out_of_control_count", ">", 0)]
        # why: mutually exclusive with the first alert, so a report is never counted twice
        low_cpi = base + [("out_of_control_count", "=", 0), ("cpi", "<", 1)]
        action = "cmc_job_costing.action_cmc_evm_run"
        # one query: inside the union, a report without out-of-control items can only be a low-CPI one
        counts = dict(Evm._read_group(
            base + ["|", ("out_of_control_count", ">", 0), ("cpi", "<", 1)], ["out_of_control_count"], ["__count"]))
        danger = sum(n for ooc, n in counts.items() if ooc)
        low = counts.get(0, 0)
        alerts = []
        if danger:
            alerts.append(self._cmc_dashboard_alert(
                "job_costing.out_of_control", "danger", self.env._("%s EVM status reports out of control", danger) if danger > 1 else self.env._("1 EVM status report out of control"),
                danger, action, out_of_control, card="job_costing.jobs"))
        if low:
            alerts.append(self._cmc_dashboard_alert(
                "job_costing.cpi_below_one", "warning", self.env._("%s EVM status reports with CPI < 1", low) if low > 1 else self.env._("1 EVM status report with CPI < 1"),
                low, action, low_cpi, card="job_costing.jobs"))
        return alerts
