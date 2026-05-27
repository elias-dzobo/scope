import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from research_core.schemas.tool_schema import QueryGenerationResponse


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_query_generation_contract_accepts_canonical_shape():
    payload = json.loads((FIXTURES_DIR / "query_plan.json").read_text())
    parsed = QueryGenerationResponse.model_validate(payload)

    assert parsed.stock == "Acme Corp"
    assert parsed.pillars[0].pillar_name == "Valuation"
    assert parsed.pillars[0].queries[0].query


def test_query_generation_contract_rejects_legacy_dict_pillars_shape():
    legacy_payload = {
        "stock": "Acme Corp",
        "pillars": {
            "Valuation": {
                "objective": "legacy format",
                "queries": [{"query": "Acme PE", "intent": "test"}],
            }
        },
    }

    with pytest.raises(ValidationError):
        QueryGenerationResponse.model_validate(legacy_payload)
