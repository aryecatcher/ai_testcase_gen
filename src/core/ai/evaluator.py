from __future__ import annotations

import os
import statistics
from typing import Any, Dict, List

from ...models.domain import Requirement, TestCase
from .llm_service import LLMService
from ..kg.graph_service import KnowledgeGraphService


def get_requirement_entity(req: Requirement, key: str) -> str:
    if hasattr(req, "get_extracted_entities"):
        entities = req.get_extracted_entities()
        return str(getattr(entities, key, "") or "").strip()
    entities = getattr(req, "extracted_entities", {}) or {}
    if isinstance(entities, dict):
        return str(entities.get(key, "") or "").strip()
    return str(getattr(entities, key, "") or "").strip()


def serialize_case_for_judge(tc: TestCase) -> Dict[str, Any]:
    ti = tc.get_test_instruction() if hasattr(tc, "get_test_instruction") else tc.test_instruction
    return {
        "test_case_id": tc.test_case_id,
        "title": tc.title or "",
        "priority": tc.priority or "",
        "type": tc.dimension or "",
        "methodology": list(tc.methodology or []),
        "precondition": getattr(ti, "pre_condition", "") or "",
        "steps": list(getattr(ti, "steps", []) or []),
        "expected_result": getattr(ti, "expected_result", "") or "",
    }


def score_judge_result(result: Dict[str, Any]) -> int:
    violations = len(result.get("violations", []) or [])
    gaps = len(result.get("gaps", []) or [])
    score = 100 - violations * 20 - gaps * 10
    if result.get("passed"):
        score = min(100, score + 5)
    return max(0, min(100, score))


def build_case_map(all_cases: List[TestCase]) -> Dict[str, List[TestCase]]:
    case_map: Dict[str, List[TestCase]] = {}
    for tc in all_cases:
        case_map.setdefault(tc.related_req_id, []).append(tc)
    return case_map


async def evaluate_requirements(reqs: List[Requirement], case_map: Dict[str, List[TestCase]]) -> Dict[str, Any]:
    llm = LLMService()
    kg = KnowledgeGraphService()
    report_items: List[Dict[str, Any]] = []
    total_tokens = 0

    for req in reqs:
        related_cases = case_map.get(req.id, [])
        if not related_cases:
            continue
        keyword = get_requirement_entity(req, "feature") or get_requirement_entity(req, "module") or (req.original_text or "")[:80]
        constraints = kg.get_related_constraints(keyword)
        payload = [serialize_case_for_judge(tc) for tc in related_cases]
        result = await llm.async_judge_cases(constraints, payload)
        total_tokens += int(result.get("tokens", 0) or 0)
        report_items.append(
            {
                "req_id": req.id,
                "module": get_requirement_entity(req, "module"),
                "feature": get_requirement_entity(req, "feature"),
                "case_count": len(related_cases),
                "score": score_judge_result(result),
                "passed": bool(result.get("passed")),
                "violations": result.get("violations", []) or [],
                "gaps": result.get("gaps", []) or [],
                "tokens": int(result.get("tokens", 0) or 0),
            }
        )

    scores = [item["score"] for item in report_items]
    passed_count = sum(1 for item in report_items if item["passed"])
    summary = {
        "requirements_evaluated": len(report_items),
        "average_score": round(statistics.mean(scores), 2) if scores else 0.0,
        "min_score": min(scores) if scores else 0,
        "max_score": max(scores) if scores else 0,
        "passed_count": passed_count,
        "failed_count": len(report_items) - passed_count,
        "total_tokens": total_tokens,
        "judge_model": os.getenv("LLM_MODEL_JUDGE") or os.getenv("LLM_MODEL_GEN") or "deepseek-r1:7b",
    }
    return {"summary": summary, "items": report_items}
