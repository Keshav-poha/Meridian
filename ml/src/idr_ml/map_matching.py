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


def hmm_map_match(
    graph: RoadGraph,
    east_m: np.ndarray,
    north_m: np.ndarray,
    *,
    emission_sigma_m: float = 75.0,
    transition_scale_m: float = 35.0,
    candidates_per_point: int = 4,
) -> tuple[np.ndarray, np.ndarray]:
    """Viterbi decode of road-segment candidates for a DR trajectory.

    Emission likelihood penalizes DR-to-road distance. The transition term
    preserves the observed displacement between consecutive fixes, avoiding
    jitter between nearby parallel roads without needing a routing service.
    """
    observations = np.column_stack([east_m, north_m]).astype(float)
    candidate_sets = [graph.candidates(point, limit=candidates_per_point) for point in observations]
    if any(not candidates for candidates in candidate_sets):
        raise ValueError("a DR point produced no road candidates")
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
    matched = np.asarray([candidate_sets[index][choice].point for index, choice in enumerate(selected)])
    return matched[:, 0], matched[:, 1]
