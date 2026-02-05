from typing import List, Dict, Any
from loguru import logger
from ..ai.llm_service import LLMService
from ...models.domain import TestCase, TestInstruction, TestDataSets

class FeedbackManager:
    def __init__(self, llm_service: LLMService):
        self.llm_service = llm_service
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
                # Construct TestDataSets safely
                td_raw = raw.get("test_data", {})
                test_data = TestDataSets(
                    valid=td_raw.get("valid", {}),
                    invalid=td_raw.get("invalid", {})
                )
                
                # We assume the LLM returns the full structure. 
                # If it's a refinement, we might want to keep the old ID or generate a new one?
                # Usually refinement implies updating the same logical case, but technically it's a new version.
                # Let's generate a new ID for the refined version but link it if we had a parent_id field (we don't yet).
                
                tc = TestCase(
                    related_req_id=raw.get("related_req_id", "unknown"), # LLM might lose this, need to preserve it
                    title=raw.get("title", "Refined Case"),
                    test_instruction=TestInstruction(
                        pre_condition=raw.get("precondition", "None"),
                        steps=raw.get("steps", []),
                        expected_result=raw.get("expected_result", ""),
                        test_data_sets=test_data
                    ),
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
