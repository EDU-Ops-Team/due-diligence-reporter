#!/usr/bin/env python3
"""Compatibility wrapper for the package-level ad-hoc DDR runner."""

from __future__ import annotations

import sys
from pathlib import Path


def _repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "pyproject.toml").exists() and (
            parent / "src" / "due_diligence_reporter"
        ).exists():
            return parent
    raise RuntimeError("Could not locate due-diligence-reporter repo root")


PROJECT_ROOT = _repo_root()
SRC_ROOT = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from due_diligence_reporter.adhoc_runner import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
