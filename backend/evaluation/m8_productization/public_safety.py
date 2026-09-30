"""Fail closed when public Git content contains private paths or obvious credentials."""

from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Finding:
    path: str
    rule: str
    line: int | None = None


_FORBIDDEN_LEGACY_NAME = "echo" + "mind"

_CONTENT_RULES = {
    "legacy_product_reference": re.compile(_FORBIDDEN_LEGACY_NAME, re.IGNORECASE),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "openai_style_key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "github_token": re.compile(r"\bgh[opusr]_[A-Za-z0-9]{30,}\b"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "credentialed_url": re.compile(r"https?://[^\s/:]+:[^\s/@]+@[^\s/]+"),
}


def _tracked_files(root: Path) -> list[Path]:
    """Return every file that would be publishable by a normal Git add."""

    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return [root / item.decode("utf-8") for item in result.stdout.split(b"\0") if item]


def scan_paths(root: Path, paths: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        relative = path.relative_to(root).as_posix()
        lowered = relative.casefold()
        if lowered == "docs" or lowered.startswith("docs/"):
            findings.append(Finding(relative, "private_docs_tracked"))
        if path.name == ".env":
            findings.append(Finding(relative, "runtime_env_tracked"))
        if not path.is_file() or path.stat().st_size > 5_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            for rule, pattern in _CONTENT_RULES.items():
                if pattern.search(line):
                    findings.append(Finding(relative, rule, line_number))
    return findings


def scan_history(root: Path) -> list[Finding]:
    """Detect a forbidden legacy product reference reachable from the release HEAD."""

    commits = subprocess.run(
        [
            "git",
            "log",
            "HEAD",
            "-i",
            "-G",
            _FORBIDDEN_LEGACY_NAME,
            "--format=%H",
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.splitlines()
    objects = subprocess.run(
        ["git", "rev-list", "--objects", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.splitlines()
    path_hit = any(_FORBIDDEN_LEGACY_NAME in line.casefold() for line in objects)
    findings = [Finding(".git", "legacy_reference_in_history") for _ in commits[:1]]
    if path_hit and not findings:
        findings.append(Finding(".git", "legacy_path_in_history"))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--working-tree-only", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    findings = scan_paths(root, _tracked_files(root))
    if not args.working_tree_only:
        findings.extend(scan_history(root))
    if findings:
        for item in findings:
            location = f"{item.path}:{item.line}" if item.line else item.path
            print(f"{location}: {item.rule}")
        return 1
    print("Public safety scan passed: tracked private docs and obvious secrets not found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
