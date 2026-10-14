#!/usr/bin/env python3
"""Select a reproducible local test batch from the public Signalpost universe (same method as the starter kit)."""
from __future__ import annotations

import argparse
import gzip
import json
import random
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--seed", type=int, default=20260823)
    args = parser.parse_args()
    path = Path(args.universe)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    chosen = random.Random(args.seed).sample(rows, args.count)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as handle:
        for row in chosen:
            handle.write(json.dumps({"organisation_number": row["organisation_number"]}) + "\n")
    print(f"wrote {len(chosen)} organisation numbers to {args.output}")


if __name__ == "__main__":
    main()
