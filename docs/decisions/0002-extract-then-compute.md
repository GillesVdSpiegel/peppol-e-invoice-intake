# 0002 — The model reads, the code computes

**Status:** accepted
**Date:** 2026-09-17
**Phase:** 3

## Context

The obvious way to build this pipeline is to ask the model for the UBL document
and let validation tell it what is wrong. The brief warns against exactly that:

> Tax total arithmetic is the hardest part. Extraction produces numbers; making
> them sum correctly under the rules is a separate problem.

EN 16931 states BR-CO-10 through BR-CO-17 as exact-equality rules. The VAT
breakdown must be rounded per *group*, not per line; the payable amount is a
chain of four dependent subtractions; a price with a third decimal breaks the
quantity-times-price identity if it is printed rounded. Phase 2 got six of twenty
hand-written invoices wrong on the first attempt for precisely these reasons, and
that was deterministic code with the rules in front of it.

Asking a language model to do that arithmetic in JSON, where every number is a
float, is asking it to fail in a way that is slow and expensive to correct.

## Decision

**The model extracts fields. Deterministic code computes every derived value.**

The model returns what is *printed*: line items, parties, dates, identifiers, and
the totals as they appear on the page. It is explicitly told not to calculate
anything. Mapping then builds the `Invoice` model from phase 2 — the same model
the corpus was generated with — and that model computes the line extension
amount, the VAT breakdown, and the four totals.

Every number crosses the boundary as a **string**, parsed into `Decimal` at the
edge. JSON has one numeric type and it is a float; `1832.98` round-trips as
`1832.9799999999998`, which fails an exact-equality rule.

Consequences that follow:

- The emitted document satisfies the arithmetic rules by construction.
- The repair attempt edits the *extraction*, never the XML — a validation failure
  is evidence the reading was wrong, and patching the XML would leave the
  underlying field wrong while hiding the symptom.
- The Peppol electronic address (BT-34 / BT-49) is **derived**, because no paper
  invoice prints one. Belgian parties get scheme 0208 from the enterprise number;
  others get their country's VAT scheme. This is recorded as a derivation, and
  when nothing printed on the page supports one, the document is flagged for a
  person rather than given an invented identifier.

## The problem this creates, and the answer to it

If the pipeline simply recomputes everything, a misread quantity produces a
**perfectly valid invoice for the wrong amount**. That is worse than an invalid
one: validation only ever sees the computed, self-consistent numbers, so nothing
downstream notices. The document would be legally well-formed and factually wrong.

So the model is also asked for the totals **as printed**, and `reconcile()`
compares them against the computed ones. A disagreement is surfaced as a
`RECONCILIATION` problem and never silently resolved. The printed VAT summary is
compared row by row as a second, independent check that usually localises which
line was misread.

This is why `PipelineResult` exposes `valid` and `reconciles` separately. A
document can be valid and not reconcile, and that combination is the one a person
most needs to see.

## What this does to the metrics, stated plainly

**First-pass validation rate will be high, and it does not mean much.** Totals are
computed rather than extracted, so the whole BR-CO family cannot fail for
arithmetic reasons. Phase 4 must report it, but it must not be read as a measure
of how well the model reads invoices.

The numbers that carry information are:

- **Per-field extraction accuracy** — what the model actually got right.
- **Reconciliation rate** — how often the computed totals match the printed ones.
  This is the honest end-to-end signal, and it is the one that catches the
  failure mode validation cannot.

Reporting the validation rate without that caveat would be the kind of unearned
number this project exists to avoid.

## Trade-offs

**What is given up.** The model never sees the shape of the final document, so it
cannot use UBL structure as a hint about what it is reading. A field that mapping
has no rule for is simply lost, where a model emitting UBL directly might have
placed it. In practice this favours the mapped approach: a field placed by
guesswork is not an improvement over a field reported as unmapped.

**Rigidity.** Extending to credit notes, document-level allowances or charges, or
non-Belgian extensions means writing mapping code, not just changing a prompt.
For v1 that is the right trade; at a wider scope it would start to hurt.

**Two requests for a repaired document.** The repair re-sends the PDF so the model
can look again, which roughly doubles the cost of any document that fails. The
alternative — repairing from the previous extraction alone — asks the model to
correct a misreading without being allowed to re-read, which is close to asking it
to guess again.

## What I would do differently

**Measure reconciliation before building the repair loop.** The repair attempt was
built on the assumption that validation failures are the main failure mode. Given
that deterministic mapping makes most validation failures impossible, the dominant
failure is probably a document that validates but does not reconcile — and the
repair loop as built is not triggered by that at all. A reconciliation mismatch is
strong, specific evidence of a misread line, and it would likely be a better
repair trigger than a validation failure. Phase 4's numbers will show whether that
is right; the ordering should have been the other way round.

**Ask for per-field confidence rather than a list of uncertainties.** The schema
asks the model to volunteer what it was unsure about, which works but is
all-or-nothing. A confidence attached to each extracted field would let the report
rank what a person should check first, instead of listing everything the model
happened to mention.
