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

## Implementation notes (as built, 2026-11-22)
- Parser `adult-v1` lives in `backend/app/analysis/datasets.py`; the registry with pinned hashes is in `backend/app/analysis/registry.py`. The parser version is stored on `dataset_sources.parser_version` and is part of the source's uniqueness key.
- The fixture `backend/tests/fixtures/adult_sample.data` holds the first 40 rows plus the first 10 later rows containing `?` (13 rows with missing values in total), byte-exact from `adult.data`, including the trailing blank line. SHA-256: `7e87fe36d36dd1e4df2d7cca00dd4a9716ee32d3c05d6bc2356fc717fc8a779e`. `.gitattributes` marks fixtures `-text` so that line-ending conversion cannot change the hash.
- Discovered during testing: with `keep_default_na=False`, pandas pads short rows and empty fields with `""` instead of failing, which would silently create a `""` category. The parser rejects empty fields, malformed rows, non-numeric values in numeric columns, and the `adult.test` label format (`<=50K.`).
- The hash is re-verified on the first load of a source in each process, so a file modified after registration fails with `DATASET_INTEGRITY`.

## Verification
- `tests/unit/test_parser.py`: column names and dtypes; `?` maps to missing, with counts checked per column against a stdlib reading of the raw file; the trailing blank line is ignored; short rows, long rows, empty fields, non-numeric values, and test-split labels are rejected. Passing.
- `tests/integration/test_registry_and_tools.py`: hash mismatch rejected with nothing written; path escape rejected; missing file rejected; registration idempotent; tampering after registration detected. Passing.
- Manual check, 2026-11-22: `python scripts/register_datasets.py adult` registered 32,561 rows with the pinned hash; re-running returned the same source ID. The missing counts (workclass 1,836, occupation 1,843, native_country 583) match commonly reported figures for `adult.data`. A test asserting 32,561 rows runs when the raw file is present and is skipped otherwise.
