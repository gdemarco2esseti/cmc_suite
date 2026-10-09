import logging
from collections import defaultdict

from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import SQL, float_compare, float_round

from .cmc_utils import largest_remainder

_logger = logging.getLogger(__name__)


def _solve_linear(matrix, vector):
    """Gaussian elimination with partial pivoting for a small dense system
    matrix @ x = vector. Returns x. Raises ValueError if singular."""
    n = len(vector)
    a = [row[:] + [vector[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            raise ValueError("singular system")
        a[col], a[pivot] = a[pivot], a[col]
        piv = a[col][col]
        a[col] = [v / piv for v in a[col]]
        for r in range(n):
            if r != col and a[r][col]:
                factor = a[r][col]
                a[r] = [v - factor * a[col][i] for i, v in enumerate(a[r])]
    return [a[i][n] for i in range(n)]




class CmcClosingRun(models.Model):
    _name = "cmc.closing.run"
    _description = "Cost Center Closing Run"
    _order = "date_to desc, id desc"
    _check_company_auto = True

    name = fields.Char(required=True, default=lambda self: self.env._("Closing"))
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True
    )
    currency_id = fields.Many2one(related="company_id.currency_id")
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    method = fields.Selection(
        selection=[
            ("direct", "Direct"),
            ("step_down", "Step-down"),
            ("reciprocal", "Reciprocal"),
        ],
        required=True,
        default="direct",
    )
    state = fields.Selection(
        selection=[("draft", "Draft"), ("done", "Computed")],
        default="draft",
        required=True,
    )
    line_ids = fields.One2many("cmc.closing.line", "run_id")
    cost_analysis_ids = fields.One2many("cmc.cost.analysis.line", "run_id")

    # ---- data gathering -------------------------------------------------

    def _centers(self):
        return self.env["account.analytic.account"].search(
            [
                ("cmc_center_type", "!=", False),
                ("company_id", "in", [False] + self.company_id.ids),
            ]
        )

    def _initial_costs(self, centers):
        return centers._cmc_cost_groups(
            self.date_from, self.date_to, company=self.company_id
        )

    def _proportion_maps(self, to_close, centers):
        """Build, in ONE query, the per-provider allocation proportions.
        Returns (prop_full, prop_final): provider_id -> {receiver_id: fraction}.
        prop_full normalizes over all receivers; prop_final over final ones only."""
        final_ids = set(
            centers.filtered(lambda c: c.cmc_center_type == "final").ids
        )
        services = self.env["cmc.center.service"].search(
            [
                ("provider_id", "in", to_close.ids),
                ("company_id", "in", [False] + self.company_id.ids),
            ]
        )
        qty_rounding = 10 ** -self.env["decimal.precision"].precision_get(
            "Product Unit"
        )
        raw = defaultdict(lambda: defaultdict(float))
        for svc in services:
            raw[svc.provider_id.id][svc.receiver_id.id] += svc.quantity

        def _normalize(weights):
            total = sum(weights.values())
            # a residual quantity is not an allocation base: dividing by it
            # would explode the proportions instead of reporting "no base"
            if float_compare(total, 0.0, precision_rounding=qty_rounding) <= 0:
                return {}
            return {rid: q / total for rid, q in weights.items()}

        prop_full, prop_final = {}, {}
        for pid in to_close.ids:
            weights = raw.get(pid, {})
            prop_full[pid] = _normalize(weights)
            prop_final[pid] = _normalize(
                {rid: q for rid, q in weights.items() if rid in final_ids}
            )
        return prop_full, prop_final

    @api.constrains("date_from", "date_to")
    def _check_dates_ordered(self):
        for run in self:
            if run.date_to < run.date_from:
                raise ValidationError(
                    self.env._("The period end date cannot precede its start date.")
                )

    def _conserve(self, total, raw):
        """Round raw {key: value} so the rounded parts re-sum to `total` exactly."""
        rounding = self.company_id.currency_id.rounding
        try:
            return largest_remainder(total, raw, rounding)
        except ValueError as err:
            raise ValidationError(
                self.env._("Allocation rounding failed to converge.")
            ) from err

    # ---- engine ---------------------------------------------------------

    def action_compute(self):
        if self.ids:
            # serialize concurrent Compute on the same run(s); flush first so the
            # lock sees the pending ORM writes on these rows
            self.env.flush_all()
            self.env.cr.execute(
                SQL(
                    "SELECT id FROM cmc_closing_run WHERE id IN %s FOR UPDATE",
                    tuple(self.ids),
                )
            )
        for run in self:
            run._compute_closing()
        return True

    def _compute_closing(self):
        self.ensure_one()
        centers = self._centers()
        initial = self._initial_costs(centers)
        to_close = centers.filtered(
            lambda c: c.cmc_center_type in ("auxiliary", "intermediate")
        )
        prop_full, prop_final = self._proportion_maps(to_close, centers)

        if self.method == "reciprocal":
            received, distributed = self._run_reciprocal(to_close, initial, prop_full)
        else:
            received, distributed = self._run_sequential(
                to_close, initial, prop_full, prop_final
            )

        # idempotent: rebuild result lines
        self.line_ids.unlink()
        vals = []
        for center in centers:
            ini = initial.get(center.id, 0.0)
            rec = received.get(center.id, 0.0)
            dist = distributed.get(center.id, 0.0)
            vals.append(
                {
                    "run_id": self.id,
                    "center_id": center.id,
                    "cost_initial": ini,
                    "cost_received": rec,
                    "cost_distributed": dist,
                    "cost_residual": ini + rec - dist,
                }
            )
        self.env["cmc.closing.line"].create(vals)
        self._build_cost_analysis(centers, received)
        self.state = "done"

    def _build_cost_analysis(self, centers, received):
        """Materialize the cost-analysis matrix (center x element) in both
        pre- and post-ribaltamento states, tying out to this closing."""
        self.cost_analysis_ids.unlink()
        cost_by = centers._cmc_cost_groups(
            self.date_from, self.date_to, by_element=True, company=self.company_id
        )
        to_close = set(
            centers.filtered(
                lambda c: c.cmc_center_type in ("auxiliary", "intermediate")
            ).ids
        )
        final_ids = set(centers.filtered(lambda c: c.cmc_center_type == "final").ids)
        ribalt = self.env.ref(
            "cmc_base.cost_element_ribaltamento", raise_if_not_found=False
        )
        if not ribalt:
            # the record is noupdate="1", so a reinstall will not bring it back:
            # say it out loud instead of silently merging the reallocated cost
            # into the "no element" bucket
            _logger.warning(
                "cmc_base.cost_element_ribaltamento is missing: reallocated "
                "cost will be booked without a cost element."
            )
        vals = []
        for (cid, eid), amount in cost_by.items():
            vals.append(
                {"run_id": self.id, "center_id": cid, "element_id": eid,
                 "state": "pre", "amount": amount}
            )
            # post: to-close centers are emptied; others keep their own cost
            if cid not in to_close:
                vals.append(
                    {"run_id": self.id, "center_id": cid, "element_id": eid,
                     "state": "post", "amount": amount}
                )
        # finals absorb the reallocated auxiliary/intermediate cost
        for cid, amount in received.items():
            if cid in final_ids and amount:
                vals.append(
                    {"run_id": self.id, "center_id": cid,
                     "element_id": ribalt.id if ribalt else False,
                     "state": "post", "amount": amount}
                )
        # conservation guard: pre total must equal post total
        currency = self.company_id.currency_id
        pre_tot = sum(v["amount"] for v in vals if v["state"] == "pre")
        post_tot = sum(v["amount"] for v in vals if v["state"] == "post")
        if currency.compare_amounts(pre_tot, post_tot) != 0:
            raise ValidationError(
                self.env._(
                    "Cost analysis matrix not conserved: pre %(pre)s vs post %(post)s.",
                    pre=pre_tot,
                    post=post_tot,
                )
            )
        self.env["cmc.cost.analysis.line"].create(vals)

    def _ordered_providers(self, to_close, prop_full):
        """Step-down order: centers serving more other to-close centers first."""
        close_ids = set(to_close.ids)

        def serves_count(provider):
            return sum(1 for rid in prop_full.get(provider.id, {}) if rid in close_ids)

        return to_close.sorted(key=lambda c: (-serves_count(c), c.id))

    def _run_sequential(self, to_close, initial, prop_full, prop_final):
        """Direct and step-down share the same sweep; they differ only in which
        receivers are eligible at each step."""
        received = defaultdict(float)
        distributed = defaultdict(float)
        current = {c.id: initial.get(c.id, 0.0) for c in to_close}

        order = to_close if self.method == "direct" else self._ordered_providers(
            to_close, prop_full
        )
        closed = set()

        for provider in order:
            amount = current[provider.id]
            if self.method == "direct":
                props = prop_final.get(provider.id, {})
                hint = self.env._(
                    " (the direct method ignores services to other auxiliary centers)"
                )
            else:
                eligible = {
                    rid: w
                    for rid, w in prop_full.get(provider.id, {}).items()
                    if rid not in closed and rid != provider.id
                }
                total = sum(eligible.values())
                props = {rid: w / total for rid, w in eligible.items()} if total else {}
                hint = ""
            if amount and not props:
                raise ValidationError(
                    self.env._(
                        "Cost center %(name)s cannot be closed: no allocation "
                        "base toward final centers.%(hint)s",
                        name=provider.display_name,
                        hint=hint,
                    )
                )
            shares = self._conserve(amount, {rid: w * amount for rid, w in props.items()})
            for rid, share in shares.items():
                received[rid] += share
                if rid in current:
                    current[rid] += share
            distributed[provider.id] = amount
            closed.add(provider.id)
        return received, distributed

    def _run_reciprocal(self, to_close, initial, prop_full):
        providers = list(to_close)
        n = len(providers)
        idx = {p.id: i for i, p in enumerate(providers)}
        total_initial = sum(initial.get(p.id, 0.0) for p in providers)

        for p in providers:
            if initial.get(p.id, 0.0) and not prop_full.get(p.id):
                raise ValidationError(
                    self.env._(
                        "Cost center %(name)s cannot be closed: it has cost but "
                        "renders no service.",
                        name=p.display_name,
                    )
                )

        final_ids = {
            rid
            for p in providers
            for rid in prop_full.get(p.id, {})
            if rid not in idx
        }
        if total_initial and not final_ids:
            raise ValidationError(
                self.env._(
                    "Reciprocal closing has no path to any final center: the "
                    "auxiliary centers only serve each other."
                )
            )

        # build (I - A^T): S = initial + sum_j prop[j][p] * S[j]
        amat = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
        for j_p in providers:
            j = idx[j_p.id]
            for rid, w in prop_full.get(j_p.id, {}).items():
                if rid in idx:  # provider receiver
                    amat[idx[rid]][j] -= w
        vector = [initial.get(p.id, 0.0) for p in providers]
        try:
            solved = _solve_linear(amat, vector)
        except ValueError as exc:
            raise ValidationError(
                self.env._("The reciprocal services form an unsolvable system.")
            ) from exc
        s = {providers[i].id: solved[i] for i in range(n)}

        received = defaultdict(float)
        distributed = defaultdict(float)
        final_raw = defaultdict(float)
        for p in providers:
            distributed[p.id] = s[p.id]
            for rid, w in prop_full.get(p.id, {}).items():
                flow = w * s[p.id]
                received[rid] += flow
                if rid not in idx:  # final receiver
                    final_raw[rid] += flow
        # enforce exact conservation on final absorption
        rounding = self.company_id.currency_id.rounding
        conserved = self._conserve(
            float_round(total_initial, precision_rounding=rounding), dict(final_raw)
        )
        for rid in final_raw:
            received[rid] = conserved.get(rid, received[rid])
        return received, distributed


class CmcClosingLine(models.Model):
    _name = "cmc.closing.line"
    _description = "Cost Center Closing Result Line"
    _order = "run_id, center_id"

    run_id = fields.Many2one(
        "cmc.closing.run", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(related="run_id.company_id", store=True)
    currency_id = fields.Many2one(related="run_id.currency_id")
    center_id = fields.Many2one(
        "account.analytic.account", required=True, index=True, ondelete="cascade"
    )
    center_type = fields.Selection(related="center_id.cmc_center_type", store=True)
    cost_initial = fields.Monetary()
    cost_received = fields.Monetary()
    cost_distributed = fields.Monetary()
    cost_residual = fields.Monetary()
