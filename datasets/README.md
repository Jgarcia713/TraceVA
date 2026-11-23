# Datasets

Raw dataset files are **not committed** (see `docs/decisions/ADR-001-adult-dataset-source.md`). Place them here manually. The backend reads from this directory by default (`DATA_DIR`).

## UCI Adult Income

- Source: https://archive.ics.uci.edu/dataset/2/adult (license: CC BY 4.0)
- Download the dataset archive and place its files under `datasets/adult/`.
- Only `adult.data` is registered (training split, 32,561 rows). Registration verifies its hash and refuses mismatches.

| File | Bytes | SHA-256 |
|---|---|---|
| `adult/adult.data` | 3,974,305 | `5b00264637dbfec36bdeaab5676b0b309ff9eb788d63554ca0a249491c86603d` |

Verify manually:

```bash
sha256sum datasets/adult/adult.data          # Git Bash / Linux
```
```powershell
Get-FileHash datasets\adult\adult.data -Algorithm SHA256   # PowerShell
```
