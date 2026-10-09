from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools import SQL

# why: the state of a run moves only through action_compute and
# action_reset_to_draft. An object() cannot travel over RPC (the context is
# JSON there), so neither a client nor a sudo() caller can fake a run as
# computed, or reopen it while its results stay on screen.
CMC_RUN_STATE = object()


class CmcRunMixin(models.AbstractModel):
    """Lifecycle shared by every CMC computation run (AD-001).

    A computed (`done`) run is locked: its header input fields and its input
    lines cannot change. The only way back is `action_reset_to_draft`, which
    deletes the results. Concrete models declare which fields are inputs and
    which One2many hold results, and implement `_cmc_compute()`."""

    _name = "cmc.run.mixin"
    _description = "CMC computation run lifecycle"

    # header fields and input One2many/Many2many a computed run must keep
    _cmc_input_fields = ()
    # One2many fields holding the computed results, emptied on reset
    _cmc_result_fields = ()

    state = fields.Selection(
        selection=[("draft", "Draft"), ("done", "Computed")],
        default="draft",
        required=True,
        readonly=True,
        copy=False,
    )

    # ---- CRUD -----------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        if not self._cmc_state_unlocked() and any(
            vals.get("state", "draft") != "draft" for vals in vals_list
        ):
            raise UserError(
                self.env._("A run is created in draft: compute it to make it computed.")
            )
        return super().create(vals_list)

    def write(self, vals):
        if "state" in vals and not self._cmc_state_unlocked():
            raise UserError(
                self.env._("Use Compute or Reset to Draft to change the state of a run.")
            )
        if not set(self._cmc_input_fields).isdisjoint(vals):
            # no env.su bypass: the classification gate writes as superuser
            self._cmc_check_editable()
        return super().write(vals)

    def unlink(self):
        self._cmc_check_no_dependents()
        return super().unlink()

    # ---- actions --------------------------------------------------------

    def action_compute(self):
        self._cmc_lock_rows()
        computed = self.filtered(lambda run: run.state == "done")
        if computed:
            raise UserError(
                self.env._(
                    "Run %(runs)s is already computed. Reset it to draft to compute it again.",
                    runs=", ".join(computed.mapped("display_name")),
                )
            )
        for run in self:
            run._cmc_compute()
            run._cmc_set_state("done")
        return True

    def action_reset_to_draft(self):
        self._cmc_lock_rows()
        self._cmc_check_no_dependents()
        self.check_access("write")
        for fname in self._cmc_result_fields:
            # why: results are written by the engine only (some result models
            # give users read access alone); deleting them is part of the
            # reset the user was just allowed to perform on the run itself
            self.mapped(fname).sudo().unlink()
        self._cmc_set_state("draft")
        return True

    # ---- hooks ----------------------------------------------------------

    def _cmc_compute(self):
        """Build the results of one run. Called by action_compute, which then
        marks the run computed."""
        raise NotImplementedError

    def _cmc_check_no_dependents(self):
        """Raise UserError when another computed document reads these runs and
        would be left pointing at results that no longer exist (AD-002)."""

    # ---- helpers --------------------------------------------------------

    def _cmc_state_unlocked(self):
        return self.env.context.get("cmc_run_state") is CMC_RUN_STATE

    def _cmc_set_state(self, state):
        self.with_context(cmc_run_state=CMC_RUN_STATE).write({"state": state})

    def _cmc_check_editable(self):
        computed = self.filtered(lambda run: run.state == "done")
        if computed:
            raise UserError(
                self.env._(
                    "Run %(runs)s is computed. Reset it to draft before changing its inputs.",
                    runs=", ".join(computed.mapped("display_name")),
                )
            )

    def _cmc_lock_rows(self):
        """Serialize concurrent Compute/Reset on the same runs."""
        if not self.ids:
            return
        # why: flush first, otherwise the row lock is taken on a state the
        # current transaction has not written yet and a concurrent update can
        # slip in between
        self.env.flush_all()
        self.env.cr.execute(
            SQL(
                "SELECT id FROM %s WHERE id IN %s FOR UPDATE",
                SQL.identifier(self._table),
                tuple(self.ids),
            )
        )
        # the state read before the lock may be stale
        self.invalidate_recordset(["state"])


class CmcRunLineMixin(models.AbstractModel):
    """Input line of a CMC run: it cannot change while its run is computed."""

    _name = "cmc.run.line.mixin"
    _description = "CMC run input line"

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines.run_id._cmc_check_editable()
        return lines

    def write(self, vals):
        self.run_id._cmc_check_editable()
        res = super().write(vals)
        if "run_id" in vals:
            self.run_id._cmc_check_editable()
        return res

    def unlink(self):
        self.run_id._cmc_check_editable()
        return super().unlink()
