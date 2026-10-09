from collections import defaultdict

from odoo import models
from odoo.exceptions import UserError
from odoo.fields import Command, Domain
from odoo.tools import float_compare, float_round

from odoo.addons.cmc_account.models.account_move_line import REVENUE_TYPES
from odoo.addons.cmc_base.models.cmc_utils import month_range, spread

REFUND_TYPES = ("out_refund", "in_refund")


class CmcProfitRun(models.Model):
    _inherit = "cmc.profit.run"

    # ---- helpers ------------------------------------------------------------

    def _cmc_round(self, amount):
        return float_round(amount, precision_rounding=self.currency_id.rounding)

    def _cmc_commercial_map(self, partner_ids):
        """{partner id: commercial partner id} in one read."""
        rows = self.env["res.partner"].browse(list(partner_ids)).read(["commercial_partner_id"], load=False)
        return {row["id"]: row["commercial_partner_id"] for row in rows}

    def _cmc_attribute_map(self, records, code):
        """{record id: attribute value id}, in one query."""
        return {rid: value_id for value_id, ids in records._cmc_group_by_attribute(code).items() if value_id for rid in ids}

    def _cmc_by_commercial(self, rows, commercial):
        """Sum the (partner, amount) rows by commercial partner id (0 = none)."""
        total = defaultdict(float)
        for partner, amount in rows:
            total[commercial.get(partner.id, partner.id) or 0] += amount or 0.0
        return total

    # ---- sources ------------------------------------------------------------

    def _cmc_check_sources(self):
        abc, version, fin = self.abc_run_id, self.cost_version_id, self.fin_analysis_id
        for origin in (abc, version, fin):
            if origin and origin.state != "done":
                raise UserError(self.env._("%s is not computed: a computed run reads certified sources only.", origin.display_name))
        if abc and not (abc.date_from and abc.date_to and self.date_from <= abc.date_from and abc.date_to <= self.date_to):
            raise UserError(self.env._("The ABC run %s must lie inside the span of the run.", abc.display_name))
        for origin in (abc, version, fin):
            if origin:
                self._cmc_record_usage(origin)

    # ---- readers (AD-111 / AD-114: grouped, never per line) -------------------

    def _cmc_read_facts(self):
        """{(product id, commercial partner id, user id, month): {revenue, quantity}}."""
        Line = self.env["account.move.line"]
        company = self.company_id
        groupby = ("product_id", "partner_id", "move_id.invoice_user_id", "date:month")
        revenue = Line._cmc_revenue_read_group(company, self.date_from, self.date_to, groupby)
        domain = (
            Line._cmc_posted_domain(company, self.date_from, self.date_to)
            & Domain("account_id.account_type", "in", REVENUE_TYPES)
            & Domain("product_id", "!=", False)
        )
        quantity = Line._cmc_reader_scope(company)._read_group(domain, [*groupby, "move_id.move_type", "product_uom_id"], ["quantity:sum"])
        partners = {row[1].id for row in revenue if row[1]} | {row[1].id for row in quantity if row[1]}
        commercial = self._cmc_commercial_map(partners)

        def key(product, partner, user, month):
            return product.id or 0, commercial.get(partner.id, partner.id) or 0, user.id or 0, month

        facts = defaultdict(lambda: {"revenue": 0.0, "quantity": 0.0})
        for product, partner, user, month, amount in revenue:
            facts[key(product, partner, user, month)]["revenue"] += amount
        for product, partner, user, month, move_type, uom, qty in quantity:
            # RN-001: a packaging UoM (Dozen) counts in the product UoM
            qty = uom._compute_quantity(qty or 0.0, product.uom_id, round=False) if uom else (qty or 0.0)
            facts[key(product, partner, user, month)]["quantity"] += -qty if move_type in REFUND_TYPES else qty
        return facts

    def _cmc_unit_rates(self, objects, months):
        """{(object id, month, component): value}: one search for every product (AD-108, same rule as cmc.rate lookup)."""
        driver = self.unit_cost_driver_id
        if not (driver and objects):
            return {}
        keys = [("cmc.cost.object", obj.id, driver.id, month) for obj in objects for month in months]
        found = self.env["cmc.rate"]._cmc_lookup_batch(
            keys, origin=self.cost_version_id, component=("variable", "total"), company=self.company_id,
        )
        rates = {(key[1], key[3], key[4]): rate.value for key, rate in found.items()}
        return rates

    def _cmc_abc_costs(self):
        """ABC costs as positive figures: product/customer/channel/supplier keyed by record id, sustaining as a number."""
        costs = {"product": defaultdict(float), "customer": defaultdict(float), "channel": defaultdict(float),
                 "supplier": defaultdict(float), "sustaining": 0.0}
        abc = self.abc_run_id
        if not abc:
            return costs
        results = abc.result_ids.read(["cost_object_id", "amount"], load=False)
        flows = self.env["cmc.abc.flow"]._read_group(
            Domain([
                ("run_id", "=", abc.id), ("stage", "=", "object"), ("source_activity_id.cost_level", "=", "product"),
                ("target_object_id.res_model", "=", "product.product"),
            ]),
            ["target_object_id"], ["amount:sum"],
        )
        ids = {row["cost_object_id"] for row in results if row["cost_object_id"]} | {obj.id for obj, _amount in flows}
        info = {row["id"]: row for row in self.env["cmc.cost.object"].browse(list(ids)).read(["object_type", "res_model", "res_id"], load=False)}
        for obj, amount in flows:
            costs["product"][info[obj.id]["res_id"]] += amount
        for row in results:
            obj = info.get(row["cost_object_id"])
            if obj and obj["object_type"] in ("customer", "supplier"):
                costs[obj["object_type"]][obj["res_id"]] += row["amount"]
            elif obj and obj["object_type"] == "channel" and obj["res_model"] == "cmc.attribute.value":
                costs["channel"][obj["res_id"]] += row["amount"]
        costs["sustaining"] = abc.sustaining_cost
        return costs

    def _cmc_order_counts(self, below=0.0):
        """{commercial partner id: posted out_invoice count of the span (untaxed amount < below when given)}."""
        domain = Domain([
            ("move_type", "=", "out_invoice"), ("state", "=", "posted"), ("company_id", "=", self.company_id.id),
            ("date", ">=", self.date_from), ("date", "<=", self.date_to),
        ])
        if below:
            domain &= Domain("amount_untaxed", "<", below)
        moves = self.env["account.move.line"]._cmc_reader_scope(self.company_id).env["account.move"]
        return {partner.id: count for partner, count in moves._read_group(domain, ["commercial_partner_id"], ["__count"])}

    def _cmc_whatif_surcharge(self, surcharge, min_order_amount=0.0):
        self.ensure_one()
        counts = self._cmc_order_counts(min_order_amount)
        return {
            row.partner_id.id: row.customer_profit + surcharge * counts.get(row.partner_id.id, 0)
            for row in self.customer_ids.filtered("partner_id")
        }

    def _cmc_reconciliation(self):
        self.ensure_one()
        live = self._cmc_round(self.env["account.move.line"]._cmc_revenue_read_group(self.company_id, self.date_from, self.date_to)[0][-1])
        return {"gl_revenue": live, "run_revenue": self.revenue_total, "difference": self._cmc_round(live - self.revenue_total)}

    # ---- compute ------------------------------------------------------------

    def _cmc_compute(self):
        self.ensure_one()
        self._cmc_check_sources()
        company = self.company_id
        Line = self.env["account.move.line"]
        Object = self.env["cmc.cost.object"]
        rnd = self._cmc_round
        gl_revenue = rnd(Line._cmc_revenue_read_group(company, self.date_from, self.date_to)[0][-1])
        raw_facts = self._cmc_read_facts()
        abc = self._cmc_abc_costs()

        # ---- cost objects and snapshots
        product_ids = sorted({key[0] for key in raw_facts if key[0]})
        products = self.env["product.product"].browse(product_ids)
        product_obj = dict(zip(product_ids, Object._cmc_ensure_objects(products, company=company).ids))
        partner_ids = sorted({key[1] for key in raw_facts if key[1]} | set(abc["customer"]))
        partners = self.env["res.partner"].browse(partner_ids)
        customer_obj = dict(zip(partner_ids, Object._cmc_ensure_objects(partners, company=company, object_type="customer").ids))
        unassigned_obj = Object._cmc_ensure_objects(company, company=company, object_type="unassigned").id
        segment_of = self._cmc_attribute_map(partners, "SEGMENT")
        channel_of = self._cmc_attribute_map(partners, "CHANNEL")
        product_info = {row["id"]: row for row in products.read(["product_tmpl_id", "lst_price"], load=False)}
        sbu_of_tmpl = self._cmc_attribute_map(products.product_tmpl_id, "SBU")
        sbu_of = {pid: sbu_of_tmpl.get(row["product_tmpl_id"], False) for pid, row in product_info.items()}
        rates = self._cmc_unit_rates(Object.browse(list(product_obj.values())), month_range(self.date_from, self.date_to))

        # ---- facts: revenue, list price, variable cost
        facts, missing = [], set()
        for (pid, cid, uid, month), vals in raw_facts.items():
            qty = vals["quantity"] if pid else 0.0
            fact = {
                "date": month, "product_id": pid, "partner_id": cid, "salesperson_id": uid, "quantity": qty,
                "revenue": rnd(vals["revenue"]), "segment_value_id": segment_of.get(cid, False),
                "channel_value_id": channel_of.get(cid, False), "sbu_value_id": sbu_of.get(pid, False),
                "product_object_id": product_obj.get(pid, False), "customer_object_id": customer_obj.get(cid, unassigned_obj),
                "variable_unit_cost": 0.0, "full_unit_cost": 0.0, "product_sustaining_cost": 0.0,
            }
            list_amount = fact["revenue"]
            if pid:
                variable = rates.get((product_obj[pid], month, "variable"))
                if variable is None and self.unit_cost_driver_id:
                    missing.add(pid)
                fact["variable_unit_cost"] = variable or 0.0
                fact["full_unit_cost"] = rates.get((product_obj[pid], month, "total"), 0.0)
                list_amount = rnd(product_info[pid]["lst_price"] * qty)
            fact["list_amount"] = list_amount
            fact["discount_amount"] = rnd(fact["revenue"] - list_amount)
            fact["unit_price"] = fact["revenue"] / qty if qty else 0.0
            fact["variable_cost"] = -rnd(qty * fact["variable_unit_cost"])
            fact["contribution_margin"] = rnd(fact["revenue"] + fact["variable_cost"])
            fact["floor_status"] = self._cmc_floor_status(fact["unit_price"], fact["variable_unit_cost"], fact["full_unit_cost"], qty)
            facts.append(fact)
        self._cmc_spread_product_sustaining(facts, abc["product"])

        customers = self._cmc_customer_rows(facts, abc, customer_obj, unassigned_obj, segment_of, channel_of, partner_ids)
        products_rows = self._cmc_product_rows(facts, product_obj, sbu_of)
        suppliers = self._cmc_supplier_rows(abc["supplier"])
        waterfall = self._cmc_waterfall(customers)
        self._cmc_rank(customers, "customer_profit", cumulative_key="cumulative_profit")
        self._cmc_rank(products_rows, "product_margin")
        header = self._cmc_header(facts, customers, abc, gl_revenue)
        self._cmc_quadrants(customers, header)
        channels = self._cmc_channel_rows(customers)
        sbus = self._cmc_sbu_rows(products_rows)

        # why sudo: results are read-only for users; the engine writes them under the run sentinel
        run = {"run_id": self.id}
        for model, rows in (
            ("cmc.profit.fact", facts), ("cmc.profit.product", products_rows), ("cmc.profit.customer", customers),
            ("cmc.profit.channel", channels), ("cmc.profit.waterfall", waterfall), ("cmc.profit.supplier", suppliers),
            ("cmc.profit.sbu", sbus),
        ):
            # 0 is the grouping key of 'none', never a record id
            self.env[model].sudo().create([{**{k: v or False if k.endswith("_id") else v for k, v in row.items()}, **run} for row in rows])
        self.write({**header, "missing_cost_product_ids": [Command.set(sorted(missing))]})

    # ---- fact helpers ---------------------------------------------------------

    @staticmethod
    def _cmc_floor_status(price, variable_unit, full_unit, quantity):
        if not quantity:
            return "ok"
        if float_compare(price, variable_unit, precision_digits=4) < 0:
            return "below_variable"
        return "below_full" if float_compare(price, full_unit, precision_digits=4) < 0 else "ok"

    def _cmc_spread_product_sustaining(self, facts, product_cost):
        """Product-level ABC cost, spread over the facts of the product by quantity (largest remainder)."""
        by_product = defaultdict(dict)
        for index, fact in enumerate(facts):
            if fact["product_id"] and fact["quantity"] > 0:
                by_product[fact["product_id"]][index] = fact["quantity"]
        for pid, weights in by_product.items():
            if product_cost.get(pid):
                for index, share in spread(self._cmc_round(product_cost[pid]), weights, self.currency_id.rounding).items():
                    facts[index]["product_sustaining_cost"] = -share

    def _cmc_customer_rows(self, facts, abc, customer_obj, unassigned_obj, segment_of, channel_of, partner_ids):
        rnd = self._cmc_round
        by_partner = {pid: defaultdict(float) for pid in [*partner_ids, 0]}
        for fact in facts:
            row = by_partner[fact["partner_id"]]
            for fname in ("revenue", "variable_cost", "product_sustaining_cost", "list_amount", "discount_amount"):
                row[fname] += fact[fname]
        # channel cost: spread over the customers of the channel pro rata revenue; none -> stays unassigned
        by_channel = defaultdict(dict)
        for pid in partner_ids:
            if channel_of.get(pid) and by_partner[pid]["revenue"] > 0:
                by_channel[channel_of[pid]][pid] = by_partner[pid]["revenue"]
        for channel, cost in abc["channel"].items():
            if cost and by_channel.get(channel):
                for pid, share in spread(rnd(cost), by_channel[channel], self.currency_id.rounding).items():
                    by_partner[pid]["channel_cost"] = -share
        ar = self._cmc_receivables(partner_ids)
        orders = self._cmc_order_counts()
        months = len(month_range(self.date_from, self.date_to))
        rows = []
        for pid, vals in by_partner.items():
            if not pid and not vals["revenue"]:
                continue
            m1 = rnd(vals["revenue"] + vals["variable_cost"])
            m2 = rnd(m1 + vals["product_sustaining_cost"])
            cts = -rnd(abc["customer"].get(pid, 0.0))
            m3 = rnd(m2 + cts)
            m4 = rnd(m3 + vals["channel_cost"])
            balance = ar.get(pid, 0.0) if pid else 0.0
            capital = -rnd(balance * self.capital_rate / 100 * months / 12)
            rows.append({
                "partner_id": pid, "cost_object_id": customer_obj.get(pid, unassigned_obj),
                "segment_value_id": segment_of.get(pid, False), "channel_value_id": channel_of.get(pid, False),
                "revenue": rnd(vals["revenue"]), "variable_cost": rnd(vals["variable_cost"]), "contribution_margin": m1,
                "product_sustaining_cost": rnd(vals["product_sustaining_cost"]), "product_margin": m2, "cost_to_serve": cts,
                "served_margin": m3, "channel_cost": rnd(vals["channel_cost"]), "customer_profit": m4,
                "receivable_balance": rnd(balance), "capital_charge": capital, "economic_profit": rnd(m4 + capital),
                "order_count": orders.get(pid, 0) if pid else 0, "list_amount": rnd(vals["list_amount"]),
                "discount_amount": rnd(vals["discount_amount"]), "rank": 0, "cumulative_profit": 0.0, "cumulative_profit_pct": 0.0,
            })
        return rows

    def _cmc_receivables(self, partner_ids):
        """{commercial partner id: posted receivable balance at date_to, cumulative}."""
        if not partner_ids:
            return {}
        scope = self.env["account.move.line"]._cmc_reader_scope(self.company_id)
        domain = Domain([
            ("parent_state", "=", "posted"), ("company_id", "=", self.company_id.id), ("date", "<=", self.date_to),
            ("account_id.account_type", "=", "asset_receivable"), ("partner_id.commercial_partner_id", "in", partner_ids),
        ])
        rows = scope._read_group(domain, ["partner_id"], ["balance:sum"])
        return self._cmc_by_commercial(rows, self._cmc_commercial_map({partner.id for partner, _balance in rows}))

    def _cmc_product_rows(self, facts, product_obj, sbu_of):
        rnd = self._cmc_round
        by_product = defaultdict(list)
        for fact in facts:
            by_product[fact["product_id"]].append(fact)
        rows = []
        for pid, group in by_product.items():
            qty = sum(f["quantity"] for f in group)
            revenue = rnd(sum(f["revenue"] for f in group))
            variable = rnd(sum(f["variable_cost"] for f in group))
            sustaining = rnd(sum(f["product_sustaining_cost"] for f in group))
            unit_variable = sum(f["quantity"] * f["variable_unit_cost"] for f in group) / qty if qty else 0.0
            unit_full = sum(f["quantity"] * f["full_unit_cost"] for f in group) / qty if qty else 0.0
            price = revenue / qty if qty else 0.0
            rows.append({
                "product_id": pid, "cost_object_id": product_obj.get(pid, False), "sbu_value_id": sbu_of.get(pid, False),
                "quantity": qty, "revenue": revenue, "variable_cost": variable, "contribution_margin": rnd(revenue + variable),
                "product_sustaining_cost": sustaining, "product_margin": rnd(revenue + variable + sustaining),
                "avg_price": price, "variable_unit_cost": unit_variable, "full_unit_cost": unit_full,
                "floor_status": self._cmc_floor_status(price, unit_variable, unit_full, qty if pid else 0.0),
                "rank": 0, "cumulative_profit_pct": 0.0,
            })
        return rows

    def _cmc_rank(self, rows, margin_key, cumulative_key=None):
        """Whale curve: rows with a product/customer ranked by margin desc; the unassigned row keeps rank 0."""
        ranked = sorted((r for r in rows if r.get("partner_id") or r.get("product_id")), key=lambda r: -r[margin_key])
        total = sum(r[margin_key] for r in ranked)
        cumulative = 0.0
        for rank, row in enumerate(ranked, 1):
            cumulative += row[margin_key]
            row["rank"] = rank
            row["cumulative_profit_pct"] = cumulative / total * 100 if total else 0.0
            if cumulative_key:
                row[cumulative_key] = self._cmc_round(cumulative)

    def _cmc_quadrants(self, customers, header):
        ranked = [r for r in customers if r["partner_id"]]
        revenue = sum(r["revenue"] for r in ranked)
        margin_threshold = sum(r["product_margin"] for r in ranked) / revenue * 100 if revenue else 0.0
        cost_threshold = -sum(r["cost_to_serve"] for r in ranked) / revenue * 100 if revenue else 0.0
        for row in ranked:
            row["net_margin_pct"] = row["product_margin"] / row["revenue"] * 100 if row["revenue"] else 0.0
            row["cost_to_serve_pct"] = -row["cost_to_serve"] / row["revenue"] * 100 if row["revenue"] else 0.0
            high_margin = float_compare(row["net_margin_pct"], margin_threshold, precision_digits=6) >= 0
            high_cost = float_compare(row["cost_to_serve_pct"], cost_threshold, precision_digits=6) >= 0
            row["quadrant"] = {(True, False): "passive", (True, True): "carriage", (False, False): "bargain", (False, True): "aggressive"}[
                (high_margin, high_cost)
            ]
        header.update({"quadrant_margin_threshold": margin_threshold, "quadrant_cost_threshold": cost_threshold})

    def _cmc_channel_rows(self, customers):
        rnd = self._cmc_round
        by_channel = defaultdict(lambda: defaultdict(float))
        for row in customers:
            group = by_channel[row["channel_value_id"]]
            for fname in ("revenue", "served_margin", "channel_cost"):
                group[fname] += row[fname]
        return [
            {
                "channel_value_id": channel, "revenue": rnd(vals["revenue"]), "served_margin": rnd(vals["served_margin"]),
                "channel_cost": rnd(vals["channel_cost"]), "channel_margin": rnd(vals["served_margin"] + vals["channel_cost"]),
            }
            for channel, vals in by_channel.items()
        ]

    def _cmc_waterfall(self, customers):
        """Waterfall rows (price elements, -balance) and the off-invoice / pocket amounts of the customer rows."""
        rnd = self._cmc_round
        elements = self.env["cmc.profit.price.element"].search([("company_id", "=", self.company_id.id)])
        by_account = defaultdict(list)
        for element in elements:
            for account in element.account_ids:
                by_account[account.id].append(element.id)
        rows, off_invoice = [], defaultdict(float)
        if by_account:
            scope = self.env["account.move.line"]._cmc_reader_scope(self.company_id)
            domain = scope._cmc_posted_domain(self.company_id, self.date_from, self.date_to) & Domain("account_id", "in", list(by_account))
            groups = scope._read_group(domain, ["account_id", "partner_id"], ["balance:sum"])
            commercial = self._cmc_commercial_map({partner.id for _account, partner, _balance in groups if partner})
            amounts = defaultdict(float)
            for account, partner, balance in groups:
                for element_id in by_account[account.id]:
                    amounts[commercial.get(partner.id, partner.id) or 0, element_id] -= balance or 0.0
            for (pid, element_id), amount in amounts.items():
                rows.append({"partner_id": pid, "element_id": element_id, "amount": rnd(amount)})
                off_invoice[pid] += amount
        for row in customers:
            row["off_invoice_amount"] = rnd(off_invoice.get(row["partner_id"], 0.0))
            row["pocket_amount"] = rnd(row["revenue"] + row["off_invoice_amount"])
        return rows

    def _cmc_supplier_rows(self, activity_costs):
        rnd = self._cmc_round
        scope = self.env["account.move.line"]._cmc_reader_scope(self.company_id)
        domain = scope._cmc_posted_domain(self.company_id, self.date_from, self.date_to) & Domain([
            ("move_id.move_type", "in", ("in_invoice", "in_refund")), ("display_type", "=", "product"),
        ])
        groups = scope._read_group(domain, ["partner_id"], ["balance:sum"])
        commercial = self._cmc_commercial_map({partner.id for partner, _balance in groups if partner})
        purchases = self._cmc_by_commercial(groups, commercial)
        partner_ids = sorted((set(purchases) | set(activity_costs)) - {0})
        objects = self.env["cmc.cost.object"]._cmc_ensure_objects(
            self.env["res.partner"].browse(partner_ids), company=self.company_id, object_type="supplier"
        )
        rows = []
        for pid, obj in zip(partner_ids, objects):
            purchase = -rnd(purchases.get(pid, 0.0))
            activity = -rnd(activity_costs.get(pid, 0.0))
            rows.append({
                "partner_id": pid, "cost_object_id": obj.id, "purchase_amount": purchase, "activity_cost": activity,
                "tco": rnd(purchase + activity), "tco_index": (purchase + activity) / purchase if purchase else 0.0, "rank": 0,
            })
        for rank, row in enumerate(sorted((r for r in rows if r["purchase_amount"]), key=lambda r: r["tco_index"]), 1):
            row["rank"] = rank
        return rows

    def _cmc_sbu_rows(self, product_rows):
        """MSLC per SBU (+ corporate) with the capital of the inputs or of the financial analysis."""
        rnd = self._cmc_round
        inputs = {line.sbu_value_id.id: line for line in self.sbu_line_ids}
        values = {}
        if self.fin_analysis_id:
            values = {(v["code"], v["year"]): v["value"] for v in self.fin_analysis_id.value_ids.read(["code", "year", "value"])}
        year = self.date_to.year
        by_sbu = defaultdict(lambda: defaultdict(float))
        for row in product_rows:
            for fname in ("revenue", "variable_cost", "product_sustaining_cost"):
                by_sbu[row["sbu_value_id"]][fname] += row[fname]
        for sbu in inputs:
            by_sbu[sbu]  # noqa: B018 - an SBU with capital but no sale still gets its row
        rows = []
        for sbu, vals in by_sbu.items():
            line = inputs.get(sbu)
            if line and self.fin_analysis_id:
                key = line.capital_key / 100
                opening, closing, pfn = (values.get(("coin", year - 1), 0.0) * key, values.get(("coin", year), 0.0) * key,
                                         values.get(("pfn", year), 0.0) * key)
            else:
                opening, closing, pfn = (line.coin_open, line.coin_close, line.pfn) if line else (0.0, 0.0, 0.0)
            mslc = rnd(vals["revenue"] + vals["variable_cost"] + vals["product_sustaining_cost"])
            cash_flow = rnd(mslc - (closing - opening))
            rows.append({
                "sbu_value_id": sbu, "revenue": rnd(vals["revenue"]), "variable_cost": rnd(vals["variable_cost"]),
                "product_sustaining_cost": rnd(vals["product_sustaining_cost"]), "mslc": mslc, "coin_open": rnd(opening),
                "coin_close": rnd(closing), "pfn": rnd(pfn), "equity": rnd(closing - pfn),
                "roce": mslc / opening * 100 if opening else 0.0, "growth": (closing - opening) / opening * 100 if opening else 0.0,
                "cash_flow": cash_flow, "sustainable": cash_flow > 0,
            })
        return rows

    def _cmc_header(self, facts, customers, abc, gl_revenue):
        rnd = self._cmc_round
        revenue = rnd(sum(f["revenue"] for f in facts))
        variable = rnd(sum(f["variable_cost"] for f in facts))
        sustaining = rnd(sum(f["product_sustaining_cost"] for f in facts))
        serve = rnd(sum(r["cost_to_serve"] for r in customers))
        channel = rnd(sum(r["channel_cost"] for r in customers))
        business = -rnd(abc["sustaining"])
        operating = rnd(sum(r["customer_profit"] for r in customers) + business)
        capital = rnd(sum(r["capital_charge"] for r in customers))
        assigned = rnd(sustaining + serve + channel + business - sum(abc["supplier"].values()))
        # why sudo: the cost base is a company figure whatever the analytic lines the user may see
        lines = self.env["account.analytic.line"].sudo()
        ledger = -lines._cmc_cost_read_group(Domain([
            ("cmc_excluded", "=", False), ("company_id", "in", [False, *self.company_id.ids]),
            ("date", ">=", self.date_from), ("date", "<=", self.date_to),
        ]))[0][0]
        ledger = rnd(ledger)
        if float_compare(assigned, ledger, precision_rounding=self.currency_id.rounding) < 0:
            raise UserError(self.env._(
                "The cost assigned by the ABC run (%(assigned)s) exceeds the analytic cost base of the span (%(ledger)s): "
                "recompute the ABC run.", assigned=-assigned, ledger=-ledger,
            ))
        ranked = [r for r in customers if r["partner_id"]]
        peak = max((r["cumulative_profit"] for r in ranked), default=0.0)
        total = rnd(sum(r["customer_profit"] for r in ranked))
        return {
            "revenue_total": revenue, "gl_revenue": gl_revenue, "variable_cost_total": variable,
            "contribution_total": rnd(revenue + variable), "product_sustaining_total": sustaining, "cost_to_serve_total": serve,
            "channel_cost_total": channel, "business_sustaining_cost": business, "operating_profit": operating,
            "capital_charge_total": capital, "economic_profit": rnd(operating + capital), "ledger_cost": ledger,
            "assigned_cost": assigned, "unassigned_cost": rnd(ledger - assigned), "whale_total": total,
            "whale_peak": peak, "whale_unrealized": rnd(peak - total),
        }

    # ---- direct costing (DC-02) ------------------------------------------------

    def action_cmc_generate_direct_costing(self, dimension="product"):
        self.ensure_one()
        if self.state != "done":
            raise UserError(self.env._("Compute the run before generating a direct costing statement."))
        if dimension not in ("product", "customer", "segment"):
            raise UserError(self.env._("Unknown dimension %s.", dimension))
        unassigned = self.env._("Unassigned")
        lines = defaultdict(lambda: defaultdict(float))
        if dimension == "product":
            for row in self.product_ids:
                line = lines[row.product_id.display_name or unassigned]
                line["revenue"] += row.revenue
                line["variable_cost"] -= row.variable_cost
                line["specific_fixed_cost"] -= row.product_sustaining_cost
        else:
            for row in self.customer_ids:
                label = (row.partner_id if dimension == "customer" else row.segment_value_id).display_name or unassigned
                line = lines[label]
                line["revenue"] += row.revenue
                line["variable_cost"] -= row.variable_cost
                line["specific_fixed_cost"] -= row.product_sustaining_cost + row.cost_to_serve + row.channel_cost
        specific = sum(line["specific_fixed_cost"] for line in lines.values())
        return self.env["cmc.direct.costing"].create({
            "name": self.env._("%(run)s - by %(dimension)s", run=self.name, dimension=dimension),
            "company_id": self.company_id.id, "date_from": self.date_from, "date_to": self.date_to, "mode": "evoluto",
            "common_fixed_cost": self._cmc_round(self.contribution_total - specific - self.operating_profit),
            "line_ids": [Command.create({"name": name, **vals}) for name, vals in lines.items()],
        })

    def _cmc_open_direct_costing(self, dimension):
        return self.action_cmc_generate_direct_costing(dimension=dimension).get_formview_action()

    def action_cmc_direct_costing_product(self):
        return self._cmc_open_direct_costing("product")

    def action_cmc_direct_costing_customer(self):
        return self._cmc_open_direct_costing("customer")

    def action_cmc_direct_costing_segment(self):
        return self._cmc_open_direct_costing("segment")
