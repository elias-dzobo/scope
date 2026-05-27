"""Persistence helpers for harness state and observations."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from research_core.harness.models import QualityGateResult, ResearchBrief, ResearchPlan, ResearchObservation
from research_core.storage import ArtifactStore, LocalArtifactStore, artifact_store, artifacts_dir, ticker_artifact_key


class HarnessMemoryStore:
    """Persist harness state next to existing research artifacts.

    This intentionally uses local JSON files to match the repository's current
    artifact pattern. A production deployment can replace this with object
    storage and a durable database without changing the controller contract.
    """

    def __init__(self, artifact_root: str | None = None, store: ArtifactStore | None = None) -> None:
        self.artifact_root = Path(artifact_root) if artifact_root is not None else artifacts_dir()
        self.store = store or (LocalArtifactStore(self.artifact_root) if artifact_root is not None else artifact_store())

    def run_dir(self, ticker: str) -> Path:
        """Return the harness directory for a ticker-backed research run."""
        clean_ticker = ticker.strip().upper() or "UNKNOWN"
        return self.artifact_root / clean_ticker / "harness"

    def save_run_state(
        self,
        ticker: str,
        brief: ResearchBrief,
        plan: ResearchPlan,
        gate_result: QualityGateResult | None = None,
    ) -> None:
        """Write the current brief, plan, and latest gate result to disk."""
        output_dir = self.run_dir(ticker)
        output_dir.mkdir(parents=True, exist_ok=True)
        self._write_model(ticker, "harness/brief.json", output_dir / "brief.json", brief)
        self._write_model(ticker, "harness/plan.json", output_dir / "plan.json", plan)
        if gate_result is not None:
            self._write_model(ticker, "harness/latest_gate_result.json", output_dir / "latest_gate_result.json", gate_result)

    def append_trace(self, ticker: str, payload: ResearchObservation | dict[str, Any]) -> None:
        """Append one compact trace event to `trace.jsonl`."""
        output_dir = self.run_dir(ticker)
        output_dir.mkdir(parents=True, exist_ok=True)
        trace_path = output_dir / "trace.jsonl"
        record = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
        if isinstance(self.store, LocalArtifactStore):
            with trace_path.open("a") as handle:
                handle.write(json.dumps(record) + "\n")
            return
        self.store.write_text(ticker_artifact_key(ticker, "harness", "trace.jsonl"), json.dumps(record) + "\n")

    def _write_model(self, ticker: str, relative_key: str, path: Path, payload: BaseModel) -> None:
        """Serialize a Pydantic model with stable indentation."""
        if isinstance(self.store, LocalArtifactStore):
            os.makedirs(path.parent, exist_ok=True)
            path.write_text(payload.model_dump_json(indent=2))
            return
        self.store.write_text(ticker_artifact_key(ticker, relative_key), payload.model_dump_json(indent=2), "application/json")
