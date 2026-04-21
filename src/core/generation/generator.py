import concurrent.futures
import asyncio
from typing import List, Dict, Any
import json
import time
import traceback
from loguru import logger
from ..ai.llm_service import LLMService
from ..kg.graph_service import KnowledgeGraphService
from ...models.domain import Requirement, TestCase, TestInstruction, BusinessLogic, TestDataSets, TestCaseStatus, ExtractedEntities, ReqSpec
from .data_synthesizer import DataSynthesizer
from .validators import ValidationInterceptor
from ..ai.req_parser import RequirementParser
from ..ai.optimizer import CaseOptimizer
from .workflow import GenerationWorkflow
from ...data.database import update_requirement, get_requirement_by_id
import hashlib
from difflib import SequenceMatcher


def _safe_json_loads(text: str):
    try:
        return json.loads(text)
    except Exception:
        return None


def _to_extracted_entities(raw) -> ExtractedEntities:
    """兼容 dict 和 ExtractedEntities 对象"""
    if raw is None:
        return ExtractedEntities()
    if isinstance(raw, ExtractedEntities):
        return raw
    if isinstance(raw, str):
        parsed = _safe_json_loads(raw)
        if isinstance(parsed, dict):
            raw = parsed
    if isinstance(raw, dict):
        try:
            return ExtractedEntities(**raw)
        except Exception as e:
            logger.warning(f"_to_extracted_entities: invalid dict payload, fallback to empty. err={e} payload={raw}")
            return ExtractedEntities()
    logger.warning(f"_to_extracted_entities: unexpected payload type={type(raw)} value={raw}, fallback to empty.")
    return ExtractedEntities()


def _to_req_spec(raw):
    """兼容 dict 和 ReqSpec 对象"""
    if raw is None:
        return None
    if isinstance(raw, ReqSpec):
        return raw
    if isinstance(raw, str):
        parsed = _safe_json_loads(raw)
        if isinstance(parsed, dict):
            raw = parsed
    if isinstance(raw, dict):
        try:
            return ReqSpec(**raw)
        except Exception as e:
            logger.warning(f"_to_req_spec: invalid dict payload, fallback to None. err={e} payload={raw}")
            return None
    logger.warning(f"_to_req_spec: unexpected payload type={type(raw)} value={raw}, fallback to None.")
    return None


class TestCaseGenerator:
    def __init__(self, llm_service: LLMService, kg_service: KnowledgeGraphService, max_concurrency: int | None = None):
        self.llm_service = llm_service
        self.kg_service = kg_service
        if max_concurrency is None:
            self.max_concurrency = 4 if getattr(llm_service, "_is_local_compatible", False) else 20
        else:
            self.max_concurrency = max_concurrency
        self.synth = DataSynthesizer()
        self.validator = ValidationInterceptor()
        self.parser = RequirementParser()
        self.optimizer = CaseOptimizer()
        self.workflow = GenerationWorkflow(llm_service, kg_service) # LangGraph Workflow
        self._lock = asyncio.Lock()
        # In-memory caches to speed up repeated queries within a single session
        self._kg_cache = {} 
        self._similarity_cache = {} # context_hash -> List[TestCase]

    def generate(self, requirements: List[Requirement], progress_callback=None) -> List[TestCase]:
        """
        Main entry point (synchronous wrapper for compatibility).
        """
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                logger.warning("Already in a running event loop. 'generate' might block or fail.")
                return asyncio.run_coroutine_threadsafe(
                    self.async_generate(requirements, progress_callback), 
                    loop
                ).result()
        except RuntimeError:
            # No running loop, safe to use asyncio.run
            return asyncio.run(self.async_generate(requirements, progress_callback))

    async def async_generate(self, requirements: List[Requirement], progress_callback=None) -> List[TestCase]:
        """
        Asynchronous generation of test cases using AsyncIO.
        """
        raw_test_cases = []
        async for update in self.stream_generate(requirements):
            if update["type"] == "result":
                raw_test_cases.extend(update["data"])
            if progress_callback and update["type"] == "progress":
                if update.get("status") == "已完成":
                    progress_callback(update["current"], update["total"])
        
        # Deduplication (Reduce Phase)
        final_cases = self._deduplicate_cases(raw_test_cases)
        return final_cases

    async def stream_generate(self, requirements: List[Requirement]):
        """
        Stream generation updates (progress, results, and workflow trace).
        Uses a queue to multiplex updates from multiple parallel tasks.
        """
        total_reqs = len(requirements)
        if total_reqs == 0:
            yield {"type": "progress", "current": 0, "total": 0}
            return

        queue = asyncio.Queue()
        completed_reqs = 0
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def _producer(req: Requirement):
            nonlocal completed_reqs
            async with semaphore:
                try:
                    req_start = time.time()
                    tokens_total = 0
                    last_emitted_sig = None
                    # Accumulators for trace
                    accumulated_trace = []
                    
                    async for wf_update in self.workflow.run_with_updates(req):
                        try:
                            tokens_total += int(wf_update.get("tokens", 0) or 0)
                        except Exception:
                            pass
                        # Update accumulators
                        if wf_update["trace"]:
                            accumulated_trace.extend(wf_update["trace"])
                        
                        await queue.put({
                            "type": "progress",
                            "req_id": req.id,
                            "current": completed_reqs,
                            "total": total_reqs,
                            "node": wf_update["node"],
                            "status": wf_update["status"],
                            "trace": wf_update["trace"],
                            "kg_hit": wf_update.get("kg_hit", False),
                            "tokens": wf_update.get("tokens", 0),
                            "iteration": wf_update.get("iteration", 0),
                            "elapsed_sec": round(time.time() - req_start, 3)
                        })
                        if wf_update["final_cases"]:
                            try:
                                sig_parts = []
                                for c in wf_update["final_cases"]:
                                    if hasattr(c, "get_test_instruction"):
                                        ti = c.get_test_instruction()
                                        sig_parts.append(f"{c.title}|{ti.steps}|{ti.expected_result}")
                                    else:
                                        sig_parts.append(str(c))
                                sig = hashlib.md5("|".join(sig_parts).encode("utf-8")).hexdigest()
                            except Exception:
                                sig = None

                            if sig is None or sig != last_emitted_sig:
                                last_emitted_sig = sig
                                logger.info(f"Producer for {req.id}: Putting {len(wf_update['final_cases'])} final cases into queue.")
                                await queue.put({"type": "result", "req_id": req.id, "data": wf_update["final_cases"]})
                    
                    # Persistence: Update the requirement in DB with final trace
                    try:
                        db_req = get_requirement_by_id(req.id)
                        if db_req:
                            db_req.generation_trace = accumulated_trace
                            # Ensure req_spec is a ReqSpec object before updating
                            req_spec_obj = _to_req_spec(db_req.req_spec)
                            if req_spec_obj:
                                db_req.req_spec = req_spec_obj.model_dump()
                            update_requirement(db_req)
                            logger.info(f"Persisted trace for req {req.id}")
                    except Exception as db_err:
                        logger.error(f"Failed to persist audit data for {req.id}: {db_err}")

                    # Augmentation
                    # Ensure req is fully hydrated before augmentation
                    req.req_spec = _to_req_spec(req.req_spec)
                    req.extracted_entities = _to_extracted_entities(req.extracted_entities)
                    augmented_raw = self._augment_cases(req)
                    logger.info(f"_producer: augmented_raw type={type(augmented_raw)} value={augmented_raw}")

                    if isinstance(augmented_raw, dict):
                        augmented_raw = [augmented_raw]
                    elif not isinstance(augmented_raw, list):
                        logger.warning(f"_producer: augmented_raw is not list/dict, skipping. type={type(augmented_raw)}")
                        augmented_raw = []

                    aug_cases = []
                    for idx, raw in enumerate(augmented_raw):
                        if not isinstance(raw, dict):
                            logger.warning(f"_producer: augmented_raw[{idx}] is not dict, skipping. type={type(raw)} value={raw}")
                            continue

                        td_raw = raw.get("test_data", {}) or {}
                        if isinstance(td_raw, str):
                            td_parsed = _safe_json_loads(td_raw)
                            td_raw = td_parsed if isinstance(td_parsed, dict) else {}

                        steps = self.validator.normalize_steps(
                            raw.get("steps", []),
                            raw.get("title", "Augmented Case"),
                        )

                        methodology_raw = raw.get("methodology", ["Rule-Based"])
                        if isinstance(methodology_raw, str):
                            methodology = [methodology_raw]
                        elif isinstance(methodology_raw, list):
                            methodology = [str(m) for m in methodology_raw]
                        else:
                            methodology = ["Rule-Based"]

                        tc = TestCase(
                            related_req_id=req.id,
                            title=self.validator.clean_text(raw.get("title", "Augmented Case")) or "Augmented Case",
                            test_instruction={
                                "pre_condition": self.validator.clean_text(raw.get("precondition", "None")) or "系统已完成基础部署，测试数据准备完成。",
                                "steps": steps,
                                "expected_result": self.validator.clean_text(raw.get("expected_result", "Success")) or "系统按照需求规则处理，并返回明确结果。",
                                "test_data_sets": {
                                    "valid": td_raw.get("valid", {}) if isinstance(td_raw, dict) else {},
                                    "invalid": td_raw.get("invalid", {}) if isinstance(td_raw, dict) else {}
                                }
                            },
                            methodology=methodology,
                            dimension=raw.get("type", "Functional"),
                            priority=raw.get("priority", "P1")
                        )
                        aug_cases.append(tc)
                    
                    if aug_cases:
                        await queue.put({"type": "result", "req_id": req.id, "data": aug_cases})

                    async with self._lock:
                        completed_reqs += 1
                    
                    await queue.put({
                        "type": "progress",
                        "req_id": req.id,
                        "current": completed_reqs,
                        "total": total_reqs,
                        "status": "已完成",
                        "trace": ["✅ 该需求处理完毕。"],
                        "tokens_total": tokens_total,
                        "elapsed_sec": round(time.time() - req_start, 3)
                    })
                except Exception as e:
                    err_type = e.__class__.__name__
                    err_msg = str(e) or repr(e)
                    tb = traceback.format_exc(limit=5)
                    logger.error(f"Error processing {req.id}: [{err_type}] {err_msg}\n{tb}")
                    await queue.put({
                        "type": "error",
                        "req_id": req.id,
                        "message": f"[{err_type}] {err_msg}",
                        "detail": tb
                    })

        # Start all producers
        tasks = [asyncio.create_task(_producer(req)) for req in requirements]
        
        # Monitor producers and signal end
        async def _monitor():
            await asyncio.gather(*tasks)
            await queue.put(None) # Sentinel

        asyncio.create_task(_monitor())

        # Consumer: yield from queue as items arrive
        while True:
            update = await queue.get()
            if update is None:
                break
            yield update

    def _deduplicate_cases(self, cases: List[TestCase]) -> List[TestCase]:
        """
        Reduce phase: Removes duplicates using semantic hashing and fuzzy matching.
        """
        def _norm(text: str) -> str:
            text = (text or "").replace("(Mock)", "").replace("（Mock）", "").strip().lower()
            return " ".join(text.split())

        def _quality(tc: TestCase) -> tuple:
            ti = tc.get_test_instruction() if hasattr(tc, "get_test_instruction") else tc.test_instruction
            if isinstance(ti, dict):
                steps = ti.get("steps", []) or []
                expected = ti.get("expected_result", "") or ""
            else:
                steps = getattr(ti, "steps", []) or []
                expected = getattr(ti, "expected_result", "") or ""
            title = _norm(tc.title or "")
            is_mock = "(mock)" in (tc.title or "").lower() or _norm(expected) == "system behaves as required."
            return (0 if is_mock else 1, len(steps), len(_norm(expected)), len(title))

        unique_map = {}
        pending_cases = []
        
        # 1. First Pass: Separate valid cases from pending fragments
        valid_cases = []
        for case in cases:
            if case.status == TestCaseStatus.PENDING:
                pending_cases.append(case)
            else:
                valid_cases.append(case)
                
        # 2. Logic Reconstruction
        for pending in pending_cases:
            if pending.title:
                pending.title = pending.title.replace("PENDING_LOGIC", "Constraint Check")
            pending.status = TestCaseStatus.COMPLETE
            valid_cases.append(pending)

        # 3. Deduplication on Valid Cases
        buckets = {}
        
        for case in valid_cases:
            # 兼容 test_instruction 为 dict 或对象
            ti = case.test_instruction
            if isinstance(ti, dict):
                steps_val = ti.get("steps", [])
                if isinstance(steps_val, list):
                    steps_str = "".join([str(s) for s in steps_val])
                else:
                    steps_str = str(steps_val or "")
                expected = ti.get("expected_result", "")
            else:
                steps_str = "".join([str(s) for s in (ti.steps if ti else [])])
                expected = ti.expected_result if ti else ""

            title_norm = _norm(case.title or "")
            content_sig = f"{title_norm}_{_norm(steps_str)}_{_norm(expected)}"
            fingerprint = hashlib.md5(content_sig.encode()).hexdigest()
            
            title_prefix = title_norm[:12]
            bucket_key = f"{case.dimension}_{title_prefix}"
            
            is_duplicate = False
            
            if fingerprint in unique_map:
                existing = unique_map[fingerprint]
                if _quality(case) > _quality(existing):
                    unique_map[fingerprint] = case
                is_duplicate = True
            else:
                if bucket_key in buckets:
                    for existing in buckets[bucket_key]:
                        if SequenceMatcher(None, title_norm, _norm(existing.title or "")).ratio() > 0.92:
                            if _quality(case) > _quality(existing):
                                idx = buckets[bucket_key].index(existing)
                                buckets[bucket_key][idx] = case
                            is_duplicate = True
                            break
            
            if not is_duplicate:
                unique_map[fingerprint] = case
                if bucket_key not in buckets:
                    buckets[bucket_key] = []
                buckets[bucket_key].append(case)
                
        return list(unique_map.values())

    async def _async_process_single_req(self, req: Requirement) -> List[TestCase]:
        """Legacy method."""
        final_cases = []
        async for update in self.stream_generate([req]):
            if update["type"] == "result":
                final_cases.extend(update["data"])
        return final_cases

    async def _wrap_stream_iterator(self, it):
        """Helper to collect results from an async iterator for as_completed."""
        results = []
        async for x in it:
            results.append(x)
        return results

    def _augment_cases(self, req: Requirement) -> List[Dict[str, Any]]:
        """
        Create additional cases for interface/performance/boundary scenarios.
        兼容 extracted_entities 和 req_spec 为 dict 或对象两种情况。
        """
        augmented = []
        text = req.original_text.lower()

        # 兼容 dict 和对象
        entities = _to_extracted_entities(req.extracted_entities)
        req_spec = _to_req_spec(req.req_spec)

        # 1. Boundary Value Analysis (Dynamic from Constraints)
        constraints = getattr(entities, "constraints", [])
        if not isinstance(constraints, list):
            logger.warning(f"_augment_cases: constraints is not list, skipping. type={type(constraints)} value={constraints}")
            constraints = []

        for i, constraint in enumerate(constraints):
            if isinstance(constraint, str):
                parsed = _safe_json_loads(constraint)
                if isinstance(parsed, dict):
                    constraint = parsed
                else:
                    logger.warning(f"_augment_cases: constraint[{i}] is str but not json object, skipping. value={constraint}")
                    continue
            if not isinstance(constraint, dict):
                logger.warning(f"_augment_cases: constraint[{i}] is not dict, skipping. type={type(constraint)} value={constraint}")
                continue

            c_type = constraint.get("type")
            
            if c_type == "length_range":
                min_l = constraint.get("min", 0)
                max_l = constraint.get("max", 0)
                bs = self.synth.boundary_string(min_l, max_l)
                augmented.append({
                    "title": f"边界值测试: 长度 {min_l}-{max_l}",
                    "precondition": "系统运行中",
                    "steps": [
                        f"1. 输入长度为 {min_l} 和 {max_l} 的字符串 (有效)", 
                        f"2. 输入长度为 {max(0, min_l-1)} 和 {max_l+1} 的字符串 (无效)"
                    ],
                    "expected_result": "系统应根据边界规则进行验证",
                    "test_data": {"valid": {"min": bs["min"], "max": bs["max"]}, "invalid": {"min-1": bs["min-1"], "max+1": bs["max+1"]}},
                    "priority": "P1",
                    "type": "功能测试",
                    "methodology": ["边界值分析"]
                })
                logger.info(f"_augment_cases: Added length_range case. test_data type: {type(augmented[-1]['test_data'])}")
            
            elif c_type == "min_value":
                val = constraint.get("value", 0)
                augmented.append({
                    "title": f"边界值测试: 最小值 {val}",
                    "precondition": "系统就绪",
                    "steps": [f"1. 输入 {val} (有效)", f"2. 输入 {val-1} (无效)"],
                    "expected_result": "系统接受 >= 值的输入，拒绝 < 值的输入",
                    "test_data": {"valid": {"value": val}, "invalid": {"value": val - 1}},
                    "priority": "P1",
                    "type": "功能测试",
                    "methodology": ["边界值分析"]
                })
                logger.info(f"_augment_cases: Added min_value case. test_data type: {type(augmented[-1]['test_data'])}")
                
            elif c_type == "mandatory":
                augmented.append({
                    "title": "反向测试: 必填字段缺失",
                    "precondition": "表单已加载",
                    "steps": ["1. 留空必填字段", "2. 提交表单"],
                    "expected_result": "显示错误提示信息",
                    "test_data": {"invalid": {"field": "null/empty"}},
                    "priority": "P1",
                    "type": "功能测试",
                    "methodology": ["等价类划分"]
                })
                logger.info(f"_augment_cases: Added mandatory case. test_data type: {type(augmented[-1]['test_data'])}")

        # 2. Legacy Fallback
        if not augmented and ("长度" in text or "length" in text):
            bs = self.synth.boundary_string(8, 16)
            augmented.append({
                "title": "边界值测试: 长度 8-16 (默认)",
                "precondition": "系统运行中",
                "steps": ["1. 输入不同长度 of strings", "2. 提交"],
                "expected_result": "系统应根据边界规则进行验证",
                "test_data": {"valid": {"min": bs["min"], "max": bs["max"]}, "invalid": {"min-1": bs["min-1"], "max+1": bs["max+1"]}},
                "priority": "P2",
                "type": "功能测试",
                "methodology": ["边界值分析"]
            })
            logger.info(f"_augment_cases: Added legacy fallback case. test_data type: {type(augmented[-1]['test_data'])}")

        # 3. API Case
        if req_spec and req_spec.type.value == "interface":
            augmented.append({
                "title": "API: 请求/响应验证",
                "precondition": "API 服务可用",
                "steps": ["1. 发送带有有效负载的请求", "2. 验证响应代码和结构"],
                "expected_result": "HTTP 200; 结构匹配",
                "test_data": {"valid": {"payload": {"example": "value"}}, "invalid": {"payload": {"example": None}}},
                "priority": "P0",
                "type": "接口测试",
                "methodology": ["判定表"]
            })
            logger.info(f"_augment_cases: Added API case. test_data type: {type(augmented[-1]['test_data'])}")
            
        # 4. Performance Case
        if "响应时间" in text or "并发" in text or (req_spec and req_spec.type.value == "performance"):
            amounts = self.synth.positive_amounts()
            augmented.append({
                "title": "性能测试: 并发与响应时间基线",
                "precondition": "负载测试环境就绪",
                "steps": ["1. 模拟 100 个并发用户", "2. 测量响应时间和资源利用率"],
                "expected_result": "响应时间 < 500ms; CPU < 80%",
                "test_data": {"valid": {"users": 100, "baseline": amounts["min_positive"]}},
                "priority": "P1",
                "type": "性能测试",
                "methodology": ["性能基准测试"]
            })
            logger.info(f"_augment_cases: Added performance case. test_data type: {type(augmented[-1]['test_data'])}")
            
        logger.info(f"_augment_cases: returning {len(augmented)} cases: {augmented}")
        return augmented
