# Generator data

[manifest.json](manifest.json) maps resource IDs to package-relative JSON files.
The catalog loader reads each resource's `values` and optional `references`.

- `authored/`: identity pools and generation parameters.
- `registries/`: document mappings.
- `dp/`: released financial priors, AP/AJE blocks, and their manifest.

The [DP release guide](../../../../docs/dp-release/README.md) contains the ledger
and mechanism definitions.

Keep decimal-sensitive amounts as strings and dates in ISO 8601 format.
Generation and accounting calculations live in Python.
