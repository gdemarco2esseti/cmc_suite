from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestReclassMixin(TransactionCase):
    def test_abstract_raises(self):
        # a bare model inheriting only the mixin must raise NotImplementedError
        self.assertIn("cmc.reclass.mixin", self.env)
        mixin = self.env["cmc.reclass.mixin"]
        with self.assertRaises(NotImplementedError):
            mixin._cmc_reclass()

    def test_fmt_helpers(self):
        M = self.env["cmc.reclass.mixin"]
        self.assertEqual(M._reclass_fmt(1234.0, "plain", "€"), "€ 1.234")
        self.assertEqual(M._reclass_fmt(-1234.0, "subtract", "€"), "–€ 1.234")
        self.assertEqual(M._reclass_fmt(42.86, "percent", "€"), "42,9%")
        self.assertEqual(M._reclass_fmt(None, "plain", "€"), "")
