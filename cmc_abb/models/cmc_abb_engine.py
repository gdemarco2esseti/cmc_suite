import math
from collections import defaultdict

from odoo import models
from odoo.exceptions import UserError
from odoo.fields import Domain
from odoo.tools import float_compare, float_round

from odoo.addons.cmc_base.models.cmc_utils import month_range

COMMITTED = ("committed_fixed", "committed_step")
RESOURCE_FIELDS = (
    "name", "spending_profile", "unit_size", "unit_cost", "committed_units", "driver_id", "center_id", "budget_element_id",
    "cost_element_ids", "idle_responsibility_id", "nonproductive_responsibility_id",
)


def _supply(res, need, months):
    """Supply of one resource for a `need` (resource-driver units) over `months`, by spending profile."""
    flexible = res["spending_profile"] == "flexible"
    # why: a committed unit size and cost are annual; a flexible one is per unit bought
    scale = 1.0 if flexible else months / 12.0
    capacity, unit_cost = res["unit_size"] * scale, res["unit_cost"] * scale
    required = need / capacity
    if flexible:
        units = required
    elif res["spending_profile"] == "committed_step":
        # why: 6-digit round first, 1,600 h on 800 h units is exactly 2 and float noise must not add a lump
        units = float(math.ceil(float_round(required, precision_digits=6)))
    else:
        units = res["committed_units"]
    committed = res["committed_units"]
    supply_qty = units * capacity
    return {
        "required_units": required, "supply_units": units, "supply_qty": supply_qty,
        "unused_qty": 0.0 if flexible else max(supply_qty - need, 0.0),
        "shortage_units": {"committed_step": max(units - committed, 0.0), "committed_fixed": max(required - committed, 0.0)}.get(
            res["spending_profile"], 0.0
        ),
        "excess_units": max(committed - units, 0.0) if res["spending_profile"] == "committed_step" else 0.0,
        "budgeted_cost": units * unit_cost,
    }


class CmcAbbRun(models.Model):
    _inherit = "cmc.abb.run"

    def _cmc_compute(self):
        self.ensure_one()
        source, target = self.source_version_id, self.target_version_id
        self._cmc_check_sources()
        months = len(source._cmc_months())
        volume = self._cmc_planned_volume()
        bill, usage, overrides, states = self._cmc_read_inputs()
        activities = self._cmc_read_activities(bill, usage, overrides)
        demand = self._cmc_activity_demand(volume, bill, overrides, activities)
        uses = defaultdict(dict)
        for line in usage:
            uses[line["activity_id"]][line["resource_id"]] = line["usage_per_unit"]
        missing = [activities[a]["name"] for a in activities if demand[a] > 0 and not uses[a]]
        if missing:
            raise UserError(self.env._("Activities %s have demand but no resource usage line.", ", ".join(missing)))
        resources = self._cmc_read_resources(uses, states, bool(target))
        resource_demand = defaultdict(float)
        for act_id, per_resource in uses.items():
            for res_id, per_unit in per_resource.items():
                resource_demand[res_id] += demand[act_id] * per_unit
        nonproductive = defaultdict(float)
        for state in states:
            nonproductive[state["resource_id"]] += state["quantity"]
        supply = {
            res_id: _supply(res, resource_demand[res_id] + nonproductive[res_id], months) for res_id, res in resources.items()
        }
        for res_id, res in resources.items():
            sup = supply[res_id]
            need = resource_demand[res_id] + nonproductive[res_id]
            # why: with a shortage the cost is spread over the need, so nothing is assigned beyond the budget;
            # an idle committed resource (no need) still has a rate over its supply
            divisor = max(sup["supply_qty"], need)
            sup["rate"] = sup["budgeted_cost"] / divisor if divisor else 0.0
            sup["assigned_cost"] = resource_demand[res_id] * sup["rate"]
            sup["unused_cost"] = sup["budgeted_cost"] - sup["assigned_cost"]
        rates = self._cmc_activity_rates(uses, resources, supply, demand)
        capacity = self._cmc_constraints(uses, resources, supply, demand, resource_demand, nonproductive)
        base = self._cmc_baseline(supply, demand)
        calibration_qty, costed = self._cmc_calibration(months)
        warnings = self._cmc_warnings(activities, demand, calibration_qty, costed, volume)

        Result = self.env["cmc.abb.activity.result"].sudo()  # why sudo: results are read-only for users
        run = {"run_id": self.id}
        Result.create([
            {
                **run, "activity_id": a, "driver_id": activities[a]["driver_id"], "demand_qty": demand[a],
                "calibration_qty": calibration_qty.get(a, 0.0) if self.calibration_run_id else 0.0,
                "deviation_percent": warnings["deviation"].get(a, 0.0), **rates[a], "assigned_cost": demand[a] * rates[a]["rate"],
                "capacity_qty": capacity["activity"][a][0], "constraining_resource_id": capacity["activity"][a][1],
            }
            for a in activities
        ])
        unit_costs = self._cmc_object_costs(bill, rates)
        self.env["cmc.abb.object.result"].sudo().create([
            {**run, "cost_object_id": obj, "planned_qty": qty, "unit_cost": unit_costs.get(obj, 0.0), "amount": qty * unit_costs.get(obj, 0.0)}
            for obj, qty in volume.items()
        ])
        resource_vals = [
            {
                **run, "resource_id": res_id, "driver_id": res["driver_id"], "center_id": res["center_id"],
                "element_id": res["element_id"], "spending_profile": res["spending_profile"],
                "demand_qty": resource_demand[res_id], "nonproductive_qty": nonproductive[res_id],
                "committed_units": res["committed_units"], **supply[res_id],
                "delta_supply_units": supply[res_id]["supply_units"] - base["supply"].get(res_id, 0.0) if base["run"] else 0.0,
                "delta_cost": supply[res_id]["budgeted_cost"] - base["cost"].get(res_id, 0.0) if base["run"] else 0.0,
            }
            for res_id, res in resources.items()
        ]
        self.env["cmc.abb.resource.result"].sudo().create(resource_vals)
        state_vals = self._cmc_capacity_states(resources, supply, resource_demand, states)
        self.env["cmc.abb.capacity.state"].sudo().create(state_vals)
        self.env["cmc.abb.warning"].sudo().create([{**run, **vals} for vals in warnings["rows"]])

        total = sum(sup["budgeted_cost"] for sup in supply.values())
        assigned = sum(sup["assigned_cost"] for sup in supply.values())
        if float_compare(sum(vals["cost"] for vals in state_vals), total, precision_rounding=self.currency_id.rounding):
            raise UserError(self.env._("The capacity states of the run do not add up to its budgeted cost."))
        self.write({
            "total_budgeted_cost": total, "total_assigned_cost": assigned, "total_unused_cost": total - assigned,
            "extrapolation_warning": bool(warnings["rows"]), "bottleneck_resource_id": capacity["bottleneck"],
            "mix_capacity_factor": capacity["factor"], "delta_cost": total - base["total"],
            "predicted_cost": sum(demand[a] * base["rate"].get(a, 0.0) for a in demand) if base["run"] else 0.0,
        })
        if target:
            self._cmc_write_budget(target, resources, supply)
            self._cmc_publish_rates(target, source._cmc_months())

    # ---- sources and inputs ------------------------------------------------------

    def _cmc_check_sources(self):
        source, target = self.source_version_id, self.target_version_id
        if target:
            if target == source:
                raise UserError(self.env._("The budget version cannot be the version that holds the planned volumes."))
            if target.state != "draft":
                raise UserError(self.env._("%s is approved: the budget lines go into a draft version.", target.display_name))
            if (target.date_from, target.date_to) != (source.date_from, source.date_to):
                raise UserError(self.env._("%s must cover the same span as %s.", target.display_name, source.display_name))
            other = self.sudo().search_count([("target_version_id", "=", target.id), ("state", "!=", "draft"), ("id", "!=", self.id)], limit=1)
            if other:
                raise UserError(self.env._("%s already holds a computed ABB run: reset it first.", target.display_name))
        # raises unless the origins are approved / computed, and locks them against a reset under this run
        for origin in (source, self.calibration_run_id, self.baseline_run_id):
            if origin:
                self._cmc_record_usage(origin)

    def _cmc_planned_volume(self):
        """{cost object id: planned quantity of the output measure in the source version}."""
        # why sudo: planned facts of the run company are engine input, whoever presses Compute
        groups = self.env["cmc.measure.quantity"].sudo()._read_group(
            Domain([("version_id", "=", self.source_version_id.id), ("driver_id", "=", self.output_measure_id.id)])
            & Domain("cost_object_id", "!=", False),
            ["cost_object_id"], ["quantity:sum"],
        )
        volume = {obj.id: qty for obj, qty in groups if qty}
        if not volume:
            raise UserError(
                self.env._("%(version)s has no planned quantity of %(measure)s.",
                           version=self.source_version_id.display_name, measure=self.output_measure_id.display_name)
            )
        return volume

    def _cmc_read_inputs(self):
        bill = self.bill_line_ids.read(["cost_object_id", "activity_id", "qty_per_unit"], load=False)
        usage = self.usage_line_ids.read(["activity_id", "resource_id", "usage_per_unit"], load=False)
        overrides = {o["activity_id"]: o["quantity"] for o in self.driver_override_ids.read(["activity_id", "quantity"], load=False)}
        states = self.time_state_ids.read(["resource_id", "mode", "quantity", "responsibility_id"], load=False)
        return bill, usage, overrides, states

    def _cmc_read_activities(self, bill, usage, overrides):
        ids = {b["activity_id"] for b in bill} | {u["activity_id"] for u in usage} | set(overrides)
        activities = {a["id"]: a for a in self.env["cmc.abc.activity"].browse(sorted(ids)).read(["name", "driver_id"], load=False)}
        undriven = [a["name"] for a in activities.values() if not a["driver_id"]]
        if undriven:
            raise UserError(self.env._("Activities %s have no activity driver.", ", ".join(undriven)))
        return activities

    def _cmc_activity_demand(self, volume, bill, overrides, activities):
        demand = dict.fromkeys(activities, 0.0)
        for line in bill:
            demand[line["activity_id"]] += volume.get(line["cost_object_id"], 0.0) * line["qty_per_unit"]
        demand.update(overrides)  # a planned driver quantity replaces the computed demand
        return demand

    def _cmc_read_resources(self, uses, states, budget):
        ids = {res for per_resource in uses.values() for res in per_resource} | {s["resource_id"] for s in states}
        resources = {r["id"]: r for r in self.env["cmc.abc.resource"].browse(sorted(ids)).read(list(RESOURCE_FIELDS), load=False)}
        for res in resources.values():
            if not res["driver_id"]:
                raise UserError(self.env._("Resource %s has no unit (driver): there is no rate without it.", res["name"]))
            # fallback: the single cost element of the resource
            res["element_id"] = res["budget_element_id"] or (res["cost_element_ids"][0] if len(res["cost_element_ids"]) == 1 else False)
            if budget and not res["element_id"]:
                raise UserError(self.env._("Resource %s has no budget cost element: set one to write budget lines.", res["name"]))
        return resources

    # ---- north -> south ------------------------------------------------------------

    def _cmc_activity_rates(self, uses, resources, supply, demand):
        rates = {}
        for act_id in demand:
            fixed = sum(per_unit * supply[res]["rate"] for res, per_unit in uses[act_id].items()
                        if resources[res]["spending_profile"] in COMMITTED)
            variable = sum(per_unit * supply[res]["rate"] for res, per_unit in uses[act_id].items()
                           if resources[res]["spending_profile"] not in COMMITTED)
            rates[act_id] = {"rate": fixed + variable, "fixed_rate": fixed, "variable_rate": variable}
        return rates

    def _cmc_object_costs(self, bill, rates):
        unit_costs = defaultdict(float)
        for line in bill:
            unit_costs[line["cost_object_id"]] += line["qty_per_unit"] * rates[line["activity_id"]]["rate"]
        return unit_costs

    def _cmc_constraints(self, uses, resources, supply, demand, resource_demand, nonproductive):
        """CAP-06: per activity the first constraining committed resource, per run the bottleneck of the mix."""
        activity = {}
        for act_id in demand:
            best = (0.0, False)
            for res_id, per_unit in uses[act_id].items():
                if resources[res_id]["spending_profile"] not in COMMITTED:
                    continue
                others = resource_demand[res_id] - demand[act_id] * per_unit
                available = max(supply[res_id]["supply_qty"] - nonproductive[res_id] - others, 0.0) / per_unit
                if not best[1] or available < best[0]:
                    best = (available, res_id)
            activity[act_id] = best
        bottleneck, factor = False, 0.0
        for res_id, res in resources.items():
            if res["spending_profile"] in COMMITTED and resource_demand[res_id] > 0:
                scale = max(supply[res_id]["supply_qty"] - nonproductive[res_id], 0.0) / resource_demand[res_id]
                if not bottleneck or scale < factor:
                    bottleneck, factor = res_id, scale
        return {"activity": activity, "bottleneck": bottleneck, "factor": factor}

    def _cmc_capacity_states(self, resources, supply, resource_demand, states):
        """CAP-05: supplied capacity = productive + non-productive (per mode) + idle, each costed at the resource rate."""
        rows = defaultdict(float)  # (resource, state, mode, responsibility) -> quantity
        for res_id, res in resources.items():
            rows[(res_id, "productive", False, False)] = resource_demand[res_id]
            if res["spending_profile"] in COMMITTED:
                need = resource_demand[res_id] + sum(s["quantity"] for s in states if s["resource_id"] == res_id)
                rows[(res_id, "idle", False, res["idle_responsibility_id"])] = max(supply[res_id]["supply_qty"] - need, 0.0)
        for state in states:
            owner = state["responsibility_id"] or resources[state["resource_id"]]["nonproductive_responsibility_id"]
            rows[(state["resource_id"], "nonproductive", state["mode"], owner)] += state["quantity"]
        return [
            {
                "run_id": self.id, "resource_id": res_id, "state": state, "mode": mode, "quantity": qty,
                "cost": qty * supply[res_id]["rate"], "responsibility_id": owner,
            }
            for (res_id, state, mode, owner), qty in rows.items() if qty
        ]

    # ---- calibration (BUD-06 extrapolation warnings) ----------------------------------

    def _cmc_calibration(self, months):
        """({activity id: calibration run used quantity scaled to the span}, {cost object ids costed by the run})."""
        calibration = self.calibration_run_id
        if not calibration:
            return {}, set()
        scale = months / len(month_range(calibration.date_from, calibration.date_to)) if calibration.date_from and calibration.date_to else 1.0
        # why sudo: certified results of the calibration run, whoever presses Compute
        used = self.env["cmc.abc.activity.cost"].sudo()._read_group([("run_id", "=", calibration.id)], ["activity_id"], ["used_qty:sum"])
        costed = self.env["cmc.abc.result"].sudo()._read_group([("run_id", "=", calibration.id)], ["cost_object_id"])
        return {act.id: qty * scale for act, qty in used}, {obj.id for (obj,) in costed}

    def _cmc_warnings(self, activities, demand, calibration_qty, costed, volume):
        deviation, rows = {}, []
        if not self.calibration_run_id:
            return {"deviation": deviation, "rows": rows}
        for act_id in activities:
            calibrated = calibration_qty.get(act_id, 0.0)
            deviation[act_id] = (demand[act_id] - calibrated) / calibrated * 100.0 if calibrated else 0.0
            if (calibrated and abs(deviation[act_id]) > self.calibration_tolerance) or (not calibrated and demand[act_id] > 0):
                rows.append({
                    "warning_type": "volume", "activity_id": act_id, "calibration_qty": calibrated, "planned_qty": demand[act_id],
                    "deviation_percent": deviation[act_id],
                })
        rows += [
            {"warning_type": "mix", "cost_object_id": obj, "planned_qty": qty} for obj, qty in volume.items() if obj not in costed
        ]
        return {"deviation": deviation, "rows": rows}

    # ---- FCS-03 baseline ------------------------------------------------------------------

    def _cmc_baseline(self, supply, demand):
        base = self.baseline_run_id
        if not base:
            return {"run": False, "supply": {}, "cost": {}, "rate": {}, "total": 0.0}
        sudo = base.sudo()
        rows = sudo.resource_result_ids.read(["resource_id", "supply_units", "budgeted_cost"], load=False)
        return {
            "run": True, "total": base.total_budgeted_cost,
            "supply": {r["resource_id"]: r["supply_units"] for r in rows}, "cost": {r["resource_id"]: r["budgeted_cost"] for r in rows},
            "rate": {r["activity_id"]: r["rate"] for r in sudo.activity_result_ids.read(["activity_id", "rate"], load=False)},
        }

    # ---- target version: budget lines and prospective rates ------------------------------

    def _cmc_write_budget(self, target, resources, supply):
        """One budget batch per resource: centre x element x behavior, never an analytic line (AD-110)."""
        for res_id, res in resources.items():
            cost = supply[res_id]["budgeted_cost"]
            if not cost:
                continue
            target._cmc_budget_add(
                {
                    "kind": "cost", "center_id": res["center_id"], "element_id": res["element_id"],
                    "behavior": "fixed" if res["spending_profile"] in COMMITTED else "variable", "driver_id": res["driver_id"],
                    "abb_run_id": self.id, "abb_resource_id": res_id,
                },
                target.currency_id.round(cost / target.currency_rate),
            )

    def _cmc_publish_rates(self, target, months):
        # why sudo: rates are written by the engine; users have no create right on the registry for plans
        rates = self.env["cmc.rate"].sudo()._cmc_publish(
            target,
            [{"holder": holder, "driver": driver, "component": comp, "date": month, "value": value}
             for holder, driver, comp, value in self._cmc_rate_specs() for month in months],
        )
        rates.abb_run_id = self
