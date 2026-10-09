"""RN-001 / CB-6 - structural lock of a computed run on EVERY stored field (cmc.run.mixin).

CONTRACT
- ``cmc.run.mixin._cmc_editable_when_done``: new class attribute, tuple of field names, default ``("name",)``.
  Every concrete model inheriting the mixin exposes it (inherited or overridden); each entry is a field of the
  model, never an input (``_cmc_input_fields``), a result (``_cmc_result_fields``) or ``state``.
- On a ``done`` run, ``write()`` of ANY stored (column) field outside ``_cmc_editable_when_done`` raises
  UserError - inputs AND outputs AND any other stored field (e.g. ``cmc.plan.version.parent_id``), for every
  caller (``group_cmc_user``, ``sudo``), unless under the ``CMC_RUN_STATE`` sentinel (Compute / Reset).
- ``name`` stays editable on a done run.
- Draft runs are not affected.
"""

from datetime import date, timedelta

from odoo.exceptions import UserError
from odoo.orm.models import MAGIC_COLUMNS
from odoo.tests.common import TransactionCase, new_test_user, tagged

from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE

_SKIP = set(MAGIC_COLUMNS) | {"state", "display_name"}


class _Rollback(Exception):
    pass


class RunLockMixin:
    """Shared probe (also used by the process costing tests): writes a different value in every locked field."""

    def _locked_fields(self, run):
        editable = set(getattr(type(run), "_cmc_editable_when_done", ("name",)))
        return [
            fname for fname, field in run._fields.items()
            if field.store and field.column_type and fname not in editable and fname not in _SKIP
        ]

    def _other_value(self, run, fname):
        field = run._fields[fname]
        current = run[fname]
        if field.type == "many2one":
            other = run.env[field.comodel_name].sudo().search([("id", "!=", current.id)], limit=1)
            return other.id or None
        if field.type == "boolean":
            return not current
        if field.type in ("integer", "float", "monetary"):
            return (current or 0) + 1
        if field.type in ("char", "text", "html"):
            return (current or "") + " changed"
        if field.type in ("date", "datetime"):
            return (current or date.today()) + timedelta(days=1)
        if field.type == "selection":
            keys = [key for key in field.get_values(run.env) if key != current]
            return keys[0] if keys else None
        return None

    def _assert_locked_fields(self, run):
        """Every stored field outside _cmc_editable_when_done refuses a write on the done run with UserError."""
        self.assertEqual(run.state, "done")
        leaks = []
        for fname in self._locked_fields(run):
            value = self._other_value(run, fname)
            if value is None:
                continue
            try:
                with self.env.cr.savepoint():
                    run.write({fname: value})
                    run.env.flush_all()
                    raise _Rollback
            except UserError:
                continue
            except _Rollback:
                leaks.append(fname)
            except Exception as exc:  # noqa: BLE001 - any other error means the lock is not the first guard
                leaks.append("%s (%s)" % (fname, type(exc).__name__))
            finally:
                run.env.invalidate_all()
        self.assertFalse(leaks, "%s: fields writable on a computed run: %s" % (run._name, ", ".join(leaks)))


@tagged("post_install", "-at_install")
class TestRunLock(RunLockMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        plan = cls.env["account.analytic.plan"].search([("parent_id", "=", False)]).filtered(
            lambda p: p._column_name() == "account_id"
        )[:1]
        cls.center = cls.env["account.analytic.account"].create(
            {"name": "RN001 center", "plan_id": plan.id, "cmc_center_type": "final"}
        )
        year = date.today().year
        cls.env["account.analytic.line"].create(
            {"name": "cost", "account_id": cls.center.id, "amount": -500, "date": date(year, 6, 15)}
        )
        cls.period = {"date_from": date(year, 1, 1), "date_to": date(year, 12, 31)}
        cls.user = new_test_user(cls.env, login="cmc_rn001_user", groups="base.group_user,cmc_base.group_cmc_user")
        cls.manager = new_test_user(cls.env, login="cmc_rn001_manager", groups="base.group_user,cmc_base.group_cmc_manager")
        cls.other_version = cls.env["cmc.plan.version"].create({"name": "RN001 other", **cls.period})

    def _closing(self):
        run = self.env["cmc.closing.run"].create({"name": "RN001 closing", "method": "direct", **self.period})
        run.action_compute()
        return run

    def _version(self):
        version = self.env["cmc.plan.version"].create({"name": "RN001 budget", **self.period})
        version.with_user(self.manager).action_approve()
        return version

    def _run_models(self):
        Mixin = type(self.env["cmc.run.mixin"])
        return [
            name for name in self.env.registry
            if name != "cmc.run.mixin" and not self.env[name]._abstract and issubclass(type(self.env[name]), Mixin)
        ]

    # ---- structural (CB-6) -----------------------------------------------

    def test_mixin_default_editable_when_done(self):
        # RN-001: the mixin declares the whitelist; by default only the name is editable on a computed run
        self.assertEqual(type(self.env["cmc.run.mixin"])._cmc_editable_when_done, ("name",))

    def test_every_run_model_declares_valid_whitelist(self):
        # CB-6: every run model of the registry has a whitelist made of real, non-input, non-result fields
        names = self._run_models()
        self.assertIn("cmc.closing.run", names)
        self.assertIn("cmc.plan.version", names)
        for name in names:
            Model = self.env[name]
            with self.subTest(model=name):
                editable = getattr(type(Model), "_cmc_editable_when_done", None)
                self.assertIsInstance(editable, tuple)
                self.assertFalse(set(editable) - set(Model._fields), "unknown fields in the whitelist")
                self.assertFalse(
                    set(editable) & (set(Model._cmc_input_fields) | set(Model._cmc_result_fields) | {"state"}),
                    "inputs / results / state cannot be editable on a computed run",
                )
                if "name" in Model._fields:
                    self.assertIn("name", editable)

    # ---- behavioural on concrete runs --------------------------------------

    def test_closing_done_locks_every_stored_field(self):
        # RN-001: a computed closing refuses any stored field outside the whitelist, as group_cmc_user
        self._assert_locked_fields(self._closing().with_user(self.user))

    def test_plan_version_done_locks_every_stored_field(self):
        # RN-001: an approved version refuses any stored field outside the whitelist (parent_id included)
        self._assert_locked_fields(self._version().with_user(self.user))

    def test_plan_version_done_parent_refused(self):
        # RN-001: parent_id is not an input but a done version still refuses it, for a user and for sudo
        version = self._version()
        with self.assertRaises(UserError):
            version.with_user(self.user).write({"parent_id": self.other_version.id})
        with self.assertRaises(UserError):
            version.sudo().write({"parent_id": self.other_version.id})
        self.assertFalse(version.parent_id)

    def test_name_editable_on_done_runs(self):
        # RN-001: renaming a computed run changes no figure
        for run in (self._closing(), self._version()):
            run.with_user(self.user).write({"name": "RN001 renamed"})
            self.assertEqual(run.name, "RN001 renamed")

    def test_sentinel_writes_on_done_run(self):
        # RN-001: the engine path (CMC_RUN_STATE) is not blocked by the lock
        version = self._version()
        version.with_context(cmc_run_state=CMC_RUN_STATE).write({"parent_id": self.other_version.id})
        self.assertEqual(version.parent_id, self.other_version)

    def test_draft_run_stays_writable(self):
        # RN-001: the lock applies to computed runs only
        version = self.env["cmc.plan.version"].create({"name": "RN001 draft", **self.period})
        version.with_user(self.user).write({"parent_id": self.other_version.id})
        self.assertEqual(version.parent_id, self.other_version)
