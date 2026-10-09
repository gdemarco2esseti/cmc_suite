from collections import defaultdict

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Domain

from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE


class CmcJobCostRun(models.Model):
    _name = "cmc.job.cost.run"
    _inherit = ["cmc.run.mixin"]
    _description = "Job Cost Run"
    _order = "id desc"
    _check_company_auto = True

    _cmc_input_fields = (
        "name", "company_id", "date_from", "date_to", "project_ids", "labour_rate_source", "labour_driver_id",
        "overhead_source", "overhead_base", "overhead_rate", "overhead_driver_id", "overhead_center_ids",
    )
    _cmc_result_fields = ("sheet_ids", "labour_ids")

    name = fields.Char(required=True, default=lambda self: self.env._("Job costing"))
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    project_ids = fields.Many2many(
        "project.project", check_company=True, string="Jobs",
        help="Empty = every job of the company with cost lines or timesheets in the period.",
    )
    labour_rate_source = fields.Selection([("table", "Rate table"), ("registry", "Rate registry")], default="table", required=True)
    labour_driver_id = fields.Many2one(
        "cmc.cost.driver", string="Labour Rate Unit", check_company=True,
        help="Registry: the hourly rate to read. Table: when set, the center rates applied are published under it.",
    )
    overhead_source = fields.Selection([("manual", "Manual rate"), ("registry", "Rate registry")], default="manual", required=True)
    overhead_base = fields.Selection([("hours", "Labour hours"), ("direct_cost", "Prime cost")], default="hours", required=True)
    overhead_rate = fields.Float(digits=(16, 4), help="Per hour, or per currency unit of prime cost.")
    overhead_driver_id = fields.Many2one("cmc.cost.driver", string="Overhead Rate Unit", check_company=True)
    overhead_center_ids = fields.Many2many(
        "account.analytic.account", string="Overhead Centers", check_company=True,
        help="The centers whose indirect cost is compared with the overhead applied.",
    )
    sheet_ids = fields.One2many("cmc.job.sheet", "run_id")
    labour_ids = fields.One2many("cmc.job.sheet.labour", "run_id")
    overhead_applied = fields.Monetary(readonly=True, copy=False)
    overhead_actual = fields.Monetary(readonly=True, copy=False)
    overhead_variance = fields.Monetary(readonly=True, copy=False, help="Applied minus actual: above zero over-applied, below zero under-applied.")
    wip_total = fields.Monetary(string="Work in Progress", readonly=True, copy=False)
    completed_total = fields.Monetary(string="Completed Jobs", readonly=True, copy=False)

    @api.constrains("date_from", "date_to")
    def _check_dates_ordered(self):
        if any(run.date_to < run.date_from for run in self):
            raise ValidationError(self.env._("The end date cannot precede the start date."))

    @api.constrains("overhead_rate")
    def _check_overhead_rate(self):
        if any(run.overhead_rate < 0 for run in self):
            raise ValidationError(self.env._("The overhead rate cannot be negative."))

    @api.constrains("labour_rate_source", "labour_driver_id", "overhead_source", "overhead_driver_id", "overhead_base")
    def _check_registry_sources(self):
        for run in self:
            if run.labour_rate_source == "registry" and not run.labour_driver_id:
                raise ValidationError(self.env._("A registry labour rate needs its rate unit."))
            if run.overhead_source == "registry":
                if not run.overhead_driver_id:
                    raise ValidationError(self.env._("A registry overhead rate needs its rate unit."))
                if run.overhead_base != "hours":
                    raise ValidationError(self.env._("Registry overhead rates are per center hour: use the hours base."))

    def _cmc_reset_values(self):
        return {"overhead_applied": 0.0, "overhead_actual": 0.0, "overhead_variance": 0.0, "wip_total": 0.0, "completed_total": 0.0}

    # ---- engine ---------------------------------------------------------

    def _cmc_compute(self):
        self.ensure_one()
        # why sudo: lines, timesheets, employees, rates and projects are read inside the run company whoever presses Compute (AD-115);
        # results are written by the engine, the user's ACL on them is read-only
        engine = self.sudo()
        direct, hours, projects, objects = engine._cmc_read_lines()
        labour, published, read_rates = engine._cmc_labour(hours)
        overhead, overhead_rates = engine._cmc_registry_overhead(hours)
        for model, rates in (read_rates | overhead_rates).grouped("origin_model").items():
            self._cmc_record_usage(engine.env[model].browse(set(rates.mapped("origin_id"))))
        if published:
            self.env["cmc.rate"].sudo()._cmc_publish(self, published)
        sheets = engine._cmc_create_sheets(projects, objects, direct, labour, overhead)
        actual = engine._cmc_overhead_actual()
        applied = sum(sheets.mapped("overhead_applied"))
        wip = sheets.filtered(lambda s: s.job_status == "wip")
        done = sheets - wip
        self.write({
            "overhead_applied": applied,
            "overhead_actual": actual,
            "overhead_variance": self.company_id.currency_id.round(applied - actual),
            "wip_total": sum(wip.mapped("job_cost")),
            "completed_total": sum(done.mapped("job_cost")),
        })

    def _cmc_read_lines(self):
        """Direct costs {object: (material, other)} and hours rows at job x employee x month x center grain."""
        Line = self.env["account.analytic.line"]
        base = Domain([
            ("company_id", "=", self.company_id.id), ("date", ">=", self.date_from), ("date", "<=", self.date_to),
            ("cmc_excluded", "=", False),
        ])
        Object = self.env["cmc.cost.object"]
        if self.project_ids:
            objects = Object._cmc_ensure_objects(self.project_ids, company=self.company_id, object_type="project")
            base &= Domain("cmc_cost_object_id", "in", objects.ids)
        else:
            base &= Domain("cmc_cost_object_id.object_type", "=", "project")
        # timesheet lines carry the project: their hours are valued at the resource rate, never at the employee cost
        costs = Line._cmc_cost_read_group(base & Domain("project_id", "=", False), ["cmc_cost_object_id", "cmc_cost_element_id"])
        plan_fields = Line._get_plan_fnames()
        hours = Line._read_group(
            base & Domain("project_id", "!=", False), ["cmc_cost_object_id", "employee_id", "date:month", *plan_fields], ["unit_amount:sum"]
        )
        if not self.project_ids:
            objects = Object.browse(list({row[0].id for row in costs} | {row[0].id for row in hours}))
        projects = self.env["project.project"].browse(objects.mapped("res_id")).exists() if not self.project_ids else self.project_ids
        mp = self.env.ref("cmc_base.cmc_element_mp")
        direct = defaultdict(lambda: [0.0, 0.0])
        for obj, element, cost in costs:
            direct[obj.id][0 if element == mp else 1] += cost
        accounts = self.env["account.analytic.account"].browse(
            list({acc.id for row in hours for acc in row[3:-1] if acc})
        )
        accounts.mapped("cmc_center_type")  # one prefetch for the whole set
        rows = []
        for obj, employee, month, *rest in hours:
            qty, cols = rest[-1], rest[:-1]
            centers = [acc for acc in cols if acc and (acc.cmc_center_type or acc.cmc_is_period)]
            if not centers:
                raise UserError(self.env._("Hours of %(job)s have no cost center.", job=obj.display_name))
            rows.append((obj.id, employee, month, centers[0], qty))
        return direct, rows, projects, {o.res_id: o for o in objects}

    def _cmc_labour(self, hours):
        """({object id: {(center, category, month, origin): [hours, rate]}}, rates to publish, registry rates read)."""
        labour = defaultdict(dict)
        published, read_rates = {}, self.env["cmc.rate"]
        registry = self.labour_rate_source == "registry"
        if registry:
            lookup = self._cmc_registry_rates(hours, self.labour_driver_id)
        else:
            rates = self._cmc_table_rates(hours)
            self.env["hr.employee"].browse([r[1].id for r in hours if r[1]]).mapped("category_ids")  # one prefetch
        for obj_id, employee, month, center, qty in hours:
            if registry:
                rate = lookup[(center.id, month)]
                key, value = (center, self.env["hr.employee.category"], month, "registry"), rate.value
                read_rates |= rate
            else:
                row = self._cmc_pick_table_rate(rates, employee, center, month)
                key, value = (center, row.category_id, month, "table"), row.hourly_rate
                if not row.category_id and self.labour_driver_id:
                    published[(center.id, month)] = {"holder": center, "driver": self.labour_driver_id, "date": month, "value": row.hourly_rate}
            slot = labour[obj_id].setdefault(key, [0.0, value])
            slot[0] += qty
        return labour, list(published.values()), read_rates

    def _cmc_table_rates(self, hours):
        rates = self.env["cmc.job.rate"].search([
            ("company_id", "=", self.company_id.id), ("center_id", "in", [r[3].id for r in hours]), ("date", "in", list({r[2] for r in hours})),
        ])
        by_slot = defaultdict(dict)
        for rate in rates:
            by_slot[(rate.center_id.id, rate.date)][rate.category_id.id] = rate
        return by_slot

    def _cmc_pick_table_rate(self, rates, employee, center, month):
        slot = rates.get((center.id, month), {})
        matches = [cat for cat in employee.category_ids.ids if cat in slot]
        if len(matches) > 1:
            raise UserError(
                self.env._("%(employee)s matches several rate categories on %(center)s in %(month)s.",
                           employee=employee.display_name, center=center.display_name, month=month)
            )
        key = matches[0] if matches else False
        if key not in slot:
            raise UserError(self.env._("No hourly rate for %(center)s in %(month)s.", center=center.display_name, month=month))
        return slot[key]

    def _cmc_registry_rates(self, hours, driver):
        Rate = self.env["cmc.rate"]
        found = {}
        for center, month in sorted({(r[3], r[2]) for r in hours}, key=lambda cm: (cm[0].id, cm[1])):
            rate = Rate._cmc_lookup(center, driver, month, company=self.company_id)
            if not rate:
                raise UserError(
                    self.env._("No registry rate for %(center)s in %(month)s.", center=center.display_name, month=month)
                )
            found[(center.id, month)] = rate
        return found

    def _cmc_registry_overhead(self, hours):
        """({object id: overhead}, registry rates read); manual overhead is computed on the sheet."""
        if self.overhead_source != "registry":
            return {}, self.env["cmc.rate"]
        lookup = self._cmc_registry_rates(hours, self.overhead_driver_id)
        overhead = defaultdict(float)
        for obj_id, _employee, month, center, qty in hours:
            overhead[obj_id] += qty * lookup[(center.id, month)].value
        return overhead, self.env["cmc.rate"].union(*lookup.values())

    def _cmc_create_sheets(self, projects, objects, direct, labour, registry_overhead):
        currency = self.company_id.currency_id
        revenue = self.env["account.move.line"]._cmc_revenue_by_analytic(
            self.company_id, self.date_from, self.date_to, projects.account_id.ids
        )
        sheet_vals, labour_vals = [], []
        for project in projects.sorted("id"):
            obj = objects.get(project.id) or self.env["cmc.cost.object"]
            rows = labour.get(obj.id, {})
            hours = sum(slot[0] for slot in rows.values())
            material, other = direct.get(obj.id, (0.0, 0.0))
            labour_cost = sum(currency.round(slot[0] * slot[1]) for slot in rows.values())
            prime = material + other + labour_cost
            if self.overhead_source == "registry":
                base_qty, applied = hours, currency.round(registry_overhead.get(obj.id, 0.0))
            else:
                base_qty = hours if self.overhead_base == "hours" else prime
                applied = currency.round(self.overhead_rate * base_qty)
            job_cost = prime + applied
            sales = currency.round(revenue.get(project.account_id.id, 0.0))
            closed = project.cmc_job_closed and project.cmc_job_close_date and project.cmc_job_close_date <= self.date_to
            sheet_vals.append({
                "run_id": self.id, "project_id": project.id, "cost_object_id": obj.id, "hours": hours,
                "direct_material_cost": material, "other_direct_cost": other, "direct_cost": material + other,
                "labour_cost": labour_cost, "prime_cost": prime, "overhead_base_qty": base_qty, "overhead_applied": applied,
                "job_cost": job_cost, "revenue": sales, "margin": sales - job_cost,
                "margin_percent": (sales - job_cost) / sales * 100.0 if sales else 0.0,
                "job_status": "completed" if closed else "wip", "close_date": project.cmc_job_close_date if closed else False,
            })
            labour_vals.append([
                {"run_id": self.id, "center_id": center.id, "category_id": category.id, "date": month, "hours": slot[0],
                 "rate": slot[1], "amount": currency.round(slot[0] * slot[1]), "rate_origin": origin}
                for (center, category, month, origin), slot in rows.items()
            ])
        sheets = self.env["cmc.job.sheet"].create(sheet_vals)
        self.env["cmc.job.sheet.labour"].create([
            {**vals, "sheet_id": sheet.id} for sheet, group in zip(sheets, labour_vals, strict=True) for vals in group
        ])
        return sheets

    def _cmc_overhead_actual(self):
        Line = self.env["account.analytic.line"]
        total = 0.0
        for plan, centers in self.overhead_center_ids.grouped("plan_id").items():
            if not plan:
                continue
            col = plan._column_name()
            rows = Line._cmc_cost_read_group(Domain([
                ("company_id", "in", [False, self.company_id.id]), ("date", ">=", self.date_from), ("date", "<=", self.date_to),
                ("cmc_excluded", "=", False), ("cmc_directness", "=", "indirect"), (col, "in", centers.ids),
            ]))
            total += rows[0][0]
        return self.company_id.currency_id.round(total)


class CmcJobSheet(models.Model):
    _name = "cmc.job.sheet"
    _inherit = ["cmc.run.result.mixin"]
    _description = "Job Cost Sheet"
    _order = "run_id, project_id"
    _check_company_auto = True

    run_id = fields.Many2one("cmc.job.cost.run", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="run_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="run_id.currency_id")
    project_id = fields.Many2one("project.project", string="Job", required=True, ondelete="restrict", index=True)
    cost_object_id = fields.Many2one("cmc.cost.object", ondelete="restrict")
    hours = fields.Float()
    direct_material_cost = fields.Monetary()
    other_direct_cost = fields.Monetary()
    direct_cost = fields.Monetary()
    labour_cost = fields.Monetary()
    prime_cost = fields.Monetary()
    overhead_base_qty = fields.Float()
    overhead_applied = fields.Monetary()
    job_cost = fields.Monetary()
    revenue = fields.Monetary()
    margin = fields.Monetary()
    margin_percent = fields.Float()
    job_status = fields.Selection([("wip", "Work in Progress"), ("completed", "Completed")], required=True)
    close_date = fields.Date()
    labour_ids = fields.One2many("cmc.job.sheet.labour", "sheet_id")

    def write(self, vals):
        # why: a user without write rights gets the ACL answer, not the engine-only one
        self.check_access("write")
        return super().write(vals)


class CmcJobSheetLabour(models.Model):
    _name = "cmc.job.sheet.labour"
    _inherit = ["cmc.run.result.mixin"]
    _description = "Job Cost Sheet Labour"
    _order = "sheet_id, date, center_id"

    run_id = fields.Many2one("cmc.job.cost.run", required=True, ondelete="cascade", index=True)
    sheet_id = fields.Many2one("cmc.job.sheet", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="run_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="run_id.currency_id")
    center_id = fields.Many2one("account.analytic.account", required=True, ondelete="restrict")
    category_id = fields.Many2one("hr.employee.category", ondelete="restrict")
    date = fields.Date(required=True, help="First day of the month.")
    hours = fields.Float()
    rate = fields.Float(digits=(16, 4))
    amount = fields.Monetary()
    rate_origin = fields.Selection([("table", "Rate table"), ("registry", "Rate registry")], required=True)

    def write(self, vals):
        # why: a user without write rights gets the ACL answer, not the engine-only one
        self.check_access("write")
        return super().write(vals)
