# CMC Suite Refactor — Master Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> Each block has its own detailed plan (`2026-10-09-cmc-refactor-block<N>.md`), written when the block starts: block N+1 depends on how block N landed.

**Goal:** Consolidate the 10-module CMC suite (one run lifecycle, one bridge hook, smaller engines) and close the defects the 2026-10-09 rōnin pass found, with the 241-test regression staying green throughout.

**Architecture:** Defects first (block 1, red tests that reproduce the real core flow), then the shared `cmc.run.mixin` in `cmc_base` carrying the lifecycle decided in AD-001/AD-002 (block 2), then a declarative bridge hook in `cmc_account` (block 3), then pure-step engine splits and simplify passes (block 4), then gates and close-out (block 5).

**Tech Stack:** Odoo 19 (ORM `models.Constraint`, `Domain`, `SQL`), TransactionCase `post_install`, conda env `odoo-19`, PostgreSQL 17.

---

## Decisions (user, 2026-10-09)

| ID | Decision |
|----|----------|
| AD-001 | A computed (`done`) run is **locked**: any create/write/unlink on its input lines AND on its header input fields raises `UserError`. The only way back is an explicit `action_reset_to_draft`, which **deletes the results**. Applies to closing, costing, abc, tdabc, joint. Changes joint (today: silent auto-invalidate) and full costing (today: reset keeps stale results). |
| AD-002 | Resetting a closing run is **forbidden** while a `done` costing run references it (`UserError` naming the costing runs). No cascade. |
| AD-003 | Scope: blocks 1–5, checkpoint with the user after each block. |
| AD-005 | Sign convention = Odoo natural: **cost = negative `amount`** on `account.analytic.line`. Cost Entry shows/accepts a positive cost and stores it negative; engines read cost as `-amount` in one canonical place (no scattered `abs()`). Migration flips existing manual CMC lines stored positive (lines without a source document). Done as Task 8 of block 1. |
| AD-006 | MRP labour twin = every analytic line of the `_post_labour` journal entry (expense AND WIP side), not only the line stamped on `time_ids`: the entry only moves value already measured by the workcenter line. |
| AD-007 | A CMC controller (`group_cmc_user`) must be able to classify any analytic line in the Unclassified queue (incl. bill lines when `hr_timesheet` is installed): CMC-only classification fields written via a checked `sudo()`; other fields keep core protection. Task 7. |
| AD-004 | Source of truth = repo `custom/cmc_suite01`; at close-out `custom/cmc_*` become symlinks to it (deleting the copies needs explicit confirmation). |

## Blocks

| # | Block | Est. | Ronin refs |
|---|-------|------|------------|
| 1 | Defect fixes, TDD (closing dates, cockpit currency, CVR actual-mode derivation, MRP labour twin, per-line manual classification) | ~14h | base RN-002/003, fc/dc RN-002, bridges RN-001/002 |
| 2 | `cmc.run.mixin` + `cmc.run.result.mixin` + AD-001/AD-002 on the 5 run models; `_cmc_qty_rounding` in `cmc_utils`; headers readonly when done | ~14h | base RN-001, fc/dc RN-001/003, abc RN-001/002/003 |
| 3 | Declarative bridge hook `_cmc_bridge_profiles()` in `cmc_account`, classification source tracking so a bridge can withdraw what it set; drop duplicated batching | ~10h | bridges RN-003 + hook |
| 4 | Engine splits into pure steps (`_compute_costing`, `_compute_cvr`, tdabc `_scenario_results`, joint weights), `_spread()` helper, closing filters once, dead code (`leverage_impact`), `/simplify` on reclass | ~7h | SIMPLIFY notes |
| 5 | Gates: `odoo-review` + `odoo-security`, rōnin re-pass, full regression, i18n regen (`odoo-bin i18n export` → polib), version bumps, README, symlinks, memory | ~4h | — |

## Invariants (never cut — every block's reviewer checks these)

- flush before `FOR UPDATE`; every `sudo()` keeps its `# why`.
- Conservation: closing pre = post; tdabc `unused_cost = cost − target` exact residual; `largest_remainder` `max_iter` + `ValueError`.
- Fail-loud guards (joint net<0 / no main / base≤0, tdabc practical≤0, costing "cost without base", reciprocal "no path to final center").
- Multi-company: `ir.rule` `parent_of company_ids` on every model, `_char_map` company filter, `with_user` + two-company tests.
- `cmc_reclass_mixin` stays non-stored; `account_analytic_line` inverse order (write new center, then clear old column); MRP UoM-hour labour discriminator; MRP adopt-core-line.
- Test classes `@tagged("post_install", "-at_install")`.
- (rōnin, end of block 1) AD-001 lock must NOT let `env.su` through: the CMC classification gate writes as superuser.
- Every bridge override of `_cmc_auto_classify` skips `cmc_excluded` and `cmc_classified_manually` lines (MRO runs bridges before the base). Block 3's hook must own this skip and the single gate entry point (`_cmc_classification_gate`), not only the bridge profiles.
- Overrides of `write` above core checks call `_cmc_classification_gate`; Enterprise checks are bypassed only with the `CMC_GATE` sentinel. System writes use sentinels (`CMC_AUTO`, `CMC_GATE`, `_SKIP_CURVE`), never booleans in context.

## Test command (every task, every block)

```bash
cd /Volumes/WDEXSSD/2ST/05_DEV/odoo19
PY=/opt/homebrew/Caskroom/miniconda/base/envs/odoo-19/bin/python
WT=custom/cmc_suite01/.worktrees/refactor-2026-10
M=cmc_base,cmc_full_costing,cmc_direct_costing,cmc_account,cmc_hr_timesheet,cmc_mrp,cmc_abc,cmc_joint_costs,cmc_tdabc,cmc_suite
# fresh DB WITH demo (plan-aware tests need the demo analytic plans); never cmc_all (seeded demo)
dropdb --if-exists cmc_rf_test && createdb cmc_rf_test
$PY odoo/odoo-bin -d cmc_rf_test --addons-path=odoo/addons,enterprise,$WT --http-port=8775 -i cmc_suite --without-demo=False --stop-after-init --log-level=warn
# -u MUST list all 10 modules, otherwise only 2 tests run
$PY odoo/odoo-bin -d cmc_rf_test --addons-path=odoo/addons,enterprise,$WT --http-port=8775 -u $M --test-enable \
  --test-tags=/cmc_base,/cmc_full_costing,/cmc_direct_costing,/cmc_account,/cmc_hr_timesheet,/cmc_mrp,/cmc_abc,/cmc_joint_costs,/cmc_tdabc,/cmc_suite \
  --stop-after-init --log-level=test 2>&1 | grep -E "tests when loading|^(FAIL|ERROR):"
```
Baseline 2026-10-09: `0 failed, 0 error(s) of 241 tests`. `ERROR: ... viola il vincolo` lines are expected (constraint tests).
Single module while iterating: `--test-tags=/cmc_mrp` (still `-u` the module under change).

## Status (2026-10-09)

| # | Commits | Gate | Notes |
|---|---------|------|-------|
| 1 | see block1.md | review + rōnin | closed earlier |
| 2 | a461aa6, f70318e | review (2 HIGH) + rōnin (RN-001/002) fixed | `cmc.run.result.mixin` became a CRUD guard (no shared fields: `run_id` comodel differs per model); `cmc.run.line.mixin` added for input lines; `qty_rounding(env)` module function |
| 3 | 9f26bf0, 34b278e | review (1 HIGH) + rōnin (RN-001) fixed | `cmc_bridge_source` = `key:fields`; gate accepts it only under CMC_AUTO; history corrected on first touch (release notes §7) |
| 4 | f5415a1, ac5994a, 420edc3, 832c422 | review + rōnin (RN-001) fixed | result rows only under CMC_RUN_STATE; CVR rounding kept; MRP adoption via `_cmc_fill` |
| 5 | 5747a78, eaff3b8, f1f7dfe | full regression 387/387 with EE bridge; upgrade rehearsal on a copy of cmc_all | symlinks `custom/cmc_*` pending explicit confirmation (AD-004) |

Close-out order (rōnin final RN-002), each step on explicit user confirmation:
1. merge `refactor/suite-2026-10` into the branch the main checkout `custom/cmc_suite01` runs;
2. replace `custom/cmc_*` copies with symlinks to `custom/cmc_suite01/cmc_*` (copies are identical to
   the main checkout today, so nothing is lost) and add `custom/cmc_timesheet_grid`;
3. `-u` all CMC modules on the working databases, following the release notes.
Symlinks before the merge would keep Odoo on the pre-refactor code.
