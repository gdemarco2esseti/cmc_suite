import logging
from collections import defaultdict

from odoo import api, fields, models

from odoo.addons.cmc_base.models.account_analytic_line import CMC_AUTO

_logger = logging.getLogger(__name__)

CLASSIFICATION_FIELDS = ("cmc_behavior", "cmc_directness", "cmc_cost_element_id")
# the center is NOT here: changing it moves the accounting allocation
CMC_CLASSIFICATION_FIELDS = {*CLASSIFICATION_FIELDS, "cmc_classified_manually"}


class AccountAnalyticLine(models.Model):
    _inherit = "account.analytic.line"

    cmc_classified_manually = fields.Boolean(
        string="Manually Classified",
        copy=False,
        index="btree_not_null",
        help="Set when a user classifies the line by hand; rules never overwrite it.",
    )

    # ------------------------------------------------------------------
    # helpers shared with the operational bridges
    # ------------------------------------------------------------------

    @api.model
    def _cmc_element_ref(self, xmlid):
        """Resolve a cost-element xmlid.

        The taxonomy records are ``noupdate="1"``: once deleted a module
        reinstall does NOT bring them back. Degrading silently would leave the
        bridges posting cost lines with no cost element forever, so log loudly
        and name the xmlid.
        """
        element = self.env.ref(xmlid, raise_if_not_found=False)
        if not element:
            _logger.warning(
                "CMC: cost element %s is missing (deleted?). Cost lines will be "
                "posted without a cost element until it is restored.", xmlid,
            )
        return element

    @api.model
    def _cmc_classify_trigger_fields(self):
        """Fields whose modification must re-run the classification engine on a
        non-manual line.

        Classification cannot fire from ``create`` only: the timesheet grid, the
        portal and most import paths create the row first and write the
        discriminating fields afterwards. The operational bridges extend this
        set with their own discriminators.
        """
        return {
            "general_account_id",
            "move_line_id",
            "partner_id",
            "product_id",
        } | set(self._get_plan_fnames())

    def _cmc_write_batched(self, vals_by_line):
        """Issue ONE write per distinct vals set instead of one per record.

        cmc_base overrides write() on analytic lines to resync capacity rows, so
        a per-record write on a 1000-line vendor bill costs thousands of extra
        queries -- multiplied by the number of installed bridges.
        """
        batches = defaultdict(list)
        for line_id, vals in vals_by_line:
            batches[tuple(sorted(vals.items()))].append(line_id)
        for key, ids in batches.items():
            self.browse(ids).with_context(cmc_auto_classify=CMC_AUTO).write(dict(key))

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        # a classification provided explicitly at create time = manual
        if self.env.context.get("cmc_auto_classify") is not CMC_AUTO:
            manual_ids = [
                line.id
                for line, vals in zip(lines, vals_list)
                if any(vals.get(fname) for fname in CLASSIFICATION_FIELDS)
            ]
            if manual_ids:
                self.browse(manual_ids).write({"cmc_classified_manually": True})
        lines._cmc_auto_classify()
        return lines

    def write(self, vals):
        auto = self.env.context.get("cmc_auto_classify") is CMC_AUTO
        # "remove manual" is a true reset to what the rules say
        reset = not auto and "cmc_classified_manually" in vals and not vals["cmc_classified_manually"]
        # a user write of a TRUTHY classification value marks the line as manual
        manual = not (auto or reset) and any(vals.get(fname) for fname in CLASSIFICATION_FIELDS)
        # never mutate the caller's dict: core reuses one vals dict across
        # several write() calls in a loop.
        if reset:
            # 'indirect' is the fill-only sentinel of the engine
            vals = dict(vals, cmc_cost_element_id=False, cmc_behavior=False, cmc_directness="indirect")
        elif manual:
            vals = dict(vals, cmc_classified_manually=vals.get("cmc_classified_manually", True))
        res = super().write(vals)
        if not auto:
            if vals.keys() & CMC_CLASSIFICATION_FIELDS:
                self._cmc_store_manual_classification()
            if reset:
                self._cmc_auto_classify()
            elif vals.keys() & self._cmc_classify_trigger_fields():
                self.filtered(
                    lambda line: not line.cmc_classified_manually
                )._cmc_auto_classify()
        return res

    # ------------------------------------------------------------------
    # manual override persistence (survives draft / re-post)
    # ------------------------------------------------------------------

    def _cmc_store_manual_classification(self):
        """Persist the manual classification per (journal item, cost center).

        Known edges, fixed on purpose: the override belongs to the journal item,
        so an item recreated in draft loses it (cascade); changing the center of
        a line leaves the record of the old center orphaned. Lines sharing the
        key (multi-plan distributions) share one classification: the last wins.
        """
        lines = self.filtered(lambda line: line.move_line_id and line.cmc_center_id)
        if not lines:
            return
        # why sudo: the controller classifying a cost line may have no access to
        # journal items; the stored fields are CMC-only metadata, never
        # accounting figures.
        Manual = self.env["cmc.manual.classification"].sudo()
        existing = {
            (rec.move_line_id.id, rec.center_id.id): rec
            for rec in Manual.search([("move_line_id", "in", lines.move_line_id.ids)])
        }
        wanted = {}  # key -> vals, or None when the key must be forgotten
        for line in lines:
            wanted[(line.move_line_id.id, line.cmc_center_id.id)] = {
                "cmc_behavior": line.cmc_behavior or False,
                "cmc_directness": line.cmc_directness or False,
                "cmc_cost_element_id": line.cmc_cost_element_id.id,
            } if line.cmc_classified_manually else None
        stale = Manual
        updates = defaultdict(list)
        to_create = []
        for key, vals in wanted.items():
            record = existing.get(key)
            if vals is None:
                stale |= record or Manual
            elif record:
                updates[tuple(sorted(vals.items()))].append(record.id)
            else:
                to_create.append(dict(vals, move_line_id=key[0], center_id=key[1]))
        stale.unlink()
        for vals, ids in updates.items():
            Manual.browse(ids).write(dict(vals))
        if to_create:
            Manual.create(to_create)

    # ------------------------------------------------------------------
    # classification engine
    # ------------------------------------------------------------------

    def _cmc_auto_classify(self):
        """Fill-only auto-classification: restore any manual override persisted
        for its (journal item, center), then derive the element from the financial account
        mapping and apply the first matching rule. Never touches manually
        classified lines; never creates new lines."""
        # why not filtered by the bridges' callers: bridge overrides run BEFORE
        # this base (MRO), so each of them skips excluded lines on its own.
        lines = self.filtered(lambda line: not line.cmc_excluded)
        if not lines:
            return True
        Rule = self.env["cmc.classification.rule"]
        rules_by_company = {}
        # why sudo: read-only lookup of the stored CMC override; a warehouse or
        # shop-floor user posting a cost line has no read access to the
        # journal items it hangs from.
        manual_by_key = {
            (rec.move_line_id.id, rec.center_id.id): rec
            for rec in self.env["cmc.manual.classification"].sudo().search(
                [("move_line_id", "in", lines.move_line_id.ids)]
            )
        }
        # why sudo: read-only read of the account -> cost-element mapping, which
        # any user posting a cost line must be able to trigger. Resolved ONCE
        # for the whole batch: calling .sudo() inside the loop would build a
        # fresh environment per line and defeat the ORM prefetch (one extra
        # query per line).
        element_by_account = {
            account.id: account.cmc_cost_element_id.id
            for account in lines.general_account_id.sudo()
        }
        vals_by_line = []
        for line in lines:
            center = line.cmc_center_id  # non-stored compute: read once per line
            source = manual_by_key.get((line.move_line_id.id, center.id))
            if source is not None:
                if not line.cmc_classified_manually:
                    vals_by_line.append((line.id, {
                        "cmc_behavior": source.cmc_behavior or False,
                        "cmc_directness": source.cmc_directness or False,
                        "cmc_cost_element_id": source.cmc_cost_element_id.id,
                        "cmc_classified_manually": True,
                    }))
                continue
            if line.cmc_classified_manually:
                continue
            company = line.company_id or self.env.company
            if company.id not in rules_by_company:
                rules_by_company[company.id] = Rule._rules_for_company(company)
            vals = {}
            mapped_element = element_by_account.get(line.general_account_id.id)
            if not line.cmc_cost_element_id and mapped_element:
                vals["cmc_cost_element_id"] = mapped_element
            rule = next(
                (r for r in rules_by_company[company.id] if r._matches(line, center=center)),
                False,
            )
            if rule:
                if rule.cmc_behavior and not line.cmc_behavior:
                    vals["cmc_behavior"] = rule.cmc_behavior
                # fill-only: treat the bare 'indirect' default as "unset" so a
                # rule does not clobber a value a bridge deliberately set (MOD/MP
                # write 'direct' before this engine runs last in the MRO).
                if rule.cmc_directness and line.cmc_directness in (False, "indirect"):
                    vals["cmc_directness"] = rule.cmc_directness
                if (
                    rule.cmc_cost_element_id
                    and "cmc_cost_element_id" not in vals
                    and not line.cmc_cost_element_id
                ):
                    vals["cmc_cost_element_id"] = rule.cmc_cost_element_id.id
            if vals:
                vals_by_line.append((line.id, vals))
        lines._cmc_write_batched(vals_by_line)
        return True
