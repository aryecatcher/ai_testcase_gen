import os
import sys
from unittest.mock import MagicMock, AsyncMock

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from src.core.generation.generator import TestCaseGenerator
from src.models.domain import Requirement, IngestionMetadata


def test_progress_callback():
    mock_llm = MagicMock()
    mock_kg = MagicMock()
    mock_kg.get_related_constraints = MagicMock(return_value="")
    mock_kg.expand_scenarios = MagicMock(return_value=[])

    mock_llm.async_client = None
    mock_llm.async_generate_cases = AsyncMock(
        return_value=[
            {
                "title": "Generated Case",
                "precondition": "None",
                "steps": [{"action": "step1", "result": "ok"}],
                "expected_result": "Success",
                "test_data": {"valid": {}, "invalid": {}},
                "priority": "P2",
                "type": "功能测试",
                "methodology": ["LLM"],
            }
        ]
    )

    generator = TestCaseGenerator(mock_llm, mock_kg)

    requirements = [
        Requirement(
            id=f"REQ-{i}",
            original_text=f"Requirement {i}",
            ingestion_metadata=IngestionMetadata(source_file="test.txt"),
        )
        for i in range(5)
    ]

    progress_updates = []

    def callback(current, total):
        progress_updates.append((current, total))

    generator.generate(requirements, progress_callback=callback)

    assert len(progress_updates) == len(requirements), (
        f"Expected {len(requirements)} updates, got {len(progress_updates)}"
    )
    assert progress_updates[-1] == (len(requirements), len(requirements)), (
        f"Final update should be ({len(requirements)}, {len(requirements)}), got {progress_updates[-1]}"
    )


if __name__ == "__main__":
    test_progress_callback()
    print("Progress callback verification OK")
