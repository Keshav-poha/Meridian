# Map-matching safety rule

Road matching is a display and consistency aid. It must never turn an uncertain
dead-reckoning estimate into a confident-looking point on a neighbouring road.

`safe_hmm_map_match` is therefore fail-closed. For every observation it accepts
a snap only when all of these conditions hold:

1. The closest eligible road segment is no farther than **20 m** from the raw
   DR point (configurable with `max_snap_distance_m`).
2. The closest different OSM way is at least **8 m** farther away
   (configurable with `ambiguity_distance_margin_m`). Adjacent segments of the
   same way do not count as a competing road.
3. When calibrated heading and speed of at least **2 m/s** are supplied, the
   road's undirected bearing is within **45 degrees** of the vehicle heading.
4. The Viterbi smoother still selects that point's closest unambiguous way.

If any rule fails, the function leaves the raw DR coordinate untouched, returns
`accepted=False`, zero map confidence, and an explicit rejection reason. A
consumer must render this as **no road match**, rather than portraying the raw
coordinate as road-constrained.

This policy intentionally sacrifices coverage around dense parallel roads,
interchanges, and large DR errors. It is safer than a nearest-road-only matcher.
It does not yet prove routable/topological correctness: a production live
matcher still needs directed-road access, turn restrictions, GNSS covariance,
and a causal fixed-lag filter rather than a full offline Viterbi decode.

`hmm_map_match` remains as a backwards-compatible coordinate wrapper. New code
should call `safe_hmm_map_match` and propagate its `accepted`, `confidence`, and
`rejection_reasons` values to the UI or log.

## Mobile runtime policy

The mobile runtime uses the same fail-closed principle with a lightweight local
segment matcher. While a valid GNSS position is accepted, it may cache nearby
eligible OSM road geometry on the device. During a GNSS outage, it considers a
dead-reckoning prediction for a road constraint only when all of the following
hold:

1. The segment is within **18 m** of the prediction.
2. A different candidate road is at least **8 m** farther away.
3. At speeds of **1.5 m/s** or greater, the undirected road bearing is within
   **50 degrees** of the predicted heading.

The most recently accepted road may be retained only when it is within 4 m of
the best candidate. If no cached geometry exists, a request fails, or any safety
gate rejects the candidate, the raw inertial prediction is shown unchanged.

Raw GNSS positions are never road-constrained. This deliberately preserves
legitimate off-road locations such as walking paths, parking areas, and service
accesses even if they are absent from the cached road geometry.
