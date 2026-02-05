import concurrent.futures
from typing import List, Dict, Any
from loguru import logger
from ..ai.llm_service import LLMService
from ..kg.graph_service import KnowledgeGraphService
from ...models.domain import Requirement, TestCase, TestInstruction, BusinessLogic, TestDataSets
from .data_synthesizer import DataSynthesizer
from .validators import ValidationInterceptor
from ..ai.req_parser import RequirementParser
from ..ai.optimizer import CaseOptimizer
import hashlib
from difflib import SequenceMatcher

class TestCaseGenerator:
    def __init__(self, llm_service: LLMService, kg_service: KnowledgeGraphService):
        self.llm_service = llm_service
        self.kg_service = kg_service
        self.synth = DataSynthesizer()
        self.validator = ValidationInterceptor()
        self.parser = RequirementParser()
        self.optimizer = CaseOptimizer()

    def generate(self, requirements: List[Requirement]) -> List[TestCase]:
        raw_test_cases = []
        
        # Parallel Execution (Map Phase)
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            future_to_req = {executor.submit(self._process_single_req, req): req for req in requirements}
            
            for future in concurrent.futures.as_completed(future_to_req):
                req = future_to_req[future]
                try:
                    result = future.result()
                    raw_test_cases.extend(result)
                except Exception as e:
                    logger.error(f"Error processing requirement {req.id}: {e}")
        
        # Deduplication (Reduce Phase)
        final_cases = self._deduplicate_cases(raw_test_cases)
        return final_cases

    def _deduplicate_cases(self, cases: List[TestCase]) -> List[TestCase]:
        """
        Reduce phase: Removes duplicates using semantic hashing and fuzzy matching.
        Also merges cases from split table parts based on title similarity.
        Handles 'Logical Grouping' for PENDING_LOGIC items.
        """
        unique_map = {}
        pending_logics = [] # Store incomplete logic fragments
        
        # 1. First Pass: Separate valid cases from pending fragments
        valid_cases = []
        for case in cases:
            if "PENDING_LOGIC" in case.title or "PENDING_LOGIC" in case.related_req_id:
                pending_logics.append(case)
            else:
                valid_cases.append(case)
                
        # 2. Logic Reconstruction (Heuristic): Try to attach pending logic to nearest valid case
        # In a full implementation, we would use vector similarity.
        # Here we use simple keyword matching.
        for pending in pending_logics:
            # Try to find a valid case that matches the constraints in pending
            # e.g. pending has "length < 8", valid has "password field"
            # For now, we just append them as "Constraint Checks" to be safe, 
            # or merge if we find a very strong match.
            
            # Simple fallback: Convert PENDING to a Constraint Check Case
            pending.title = pending.title.replace("PENDING_LOGIC", "Constraint Check")
            valid_cases.append(pending)

        # 3. Deduplication on Valid Cases
        for case in valid_cases:
            # Structural Hash (Action + Expected Result)
            steps_str = "".join(case.test_instruction.steps)
            content_sig = f"{steps_str}_{case.test_instruction.expected_result}"
            fingerprint = hashlib.md5(content_sig.encode()).hexdigest()
            
            if fingerprint in unique_map:
                continue
                
            # Fuzzy Title Match
            is_duplicate = False
            for existing_fp, existing in unique_map.items():
                if SequenceMatcher(None, case.title, existing.title).ratio() > 0.9:
                    is_duplicate = True
                    break
            
            if not is_duplicate:
                unique_map[fingerprint] = case
                
        return list(unique_map.values())

    def _process_single_req(self, req: Requirement) -> List[TestCase]:
        """
        Process a single requirement (isolated for parallelism).
        """
        generated_cases = []
        try:
            # 1. Retrieve Context from KG
            module = req.extracted_entities.module or "Unknown"
            constraints = self.kg_service.get_related_constraints(module)
            scenarios_list = self.kg_service.expand_scenarios(module)
            scenarios_text = ""
            if scenarios_list:
                scenarios_text = "\n".join([f"- {s.get('type')}: {s.get('name')} ({s.get('logic')})" for s in scenarios_list])
            
            # 2. Call LLM
            raw_cases = self.llm_service.generate_cases(
                req=req,
                kg_constraints=constraints,
                scenarios=scenarios_text
            )
            # 2.b Add synthesized cases based on heuristics (Boundary/Performance/API)
            raw_cases = [self.validator.validate_case(rc) for rc in raw_cases]
            raw_cases += self._augment_cases(req)
            # 2.c Optimize via feedback loop (if gaps found)
            feedback_text = self.optimizer.evaluate_gaps(raw_cases)
            if feedback_text and self.llm_service.client:
                refined = self.llm_service.refine_cases(raw_cases, feedback_text)
                if refined:
                    raw_cases = refined
            
            # 3. Convert to TestCase Objects
            for raw in raw_cases:
                # Construct TestDataSets safely
                td_raw = raw.get("test_data", {})
                test_data = TestDataSets(
                    valid=td_raw.get("valid", {}),
                    invalid=td_raw.get("invalid", {})
                )
                
                tc = TestCase(
                    related_req_id=req.id,
                    title=raw.get("title", f"Test for {req.id}"),
                    test_instruction=TestInstruction(
                        pre_condition=raw.get("precondition", "None"),
                        steps=raw.get("steps", []),
                        expected_result=raw.get("expected_result", ""),
                        test_data_sets=test_data
                    ),
                    methodology=raw.get("methodology", []),
                    dimension=raw.get("type", "Functional"),
                    priority=raw.get("priority", "P2")
                )
                generated_cases.append(tc)
        except Exception as e:
            logger.error(f"Error inside thread for {req.id}: {e}")
            
        return generated_cases

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
                min_l = constraint.get("min")
                max_l = constraint.get("max")
                bs = self.synth.boundary_string(min_l, max_l)
                augmented.append({
                    "title": f"边界值测试: 长度 {min_l}-{max_l}",
                    "precondition": "系统运行中",
                    "steps": [f"1. 输入长度为 {min_l} 和 {max_l} 的字符串 (有效)", f"2. 输入长度为 {min_l-1} 和 {max_l+1} 的字符串 (无效)"],
                    "expected_result": "系统应根据边界规则进行验证",
                    "test_data": {"valid": {"min": bs["min"], "max": bs["max"]}, "invalid": {"min-1": bs["min-1"], "max+1": bs["max+1"]}},
                    "priority": "P1",
                    "type": "功能测试",
                    "methodology": ["边界值分析"]
                })
            
            elif c_type == "min_value":
                val = constraint.get("value")
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
