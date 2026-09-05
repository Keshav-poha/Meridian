"""Offline OSM road-graph extraction and lightweight HMM map matching."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np

from .dead_reckoning import EARTH_RADIUS_M


def latlon_to_local_enu(latitude: float, longitude: float, origin_latitude: float, origin_longitude: float) -> tuple[float, float]:
    latitude_radians = math.radians(latitude)
    return (
        math.radians(longitude - origin_longitude) * EARTH_RADIUS_M * math.cos(math.radians(origin_latitude)),
        math.radians(latitude - origin_latitude) * EARTH_RADIUS_M,
    )


def download_osm_roads(path: str | Path, *, south: float, west: float, north: float, east: float) -> Path:
    """Download driveable OSM ways and their nodes from public Overpass."""
    query = f'''[out:json][timeout:60];
way["highway"~"motorway|trunk|primary|secondary|tertiary|unclassified|residential|service"]({south},{west},{north},{east});
(._;>;);out body;'''
    request = Request(
        "https://overpass-api.de/api/interpreter",
        data=urlencode({"data": query}).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "MERIDIAN-IDR/0.1"},
    )
    with urlopen(request, timeout=90) as response:
        payload = response.read()
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    return destination


@dataclass(frozen=True)
class RoadSegment:
    start: np.ndarray
    end: np.ndarray
    way_id: int


@dataclass(frozen=True)
class Candidate:
    segment_index: int
    point: np.ndarray
    distance_m: float


@dataclass(frozen=True)
class MapMatchResult:
    """A fail-closed map-match result.

    ``accepted`` is false where the matcher intentionally retained the raw
    dead-reckoning point. Consumers must use that flag (and not infer a snap
    from coordinates alone) before displaying a road-constrained location.
    """

    east_m: np.ndarray
    north_m: np.ndarray
    accepted: np.ndarray
    confidence: np.ndarray
    rejection_reasons: tuple[str | None, ...]


class RoadGraph:
    def __init__(self, segments: list[RoadSegment]) -> None:
        if not segments:
            raise ValueError("OSM extract contains no driveable road segments")
        self.segments = segments

    @classmethod
    def from_overpass(cls, path: str | Path, *, origin_latitude: float, origin_longitude: float) -> "RoadGraph":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        nodes = {
            item["id"]: np.asarray(latlon_to_local_enu(item["lat"], item["lon"], origin_latitude, origin_longitude))
            for item in payload.get("elements", [])
            if item.get("type") == "node" and "lat" in item and "lon" in item
        }
        segments: list[RoadSegment] = []
        for item in payload.get("elements", []):
            if item.get("type") != "way" or "highway" not in item.get("tags", {}):
                continue
            node_ids = item.get("nodes", [])
            for first, second in zip(node_ids, node_ids[1:], strict=False):
                if first in nodes and second in nodes and not np.allclose(nodes[first], nodes[second]):
                    segments.append(RoadSegment(nodes[first], nodes[second], int(item["id"])))
        return cls(segments)

    def candidates(self, point: np.ndarray, *, limit: int = 4) -> list[Candidate]:
        found: list[Candidate] = []
        for index, segment in enumerate(self.segments):
            direction = segment.end - segment.start
            length_squared = float(direction @ direction)
            fraction = float(np.clip(((point - segment.start) @ direction) / length_squared, 0.0, 1.0))
            projected = segment.start + fraction * direction
            found.append(Candidate(index, projected, float(np.linalg.norm(point - projected))))
        return sorted(found, key=lambda candidate: candidate.distance_m)[:limit]


def _validate_observations(east_m: np.ndarray, north_m: np.ndarray) -> np.ndarray:
    east = np.asarray(east_m, dtype=float)
    north = np.asarray(north_m, dtype=float)
    if east.ndim != 1 or north.ndim != 1 or len(east) != len(north):
        raise ValueError("east_m and north_m must be one-dimensional arrays of equal length")
    if len(east) == 0:
        raise ValueError("cannot map-match an empty trajectory")
    observations = np.column_stack([east, north])
    if not np.isfinite(observations).all():
        raise ValueError("map matching requires finite trajectory coordinates")
    return observations


def _heading_error_rad(segment: RoadSegment, heading_rad: float) -> float:
    """Return unsigned angular error for an undirected road segment.

    OSM ways in this lightweight graph do not encode legal travel direction,
    so heading and heading + pi are both considered aligned with a segment.
    """
    direction = segment.end - segment.start
    bearing = math.atan2(float(direction[1]), float(direction[0]))
    return abs((heading_rad - bearing + math.pi / 2.0) % math.pi - math.pi / 2.0)


def _viterbi_candidate_path(
    observations: np.ndarray,
    candidate_sets: list[list[Candidate]],
    *,
    emission_sigma_m: float,
    transition_scale_m: float,
) -> list[Candidate]:
    """Decode one contiguous run of already safety-screened candidates."""
    previous_score = np.asarray([-(candidate.distance_m / emission_sigma_m) ** 2 for candidate in candidate_sets[0]])
    backpointers: list[np.ndarray] = []
    for index in range(1, len(candidate_sets)):
        previous, current = candidate_sets[index - 1], candidate_sets[index]
        observed_distance = float(np.linalg.norm(observations[index] - observations[index - 1]))
        current_score = np.full(len(current), -np.inf)
        pointer = np.zeros(len(current), dtype=int)
        for current_index, candidate in enumerate(current):
            transition = np.asarray(
                [abs(float(np.linalg.norm(candidate.point - prior.point)) - observed_distance) / transition_scale_m for prior in previous]
            )
            options = previous_score - transition
            pointer[current_index] = int(np.argmax(options))
            current_score[current_index] = options[pointer[current_index]] - (candidate.distance_m / emission_sigma_m) ** 2
        backpointers.append(pointer)
        previous_score = current_score
    selected = [int(np.argmax(previous_score))]
    for pointer in reversed(backpointers):
        selected.append(int(pointer[selected[-1]]))
    selected.reverse()
    return [candidate_sets[index][choice] for index, choice in enumerate(selected)]


def safe_hmm_map_match(
    graph: RoadGraph,
    east_m: np.ndarray,
    north_m: np.ndarray,
    *,
    emission_sigma_m: float = 75.0,
    transition_scale_m: float = 35.0,
    candidates_per_point: int = 4,
    max_snap_distance_m: float = 20.0,
    ambiguity_distance_margin_m: float = 8.0,
    heading_rad: np.ndarray | None = None,
    speed_mps: np.ndarray | None = None,
    min_heading_speed_mps: float = 2.0,
    max_heading_error_deg: float = 45.0,
) -> MapMatchResult:
    """Fail-closed Viterbi road matching for a DR trajectory.

    The matcher only changes a point when its nearest eligible road is within
    ``max_snap_distance_m`` and is clearly better than the closest *different
    OSM way* by ``ambiguity_distance_margin_m``. At useful driving speeds an
    optional heading check rejects candidates whose undirected segment bearing
    differs by more than ``max_heading_error_deg``. Rejected observations keep
    their raw DR coordinates and are explicitly marked in the result.

    This is deliberately conservative: retaining a drifty raw point is safer
    than silently snapping the vehicle onto a plausible neighbouring road.
    """
    if emission_sigma_m <= 0 or transition_scale_m <= 0:
        raise ValueError("emission_sigma_m and transition_scale_m must be positive")
    if candidates_per_point < 1:
        raise ValueError("candidates_per_point must be at least one")
    if max_snap_distance_m <= 0 or ambiguity_distance_margin_m < 0:
        raise ValueError("map-match distance thresholds must be non-negative and max snap positive")
    if min_heading_speed_mps < 0 or not 0 < max_heading_error_deg <= 90:
        raise ValueError("invalid heading safety thresholds")

    observations = _validate_observations(east_m, north_m)
    point_count = len(observations)
    headings = None if heading_rad is None else np.asarray(heading_rad, dtype=float)
    speeds = None if speed_mps is None else np.asarray(speed_mps, dtype=float)
    if (headings is None) != (speeds is None):
        raise ValueError("heading_rad and speed_mps must be supplied together")
    if headings is not None and (headings.shape != (point_count,) or speeds is None or speeds.shape != (point_count,)):
        raise ValueError("heading_rad and speed_mps must have one value per trajectory point")

    # Look beyond the Viterbi beam when checking ambiguity. Multiple adjacent
    # segments of one way are benign; a nearby *different* way is not.
    safety_candidate_limit = max(candidates_per_point, 8)
    candidate_sets: list[list[Candidate] | None] = []
    primary_way_ids: list[int | None] = []
    reasons: list[str | None] = []
    pre_confidence = np.zeros(point_count, dtype=float)
    max_heading_error_rad = math.radians(max_heading_error_deg)

    for index, point in enumerate(observations):
        nearby = graph.candidates(point, limit=safety_candidate_limit)
        if not nearby or nearby[0].distance_m > max_snap_distance_m:
            candidate_sets.append(None)
            primary_way_ids.append(None)
            reasons.append("too_far_from_road")
            continue

        use_heading = headings is not None and speeds is not None and np.isfinite(headings[index]) and np.isfinite(speeds[index]) and speeds[index] >= min_heading_speed_mps
        eligible = [candidate for candidate in nearby if candidate.distance_m <= max_snap_distance_m]
        if use_heading:
            eligible = [
                candidate
                for candidate in eligible
                if _heading_error_rad(graph.segments[candidate.segment_index], float(headings[index])) <= max_heading_error_rad
            ]
            if not eligible:
                candidate_sets.append(None)
                primary_way_ids.append(None)
                reasons.append("heading_inconsistent")
                continue

        # Keep the closest candidate from every distinct way for the HMM beam.
        by_way: dict[int, Candidate] = {}
        for candidate in eligible:
            way_id = graph.segments[candidate.segment_index].way_id
            if way_id not in by_way:
                by_way[way_id] = candidate
        distinct_ways = sorted(by_way.values(), key=lambda candidate: candidate.distance_m)
        best = distinct_ways[0]
        if len(distinct_ways) > 1:
            runner_up = distinct_ways[1]
            margin = runner_up.distance_m - best.distance_m
            if margin < ambiguity_distance_margin_m:
                candidate_sets.append(None)
                primary_way_ids.append(None)
                reasons.append("ambiguous_nearby_roads")
                continue
        else:
            margin = max_snap_distance_m

        # Preserve adjacent segments of the selected way in the beam so a path
        # can progress through a way without manufacturing an ambiguity.
        selected_way = graph.segments[best.segment_index].way_id
        beam = [candidate for candidate in eligible if graph.segments[candidate.segment_index].way_id == selected_way]
        beam.extend(candidate for candidate in distinct_ways[1:candidates_per_point] if candidate not in beam)
        candidate_sets.append(beam[:candidates_per_point])
        primary_way_ids.append(selected_way)
        reasons.append(None)
        distance_confidence = max(0.0, 1.0 - best.distance_m / max_snap_distance_m)
        ambiguity_confidence = min(1.0, max(0.0, margin / max(max_snap_distance_m, 1e-6)))
        pre_confidence[index] = min(distance_confidence, ambiguity_confidence if len(distinct_ways) > 1 else 1.0)

    result = observations.copy()
    accepted = np.zeros(point_count, dtype=bool)
    index = 0
    while index < point_count:
        if candidate_sets[index] is None:
            index += 1
            continue
        end = index + 1
        while end < point_count and candidate_sets[end] is not None:
            end += 1
        run_sets = [candidate_sets[item] for item in range(index, end)]
        # All entries in this contiguous run are non-None by construction.
        selected = _viterbi_candidate_path(observations[index:end], [item for item in run_sets if item is not None], emission_sigma_m=emission_sigma_m, transition_scale_m=transition_scale_m)
        for offset, candidate in enumerate(selected):
            point_index = index + offset
            # Do not let temporal smoothing override the point's unambiguous
            # nearest way: that would turn a safe gate back into a wrong-road
            # snap under a strong preceding Viterbi path.
            if graph.segments[candidate.segment_index].way_id != primary_way_ids[point_index]:
                reasons[point_index] = "viterbi_disagrees_with_nearest_way"
                continue
            result[point_index] = candidate.point
            accepted[point_index] = True
        index = end

    confidence = pre_confidence * accepted.astype(float)
    return MapMatchResult(
        east_m=result[:, 0],
        north_m=result[:, 1],
        accepted=accepted,
        confidence=confidence,
        rejection_reasons=tuple(reasons),
    )


def hmm_map_match(
    graph: RoadGraph,
    east_m: np.ndarray,
    north_m: np.ndarray,
    **kwargs: Any,
) -> tuple[np.ndarray, np.ndarray]:
    """Backwards-compatible coordinate-only wrapper around safe matching.

    Its outputs retain raw DR points for rejected observations. New callers
    should prefer :func:`safe_hmm_map_match` so they can surface confidence and
    no-match status rather than presenting raw and snapped positions alike.
    """
    result = safe_hmm_map_match(graph, east_m, north_m, **kwargs)
    return result.east_m, result.north_m
