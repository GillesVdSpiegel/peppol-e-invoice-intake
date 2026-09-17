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
| 2 | Test corpus: known-good UBL rendered to PDFs with perfect ground truth | Not started |
| 3 | Extraction and mapping pipeline with a single capped repair attempt | Not started |
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

## Fixtures

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
```

`check` exits non-zero if any document is invalid, so it composes in a shell
pipeline or a CI step.

## Licence

MIT, for the code and fixtures in this repository. The downloaded validation
artefacts keep their own licences; see [artefacts/NOTICE.md](artefacts/NOTICE.md).
