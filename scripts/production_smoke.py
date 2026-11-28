from __future__ import annotations

import py_compile
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

PYTHON_FILES = [
    "app.py",
    "prospecting_core.py",
    "auth_system.py",
    "monetization.py",
    "rep_match_score.py",
    "territory_intelligence.py",
    "profile_claims.py",
    "review_system.py",
    "connection_requests.py",
    "shortlists.py",
    "deployment_status.py",
    "trigger_leads.py",
    "discover_core.py",
    "api/main.py",
    "api/auth.py",
    "api/pipeline.py",
    "api/cache.py",
    "api/settings.py",
]


def compile_python_files() -> None:
    for rel_path in PYTHON_FILES:
        py_compile.compile(str(ROOT / rel_path), doraise=True)


def run_unit_tests() -> None:
    subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        cwd=ROOT,
        check=True,
    )


def main() -> int:
    compile_python_files()
    run_unit_tests()
    print("Production smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
