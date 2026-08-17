from __future__ import annotations

import re
from typing import Any, Dict, List
from loguru import logger


QUALITY_CATEGORY_DEFINITIONS: Dict[str, str] = {
    "功能性": "在指定条件下使用时，产品或系统提供满足明确和隐含要求的功能程度。",
    "性能效率": "性能与在指定条件下所使用的资源量有关。",
    "兼容性": "在共享相同的硬件或软件环境的条件下，产品、系统或组件能够与其他产品、系统或组件交换信息，和/或执行其所需的功能的程度。",
    "易用性": "在指定的使用周境中，产品或系统在有效性、效率和满意度特性方面为了指定的目标可为指定用户使用的程度。",
    "可靠性": "系统、产品或组件在指定条件下、指定时间内执行指定功能的程度。",
    "信息安全性": "产品或系统保护信息和数据的程度，以使用户、其他产品或系统具有与其授权类型和授权级别一致的数据访问度。",
    "维护性": "产品或系统能够被预期的维护人员修改的有效性和效率的程度。",
    "可移植性": "系统、产品或组件能够从一种硬件、软件、或者其他运行（或使用）环境迁移到另一种环境的有效性和效率的程度。",
}


QUALITY_CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "性能效率": ["性能", "吞吐", "响应时间", "并发", "延迟", "耗时", "cpu", "内存", "负载", "资源", "benchmark", "qps", "tps"],
    "兼容性": ["兼容", "浏览器", "跨平台", "适配", "版本", "android", "ios", "windows", "mac", "linux", "交换信息", "对接"],
    "易用性": ["易用", "可用", "可访问", "用户体验", "友好", "提示", "交互", "引导", "满意度", "上手", "无障碍"],
    "可靠性": ["可靠", "稳定", "容错", "故障", "恢复", "重试", "可用性", "异常恢复", "宕机", "超时", "降级", "冗余"],
    "信息安全性": ["安全", "鉴权", "认证", "授权", "权限", "token", "密码", "加密", "脱敏", "越权", "注入", "xss", "csrf", "sql", "审计"],
    "维护性": ["维护", "扩展", "重构", "可测试", "可读", "可修改", "模块化", "解耦", "配置化", "日志", "诊断"],
    "可移植性": ["移植", "迁移", "部署环境", "容器", "docker", "操作系统", "环境切换", "云平台", "安装", "跨环境"],
    "功能性": ["功能", "业务", "流程", "查询", "新增", "删除", "修改", "导出", "导入", "审批", "登录", "支付", "告警", "管理"],
}


_CATEGORY_PRIORITY = [
    "信息安全性",
    "性能效率",
    "可靠性",
    "兼容性",
    "易用性",
    "维护性",
    "可移植性",
    "功能性",
]

_LLM_SERVICE = None
_LLM_SERVICE_INIT_FAILED = False


def _normalize_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def _classify_quality_category_fixed_rules(text: str, extra_texts: List[str] | None = None) -> Dict[str, Any]:
    corpus_parts = [text] + list(extra_texts or [])
    corpus = " ".join(_normalize_text(item) for item in corpus_parts if item)

    matched_keywords: Dict[str, List[str]] = {}
    for category, keywords in QUALITY_CATEGORY_KEYWORDS.items():
        hits = [kw for kw in keywords if kw.lower() in corpus]
        if hits:
            matched_keywords[category] = hits

    for category in _CATEGORY_PRIORITY:
        hits = matched_keywords.get(category)
        if hits:
            return {
                "category": category,
                "basis": QUALITY_CATEGORY_DEFINITIONS[category],
                "matched_keywords": hits[:5],
                "method": "fixed_rules",
            }

    return {
        "category": "功能性",
        "basis": QUALITY_CATEGORY_DEFINITIONS["功能性"],
        "matched_keywords": ["默认归类"],
        "method": "fixed_rules",
    }


def _get_llm_service():
    global _LLM_SERVICE, _LLM_SERVICE_INIT_FAILED
    if _LLM_SERVICE is not None:
        return _LLM_SERVICE
    if _LLM_SERVICE_INIT_FAILED:
        return None
    try:
        from src.core.ai.llm_service import LLMService
        service = LLMService()
        if not getattr(service, "client", None) or getattr(service, "_config_error", None):
            _LLM_SERVICE_INIT_FAILED = True
            return None
        _LLM_SERVICE = service
        return _LLM_SERVICE
    except Exception as e:
        logger.warning(f"Failed to initialize LLM quality classifier, fallback to rules: {e}")
        _LLM_SERVICE_INIT_FAILED = True
        return None


def classify_quality_category(text: str, extra_texts: List[str] | None = None) -> Dict[str, Any]:
    service = _get_llm_service()
    if service is not None:
        try:
            return service.classify_quality_characteristic(text, QUALITY_CATEGORY_DEFINITIONS, extra_texts)
        except Exception as e:
            logger.warning(f"LLM quality classification failed, fallback to rules: {e}")
    return _classify_quality_category_fixed_rules(text, extra_texts)


def _meta_dict(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return {}


def get_requirement_quality_category(req: Any) -> str:
    meta = _meta_dict(getattr(req, "ingestion_metadata", None))
    return str(meta.get("quality_characteristic") or "")


def get_testcase_quality_category(tc: Any) -> str:
    env = _meta_dict(getattr(tc, "system_env", None))
    return str(env.get("quality_characteristic") or "")


def annotate_quality_characteristics(requirements: List[Any], test_cases: List[Any]) -> Dict[str, int]:
    changed_req = 0
    changed_case = 0
    req_category_map: Dict[str, str] = {}

    for req in requirements or []:
        entities = req.get_extracted_entities() if hasattr(req, "get_extracted_entities") else None
        module = str(getattr(entities, "module", "") or "")
        feature = str(getattr(entities, "feature", "") or "")
        result = classify_quality_category(getattr(req, "original_text", ""), [module, feature])
        meta = _meta_dict(getattr(req, "ingestion_metadata", None))
        if meta.get("quality_characteristic") != result["category"] or meta.get("quality_characteristic_method") != result["method"]:
            meta["quality_characteristic"] = result["category"]
            meta["quality_characteristic_method"] = result["method"]
            meta["quality_characteristic_basis"] = result.get("basis", "")
            setattr(req, "ingestion_metadata", meta)
            changed_req += 1
        req_category_map[getattr(req, "id", "")] = result["category"]

    for tc in test_cases or []:
        ti = tc.get_test_instruction() if hasattr(tc, "get_test_instruction") else None
        steps = "\n".join(getattr(ti, "steps", []) or [])
        expected = getattr(ti, "expected_result", "") if ti else ""
        linked_category = req_category_map.get(getattr(tc, "related_req_id", ""), "")
        result = classify_quality_category(
            getattr(tc, "title", ""),
            [steps, expected, getattr(tc, "dimension", ""), linked_category],
        )
        env = _meta_dict(getattr(tc, "system_env", None))
        if env.get("quality_characteristic") != result["category"] or env.get("quality_characteristic_method") != result["method"]:
            env["quality_characteristic"] = result["category"]
            env["quality_characteristic_method"] = result["method"]
            env["quality_characteristic_basis"] = result.get("basis", "")
            setattr(tc, "system_env", env)
            changed_case += 1

    return {"requirements_changed": changed_req, "test_cases_changed": changed_case}


def build_statistics(requirements: List[Any], test_cases: List[Any]) -> Dict[str, Any]:
    annotate_quality_characteristics(requirements, test_cases)
    req_rows: List[Dict[str, Any]] = []
    case_rows: List[Dict[str, Any]] = []
    req_category_map: Dict[str, str] = {}

    for req in requirements or []:
        entities = req.get_extracted_entities() if hasattr(req, "get_extracted_entities") else None
        module = str(getattr(entities, "module", "") or "")
        feature = str(getattr(entities, "feature", "") or "")
        category = get_requirement_quality_category(req) or "功能性"
        req_category_map[getattr(req, "id", "")] = category
        req_rows.append(
            {
                "需求ID": getattr(req, "id", ""),
                "模块": module or "—",
                "功能": feature or "—",
                "分类": category,
                "原始文本": getattr(req, "original_text", ""),
            }
        )

    for tc in test_cases or []:
        ti = tc.get_test_instruction() if hasattr(tc, "get_test_instruction") else None
        steps = "\n".join(getattr(ti, "steps", []) or [])
        expected = getattr(ti, "expected_result", "") if ti else ""
        linked_category = req_category_map.get(getattr(tc, "related_req_id", ""), "")
        category = get_testcase_quality_category(tc) or linked_category or "功能性"
        case_rows.append(
            {
                "用例ID": getattr(tc, "test_case_id", ""),
                "关联需求": getattr(tc, "related_req_id", ""),
                "标题": getattr(tc, "title", "") or "—",
                "分类": category,
            }
        )

    category_summary: List[Dict[str, Any]] = []
    for category, basis in QUALITY_CATEGORY_DEFINITIONS.items():
        req_count = sum(1 for row in req_rows if row["分类"] == category)
        case_count = sum(1 for row in case_rows if row["分类"] == category)
        category_summary.append(
            {
                "分类": category,
                "需求数": req_count,
                "用例数": case_count,
            }
        )

    overview = {
        "需求总数": len(req_rows),
        "用例总数": len(case_rows),
        "分类总数": len(QUALITY_CATEGORY_DEFINITIONS),
        "已分类需求数": len(req_rows),
        "已分类用例数": len(case_rows),
    }
    return {
        "overview": overview,
        "category_summary": category_summary,
        "requirement_rows": req_rows,
        "case_rows": case_rows,
    }
