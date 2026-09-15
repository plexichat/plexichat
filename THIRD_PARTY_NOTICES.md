# Third-party notices

This file lists third-party Python code that ships with the Plexichat server. Use
it to check licenses before you distribute a build. The web client keeps its own
equivalent at `plexichat-client/THIRD_PARTY_NOTICES.md`.

The shipping set is the pinned runtime tree in `requirements.txt`. Test and
developer tooling lives in `requirements-dev.txt` and is deliberately excluded
from the runtime image, so it is not listed here.

## Summary table

Generated from the 92 distributions pinned in `requirements.txt`:

| License | Packages | Notes |
|---------|----------|-------|
| MIT | 32 | Permissive. |
| BSD-3-Clause | 25 | Permissive. |
| Apache-2.0 | 15 | Permissive. |
| BSD-2-Clause | 3 | Permissive. |
| MPL-2.0 | 3 | Weak copyleft, accepted (no linking obligation). |
| PSF-2.0 | 3 | Permissive. |
| MIT-0 | 2 | Permissive. |
| Apache-2.0 OR BSD-3-Clause | 1 | Permissive (choose either). |
| Apache-2.0 OR MIT | 1 | Permissive (choose either). |
| BSD-3-Clause OR 0BSD OR MIT OR Zlib OR CC0-1.0 | 1 | Permissive (choose either). |
| BSD-3-Clause OR Apache-2.0 | 1 | Permissive (choose either). |
| ISC | 1 | Permissive. |
| MIT OR BSD-3-Clause | 1 | Permissive (choose either). |
| MIT OR PSF-2.0 | 1 | Permissive (choose either). |
| MIT-CMU | 1 | Permissive (Pillow). |
| Unlicense | 1 | Public domain. |

## No GPL/LGPL packages ship

Every copyleft dependency has been removed from the runtime tree, so
`ALLOWED_COPYLEFT` in `ci-tooling/license_audit.py` is now empty and the audit
enforces the same rule for the server as the web client already enforced for its
own tree. The replacements:

| Was | License | Replaced by | License |
|-----|---------|-------------|---------|
| `fpdf2` | LGPL-3.0-only | `reportlab` | BSD-3-Clause |
| `odfpy` | Apache *or* GPL *or* LGPL (self-contradictory upstream metadata) | hand-rolled ODT writer in `src/api/routes/artifacts_export.py` | in-tree |
| `psycopg`, `psycopg-binary`, `psycopg-pool` | LGPL-3.0-only | `pg8000` | BSD-3-Clause |

Notes on the substitutions:

- **PDF export** (`reportlab`) — same artifact route, no functional change; the
  renderer wraps and paginates text itself.
- **ODT export** — an ODF text document is a ZIP of XML, and the export emits
  only a title and plain paragraphs, so the writer is ~60 lines of in-tree code
  rather than a dependency.
- **PostgreSQL** (`pg8000`) — pure-Python DB-API 2.0 driver. It speaks the same
  cursor interface (`execute`/`fetchone`/`fetchall`/`rowcount`) and `%s`
  placeholders the data layer already used, so `src/core/database/` and its
  callers are unchanged. Connection pooling is handled in-tree by
  `src/core/database/engines/pool.py`.

If you add a copyleft dependency deliberately, record it in `ALLOWED_COPYLEFT`
**and** in this file; the audit fails otherwise.

## Packages whose wheels carry no usable license metadata

Automated scanners report these as `UNKNOWN`. The audit resolves them by reading
the license text bundled in the wheel, which is why they are not failures:

- **google-crc32c `1.8.0`** — ships only a `License-File` entry, with no
  `License`, `License-Expression` or `License ::` classifier, so metadata-only
  scanners such as `pip-licenses` report `UNKNOWN`. The bundled text is the
  Apache License 2.0. It arrives transitively via `aiortc`.
- **`google-re2` and `authlib`** — resolve from their trove classifiers rather
  than PEP 639 metadata (BSD-3-Clause and BSD-3-Clause respectively).

Nothing else in the runtime tree is unidentified.

## Weak copyleft: MPL-2.0

`certifi`, `py-vapid` and `pywebpush` are MPL-2.0. MPL-2.0 is file-level
copyleft: it imposes no obligation on the rest of the program and no relinking
requirement, so it is treated as acceptable here. If you modify those packages
themselves, you must publish the modified files. `certifi` is a transitive
dependency of `httpx`/`requests` and cannot be avoided; `pywebpush` is what
performs the Web Push (VAPID) payload encryption.

## Relationship to other trees

- **Web client** (`plexichat-client/`) — separate notices file, audited with
  `npm run audit:licenses`. That tree is permissive-only.
- **Base image and services** — the Debian `python:3.14.6-slim-trixie` base, the
  PostgreSQL/Valkey/Alpine service images and the bundled `ffmpeg` are provided
  by their own upstreams under their own licenses and are not covered by this
  file.

## How to check licenses

1. Run the audit:

   ```bash
   python ci-tooling/license_audit.py
   ```

   It reads each distribution pinned in `requirements.txt`, resolves the license
   from PEP 639 `License-Expression`, the `License` field, the trove classifiers
   or the bundled license text, and prints the summary above. `--all` audits
   everything installed; `--format markdown` regenerates the table; `--format
   json` is for tooling.

2. If you add a dependency:

   1. Run the audit. A new copyleft or unidentified license fails it.
   2. Add the reason to `ALLOWED_COPYLEFT` / `ALLOWED_UNKNOWN` in
      `ci-tooling/license_audit.py` **and** to this file, or replace the
      dependency.
   3. Reject GPL, AGPL, LGPL, SSPL and commercial licenses unless you get
      written approval.

## Attribution

Ship this file alongside any distribution of the server, including container
images. It serves as the notice file for bundled code.
