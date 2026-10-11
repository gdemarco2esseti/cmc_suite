"""Stress test 2026-10 (corsia A) - FX-011: the CMC center on the MO form is a labelled field."""

from lxml import etree

from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestFx011MoCenterLabel(TransactionCase):
    """FX-011: inside div[@name='bom_div'] (an o_row) the field renders as an unlabelled box."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(
            cls.env, login="fx011_user", groups="base.group_user,mrp.group_mrp_user,cmc_base.group_cmc_user"
        )

    def _form_arch(self):
        view = self.env.ref("mrp.mrp_production_form_view")
        return etree.fromstring(self.env["mrp.production"].with_user(self.user).get_view(view.id, "form")["arch"])

    def test_fx011_center_not_inside_bom_div(self):
        # FX-011 AC: the CMC center is its own form row, not glued into the BoM row
        arch = self._form_arch()
        nodes = arch.xpath("//field[@name='cmc_center_id']")
        self.assertEqual(len(nodes), 1, "a controlling user must see the CMC center on the MO form")
        self.assertFalse(nodes[0].xpath("ancestor::div[@name='bom_div']"), "cmc_center_id must not live inside bom_div")

    def test_fx011_center_has_label(self):
        # FX-011 AC: the field shows its label ("CMC Cost Center") - a field directly in a <group> or with a <label for>
        arch = self._form_arch()
        node = arch.xpath("//field[@name='cmc_center_id']")[0]
        labelled = node.getparent().tag == "group" or bool(arch.xpath("//label[@for='cmc_center_id']"))
        self.assertTrue(labelled, "cmc_center_id must render with a label")
        self.assertNotEqual(node.get("nolabel"), "1")
        string = self.env["mrp.production"].with_user(self.user).fields_get(["cmc_center_id"])["cmc_center_id"]["string"]
        self.assertTrue(string)
