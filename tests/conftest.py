"""
Shared pytest setup. pytest loads this file automatically before any test.
"""
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Files that scripts/setup.sh creates. The tests can't run without them.
FILES_CREATED_BY_SETUP = (
    REPO_ROOT / "src" / "generated" / "bazaar_pb2.py",
    REPO_ROOT / "bazaar-protobuf-starter-linux" / "README.md",
)


def pytest_configure(config):
    """Stop with a clear instruction instead of a confusing ImportError."""
    missing = [
        str(path.relative_to(REPO_ROOT))
        for path in FILES_CREATED_BY_SETUP
        if not path.exists()
    ]
    if missing:
        raise pytest.UsageError(
            f"Missing: {', '.join(missing)}\n"
            "Run `bash scripts/setup.sh` inside the dev container first."
        )
