# ADR-001: Adult Income dataset source and repository policy

Status: Accepted
Date: 2026-11-22

## Context
The primary dataset is UCI Adult Income (brief §2). The brief requires a pinned, locally stored source file with a recorded hash, documented columns, and explicit missing-value handling (brief §2, §6 `DatasetSource`). The original UCI distribution in `datasets/adult/` has format quirks:

- fields separated by `", "` (comma + space); no header row
- missing values encoded as `?`
- `adult.test` begins with a `|1x3 Cross validator` line and its labels carry a trailing period (`<=50K.`)
- trailing blank line(s) at end of file

Brief §14 left open which files to use and whether data may be committed.

## Options
1. Register `adult.data` only; normalize quirks in parsing code.
2. Register `adult.data` and `adult.test` as two separate sources.
3. Build a combined cleaned CSV derivative via a script and register that file.

For repository policy: commit raw files + fixture; gitignore raw files + commit fixture; gitignore everything and synthesize test data in code.

## Decision
- Register **`adult.data` only** (32,561 rows) as the Adult source for Milestone 1. `adult.test` may be registered later as a separate source if needed.
- The registered hash is of the **raw, unmodified file**. Parsing rules (column names, separator/whitespace handling, `?` → missing) live in versioned code, not in a derived file.
- **Raw UCI files are gitignored.** The README documents the download location and expected SHA-256.
- A **small sanitized fixture (~50 rows)** derived from `adult.data` is committed for tests.

Expected hash (computed 2026-11-22 from the local copy):

| File | Bytes | SHA-256 |
|---|---|---|
| `adult.data` | 3,974,305 | `5b00264637dbfec36bdeaab5676b0b309ff9eb788d63554ca0a249491c86603d` |

## Rationale
- Hashing the raw file pins exactly what UCI distributes. A cleaned derivative would add a pipeline stage whose own provenance would need documenting and pinning.
- A single source keeps Milestone 1 minimal. Train/test separation is irrelevant to descriptive analysis.
- Keeping data out of git keeps the repository lean and avoids redistributing a dataset copy. The committed fixture keeps tests fast, self-contained, and independent of the full file.

## Consequences
- Setup requires a manual download step. Registration must fail loudly on hash mismatch.
- Parsing code is part of the reproducibility contract. Changes to it alter derived results even though the source hash is unchanged, so the parser should carry a version recorded alongside the source.
- Tests must not assume full-dataset statistics. Known-answer benchmarks (brief §10A) run against the full file, outside the unit-test suite.

## Verification (planned)
- Unit tests parse the fixture: correct column names and dtypes, `?` mapped to missing, no trailing blank rows.
- Registration test: hash mismatch is rejected; a matching file registers with the correct row count.
- Manual check: registering the full `adult.data` yields 32,561 rows and the hash above.
