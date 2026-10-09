import logging
from collections import defaultdict

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

# referenced model -> (registry type); a template maps to its single variant
TYPES = {"product.product": "product", "res.partner": "customer", "project.project": "project"}


def migrate(cr, version):
    """AD-106: stamp the registry M2o from the informational cmc_cost_object_ref."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    env.flush_all()
    cr.execute(
        "SELECT id, company_id, cmc_cost_object_ref FROM account_analytic_line "
        "WHERE cmc_cost_object_ref IS NOT NULL AND cmc_cost_object_id IS NULL"
    )
    lines = [(lid, cid, *ref.split(",", 1)) for lid, cid, ref in cr.fetchall()]
    if not lines:
        return
    # template id -> its variant, only when it has exactly one
    tmpl_ids = {int(rid) for _l, _c, model, rid in lines if model == "product.template"}
    variants = {}
    if tmpl_ids:
        cr.execute(
            "SELECT product_tmpl_id, min(id) FROM product_product WHERE product_tmpl_id IN %s "
            "GROUP BY product_tmpl_id HAVING count(*) = 1",
            (tuple(tmpl_ids),),
        )
        variants = dict(cr.fetchall())
    wanted = defaultdict(set)  # (company, model) -> ids
    resolved = []  # (line, company, model, id)
    for lid, cid, model, rid in lines:
        rid = int(rid)
        if model == "product.template":
            model, rid = "product.product", variants.get(rid)
        if model in TYPES and model in env and rid:
            wanted[(cid, model)].add(rid)
            resolved.append((lid, cid, model, rid))
    # a deleted record maps nothing
    existing = {
        key: set(env[key[1]].with_context(active_test=False).browse(ids).exists().ids)
        for key, ids in wanted.items()
    }
    object_by_key = {}
    for (cid, model), ids in existing.items():
        records = env[model].browse(sorted(ids))
        objects = env["cmc.cost.object"]._cmc_ensure_objects(records, company=env["res.company"].browse(cid), object_type=TYPES[model])
        object_by_key.update({(cid, model, rec.id): obj.id for rec, obj in zip(records, objects)})
    line_ids, object_ids = [], []
    for lid, cid, model, rid in resolved:
        if (cid, model, rid) in object_by_key:
            line_ids.append(lid)
            object_ids.append(object_by_key[(cid, model, rid)])
    if line_ids:
        cr.execute(
            "UPDATE account_analytic_line l SET cmc_cost_object_id = v.obj "
            "FROM unnest(%s::int[], %s::int[]) AS v(line_id, obj) "
            "WHERE l.id = v.line_id AND l.cmc_cost_object_id IS NULL",
            (line_ids, object_ids),
        )
    env.invalidate_all()
    stamped = set(line_ids)
    unmapped = defaultdict(int)
    for lid, _cid, model, rid in lines:
        if lid not in stamped:
            unmapped[f"{model},{rid}"] += 1
    for ref, count in unmapped.items():
        _logger.warning("cmc_base: cost object reference %s cannot be mapped to the registry (%d line(s) left empty)", ref, count)
