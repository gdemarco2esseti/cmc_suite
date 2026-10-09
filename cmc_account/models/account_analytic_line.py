import logging
from collections import defaultdict

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

CLASSIFICATION_FIELDS = ("cmc_behavior", "cmc_directness", "cmc_cost_element_id")


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
            self.browse(ids).with_context(cmc_auto_classify=True).write(dict(key))

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        # a classification provided explicitly at create time = manual
        if not self.env.context.get("cmc_auto_classify"):
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
        auto = self.env.context.get("cmc_auto_classify")
        # a user write of a TRUTHY classification value marks the line as manual
        manual = not auto and any(vals.get(fname) for fname in CLASSIFICATION_FIELDS)
        if manual:
            # never mutate the caller's dict: core reuses one vals dict across
            # several write() calls in a loop.
            vals = dict(vals, cmc_classified_manually=vals.get(
                "cmc_classified_manually", True
            ))
        res = super().write(vals)
        if not auto:
            if manual or "cmc_classified_manually" in vals:
                self._cmc_store_manual_classification()
            if vals.keys() & self._cmc_classify_trigger_fields():
                self.filtered(
                    lambda line: not line.cmc_classified_manually
                )._cmc_auto_classify()
        return res

    # ------------------------------------------------------------------
    # manual override persistence (survives draft / re-post)
    # ------------------------------------------------------------------

    def _cmc_store_manual_classification(self):
        """Mirror the manual classification onto the journal item."""
        vals_by_move_line = []
        for line in self:
            move_line = line.move_line_id
            if not move_line:
                continue
            vals_by_move_line.append((move_line.id, {
                "cmc_classified_manually": line.cmc_classified_manually,
                "cmc_behavior": line.cmc_behavior or False,
                "cmc_directness": line.cmc_directness or False,
                "cmc_cost_element_id": line.cmc_cost_element_id.id,
            }))
        if not vals_by_move_line:
            return
        batches = defaultdict(list)
        for move_line_id, vals in vals_by_move_line:
            batches[tuple(sorted(vals.items()))].append(move_line_id)
        # why sudo: the controller classifying a cost line may have no write
        # access to journal items; the written fields are CMC-only metadata,
        # never accounting figures.
        AML = self.env["account.move.line"].sudo()
        for key, ids in batches.items():
            AML.browse(ids).write(dict(key))

    # ------------------------------------------------------------------
    # classification engine
    # ------------------------------------------------------------------

    def _cmc_auto_classify(self):
        """Fill-only auto-classification: restore any manual override persisted
        on the journal item, then derive the element from the financial account
        mapping and apply the first matching rule. Never touches manually
        classified lines; never creates new lines."""
        self = self.filtered(lambda line: not line.cmc_excluded)
        if not self:
            return True
        Rule = self.env["cmc.classification.rule"]
        rules_by_company = {}
        # why sudo: read-only lookup of the CMC override stored on the journal
        # item; a warehouse or shop-floor user posting a cost line has no read
        # access to account.move.line.
        manual_move_lines = {
            move_line.id: move_line
            for move_line in self.move_line_id.sudo()
            if move_line.cmc_classified_manually
        }
        # why sudo: read-only read of the account -> cost-element mapping, which
        # any user posting a cost line must be able to trigger. Resolved ONCE
        # for the whole batch: calling .sudo() inside the loop would build a
        # fresh environment per line and defeat the ORM prefetch (one extra
        # query per line).
        element_by_account = {
            account.id: account.cmc_cost_element_id.id
            for account in self.general_account_id.sudo()
        }
        vals_by_line = []
        for line in self:
            source = manual_move_lines.get(line.move_line_id.id)
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
            center = line.cmc_center_id  # non-stored compute: read once per line
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
        self._cmc_write_batched(vals_by_line)
        return True
