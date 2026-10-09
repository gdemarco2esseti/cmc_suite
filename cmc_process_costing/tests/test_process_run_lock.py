"""RN-001 / CB-6 - a computed process run is locked on EVERY stored field, outputs included.

CONTRACT (part, see common.py and ``cmc_base/tests/test_run_lock.py``)
- ``cmc.run.mixin._cmc_editable_when_done`` (class attribute, default ``("name",)``): on a ``done`` run a write to
  any stored field outside it raises UserError, unless under the ``CMC_RUN_STATE`` sentinel (the engine).
- Why: ``transferred_out_cost`` of D1 is the transferred-in cost D2 reads at Compute; a user edit of a computed
  output would be chained downstream as a certified figure.
"""

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from odoo.addons.cmc_base.models.cmc_run_mixin import CMC_RUN_STATE
from odoo.addons.cmc_base.tests.test_run_lock import RunLockMixin

from .common import ProcessCase


@tagged("post_install", "-at_install")
class TestProcessRunLock(RunLockMixin, ProcessCase):
    def test_user_cannot_write_transferred_out_on_done_run(self):
        # RN-001: a group_cmc_user cannot overwrite a computed output; D2 chains the ORIGINAL value
        d1 = self._d1()
        self.assertEqual(d1.state, "done")
        with self.assertRaises(UserError):
            d1.with_user(self.user).write({"transferred_out_cost": 1.0})
        d1.invalidate_recordset()
        self.assertEqual(d1.transferred_out_cost, 108900.0)
        d2 = self._d2(d1, env=self.user.env)
        self.assertEqual(self._line(d2, "transferred_in").period_cost, 108900.0)
        self.assertEqual(d2.transferred_out_cost, 124800.0)

    def test_every_locked_field_refused_on_done_run(self):
        # RN-001 / CB-6: every stored field outside _cmc_editable_when_done is refused on a computed run
        self._assert_locked_fields(self._d1().with_user(self.user))

    def test_name_editable_on_done_run(self):
        # RN-001: renaming a computed run changes no figure
        d1 = self._d1()
        d1.with_user(self.user).write({"name": "WP06 renamed"})
        self.assertEqual(d1.name, "WP06 renamed")

    def test_sentinel_writes_outputs(self):
        # RN-001: the engine (CMC_RUN_STATE sentinel) still writes the outputs of a computed run
        d1 = self._d1()
        d1.with_context(cmc_run_state=CMC_RUN_STATE).write({"transferred_out_cost": 1.0})
        self.assertEqual(d1.transferred_out_cost, 1.0)
