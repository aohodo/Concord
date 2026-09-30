"""Run the reproducible M8-A productization checks and write public evidence."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _public_command(command: list[str], root: Path) -> list[str]:
    """Remove workstation-specific executable and workspace paths from evidence."""

    rendered: list[str] = []
    executable_aliases = {
        str(Path(sys.executable).resolve()).casefold(): "python",
    }
    for raw in command:
        value = str(raw)
        resolved_alias = executable_aliases.get(str(Path(value).resolve()).casefold())
        if resolved_alias:
            rendered.append(resolved_alias)
            continue
        if Path(value).name.casefold() in {"npm", "npm.cmd"}:
            rendered.append("npm")
            continue
        if Path(value).name.casefold() in {"docker", "docker.exe"}:
            rendered.append("docker")
            continue
        try:
            candidate = Path(value).resolve()
            rendered.append(candidate.relative_to(root).as_posix() or ".")
        except (OSError, ValueError):
            rendered.append(value)
    return rendered


def _run(name: str, command: list[str], root: Path) -> dict[str, Any]:
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    output = "\n".join(
        item.strip() for item in (completed.stdout, completed.stderr) if item.strip()
    )
    return {
        "name": name,
        "command": _public_command(command, root),
        "passed": completed.returncode == 0,
        "return_code": completed.returncode,
        "duration_seconds": round(time.perf_counter() - started, 3),
        "output_tail": output[-4000:],
    }


def _report(results: list[dict[str, Any]], generated_at: str) -> str:
    passed = sum(item["passed"] for item in results)
    lines = [
        "# M8-A Productization Validation",
        "",
        f"Generated: `{generated_at}`",
        "",
        f"Result: **{passed}/{len(results)} checks passed**.",
        "",
        "| check | result | duration (s) |",
        "|---|---:|---:|",
    ]
    for item in results:
        lines.append(
            f"| {item['name']} | {'PASS' if item['passed'] else 'FAIL'} | "
            f"{item['duration_seconds']} |"
        )
    lines.extend(
        [
            "",
            "## Coverage interpretation",
            "",
            (
                "- Backend tests include M1–M7 unit/integration, longitudinal Case, fault "
                "injection, human-collaboration, ablation and verified-experience call-chain "
                "coverage."
            ),
            "- Frontend build proves the Vue Case Console compiles against its checked-in lockfile.",
            "- Compose validation proves the optional Redis and full-stack topology parses.",
            (
                "- Public safety scans publishable files and fails on private `docs/`, runtime "
                "`.env` or obvious credential material."
            ),
            "- These checks do not claim real-world outcome quality or production certification.",
            "",
            "Detailed command output is retained in `results.json`.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).parents[3])
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parent / "results",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    npm = shutil.which("npm.cmd") or shutil.which("npm") or "npm"
    docker = shutil.which("docker.exe") or shutil.which("docker") or "docker"
    commands = [
        (
            "backend M1-M7 regression and M8-A call-chain tests",
            [sys.executable, "-m", "pytest", "backend/tests", "-q"],
        ),
        (
            "longitudinal interaction evaluation protocol",
            [
                sys.executable,
                "-m",
                "pytest",
                "evaluation/v2_interaction/tests",
                "-q",
            ],
        ),
        (
            "Python lint",
            [sys.executable, "-m", "ruff", "check", "backend", "evaluation/v2_interaction"],
        ),
        (
            "tracked public-tree safety",
            [
                sys.executable,
                "backend/evaluation/m8_productization/public_safety.py",
                "--root",
                str(root),
            ],
        ),
        (
            "Python dependency audit",
            [sys.executable, "-m", "pip_audit", "-r", "backend/requirements.txt"],
        ),
        ("Vue production build", [npm, "run", "build", "--prefix", "frontend"]),
        (
            "frontend high-severity dependency audit",
            [npm, "audit", "--audit-level=high", "--prefix", "frontend"],
        ),
        (
            "backend Docker Compose topology",
            [docker, "compose", "-f", "backend/docker-compose.yml", "config", "-q"],
        ),
        (
            "frontend Docker Compose topology",
            [docker, "compose", "-f", "frontend/docker-compose.yml", "config", "-q"],
        ),
    ]
    results = [_run(name, command, root) for name, command in commands]
    generated_at = datetime.now(UTC).isoformat()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    (output / "results.json").write_text(
        json.dumps(
            {"generated_at": generated_at, "checks": results},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (output / "M8_A_REPORT.md").write_text(
        _report(results, generated_at), encoding="utf-8"
    )
    print(_report(results, generated_at))
    return 0 if all(item["passed"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
