from __future__ import annotations

import os
import sys
import time
import re
import hashlib
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

import httpx
import streamlit as st
import asyncio
import json
from urllib.parse import urlparse
import pandas as pd
from dotenv import load_dotenv

_UI_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _UI_DIR.parent
sys.path.insert(0, str(_PROJECT_ROOT))

load_dotenv()

_FIXED_LLM_MODEL = "deepseek-r1:7b"
_FIXED_LLM_BASE_URL = "http://localhost:11434/v1"
_FIXED_LLM_API_KEY = "ollama"

from data.storage import load_json, save_json
from src.models.domain import ProjectContext, Requirement, TestCase
from src.data.database import init_db, get_all_requirements, get_all_test_cases, save_requirement, save_test_case, get_session
from src.data.migration import migrate_json_to_db
from src.core.analytics import annotate_quality_characteristics, build_statistics
from src.core.ai.evaluator import build_case_map, evaluate_requirements
from src.core.ingestion.change_analyzer import (
    analyze_requirement_changes,
    apply_case_change_plan,
    apply_requirement_change_statuses,
)
from sqlmodel import Session

_raw_backend_url = (os.getenv("BACKEND_URL") or "").strip() or "http://localhost:8002"
_BACKEND_URL = _raw_backend_url[:-1] if _raw_backend_url.endswith("/") else _raw_backend_url

def _check_backend() -> tuple[bool, str]:
    parsed = urlparse(_BACKEND_URL)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    start_hint = (
        f"$env:PYTHONDONTWRITEBYTECODE='1'; "
        f"python -m uvicorn src.api.main:app --host 127.0.0.1 --port {port}"
    )
    last_err = ""
    for _ in range(3):
        try:
            with httpx.Client(timeout=8.0, trust_env=False) as client:
                resp = client.get(f"{_BACKEND_URL}/health")
            if resp.status_code == 200:
                return True, ""
            last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
        except Exception as e:
            last_err = str(e)
        time.sleep(0.5)
    return False, f"{last_err} | 启动命令: {start_hint}"

def _backend_headers() -> Dict[str, str]:
    return {
        "X-LLM-MODEL-GEN": _FIXED_LLM_MODEL,
        "X-LLM-MODEL-JUDGE": _FIXED_LLM_MODEL,
        "X-OPENAI-BASE-URL": _FIXED_LLM_BASE_URL,
        "X-OPENAI-API-KEY": _FIXED_LLM_API_KEY,
    }


def _get_backend_startup_status() -> Dict[str, Any]:
    ok, err = _check_backend()
    if not ok:
        return {
            "status": "error",
            "message": f"后端不可用：{err}",
            "checked_at": datetime.now().isoformat(timespec="seconds"),
            "steps": [],
        }
    try:
        with httpx.Client(timeout=8.0, trust_env=False) as client:
            resp = client.get(f"{_BACKEND_URL}/startup-status")
        resp.raise_for_status()
        payload = resp.json()
        payload["message"] = payload.get("llm", {}).get("message", "")
        return payload
    except Exception as e:
        return {
            "status": "error",
            "message": f"获取后端启动状态失败：{e}",
            "checked_at": datetime.now().isoformat(timespec="seconds"),
            "steps": [],
        }

def _get_conf(meta) -> float:
    """兼容 IngestionMetadata 对象和 dict 两种情况"""
    if meta is None:
        return 0.0
    if isinstance(meta, dict):
        return float(meta.get("parsing_confidence", 0))
    return float(getattr(meta, "parsing_confidence", 0))

def _normalize_text(text: str) -> str:
    text = (text or "").strip()
    text = text.replace("(Mock)", "").replace("（Mock）", "")
    text = re.sub(r"\s+", " ", text)
    return text

def _meta_to_dict(meta: Any) -> Dict[str, Any]:
    if meta is None:
        return {}
    if isinstance(meta, dict):
        return dict(meta)
    if hasattr(meta, "model_dump"):
        return meta.model_dump(mode="json")
    return {}

def _requirement_change_status(req: Requirement) -> str:
    return _meta_to_dict(req.ingestion_metadata).get("change_status", "—")

def _impact_badge(tc: TestCase) -> str:
    env = _meta_to_dict(tc.system_env)
    impact = env.get("change_impact", "")
    labels = {
        "needs_update": "需更新",
        "reused": "沿用",
        "obsolete": "待废弃",
    }
    return labels.get(impact, "")

def _render_ai_eval_report(report: Dict[str, Any]) -> None:
    summary = report.get("summary", {})
    items = report.get("items", [])
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("评估需求数", summary.get("requirements_evaluated", 0))
    c2.metric("平均分", summary.get("average_score", 0.0))
    c3.metric("通过数", summary.get("passed_count", 0))
    c4.metric("失败数", summary.get("failed_count", 0))
    st.caption(
        f"判官模型：{summary.get('judge_model', '—')} | "
        f"总 Tokens：{summary.get('total_tokens', 0)}"
    )
    if not items:
        st.info("当前范围没有可评估的需求。")
        return
    rows = []
    for item in sorted(items, key=lambda x: (x.get("score", 0), x.get("req_id", ""))):
        rows.append(
            {
                "需求ID": item.get("req_id", ""),
                "模块": item.get("module", ""),
                "功能": item.get("feature", ""),
                "用例数": item.get("case_count", 0),
                "得分": item.get("score", 0),
                "是否通过": "是" if item.get("passed") else "否",
                "问题数": len(item.get("violations", []) or []),
                "缺口数": len(item.get("gaps", []) or []),
            }
        )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    low_items = [item for item in rows if item["是否通过"] == "否"][:5]
    if low_items:
        st.warning("存在未通过需求，建议优先查看下方详细问题。")
    for item in sorted(items, key=lambda x: (x.get("score", 0), x.get("req_id", "")))[:5]:
        with st.expander(f"{item.get('req_id', 'UNKNOWN')} | 得分 {item.get('score', 0)}", expanded=False):
            violations = item.get("violations", []) or []
            gaps = item.get("gaps", []) or []
            if violations:
                st.markdown("**违反项**")
                for line in violations:
                    st.write(f"- {line}")
            if gaps:
                st.markdown("**覆盖缺口**")
                for line in gaps:
                    st.write(f"- {line}")
            if not violations and not gaps:
                st.success("未发现明显问题。")

def _format_dt(value: Any) -> str:
    if not value:
        return "—"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    text = str(value).strip()
    if not text:
        return "—"
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return text

def _current_requirement_ids() -> set[str]:
    return {r.id for r in st.session_state.context.requirements}

def _current_batch_cases() -> List[TestCase]:
    req_ids = _current_requirement_ids()
    return [tc for tc in st.session_state.context.test_cases if tc.related_req_id in req_ids]

def _current_batch_meta() -> Dict[str, Any]:
    reqs = st.session_state.context.requirements or []
    if not reqs:
        return {"batch_id": "", "parsed_at": "", "files": []}
    metas = [_meta_to_dict(r.ingestion_metadata) for r in reqs]
    batch_id = next((m.get("upload_batch_id", "") for m in metas if m.get("upload_batch_id")), "")
    parsed_at = next((m.get("parsed_at", "") for m in metas if m.get("parsed_at")), "")
    files = list(dict.fromkeys([m.get("source_file", "") for m in metas if m.get("source_file", "")]))
    if not parsed_at:
        parsed_at = next((m.get("timestamp", "") for m in metas if m.get("timestamp")), "")
    return {"batch_id": batch_id, "parsed_at": parsed_at, "files": files}

def _mark_requirements_batch(reqs: List[Requirement], uploaded_files: List[Any]) -> None:
    batch_time = datetime.now()
    batch_id = batch_time.strftime("upload_%Y%m%d_%H%M%S")
    file_names = [getattr(f, "name", "") for f in uploaded_files]
    for req in reqs:
        meta = _meta_to_dict(req.ingestion_metadata)
        if not meta.get("source_file") and file_names:
            meta["source_file"] = file_names[0]
        if not meta.get("project_name") and (meta.get("source_file") or file_names):
            meta["project_name"] = meta.get("source_file") or file_names[0]
        meta["upload_batch_id"] = batch_id
        meta["parsed_at"] = batch_time.isoformat(timespec="seconds")
        meta["batch_files"] = file_names
        req.ingestion_metadata = meta
    st.session_state.current_upload_batch_id = batch_id
    st.session_state.current_upload_parsed_at = batch_time.isoformat(timespec="seconds")
    st.session_state.current_upload_files = file_names

def _stamp_generated_cases(cases: List[TestCase]) -> List[TestCase]:
    generated_at = datetime.now().isoformat(timespec="seconds")
    batch = _current_batch_meta()
    req_project_map = {r.id: _get_requirement_project_name(r) for r in st.session_state.context.requirements}
    for tc in cases:
        env = tc.system_env if isinstance(tc.system_env, dict) else {}
        env = dict(env or {})
        env["generated_at"] = generated_at
        env["source_upload_batch_id"] = batch.get("batch_id", "")
        env["source_parsed_at"] = batch.get("parsed_at", "")
        env["source_files"] = batch.get("files", [])
        env["project_name"] = req_project_map.get(tc.related_req_id) or (batch.get("files", [""])[0] if batch.get("files") else "")
        tc.system_env = env
    st.session_state.current_generation_time = generated_at
    return cases

def _case_generated_at(tc: TestCase) -> str:
    env = tc.system_env if isinstance(tc.system_env, dict) else {}
    return _format_dt((env or {}).get("generated_at"))

def _extract_feishu_tokens(url: str) -> Dict[str, str]:
    text = (url or "").strip()
    result = {
        "spreadsheet_token": "",
        "sheet_id": "",
        "document_id": "",
        "app_token": "",
    }
    if not text:
        return result
    m_sheet = re.search(r"/sheets/([A-Za-z0-9]+)", text)
    if m_sheet:
        result["spreadsheet_token"] = m_sheet.group(1)
    m_sheet_id = re.search(r"[?&]sheet=([A-Za-z0-9]+)", text)
    if m_sheet_id:
        result["sheet_id"] = m_sheet_id.group(1)
    m_doc = re.search(r"/docx/([A-Za-z0-9]+)", text)
    if m_doc:
        result["document_id"] = m_doc.group(1)
    m_base = re.search(r"/base/([A-Za-z0-9]+)", text)
    if m_base:
        result["app_token"] = m_base.group(1)
    return result


def _build_feishu_sheet_url(spreadsheet_token: str, sheet_id: str = "") -> str:
    token = (spreadsheet_token or "").strip()
    sid = (sheet_id or "").strip()
    if not token:
        return ""
    url = f"https://feishu.cn/sheets/{token}"
    if sid:
        url += f"?sheet={sid}"
    return url


def _sheet_export_setting_defaults() -> Dict[str, Any]:
    return {
        "feishu_shared_url": "",
        "feishu_app_id": os.getenv("FEISHU_APP_ID", ""),
        "feishu_tenant_token": os.getenv("FEISHU_TENANT_TOKEN", ""),
        "feishu_app_secret": os.getenv("FEISHU_APP_SECRET", ""),
        "feishu_open_base_url": os.getenv("FEISHU_OPEN_BASE_URL", "https://open.feishu.cn"),
        "feishu_requirement_link_base_url": os.getenv("FEISHU_REQUIREMENT_LINK_BASE_URL", ""),
        "feishu_spreadsheet_token": os.getenv("FEISHU_SPREADSHEET_TOKEN", ""),
        "feishu_sheet_id": os.getenv("FEISHU_SHEET_ID", ""),
        "feishu_sheet_start_cell": os.getenv("FEISHU_SHEET_START_CELL", "A1"),
        "feishu_auto_create_sheet": True,
        "feishu_sheet_title": "",
        "feishu_sheet_folder_token": os.getenv("FEISHU_SHEET_FOLDER_TOKEN", ""),
        "feishu_last_sheet_url": "",
    }


def _load_sheet_export_settings() -> None:
    saved = load_json("feishu_sheet_settings") or {}
    defaults = _sheet_export_setting_defaults()
    for key, default in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = saved.get(key, default)


def _save_sheet_export_settings() -> None:
    defaults = _sheet_export_setting_defaults()
    payload = {key: st.session_state.get(key, default) for key, default in defaults.items()}
    save_json("feishu_sheet_settings", payload)


def _get_requirement_project_name(req: Requirement) -> str:
    meta = _meta_to_dict(req.ingestion_metadata)
    if meta.get("project_name"):
        return str(meta.get("project_name"))
    if meta.get("source_file"):
        return str(meta.get("source_file"))
    batch_files = meta.get("batch_files") or []
    if batch_files:
        return str(batch_files[0])
    return ""


def _backfill_case_project_names(cases: List[TestCase], requirements: List[Requirement]) -> None:
    req_project_map = {r.id: _get_requirement_project_name(r) for r in requirements}
    fallback_name = (st.session_state.get("current_upload_files") or [""])[0]
    for tc in cases:
        env = _meta_to_dict(tc.system_env)
        if env.get("project_name"):
            continue
        project_name = req_project_map.get(tc.related_req_id) or fallback_name
        if project_name:
            env["project_name"] = project_name
            tc.system_env = env

_KG_CANDIDATES_PATH = _PROJECT_ROOT / "data" / "kg_candidates.json"

def _load_kg_candidates() -> List[Dict[str, Any]]:
    data = load_json("kg_candidates")
    return data if isinstance(data, list) else []

def _save_kg_candidates(candidates: List[Dict[str, Any]]) -> None:
    save_json("kg_candidates", candidates)

def _init_kg_candidate_state() -> None:
    if "kg_candidates" not in st.session_state:
        st.session_state.kg_candidates = _load_kg_candidates()

def _candidate_signature(module: str, item_type: str, content: str) -> str:
    payload = f"{(module or '').strip().lower()}||{(item_type or '').strip()}||{_normalize_text(content or '')}"
    return hashlib.md5(payload.encode("utf-8")).hexdigest()

def _queue_kg_candidate(module: str, item_type: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
    module = (module or "").strip() or "Unknown"
    content = _normalize_text(content or "")
    if not content:
        return False
    _init_kg_candidate_state()
    sign = _candidate_signature(module, item_type, content)
    candidates = st.session_state.kg_candidates
    if any(item.get("signature") == sign for item in candidates):
        return False
    candidates.append({
        "id": f"kgc_{datetime.now().strftime('%Y%m%d%H%M%S')}_{len(candidates)+1}",
        "signature": sign,
        "module": module,
        "item_type": item_type,
        "content": content,
        "metadata": metadata or {},
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "status": "pending",
    })
    _save_kg_candidates(candidates)
    return True

def _remove_kg_candidate(candidate_id: str) -> None:
    _init_kg_candidate_state()
    st.session_state.kg_candidates = [c for c in st.session_state.kg_candidates if c.get("id") != candidate_id]
    _save_kg_candidates(st.session_state.kg_candidates)

def _requirement_kg_candidates(req: Requirement) -> List[Dict[str, Any]]:
    module = _get_entity(req.extracted_entities, "module", "Unknown")
    feature = _get_entity(req.extracted_entities, "feature", "")
    constraints = _constraints_list(req.extracted_entities)
    candidates: List[Dict[str, Any]] = []
    for text in constraints[:6]:
        candidates.append({
            "module": module,
            "item_type": "Rule",
            "content": text,
            "metadata": {"source": "requirement_parse", "req_id": req.id, "feature": feature},
        })
    if feature:
        candidates.append({
            "module": module,
            "item_type": "Business",
            "content": f"{feature}：{_normalize_text(req.original_text)[:200]}",
            "metadata": {"source": "requirement_parse", "req_id": req.id, "feature": feature},
        })
    return candidates

def _queue_requirement_candidates(reqs: List[Requirement]) -> int:
    count = 0
    for req in reqs:
        for item in _requirement_kg_candidates(req):
            if _queue_kg_candidate(item["module"], item["item_type"], item["content"], item.get("metadata")):
                count += 1
    return count

def _split_candidate_phrases(text: str) -> List[str]:
    text = _normalize_text(text or "")
    if not text:
        return []
    parts = re.split(r"[；;。！？\n]|(?<=\))\s+|(?<=）)\s+|(?<=\d)\.\s*", text)
    result: List[str] = []
    seen = set()
    for part in parts:
        part = _normalize_text(part)
        if len(part) < 4:
            continue
        key = part.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(part)
    return result

def _queue_requirement_delta_candidates(
    req: Requirement,
    old_module: str,
    old_feature: str,
    old_text: str,
    new_module: str,
    new_feature: str,
    new_text: str,
) -> int:
    module = (new_module or old_module or "Unknown").strip() or "Unknown"
    count = 0
    if _normalize_text(old_module) != _normalize_text(new_module) and _normalize_text(new_module):
        count += 1 if _queue_kg_candidate(
            module,
            "Business",
            f"模块归属调整为：{new_module}",
            {"source": "requirement_diff", "req_id": req.id, "field": "module"},
        ) else 0
    if _normalize_text(old_feature) != _normalize_text(new_feature) and _normalize_text(new_feature):
        count += 1 if _queue_kg_candidate(
            module,
            "Business",
            f"功能归属调整为：{new_feature}",
            {"source": "requirement_diff", "req_id": req.id, "field": "feature"},
        ) else 0

    old_parts = set(_split_candidate_phrases(old_text))
    new_parts = [p for p in _split_candidate_phrases(new_text) if p not in old_parts]
    for part in new_parts[:6]:
        item_type = "Rule" if any(flag in part for flag in ["必须", "不得", "不能", "应", "需", "规则", "限制", "时长", "次数"]) else "Business"
        count += 1 if _queue_kg_candidate(
            module,
            item_type,
            part,
            {"source": "requirement_diff", "req_id": req.id, "feature": new_feature or old_feature},
        ) else 0
    return count

_STEP_PREFIX_RE = re.compile(r"^\s*(?:\d+\s*[.)、．]|[-*•])\s+")

def _parse_steps_text(text: str) -> List[str]:
    lines = []
    for ln in (text or "").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        ln = _STEP_PREFIX_RE.sub("", ln).strip()
        ln = re.sub(r"\s+", " ", ln)
        if ln:
            lines.append(ln)
    out: List[str] = []
    seen = set()
    for ln in lines:
        key = ln.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(ln)
    return out

def _number_steps(steps: List[str]) -> List[str]:
    cleaned = []
    for s in steps or []:
        s = (s or "").strip()
        if not s:
            continue
        s = _STEP_PREFIX_RE.sub("", s).strip()
        s = re.sub(r"\s+", " ", s)
        if s:
            cleaned.append(s)
    return [f"{i}. {s}" for i, s in enumerate(cleaned, start=1)]

def _steps_to_text(steps: List[str]) -> str:
    return "\n".join([str(s).strip() for s in (steps or []) if str(s).strip()])

def _case_signature(tc: TestCase) -> str:
    ti = tc.get_test_instruction()
    title = _normalize_text(tc.title or "")
    steps = "|".join(_normalize_text(s) for s in (ti.steps or []))
    expected = _normalize_text(ti.expected_result or "")
    payload = f"{title}||{steps}||{expected}"
    return hashlib.md5(payload.encode("utf-8")).hexdigest()

def _case_semantic_bucket(tc: TestCase) -> str:
    ti = tc.get_test_instruction()
    title = _normalize_text(tc.title or "")
    pre = _normalize_text(ti.pre_condition or "")
    pre = pre.replace("功能可正常访问", "").replace("模块已部署且基础数据准备完成", "")
    pre = re.sub(r"\s+", " ", pre).strip(" /")
    return pre or title

def _case_quality_score(tc: TestCase) -> tuple:
    ti = tc.get_test_instruction()
    title = _normalize_text(tc.title or "")
    steps = [s for s in (ti.steps or []) if _normalize_text(s)]
    expected = _normalize_text(ti.expected_result or "")
    is_mock = "(mock)" in (tc.title or "").lower() or expected.lower() == "system behaves as required."
    has_generic = any(k in title.lower() for k in ["verify ", "mock", "success path"]) or expected.lower() in {
        "system behaves as required.",
        "success",
        "success.",
    }
    return (
        0 if is_mock else 1,
        0 if has_generic else 1,
        len(steps),
        len(expected),
        len(title),
    )

def _dedupe_project_cases(cases: List[TestCase]) -> List[TestCase]:
    best_by_sig: Dict[str, TestCase] = {}
    for tc in cases:
        sig = _case_signature(tc)
        current = best_by_sig.get(sig)
        if current is None or _case_quality_score(tc) > _case_quality_score(current):
            best_by_sig[sig] = tc
    deduped = list(best_by_sig.values())

    # Semantic pruning: if a bucket already has concrete capability cases, drop older broad "核心能力覆盖校验" style cases.
    by_bucket: Dict[str, List[TestCase]] = {}
    for tc in deduped:
        by_bucket.setdefault(_case_semantic_bucket(tc), []).append(tc)

    final_cases: List[TestCase] = []
    for _, bucket_cases in by_bucket.items():
        has_specific_positive = any(
            ("核心能力覆盖校验" not in _normalize_text(tc.title or ""))
            and ("异常输入校验" not in _normalize_text(tc.title or ""))
            and ("规则约束校验" not in _normalize_text(tc.title or ""))
            and ("校验" in _normalize_text(tc.title or ""))
            for tc in bucket_cases
        )
        for tc in bucket_cases:
            title = _normalize_text(tc.title or "")
            if has_specific_positive and "核心能力覆盖校验" in title:
                continue
            final_cases.append(tc)
    return final_cases

def _preview_rows(cases: List[TestCase], req_meta: Optional[Dict[str, Dict[str, str]]] = None) -> List[Dict[str, Any]]:
    rows = []
    req_meta = req_meta or {}
    for tc in cases:
        ti = tc.get_test_instruction()
        meta = req_meta.get(tc.related_req_id) or {}
        rows.append({
            "Case ID": tc.test_case_id,
            "项目名称": meta.get("project_name") or _meta_to_dict(tc.system_env).get("project_name", "—"),
            "需求ID": tc.related_req_id,
            "模块": meta.get("module") or "—",
            "功能": meta.get("feature") or "—",
            "生成时间": _case_generated_at(tc),
            "标题": _normalize_text(tc.title or ""),
            "类型": tc.dimension,
            "优先级": tc.priority,
            "步骤数": len(ti.steps or []),
            "预期结果": _normalize_text(ti.expected_result or "")[:120],
        })
    return rows

def _set_conf(meta, value: float):
    """兼容设置 parsing_confidence"""
    if isinstance(meta, dict):
        meta["parsing_confidence"] = value
    else:
        meta.parsing_confidence = value

def _get_entity(entities, key: str, default="") -> str:
    """兼容 ExtractedEntities 对象和 dict 两种情况"""
    if entities is None:
        return default
    if isinstance(entities, dict):
        return entities.get(key) or default
    return getattr(entities, key, None) or default

def _set_entity(entities, key: str, value):
    """兼容设置 entity 字段"""
    if isinstance(entities, dict):
        entities[key] = value
    else:
        setattr(entities, key, value)

def _get_req_spec(req: Requirement):
    raw = getattr(req, "req_spec", None)
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    return {
        "req_id": getattr(raw, "req_id", ""),
        "module_path": getattr(raw, "module_path", ""),
        "priority": getattr(raw, "priority", ""),
        "type": getattr(raw, "type", ""),
    }

def _constraints_list(entities) -> List[str]:
    if entities is None:
        return []
    raw = entities.get("constraints", []) if isinstance(entities, dict) else getattr(entities, "constraints", []) or []
    result: List[str] = []
    for item in raw:
        if isinstance(item, dict):
            txt = item.get("value") or item.get("text") or item.get("original") or item.get("type")
            if txt:
                result.append(str(txt))
        elif item:
            result.append(str(item))
    return result

def _req_debug_rows(reqs: List[Requirement]) -> List[Dict[str, Any]]:
    rows = []
    for r in reqs:
        spec = _get_req_spec(r)
        constraints = _constraints_list(r.extracted_entities)
        rows.append({
            "ID": r.id,
            "模块": _get_entity(r.extracted_entities, "module", "—"),
            "功能": _get_entity(r.extracted_entities, "feature", "—"),
            "路径": spec.get("module_path") or "—",
            "类型": str(spec.get("type") or "—"),
            "优先级": spec.get("priority") or "—",
            "约束数": len(constraints),
            "约束预览": "；".join(constraints[:3]) if constraints else "—",
            "置信度": _get_conf(r.ingestion_metadata),
        })
    return rows


async def call_api_kg_learn_history(module: str, history: List[Dict[str, Any]]):
    async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
        payload = {"module": module, "history": history}
        response = await client.post(f"{_BACKEND_URL}/kg/learn/history", json=payload, headers=_backend_headers())
        response.raise_for_status()
        return response.json()

async def call_api_generate_stream(requirements: List[Requirement]):
    """
    Consume SSE stream from backend and yield updates.
    """
    async with httpx.AsyncClient(timeout=300.0, trust_env=False) as client:
        req_dicts = [req.model_dump(mode="json") for req in requirements]
        async with client.stream("POST", f"{_BACKEND_URL}/generate/stream", json=req_dicts, headers=_backend_headers()) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data = json.loads(line[6:])
                    yield data

async def call_api_refine_stream(tc_list: List[TestCase], feedback: str):
    async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
        payload = {
            "tc_list": [tc.model_dump(mode="json") for tc in tc_list],
            "feedback": feedback
        }
        async with client.stream("POST", f"{_BACKEND_URL}/refine/stream", json=payload, headers=_backend_headers()) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    yield json.loads(line[6:])

async def call_api_kg_learn(module: str, tc: Optional[TestCase] = None, rule: Optional[str] = None):
    async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
        payload = {"module": module}
        if tc:
            payload["tc"] = tc.model_dump(mode="json")
        if rule:
            payload["rule"] = rule
            
        response = await client.post(f"{_BACKEND_URL}/kg/learn", json=payload, headers=_backend_headers())
        response.raise_for_status()
        res_data = response.json()
        return res_data.get("success", False), res_data.get("extracted_rules", [])

async def call_api_kg_add_item(module: str, item_type: str, content: str, metadata: Optional[Dict[str, Any]] = None):
    async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
        payload = {
            "module": module,
            "item_type": item_type,
            "content": content,
            "metadata": metadata or {},
        }
        response = await client.post(f"{_BACKEND_URL}/kg/learn/item", json=payload, headers=_backend_headers())
        response.raise_for_status()
        return response.json()

async def call_api_kg_postmortem(module: str, failure: str):
    async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
        payload = {"module": module, "failure": failure}
        response = await client.post(f"{_BACKEND_URL}/kg/learn/postmortem", json=payload, headers=_backend_headers())
        response.raise_for_status()
        return response.json()

_PROJECT_CONTEXT_PATH = _PROJECT_ROOT / "data" / "project_context.json"

# --- Inline SVG icons (flat stroke, no emoji) ---
_S = 'xmlns="http://www.w3.org/2000/svg"'
_VB = 'width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round'


def _svg(name: str) -> str:
    """Return full <svg> element for sidebar / headings."""
    inner_map = {
        "book": '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>',
        "upload": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>',
        "layers": '<polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/>',
        "clipboard": '<path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1" ry="1"/><path d="M9 12h6M9 16h6"/>',
        "sliders": '<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/>',
        "trash": '<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
        "layout": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18"/>',
        "package": '<path d="M16.5 9.4 7.55 4.24"/><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/>',
        "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
        "eye": '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>',
        "link": '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
        "info": '<circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>',
        "list-check": '<path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2"/><path d="M9 5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v0a2 2 0 0 1-2 2H11a2 2 0 0 1-2-2z"/><path d="m9 12 2 2 4-4"/>',
        "sparkles": '<path d="M9 3l1.2 3.6L14 8l-3.8 1.4L9 13l-1.2-3.6L4 8l3.8-1.4L9 3z"/><path d="M19 11l.9 2.7L23 15l-3.1 1.3L19 19l-.9-2.7L15 15l3.1-1.3L19 11z"/><path d="M16 3l.6 1.8L18 5.4l-1.4.6L16 8l-.6-2L14 5.4l1.4-.6L16 3z"/>',
    }
    inner = inner_map.get(name, inner_map["info"])
    return f'<svg {_S} {_VB}">{inner}</svg>'


def _heading_html(label: str, icon: str) -> str:
    return (
        f'<div class="app-section-head">'
        f'<span class="app-section-icon">{_svg(icon)}</span>'
        f'<span class="app-section-label">{label}</span>'
        f"</div>"
    )


def _render_heading(label: str, icon: str) -> None:
    return None


def _tool_item_html(title: str, active: bool = False, muted: bool = False) -> str:
    cls = "sidebar-menu-static active" if active else "sidebar-menu-static"
    return (
        f'<div class="{cls}">'
        f'<div class="sidebar-menu-row">'
        f'<div class="sidebar-menu-text">'
        f'<div class="sidebar-menu-main">'
        f'<span class="tool-title">{title}</span>'
        f"</div>"
        f'</div>'
        f"</div>"
    )


def _dashboard_card(title: str, value: str, meta: str = "", accent: str = "blue") -> str:
    return (
        f'<div class="dashboard-card dashboard-card-{accent}">'
        f'<div class="dashboard-card-accent"></div>'
        f'<div class="dashboard-card-title">{title}</div>'
        f'<div class="dashboard-card-value">{value}</div>'
        f'<div class="dashboard-card-meta">{meta}</div>'
        "</div>"
    )


def _rate_ring_html(percent: float, passed: int, failed: int, skipped: int) -> str:
    pct = max(0, min(int(round(percent)), 100))
    color = "#52c41a" if pct >= 80 else "#faad14" if pct >= 60 else "#ff4d4f"
    angle = pct * 3.6
    return f"""
    <div class="rate-ring-wrap">
        <div class="rate-ring" style="background: conic-gradient({color} {angle}deg, #edf2f7 {angle}deg 360deg);">
            <div class="rate-ring-inner">
                <div class="rate-ring-value">{pct}<span>%</span></div>
            </div>
        </div>
        <div class="rate-ring-legend">
            <div><span class="legend-dot passed"></span>通过 {passed}</div>
            <div><span class="legend-dot failed"></span>失败 {failed}</div>
            <div><span class="legend-dot skipped"></span>跳过 {skipped}</div>
        </div>
    </div>
    """


def _status_block_html(title: str, status: str, detail: str = "") -> str:
    status_map = {
        "success": ("status-success", "正常"),
        "degraded": ("status-warn", "降级"),
        "error": ("status-error", "异常"),
        "pending": ("status-pending", "等待"),
    }
    cls, text = status_map.get(status, ("status-pending", status or "未知"))
    return (
        f'<div class="status-block {cls}">'
        f'<div class="status-block-head"><span>{title}</span><span class="status-pill">{text}</span></div>'
        f'<div class="status-block-detail">{detail or "—"}</div>'
        f'</div>'
    )


def _menu_tree_group_html(title: str, active: bool = False) -> str:
    row_cls = "sidebar-menu-static active" if active else "sidebar-menu-static"
    return (
        f'<div class="{row_cls}">'
        f'<div class="sidebar-menu-row">'
        f'<div class="sidebar-menu-text">'
        f'<div class="sidebar-menu-main"><span class="tool-title">{title}</span></div>'
        f"</div>"
        f"</div>"
        f"</div>"
    )


def _nav_button(label: str, page: str) -> None:
    current = st.session_state.get("current_page", "首页")
    if st.button(
        label,
        key=f"nav_{page}",
        use_container_width=True,
        type="primary" if current == page else "secondary",
    ):
        st.session_state.current_page = page
        st.rerun()


def _render_nav_row(label: str, page: str, indent: bool = False) -> None:
    if indent:
        c_pad, c_text = st.columns([0.45, 6.55], gap="small")
        with c_pad:
            st.markdown("")
    else:
        c_text = st.container()
    with c_text:
        _nav_button(label, page)


def _topbar_html(page: str) -> str:
    return f"""
    <div class="platform-topbar">
        <div class="platform-topbar-left">
            <div class="platform-logo">W</div>
            <div>
                <div class="platform-name">WHartTest</div>
                <div class="platform-breadcrumb">一体化智能测试管理平台 / {page}</div>
            </div>
        </div>
        <div class="platform-topbar-right">
            <div class="platform-theme-btn" title="主题切换">T</div>
            <div class="platform-select-pill">演示项目 [Demo Project]</div>
            <div class="platform-pill">当前版本: v2.1.0</div>
            <div class="platform-user-wrap">
                <div class="platform-avatar">A</div>
                <div class="platform-user">admin</div>
                <div class="platform-user-caret">v</div>
            </div>
        </div>
    </div>
    """


APP_CSS = """
<style>
    .stApp { background: #f3f4f6; }
    .main .block-container { padding-top: 0.85rem; padding-bottom: 2rem; max-width: 1520px; }
    h1 { font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif; font-weight: 700; color: #111827; font-size: 1.9rem; margin-bottom: 0.15rem; }
    .platform-topbar {
        background: #ffffff;
        border: 1px solid #d9dee8;
        border-radius: 14px;
        padding: 0.85rem 1rem;
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 1rem;
        margin-bottom: 0.85rem;
        box-shadow: 0 2px 10px rgba(15, 23, 42, 0.04);
    }
    .platform-topbar-left, .platform-topbar-right { display: flex; align-items: center; gap: 0.8rem; }
    .platform-logo {
        width: 34px; height: 34px; border-radius: 8px; background: #2563eb; color: #fff;
        display: flex; align-items: center; justify-content: center; font-weight: 700;
        box-shadow: 0 6px 18px rgba(37, 99, 235, 0.25);
    }
    .platform-name { font-size: 1rem; font-weight: 700; color: #111827; }
    .platform-breadcrumb { font-size: 0.78rem; color: #6b7280; margin-top: 0.12rem; }
    .platform-select-pill {
        background: #f7f8fa; border: 1px solid #e5e7eb; color: #374151;
        border-radius: 8px; padding: 0.38rem 0.72rem; font-size: 0.8rem; font-weight: 500;
    }
    .platform-theme-btn {
        width: 32px; height: 32px; border-radius: 999px; background: #f7f8fa; border: 1px solid #e5e7eb;
        color: #374151; display: flex; align-items: center; justify-content: center; font-size: 0.92rem; font-weight: 700;
    }
    .platform-pill {
        background: #f3f4f6; border: 1px solid #e5e7eb; color: #374151;
        border-radius: 999px; padding: 0.35rem 0.65rem; font-size: 0.78rem; font-weight: 600;
    }
    .platform-avatar {
        width: 34px; height: 34px; border-radius: 999px; background: #0ea5e9; color: white;
        display: flex; align-items: center; justify-content: center; font-weight: 700;
    }
    .platform-user-wrap {
        display: flex; align-items: center; gap: 0.45rem; padding-left: 0.15rem;
    }
    .platform-user { font-size: 0.84rem; color: #374151; font-weight: 600; }
    .platform-user-caret { font-size: 0.74rem; color: #6b7280; margin-left: -0.1rem; }
    .sidebar-brand {
        background: #ffffff;
        border: 1px solid #dfe4ea;
        border-radius: 14px;
        padding: 0.9rem 0.95rem;
        display: flex;
        align-items: center;
        gap: 0.7rem;
        margin-bottom: 0.9rem;
        box-shadow: 0 1px 8px rgba(15, 23, 42, 0.04);
    }
    .sidebar-brand-logo {
        width: 36px; height: 36px; border-radius: 10px; background: linear-gradient(135deg, #2563eb 0%, #38bdf8 100%);
        color: #fff; display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 1rem;
    }
    .sidebar-brand-text { font-size: 0.96rem; font-weight: 700; color: #1f2937; }
    .app-shell-header {
        background: transparent;
        border: none;
        border-radius: 0;
        padding: 0.15rem 0 0.55rem 0;
        margin-bottom: 0.25rem;
        box-shadow: none;
    }
    .app-shell-title { display: flex; align-items: center; justify-content: space-between; gap: 1rem; }
    .app-shell-minor {
        color: #6b7280; font-size: 0.82rem; font-weight: 500; letter-spacing: 0.01em;
    }
    .dashboard-card {
        background: #ffffff;
        border: 1px solid #dfe4ea;
        border-radius: 14px;
        padding: 0.95rem 1rem;
        min-height: 112px;
        box-shadow: 0 1px 8px rgba(15, 23, 42, 0.03);
        position: relative;
        overflow: hidden;
    }
    .dashboard-card-accent { position: absolute; inset: 0 auto 0 0; width: 4px; background: #2563eb; }
    .dashboard-card-blue .dashboard-card-accent { background: #2563eb; }
    .dashboard-card-green .dashboard-card-accent { background: #52c41a; }
    .dashboard-card-orange .dashboard-card-accent { background: #faad14; }
    .dashboard-card-purple .dashboard-card-accent { background: #722ed1; }
    .dashboard-card-title { color: #6b7280; font-size: 0.86rem; margin-bottom: 0.5rem; }
    .dashboard-card-value { color: #111827; font-size: 1.9rem; font-weight: 700; line-height: 1.1; }
    .dashboard-card-meta { color: #6b7280; font-size: 0.82rem; margin-top: 0.45rem; }
    .dashboard-panel {
        background: #ffffff;
        border: 1px solid #dfe4ea;
        border-radius: 14px;
        padding: 1rem;
        margin-top: 0.9rem;
        box-shadow: 0 1px 8px rgba(15, 23, 42, 0.03);
    }
    .dashboard-panel-title { color: #111827; font-size: 1.02rem; font-weight: 700; margin-bottom: 0.8rem; }
    .sidebar-menu-static {
        border-radius: 6px; padding: 0.14rem 0; margin-bottom: 0.08rem; border: 1px solid transparent;
    }
    .sidebar-menu-static.active {
        background: rgba(0, 160, 233, 0.04);
        border-color: rgba(0, 160, 233, 0.08);
    }
    .sidebar-menu-row { display: flex; align-items: flex-start; gap: 0.45rem; }
    .sidebar-menu-text { min-width: 0; flex: 1; }
    .sidebar-menu-main { display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; }
    .sidebar-menu-child { margin-left: 0.85rem; position: relative; }
    .sidebar-menu-child::before {
        content: ""; position: absolute; left: -0.42rem; top: -0.12rem; bottom: -0.12rem; width: 1px; background: #e5e7eb;
    }
    .tool-title { font-weight: 600; color: #111827; font-size: 0.84rem; }
    .sidebar-group-title {
        color: #6b7280; font-size: 0.76rem; font-weight: 700; letter-spacing: 0.04em;
        margin: 0.35rem 0 0.45rem 0; text-transform: uppercase;
    }
    .sidebar-section-tip {
        color: #6b7280; font-size: 0.74rem; line-height: 1.35; margin-bottom: 0.32rem;
    }
    .metric-chip-row { display: flex; gap: 0.5rem; flex-wrap: wrap; margin-top: 0.65rem; }
    .metric-chip {
        background: #f3f4f6; color: #374151; border-radius: 999px;
        padding: 0.28rem 0.62rem; font-size: 0.76rem; font-weight: 600;
    }
    .progress-legend {
        display: flex; gap: 0.9rem; flex-wrap: wrap; margin: 0.35rem 0 0.65rem 0;
        color: #6b7280; font-size: 0.78rem;
    }
    .progress-dot { width: 8px; height: 8px; display: inline-block; border-radius: 999px; margin-right: 0.3rem; }
    .rate-ring-wrap { display: flex; flex-direction: column; align-items: center; gap: 0.8rem; padding-top: 0.3rem; }
    .rate-ring {
        width: 138px; height: 138px; border-radius: 999px; display: flex; align-items: center; justify-content: center;
    }
    .rate-ring-inner {
        width: 104px; height: 104px; border-radius: 999px; background: #ffffff; display: flex; align-items: center; justify-content: center;
        box-shadow: inset 0 0 0 1px #eef2f7;
    }
    .rate-ring-value { font-size: 2rem; font-weight: 700; color: #111827; line-height: 1; }
    .rate-ring-value span { font-size: 0.9rem; color: #6b7280; margin-left: 0.15rem; }
    .rate-ring-legend { width: 100%; display: grid; grid-template-columns: 1fr; gap: 0.45rem; color: #4b5563; font-size: 0.82rem; }
    .legend-dot.passed { background: #52c41a; }
    .legend-dot.failed { background: #ff4d4f; }
    .legend-dot.skipped { background: #faad14; }
    .status-block {
        background: #ffffff; border: 1px solid #e5e7eb; border-radius: 10px; padding: 0.7rem 0.8rem; margin-bottom: 0.45rem;
    }
    .status-block-head { display: flex; justify-content: space-between; align-items: center; gap: 0.5rem; color: #111827; font-size: 0.84rem; font-weight: 700; }
    .status-block-detail { color: #6b7280; font-size: 0.76rem; margin-top: 0.28rem; line-height: 1.35; }
    .status-pill { border-radius: 999px; padding: 0.15rem 0.45rem; font-size: 0.72rem; font-weight: 700; }
    .status-success .status-pill { background: #ecfdf3; color: #15803d; }
    .status-warn .status-pill { background: #fff7ed; color: #c2410c; }
    .status-error .status-pill { background: #fef2f2; color: #b91c1c; }
    .status-pending .status-pill { background: #f3f4f6; color: #4b5563; }
    h2, h3 { font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif; font-weight: 600; color: #111827; }
    .app-section-head { display: flex; align-items: center; gap: 0.5rem; margin: 0.5rem 0 0.75rem 0; }
    .app-section-icon { display: inline-flex; color: #1e40af; align-items: center; justify-content: center; }
    .app-section-icon svg { display: block; }
    .app-section-label { font-size: 1.05rem; font-weight: 600; color: #111827; }
    .tutorial-step { display: flex; gap: 0.75rem; align-items: flex-start; margin: 0.6rem 0; padding: 0.5rem 0; border-bottom: 1px solid #e5e7eb; }
    .tutorial-step:last-child { border-bottom: none; }
    .tutorial-num {
        flex-shrink: 0; width: 1.75rem; height: 1.75rem; border-radius: 4px;
        background: #1e40af; color: #fff; font-size: 0.85rem; font-weight: 600;
        display: flex; align-items: center; justify-content: center;
    }
    .tutorial-body { color: #374151; font-size: 0.92rem; line-height: 1.5; }
    .tutorial-note { background: #f9fafb; border-left: 3px solid #1e40af; padding: 0.75rem 1rem; margin-top: 1rem; color: #4b5563; font-size: 0.88rem; }
    .stButton > button[kind="primary"] {
        background-color: #1e40af !important; color: #ffffff !important; border: 1px solid #1e3a8a !important;
    }
    .stButton > button[kind="primary"]:hover { background-color: #1d4ed8 !important; }
    [data-testid="stSidebar"] { background-color: #f8f9fb; border-right: 1px solid #dfe4ea; }
    [data-testid="stSidebar"] > div:first-child { padding-top: 0.6rem; }
    [data-testid="stSidebar"] .stButton { margin: 0; }
    [data-testid="stSidebar"] [data-testid="column"] { padding-top: 0 !important; padding-bottom: 0 !important; }
    [data-testid="stSidebar"] .stButton > button {
        background: transparent !important;
        border: 1px solid transparent !important;
        color: #374151 !important;
        border-radius: 6px !important;
        min-height: 28px !important;
        height: 28px !important;
        padding: 0 8px !important;
        justify-content: flex-start !important;
        font-size: 0.84rem !important;
        font-weight: 500 !important;
        box-shadow: none !important;
    }
    [data-testid="stSidebar"] .stButton > button:hover {
        background: rgba(0, 160, 233, 0.06) !important;
        border-color: rgba(0, 160, 233, 0.08) !important;
        color: #2563eb !important;
    }
    [data-testid="stSidebar"] .stButton > button[kind="primary"] {
        background: rgba(0, 160, 233, 0.08) !important;
        border: 1px solid rgba(0, 160, 233, 0.14) !important;
        color: #2563eb !important;
        box-shadow: inset 2px 0 0 #2563eb !important;
    }
    [data-testid="stSidebar"] .stRadio label { font-size: 0.92rem; }
    [data-testid="stSidebar"] .stRadio > div { gap: 0.25rem; }
    [data-testid="stSidebar"] .stRadio [role="radiogroup"] > label {
        background: #ffffff; border: 1px solid #e5e7eb; border-radius: 10px; padding: 9px 10px;
    }
    [data-testid="stSidebar"] .stRadio [role="radiogroup"] > label:has(input:checked) {
        background: linear-gradient(135deg, #eff6ff 0%, #dbeafe 100%); border-color: #93c5fd;
        box-shadow: inset 3px 0 0 #2563eb;
    }
    [data-testid="stSidebar"] [data-testid="column"] { align-self: start; }
    div[data-testid="stMetric"] {
        background: #ffffff; border: 1px solid #dfe4ea; border-radius: 14px; padding: 0.65rem 0.8rem;
    }
    .stDataFrame, .stTable { background: #ffffff; border-radius: 14px; }
</style>
"""


def get_test_case_exporter():
    from src.core.output.exporter import TestCaseExporter

    return TestCaseExporter


def get_postman_exporter():
    from src.core.output.postman_exporter import PostmanExporter

    return PostmanExporter


def get_feishu_client(**kwargs):
    from src.core.output.feishu_client import FeishuClient

    return FeishuClient(**kwargs)


def get_testlink_importer():
    from src.core.integration.testlink_service import TestLinkImporter

    return TestLinkImporter


@st.cache_resource
def init_services():
    # 延迟导入：避免打开页面时加载 Docling / NetworkX / OpenAI 等大依赖
    from src.core.ai.llm_service import LLMService
    from src.core.feedback.manager import FeedbackManager
    from src.core.generation.generator import TestCaseGenerator
    from src.core.ingestion.ingestor import RequirementIngestor
    from src.core.kg.graph_service import KnowledgeGraphService

    llm_service = LLMService(
        api_key=_FIXED_LLM_API_KEY,
        base_url=_FIXED_LLM_BASE_URL,
        model=_FIXED_LLM_MODEL,
    )
    llm_service.model_judge = _FIXED_LLM_MODEL
    connection = llm_service.check_connection()
    if connection.get("status") != "success":
        raise RuntimeError(connection.get("message") or "本地模型连接测试失败。")
    kg_service = KnowledgeGraphService()
    return {
        "ingestor": RequirementIngestor(),
        "generator": TestCaseGenerator(llm_service, kg_service),
        "feedback": FeedbackManager(llm_service),
    }


def _run_frontend_service_init(force: bool = False) -> Dict[str, Any]:
    if force:
        init_services.clear()
    try:
        os.environ["LLM_MODEL_GEN"] = _FIXED_LLM_MODEL
        os.environ["LLM_MODEL_JUDGE"] = _FIXED_LLM_MODEL
        os.environ["OPENAI_BASE_URL"] = _FIXED_LLM_BASE_URL
        os.environ["OPENAI_API_KEY"] = _FIXED_LLM_API_KEY
        services = init_services()
        st.session_state.services = services
        status = {
            "status": "success",
            "message": f"核心服务已就绪 (Gen/Judge: {_FIXED_LLM_MODEL})",
            "checked_at": datetime.now().isoformat(timespec="seconds"),
        }
    except Exception as e:
        st.session_state.services = {}
        status = {
            "status": "error",
            "message": f"初始化失败: {e}",
            "checked_at": datetime.now().isoformat(timespec="seconds"),
        }
    st.session_state.frontend_startup_status = status
    return status


def _auto_bootstrap_startup(force: bool = False) -> None:
    should_run = force or not st.session_state.get("startup_bootstrap_done", False)
    if not should_run:
        return
    st.session_state.backend_startup_status = _get_backend_startup_status()
    _run_frontend_service_init(force=force)
    st.session_state.startup_bootstrap_done = True


def _ensure_frontend_services(force_retry: bool = False) -> bool:
    if st.session_state.services.get("ingestor"):
        return True
    status = _run_frontend_service_init(force=force_retry)
    return status.get("status") == "success" and bool(st.session_state.services.get("ingestor"))


def _queue_widget_state_updates(updates: Dict[str, Any], message: str = "", level: str = "success") -> None:
    pending = dict(st.session_state.get("_pending_widget_state_updates", {}))
    pending.update({k: v for k, v in updates.items() if v is not None})
    st.session_state["_pending_widget_state_updates"] = pending
    if message:
        st.session_state["_pending_widget_state_notice"] = {
            "level": level,
            "message": message,
        }


def _apply_pending_widget_state_updates() -> None:
    pending = st.session_state.get("_pending_widget_state_updates", {})
    if pending:
        for key, value in pending.items():
            st.session_state[key] = value
        st.session_state["_pending_widget_state_updates"] = {}


def _flush_pending_widget_state_notice() -> None:
    notice = st.session_state.get("_pending_widget_state_notice")
    if not notice:
        return
    level = notice.get("level", "info")
    message = notice.get("message", "")
    if message:
        if level == "success":
            st.success(message)
        elif level == "warning":
            st.warning(message)
        elif level == "error":
            st.error(message)
        else:
            st.info(message)
    st.session_state["_pending_widget_state_notice"] = None


def save_context() -> None:
    st.session_state.context.test_cases = _dedupe_project_cases(st.session_state.context.test_cases)
    annotate_quality_characteristics(
        st.session_state.context.requirements,
        st.session_state.context.test_cases,
    )
    st.session_state.req_count = len(st.session_state.context.requirements)
    st.session_state.case_count = len(st.session_state.context.test_cases)
    st.session_state.case_map = {}
    for tc in st.session_state.context.test_cases:
        st.session_state.case_map.setdefault(tc.related_req_id, []).append(tc)

    # Save to Database
    try:
        from src.data.database import get_session
        import json
        from pydantic import BaseModel

        def _jsonify(obj):
            if isinstance(obj, BaseModel):
                return obj.model_dump(mode="json")
            return obj

        with get_session() as session:
            for req in st.session_state.context.requirements:
                req.ingestion_metadata = _jsonify(req.ingestion_metadata)
                req.extracted_entities = _jsonify(req.extracted_entities)
                req.req_spec = _jsonify(req.req_spec)
                session.merge(req)
            for tc in st.session_state.context.test_cases:
                tc.business_logic = _jsonify(tc.business_logic)
                tc.test_instruction = _jsonify(tc.test_instruction)
                tc.system_env = _jsonify(tc.system_env)
                session.merge(tc)
            session.commit()
    except Exception as e:
        st.error(f"数据库保存失败: {e}")

def _init_session() -> None:
    # 1. Initialize Database and Migrate if necessary
    init_db()
    if not os.path.exists("data/app_database.db") or os.path.exists(_PROJECT_CONTEXT_PATH):
        migrate_json_to_db()

    if "context" not in st.session_state:
        # Load from Database instead of JSON
        try:
            reqs = get_all_requirements()
            tcs = get_all_test_cases()
            st.session_state.context = ProjectContext(
                project_name="SQLite Project",
                requirements=reqs,
                test_cases=tcs
            )
            if not reqs and not tcs:
                 # Fallback to JSON if DB is empty but JSON exists (just in case)
                 if _PROJECT_CONTEXT_PATH.is_file():
                     saved_data = load_json("project_context")
                     if saved_data:
                         st.session_state.context = ProjectContext.model_validate(saved_data)
        except Exception as e:
            st.error(f"无法恢复历史项目数据: {e}")
            st.session_state.context = ProjectContext()

        changed = annotate_quality_characteristics(
            st.session_state.context.requirements,
            st.session_state.context.test_cases,
        )
        if changed["requirements_changed"] or changed["test_cases_changed"]:
            save_context()

    if "req_count" not in st.session_state:
        st.session_state.req_count = len(st.session_state.context.requirements)
    if "case_count" not in st.session_state:
        st.session_state.case_count = len(st.session_state.context.test_cases)
    if "case_map" not in st.session_state:
        st.session_state.case_map = {}
        for tc in st.session_state.context.test_cases:
            st.session_state.case_map.setdefault(tc.related_req_id, []).append(tc)

    if "services" not in st.session_state:
        st.session_state.services = {}
    if "frontend_startup_status" not in st.session_state:
        st.session_state.frontend_startup_status = {
            "status": "pending",
            "message": "等待自动初始化",
            "checked_at": "",
        }
    if "backend_startup_status" not in st.session_state:
        st.session_state.backend_startup_status = {
            "status": "pending",
            "message": "等待后端启动自检",
            "checked_at": "",
            "steps": [],
        }
    if "startup_bootstrap_done" not in st.session_state:
        st.session_state.startup_bootstrap_done = False
    if "current_page" not in st.session_state:
        st.session_state.current_page = "首页"
    elif st.session_state.current_page == "使用说明":
        st.session_state.current_page = "首页"
    _init_kg_candidate_state()

    batch_meta = _current_batch_meta()
    if "current_upload_batch_id" not in st.session_state:
        st.session_state.current_upload_batch_id = batch_meta.get("batch_id", "")
    if "current_upload_parsed_at" not in st.session_state:
        st.session_state.current_upload_parsed_at = batch_meta.get("parsed_at", "")
    if "current_upload_files" not in st.session_state:
        st.session_state.current_upload_files = batch_meta.get("files", [])
    if "current_generation_time" not in st.session_state:
        current_cases = _current_batch_cases()
        latest_time = ""
        if current_cases:
            latest_time = max(
                (_meta_to_dict(tc.system_env).get("generated_at", "") for tc in current_cases),
                default="",
            )
        st.session_state.current_generation_time = latest_time
    if "requirement_change_report" not in st.session_state:
        st.session_state.requirement_change_report = {}
    if "ai_eval_report" not in st.session_state:
        st.session_state.ai_eval_report = {}
    if "ai_eval_scope" not in st.session_state:
        st.session_state.ai_eval_scope = ""
    if "_pending_widget_state_updates" not in st.session_state:
        st.session_state["_pending_widget_state_updates"] = {}
    if "_pending_widget_state_notice" not in st.session_state:
        st.session_state["_pending_widget_state_notice"] = None
    _load_sheet_export_settings()


def render_sidebar() -> None:
    current = st.session_state.get("current_page", "首页")
    if current == "使用说明":
        st.session_state.current_page = "首页"
        current = "首页"
    st.markdown(
        """
        <div class="sidebar-brand">
            <div class="sidebar-brand-logo">W</div>
            <div class="sidebar-brand-text">WHartTest</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    _render_nav_row("首页", "首页")

    st.markdown(_menu_tree_group_html("AI测试用例生成", active=True), unsafe_allow_html=True)
    nav_items = [
        ("数据统计", "数据统计"),
        ("导入需求", "导入需求"),
        ("生成用例", "生成用例"),
        ("评审与导出", "评审与导出"),
        ("知识图谱", "知识图谱"),
    ]
    for label, page in nav_items:
        _render_nav_row(label, page, indent=True)

    st.markdown(_tool_item_html("UI自动化", muted=True), unsafe_allow_html=True)
    st.markdown(_tool_item_html("接口测试设计", muted=True), unsafe_allow_html=True)
    st.markdown(_tool_item_html("MCP / Skills", muted=True), unsafe_allow_html=True)

def render_tab_guide() -> None:
    st.session_state.current_page = "首页"
    st.rerun()


def render_tab_stats() -> None:
    _render_heading("数据统计", "layout")
    st.caption("该页面为只读统计视图，不提供任何编辑入口；“质量特性”分类结果已写回数据库，并用于后续导出。")

    stats = build_statistics(
        st.session_state.context.requirements,
        st.session_state.context.test_cases,
    )
    overview = stats["overview"]
    s1, s2, s3, s4 = st.columns(4)
    s1.metric("需求总数", overview["需求总数"])
    s2.metric("用例总数", overview["用例总数"])
    s3.metric("分类总数", overview["分类总数"])
    s4.metric("已分类数据", overview["已分类需求数"] + overview["已分类用例数"])

    st.markdown("### 分类汇总")
    st.dataframe(pd.DataFrame(stats["category_summary"]), use_container_width=True, hide_index=True)

    d1, d2 = st.columns(2)
    with d1:
        st.markdown("### 需求分类明细")
        st.dataframe(pd.DataFrame(stats["requirement_rows"]), use_container_width=True, hide_index=True)
    with d2:
        st.markdown("### 用例分类明细")
        st.dataframe(pd.DataFrame(stats["case_rows"]), use_container_width=True, hide_index=True)

    ready = sum(
        1
        for r in st.session_state.context.requirements
        if _get_conf(r.ingestion_metadata) >= 0.8
    )
    current_cases = _current_batch_cases()
    c1, c2, c3 = st.columns(3)
    c1.metric("已导入需求", len(st.session_state.context.requirements))
    c2.metric("就绪可生成", ready)
    c3.metric("本次已生成", len(current_cases))

    batch_meta = _current_batch_meta()
    if batch_meta.get("parsed_at"):
        st.caption(
            f"本次解析时间：{_format_dt(batch_meta.get('parsed_at'))}"
            + (f" | 文件：{', '.join(batch_meta.get('files', []))}" if batch_meta.get("files") else "")
        )

    if not st.session_state.services.get("ingestor"):
        st.warning("模型服务暂未就绪；系统会在导入或生成时自动重试连接。")
    elif not st.session_state.context.requirements:
        st.info("下一步：在「导入需求」中上传并解析文档。")
    elif ready == 0:
        st.info("下一步：在「评审与导出」中完善低置信度需求并标记就绪。")
    elif not st.session_state.context.test_cases:
        st.info("下一步：在「生成用例」中执行批量生成。")
    else:
        st.success("可进行用例校对与导出。")


def render_home_dashboard() -> None:
    ctx = st.session_state.context
    current_cases = _current_batch_cases()
    stats = build_statistics(
        st.session_state.context.requirements,
        st.session_state.context.test_cases,
    )
    summary_rows = pd.DataFrame(stats["category_summary"]).sort_values(by=["用例数", "需求数"], ascending=False)
    top_category = "—"
    if not summary_rows.empty and int(summary_rows.iloc[0]["用例数"]) > 0:
        top_category = str(summary_rows.iloc[0]["分类"])
    avg_time = ctx.total_generation_time / ctx.total_requests if ctx.total_requests > 0 else 0
    hit_rate = (ctx.kg_hit_count / ctx.total_requests * 100) if ctx.total_requests > 0 else 0
    pass_rate = 0.0
    if current_cases:
        pass_count = sum(
            1 for tc in current_cases
            if _meta_to_dict(tc.system_env).get("execution_status") == "Pass"
        )
        pass_rate = pass_count / len(current_cases) * 100
    fail_count = sum(
        1 for tc in current_cases
        if _meta_to_dict(tc.system_env).get("execution_status") == "Fail"
    )
    pass_count = sum(
        1 for tc in current_cases
        if _meta_to_dict(tc.system_env).get("execution_status") == "Pass"
    )
    nt_count = max(len(current_cases) - pass_count - fail_count, 0) if current_cases else 0
    latest_req_rows = pd.DataFrame(stats["requirement_rows"]).head(8)
    latest_case_rows = pd.DataFrame(stats["case_rows"]).head(8)
    category_chart = summary_rows[["分类", "用例数"]].set_index("分类") if not summary_rows.empty else pd.DataFrame(columns=["用例数"])
    trend_rows: List[Dict[str, Any]] = []
    for tc in st.session_state.context.test_cases:
        generated_at = _meta_to_dict(tc.system_env).get("generated_at", "")
        if generated_at:
            trend_rows.append({
                "日期": str(generated_at)[:10],
                "数量": 1,
            })
    trend_df = pd.DataFrame(trend_rows)
    if not trend_df.empty:
        trend_df = trend_df.groupby("日期", as_index=False)["数量"].sum().sort_values("日期")
    token_rows = pd.DataFrame([
        {"指标": "累计请求数", "值": ctx.total_requests},
        {"指标": "累计 Tokens", "值": ctx.total_tokens},
        {"指标": "最近生成时间", "值": _format_dt(st.session_state.current_generation_time)},
        {"指标": "当前批次文件", "值": ", ".join(st.session_state.get("current_upload_files", [])) or "—"},
    ])

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(_dashboard_card("功能用例", str(len(current_cases)), f"通过 {pass_count} · 待处理 {fail_count + nt_count}", "green"), unsafe_allow_html=True)
    with c2:
        st.markdown(_dashboard_card("AI测试用例生成", str(st.session_state.req_count), f"就绪 {sum(1 for r in st.session_state.context.requirements if _get_conf(r.ingestion_metadata) >= 0.8)} · 主类 {top_category}", "blue"), unsafe_allow_html=True)
    with c3:
        st.markdown(_dashboard_card("执行统计", str(len(current_cases)), f"通过 {pass_count} · 失败 {fail_count}", "orange"), unsafe_allow_html=True)
    with c4:
        st.markdown(_dashboard_card("MCP / Skills", str(0), "MCP 0/0 · Skills 0/0", "purple"), unsafe_allow_html=True)

    p1, p2, p3 = st.columns([1.4, 0.9, 1.2])
    with p1:
        st.markdown('<div class="dashboard-panel"><div class="dashboard-panel-title">用例审核状态</div>', unsafe_allow_html=True)
        st.dataframe(summary_rows, use_container_width=True, hide_index=True)
        total_exec = max(len(current_cases), 1)
        st.progress(min(pass_count / total_exec, 1.0), text=f"已通过 {(pass_count / total_exec * 100):.0f}%")
        st.progress(min(fail_count / total_exec, 1.0), text=f"待处理 {(fail_count / total_exec * 100):.0f}%")
        st.markdown("</div>", unsafe_allow_html=True)
    with p2:
        st.markdown('<div class="dashboard-panel"><div class="dashboard-panel-title">执行通过率</div>', unsafe_allow_html=True)
        st.markdown(_rate_ring_html(pass_rate, pass_count, fail_count, nt_count), unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)
    with p3:
        st.markdown('<div class="dashboard-panel"><div class="dashboard-panel-title">Token 统计</div>', unsafe_allow_html=True)
        st.dataframe(token_rows, use_container_width=True, hide_index=True)
        st.markdown("</div>", unsafe_allow_html=True)

    d1, d2 = st.columns([1.5, 1])
    with d1:
        st.markdown('<div class="dashboard-panel"><div class="dashboard-panel-title">近7天执行趋势</div>', unsafe_allow_html=True)
        if not trend_df.empty:
            st.bar_chart(trend_df.set_index("日期"))
        else:
            st.info("暂无可展示的生成趋势数据。")
        st.markdown("</div>", unsafe_allow_html=True)
    with d2:
        st.markdown('<div class="dashboard-panel"><div class="dashboard-panel-title">质量特性分布 / 最近数据</div>', unsafe_allow_html=True)
        if not category_chart.empty:
            st.bar_chart(category_chart)
        detail_scope = st.radio("明细视图", ["需求", "用例"], horizontal=True, key="home_detail_scope")
        if detail_scope == "需求":
            st.dataframe(latest_req_rows, use_container_width=True, hide_index=True)
        else:
            st.dataframe(latest_case_rows, use_container_width=True, hide_index=True)
        st.markdown("</div>", unsafe_allow_html=True)


def render_tab_import() -> None:
    import pandas as pd

    _render_heading("导入需求", "upload")
    col_a, col_b = st.columns([2, 1])
    with col_a:
        uploaded_files = st.file_uploader(
            "选择文件",
            type=["docx", "xlsx", "txt", "json", "md"],
            help="支持 DOCX、XLSX、TXT、JSON、MD",
            accept_multiple_files=True,
        )
    with col_b:
        st.markdown("<br>", unsafe_allow_html=True)
        can_run = bool(uploaded_files)
        if st.button("解析文档", type="primary", use_container_width=True, disabled=not can_run):
            if not _ensure_frontend_services(force_retry=True):
                st.error("模型服务初始化失败，请检查本地 Ollama 与 deepseek-r1:7b 是否可用。")
                return
            temp_path = _PROJECT_ROOT / "temp_upload"
            temp_path.mkdir(exist_ok=True)
            all_reqs = []
            ingest_errors = []
            previous_requirements = list(st.session_state.context.requirements)
            previous_cases = list(st.session_state.context.test_cases)
            progress_text = st.empty()
            for i, uploaded_file in enumerate(uploaded_files):
                file_path = temp_path / uploaded_file.name
                with open(file_path, "wb") as f:
                    f.write(uploaded_file.getbuffer())
                progress_text.text(f"解析中 ({i+1}/{len(uploaded_files)}): {uploaded_file.name}")
                try:
                    reqs = st.session_state.services["ingestor"].ingest(str(file_path))
                    all_reqs.extend(reqs)
                except Exception as e:
                    ingest_errors.append((uploaded_file.name, str(e)))
                    st.error(f"{uploaded_file.name}: {e}")
            if all_reqs:
                _mark_requirements_batch(all_reqs, uploaded_files)
                change_report = analyze_requirement_changes(previous_requirements, all_reqs, previous_cases)
                all_reqs = apply_requirement_change_statuses(all_reqs, change_report)
                st.session_state.context.test_cases = apply_case_change_plan(previous_cases, change_report)
                st.session_state.requirement_change_report = change_report
                st.session_state.context.requirements = all_reqs
                st.session_state.req_count = len(all_reqs)
                save_context()
                candidate_count = _queue_requirement_candidates(all_reqs)
                if ingest_errors:
                    st.warning(
                        f"共 {len(uploaded_files)} 个文件：得到 {len(all_reqs)} 条需求；{len(ingest_errors)} 个文件失败。"
                    )
                else:
                    st.success(f"已解析 {len(uploaded_files)} 个文件，共 {len(all_reqs)} 条需求。")
                summary = change_report.get("summary", {})
                if any(summary.get(key, 0) for key in ("updated", "new", "removed", "impacted_cases", "reused_cases")):
                    st.info(
                        "版本比对："
                        f"新增 {summary.get('new', 0)} 条，"
                        f"变更 {summary.get('updated', 0)} 条，"
                        f"删除 {summary.get('removed', 0)} 条；"
                        f"沿用历史用例 {summary.get('reused_cases', 0)} 条，"
                        f"待更新/待废弃用例 {summary.get('impacted_cases', 0)} 条。"
                    )
                if candidate_count:
                    st.info(f"已从本次需求中自动提炼 {candidate_count} 条待确认图谱候选，可前往「知识图谱」确认入库。")
            elif ingest_errors:
                st.error("全部文件解析失败，需求列表未更改。")
            progress_text.empty()
        elif uploaded_files and not st.session_state.services.get("ingestor"):
            st.info("正在等待模型服务就绪，点击“解析文档”时会自动重试初始化。")

    if not st.session_state.context.requirements:
        return

    st.divider()
    _render_heading("需求列表", "clipboard")
    batch_meta = _current_batch_meta()
    if batch_meta.get("parsed_at"):
        st.caption(
            f"当前文档批次解析于：{_format_dt(batch_meta.get('parsed_at'))}"
            + (f" | 文件：{', '.join(batch_meta.get('files', []))}" if batch_meta.get("files") else "")
        )
    total = len(st.session_state.context.requirements)
    high_conf = sum(1 for r in st.session_state.context.requirements if _get_conf(r.ingestion_metadata) >= 0.8)
    low_conf = total - high_conf
    m1, m2, m3 = st.columns(3)
    m1.metric("总数", total)
    m2.metric("就绪 (≥0.8)", high_conf)
    m3.metric("待复核", low_conf)

    change_report = st.session_state.get("requirement_change_report") or {}
    change_summary = change_report.get("summary", {})
    if any(change_summary.get(key, 0) for key in ("updated", "new", "removed", "impacted_cases", "reused_cases")):
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("新增需求", change_summary.get("new", 0))
        c2.metric("变更需求", change_summary.get("updated", 0))
        c3.metric("删除需求", change_summary.get("removed", 0))
        c4.metric("沿用用例", change_summary.get("reused_cases", 0))
        c5.metric("待处理用例", change_summary.get("impacted_cases", 0))

    req_data = []
    for r in st.session_state.context.requirements:
        req_data.append(
            {
                "ID": r.id,
                "内容预览": (r.original_text[:80] + "…") if len(r.original_text) > 80 else r.original_text,
                "模块": _get_entity(r.extracted_entities, "module", "—"),
                "功能": _get_entity(r.extracted_entities, "feature", "—"),
                "变更": _requirement_change_status(r),
                "置信度": _get_conf(r.ingestion_metadata),
                "状态": "就绪" if _get_conf(r.ingestion_metadata) >= 0.8 else "待复核",
            }
        )
    st.dataframe(
        pd.DataFrame(req_data),
        column_config={
            "置信度": st.column_config.ProgressColumn(
                "置信度", format="%.2f", min_value=0, max_value=1
            ),
        },
        use_container_width=True,
        hide_index=True,
    )
    if low_conf > 0:
        st.warning(f"{low_conf} 条需求待复核，请到「评审与导出」处理。")

    with st.expander("解析调试面板", expanded=False):
        _render_heading("解析结果预览", "info")
        debug_rows = _req_debug_rows(st.session_state.context.requirements)
        st.dataframe(
            pd.DataFrame(debug_rows),
            column_config={
                "置信度": st.column_config.ProgressColumn(
                    "置信度", format="%.2f", min_value=0, max_value=1
                ),
            },
            use_container_width=True,
            hide_index=True,
            height=260,
        )

        debug_options = {
            r.id: f"[{r.id}] {_get_entity(r.extracted_entities, 'module', '—')} / {_get_entity(r.extracted_entities, 'feature', '—')}"
            for r in st.session_state.context.requirements
        }
        selected_debug_req_id = st.selectbox(
            "选择一条需求查看解析详情",
            options=list(debug_options.keys()),
            format_func=lambda x: debug_options[x],
            key="debug_req_select",
        )
        selected_debug_req = next((r for r in st.session_state.context.requirements if r.id == selected_debug_req_id), None)
        if selected_debug_req:
            spec = _get_req_spec(selected_debug_req)
            constraints = _constraints_list(selected_debug_req.extracted_entities)
            d1, d2, d3 = st.columns(3)
            d1.metric("解析类型", str(spec.get("type") or "—"))
            d2.metric("优先级", spec.get("priority") or "—")
            d3.metric("约束数", len(constraints))
            st.code(
                json.dumps(
                    {
                        "id": selected_debug_req.id,
                        "module": _get_entity(selected_debug_req.extracted_entities, "module", ""),
                        "feature": _get_entity(selected_debug_req.extracted_entities, "feature", ""),
                        "module_path": spec.get("module_path", ""),
                        "type": str(spec.get("type") or ""),
                        "priority": spec.get("priority", ""),
                        "constraints": constraints,
                        "confidence": _get_conf(selected_debug_req.ingestion_metadata),
                        "original_text": selected_debug_req.original_text[:1500],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                language="json",
            )


def render_tab_generate() -> None:
    _render_heading("生成用例", "layers")
    if not st.session_state.context.requirements:
        st.warning("请先完成「导入需求」。")
        return

    all_ready_reqs = [
        r for r in st.session_state.context.requirements if _get_conf(r.ingestion_metadata) >= 0.8
    ]
    change_report = st.session_state.get("requirement_change_report") or {}
    changed_req_ids = set(change_report.get("changed_requirement_ids", []))
    if changed_req_ids:
        scope = st.radio(
            "生成范围",
            ["仅新增/变更需求", "全部就绪需求"],
            horizontal=True,
            help="默认只为新增或已变更的需求重生成，减少不必要的全量覆盖。",
        )
        ready_reqs = [
            r for r in all_ready_reqs
            if scope == "全部就绪需求" or r.id in changed_req_ids
        ]
    else:
        ready_reqs = all_ready_reqs
    st.markdown(
        f"就绪需求 **{len(ready_reqs)}** 条（置信度 ≥ 0.8）。批量生成采用功能 / 安全 / 性能等策略，并结合知识图谱约束。"
    )
    col_info, col_go = st.columns([4, 1])
    with col_go:
        start = st.button("开始生成", type="primary", use_container_width=True, disabled=len(ready_reqs) == 0)
    with col_info:
        if len(ready_reqs) == 0:
            st.info("无就绪需求。请在「评审与导出」中编辑并标记就绪。")

    if not start:
        return

    if not st.session_state.services.get("ingestor"):
        _ensure_frontend_services(force_retry=True)

    backend_online, backend_err = _check_backend()

    if not backend_online:
        extra = f" ({backend_err})" if backend_err else ""
        st.error(f"后端服务未启动 (URL: {_BACKEND_URL}){extra}。")
        return

    progress_bar = st.progress(0)
    status_text = st.empty()
    
    st.markdown("### 🛰️ 批量执行监控")
    monitor_container = st.container(border=True)
    with monitor_container:
        # Header for the monitor
        h1, h2, h3 = st.columns([1, 2, 3])
        h1.markdown("**ID**")
        h2.markdown("**当前节点**")
        h3.markdown("**状态描述**")
        
        # Create placeholders for each requirement
        req_placeholders = {}
        for r in ready_reqs:
            row = st.columns([1, 2, 3])
            req_placeholders[r.id] = {
                "node": row[1].empty(),
                "status": row[2].empty()
            }
            row[0].write(f"`{r.id[:8]}`")
            req_placeholders[r.id]["node"].info("等待中...")
            req_placeholders[r.id]["status"].write("—")

    trace_expander = st.expander("实时思考轨迹 (Recent Traces)", expanded=True)
    trace_container = trace_expander.empty()

    with st.spinner("正在通过后端工作流生成用例…"):
        try:
            # Consume the stream
            final_results = []
            traces = []
            done_req_ids = set()
            token_counted = set()
            stream_errors = []
            
            async def run_gen():
                async for update in call_api_generate_stream(ready_reqs):
                    if update["type"] == "progress":
                        node = update.get("node", "System")
                        st_text = update.get("status", "运行中")
                        req_id = update.get("req_id")
                        tokens = update.get("tokens", 0)
                        iteration = update.get("iteration", 0)
                        kg_hit = update.get("kg_hit", False)
                        
                        # Update Monitor Row
                        if req_id in req_placeholders:
                            p = req_placeholders[req_id]
                            # Use different colors for different nodes
                            node_color = "blue"
                            if node == "retrieve_context": node_color = "orange"
                            elif node == "generate_initial": node_color = "green"
                            elif node == "validate_and_augment": node_color = "violet"
                            elif node == "optimize": node_color = "red"
                            
                            p["node"].markdown(f":{node_color}[{node}]")
                            p["status"].write(st_text)
                            
                            if st_text == "已完成":
                                p["node"].markdown("OK :green[完成]")
                                if req_id:
                                    done_req_ids.add(req_id)
                        
                        # Update Global Metrics
                        if node == "retrieve_context":
                            st.session_state.context.kg_hit_count += 1 if kg_hit else 0
                        if req_id and tokens:
                            key = (req_id, node, iteration)
                            if key not in token_counted:
                                token_counted.add(key)
                                st.session_state.context.total_tokens += int(tokens or 0)
                        
                        # Update Requirement in memory for audit trace
                        if req_id:
                            target_req = next((r for r in st.session_state.context.requirements if r.id == req_id), None)
                            if target_req:
                                if not hasattr(target_req, "generation_trace") or target_req.generation_trace is None:
                                    target_req.generation_trace = []
                                
                                node_trace = update.get("trace", [])
                                for t in node_trace:
                                    msg = f"[{node}] {t}"
                                    if not target_req.generation_trace or target_req.generation_trace[-1] != msg:
                                        target_req.generation_trace.append(msg)
                                        if len(target_req.generation_trace) > 200:
                                            target_req.generation_trace = target_req.generation_trace[-200:]
                                    # Also add to global trace for current view
                                    traces.append(f"**[{req_id[:8]}]** {t}")
                        
                        current = len(done_req_ids)
                        total = len(ready_reqs)
                        pct = int((current / total) * 100) if total > 0 else 0
                        progress_bar.progress(pct)
                        status_text.text(f"总进度: {current}/{total} | Tokens: {st.session_state.context.total_tokens}")
                        
                        # Show last 10 global traces
                        trace_container.markdown("\n\n".join(traces[-10:]))
                        
                    elif update["type"] == "result":
                        for tc_raw in update["data"]:
                            final_results.append(TestCase.model_validate(tc_raw))
                    elif update["type"] == "error":
                        err_line = f"需求 {update.get('req_id')} 处理出错: {update.get('message')}"
                        stream_errors.append(err_line)
                        st.error(err_line)
                        detail = update.get("detail")
                        if detail:
                            traces.append(f"**[{update.get('req_id', 'unknown')[:8]}]** ERROR\n```text\n{detail[:2000]}\n```")
            
            start_time = time.time()
            asyncio.run(run_gen())
            end_time = time.time()
            
            # Finalize metrics
            st.session_state.context.total_generation_time += (end_time - start_time)
            st.session_state.context.total_requests += len(ready_reqs)
            save_context()
            
            cases = _stamp_generated_cases(final_results)
            if stream_errors and not cases:
                raise RuntimeError(" ; ".join(stream_errors[:3]))
        except Exception as gen_err:
            progress_bar.progress(0)
            status_text.text("已中断。")
            err_text = str(gen_err) or repr(gen_err)
            st.error(f"生成失败: {err_text}")
            return

    status_text.text("合并与去重…")
    progress_bar.progress(100)
    ready_ids = {r.id for r in ready_reqs}
    kept = [tc for tc in st.session_state.context.test_cases if tc.related_req_id not in ready_ids]
    st.session_state.context.test_cases = _dedupe_project_cases(kept + cases)
    st.session_state.case_count = len(st.session_state.context.test_cases)
    save_context()
    current_cases = _current_batch_cases()
    status_text.text(f"本次新增 {len(cases)} 条；当前文档可见 {len(current_cases)} 条；项目累计 {len(st.session_state.context.test_cases)} 条。")
    st.success(
        f"完成：本次 {len(ready_reqs)} 条需求 → {len(cases)} 条用例；生成时间 {_format_dt(st.session_state.current_generation_time)}；当前文档显示 {len(current_cases)} 条。"
    )


def render_tab_review_export() -> None:
    _apply_pending_widget_state_updates()
    _flush_pending_widget_state_notice()
    _render_heading("评审与导出", "package")
    if not st.session_state.context.requirements:
        st.info("请先在「导入需求」中解析文档。")
        return

    if "focus_case_id" not in st.session_state:
        st.session_state.focus_case_id = None
    if "selected_case_ids" not in st.session_state:
        st.session_state.selected_case_ids = []
    if "refine_running_case_id" not in st.session_state:
        st.session_state.refine_running_case_id = None

    col_filter, _ = st.columns([2, 1])
    with col_filter:
        only_low = st.checkbox("仅显示待复核（置信度 < 0.8）", value=True, key="review_only_low")

    if only_low:
        filtered_reqs = [
            r for r in st.session_state.context.requirements if _get_conf(r.ingestion_metadata) < 0.8
        ]
    else:
        filtered_reqs = list(st.session_state.context.requirements)

    if filtered_reqs:
        st.markdown("### 批量人工审批")
        batch_options = {
            r.id: f"[{r.id}] {(r.original_text[:50] + '…') if len(r.original_text) > 50 else r.original_text}"
            for r in filtered_reqs
        }
        batch_selected_ids = st.multiselect(
            "选择要标记为就绪的需求",
            options=list(batch_options.keys()),
            format_func=lambda x: batch_options[x],
        )
        if st.button(
            "批量标记就绪",
            type="primary",
            use_container_width=True,
            disabled=len(batch_selected_ids) == 0,
        ):
            for rid in batch_selected_ids:
                req = next((r for r in st.session_state.context.requirements if r.id == rid), None)
                if not req:
                    continue
                if req.ingestion_metadata is None:
                    req.ingestion_metadata = {"parsing_confidence": 1.0}
                else:
                    _set_conf(req.ingestion_metadata, 1.0)
            save_context()
            st.toast(f"已批量标记就绪：{len(batch_selected_ids)} 条")
            st.rerun()

    if only_low and not filtered_reqs:
        st.success("当前没有待复核需求，所有条目置信度均 ≥ 0.8。")
        st.caption("取消勾选上方选项可浏览全部需求。")
    elif not filtered_reqs:
        st.caption("无需求可显示。")
    else:
        req_options = {r.id: f"[{r.id}] {(r.original_text[:50] + '…') if len(r.original_text) > 50 else r.original_text}" for r in filtered_reqs}
        if "review_selected_req_id" not in st.session_state or st.session_state.review_selected_req_id not in req_options:
            st.session_state.review_selected_req_id = list(req_options.keys())[0]

        selected_req_id = st.selectbox(
            "选择需求",
            options=list(req_options.keys()),
            format_func=lambda x: req_options[x],
            key="review_selected_req_id",
        )
        selected_req = next((r for r in st.session_state.context.requirements if r.id == selected_req_id), None)
        current_batch_requirements = list(st.session_state.context.requirements)
        current_batch_cases = _current_batch_cases()

        st.divider()
        _render_heading("AI 质量评估", "info")
        eval_col1, eval_col2 = st.columns(2)
        with eval_col1:
            can_eval_batch = bool(current_batch_cases and current_batch_requirements)
            if st.button("评估当前批次", key="eval_current_batch", use_container_width=True, disabled=not can_eval_batch):
                with st.spinner("正在评估当前批次用例质量..."):
                    try:
                        report = asyncio.run(
                            evaluate_requirements(
                                current_batch_requirements,
                                build_case_map(current_batch_cases),
                            )
                        )
                        st.session_state.ai_eval_report = report
                        st.session_state.ai_eval_scope = "当前批次"
                        st.success("当前批次评估完成。")
                    except Exception as e:
                        st.error(f"评估失败: {e}")
        with eval_col2:
            selected_cases_for_eval = st.session_state.case_map.get(selected_req_id, [])
            can_eval_selected = bool(selected_req and selected_cases_for_eval)
            if st.button("评估当前需求", key="eval_selected_req", use_container_width=True, disabled=not can_eval_selected):
                with st.spinner("正在评估当前需求用例质量..."):
                    try:
                        report = asyncio.run(
                            evaluate_requirements(
                                [selected_req],
                                build_case_map(selected_cases_for_eval),
                            )
                        )
                        st.session_state.ai_eval_report = report
                        st.session_state.ai_eval_scope = f"需求 {selected_req_id}"
                        st.success(f"{selected_req_id} 评估完成。")
                    except Exception as e:
                        st.error(f"评估失败: {e}")
        if st.session_state.get("ai_eval_report"):
            st.caption(f"当前展示：{st.session_state.get('ai_eval_scope') or '最近一次评估'}")
            _render_ai_eval_report(st.session_state.ai_eval_report)

        col_req, col_cases = st.columns(2, gap="medium")

        with col_req:
            _render_heading("需求编辑", "clipboard")
            if selected_req:
                with st.container(border=True):
                    st.caption(f"ID: {selected_req.id} | 置信度: {_get_conf(selected_req.ingestion_metadata):.2f}")
                    new_module = st.text_input(
                        "模块",
                        value=_get_entity(selected_req.extracted_entities, "module"),
                        key=f"mod_{selected_req.id}",
                    )
                    new_feature = st.text_input(
                        "功能",
                        value=_get_entity(selected_req.extracted_entities, "feature"),
                        key=f"feat_{selected_req.id}",
                    )
                    new_text = st.text_area(
                        "需求原文",
                        value=selected_req.original_text,
                        height=200,
                        key=f"txt_{selected_req.id}",
                    )
                    b1, b2 = st.columns(2)
                    if b1.button("保存修改", key=f"save_{selected_req.id}", use_container_width=True):
                        old_module = _get_entity(selected_req.extracted_entities, "module")
                        old_feature = _get_entity(selected_req.extracted_entities, "feature")
                        old_text = selected_req.original_text
                        _set_entity(selected_req.extracted_entities, "module", new_module)
                        _set_entity(selected_req.extracted_entities, "feature", new_feature)
                        selected_req.original_text = new_text
                        save_context()
                        queued = 0
                        if (old_module != new_module) or (old_feature != new_feature) or (_normalize_text(old_text) != _normalize_text(new_text)):
                            queued = _queue_requirement_delta_candidates(
                                selected_req, old_module, old_feature, old_text, new_module, new_feature, new_text
                            )
                            if queued == 0:
                                queued = _queue_requirement_candidates([selected_req])
                        st.toast("已保存")
                        if queued:
                            st.info(f"已从本次人工修订中新增 {queued} 条图谱候选。")
                        st.rerun()
                    if b2.button("保存并标记就绪", key=f"approve_{selected_req.id}", type="primary", use_container_width=True):
                        old_module = _get_entity(selected_req.extracted_entities, "module")
                        old_feature = _get_entity(selected_req.extracted_entities, "feature")
                        old_text = selected_req.original_text
                        _set_entity(selected_req.extracted_entities, "module", new_module)
                        _set_entity(selected_req.extracted_entities, "feature", new_feature)
                        selected_req.original_text = new_text
                        _set_conf(selected_req.ingestion_metadata, 1.0)
                        save_context()
                        if (old_module != new_module) or (old_feature != new_feature) or (_normalize_text(old_text) != _normalize_text(new_text)):
                            queued = _queue_requirement_delta_candidates(
                                selected_req, old_module, old_feature, old_text, new_module, new_feature, new_text
                            )
                            if queued == 0:
                                queued = _queue_requirement_candidates([selected_req])
                            if queued:
                                st.info(f"已从本次人工修订中新增 {queued} 条图谱候选。")
                        st.toast("已标记就绪")
                        st.rerun()
                
                # --- Generation Audit Trace (Beta 2.0) ---
                if hasattr(selected_req, "generation_trace") and selected_req.generation_trace:
                    with st.expander("🕵️ 生成审计轨迹 (Generation Trace)", expanded=False):
                        for t in selected_req.generation_trace:
                            st.markdown(f"- {t}")
                elif selected_req_id in st.session_state.case_map:
                    st.info("该需求已生成用例，但审计轨迹在旧版本中未记录。")
                
                # --- Batch Learn from Feedback History (Beta 2.0) ---
                linked_cases = st.session_state.case_map.get(selected_req_id, [])
                all_history = []
                for tc in linked_cases:
                    all_history.extend(tc.feedback_history)
                
                if all_history:
                    st.divider()
                    st.markdown(f"**反馈沉淀** (累计 {len(all_history)} 条修正记录)")
                    if st.button("从历史修正中提取规则", key=f"learn_history_{selected_req_id}", use_container_width=True):
                        module = _get_entity(selected_req.extracted_entities, "module", "Unknown")
                        with st.spinner("正在从历史修正中精炼业务规则..."):
                            try:
                                res = asyncio.run(call_api_kg_learn_history(module, all_history))
                                if res.get("success"):
                                    st.success(f"成功从历史中提取并学习了 {res.get('count')} 条新规则。")
                                    with st.expander("查看新提取的规则"):
                                        for r in res.get("extracted_rules", []): st.write(f"- {r}")
                                else:
                                    st.warning("未能从当前历史中提取到有效新规则。")
                            except Exception as e:
                                st.error(f"提取失败: {e}")

        with col_cases:
            _render_heading("关联用例", "list-check")
            if not st.session_state.context.test_cases:
                st.warning("尚无测试用例，请使用「生成用例」批量生成。")
            else:
                linked = st.session_state.case_map.get(selected_req_id, [])
                if not linked:
                    st.info("该需求下暂无自动用例。")
                    if st.button("仅为此需求生成", key=f"gen_single_{selected_req_id}", use_container_width=True):
                        backend_online, backend_err = _check_backend()

                        if not backend_online:
                            extra = f" ({backend_err})" if backend_err else ""
                            st.error(f"后端服务未启动 (URL: {_BACKEND_URL}){extra}。")
                        else:
                            with st.spinner("正在调用后端 API…"):
                                try:
                                    final_results = []
                                    async def run_single_gen():
                                        async for update in call_api_generate_stream([selected_req]):
                                            if update["type"] == "result":
                                                for tc_raw in update["data"]:
                                                    final_results.append(TestCase.model_validate(tc_raw))
                                    asyncio.run(run_single_gen())
                                    new_cases = _stamp_generated_cases(final_results)
                                except Exception as gen_err:
                                    st.error(f"失败: {gen_err}")
                                else:
                                    if new_cases:
                                        rid = selected_req.id
                                        st.session_state.context.test_cases = [
                                            tc for tc in st.session_state.context.test_cases if tc.related_req_id != rid
                                        ]
                                        st.session_state.context.test_cases.extend(new_cases)
                                        st.session_state.case_map.setdefault(rid, []).extend(new_cases)
                                        st.session_state.case_count = len(st.session_state.context.test_cases)
                                        save_context()
                                        st.success(f"已生成 {len(new_cases)} 条。")
                                        st.rerun()
                                    else:
                                        st.warning("未返回用例，请检查模型或需求内容。")
                else:
                    st.caption(f"共 {len(linked)} 条")
                    for tc in linked:
                        expanded = bool(st.session_state.focus_case_id and st.session_state.focus_case_id == tc.test_case_id)
                        with st.expander(f"{tc.priority} | {tc.title}", expanded=expanded):
                            ti = tc.get_test_instruction()
                            impact_badge = _impact_badge(tc)
                            if impact_badge:
                                st.warning(f"版本影响：{impact_badge}")
                            if tc.methodology:
                                st.caption("策略: " + ", ".join(tc.methodology))

                            key_title = f"title_{tc.test_case_id}"
                            if key_title not in st.session_state:
                                st.session_state[key_title] = tc.title
                            new_title = st.text_input("标题", key=key_title)

                            key_steps = f"steps_{tc.test_case_id}"
                            if key_steps not in st.session_state:
                                st.session_state[key_steps] = _steps_to_text(ti.steps)
                            new_steps = st.text_area(
                                "步骤（每行一条）",
                                height=100,
                                key=key_steps,
                            )

                            parsed_steps = _parse_steps_text(new_steps)
                            if parsed_steps:
                                st.caption("步骤预览")
                                st.markdown("\n".join(_number_steps(parsed_steps)))

                            new_expected = st.text_area(
                                "预期",
                                value=ti.expected_result,
                                height=70,
                                key=f"exp_{tc.test_case_id}",
                            )

                            feedback = st.text_area(
                                "修正意见",
                                placeholder="例如：增加对 SQL 注入的校验，补充失败提示文案…",
                                height=70,
                                key=f"feed_{tc.test_case_id}",
                            )

                            c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
                            if c1.button("保存用例", key=f"btn_{tc.test_case_id}", use_container_width=True):
                                old_steps_text = _steps_to_text(ti.steps)
                                old_expected = ti.expected_result or ""
                                tc.title = (new_title or "").strip()
                                ti.steps = _number_steps(_parse_steps_text(new_steps))
                                ti.expected_result = (new_expected or "").strip()
                                tc.test_instruction = ti.model_dump(mode="json")
                                module = _get_entity(selected_req.extracted_entities, "module", "Unknown")
                                queued = 0
                                if (feedback or "").strip():
                                    candidate_type = "FailureMode" if any(flag in feedback for flag in ["异常", "报错", "失效", "Bug", "bug"]) else "Rule"
                                    queued += 1 if _queue_kg_candidate(
                                        module,
                                        candidate_type,
                                        feedback,
                                        {"source": "manual_review", "case_id": tc.test_case_id, "title": tc.title},
                                    ) else 0
                                elif (_normalize_text(old_steps_text) != _normalize_text(new_steps)) or (_normalize_text(old_expected) != _normalize_text(new_expected)):
                                    queued += 1 if _queue_kg_candidate(
                                        module,
                                        "Rule",
                                        f"{tc.title or '人工评审'}：{(new_expected or '').strip()}",
                                        {"source": "manual_review", "case_id": tc.test_case_id},
                                    ) else 0
                                save_context()
                                st.toast(f"已更新 {tc.test_case_id}")
                                if queued:
                                    st.info(f"已新增 {queued} 条待确认图谱候选。")

                            if c2.button("规范化步骤", key=f"fmt_{tc.test_case_id}", use_container_width=True):
                                st.session_state[key_steps] = "\n".join(_number_steps(_parse_steps_text(new_steps)))
                                st.toast("已规范化")
                                st.rerun()

                            refine_disabled = bool(st.session_state.refine_running_case_id and st.session_state.refine_running_case_id != tc.test_case_id)
                            if c3.button(
                                "AI 修正",
                                key=f"refine_{tc.test_case_id}",
                                use_container_width=True,
                                disabled=refine_disabled,
                                help="基于‘修正意见’重构此用例",
                            ):
                                if not (feedback or "").strip():
                                    st.info("请先输入修正意见。")
                                else:
                                    st.session_state.refine_running_case_id = tc.test_case_id
                                    refine_trace_container = st.empty()
                                    with st.spinner("AI 正在修正..."):
                                        try:
                                            final_refined = []
                                            async def run_refine_gen():
                                                async for update in call_api_refine_stream([tc], feedback):
                                                    status = update.get("status", "")
                                                    trace = update.get("trace", [])
                                                    if trace:
                                                        refine_trace_container.info(f"**{status}**: {trace[-1]}")
                                                    if update.get("final_cases"):
                                                        for r_tc in update["final_cases"]:
                                                            final_refined.append(TestCase.model_validate(r_tc))
                                            asyncio.run(run_refine_gen())

                                            if final_refined:
                                                new_tc = final_refined[0]
                                                new_tc.system_env = dict(_meta_to_dict(tc.system_env))
                                                new_tc.system_env["refined_at"] = datetime.now().isoformat(timespec="seconds")
                                                new_tc.feedback_history = tc.feedback_history + [{
                                                    "timestamp": str(datetime.now()),
                                                    "feedback": feedback,
                                                    "original_title": tc.title
                                                }]
                                                for idx, item in enumerate(st.session_state.context.test_cases):
                                                    if item.test_case_id == tc.test_case_id:
                                                        st.session_state.context.test_cases[idx] = new_tc
                                                        break
                                                module = _get_entity(selected_req.extracted_entities, "module", "Unknown")
                                                candidate_type = "FailureMode" if any(flag in feedback for flag in ["异常", "报错", "失效", "Bug", "bug"]) else "Rule"
                                                _queue_kg_candidate(
                                                    module,
                                                    candidate_type,
                                                    feedback,
                                                    {"source": "ai_refine_feedback", "case_id": tc.test_case_id, "title": tc.title},
                                                )
                                                save_context()
                                                st.success("修正成功！")
                                                st.rerun()
                                            else:
                                                st.warning("未返回修正结果，请稍后重试。")
                                        except Exception as e:
                                            st.error(f"修正失败: {e}")
                                        finally:
                                            st.session_state.refine_running_case_id = None

                            if c4.button("学习到图谱", key=f"learn_{tc.test_case_id}", use_container_width=True, help="将此用例中的逻辑永久记录到知识图谱"):
                                 module = _get_entity(selected_req.extracted_entities, "module", "Unknown")
                                 with st.spinner("正在提取结构化规则..."):
                                     try:
                                         success, rules = asyncio.run(call_api_kg_learn(module, tc=tc))
                                         if success:
                                             st.success(f"已学习 {len(rules)} 条规则到 '{module}'")
                                             if rules:
                                                 with st.expander("查看提取的规则"):
                                                     for r in rules: st.write(f"- {r}")
                                         else:
                                             st.error("学习失败")
                                     except Exception as e:
                                         st.error(f"调用失败: {e}")

                            st.caption("危险操作")
                            if st.button(
                                f"删除当前用例: {tc.test_case_id}",
                                key=f"del_{tc.test_case_id}",
                                use_container_width=True,
                                type="secondary",
                            ):
                                if (feedback or "").strip():
                                    module = _get_entity(selected_req.extracted_entities, "module", "Unknown")
                                    candidate_type = "FailureMode" if any(flag in feedback for flag in ["异常", "报错", "失效", "Bug", "bug"]) else "Rule"
                                    _queue_kg_candidate(
                                        module,
                                        candidate_type,
                                        feedback,
                                        {"source": "case_delete_feedback", "case_id": tc.test_case_id, "title": tc.title},
                                    )
                                st.session_state.context.test_cases = [
                                    item for item in st.session_state.context.test_cases if item.test_case_id != tc.test_case_id
                                ]
                                st.session_state.selected_case_ids = [
                                    cid for cid in st.session_state.selected_case_ids if cid != tc.test_case_id
                                ]
                                if st.session_state.focus_case_id == tc.test_case_id:
                                    st.session_state.focus_case_id = None
                                for key in (
                                    f"title_{tc.test_case_id}",
                                    f"steps_{tc.test_case_id}",
                                    f"exp_{tc.test_case_id}",
                                    f"feed_{tc.test_case_id}",
                                ):
                                    if key in st.session_state:
                                        del st.session_state[key]
                                save_context()
                                st.toast(f"已删除 {tc.test_case_id}")
                                st.rerun()

    st.divider()
    _render_heading("导出前预览", "eye")
    all_cases = st.session_state.context.test_cases
    cases = _current_batch_cases()
    deduped_cases = _dedupe_project_cases(cases)
    _backfill_case_project_names(all_cases, st.session_state.context.requirements)
    dup_count = max(0, len(cases) - len(deduped_cases))
    batch_meta = _current_batch_meta()
    req_meta = {
        r.id: {
            "project_name": _get_requirement_project_name(r) or "—",
            "module": _get_entity(r.extracted_entities, "module", "—"),
            "feature": _get_entity(r.extracted_entities, "feature", "—"),
        }
        for r in st.session_state.context.requirements
    }
    p1, p2 = st.columns([2, 1])
    with p1:
        last_gen_time = st.session_state.current_generation_time or max(
            (_meta_to_dict(tc.system_env).get("generated_at", "") for tc in cases),
            default="",
        )
        st.caption(
            f"当前文档用例 {len(cases)} 条；去重后 {len(deduped_cases)} 条；最近生成时间：{_format_dt(last_gen_time)}"
        )
        if batch_meta.get("files"):
            st.caption("当前批次文件：" + ", ".join(batch_meta.get("files", [])))
    with p2:
        if st.button("应用当前批次去重", use_container_width=True, disabled=dup_count == 0):
            current_req_ids = _current_requirement_ids()
            kept_history = [tc for tc in all_cases if tc.related_req_id not in current_req_ids]
            st.session_state.context.test_cases = kept_history + deduped_cases
            save_context()
            st.success(f"已移除 {dup_count} 条重复用例。")
            st.rerun()
    if cases:
        preview_scope = st.radio("预览范围", ["本次全部", "本次去重后", "项目全部"], horizontal=True)
        preview_cases = all_cases if preview_scope == "项目全部" else (deduped_cases if preview_scope == "本次去重后" else cases)

        priorities = sorted({tc.priority for tc in preview_cases if tc.priority})
        types = sorted({tc.dimension for tc in preview_cases if tc.dimension})
        f1, f2, f3 = st.columns([2, 1, 1])
        with f1:
            q = st.text_input("搜索（标题/预期/需求ID）", key="preview_search")
        with f2:
            p_filter = st.multiselect("优先级", options=priorities, default=priorities, key="preview_priority")
        with f3:
            t_filter = st.multiselect("类型", options=types, default=types, key="preview_type")

        q_norm = (q or "").strip().lower()
        filtered_preview_cases: List[TestCase] = []
        for tc in preview_cases:
            ti = tc.get_test_instruction()
            if p_filter and tc.priority not in p_filter:
                continue
            if t_filter and tc.dimension not in t_filter:
                continue
            if q_norm:
                hay = " ".join([
                    str(tc.test_case_id),
                    str(tc.related_req_id),
                    str(tc.title or ""),
                    str(ti.expected_result or ""),
                ]).lower()
                if q_norm not in hay:
                    continue
            filtered_preview_cases.append(tc)

        row_data = _preview_rows(filtered_preview_cases, req_meta=req_meta)
        if row_data:
            df = pd.DataFrame(row_data)
            selected_ids = set(st.session_state.selected_case_ids or [])
            df.insert(0, "选中", df["Case ID"].apply(lambda x: x in selected_ids))
            edited = st.data_editor(
                df,
                use_container_width=True,
                hide_index=True,
                height=320,
                column_config={
                    "选中": st.column_config.CheckboxColumn(required=False),
                },
                disabled=[c for c in df.columns if c != "选中"],
            )
            st.session_state.selected_case_ids = edited.loc[edited["选中"] == True, "Case ID"].tolist()
        else:
            st.caption("当前筛选条件下无可预览用例。")
    else:
        st.caption("暂无可预览用例。")

    st.divider()
    _render_heading("导出与集成", "download")
    st.caption("默认仅导出当前文档批次；如需历史数据，可切换导出范围。")
    export_scope = st.radio(
        "导出范围",
        ["仅预览勾选", "仅当前批次去重后", "仅当前批次全部", "项目全部用例"],
        horizontal=True,
        key="export_scope",
    )

    by_id = {tc.test_case_id: tc for tc in st.session_state.context.test_cases}
    if export_scope == "仅预览勾选":
        export_cases = [by_id[cid] for cid in (st.session_state.selected_case_ids or []) if cid in by_id]
    elif export_scope == "项目全部用例":
        export_cases = all_cases
    elif export_scope == "仅当前批次全部":
        export_cases = cases
    else:
        export_cases = deduped_cases

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    scope_tag_map = {
        "仅预览勾选": "selected",
        "仅当前批次去重后": "current_dedup",
        "仅当前批次全部": "current_all",
        "项目全部用例": "project_all",
    }
    scope_tag = scope_tag_map.get(export_scope, "current_dedup")
    e1, e2, e3, e4, e5 = st.columns(5)
    cases = export_cases
    with e1:
        if cases:
            Exporter = get_test_case_exporter()
            st.download_button(
                "Excel（标准）",
                data=Exporter(cases).to_excel(),
                file_name=f"test_cases_{scope_tag}_{ts}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
        else:
            st.button("Excel（标准）", disabled=True, use_container_width=True)
    with e2:
        if cases:
            Exporter = get_test_case_exporter()
            st.download_button(
                "Excel（飞书列）",
                data=Exporter(cases).to_feishu_excel(),
                file_name=f"feishu_cases_{scope_tag}_{ts}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
        else:
            st.button("Excel（飞书列）", disabled=True, use_container_width=True)
    with e3:
        if cases:
            PM = get_postman_exporter()
            st.download_button(
                "Postman 集合",
                data=PM(cases).to_collection(),
                file_name=f"postman_collection_{scope_tag}_{ts}.json",
                mime="application/json",
                use_container_width=True,
            )
        else:
            st.button("Postman 集合", disabled=True, use_container_width=True)
    with e4:
        if cases:
            Exporter = get_test_case_exporter()
            st.download_button(
                "Pytest 脚本",
                data=Exporter(cases).to_pytest(),
                file_name=f"test_suite_{scope_tag}_{ts}.py",
                mime="text/x-python",
                use_container_width=True,
            )
        else:
            st.button("Pytest 脚本", disabled=True, use_container_width=True)
    with e5:
        if st.button("推送到飞书Sheet", use_container_width=True, disabled=not cases):
            Exporter = get_test_case_exporter()
            exporter = Exporter(
                cases,
                requirement_link_base_url=st.session_state.get("feishu_requirement_link_base_url", ""),
            )
            client = get_feishu_client(
                app_id=st.session_state.get("feishu_app_id", ""),
                app_secret=st.session_state.get("feishu_app_secret", ""),
                spreadsheet_token=st.session_state.get("feishu_spreadsheet_token", ""),
                sheet_id=st.session_state.get("feishu_sheet_id", ""),
                tenant_access_token=st.session_state.get("feishu_tenant_token", ""),
                base_url=st.session_state.get("feishu_open_base_url", "https://open.feishu.cn"),
            )
            spreadsheet_token = st.session_state.get("feishu_spreadsheet_token", "")
            sheet_id = st.session_state.get("feishu_sheet_id", "")
            if not spreadsheet_token and st.session_state.get("feishu_auto_create_sheet", True):
                created = client.create_spreadsheet(
                    st.session_state.get("feishu_sheet_title", f"AI测试用例_{ts}"),
                    folder_token=st.session_state.get("feishu_sheet_folder_token", ""),
                )
                if created and created.get("spreadsheet_token"):
                    spreadsheet_token = created.get("spreadsheet_token", "")
                    sheet_id = created.get("sheet_id", "")
                    sheet_url = _build_feishu_sheet_url(spreadsheet_token, sheet_id)
                    updates = {
                        "feishu_spreadsheet_token": spreadsheet_token,
                        "feishu_sheet_id": sheet_id,
                        "feishu_last_sheet_url": sheet_url,
                    }
                    _queue_widget_state_updates(updates, f"已自动创建新 Sheet：{spreadsheet_token}")
                    st.rerun()
                else:
                    st.warning("自动创建新 Sheet 失败，请检查权限或文件夹配置。")
            if not spreadsheet_token:
                st.warning("请先填写 Spreadsheet Token，或启用自动创建新 Sheet。")
            else:
                values = exporter.to_sheet_values()
                actual_headers = client.get_sheet_headers(
                    spreadsheet_token=spreadsheet_token,
                    sheet_id=sheet_id or st.session_state.get("feishu_sheet_id", ""),
                )
                expected_headers = exporter.local_sheet_headers()
                compare = client.compare_headers(expected_headers, actual_headers) if actual_headers else {"missing": [], "extra": [], "matched": []}
                if actual_headers:
                    if compare["missing"] or compare["extra"]:
                        st.warning(
                            "Sheet 表头检查：缺少列 "
                            + ("、".join(compare["missing"]) if compare["missing"] else "无")
                            + "；额外列 "
                            + ("、".join(compare["extra"]) if compare["extra"] else "无")
                        )
                    else:
                        st.info("Sheet 表头校验通过。")
                else:
                    st.info("Sheet 首行为空，将直接写入新的表头和数据。")
                target_sheet_id = sheet_id or st.session_state.get("feishu_sheet_id", "")
                pushed = client.push_sheet_values(
                    values,
                    spreadsheet_token=spreadsheet_token,
                    sheet_id=target_sheet_id,
                    start_cell=st.session_state.get("feishu_sheet_start_cell", "A1"),
                )
                if not pushed:
                    detected_sheet_id = client.detect_sheet_id(spreadsheet_token)
                    if detected_sheet_id and detected_sheet_id != target_sheet_id:
                        target_sheet_id = detected_sheet_id
                        pushed = client.push_sheet_values(
                            values,
                            spreadsheet_token=spreadsheet_token,
                            sheet_id=target_sheet_id,
                            start_cell=st.session_state.get("feishu_sheet_start_cell", "A1"),
                        )
                        if pushed:
                            st.session_state["feishu_sheet_id"] = target_sheet_id
                if pushed:
                    sheet_url = _build_feishu_sheet_url(spreadsheet_token, target_sheet_id)
                    st.session_state["feishu_last_sheet_url"] = sheet_url
                    _save_sheet_export_settings()
                    st.success(f"已成功推送 {max(len(values) - 1, 0)} 条记录到飞书 Sheet。")
                else:
                    detail = client.last_error or "请检查电子表格权限、Spreadsheet Token、Sheet ID 和写入范围。"
                    st.warning(f"推送失败：{detail}")

    with st.expander("飞书配置", expanded=False):
        if not st.session_state.get("feishu_sheet_title"):
            st.session_state["feishu_sheet_title"] = f"AI测试用例_{ts}"
        if not st.session_state.get("feishu_sheet_start_cell"):
            st.session_state["feishu_sheet_start_cell"] = "A1"
        st.caption("仅保留飞书 Sheet 导出；支持自建应用 `App ID + App Secret` 自动换取 tenant_access_token。")
        shared_url = st.text_input("飞书 Sheet 分享链接（可选）", key="feishu_shared_url", help="支持粘贴 Sheet 链接，用于自动提取 Spreadsheet Token 和 Sheet ID")
        u1, u2 = st.columns(2)
        if u1.button("从链接提取 Token", use_container_width=True, disabled=not (shared_url or "").strip()):
            parsed = _extract_feishu_tokens(shared_url)
            updates = {}
            if parsed.get("spreadsheet_token"):
                updates["feishu_spreadsheet_token"] = parsed["spreadsheet_token"]
            if parsed.get("sheet_id"):
                updates["feishu_sheet_id"] = parsed["sheet_id"]
            if updates:
                updates["feishu_last_sheet_url"] = _build_feishu_sheet_url(
                    updates.get("feishu_spreadsheet_token", st.session_state.get("feishu_spreadsheet_token", "")),
                    updates.get("feishu_sheet_id", st.session_state.get("feishu_sheet_id", "")),
                )
                _queue_widget_state_updates(updates, "已自动提取可识别的 Token。")
                st.rerun()
            st.info("未从链接中识别到可用 Token。")
        if u2.button("自动探测首个 Sheet ID", use_container_width=True, disabled=not st.session_state.get("feishu_spreadsheet_token")):
            try:
                client = get_feishu_client(
                    app_id=st.session_state.get("feishu_app_id", ""),
                    app_secret=st.session_state.get("feishu_app_secret", ""),
                    spreadsheet_token=st.session_state.get("feishu_spreadsheet_token", ""),
                    tenant_access_token=st.session_state.get("feishu_tenant_token", ""),
                    base_url=st.session_state.get("feishu_open_base_url", "https://open.feishu.cn"),
                )
                sheet_id = client.detect_sheet_id(st.session_state.get("feishu_spreadsheet_token", ""))
                if sheet_id:
                    _queue_widget_state_updates(
                        {
                            "feishu_sheet_id": sheet_id,
                            "feishu_last_sheet_url": _build_feishu_sheet_url(
                                st.session_state.get("feishu_spreadsheet_token", ""),
                                sheet_id,
                            ),
                        },
                        f"已探测到 Sheet ID：{sheet_id}",
                    )
                    st.rerun()
                else:
                    st.warning("未能自动探测到 Sheet ID，请从链接中的 ?sheet= 参数手工填写。")
            except Exception as e:
                st.error(f"探测失败: {e}")
        g1, g2 = st.columns(2)
        with g1:
            st.text_input("App ID", key="feishu_app_id")
            st.text_input("Tenant Access Token", key="feishu_tenant_token", type="password", help="可选；若不填则自动使用 App ID + App Secret 换取")
        with g2:
            st.text_input("App Secret", key="feishu_app_secret", type="password")
            st.text_input("Open API Base URL", key="feishu_open_base_url")
        st.text_input(
            "需求链接前缀/模板（可选）",
            key="feishu_requirement_link_base_url",
            help="可填前缀如 http://localhost:8504/req/ 或模板如 https://example.com/req/{req_id}",
        )
        s1, s2, s3 = st.columns(3)
        with s1:
            st.text_input("Spreadsheet Token", key="feishu_spreadsheet_token")
        with s2:
            st.text_input("Sheet ID", key="feishu_sheet_id", help="例如 URL 参数里的 ?sheet=xxx")
        with s3:
            st.text_input("起始单元格", key="feishu_sheet_start_cell")
        s4, s5 = st.columns(2)
        with s4:
            st.checkbox("缺失时自动创建新 Sheet", key="feishu_auto_create_sheet")
            st.text_input("新 Sheet 标题", key="feishu_sheet_title")
        with s5:
            st.text_input("Sheet Folder Token", key="feishu_sheet_folder_token", help="自动创建电子表格时可选")
            if st.button("创建新的 Sheet 并回填 Token", key="create_new_sheet_btn", use_container_width=True):
                try:
                    client = get_feishu_client(
                        app_id=st.session_state.get("feishu_app_id", ""),
                        app_secret=st.session_state.get("feishu_app_secret", ""),
                        tenant_access_token=st.session_state.get("feishu_tenant_token", ""),
                        base_url=st.session_state.get("feishu_open_base_url", "https://open.feishu.cn"),
                    )
                    created = client.create_spreadsheet(
                        st.session_state.get("feishu_sheet_title", f"AI测试用例_{ts}"),
                        folder_token=st.session_state.get("feishu_sheet_folder_token", ""),
                    )
                    if created and created.get("spreadsheet_token"):
                        token = created.get("spreadsheet_token", "")
                        sid = created.get("sheet_id", "")
                        _queue_widget_state_updates(
                            {
                                "feishu_spreadsheet_token": token,
                                "feishu_sheet_id": sid,
                                "feishu_last_sheet_url": _build_feishu_sheet_url(token, sid),
                            },
                            "已创建新的 Sheet 并回填 Token。",
                        )
                        st.rerun()
                    else:
                        st.warning("创建失败，请检查应用权限和文件夹权限。")
                except Exception as e:
                    st.error(f"创建失败: {e}")
        if st.button("检查 Sheet 表头匹配", key="check_sheet_headers", use_container_width=True, disabled=not st.session_state.get("feishu_spreadsheet_token")):
            try:
                client = get_feishu_client(
                    app_id=st.session_state.get("feishu_app_id", ""),
                    app_secret=st.session_state.get("feishu_app_secret", ""),
                    spreadsheet_token=st.session_state.get("feishu_spreadsheet_token", ""),
                    sheet_id=st.session_state.get("feishu_sheet_id", ""),
                    tenant_access_token=st.session_state.get("feishu_tenant_token", ""),
                    base_url=st.session_state.get("feishu_open_base_url", "https://open.feishu.cn"),
                )
                Exporter = get_test_case_exporter()
                expected = Exporter(cases).local_sheet_headers() if cases else []
                actual = client.get_sheet_headers()
                compare = client.compare_headers(expected, actual)
                if compare["missing"] or compare["extra"]:
                    st.warning(f"缺少列：{'、'.join(compare['missing']) or '无'}；额外列：{'、'.join(compare['extra']) or '无'}")
                else:
                    st.success("Sheet 表头匹配。")
            except Exception as e:
                st.error(f"检查失败: {e}")
        sheet_url = st.session_state.get("feishu_last_sheet_url", "") or _build_feishu_sheet_url(
            st.session_state.get("feishu_spreadsheet_token", ""),
            st.session_state.get("feishu_sheet_id", ""),
        )
        if sheet_url:
            st.text_input("表格链接", value=sheet_url, disabled=True)
            st.markdown(f"[打开当前 Sheet]({sheet_url})")
        st.caption("当前实现会直接覆盖指定范围数据，默认把表头写到起始单元格；若未填写 Token，可自动创建新的电子表格。")
        _save_sheet_export_settings()

    st.divider()
    st.markdown(_heading_html("TestLink", "link"), unsafe_allow_html=True)
    with st.expander("连接与导入", expanded=False):
        tl_url = st.text_input(
            "TestLink XML-RPC URL",
            value="http://localhost/testlink/lib/api/xmlrpc/v1/xmlrpc.php",
        )
        tl_key = st.text_input(
            "API Key",
            value=os.getenv("TESTLINK_API_KEY", ""),
            type="password",
            help="或通过环境变量 TESTLINK_API_KEY",
        )
        if "tl_projects" not in st.session_state:
            st.session_state.tl_projects = []
        if st.button("测试连接并拉取项目"):
            if not tl_key:
                st.error("请填写 API Key")
            else:
                try:
                    TLI = get_testlink_importer()
                    temp = TLI(tl_url, tl_key, "", "admin")
                    projects = temp.get_projects_list()
                    st.session_state.tl_projects = projects
                    st.success(f"已连接，项目数: {len(projects)}")
                except Exception as e:
                    st.error(f"失败: {e}")
                    st.session_state.tl_projects = []

        if st.session_state.tl_projects:
            tl_project = st.selectbox("项目", st.session_state.tl_projects)
        else:
            tl_project = st.text_input("项目名称", value="AI_Generated_Project")
        tl_user = st.text_input("作者用户名", value="admin")
        if st.button("导入到 TestLink", disabled=not cases):
            if not tl_key:
                st.error("请填写 API Key")
            else:
                with st.spinner("导入中…"):
                    try:
                        TLI = get_testlink_importer()
                        importer = TLI(tl_url, tl_key, tl_project, tl_user)
                        success, fail = importer.import_test_cases(cases)
                        if success > 0:
                            st.success(f"成功 {success}，失败 {fail}")
                        else:
                            st.warning(f"成功 {success}，失败 {fail}，请检查项目名与权限。")
                    except Exception as e:
                        st.error(f"导入异常: {e}")


def render_tab_kg_browser() -> None:
    _render_heading("知识图谱浏览器", "layers")
    
    backend_online, backend_err = _check_backend()

    if not backend_online:
        extra = f" ({backend_err})" if backend_err else ""
        st.error(f"后端服务未启动 (URL: {_BACKEND_URL}){extra}。")
        return

    with st.spinner("正在获取图谱数据..."):
        try:
            with httpx.Client(timeout=10.0, trust_env=False) as client:
                resp = client.get(f"{_BACKEND_URL}/kg/summary")
                if resp.status_code == 200:
                    summary = resp.json()
                else:
                    st.error(f"获取图谱数据失败: {resp.text}")
                    return
        except Exception as e:
            st.error(f"连接后端失败: {e}")
            return

    _init_kg_candidate_state()
    candidates = st.session_state.kg_candidates
    pending_candidates = [c for c in candidates if c.get("status", "pending") == "pending"]

    st.markdown("### 补充工作台")
    w1, w2, w3 = st.columns(3)
    w1.metric("待确认候选", len(pending_candidates))
    w2.metric("当前批次需求候选", sum(1 for c in pending_candidates if c.get("metadata", {}).get("source") == "requirement_parse"))
    w3.metric("人工审核候选", sum(1 for c in pending_candidates if c.get("metadata", {}).get("source") in {"manual_review", "ai_refine_feedback", "case_delete_feedback"}))

    tool1, tool2 = st.columns(2)
    with tool1:
        with st.expander("故障 / Bug 补充", expanded=False):
            pm_module = st.text_input("所属模块", value="用户中心", key="kg_pm_module")
            pm_text = st.text_area("故障描述", placeholder="例如：连续输错密码 3 次后未锁定账号，存在暴力破解风险。", key="kg_pm_text", height=120)
            if st.button("加入待确认候选", key="kg_pm_add", use_container_width=True):
                if not (pm_text or "").strip():
                    st.warning("请先填写故障描述。")
                else:
                    ok = _queue_kg_candidate(pm_module, "FailureMode", pm_text, {"source": "postmortem_manual"})
                    if ok:
                        st.success("已加入待确认候选。")
                    else:
                        st.info("相同候选已存在。")
                    st.rerun()

    with tool2:
        with st.expander("Excel / CSV 批量导入候选", expanded=False):
            kg_file = st.file_uploader("上传标准化知识文件", type=["csv", "xlsx"], key="kg_bulk_upload")
            st.caption("建议列名：module / item_type / content，可兼容中文列名：模块 / 类型 / 内容")
            if st.button("导入为待确认候选", key="kg_bulk_add", use_container_width=True, disabled=kg_file is None):
                try:
                    if kg_file.name.lower().endswith(".csv"):
                        df = pd.read_csv(kg_file)
                    else:
                        df = pd.read_excel(kg_file)
                    column_map = {str(c).strip().lower(): c for c in df.columns}
                    module_col = column_map.get("module") or column_map.get("模块")
                    type_col = column_map.get("item_type") or column_map.get("type") or column_map.get("类型")
                    content_col = column_map.get("content") or column_map.get("内容") or column_map.get("rule") or column_map.get("规则")
                    if not (module_col and type_col and content_col):
                        st.error("缺少必要列：module/item_type/content 或 模块/类型/内容")
                    else:
                        added = 0
                        for _, row in df.fillna("").iterrows():
                            added += 1 if _queue_kg_candidate(
                                str(row[module_col]).strip(),
                                str(row[type_col]).strip() or "Rule",
                                str(row[content_col]).strip(),
                                {"source": "bulk_import", "file_name": kg_file.name},
                            ) else 0
                        st.success(f"已加入 {added} 条待确认候选。")
                        st.rerun()
                except Exception as e:
                    st.error(f"导入失败: {e}")

    with st.expander(f"待确认图谱候选 ({len(pending_candidates)})", expanded=bool(pending_candidates)):
        if not pending_candidates:
            st.caption("当前没有待确认候选。日常导入、人工审核修改、AI 修正反馈和故障复盘都会自动汇总到这里。")
        else:
            if "kg_candidate_selected_ids" not in st.session_state:
                st.session_state.kg_candidate_selected_ids = []
            cands_df = pd.DataFrame([
                {
                    "选中": c.get("id") in set(st.session_state.kg_candidate_selected_ids),
                    "ID": c.get("id", ""),
                    "模块": c.get("module", "Unknown"),
                    "类型": c.get("item_type", "Rule"),
                    "来源": (c.get("metadata", {}) or {}).get("source", "unknown"),
                    "创建时间": _format_dt(c.get("created_at")),
                    "内容": c.get("content", "")[:120],
                }
                for c in pending_candidates[:200]
            ])
            edited_candidates = st.data_editor(
                cands_df,
                use_container_width=True,
                hide_index=True,
                height=260,
                column_config={"选中": st.column_config.CheckboxColumn(required=False)},
                disabled=[c for c in cands_df.columns if c != "选中"],
                key="kg_candidate_table",
            )
            st.session_state.kg_candidate_selected_ids = edited_candidates.loc[
                edited_candidates["选中"] == True, "ID"
            ].tolist()
            b1, b2, b3 = st.columns(3)
            if b1.button("全选当前候选", use_container_width=True, key="kg_select_all"):
                st.session_state.kg_candidate_selected_ids = [c.get("id") for c in pending_candidates[:200]]
                st.rerun()
            if b2.button("批量确认入库", use_container_width=True, key="kg_batch_accept", disabled=not st.session_state.kg_candidate_selected_ids):
                selected_set = set(st.session_state.kg_candidate_selected_ids)
                ok = 0
                failed = 0
                for item in pending_candidates:
                    if item.get("id") not in selected_set:
                        continue
                    try:
                        res = asyncio.run(call_api_kg_add_item(
                            item.get("module", "Unknown"),
                            item.get("item_type", "Rule"),
                            item.get("content", ""),
                            item.get("metadata", {}),
                        ))
                        if res.get("success"):
                            _remove_kg_candidate(item["id"])
                            ok += 1
                        else:
                            failed += 1
                    except Exception:
                        failed += 1
                st.session_state.kg_candidate_selected_ids = []
                if ok:
                    st.success(f"已批量入库 {ok} 条。")
                if failed:
                    st.warning(f"{failed} 条入库失败，请重试。")
                st.rerun()
            if b3.button("批量忽略", use_container_width=True, key="kg_batch_reject", disabled=not st.session_state.kg_candidate_selected_ids):
                selected_set = set(st.session_state.kg_candidate_selected_ids)
                for item_id in list(selected_set):
                    _remove_kg_candidate(item_id)
                st.session_state.kg_candidate_selected_ids = []
                st.info(f"已忽略 {len(selected_set)} 条候选。")
                st.rerun()

            for item in pending_candidates[:50]:
                meta = item.get("metadata", {}) or {}
                title = f"[{item.get('item_type', 'Rule')}] {item.get('module', 'Unknown')} | {item.get('content', '')[:80]}"
                with st.container(border=True):
                    st.markdown(f"**{title}**")
                    st.caption(f"来源：{meta.get('source', 'unknown')} | 创建时间：{_format_dt(item.get('created_at'))}")
                    st.code(item.get("content", ""), language="text")
                    a1, a2 = st.columns(2)
                    if a1.button("确认入库", key=f"kg_accept_{item['id']}", use_container_width=True):
                        try:
                            res = asyncio.run(call_api_kg_add_item(
                                item.get("module", "Unknown"),
                                item.get("item_type", "Rule"),
                                item.get("content", ""),
                                item.get("metadata", {}),
                            ))
                            if res.get("success"):
                                _remove_kg_candidate(item["id"])
                                st.success("已确认入库。")
                                st.rerun()
                            else:
                                st.error("入库失败。")
                        except Exception as e:
                            st.error(f"入库失败: {e}")
                    if a2.button("忽略候选", key=f"kg_reject_{item['id']}", use_container_width=True):
                        _remove_kg_candidate(item["id"])
                        st.info("已忽略。")
                        st.rerun()

    if not summary:
        st.info("知识图谱中尚无模块数据。")
        return

    # Split summary into Global and Module-specific
    global_nodes = [m for m in summary if m.get("is_global")]
    business_nodes = [m for m in summary if not m.get("is_global")]

    col_sel, col_stat = st.columns([3, 1])
    with col_sel:
        # Sidebar or top selection for module
        mod_names = [m["name"] for m in business_nodes]
        selected_mod_name = st.selectbox("🎯 选择业务模块", options=mod_names)
    
    with col_stat:
        st.metric("业务模块数", len(business_nodes))

    # --- Global Rules Section (Beta 2.0 Enhancement) ---
    if global_nodes:
        with st.expander("🌐 全局业务基线与通用规则", expanded=True):
            g1, g2 = st.columns(2)
            for i, g_node in enumerate(global_nodes):
                target_col = g1 if i % 2 == 0 else g2
                with target_col:
                    st.markdown(f"**{g_node['name']}**")
                    for r in g_node["rules"]:
                        st.markdown(f"- {r}")
                    for method in g_node.get("methods", []):
                        st.markdown(f"- [方法] {method.get('name')}")
                    for tpl in g_node.get("templates", []):
                        st.markdown(f"- [模板] {tpl.get('name')}")
                    for fm in g_node.get("failure_modes", []):
                        st.markdown(f"- [复盘] {fm.get('name')}")
    
    selected_mod = next((m for m in business_nodes if m["name"] == selected_mod_name), None)
    
    if selected_mod:
        ctop1, ctop2, ctop3, ctop4 = st.columns(4)
        ctop1.metric("规则", len(selected_mod.get("rules", [])))
        ctop2.metric("场景", len(selected_mod.get("scenarios", [])))
        ctop3.metric("方法", len(selected_mod.get("methods", [])))
        ctop4.metric("模板/复盘", len(selected_mod.get("templates", [])) + len(selected_mod.get("failure_modes", [])))

        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown(f"### 业务规则 ({len(selected_mod['rules'])})")
            if selected_mod["rules"]:
                for rule in selected_mod["rules"]:
                    st.info(rule)
            else:
                st.caption("暂无显式规则。")
                
        with col2:
            st.markdown(f"### 测试场景 ({len(selected_mod['scenarios'])})")
            if selected_mod["scenarios"]:
                for sce in selected_mod["scenarios"]:
                    with st.expander(f"{sce['type']}: {sce['name']}", expanded=False):
                        st.write(sce["logic"])
            else:
                st.caption("暂无预定义场景。")

        col3, col4 = st.columns(2)
        with col3:
            methods = selected_mod.get("methods", [])
            st.markdown(f"### 测试方法 ({len(methods)})")
            if methods:
                for method in methods:
                    with st.expander(method.get("name", "未命名方法"), expanded=False):
                        st.write(method.get("logic", ""))
            else:
                st.caption("暂无测试方法。")

        with col4:
            templates = selected_mod.get("templates", [])
            failures = selected_mod.get("failure_modes", [])
            st.markdown(f"### 模板与复盘 ({len(templates) + len(failures)})")
            if templates:
                st.markdown("**用例模板**")
                for tpl in templates:
                    with st.expander(tpl.get("name", "未命名模板"), expanded=False):
                        st.write(tpl.get("logic", ""))
            if failures:
                st.markdown("**故障复盘**")
                for fm in failures:
                    with st.expander(fm.get("name", "未命名失效模式"), expanded=False):
                        st.write(fm.get("logic", ""))
            if not templates and not failures:
                st.caption("暂无模板或故障复盘。")

    st.divider()
    st.caption("提示：你可以通过「评审与导出」中的「学习到图谱」功能沉淀规则，也可以通过后端接口补充故障复盘、模板和测试方法。")

def main() -> None:
    icon_path = _UI_DIR / "assets" / "icon.svg"
    st.set_page_config(
        page_title="一体化智能测试管理平台",
        page_icon=str(icon_path) if icon_path.exists() else None,
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(APP_CSS, unsafe_allow_html=True)

    _init_session()
    _auto_bootstrap_startup()

    with st.sidebar:
        render_sidebar()

    page = st.session_state.get("current_page", "首页")
    if page == "使用说明":
        st.session_state.current_page = "首页"
        page = "首页"
    st.markdown(_topbar_html(page), unsafe_allow_html=True)
    st.markdown(
        f"""
        <div class="app-shell-header">
            <div class="app-shell-title">
                <div class="app-shell-minor">{page}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if page == "首页":
        render_home_dashboard()
    elif page == "数据统计":
        render_tab_stats()
    elif page == "导入需求":
        render_tab_import()
    elif page == "生成用例":
        render_tab_generate()
    elif page == "评审与导出":
        render_tab_review_export()
    elif page == "知识图谱":
        render_tab_kg_browser()


if __name__ == "__main__":
    main()
