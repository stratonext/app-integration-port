"""Repo-wide writing rules from AGENTS.md: no em-dashes anywhere in tracked files."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_no_em_dashes():
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    offenders = []
    for name in tracked:
        path = ROOT / name
        if name == "poetry.lock" or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:  # binary
            continue
        offenders += [
            f"{name}:{i}"
            for i, line in enumerate(text.splitlines(), 1)
            if "\u2014" in line
        ]
    assert not offenders, "em-dashes found (see AGENTS.md):\n" + "\n".join(offenders)
