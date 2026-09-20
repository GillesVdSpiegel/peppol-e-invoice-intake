# peppol-e-invoice-intake

Turn a messy supplier invoice PDF into a **Peppol BIS Billing 3.0 compliant UBL XML
document**, validated against the official EN 16931 and Peppol rule sets, plus a
structured report of everything that could not be mapped and why.

```console
$ peppol-e-invoice-intake check tests/fixtures/valid/be-standard-vat.xml
VALID tests/fixtures/valid/be-standard-vat.xml
```

Since 1 January 2026, structured e-invoicing has been mandatory for domestic B2B
transactions between Belgian VAT-registered businesses, based on EN 16931 with
Peppol BIS Billing 3.0 UBL as the reference format. A PDF alone no longer satisfies
the legal requirement, which leaves a lot of Belgian SMEs holding PDFs that need to
become UBL. This project is about that intake step.

## What this is not

- **It does not transmit anything over the Peppol network.** That needs a paid
  Access Point and a registered business identity. The deliverable here is
  network-ready UBL, not a sent invoice.
- **There is no hosted UI.** A CLI is the interface.
- **v1 handles invoices only** - no credit notes, no self-billing, and no
  non-Belgian national extensions.

## Status

| Phase | What it delivers | State |
|---|---|---|
| 1 | Validator harness: XSD + EN 16931 + Peppol, dual backend, fixtures, CI | **Done** |
| 2 | Test corpus: known-good UBL rendered to PDFs with perfect ground truth | **Done** |
| 3 | Extraction and mapping pipeline with a single capped repair attempt | **Done** (unmeasured) |
| 4 | Published accuracy, cost and latency metrics | Not started |
| 5 | Evaluation on real supplier invoices, reported separately | Not started |

## Metrics

Not yet measured. This table is filled in by Phase 4 and stays empty until then -
a project that publishes numbers it has not measured is worse than one that
publishes none.

| Metric | Synthetic corpus | Real invoices |
|---|---|---|
| Per-field extraction accuracy | - | - |
| Documents valid on first attempt | - | - |
| Documents valid after one repair attempt | - | - |
| Token cost per invoice | - | - |
| Median latency per invoice | - | - |

## The pipeline

```bash
peppol-e-invoice-intake convert invoice.pdf --out out/
```

    extract -> map -> emit -> validate -> (one repair attempt) -> emit -> validate

**The model reads; the code computes.** The model returns what is *printed* -
line items, parties, dates, identifiers, and the totals as they appear on the
page - and is explicitly told not to calculate anything. Deterministic code then
builds the invoice using the same arithmetic the corpus was generated with, so
BR-CO-10 through BR-CO-17 are satisfied by construction rather than by the model
getting the sums right. Every number crosses the boundary as a string and is
parsed into `Decimal`, because JSON's only numeric type is a float and those
rules are exact-equality checks.

That trade creates a specific danger, and the pipeline is built around it: if
totals are always recomputed, **a misread quantity produces a perfectly valid
invoice for the wrong amount**. Validation would never notice, because it only
ever sees the computed, self-consistent numbers. So the printed totals are
extracted too and compared against the computed ones. `PipelineResult` reports
`valid` and `reconciles` separately, and the second is the one that catches this.

Full reasoning, including what it costs:
[docs/decisions/0002-extract-then-compute.md](docs/decisions/0002-extract-then-compute.md).

### The repair attempt

Capped at one, deliberately. An unbounded loop converges on something that
validates, which is not the same as something that is right - the cheapest way to
satisfy a rule is often to drop the offending field. One attempt keeps "passed
after repair" a meaningful number rather than a measure of how long we were
willing to wait.

The repair edits the *extraction*, never the XML: a validation failure is evidence
the reading was wrong. The model is shown described problems - business term, what
the rule requires, where to look - never raw SVRL. It is told not to invent a
value to satisfy a rule, and a repair that validates no better than the original
is discarded rather than kept.

### Two arms

| Arm | Sends | Trade |
|---|---|---|
| `vision` | The PDF itself | Sees layout, columns and rules; costs more |
| `text` | Only the PDF text layer | Much cheaper; loses the spatial information that distinguishes a quantity from a unit price |

Both are kept so Phase 4 can report the cost/accuracy tradeoff rather than assert
it. A scanned invoice has no text layer, which the text arm reports as a failure
rather than hallucinating around.

### Spend is measured and capped

Cost per invoice is a published metric, so it is measured rather than estimated.
The same accounting is the guard: the ceiling is checked *before* every request,
so a misbehaving loop stops instead of running up a bill.

```bash
peppol-e-invoice-intake convert invoices/*.pdf --budget 5.00
```

### What the pipeline will not do

Anything it cannot map confidently is surfaced, not guessed. The clearest case:
no invoice prints a Peppol electronic address (BT-34 / BT-49), so it is derived -
scheme 0208 from a Belgian enterprise number, the country's VAT scheme otherwise.
When nothing printed on the page supports one, the document is flagged for a
person instead of being given an invented identifier, and it fails validation for
an honest reason.

## Validation harness

Three layers run in order; each finding is tagged with the layer that produced it,
because Peppol rejects things core EN 16931 accepts and the distinction matters
when reporting where documents fail.

1. **UBL 2.1 XSD** - structural. A schema-invalid document short-circuits the rest:
   Schematron over a broken tree describes the damage, not the cause.
2. **EN 16931** - the CEN/TC 434 `BR-*` rules.
3. **Peppol BIS Billing 3.0** - the `PEPPOL-*` rules, including Belgian specifics
   such as `PEPPOL-COMMON-R043`, the mod-97 check on 0208 enterprise numbers.

Everything reduces to one `ValidationFinding` type (layer, rule id, severity,
message, location, failed test). That type is also the contract Phase 3's repair
loop consumes - the model is handed described problems, never raw validator output.

### Two backends, and why

| Backend | Runs | Role |
|---|---|---|
| `saxon` | Stylesheets this project compiles from Schematron source via the ISO skeleton | Default; what the pipeline uses |
| `official` | The stylesheets OpenPEPPOL compiled and ships | Oracle |

`tests/test_backend_agreement.py` asserts the two produce identical findings for
every fixture. Currently **15/15 documents agree**. Without that test, "our harness
is correct" is an assumption; with it, it is a measurement - and every accuracy
number this project will publish rests on that layer being right.

The trade-off, and what this does *not* prove, is written up in
[docs/decisions/0001-dual-backend.md](docs/decisions/0001-dual-backend.md).

## The test corpus

Phase 2 inverts the usual problem. Instead of collecting invoice PDFs and
labelling them by hand, the corpus starts from a model, emits a UBL document and
a PDF from the same source, and gets perfect field-level ground truth for free.

```bash
peppol-e-invoice-intake corpus build      # 60 documents in about 25 seconds
peppol-e-invoice-intake corpus list       # what is in the catalogue
```

**20 base invoices x 3 layouts = 60 documents.** Ten invoices are written in
Dutch, six in French and four in English - language belongs to the invoice, not
to the render, because a Dutch-authored invoice shown with French column headings
is not a document any supplier would send. Flemish suppliers invoice in Dutch,
Walloon suppliers in French, exporters in English.

The catalogue is chosen for what breaks extraction, not for visual variety:
several VAT rates on one document, reverse charge, intra-community supply,
export, exempt and zero-rated supplies, line discounts, a prepaid amount, 42 line
items running past a page break, fractional quantities in hours and kilograms,
amounts from EUR 0.03 to EUR 584,180.50, and prices carrying a third decimal so
the arithmetic lands on a half cent.

### Six layouts

| Layout | What makes it different |
|---|---|
| `classic` | Conventional Belgian invoice, letterhead left, totals stacked right |
| `modern` | Coloured header band, amount due stated *before* the line detail, VAT summary as prose |
| `compact` | Dense 7.8pt type, hairline rules, VAT and totals side by side |
| `ledger` | Monospaced accounting style with the **amount column first** |
| `letterhead` | Formal letter, window-envelope address block, facts in a subject line |
| `minimal` | Near plain text: no rules, no colour, columns held apart by whitespace alone |

Each layout also uses **different wording for the same business terms** - the VAT
base is "Belastbare basis" on one and "Maatstaf" or "Bedrag excl. btw" on others.
Without that, a pipeline could memorise one string per field and score better
than it deserves.

The PDFs print Belgian conventions (`02/03/2026`, `1.520,50`) while the ground
truth stays canonical (`2026-03-02`, `1520.50`). Normalising that gap is part of
what Phase 3 has to do, so the corpus does not hand it over.

### Two things that keep the corpus honest

**Every generated document is validated before anything is written.** An invalid
invoice fails the build rather than landing on disk with a warning. Six of the
twenty failed the first time they were run - a zero-rated invoice carrying an
exemption reason that BR-Z-10 forbids, intra-community supplies missing the
deliver-to country, VAT numbers labelled as GLNs, and prices rounded to two
decimals so the line total no longer matched quantity times price. All six were
bugs in this code, caught by Phase 1.

**Every layout is rendered and read back.** `tests/test_corpus_layouts.py` renders
each layout to PDF, extracts the text, and asserts every total, line amount and
identifier is actually present. This exists because the `ledger` layout once
pushed three total amounts off the page with a CSS leader: the HTML was correct,
the PDF rendered without error, and the document was quietly missing data. A
figure clipped out of a PDF becomes ground truth asserting something the document
does not show.

Identifiers are computed, not invented: 0208 enterprise numbers satisfy
`PEPPOL-COMMON-R043`'s mod-97 check, GLNs satisfy `PEPPOL-COMMON-R040`'s GS1
check digit, and IBANs carry correct Belgian national and ISO 13616 check digits.

Sample output: [Dutch, classic](docs/samples/classic-nl.pdf) ·
[French reverse charge, letterhead](docs/samples/letterhead-fr.pdf) ·
[English intra-community, ledger](docs/samples/ledger-en.pdf)

The corpus is generated rather than committed, so `corpus/` is gitignored.

## Validation fixtures

One hand-authored valid Belgian invoice, plus 14 deliberately broken variants
generated from it by a single declared mutation each
([tests/broken_cases.py](tests/broken_cases.py)). Variants are generated rather
than committed so the base and its broken siblings cannot drift apart.

Each case pins the rule IDs it is expected to trip, captured from observed
validator output rather than from assumptions about the rule text. A fixture that
starts tripping a *different* rule fails the suite exactly as loudly as one that
stops failing at all.

Covered: missing `CustomizationID` / `ProfileID`, missing buyer and seller
`EndpointID`, missing `BuyerReference`, four distinct flavours of tax and total
arithmetic that does not reconcile, invalid country / currency / invoice type
codes, a malformed Belgian enterprise number, and a line item with no name.

## Setup

Requires Python 3.11+.

```bash
python -m venv .venv
.venv/Scripts/activate
pip install -e ".[dev]"
python scripts/fetch_artefacts.py
python -m playwright install chromium     # only needed to render corpus PDFs
pytest
```

On macOS or Linux, activate with `source .venv/bin/activate` instead.

`fetch_artefacts.py` downloads the rule sets pinned in
[artefacts/MANIFEST.json](artefacts/MANIFEST.json) and verifies every file against
a SHA-256 checksum. The artefacts are fetched rather than committed because not
every upstream carries a licence permitting redistribution - see
[artefacts/NOTICE.md](artefacts/NOTICE.md). Tests themselves never touch the
network.

## Usage

```bash
peppol-e-invoice-intake check invoice.xml
peppol-e-invoice-intake check invoice.xml --backend official
peppol-e-invoice-intake rules

peppol-e-invoice-intake corpus build      # generate the test corpus
peppol-e-invoice-intake corpus list       # base invoices and layouts
peppol-e-invoice-intake corpus status     # what is built on disk

peppol-e-invoice-intake convert x.pdf     # PDF to validated UBL (calls the API)
```

`convert` and `eval` are the only commands that cost money. They read
`ANTHROPIC_API_KEY` from a `.env` file in the project folder:

```bash
cp .env.example .env      # then put your key after the equals sign
```

`.env` is gitignored, and a test asks git itself to confirm it stays that way.
The key is loaded into the CLI's own process only when a paid command runs, so
your shell never holds it - which matters because an `ANTHROPIC_API_KEY` in the
environment silently switches tools such as Claude Code to per-token billing.
Only that one variable is read from the file. A key already set in the
environment takes precedence, and an `ant auth login` profile works too.

`check` exits non-zero if any document is invalid, so it composes in a shell
pipeline or a CI step.

## Licence

MIT, for the code and fixtures in this repository. The downloaded validation
artefacts keep their own licences; see [artefacts/NOTICE.md](artefacts/NOTICE.md).
