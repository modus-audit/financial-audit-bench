# Differential privacy release

The [36-charge ledger](dp-priors/accounting.json) reports total `rho ≈ 0.251`,
equivalent to `(epsilon ≈ 3.647, delta = 1e-5)`
under add-or-remove-one-complete-donor-binder adjacency. A binder includes its
related files and repeated records.

## Release files

- [scope.json](dp-priors/scope.json): released targets and channels.
- [accounting.json](dp-priors/accounting.json): per-charge privacy costs.
- [provenance.json](dp-priors/provenance.json): output-field-to-charge mappings.
- [manifest.json](dp-priors/manifest.json): checksums for the release files.
- [Prior data](../../src/financial_audit_bench/synthetic_binders/data/dp/releases/dp-priors/): financial priors and AP/AJE blocks used by generation.
- [definitions/](definitions/): query shapes, contribution bounds, categories, and mechanism definitions.
