from collections import defaultdict

from odoo import models
from odoo.exceptions import UserError
from odoo.fields import Domain
from odoo.tools import float_compare, float_is_zero, float_round

from odoo.addons.cmc_base.models.cmc_utils import month_start, qty_rounding, spread

from .cmc_abc import DRIVER_QTY_ROUNDING

ALIGNED_PERCENT = 1.0  # |distortion| below this: ABC and traditional costing agree
PERCENT_TOLERANCE = 0.01


def _topological(nodes, arcs):
    """(order, nodes left in a cycle): Kahn on plain ids, ties broken by id so the result is stable."""
    incoming = dict.fromkeys(nodes, 0)
    out = defaultdict(list)
    for src, dst in arcs:
        out[src].append(dst)
        incoming[dst] += 1
    ready = sorted(node for node, count in incoming.items() if not count)
    order = []
    while ready:
        node = ready.pop(0)
        order.append(node)
        for dst in out[node]:
            incoming[dst] -= 1
            if not incoming[dst]:
                ready.append(dst)
        ready.sort()
    return order, [node for node, count in incoming.items() if count]


def _pair():
    return [0.0, 0.0]  # [fixed, variable]


class CmcAbcRun(models.Model):
    _inherit = "cmc.abc.run"

    # ---- ledger reads (AD-114: grouped, never per line) --------------------

    def _cmc_ledger_domains(self):
        """(cost base of the run, stamped direct lines)."""
        base = Domain([("cmc_excluded", "=", False), ("company_id", "in", [False] + self.company_id.ids)])
        if self.date_from:
            base &= Domain("date", ">=", self.date_from)
        if self.date_to:
            base &= Domain("date", "<=", self.date_to)
        stamped = Domain([("cmc_directness", "=", "direct"), ("cmc_cost_object_id", "!=", False)])
        return base, stamped

    def _cmc_ledger_pools(self, resources):
        """({(resource id, element id): [fixed, variable]}, in-scope cost, unassigned cost).

        A pool is the cost of center x element x behavior, without the lines tagged to an activity
        (a direct charge of the activity) and the stamped direct ones (traced to their object)."""
        # why sudo: a certified pool must not depend on who presses Compute; the company domain is the scope
        Line = self.env["account.analytic.line"].sudo()
        base, stamped = self._cmc_ledger_domains()
        pooled = Domain("cmc_activity_id", "=", False) & ~stamped
        by_center = defaultdict(list)
        for res in resources.read(["center_id", "cost_element_ids"], load=False):
            by_center[res["center_id"]].append(res)
        pools = defaultdict(_pair)
        in_scope = unassigned = 0.0
        for plan, centers in resources.center_id.sudo().grouped("plan_id").items():
            col = plan._column_name()
            scope = base & Domain(col, "in", centers.ids)
            in_scope += Line._cmc_cost_read_group(scope)[0][0]
            for center, element, behavior, cost in Line._cmc_cost_read_group(
                scope & pooled, [col, "cmc_cost_element_id", "cmc_behavior"]
            ):
                covering = [r for r in by_center[center.id] if not r["cost_element_ids"] or element.id in r["cost_element_ids"]]
                if len(covering) > 1:
                    raise UserError(
                        self.env._(
                            "Several resources of %(center)s cover the same cost: it would be counted twice.",
                            center=center.display_name,
                        )
                    )
                if covering:
                    pools[(covering[0]["id"], element.id)][behavior == "variable"] += cost
                else:
                    unassigned += cost
            if not self.trace_direct:
                unassigned += Line._cmc_cost_read_group(scope & Domain("cmc_activity_id", "=", False) & stamped)[0][0]
        return pools, in_scope, unassigned

    def _cmc_quantities(self, driver, object_ids=None):
        """{cost object id: actual quantity of the driver in the period}."""
        if not (driver and self.date_from and self.date_to):
            return {}
        extra = [("driver_id", "=", driver.id), ("cost_object_id", "!=", False)]
        if object_ids is not None:
            extra.append(("cost_object_id", "in", list(object_ids)))
        # why sudo: facts of the run company are engine input, whoever presses Compute
        groups = self.env["cmc.measure.quantity"].sudo()._read_group(
            self._cmc_fact_domain(extra), ["cost_object_id"], ["quantity:sum"]
        )
        return {obj.id: qty for obj, qty in groups}

    # ---- stage 1: resources and tags -> activities --------------------------

    def _cmc_stage1(self, rounding):
        """(flows, {activity id: [fixed, variable]}, in-scope cost, unassigned cost)."""
        lines = self.resource_line_ids.read(["resource_id", "activity_id", "percent", "quantity"], load=False)
        resources = self.resource_line_ids.resource_id
        basis = {r["id"]: r for r in resources.read(["name", "driver_basis", "center_id"], load=False)}
        weights = defaultdict(lambda: defaultdict(float))
        for ln in lines:
            kind = "percent" if basis[ln["resource_id"]]["driver_basis"] == "percent" else "quantity"
            weights[ln["resource_id"]][ln["activity_id"]] += ln[kind]
        for res_id, shares in weights.items():
            total = sum(shares.values())
            percent = basis[res_id]["driver_basis"] == "percent"
            if (abs(total - 100.0) > PERCENT_TOLERANCE) if percent else float_is_zero(total, precision_rounding=DRIVER_QTY_ROUNDING):
                raise UserError(
                    self.env._(
                        "Resource %(name)s: its drivers add up to %(total)s, they must %(rule)s.",
                        name=basis[res_id]["name"], total=total,
                        rule=self.env._("total 100%") if percent else self.env._("total more than zero"),
                    )
                )
        pools, in_scope, unassigned = self._cmc_ledger_pools(resources)
        stage1 = defaultdict(_pair)
        flows = []
        for (res_id, element_id), (fixed, variable) in pools.items():
            fixed_shares = spread(fixed, weights[res_id], rounding)
            variable_shares = spread(variable, weights[res_id], rounding)
            for act_id in weights[res_id]:
                f, v = fixed_shares[act_id], variable_shares[act_id]
                if f or v:
                    stage1[act_id][0] += f
                    stage1[act_id][1] += v
                    flows.append(self._cmc_flow(
                        "resource", f, v, source_resource_id=res_id, cost_element_id=element_id, target_activity_id=act_id,
                        center_id=basis[res_id]["center_id"],
                    ))
        # the v1 tag: a direct charge of the activity, kept apart from the resource pools
        tags = defaultdict(_pair)
        for activity, center, element, behavior, cost in self.env["account.analytic.line"]._cmc_activity_pool_groups(
            self.date_from, self.date_to, self.company_id
        ):
            tags[(activity.id, center.id, element.id)][behavior == "variable"] += cost
        for (act_id, center_id, element_id), pair in tags.items():
            f, v = (float_round(x, precision_rounding=rounding) for x in pair)
            stage1[act_id][0] += f
            stage1[act_id][1] += v
            flows.append(self._cmc_flow("tag", f, v, cost_element_id=element_id, target_activity_id=act_id, center_id=center_id))
        return flows, stage1, in_scope, unassigned

    def _cmc_flow(self, stage, fixed, variable, **vals):
        return {
            "run_id": self.id, "stage": stage, "fixed_amount": fixed, "variable_amount": variable,
            "amount": fixed + variable, **vals,
        }

    def _cmc_traced(self, rounding):
        """({cost object id: [fixed, variable]}, flows): stamped direct lines straight to their object."""
        if not self.trace_direct:
            return {}, []
        base, stamped = self._cmc_ledger_domains()
        by_center = defaultdict(_pair)
        groups = self.env["account.analytic.line"].sudo()._cmc_cost_by_center(
            base & Domain("cmc_activity_id", "=", False) & stamped, ["cmc_cost_object_id", "cmc_center_id", "cmc_behavior"]
        )
        for obj, center, behavior, cost in groups:
            by_center[(obj.id, center.id)][behavior == "variable"] += cost
        traced = defaultdict(_pair)
        for (oid, _center_id), pair in by_center.items():
            pair[:] = [float_round(x, precision_rounding=rounding) for x in pair]
            traced[oid][0] += pair[0]
            traced[oid][1] += pair[1]
        flows = [self._cmc_flow("trace", f, v, target_object_id=oid, center_id=center_id) for (oid, center_id), (f, v) in by_center.items()]
        return traced, flows

    # ---- stages 2-3: activities -> activities -> objects -> objects ---------

    def _cmc_propagate_activities(self, edges, stage1, caps, rounding):
        """Each activity, once its suppliers are done, spreads 100% of its cost over its edges.
        Returns (activity cost rows, flows, {object id: components}, activities by id)."""
        qty_step = qty_rounding(self.env)
        out = defaultdict(list)
        for edge in edges:
            if edge["activity_id"]:
                out[edge["activity_id"]].append(edge)
        arcs = [(e["activity_id"], e["target_activity_id"]) for e in edges if e["activity_id"] and e["target_activity_id"]]
        nodes = set(stage1) | set(out) | set(caps) | {dst for _src, dst in arcs}
        order, cyclic = _topological(nodes, arcs)
        acts = {a["id"]: a for a in self.env["cmc.abc.activity"].browse(sorted(nodes)).read(
            ["name", "driver_id", "cost_level", "parent_id", "is_sustaining"], load=False)}
        if cyclic:
            raise UserError(
                self.env._(
                    "The activities %(names)s reassign cost to each other in a loop: reciprocal allocation is not supported.",
                    names=", ".join(acts[a]["name"] for a in cyclic),
                )
            )
        sender_sustaining = [acts[a]["name"] for a in out if acts[a]["is_sustaining"]]
        if sender_sustaining:
            raise UserError(
                self.env._(
                    "Business-sustaining activities (%(names)s) cannot be charged to cost objects.",
                    names=", ".join(sender_sustaining),
                )
            )
        driver_types = {d["id"]: d["measure_type"] for d in self.env["cmc.cost.driver"].browse(
            {a["driver_id"] for a in acts.values() if a["driver_id"]}).read(["measure_type"], load=False)}
        received = defaultdict(_pair)
        acc = defaultdict(lambda: defaultdict(_pair))  # object id -> {component: [fixed, variable]}
        rows, flows = [], []
        sustaining_object = None
        for act_id in order:
            info = acts[act_id]
            own, got = stage1.get(act_id, (0.0, 0.0)), received.get(act_id, (0.0, 0.0))
            fixed = float_round(own[0] + got[0], precision_rounding=rounding)
            variable = float_round(own[1] + got[1], precision_rounding=rounding)
            row = {
                "run_id": self.id, "activity_id": act_id, "process_id": info["parent_id"], "cost_level": info["cost_level"],
                "driver_id": info["driver_id"], "driver_type": driver_types.get(info["driver_id"]),
                "resource_cost": float_round(sum(own), precision_rounding=rounding),
                "received_cost": float_round(sum(got), precision_rounding=rounding),
                "total_cost": fixed + variable, "fixed_cost": fixed, "variable_cost": variable,
                "used_qty": 0.0, "practical_capacity": caps.get(act_id, 0.0), "rate": 0.0, "fixed_rate": 0.0,
                "variable_rate": 0.0, "charged_cost": 0.0, "unused_cost": 0.0,
            }
            rows.append(row)
            edges_a = out.get(act_id, [])
            used = sum(e["driver_qty"] for e in edges_a)
            source = {"source_activity_id": act_id}
            if info["is_sustaining"]:
                if fixed or variable:
                    sustaining_object = sustaining_object or self._cmc_sustaining_object()
                    flows.append(self._cmc_flow("sustaining", fixed, variable, target_object_id=sustaining_object.id, **source))
                continue
            cap = caps.get(act_id)
            if float_is_zero(used, precision_rounding=DRIVER_QTY_ROUNDING):
                if cap and fixed:
                    # capacity supplied and not used is idle capacity, not unallocated cost
                    fixed_rate = fixed / cap
                    row.update(fixed_rate=fixed_rate, rate=fixed_rate, unused_cost=fixed)
                    flows.append(self._cmc_flow("unused", fixed, 0.0, driver_qty=cap, rate=fixed_rate, **source))
                    fixed = 0.0
                if fixed or variable:
                    flows.append(self._cmc_flow("unallocated", fixed, variable, **source))
                continue
            if cap and float_compare(used, cap, precision_rounding=qty_step) > 0:
                raise UserError(
                    self.env._(
                        "Activity %(name)s is used for %(used)s but its practical capacity is %(capacity)s.",
                        name=info["name"], used=used, capacity=cap,
                    )
                )
            charged_fixed = float_round(fixed * used / cap, precision_rounding=rounding) if cap else fixed
            unused = float_round(fixed - charged_fixed, precision_rounding=rounding)
            fixed_rate, variable_rate = fixed / (cap or used), variable / used
            row.update(
                used_qty=used, rate=fixed_rate + variable_rate, fixed_rate=fixed_rate, variable_rate=variable_rate,
                charged_cost=charged_fixed + variable, unused_cost=unused,
            )
            shares = {e["id"]: e["driver_qty"] for e in edges_a}
            fixed_shares = spread(charged_fixed, shares, rounding)
            variable_shares = spread(variable, shares, rounding)
            for edge in edges_a:
                f, v = fixed_shares[edge["id"]], variable_shares[edge["id"]]
                extra = {"driver_qty": edge["driver_qty"], "rate": fixed_rate + variable_rate}
                if edge["target_activity_id"]:
                    received[edge["target_activity_id"]][0] += f
                    received[edge["target_activity_id"]][1] += v
                    flows.append(self._cmc_flow("activity", f, v, target_activity_id=edge["target_activity_id"], **source, **extra))
                else:
                    acc[edge["cost_object_id"]]["activity"][0] += f
                    acc[edge["cost_object_id"]]["activity"][1] += v
                    flows.append(self._cmc_flow("object", f, v, target_object_id=edge["cost_object_id"], **source, **extra))
            if unused:
                flows.append(self._cmc_flow(
                    "unused", unused, 0.0, driver_qty=(cap or used) - used, rate=fixed_rate, **source
                ))
        return rows, flows, acc

    def _cmc_sustaining_object(self):
        return self.env["cmc.cost.object"]._cmc_ensure_objects(
            self.company_id, company=self.company_id, object_type="sustaining"
        )

    def _cmc_propagate_objects(self, edges, acc, rounding):
        """Objects pass 100% of what they hold to the objects they feed (product -> customer -> channel)."""
        out = defaultdict(list)
        for edge in edges:
            if edge["source_object_id"]:
                out[edge["source_object_id"]].append(edge)
            for oid in (edge["source_object_id"], edge["cost_object_id"]):
                if oid:
                    # an endpoint gets a result row even at zero cost
                    acc.setdefault(oid, defaultdict(_pair))
        arcs = [(e["source_object_id"], e["cost_object_id"]) for e in edges if e["source_object_id"]]
        order, cyclic = _topological(set(acc) | set(out), arcs)
        if cyclic:
            names = self.env["cmc.cost.object"].sudo().browse(cyclic).mapped("display_name")
            raise UserError(
                self.env._(
                    "The cost objects %(names)s pass cost to each other in a loop: reciprocal allocation is not supported.",
                    names=", ".join(names),
                )
            )
        flows = []
        for oid in order:
            edges_o = out.get(oid, [])
            used = sum(e["driver_qty"] for e in edges_o)
            if float_is_zero(used, precision_rounding=DRIVER_QTY_ROUNDING):
                continue
            held = [float_round(sum(c[i] for c in acc[oid].values()), precision_rounding=rounding) for i in (0, 1)]
            acc[oid]["forwarded"][:] = held
            shares = {e["id"]: e["driver_qty"] for e in edges_o}
            fixed_shares = spread(held[0], shares, rounding)
            variable_shares = spread(held[1], shares, rounding)
            for edge in edges_o:
                f, v = fixed_shares[edge["id"]], variable_shares[edge["id"]]
                acc[edge["cost_object_id"]]["object"][0] += f
                acc[edge["cost_object_id"]]["object"][1] += v
                flows.append(self._cmc_flow(
                    "object", f, v, source_object_id=oid, target_object_id=edge["cost_object_id"],
                    driver_qty=edge["driver_qty"], rate=sum(held) / used,
                ))
        return flows

    # ---- results ----------------------------------------------------------

    def _cmc_result_vals(self, acc, rounding):
        """One row per cost object, with the ABC figures; comparison and burdens come after."""
        objects = self.env["cmc.cost.object"].sudo().browse(acc).read(["res_model", "res_id"], load=False)
        product_of = {o["id"]: o["res_id"] if o["res_model"] == "product.product" else False for o in objects}
        quantities = self._cmc_quantities(self.output_measure_id, acc)
        defects = self._cmc_quantities(self.defect_measure_id, acc)
        results = {}
        for oid, parts in acc.items():
            pair = {name: [float_round(x, precision_rounding=rounding) for x in parts[name]] for name in ("traced", "activity", "object", "forwarded")}
            fixed = sum(pair[name][0] for name in ("traced", "activity", "object"))
            variable = sum(pair[name][1] for name in ("traced", "activity", "object"))
            amount = float_round(fixed + variable, precision_rounding=rounding)
            qty = quantities.get(oid, 0.0)
            defect_qty = defects.get(oid, 0.0)
            results[oid] = {
                "defect_qty": defect_qty,
                "copq_amount": float_round(defect_qty * amount / qty, precision_rounding=rounding) if qty else 0.0,
                "run_id": self.id, "cost_object_id": oid, "product_id": product_of[oid],
                "traced_cost": sum(pair["traced"]), "activity_cost": sum(pair["activity"]), "object_cost": sum(pair["object"]),
                "amount": amount, "forwarded_amount": sum(pair["forwarded"]), "fixed_amount": fixed, "variable_amount": variable,
                "quantity": qty, "unit_cost": amount / qty if qty else 0.0,
                "traditional_cost": 0.0, "distortion_percent": 0.0, "costing_flag": False,
                "idle_burden": 0.0, "sustaining_burden": 0.0, "full_cost": amount,
            }
        return results

    def _cmc_traditional(self, results, rounding):
        """{cost object id: traditional cost} - volume base or approved rates (ABC-11)."""
        if self.traditional_basis == "volume":
            if not self.traditional_driver_id:
                raise UserError(self.env._("Pick the volume driver of the traditional comparison."))
            volume = {oid: qty for oid, qty in self._cmc_quantities(self.traditional_driver_id).items() if oid in results}
            base = sum(volume.values())
            if float_is_zero(base, precision_rounding=DRIVER_QTY_ROUNDING):
                return {}
            # zero-sum (Kaplan & Cooper): the pool is the overhead ABC assigns to the compared objects, burdens included
            pool = sum(results[oid]["full_cost"] - results[oid]["traced_cost"] for oid in volume)
            shares = spread(float_round(pool, precision_rounding=rounding), volume, rounding)  # cent-exact: Σ traditional = Σ ABC
            return {oid: results[oid]["traced_cost"] + share for oid, share in shares.items()}
        if self.traditional_basis == "rate":
            version, measure = self.traditional_version_id, self.output_measure_id
            if not (version and measure and self.date_from):
                raise UserError(self.env._("Rate comparison needs an approved version, the output measure and the period."))
            self._cmc_record_usage(version)
            # why sudo: the registry rows of the approved version are engine input
            rates = self.env["cmc.rate"].sudo().search([
                ("holder_model", "=", "cmc.cost.object"), ("holder_id", "in", list(results)), ("driver_id", "=", measure.id),
                ("component", "=", "total"), ("date", "=", month_start(self.date_from)), ("company_id", "=", self.company_id.id),
                ("origin_model", "=", version._name), ("origin_id", "=", version.id),
            ])
            return {rate.holder_id: rate.value * results[rate.holder_id]["quantity"] for rate in rates}
        return {}

    def _cmc_compare(self, results, rounding):
        for oid, trad in self._cmc_traditional(results, rounding).items():
            if not trad:
                continue
            res = results[oid]
            distortion = (res["full_cost"] - trad) / trad * 100.0
            res.update(
                traditional_cost=trad, distortion_percent=distortion,
                costing_flag="aligned" if abs(distortion) < ALIGNED_PERCENT else "over" if distortion < 0 else "under",
            )

    def _cmc_burdens(self, results, unused, sustaining, rounding):
        """Idle capacity and business-sustaining cost loaded on the final cost, pro rata (Frost): rates in percent."""
        final = {oid: res["amount"] - res["forwarded_amount"] for oid, res in results.items()}
        base = sum(final.values())
        if not self.compute_burdens or float_is_zero(base, precision_rounding=rounding):
            return 0.0, 0.0
        for field, pool in (("idle_burden", unused), ("sustaining_burden", sustaining)):
            for oid, share in spread(pool, final, rounding).items():
                results[oid][field] = share
        for res in results.values():
            res["full_cost"] = res["amount"] + res["idle_burden"] + res["sustaining_burden"]
        return unused / base * 100.0, sustaining / base * 100.0

    def _cmc_publish_rates(self, rows, results):
        """Activity rates and unit costs to the registry, for the month of date_from (AD-108)."""
        if not self.date_from:
            return
        month = month_start(self.date_from)
        Activity, Driver, Object = self.env["cmc.abc.activity"], self.env["cmc.cost.driver"], self.env["cmc.cost.object"]
        components = (("total", "rate", "unit_cost"), ("fixed", "fixed_rate", "fixed_amount"), ("variable", "variable_rate", "variable_amount"))
        published = [
            {"holder": Activity.browse(row["activity_id"]), "driver": Driver.browse(row["driver_id"]), "component": component,
             "date": month, "value": row[rate_field]}
            for row in rows if row["driver_id"] and row["used_qty"]
            for component, rate_field, _unit in components
        ]
        if self.output_measure_id:
            published += [
                {"holder": Object.browse(res["cost_object_id"]), "driver": self.output_measure_id, "component": component,
                 "date": month, "value": res[field] if component == "total" else res[field] / res["quantity"]}
                for res in results.values() if res["quantity"]
                for component, _rate, field in components
            ]
        # why sudo: rates of a run are engine rows (the context carries the run sentinel)
        self.env["cmc.rate"].sudo()._cmc_publish(self, published)

    def _cmc_activity_results(self, flows, rows, results):
        """v1 snapshot (activity x object) from the flows: the frozen input of the reclassified report."""
        total_cost = {row["activity_id"]: row["total_cost"] for row in rows}
        vals = [
            {
                "run_id": self.id, "activity_id": flow["source_activity_id"], "cost_object_id": flow["target_object_id"],
                "product_id": results[flow["target_object_id"]]["product_id"], "driver_qty": flow["driver_qty"],
                "pool_amount": total_cost[flow["source_activity_id"]], "amount": flow["amount"],
            }
            for flow in flows if flow["stage"] == "object" and flow.get("source_activity_id") and flow.get("target_object_id")
        ]
        vals += [
            {
                "run_id": self.id, "activity_id": flow["source_activity_id"], "product_id": False, "driver_qty": 0.0,
                "pool_amount": flow["amount"], "amount": flow["amount"], "unallocated": True,
            }
            for flow in flows if flow["stage"] == "unallocated"
        ]
        return vals

    # ---- compute ----------------------------------------------------------

    def _cmc_compute(self):
        """Resources -> activities -> activities -> objects -> objects, every cost conserved to the cent
        (largest remainder) with its fixed/variable nature carried along (AD-113)."""
        self.ensure_one()
        rounding = self.company_id.currency_id.rounding
        edges = self.line_ids.read(
            ["activity_id", "source_object_id", "target_activity_id", "cost_object_id", "driver_qty"], load=False
        )
        caps = {c["activity_id"]: c["practical_capacity"] for c in self.capacity_line_ids.read(["activity_id", "practical_capacity"], load=False)}
        flows, stage1, in_scope, unassigned = self._cmc_stage1(rounding)
        traced, trace_flows = self._cmc_traced(rounding)
        rows, activity_flows, acc = self._cmc_propagate_activities(edges, stage1, caps, rounding)
        for oid, pair in traced.items():
            acc[oid]["traced"][:] = pair
        object_flows = self._cmc_propagate_objects(edges, acc, rounding)
        all_flows = flows + trace_flows + activity_flows + object_flows
        results = self._cmc_result_vals(acc, rounding)

        def total(*stages):
            return float_round(sum(f["amount"] for f in all_flows if f["stage"] in stages), precision_rounding=rounding)

        resource_cost, traced_cost = total("resource", "tag"), total("trace")
        unused, sustaining, unallocated = total("unused"), total("sustaining"), total("unallocated")
        object_cost = float_round(sum(r["amount"] - r["forwarded_amount"] for r in results.values()), precision_rounding=rounding)
        if float_compare(resource_cost + traced_cost, object_cost + unused + sustaining + unallocated, precision_rounding=rounding):
            raise UserError(self.env._("The cost network does not conserve: %(inflow)s in, %(outflow)s out.",
                                       inflow=resource_cost + traced_cost, outflow=object_cost + unused + sustaining + unallocated))
        idle_rate, sustaining_rate = self._cmc_burdens(results, unused, sustaining, rounding)
        self._cmc_compare(results, rounding)
        self._cmc_publish_rates(rows, results)
        self._cmc_abm_snapshot(rows, all_flows, results)
        self.env["cmc.abc.flow"].create(all_flows)
        self.env["cmc.abc.activity.cost"].create(rows)
        self.env["cmc.abc.activity.performance"].create(self._cmc_performance_vals(rows))
        self.env["cmc.abc.result"].create(list(results.values()))
        self.env["cmc.abc.activity.result"].create(self._cmc_activity_results(all_flows, rows, results))
        sustaining_flow = next((f for f in all_flows if f["stage"] == "sustaining"), None)
        self.write({
            "total_resource_cost": resource_cost, "total_traced_cost": traced_cost, "total_object_cost": object_cost,
            "total_activity_cost": float_round(sum(r["resource_cost"] for r in rows), precision_rounding=rounding),
            "unused_capacity_cost": unused, "sustaining_cost": sustaining,
            "copq_total": float_round(sum(r["copq_amount"] for r in results.values()), precision_rounding=rounding),
            "in_scope_cost": float_round(in_scope, precision_rounding=rounding),
            "unassigned_cost": float_round(unassigned, precision_rounding=rounding),
            "idle_burden_rate": idle_rate, "sustaining_burden_rate": sustaining_rate,
            "sustaining_object_id": sustaining_flow["target_object_id"] if sustaining_flow else False,
        })
