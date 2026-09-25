# Synthetic binder generator

Generate manufacturing or staffing binders from shipped DP priors and authored rules:

```bash
uv run fab generate-synthetic-binder staffing_services generated_binders/staffing --seed 42
```

Writes documents to `pbc_package/`, `direct_to_auditor/`, and `planning/`, plus a
`generation_manifest.json`. Existing output binders are replaced. No API calls required.

See [inputs](data/README.md).
