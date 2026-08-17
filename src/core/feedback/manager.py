from typing import List, Dict, Any
from loguru import logger
from ..ai.llm_service import LLMService
from ...models.domain import TestCase
from ..generation.validators import ValidationInterceptor

class FeedbackManager:
    def __init__(self, llm_service: LLMService):
        self.llm_service = llm_service
        self.validator = ValidationInterceptor()
        # Ideally we inject this, but for now we instantiate or import global
        from ..kg.graph_service import KnowledgeGraphService
        self.kg_service = KnowledgeGraphService()

    def refine_cases(self, failed_cases: List[TestCase], feedback: str) -> List[TestCase]:
        """
        Refines the provided failed test cases based on user feedback.
        Also attempts to learn new rules from feedback.
        """
        # 1. KG Self-Correction (Heuristic)
        # If feedback contains "rule" or "should be", we assume it's a constraint update
        if "规则" in feedback or "should" in feedback or "必须" in feedback:
            # Simple heuristic: Associate this rule with the module of the first case
            if failed_cases:
                # We need to find the module. In a real app, TestCase should have module link.
                # Here we assume we can find it via the related requirement ID or passed context.
                # For MVP, let's just log it or try to infer.
                logger.info(f"Detected potential rule update in feedback: {feedback}")
                # In a real scenario, we would parse the 'module' from the feedback or the case context
                # For now, let's skip automatic KG update to avoid noise, 
                # or we could try to find the module from the case title if it matches KG.
                pass

        # Convert objects to dicts for the LLM
        raw_failed = [tc.model_dump() for tc in failed_cases] # pydantic v2 uses model_dump
        
        logger.info(f"Refining {len(failed_cases)} cases with feedback: {feedback}")
        refined_raw = self.llm_service.refine_cases(raw_failed, feedback)
        
        refined_objs = []
        for raw in refined_raw:
            try:
                td_raw = raw.get("test_data", {})
                tc = TestCase(
                    related_req_id=raw.get("related_req_id", "unknown"), # LLM might lose this, need to preserve it
                    title=self.validator.clean_text(raw.get("title", "Refined Case")) or "Refined Case",
                    test_instruction={
                        "pre_condition": self.validator.clean_text(raw.get("precondition", "None")) or "系统已完成基础部署，测试数据准备完成。",
                        "steps": self.validator.normalize_steps(raw.get("steps", []), raw.get("title", "Refined Case")),
                        "expected_result": self.validator.clean_text(raw.get("expected_result", "")) or "系统按照需求规则处理，并返回明确结果。",
                        "test_data_sets": {
                            "valid": td_raw.get("valid", {}) if isinstance(td_raw, dict) else {},
                            "invalid": td_raw.get("invalid", {}) if isinstance(td_raw, dict) else {}
                        }
                    },
                    methodology=raw.get("methodology", []),
                    dimension=raw.get("type", "Functional"),
                    priority=raw.get("priority", "P2"),
                    review_status="Refined"
                )
                
                # Try to preserve related_req_id from original if possible
                # Simple heuristic: map by index if count matches, otherwise just use what's there
                # Or better, pass related_req_id in the prompt explicitly per case.
                
                refined_objs.append(tc)
            except Exception as e:
                logger.error(f"Error converting refined case: {e}")
                
        return refined_objs
