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
