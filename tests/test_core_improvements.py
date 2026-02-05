import sys
import os
# Add project root to path (assuming this script is in tests/)
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)

from src.core.ingestion.ingestor import RequirementIngestor
from src.core.kg.graph_service import KnowledgeGraphService
from src.core.generation.generator import TestCaseGenerator
from src.core.ai.llm_service import LLMService
from src.models.domain import Requirement

def test_improvements():
    print("=== Testing Core Improvements ===")
    
    # 1. Test Ingestor (NLP)
    ingestor = RequirementIngestor()
    # Mock file reading by creating a requirement directly or mocking doc_processor
    # But ingestor.ingest needs a file. Let's test _enrich_requirement directly.
    
    req_text = "登录模块必须支持密码长度为 8-16 位，且输入错误 3 次后锁定账号。用户中心核心功能。"
    req = Requirement(original_text=req_text, ingestion_metadata={"source_file": "test.txt"})
    
    print(f"\n[Ingestor] Processing text: {req_text}")
    ingestor._enrich_requirement(req)
    print(f"Extracted Module: {req.extracted_entities.module}")
    print(f"Extracted Feature: {req.extracted_entities.feature}")
    print(f"Extracted Constraints: {req.extracted_entities.constraints}")
    
    # 2. Test Graph Service
    kg = KnowledgeGraphService()
    print(f"\n[Graph Service] Querying constraints for 'Login'")
    constraints = kg.get_related_constraints("Login")
    print(f"Constraints found:\n{constraints}")
    
    print(f"\n[Graph Service] Expanding scenarios for 'Login'")
    scenarios = kg.expand_scenarios("Login")
    for s in scenarios:
        print(f"- Scenario: {s['name']} ({s['type']})")

    # 3. Test Generator (Augmentation)
    # We need a mocked LLM service
    llm = LLMService() # Will warn no API key, which is fine
    generator = TestCaseGenerator(llm, kg)
    
    print(f"\n[Generator] Augmenting cases based on extracted constraints")
    augmented_cases = generator._augment_cases(req)
    for case in augmented_cases:
        print(f"- Generated Case: {case['title']}")
        print(f"  Steps: {case['steps']}")
        print(f"  Data: {case['test_data']}")

if __name__ == "__main__":
    test_improvements()
