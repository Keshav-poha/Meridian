# Shared contracts

This directory is deliberately language-neutral. Do not add a field to a target implementation before adding it here and incrementing `contract_version`.

- `config/feature_spec.json` defines preprocessing and model tensor order.
- `schemas/telemetry.schema.json` defines replay/live telemetry consumed by Developer Mode.
- `schemas/model_manifest.schema.json` defines portable model metadata and normalization references.
