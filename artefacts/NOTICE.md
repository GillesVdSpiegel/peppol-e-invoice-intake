# Validation artefact provenance

The rule sets this project validates against are third-party. They are **fetched,
not vendored**: `scripts/fetch_artefacts.py` downloads each file from a pinned
version and verifies it against a SHA-256 checksum recorded in `MANIFEST.json`.

The reason is licensing, not convenience. Redistribution terms differ per upstream
and one of them has no published licence at all, so the safe and honest default is
to point at the source rather than copy it into this repository.

| Artefact | Upstream | Pinned at | Licence |
|---|---|---|---|
| `sch/3.0.20/PEPPOL-EN16931-UBL.sch` | [OpenPEPPOL/peppol-bis-invoice-3](https://github.com/OpenPEPPOL/peppol-bis-invoice-3) | tag `v3.0.20` | **No published licence.** The repository has no LICENSE file; the file itself carries a notice that it reproduces CEN/EN 16931 business terms with permission from CEN. Fetched, never redistributed. |
| `sch/3.0.20/CEN-EN16931-UBL.sch` | same repository | tag `v3.0.20` | As above. Upstream documents it as the CEN/TC 434 artefact set, which is published separately under EUPL 1.2. |
| `sch/3.0.18/*.sch` | same repository | tag `3.0.18` | As above. Used only by the backend-agreement test. |
| `official-xslt/3.0.18/*.xslt` | [phive-rules-peppol](https://github.com/phax/phive-rules) jar on Maven Central | `3.2.10`, member `external/schematron/openpeppol/2024.11/` | Apache-2.0 |
| `skeleton/*.xsl` | [Schematron/schematron](https://github.com/Schematron/schematron) | tag `2020-10-01` | MIT |
| `xsd/**` | [OASIS UBL 2.1](https://docs.oasis-open.org/ubl/os-UBL-2.1/) | OASIS Standard `os-UBL-2.1` | OASIS IPR policy |

## Why two rule versions are pinned

`3.0.20` is the current Peppol BIS Billing release and is what the pipeline
validates against.

`3.0.18` is the newest release for which OpenPEPPOL's own **pre-compiled**
stylesheets are redistributable. They ship inside the Apache-2.0 phive-rules jar,
where that release is named `2024.11`. The backend-agreement test runs both
backends at `3.0.18` so that it compares *compilers* rather than rule versions.

## Re-pinning

```bash
python scripts/fetch_artefacts.py --update-manifest
```

Then review the `MANIFEST.json` diff and re-run `scripts/show_rules.py`. A rule
version bump can legitimately change which rule a fixture trips, and that change
should be visible in a commit rather than absorbed silently.
