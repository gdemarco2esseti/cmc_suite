from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestTdabcReclass(TransactionCase):
    def _run(self):
        res = self.env["cmc.tdabc.resource"].create(
            {"name": "R", "cost": 1000, "theoretical_time": 1000, "util_factor": 100})
        act = self.env["cmc.tdabc.activity"].create(
            {"name": "A", "code": "A", "resource_id": res.id, "base_time": 10})
        p1 = self.env["product.product"].create({"name": "P1"})
        run = self.env["cmc.tdabc.run"].create(
            {"name": "TD", "line_ids": [
                (0, 0, {"activity_id": act.id, "cost_object_type": "product",
                        "product_id": p1.id, "volume": 20})]})
        run.action_compute()
        return run, p1

    def test_structure(self):
        run, p1 = self._run()
        d = run._cmc_reclass()
        self.assertTrue(d["columns"])
        labels = [r["label"] for r in d["rows"]]
        self.assertTrue(any("Absorbed cost" in lb for lb in labels))

    def test_has_manual_scenario(self):
        run, p1 = self._run()
        d = run._cmc_reclass()
        key = "p%s" % p1.id
        manual = next(r for r in d["rows"]
                      if r["label"] == "Absorbed cost · estimate")
        self.assertIn(key, manual["values"])
        self.assertGreater(manual["total"], 0)
