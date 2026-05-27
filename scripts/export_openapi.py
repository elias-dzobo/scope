"""Export the Scope API OpenAPI schema for contract tracking."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("OBSERVABILITY_ENABLED", "false")

from scope_api.app import app


OUTPUT_PATH = Path("packages/contracts/openapi/openapi.json")


def main() -> None:
    """Write the current FastAPI OpenAPI schema to the contracts package."""
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
