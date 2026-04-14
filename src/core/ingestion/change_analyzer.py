from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from ...models.domain import Requirement, TestCase


def _normalize_text(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"[\W_]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _meta_dict(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return {}


def _entity(req: Requirement, key: str) -> str:
    entities = req.extracted_entities
    if isinstance(entities, dict):
        return str(entities.get(key, "") or "").strip()
    return str(getattr(entities, key, "") or "").strip()


def _normalized_source(req: Requirement) -> str:
    source_file = _meta_dict(req.ingestion_metadata).get("source_file", "")
    stem = Path(str(source_file or "")).stem.lower()
    stem = re.sub(r"v(?:ersion)?\s*\d+(?:\.\d+)?", "", stem)
    stem = re.sub(r"\d+(?:\.\d+)?", "", stem)
    stem = re.sub(r"[\W_]+", " ", stem)
    return re.sub(r"\s+", " ", stem).strip()


def _similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _requirement_match_score(old_req: Requirement, new_req: Requirement) -> float:
    old_module = _normalize_text(_entity(old_req, "module"))
    new_module = _normalize_text(_entity(new_req, "module"))
    old_feature = _normalize_text(_entity(old_req, "feature"))
    new_feature = _normalize_text(_entity(new_req, "feature"))
    old_text = _normalize_text(old_req.original_text or "")
    new_text = _normalize_text(new_req.original_text or "")
    old_source = _normalized_source(old_req)
    new_source = _normalized_source(new_req)

    score = _similarity(old_text, new_text) * 0.65
    if old_module and new_module:
        score += 0.2 if old_module == new_module else _similarity(old_module, new_module) * 0.12
    if old_feature and new_feature:
        score += 0.2 if old_feature == new_feature else _similarity(old_feature, new_feature) * 0.15
    if old_source and new_source and old_source == new_source:
        score += 0.1
    return min(score, 1.0)


def _classify_change(old_req: Requirement, new_req: Requirement, score: float) -> str:
    same_text = _normalize_text(old_req.original_text or "") == _normalize_text(new_req.original_text or "")
    same_module = _normalize_text(_entity(old_req, "module")) == _normalize_text(_entity(new_req, "module"))
    same_feature = _normalize_text(_entity(old_req, "feature")) == _normalize_text(_entity(new_req, "feature"))
    if same_text and same_module and same_feature:
        return "unchanged"
    if score >= 0.58:
        return "updated"
    return "new"


def analyze_requirement_changes(
    previous_requirements: List[Requirement],
    new_requirements: List[Requirement],
    existing_cases: Optional[List[TestCase]] = None,
) -> Dict[str, Any]:
    existing_cases = existing_cases or []
    previous_requirements = previous_requirements or []
    new_requirements = new_requirements or []
    unmatched_old_ids: Set[str] = {req.id for req in previous_requirements}
    mappings: List[Dict[str, Any]] = []

    for new_req in new_requirements:
        best_old = None
        best_score = 0.0
        for old_req in previous_requirements:
            if old_req.id not in unmatched_old_ids:
                continue
            score = _requirement_match_score(old_req, new_req)
            if score > best_score:
                best_old = old_req
                best_score = score
        if best_old is None or best_score < 0.58:
            mappings.append(
                {
                    "status": "new",
                    "new_req_id": new_req.id,
                    "old_req_id": None,
                    "score": round(best_score, 3),
                }
            )
            continue

        status = _classify_change(best_old, new_req, best_score)
        unmatched_old_ids.discard(best_old.id)
        mappings.append(
            {
                "status": status,
                "new_req_id": new_req.id,
                "old_req_id": best_old.id,
                "score": round(best_score, 3),
            }
        )

    removed_old_ids = sorted(unmatched_old_ids)
    for old_req_id in removed_old_ids:
        mappings.append(
            {
                "status": "removed",
                "new_req_id": None,
                "old_req_id": old_req_id,
                "score": 0.0,
            }
        )

    status_by_new = {item["new_req_id"]: item["status"] for item in mappings if item.get("new_req_id")}
    remap = {
        item["old_req_id"]: item["new_req_id"]
        for item in mappings
        if item.get("old_req_id") and item.get("new_req_id") and item["status"] in {"unchanged", "updated"}
    }

    affected_case_ids = {
        "needs_update": [],
        "reused": [],
        "obsolete": [],
    }
    for tc in existing_cases:
        old_req_id = tc.related_req_id
        if old_req_id in remap:
            mapped_status = status_by_new.get(remap[old_req_id], "unchanged")
            bucket = "needs_update" if mapped_status == "updated" else "reused"
            affected_case_ids[bucket].append(tc.test_case_id)
        elif old_req_id in removed_old_ids:
            affected_case_ids["obsolete"].append(tc.test_case_id)

    summary = {
        "unchanged": sum(1 for item in mappings if item["status"] == "unchanged"),
        "updated": sum(1 for item in mappings if item["status"] == "updated"),
        "new": sum(1 for item in mappings if item["status"] == "new"),
        "removed": len(removed_old_ids),
        "impacted_cases": len(affected_case_ids["needs_update"]) + len(affected_case_ids["obsolete"]),
        "reused_cases": len(affected_case_ids["reused"]),
    }
    return {
        "summary": summary,
        "mappings": mappings,
        "status_by_new_req_id": status_by_new,
        "remap_old_to_new_req_id": remap,
        "removed_old_req_ids": removed_old_ids,
        "changed_requirement_ids": [
            item["new_req_id"]
            for item in mappings
            if item.get("new_req_id") and item["status"] in {"new", "updated"}
        ],
        "affected_case_ids": affected_case_ids,
    }


def apply_requirement_change_statuses(requirements: List[Requirement], report: Dict[str, Any]) -> List[Requirement]:
    status_by_new = report.get("status_by_new_req_id", {})
    for req in requirements:
        meta = _meta_dict(req.ingestion_metadata)
        meta["change_status"] = status_by_new.get(req.id, "new")
        req.ingestion_metadata = meta
    return requirements


def apply_case_change_plan(test_cases: List[TestCase], report: Dict[str, Any]) -> List[TestCase]:
    remap = report.get("remap_old_to_new_req_id", {})
    removed_old_req_ids = set(report.get("removed_old_req_ids", []))
    status_by_new = report.get("status_by_new_req_id", {})

    for tc in test_cases:
        env = tc.system_env if isinstance(tc.system_env, dict) else {}
        env = dict(env or {})
        old_req_id = tc.related_req_id
        if old_req_id in remap:
            new_req_id = remap[old_req_id]
            tc.related_req_id = new_req_id
            change_status = status_by_new.get(new_req_id, "unchanged")
            env["change_impact"] = "needs_update" if change_status == "updated" else "reused"
            env["change_source_req_id"] = old_req_id
            env["change_target_req_id"] = new_req_id
        elif old_req_id in removed_old_req_ids:
            env["change_impact"] = "obsolete"
            env["change_source_req_id"] = old_req_id
        else:
            env.pop("change_impact", None)
            env.pop("change_source_req_id", None)
            env.pop("change_target_req_id", None)
        tc.system_env = env
    return test_cases
