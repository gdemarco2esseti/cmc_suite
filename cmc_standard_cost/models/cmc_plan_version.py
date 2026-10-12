from collections import defaultdict

from odoo import fields, models
from odoo.exceptions import AccessError, UserError
from odoo.fields import Domain
from odoo.tools import float_compare
from odoo.tools.misc import format_date

from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE
from odoo.addons.cmc_base.models.cmc_plan_version import CmcPlanVersion as BaseVersion


class CmcPlanVersion(models.Model):
    _inherit = "cmc.plan.version"

    _cmc_input_fields = (*BaseVersion._cmc_input_fields, "standard_type")
    # the push snapshot is evidence: written by the confirmed push under the run sentinel, by nothing else
    _cmc_output_fields = ("std_push_date", "std_push_line_ids")

    standard_type = fields.Selection(
        [("basic", "Basic"), ("ideal", "Ideal"), ("attainable", "Attainable")], default="attainable", required=True,
    )
    std_price_ids = fields.One2many("cmc.standard.price", "version_id", copy=True)
    std_rate_ids = fields.One2many("cmc.standard.rate", "version_id", copy=True)
    card_ids = fields.One2many("cmc.standard.card", "version_id", copy=True)
    std_push_date = fields.Datetime(string="Pushed On", readonly=True, copy=False)
    std_push_line_ids = fields.One2many("cmc.std.push.line", "version_id", string="Pushed Prices", readonly=True, copy=False)

    # ---- engine ---------------------------------------------------------

    def _cmc_compute(self):
        super()._cmc_compute()
        if self.version_type == "standard":
            self._cmc_std_publish()

    def copy(self, default=None):
        new = super().copy(default)
        # the sub-assembly links of the copied lines are re-pointed to the cards of the copy
        cards = {card.product_id: card for card in new.card_ids}
        for line in new.card_ids.line_ids.filtered(lambda line: line.price_source == "card"):
            line.child_card_id = cards.get(line.product_id)
        return new

    def action_reset_to_draft(self):
        for version in self.filtered("std_push_date"):
            raise UserError(self.env._(
                "%(version)s: costs already pushed to the stock valuation on %(date)s. Create a new version.",
                version=version.display_name, date=format_date(self.env, version.std_push_date),
            ))
        return super().action_reset_to_draft()

    # ---- rate table (FC-08) ---------------------------------------------

    def action_cmc_std_prefill_rates(self, budget_version, basis="practical"):
        self.ensure_one()
        self._cmc_check_editable()
        if basis not in ("practical", "normal"):
            raise UserError(self.env._("The capacity basis is Practical or Normal."))
        figures = self._cmc_std_budget_figures(budget_version)
        if not figures:
            raise UserError(self.env._(
                "%(budget)s has no cost figure between %(date_from)s and %(date_to)s: enter them in the Budget tab of that version, then prefill again.",
                budget=budget_version.display_name, date_from=format_date(self.env, self.date_from), date_to=format_date(self.env, self.date_to),
            ))
        capacities = self._cmc_std_capacities(figures)
        vals_list = []
        for center, figure in figures.items():
            capacity = capacities.get(center)
            if not capacity:
                raise UserError(self.env._(
                    "%(center)s has budget figures but no capacity analysis covering %(version)s.",
                    center=center.display_name, version=self.display_name,
                ))
            common = {
                "version_id": self.id, "center_id": center.id, "driver_id": capacity.driver_id.id, "capacity_basis": basis,
                "capacity_qty": capacity[f"capacity_{basis}"], "budget_qty": capacity.capacity_budget,
            }
            if figure["fixed"] or figure["variable"]:
                vals_list.append({**common, "rate_kind": "overhead", "budget_fixed": figure["fixed"], "budget_variable": figure["variable"]})
            if figure["mod"]:
                vals_list.append({**common, "rate_kind": "labour", "budget_fixed": 0.0, "budget_variable": figure["mod"]})
        self.std_rate_ids.unlink()
        return self.env["cmc.standard.rate"].create(vals_list)

    def _cmc_std_budget_figures(self, budget_version):
        """{center: {fixed, variable, mod}} positive cost of the budget in the span: overhead excludes MP and MOD."""
        mp, mod = (self.env.ref(f"cmc_base.cmc_element_{code}") for code in ("mp", "mod"))
        domain = budget_version._cmc_line_domain(self.date_from, self.date_to) & Domain("center_id", "!=", False)
        figures = defaultdict(lambda: {"fixed": 0.0, "variable": 0.0, "mod": 0.0})
        for center, element, behavior, amount in self.env["cmc.budget.line"]._read_group(
            domain, ["center_id", "element_id", "behavior"], ["amount_company:sum"]
        ):
            key = "mod" if element == mod else behavior
            if element != mp:
                figures[center][key] -= amount
        return figures

    def _cmc_std_capacities(self, figures):
        capacities = {}
        for capacity in self.env["cmc.capacity.analysis"].search([
            ("company_id", "=", self.company_id.id), ("center_id", "in", [center.id for center in figures]),
            ("date_from", "<=", self.date_from), ("date_to", ">=", self.date_to),
        ]):
            capacities.setdefault(capacity.center_id, capacity)
        return capacities

    # ---- publishing (AD-108) --------------------------------------------

    def _cmc_std_publish(self):
        self.ensure_one()
        months = self._cmc_months()
        mod = self.env.ref("cmc_base.cmc_element_mod")
        produced = self.env.ref("cmc_mrp.cmc_measure_produced_qty")
        objects = self.env["cmc.cost.object"]._cmc_ensure_objects(self.card_ids.product_id, company=self.company_id)
        by_product = dict(zip(self.card_ids.product_id, objects))
        labour = self.std_rate_ids.filtered(lambda line: line.rate_kind == "labour")
        overhead = self.std_rate_ids - labour
        # why labour first: a lookup of the center total (no element filter) returns the latest row, the overhead one
        rows = [
            {"holder": line.center_id, "driver": line.driver_id, "cost_element": mod, "date": month, "value": line.rate}
            for line in labour for month in months
        ] + [
            {"holder": line.center_id, "driver": line.driver_id, "component": component, "date": month, "value": value}
            for line in overhead for month in months
            for component, value in (("fixed", line.fixed_rate), ("variable", line.variable_rate), ("total", line.rate))
        ] + [
            {"holder": by_product[card.product_id], "driver": produced, "component": component, "date": month, "value": value}
            for card in self.card_ids for month in months
            for component, value in (("total", card.unit_cost), ("variable", card.unit_variable), ("fixed", card.unit_fixed))
        ]
        # why sudo: published rates are engine rows (the approval carries the run sentinel), whoever approves
        Rate = self.env["cmc.rate"].sudo()
        holders = Domain.OR(
            Domain("holder_model", "=", holder_model) & Domain("holder_id", "in", [row["holder"].id for row in rows if row["holder"]._name == holder_model])
            for holder_model in {row["holder"]._name for row in rows}
        )
        Rate.search(Domain("origin_model", "=", self._name) & Domain("origin_id", "=", self.id) & holders).unlink()
        Rate._cmc_publish(self, rows)

    # ---- valuation (FC-09) ----------------------------------------------

    def _cmc_std_check_approved(self):
        if self.version_type != "standard" or self.state != "done":
            raise UserError(self.env._("%s is not an approved standard version.", self.display_name))

    def action_cmc_std_push_prices(self):
        self.ensure_one()
        if not self.env.su and not self.env.user.has_group("cmc_base.group_cmc_manager"):
            raise AccessError(self.env._("Only a Controlling Manager pushes standard costs to the valuation."))
        self._cmc_std_check_approved()
        if not self.env.context.get("cmc_std_push_confirmed"):
            wizard = self.env["cmc.std.push.wizard"].create({
                "version_id": self.id,
                "line_ids": [(0, 0, {"product_id": product.id, "old_cost": old, "new_cost": new}) for product, old, new in self._cmc_std_push_rows()],
            })
            return {
                "type": "ir.actions.act_window", "name": self.env._("Push Standard Prices"), "res_model": "cmc.std.push.wizard",
                "res_id": wizard.id, "view_mode": "form", "target": "new",
            }
        precision = self.env["decimal.precision"].precision_get("Product Price")
        changed = [(product, old, new) for product, old, new in self._cmc_std_push_rows() if float_compare(old, new, precision_digits=precision)]
        for product, _old, new in changed:
            product.standard_price = new
        # why sudo: the snapshot is read-only for every group (ACL); only this confirmed push writes it
        self.with_context(cmc_run_state=CMC_RUN_STATE).sudo().write({
            "std_push_date": fields.Datetime.now(),
            "std_push_line_ids": [(0, 0, {"product_id": product.id, "old_cost": old, "new_cost": new}) for product, old, new in changed],
        })
        return True

    def _cmc_std_push_rows(self):
        """[(product, current standard price, card unit cost)] of the standard-costed card products, one pass."""
        # why sudo: the controlling manager has no stock rights; the write is the core revaluation (product.value)
        card_costs = {card.product_id.id: card.unit_cost for card in self.card_ids}
        products = self.card_ids.product_id.sudo().with_company(self.company_id)
        return [(product, product.standard_price, card_costs[product.id]) for product in products if product.cost_method == "standard"]

    def _cmc_std_reconciliation(self):
        self.ensure_one()
        return [
            {
                "product": card.product_id, "card_unit_cost": card.unit_cost, "valuation_price": card.valuation_price,
                "difference": card.valuation_difference, "qty_available": card.qty_available, "value_difference": card.value_difference,
            }
            for card in self.card_ids
        ]
