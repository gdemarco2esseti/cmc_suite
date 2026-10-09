from odoo import models
from odoo.exceptions import UserError


class CmcPlanVersion(models.Model):
    _inherit = "cmc.plan.version"

    def action_cmc_std_rollup(self, products):
        """One card per product and per sub-assembly of its BoM, built from the core BoM and routing (AD-103)."""
        self.ensure_one()
        self._cmc_check_editable()
        company = self.company_id
        boms = self._cmc_std_boms(products)
        planned = {price.product_id.id: price.price for price in self.std_price_ids}
        labour = {line.center_id: line for line in self.std_rate_ids if line.rate_kind == "labour"}
        overhead = {line.center_id: line for line in self.std_rate_ids if line.rate_kind == "overhead"}
        lines = {product: bom.bom_line_ids.filtered(lambda line: not line._skip_bom_line(product)) for product, bom in boms.items()}
        operations = {product: bom.operation_ids.filtered(lambda op: not op._skip_operation_line(product)) for product, bom in boms.items()}
        centers = self._cmc_std_centers(self.env["mrp.workcenter"].union(*(ops.workcenter_id for ops in operations.values())))
        components = self.env["product.product"].union(*(product_lines.product_id for product_lines in lines.values())).with_company(company)
        costs = dict(zip(components.ids, components.mapped("standard_price")))
        self.card_ids.filtered(lambda card: card.product_id in boms).unlink()
        cards = dict(zip(boms, self.env["cmc.standard.card"].create([
            {"version_id": self.id, "product_id": product.id, "bom_id": bom.id} for product, bom in boms.items()
        ])))
        specs = {}
        for product, bom in boms.items():
            card = cards[product]
            per_unit = bom.product_uom_id._compute_quantity(bom.product_qty, product.uom_id, round=False)
            specs[product] = []
            for line in lines[product]:
                component = line.product_id
                vals = {
                    "card_id": card.id, "line_type": "material", "product_id": component.id,
                    "quantity": line.product_uom_id._compute_quantity(line.product_qty, component.uom_id, round=False) / per_unit,
                }
                if component in cards:
                    vals |= {"price_source": "card", "child_card_id": cards[component].id, "child": component}
                elif component.id in planned:
                    vals |= {"price_source": "planned", "price": planned[component.id]}
                else:
                    vals |= {"price_source": "product", "price": costs[component.id]}
                specs[product].append(vals)
            for op in operations[product]:
                specs[product] += self._cmc_std_operation_vals(card, centers[op.workcenter_id], op.time_total / 60.0 / per_unit, labour, overhead)
        unit_costs = {}

        def unit_cost(product):
            if unit_costs.get(product, 0.0) is None:
                raise UserError(self.env._("The bill of materials of %s contains itself.", product.display_name))
            if product not in unit_costs:
                unit_costs[product] = None
                unit_costs[product] = sum(vals["quantity"] * (vals["price"] if "child" not in vals else unit_cost(vals["child"])) for vals in specs[product])
            return unit_costs[product]

        vals_list = [{**vals, "price": unit_cost(vals["child"])} if "child" in vals else vals for product_vals in specs.values() for vals in product_vals]
        vals_list = [{key: value for key, value in vals.items() if key != "child"} for vals in vals_list]
        self.env["cmc.standard.card.line"].create(vals_list)
        return cards

    def _cmc_std_operation_vals(self, card, center, hours, labour, overhead):
        if center not in labour:
            raise UserError(self.env._("No labour rate for %(center)s in %(version)s.", center=center.display_name, version=self.display_name))
        common = {"card_id": card.id, "center_id": center.id, "quantity": hours}
        rate = labour[center]
        vals = [{**common, "line_type": "labour", "driver_id": rate.driver_id.id, "price": rate.rate}]
        if center in overhead:
            rate = overhead[center]
            vals += [
                {**common, "line_type": "overhead", "driver_id": rate.driver_id.id, "behavior": behavior, "price": price}
                for behavior, price in (("fixed", rate.fixed_rate), ("variable", rate.variable_rate))
            ]
        return vals

    def _cmc_std_boms(self, products):
        """{product: bom} of the products and, recursively, of every component that has a BoM (one search per level)."""
        Bom = self.env["mrp.bom"]
        pairs, seen, level = {}, set(), products
        while level:
            seen.update(level.ids)
            found = Bom._bom_find(level, company_id=self.company_id.id, bom_type="normal")
            pairs.update(found)
            level = self.env["product.product"].union(*(
                line.product_id for product, bom in found.items() for line in bom.bom_line_ids if not line._skip_bom_line(product)
            )).filtered(lambda component: component.id not in seen)
        # one recordset for every BoM: lines and operations are prefetched together
        by_id = {bom.id: bom for bom in Bom.browse([bom.id for bom in pairs.values()])}
        return {product: by_id[bom.id] for product, bom in pairs.items()}

    def _cmc_std_centers(self, workcenters):
        """{workcenter: its CMC cost center}, from the analytic distribution of the work center."""
        Account = self.env["account.analytic.account"]
        ids = {wc: [int(i) for key in wc.analytic_distribution or {} for i in key.split(",")] for wc in workcenters}
        centers = Account.browse({i for wc_ids in ids.values() for i in wc_ids}).filtered("cmc_center_type")
        result = {}
        for wc, wc_ids in ids.items():
            center = next((Account.browse(i) for i in wc_ids if i in centers.ids), None)
            if not center:
                raise UserError(self.env._("Work center %s has no CMC cost center in its analytic distribution.", wc.display_name))
            result[wc] = center
        return result
