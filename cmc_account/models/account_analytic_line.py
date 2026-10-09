import logging
from collections import defaultdict

from odoo import api, fields, models
from odoo.exceptions import AccessError

from odoo.addons.cmc_base.models.account_analytic_line import CMC_AUTO

_logger = logging.getLogger(__name__)

# context sentinel (not a boolean, same reason as CMC_AUTO): marks a write made
# by the classification gate, so Enterprise bridges can tell it from a user write
CMC_GATE = object()
CLASSIFICATION_FIELDS = ("cmc_behavior", "cmc_directness", "cmc_cost_element_id")
# the center is NOT here: changing it moves the accounting allocation
CMC_CLASSIFICATION_FIELDS = {*CLASSIFICATION_FIELDS, "cmc_classified_manually"}
# what the classification engine writes: the user fields plus the bridge owner
CMC_ENGINE_FIELDS = {*CMC_CLASSIFICATION_FIELDS, "cmc_bridge_source"}
# value a bridge-filled field goes back to when the bridge withdraws it
# ('indirect' is the fill-only "unset" of the engine)
BRIDGE_UNSET = {"cmc_behavior": False, "cmc_directness": "indirect", "cmc_cost_element_id": False}


class AccountAnalyticLine(models.Model):
    _inherit = "account.analytic.line"

    cmc_classified_manually = fields.Boolean(
        string="Manually Classified",
        copy=False,
        index="btree_not_null",
        help="Set when a user classifies the line by hand; rules never overwrite it.",
    )
    cmc_bridge_source = fields.Char(
        string="Classified by Bridge",
        copy=False,
        readonly=True,
        help="Operational-bridge profile that classified the line and the "
        "fields it filled ('key:field,field'). When the line stops matching "
        "it, those fields are withdrawn.",
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
        auto = self.env.context.get("cmc_auto_classify") is CMC_AUTO
        if not (auto or self.env.su) and any("cmc_bridge_source" in vals for vals in vals_list):
            raise AccessError(self.env._("The bridge that classified a line is recorded by the system only."))
        lines = super().create(vals_list)
        # a classification provided explicitly at create time = manual
        if not auto:
            manual_ids = [
                line.id
                for line, vals in zip(lines, vals_list)
                if not line.cmc_is_relocation and any(vals.get(fname) for fname in CLASSIFICATION_FIELDS)
            ]
            if manual_ids:
                self.browse(manual_ids).write({"cmc_classified_manually": True})
        lines._cmc_auto_classify()
        return lines

    def _cmc_classification_gate(self, vals):
        """Let a CMC controller write ONLY classification fields on lines they can
        read, whoever owns the line. Returns True when it did the write.

        why sudo: the Unclassified queue sends the controller to lines of other
        users (bills, timesheets); record rules and the timesheet checks refuse
        a foreign write. The sudo skips the write ACL as well, on purpose
        (AD-007): the fields are CMC metadata, never accounting figures. It
        re-enters write(), so the manual marking still runs. Never for an
        excluded twin: sudo would defeat the superuser-only guard of cmc_base,
        which must refuse the write as the user.
        """
        # the engine also records the bridge owner; a user never writes it
        allowed = CMC_ENGINE_FIELDS if self.env.context.get("cmc_auto_classify") is CMC_AUTO else CMC_CLASSIFICATION_FIELDS
        if (
            self.env.su
            or not vals
            or not set(vals) <= allowed
            or not self.env.user.has_group("cmc_base.group_cmc_user")
        ):
            return False
        self.check_access("read")
        # relocation lines keep the classification of their run (posted under its sentinel only)
        if any(self.mapped("cmc_excluded")) or any(self.mapped("cmc_is_relocation")):
            return False
        self.sudo().with_context(cmc_classification_gate=CMC_GATE).write(vals)
        return True

    def write(self, vals):
        if self._cmc_classification_gate(vals):
            return True
        auto = self.env.context.get("cmc_auto_classify") is CMC_AUTO
        if "cmc_bridge_source" in vals and not (auto or self.env.su):
            raise AccessError(self.env._("The bridge that classified a line is recorded by the system only."))
        # "remove manual" is a true reset to what the rules say
        reset = not auto and "cmc_classified_manually" in vals and not vals["cmc_classified_manually"]
        # a user write of a TRUTHY classification value marks the line as manual
        manual = not (auto or reset) and any(vals.get(fname) for fname in CLASSIFICATION_FIELDS)
        # never mutate the caller's dict: core reuses one vals dict across
        # several write() calls in a loop.
        if reset:
            # 'indirect' is the fill-only sentinel of the engine
            vals = dict(vals, cmc_cost_element_id=False, cmc_behavior=False, cmc_directness="indirect", cmc_bridge_source=False)
        elif manual:
            # a hand classification is no longer the bridge's to withdraw
            vals = dict(vals, cmc_classified_manually=vals.get("cmc_classified_manually", True), cmc_bridge_source=False)
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

    @api.model
    def _cmc_bridge_profiles(self):
        """Declarative hook of the operational bridges (timesheet, MRP...).

        Each bridge appends one dict per kind of line it recognises:

        - ``key``: stable identifier, stored on the line it classifies;
        - ``match``: name of a line method returning True on a matching line;
        - ``element``: xmlid of the cost element to set, or False;
        - ``behavior`` / ``directness``: values to set, or False.

        The first matching profile wins. The engine owns everything else: it
        skips excluded and manually classified lines, fills only empty values,
        remembers the profile in ``cmc_bridge_source`` and withdraws that
        profile's values when the line no longer matches it.
        """
        return []

    def _cmc_auto_classify(self):
        """Fill-only auto-classification: restore any manual override persisted
        for its (journal item, center), apply the bridge profiles, derive the
        element from the financial account mapping and apply the first
        matching rule. Never touches manually classified or excluded lines;
        never creates new lines. One batched write for the whole set."""
        lines = self.filtered(lambda line: not (line.cmc_excluded or line.cmc_is_relocation))
        if not lines:
            return True
        Rule = self.env["cmc.classification.rule"]
        rules_by_company = {}
        profiles = self._cmc_bridge_profiles()
        profile_by_key = {profile["key"]: profile for profile in profiles}
        # resolved once: _cmc_element_ref logs a missing element on every call
        element_by_profile = {}
        for profile in profiles:
            element = self._cmc_element_ref(profile["element"]) if profile["element"] else None
            element_by_profile[profile["key"]] = element.id if element else False
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
                        "cmc_bridge_source": False,
                    }))
                continue
            if line.cmc_classified_manually:
                continue
            current = {
                "cmc_behavior": line.cmc_behavior or False,
                "cmc_directness": line.cmc_directness or False,
                "cmc_cost_element_id": line.cmc_cost_element_id.id,
                "cmc_bridge_source": line.cmc_bridge_source or False,
            }
            new = dict(current)
            profile = next((p for p in profiles if getattr(line, p["match"])()), None)
            owner, owned = self._cmc_parse_bridge_source(new["cmc_bridge_source"])
            if owner and (not profile or profile["key"] != owner):
                # withdraw only the fields the previous profile filled, and only
                # while they still hold its value
                previous = profile_by_key.get(owner)
                if previous:
                    profile_values = self._cmc_profile_values(previous, element_by_profile)
                    for fname in owned:
                        if new[fname] == profile_values[fname]:
                            new[fname] = BRIDGE_UNSET[fname]
                owner, owned = False, set()
            if profile:
                before = dict(new)
                self._cmc_fill(new, profile["behavior"], profile["directness"], element_by_profile[profile["key"]])
                owned |= {fname for fname in BRIDGE_UNSET if new[fname] != before[fname]}
                owner = profile["key"] if owned else False
            new["cmc_bridge_source"] = "%s:%s" % (owner, ",".join(sorted(owned))) if owner else False
            mapped_element = element_by_account.get(line.general_account_id.id)
            if not new["cmc_cost_element_id"] and mapped_element:
                new["cmc_cost_element_id"] = mapped_element
            company = line.company_id or self.env.company
            if company.id not in rules_by_company:
                rules_by_company[company.id] = Rule._rules_for_company(company)
            rule = next(
                (r for r in rules_by_company[company.id] if r._matches(line, center=center)),
                False,
            )
            if rule:
                self._cmc_fill(new, rule.cmc_behavior, rule.cmc_directness, rule.cmc_cost_element_id.id)
            vals = {fname: value for fname, value in new.items() if value != current[fname]}
            if vals:
                vals_by_line.append((line.id, vals))
        lines._cmc_write_batched(vals_by_line)
        return True

    @api.model
    def _cmc_parse_bridge_source(self, source):
        """'key:field,field' -> (key, {fields}); (False, set()) when empty."""
        if not source:
            return False, set()
        key, _sep, fnames = source.partition(":")
        return key, {fname for fname in fnames.split(",") if fname in BRIDGE_UNSET}

    @api.model
    def _cmc_profile_values(self, profile, element_by_profile):
        return {
            "cmc_behavior": profile["behavior"],
            "cmc_directness": profile["directness"],
            "cmc_cost_element_id": element_by_profile[profile["key"]],
        }

    @api.model
    def _cmc_fill(self, values, behavior, directness, element_id):
        """Fill-only merge into ``values``. The bare 'indirect' default counts
        as unset, so a bridge or a rule can still set 'direct'."""
        if behavior and not values["cmc_behavior"]:
            values["cmc_behavior"] = behavior
        if directness and values["cmc_directness"] in (False, "indirect"):
            values["cmc_directness"] = directness
        if element_id and not values["cmc_cost_element_id"]:
            values["cmc_cost_element_id"] = element_id
