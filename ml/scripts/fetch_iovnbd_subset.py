"""Fetch a public synchronized IO-VNBD smartphone/vehicle pair."""

from __future__ import annotations

import argparse
from pathlib import Path
from urllib.request import urlretrieve


UPSTREAM = "https://github.com/onyekpeu/IO-VNBD/raw/refs/heads/master"
FILES = {"smartphone": "S-M.csv", "vehicle": "V-M.csv"}
RECORDINGS = {
    "driver-b-m": {
        "remote_directory": (
            "Synchronised%20V%20abd%20S%20datasets/"
            "Categorised%20IOVNB%20Dataset/M%20%28Driver%20B%29"
        ),
        "output_directory": Path("ml/data/raw/iovnbd_m"),
        "files": FILES,
    },
    "driver-a-s3c": {
        "remote_directory": (
            "Synchronised%20V%20abd%20S%20datasets/"
            "Categorised%20IOVNB%20Dataset/S%20%28Driver%20A%29/S3c"
        ),
        "output_directory": Path(
            "ml/data/raw/iovnbd_official/Synchronised V abd S datasets/"
            "Categorised IOVNB Dataset/S (Driver A)/S3c"
        ),
        "files": {"smartphone": "S-S3c.csv", "vehicle": "V-S3c.csv"},
    },
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--recording",
        choices=tuple(RECORDINGS),
        default="driver-b-m",
        help="public synchronized recording to download",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="override the default local directory for the selected recording",
    )
    args = parser.parse_args()
    recording = RECORDINGS[args.recording]
    output_dir = args.output_dir or recording["output_directory"]
    output_dir.mkdir(parents=True, exist_ok=True)
    for kind, filename in recording["files"].items():
        destination = output_dir / filename
        if destination.exists() and destination.stat().st_size > 1024:
            print(f"{kind}: using cached {destination} ({destination.stat().st_size:,} bytes)")
            continue
        print(f"{kind}: downloading {filename}...")
        urlretrieve(f"{UPSTREAM}/{recording['remote_directory']}/{filename}", destination)
        print(f"{kind}: saved {destination} ({destination.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
