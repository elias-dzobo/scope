"""Validate Scope production environment before deployment.

Usage:
    python scripts/check_production_config.py /etc/scope/scope.env

The script loads KEY=VALUE lines from the provided env file into the current
process, then delegates validation to ``scope_api.config``. It intentionally
prints only validation status and error names, never secret values.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scope_api.config import ProductionConfigError, load_api_config


def _load_env_file(path: Path) -> None:
    """Load simple shell-style KEY=VALUE lines without expanding values."""
    if not path.exists():
        raise FileNotFoundError(f"Environment file not found: {path}")
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip().strip('"').strip("'")


def main() -> None:
    """CLI entrypoint."""
    env_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/etc/scope/scope.env")
    _load_env_file(env_path)
    try:
        config = load_api_config()
    except ProductionConfigError as exc:
        print(f"Production config invalid: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    if not config.is_production:
        print("Production config invalid: SCOPE_ENV must be production", file=sys.stderr)
        raise SystemExit(1)
    print("Production config OK")


if __name__ == "__main__":
    main()
