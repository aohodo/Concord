"""Merge preserved M4 first-pass and targeted structural reruns by Episode ID."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .catalog import load_episodes
from .report import save_report_bundle
from .runner import M4EpisodeRun


def _read(path: Path) -> list[M4EpisodeRun]:
    return [
        M4EpisodeRun.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge M4 raw runs, preferring later sources"
    )
    parser.add_argument("--input", action="append", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--catalog")
    args = parser.parse_args()

    expected_order = [item.episode_id for item in load_episodes(args.catalog)]
    merged: dict[str, M4EpisodeRun] = {}
    replacements = 0
    sources: list[dict[str, object]] = []
    for raw_path in args.input:
        path = Path(raw_path).resolve()
        rows = _read(path)
        for row in rows:
            if row.episode_id in merged:
                replacements += 1
            merged[row.episode_id] = row
        sources.append({"path": str(path), "episodes": len(rows)})

    missing = [episode_id for episode_id in expected_order if episode_id not in merged]
    unexpected = sorted(set(merged).difference(expected_order))
    if missing or unexpected:
        raise ValueError(f"catalog mismatch: missing={missing}, unexpected={unexpected}")

    output = Path(args.output_dir).resolve()
    ordered = [merged[episode_id] for episode_id in expected_order]
    save_report_bundle(output, ordered)
    manifest = {
        "strategy": "later input replaces the same episode_id; every raw source is retained",
        "catalog_episodes": len(expected_order),
        "replacements": replacements,
        "sources": sources,
    }
    (output / "experiment_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"M4 merged: {len(ordered)} Episodes; replacements={replacements}")


if __name__ == "__main__":
    main()
