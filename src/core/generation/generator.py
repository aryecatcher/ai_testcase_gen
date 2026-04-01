import concurrent.futures
import asyncio
from typing import List, Dict, Any
from loguru import logger
from ..ai.llm_service import LLMService
from ..kg.graph_service import KnowledgeGraphService
from ...models.domain import Requirement, TestCase, TestInstruction, BusinessLogic, TestDataSets, TestCaseStatus
from .data_synthesizer import DataSynthesizer
from .validators import ValidationInterceptor
from ..ai.req_parser import RequirementParser
from ..ai.optimizer import CaseOptimizer
import hashlib
from difflib import SequenceMatcher

class TestCaseGenerator:
    def __init__(self, llm_service: LLMService, kg_service: KnowledgeGraphService, max_concurrency: int = 20):
        self.llm_service = llm_service
        self.kg_service = kg_service
        self.max_concurrency = max_concurrency
        self.synth = DataSynthesizer()
        self.validator = ValidationInterceptor()
        self.parser = RequirementParser()
        self.optimizer = CaseOptimizer()
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
                # We are already in an event loop, let's use a thread to run the async function
                # Or if we want to be more direct: return the coroutine and let the caller await it.
                # But to maintain sync signature, we use a trick or just warn.
                # The recommendation was loop.run_until_complete but that doesn't work if already running.
                # Actually, if we are in an event loop, the caller SHOULD be calling async_generate.
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
        total_reqs = len(requirements)
        if total_reqs == 0:
            return []
        completed_reqs = 0
        
        # Batch processing with concurrency limit
        semaphore = asyncio.Semaphore(self.max_concurrency)
        
        async def _process_with_progress(req: Requirement):
            nonlocal completed_reqs
            async with semaphore:
                try:
                    res = await self._async_process_single_req(req)
                    return res
                finally:
                    async with self._lock:
                        completed_reqs += 1
                        current_completed = completed_reqs
                    if progress_callback:
                        progress_callback(current_completed, total_reqs)

        tasks = [_process_with_progress(req) for req in requirements]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        raw_test_cases = []
        errors = 0
        for res in results:
            if isinstance(res, list):
                raw_test_cases.extend(res)
            elif isinstance(res, Exception):
                errors += 1
                logger.error(f"Async generation error: {res}")
        if errors:
            logger.warning(f"Generation finished with {errors}/{total_reqs} requirement(s) raising exceptions; partial results returned.")
        
        # Deduplication (Reduce Phase)
        final_cases = self._deduplicate_cases(raw_test_cases)
        return final_cases

    def _deduplicate_cases(self, cases: List[TestCase]) -> List[TestCase]:
        """
        Reduce phase: Removes duplicates using semantic hashing and fuzzy matching.
        Also merges cases from split table parts based on title similarity.
        Handles 'Logical Grouping' for PENDING items.
        """
        unique_map = {}
        pending_cases = [] # Store incomplete logic fragments
        
        # 1. First Pass: Separate valid cases from pending fragments using Enum status
        valid_cases = []
        for case in cases:
            if case.status == TestCaseStatus.PENDING:
                pending_cases.append(case)
            else:
                valid_cases.append(case)
                
        # 2. Logic Reconstruction: Convert PENDING to a Constraint Check Case
        for pending in pending_cases:
            if pending.title:
                pending.title = pending.title.replace("PENDING_LOGIC", "Constraint Check")
            pending.status = TestCaseStatus.COMPLETE
            valid_cases.append(pending)

        # 3. Deduplication on Valid Cases
        # Optimization: Use bucket-based fuzzy matching with better prefixes
        buckets = {} # bucket_key -> list of cases
        
        for case in valid_cases:
            # Structural Hash (Action + Expected Result)
            steps_str = "".join(case.test_instruction.steps)
            content_sig = f"{steps_str}_{case.test_instruction.expected_result}"
            fingerprint = hashlib.md5(content_sig.encode()).hexdigest()
            
            # Bucketing strategy: dimension + title prefix (first 6 chars for more specificity)
            title_prefix = (case.title or "")[:6]
            bucket_key = f"{case.dimension}_{title_prefix}"
            
            is_duplicate = False
            
            # First check structural fingerprint
            if fingerprint in unique_map:
                is_duplicate = True
            else:
                # Then check fuzzy similarity within the same bucket
                if bucket_key in buckets:
                    for existing in buckets[bucket_key]:
                        # SequenceMatcher is slow, so we only do it for potential matches
                        if SequenceMatcher(None, case.title, existing.title).ratio() > 0.9:
                            is_duplicate = True
                            break
            
            if not is_duplicate:
                unique_map[fingerprint] = case
                if bucket_key not in buckets:
                    buckets[bucket_key] = []
                buckets[bucket_key].append(case)
                
        return list(unique_map.values())

    async def _async_process_single_req(self, req: Requirement) -> List[TestCase]:
        """
        Asynchronous processing of a single requirement.
        Includes KG caching and similarity check.
        """
        # 0. Similarity Check: Avoid LLM for duplicate requirements in the same session
        # Use a more comprehensive hash key: text + priority + dimension
        req_type = req.req_spec.type.value if req.req_spec else "unknown"
        req_priority = req.req_spec.priority if req.req_spec else "P2"
        context_str = f"{req.original_text}_{req_type}_{req_priority}"
        req_hash = hashlib.md5(context_str.encode()).hexdigest()
        
        if req_hash in self._similarity_cache:
            logger.info(f"Similarity Hit for {req.id}")
            # Clone cases with new ID to maintain independence
            cached_cases = self._similarity_cache[req_hash]
            return [tc.model_copy(update={"related_req_id": req.id}) for tc in cached_cases]

        generated_cases = []
        try:
            logger.info(f"Processing requirement (Async): {req.id}")
            
            # 1. Retrieve Context from KG with Caching
            module = req.extracted_entities.module or "Unknown"
            # Key by module + requirement type for finer granularity
            kg_cache_key = (module, req_type)
            
            if kg_cache_key in self._kg_cache:
                constraints, scenarios_text = self._kg_cache[kg_cache_key]
            else:
                constraints = self.kg_service.get_related_constraints(module)
                scenarios_list = self.kg_service.expand_scenarios(module)
                scenarios_text = ""
                if scenarios_list:
                    scenarios_text = "\n".join([f"- {s.get('type')}: {s.get('name')} ({s.get('logic')})" for s in scenarios_list])
                self._kg_cache[kg_cache_key] = (constraints, scenarios_text)
                
                # Simple TTL or maxsize limit (optional: using LRU cache would be better but requires more dependencies)
                if len(self._kg_cache) > 100:
                    # Very basic cache eviction
                    first_key = next(iter(self._kg_cache))
                    del self._kg_cache[first_key]

            # 2. Call Async LLM
            raw_cases = await self.llm_service.async_generate_cases(
                req=req,
                kg_constraints=constraints,
                scenarios=scenarios_text
            )
            
            if not isinstance(raw_cases, list):
                raw_cases = []

            # 2.b Augmentation & Validation
            raw_cases = [self.validator.validate_case(rc) for rc in raw_cases]
            raw_cases += self._augment_cases(req)
            
            # 2.c Async Optimize via feedback loop
            feedback_text = self.optimizer.evaluate_gaps(raw_cases)
            if feedback_text and self.llm_service.async_client:
                refined = await self.llm_service.async_refine_cases(raw_cases, feedback_text)
                if refined and isinstance(refined, list):
                    raw_cases = refined
            
            # 3. Convert to TestCase Objects
            final_cases = []
            for raw in raw_cases:
                if not isinstance(raw, dict): continue
                try:
                    td_raw = raw.get("test_data", {})
                    test_data = TestDataSets(
                        valid=td_raw.get("valid", {}) if isinstance(td_raw, dict) else {},
                        invalid=td_raw.get("invalid", {}) if isinstance(td_raw, dict) else {}
                    )
                    
                    steps_raw = raw.get("steps", [])
                    steps_final = []
                    if isinstance(steps_raw, list):
                        for s in steps_raw:
                            if isinstance(s, dict):
                                action = s.get("action", "") or s.get("step", "")
                                result = s.get("result", "")
                                steps_final.append(f"{action} -> {result}" if result else action)
                            else:
                                steps_final.append(str(s))
                    
                    # Determine status based on presence of PENDING_LOGIC
                    tc_title = raw.get("title", "Generated Case")
                    tc_status = TestCaseStatus.COMPLETE
                    if "PENDING_LOGIC" in tc_title or "PENDING_LOGIC" in str(raw.get("related_req_id", "")):
                        tc_status = TestCaseStatus.PENDING
                    
                    tc = TestCase(
                        related_req_id=req.id,
                        title=tc_title,
                        test_instruction=TestInstruction(
                            pre_condition=raw.get("precondition", "None"),
                            steps=steps_final,
                            expected_result=raw.get("expected_result", "Success"),
                            test_data_sets=test_data
                        ),
                        methodology=raw.get("methodology", ["LLM"]),
                        dimension=raw.get("type", "Functional"),
                        priority=raw.get("priority", "P2"),
                        status=tc_status
                    )
                    final_cases.append(tc)
                except Exception as e_conv:
                    logger.warning(f"Case conversion error: {e_conv}")
            
            # Store in similarity cache
            self._similarity_cache[req_hash] = final_cases
            return final_cases

        except Exception as e:
            logger.error(f"Async processing failed for {req.id}: {e}")
            return []


    def _augment_cases(self, req: Requirement) -> List[Dict[str, Any]]:
        """
        Create additional cases for interface/performance/boundary scenarios.
        Uses extracted entities from the Ingestor for precise generation.
        """
        augmented = []
        text = req.original_text.lower()
        entities = req.extracted_entities
        
        # 1. Boundary Value Analysis (Dynamic from Constraints)
        for constraint in entities.constraints:
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

        # 2. Legacy Fallback (if no specific constraints found but keywords exist)
        if not augmented and ("长度" in text or "length" in text):
             bs = self.synth.boundary_string(8, 16)
             augmented.append({
                "title": "边界值测试: 长度 8-16 (默认)",
                "precondition": "系统运行中",
                "steps": ["1. 输入不同长度的字符串", "2. 提交"],
                "expected_result": "系统应根据边界规则进行验证",
                "test_data": {"valid": {"min": bs["min"], "max": bs["max"]}, "invalid": {"min-1": bs["min-1"], "max+1": bs["max+1"]}},
                "priority": "P2",
                "type": "功能测试",
                "methodology": ["边界值分析"]
            })

        # 3. API Case
        if req.req_spec and req.req_spec.type.value == "interface":
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
            
        # 4. Performance Case
        if "响应时间" in text or "并发" in text or (req.req_spec and req.req_spec.type.value == "performance"):
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
            
        return augmented
