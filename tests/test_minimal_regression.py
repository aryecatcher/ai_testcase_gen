import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from src.core.ai.llm_service import _llm_cache_key
from src.core.ai.evaluator import build_case_map, score_judge_result
from src.core.analytics import build_statistics, classify_quality_category
import src.core.analytics as analytics_module
from src.core.generation.validators import ValidationInterceptor
from src.core.generation.generator import TestCaseGenerator
from src.core.ingestion.change_analyzer import analyze_requirement_changes, apply_case_change_plan
from src.core.kg.graph_service import KnowledgeGraphService
from src.core.kg.networkx_repo import NetworkXGraphRepository
from src.core.output.exporter import TestCaseExporter
from src.core.output.postman_exporter import PostmanExporter
from src.models.domain import IngestionMetadata, Requirement, TestCase, TestInstruction


def test_llm_cache_key_is_stable():
    key1 = _llm_cache_key("REQ-1", "a=1", "s=2")
    key2 = _llm_cache_key("REQ-1", "a=1", "s=2")
    key3 = _llm_cache_key("REQ-1", "a=9", "s=2")

    assert key1 == key2
    assert key1 != key3
    assert key1.startswith("REQ-1_")


def test_generator_smoke_returns_case_objects():
    mock_llm = MagicMock()
    mock_llm.async_client = None
    mock_llm.async_generate_cases = AsyncMock(
        return_value=[
            {
                "title": "Case A",
                "precondition": "Ready",
                "steps": [{"action": "open", "result": "ok"}],
                "expected_result": "Success",
                "test_data": {"valid": {}, "invalid": {}},
                "priority": "P2",
                "type": "Functional",
                "methodology": ["LLM"],
            }
        ]
    )

    mock_kg = MagicMock()
    mock_kg.get_related_constraints = MagicMock(return_value="")
    mock_kg.expand_scenarios = MagicMock(return_value=[])

    generator = TestCaseGenerator(mock_llm, mock_kg)
    req = Requirement(
        id="REQ-SMOKE",
        original_text="用户可以登录系统",
        ingestion_metadata=IngestionMetadata(source_file="smoke.txt"),
    )

    cases = generator.generate([req])
    assert cases
    assert cases[0].related_req_id == "REQ-SMOKE"
    assert cases[0].get_test_instruction().expected_result


def test_exporters_smoke_output_non_empty():
    case1 = TestCase(
        related_req_id="REQ-1",
        title="Functional Case",
        dimension="Functional",
        test_instruction=TestInstruction(
            pre_condition="User exists",
            steps=["Step 1"],
            expected_result="OK",
        ),
    )
    case2 = TestCase(
        related_req_id="REQ-2",
        title="API Case",
        dimension="Interface",
        test_instruction=TestInstruction(
            pre_condition="API up",
            steps=["Call API"],
            expected_result="200",
        ),
    )

    xlsx = TestCaseExporter([case1, case2]).to_excel()
    assert isinstance(xlsx, (bytes, bytearray))
    assert len(xlsx) > 100

    postman = PostmanExporter([case1, case2]).to_collection()
    payload = json.loads(postman.decode("utf-8"))
    assert payload["info"]["name"] == "Generated API Tests"
    assert len(payload["item"]) == 1

    headers = TestCaseExporter([case1, case2]).to_sheet_values()[0]
    assert headers == [
        "项目名称",
        "测试用例 ID",
        "需求对应",
        "优先级",
        "质量特性",
        "前提条件",
        "测试目的描述",
        "测试步骤概述",
        "期望结果",
        "实测结果",
        "Pass/ Fail/NT",
    ]


def test_requirement_change_analysis_rebinds_existing_cases():
    old_req = Requirement(
        id="REQ-OLD-1",
        original_text="用户可以登录系统并查看首页",
        ingestion_metadata={"source_file": "spec_v1.docx"},
        extracted_entities={"module": "认证中心", "feature": "登录", "constraints": []},
    )
    removed_req = Requirement(
        id="REQ-OLD-2",
        original_text="用户可以修改头像",
        ingestion_metadata={"source_file": "spec_v1.docx"},
        extracted_entities={"module": "用户中心", "feature": "头像", "constraints": []},
    )
    new_req = Requirement(
        id="REQ-NEW-1",
        original_text="用户可以登录系统、查看首页，并在连续失败 3 次后锁定账号",
        ingestion_metadata={"source_file": "spec_v2.docx"},
        extracted_entities={"module": "认证中心", "feature": "登录", "constraints": []},
    )
    case = TestCase(
        test_case_id="TC-KEEP",
        related_req_id="REQ-OLD-1",
        title="登录成功",
        test_instruction=TestInstruction(steps=["1. 打开登录页"], expected_result="登录成功"),
    )

    report = analyze_requirement_changes([old_req, removed_req], [new_req], [case])

    assert report["summary"]["updated"] == 1
    assert report["summary"]["removed"] == 1
    assert report["remap_old_to_new_req_id"] == {"REQ-OLD-1": "REQ-NEW-1"}

    updated_cases = apply_case_change_plan([case], report)
    assert updated_cases[0].related_req_id == "REQ-NEW-1"
    assert updated_cases[0].system_env["change_impact"] == "needs_update"


def test_validation_interceptor_redacts_sensitive_values():
    interceptor = ValidationInterceptor()
    raw_case = {
        "title": "校验 sk_live_1234567890abcdef",
        "precondition": "联系 admin@example.com 并访问 8.8.8.8",
        "steps": ["输入手机号 13912345678", "提交 token=abcdef1234567890"],
        "expected_result": "系统通知 admin@example.com",
        "test_data": {
            "valid": {
                "real_name": "张三",
                "server_ip": "8.8.8.8",
                "access_token": "sk_live_1234567890abcdef",
            },
            "invalid": {
                "contact_email": "ops@example.com",
            },
        },
    }

    sanitized = interceptor.validate_case(raw_case)

    assert sanitized["test_data"]["valid"]["real_name"] == "测试用户"
    assert sanitized["test_data"]["valid"]["server_ip"] == "203.0.113.10"
    assert sanitized["test_data"]["valid"]["access_token"] == "<REDACTED_SECRET>"
    assert sanitized["test_data"]["invalid"]["contact_email"] == "test@example.com"
    assert "<REDACTED_SECRET>" in sanitized["title"]
    assert "203.0.113.10" in sanitized["precondition"]


def test_kg_backend_can_be_configured_by_env(monkeypatch):
    monkeypatch.setenv("KG_BACKEND", "networkx")
    assert KnowledgeGraphService().use_neo4j is False

    monkeypatch.setenv("KG_BACKEND", "auto")
    assert KnowledgeGraphService().use_neo4j is True


def test_ai_evaluator_helpers_are_stable():
    case = TestCase(
        test_case_id="TC-EVAL",
        related_req_id="REQ-EVAL",
        title="评估样例",
        test_instruction=TestInstruction(steps=["1. 打开页面"], expected_result="展示成功"),
    )
    case_map = build_case_map([case])
    assert list(case_map.keys()) == ["REQ-EVAL"]
    assert case_map["REQ-EVAL"][0].test_case_id == "TC-EVAL"

    assert score_judge_result({"violations": [], "gaps": [], "passed": True}) == 100
    assert score_judge_result({"violations": ["v1"], "gaps": ["g1"], "passed": False}) == 70


def test_kg_ontology_auto_upgrade_is_triggered(tmp_path):
    storage = tmp_path / "kg_graph.json"
    audit = tmp_path / "kg_audit.json"
    repo = NetworkXGraphRepository(storage_path=str(storage), audit_path=str(audit))

    ok = repo.add_knowledge_item(
        "报表中心",
        "FailureMode",
        "导出功能在超时后未写入失败审计日志",
        metadata={"feature": "导出"},
    )

    assert ok is True
    assert repo.graph.has_node("报表中心")
    assert repo.graph.has_edge("报表中心", "故障复盘库")

    feature_nodes = [
        node for node, data in repo.graph.nodes(data=True)
        if data.get("type") == "Feature" and "导出" in ([node] + (data.get("alias") or []))
    ]
    assert feature_nodes, "应自动补出 feature 节点"

    failure_nodes = [
        node for node, data in repo.graph.nodes(data=True)
        if data.get("type") == "FailureMode" and data.get("content") == "导出功能在超时后未写入失败审计日志"
    ]
    assert failure_nodes, "应成功入库 failure mode"
    assert any(entry.get("action") == "AUTO_UPGRADE_ONTOLOGY" for entry in repo.audit_log)


def test_fixed_statistics_classification_works():
    req_security = Requirement(
        id="REQ-SEC",
        original_text="系统应支持权限控制、token 校验与审计日志。",
        extracted_entities={"module": "权限中心", "feature": "鉴权"},
    )
    req_perf = Requirement(
        id="REQ-PERF",
        original_text="系统在 1000 并发下响应时间不超过 2 秒，控制 CPU 与内存占用。",
        extracted_entities={"module": "告警管理", "feature": "性能监控"},
    )
    case = TestCase(
        test_case_id="TC-SEC",
        related_req_id="REQ-SEC",
        title="校验越权访问被拦截",
        dimension="Security",
        test_instruction=TestInstruction(
            steps=["1. 使用低权限账号访问高权限接口"],
            expected_result="返回无权限",
        ),
    )

    sec_result = classify_quality_category(req_security.original_text, ["权限", "token"])
    assert sec_result["category"] == "信息安全性"

    stats = build_statistics([req_security, req_perf], [case])
    summary = {row["分类"]: row for row in stats["category_summary"]}
    assert summary["信息安全性"]["需求数"] == 1
    assert summary["性能效率"]["需求数"] == 1
    assert summary["信息安全性"]["用例数"] == 1
    assert req_security.ingestion_metadata["quality_characteristic"] == "信息安全性"
    assert case.system_env["quality_characteristic"] == "信息安全性"


def test_quality_classification_prefers_llm_and_persists_method(monkeypatch):
    class StubLLMService:
        def classify_quality_characteristic(self, text, definitions, extra_texts=None):
            return {
                "category": "维护性",
                "basis": "文本强调可维护与可扩展",
                "matched_keywords": [],
                "confidence": 0.91,
                "method": "llm",
            }

    monkeypatch.setattr(analytics_module, "_LLM_SERVICE", None)
    monkeypatch.setattr(analytics_module, "_LLM_SERVICE_INIT_FAILED", False)
    monkeypatch.setattr(analytics_module, "_get_llm_service", lambda: StubLLMService())

    req = Requirement(
        id="REQ-MAINT",
        original_text="系统需要便于扩展、重构和维护。",
        extracted_entities={"module": "系统管理", "feature": "配置中心"},
    )
    case = TestCase(
        test_case_id="TC-MAINT",
        related_req_id="REQ-MAINT",
        title="验证配置模块支持扩展与维护",
        test_instruction=TestInstruction(
            steps=["1. 检查配置模块是否支持独立扩展"],
            expected_result="模块化清晰，可按配置扩展",
        ),
    )

    result = classify_quality_category(req.original_text, ["扩展", "维护"])
    assert result["category"] == "维护性"
    assert result["method"] == "llm"

    stats = build_statistics([req], [case])
    summary = {row["分类"]: row for row in stats["category_summary"]}
    assert summary["维护性"]["需求数"] == 1
    assert summary["维护性"]["用例数"] == 1
    assert req.ingestion_metadata["quality_characteristic"] == "维护性"
    assert req.ingestion_metadata["quality_characteristic_method"] == "llm"
    assert case.system_env["quality_characteristic"] == "维护性"
    assert case.system_env["quality_characteristic_method"] == "llm"
