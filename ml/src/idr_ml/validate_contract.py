"""Small dependency-free stage-one validation for shared feature metadata."""

from __future__ import annotations

from .contract import expected_window_samples, load_feature_spec, repository_root


def main() -> None:
    spec = load_feature_spec()
    required = {"contract_version", "coordinate_frames", "time", "input_channels", "labels"}
    missing = required.difference(spec)
    if missing:
        raise SystemExit(f"feature contract missing keys: {sorted(missing)}")
    samples = expected_window_samples(spec)
    if samples <= 0 or len(spec["input_channels"]) != 9:
        raise SystemExit("feature contract must define a positive window and nine raw IMU channels")
    roots = ["ml", "mobile", "edge", "shared"]
    absent = [root for root in roots if not (repository_root() / root).is_dir()]
    if absent:
        raise SystemExit(f"target roots missing: {absent}")
    print(
        "stage=1 contract=ok "
        f"version={spec['contract_version']} samples_per_window={samples} "
        f"channels={len(spec['input_channels'])}"
    )


if __name__ == "__main__":
    main()
