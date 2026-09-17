# 0001 — Validate through two independent backends

**Status:** accepted
**Date:** 2026-09-17
**Phase:** 1

## Context

Every number this project will publish — extraction accuracy, first-pass validation
rate, post-repair rate — is produced by the validation harness. If the harness is
wrong, the metrics are wrong in a way that looks like success. A harness that is
too lenient reports a pipeline that works better than it does, and nothing
downstream would catch it.

OpenPEPPOL publishes the Peppol BIS Billing rules as **Schematron source**, not as
compiled stylesheets. Running them means compiling `.sch` to XSLT first, through
the three-stage ISO Schematron skeleton. That compile step is where the realistic
failure modes live:

- The Peppol rule set defines `xsl:function` elements inline (`u:mod97-0208` for
  Belgian enterprise numbers, `u:gln`, `u:checkPIVA`, and others). The ISO skeleton
  **silently drops** these unless `allow-foreign=true` is passed. Without it,
  compilation succeeds, validation runs, and the affected rules simply never fire.
- Schematron `report` elements fire when a condition *is* met, the inverse of
  `assert`. A parser that only collects `failed-assert` silently loses real errors.

Both failures are invisible: documents still validate, the suite still passes, and
the harness quietly under-reports. That is exactly the failure mode that would
corrupt the published metrics.

## Decision

Run the rule sets through two independent backends behind one interface, both
reducing to the same `ValidationFinding` type:

- **`saxon`** — stylesheets this project compiles from Schematron source with the
  ISO skeleton. The default, and what the pipeline uses.
- **`official`** — the stylesheets OpenPEPPOL compiled and ships, extracted from
  the Apache-2.0 `phive-rules-peppol` jar. An oracle, not a runtime dependency.

`tests/test_backend_agreement.py` asserts both backends produce an identical set of
`(layer, rule_id, severity)` triples for every fixture, valid and broken. Message
text and location are deliberately excluded from that comparison: they are worded
differently by each toolchain and carry no extra information about which rule fired.

The comparison runs at rule version **3.0.18** on both sides, while the pipeline
validates against **3.0.20**. OpenPEPPOL only redistributes compiled stylesheets
for some releases, and 3.0.18 is the newest one available. Pinning both sides to a
common version makes the test compare *compilers* rather than rule versions, which
is the thing actually under suspicion.

Current result: **15/15 documents agree.**

## What this does not prove

Both backends execute on Saxon. This test verifies that our *compilation* of the
Schematron is faithful; it does not independently verify the XSLT *runtime*. A Saxon
bug would be invisible to it.

That residual risk is accepted rather than closed. Saxon is the de facto reference
XSLT processor and the Java validators in this ecosystem run on it too, so a second
runtime would mostly re-test Saxon against itself. The compile step is where this
project introduces risk, and that is where the test is aimed.

The agreement test also cannot detect a rule that is wrong *upstream*, or a fixture
that fails to exercise a rule at all. `test_agreement_suite_is_not_vacuous` guards
the degenerate case where both backends return nothing and the comparison passes
trivially.

## Trade-offs

**Cost.** Two backends, a versioned artefact layout, and a second rule version
pinned purely for testing. Phase 1 took meaningfully longer than a single-backend
harness would have.

**Version skew.** The agreement test lags the production rule set by two releases.
A compilation bug introduced by something new in 3.0.19 or 3.0.20 would not be
caught until OpenPEPPOL publishes compiled stylesheets for a later release.

**What was given up.** The original intent was to use `phive`, the OpenPEPPOL
reference validator, as the second backend. It turns out phive is distributed only
as a Java library, with no CLI artifact — wiring it in would have meant a Maven
build and a hand-written Java entry point inside a Python project, plus Maven in
CI. Using its published *stylesheets* instead gets most of the assurance at a small
fraction of the cost, and is honest about covering less.

## What I would do differently

**Pin the production rule set to the oracle version.** Validating against 3.0.20
while only verifying compilation at 3.0.18 means the version actually used is the
version not cross-checked. Pinning both to 3.0.18 would make the guarantee exact,
at the cost of running two releases behind — a real trade for a compliance project,
but the current arrangement optimises for looking current over being verified, and
that is the wrong way round for a project whose whole argument is measurement.

**Decide the fixture strategy before writing fixtures.** Broken variants started as
committed XML files and were reworked into declared mutations of a single valid
base. The second design is clearly better — base and variants cannot drift, and the
mutation *is* the description of what is broken — but it was rework that a few
minutes of thought would have avoided.
