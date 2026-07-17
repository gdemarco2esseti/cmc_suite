from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestDirectReclass(TransactionCase):
    def _dc(self):
        return self.env["cmc.direct.costing"].create({
            "name": "DC test", "common_fixed_cost": 1000,
            "line_ids": [
                (0, 0, {"name": "A", "revenue": 10000, "variable_cost": 6000,
                        "specific_fixed_cost": 1500}),
                (0, 0, {"name": "B", "revenue": 5000, "variable_cost": 2000,
                        "specific_fixed_cost": 800}),
            ]})

    def test_structure(self):
        d = self._dc()._cmc_reclass()
        self.assertEqual(len(d["columns"]), 2)
        labels = [r["label"] for r in d["rows"]]
        self.assertIn("MC lordo", labels)
        self.assertIn("Risultato operativo", labels)
        for r in d["rows"]:
            self.assertIn("kind", r)
            self.assertIn("total", r)

    def test_quadratura(self):
        dc = self._dc()
        d = dc._cmc_reclass()
        rows = {r["label"]: r for r in d["rows"]}
        ka = "l%s" % dc.line_ids[0].id
        self.assertAlmostEqual(rows["MC lordo"]["values"][ka], 10000 - 6000, 2)
        self.assertAlmostEqual(
            rows["Risultato operativo"]["total"], dc.operating_result, 2)
        self.assertIsNone(rows["Costi fissi comuni"]["values"])

    def test_json_field(self):
        dc = self._dc()
        self.assertEqual(dc.cmc_reclass_json, dc._cmc_reclass())
