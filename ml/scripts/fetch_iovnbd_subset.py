"""Fetch one public synchronized IO-VNBD smartphone/vehicle pair."""

from __future__ import annotations

import argparse
from pathlib import Path
from urllib.request import urlretrieve


UPSTREAM = "https://github.com/onyekpeu/IO-VNBD/raw/refs/heads/master"
RUN = "Synchronised%20V%20abd%20S%20datasets/Categorised%20IOVNB%20Dataset/M%20%28Driver%20B%29"
FILES = {"smartphone": "S-M.csv", "vehicle": "V-M.csv"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("ml/data/raw/iovnbd_m"))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for kind, filename in FILES.items():
        destination = args.output_dir / filename
        if destination.exists() and destination.stat().st_size > 1024:
            print(f"{kind}: using cached {destination} ({destination.stat().st_size:,} bytes)")
            continue
        print(f"{kind}: downloading {filename}...")
        urlretrieve(f"{UPSTREAM}/{RUN}/{filename}", destination)
        print(f"{kind}: saved {destination} ({destination.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
