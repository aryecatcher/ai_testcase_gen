import os
import json
from openai import OpenAI
from loguru import logger
from typing import List, Dict, Any
from .prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE, REFINE_PROMPT_TEMPLATE
from ...models.domain import Requirement
from .req_parser import RequirementParser
from .few_shots import get_examples

class LLMService:
    def __init__(self, api_key: str = None, base_url: str = None, model: str = "deepseek-r1:7b"):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "ollama")
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
        self.model = model
        self.client = None
        self.parser = RequirementParser()
        self._cache = {} # Simple in-memory cache
        
        # Always initialize client for local models (using dummy key if needed)
        if self.api_key:
            self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        else:
            # Fallback for local dev if env var missing but intended for local
            if "localhost" in self.base_url:
                self.client = OpenAI(api_key="ollama", base_url=self.base_url)
            else:
                logger.warning("No OpenAI API Key provided. Running in MOCK mode.")

    def check_connection(self) -> Dict[str, Any]:
        """
        Checks if the LLM connection is valid.
        """
        if not self.client:
            return {"status": "error", "message": "No API Key provided (Mock Mode)"}
            
        try:
            # Simple test call
            self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "Test"}],
                max_tokens=5
            )
            return {"status": "success", "message": f"Connected to {self.model}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def generate_cases(self, req: Requirement, project_name: str = "Demo Project", kg_constraints: str = "None", scenarios: str = "None") -> List[Dict[str, Any]]:
        """
        Generates test cases based on the requirement using LLM.
        """
        parsed = self.parser.parse(req)
        parsed_struct = json.dumps(parsed, ensure_ascii=False, indent=2)
        few_shots = get_examples(req.req_spec.type if req.req_spec else None) if hasattr(req, "req_spec") and req.req_spec else "Examples: None"
        if not self.client:
            return self._mock_generate(req, parsed=parsed, scenarios=scenarios)

        context = req.cleaned_text or req.original_text
        module_path = req.req_spec.module_path if req.req_spec else "Unknown"
        priority = req.req_spec.priority if req.req_spec else "P2"

        prompt = USER_PROMPT_TEMPLATE.format(
            project_name=project_name,
            module_path=module_path,
            priority=priority,
            kg_constraints=kg_constraints,
            parsed_struct=parsed_struct,
            scenarios=scenarios,
            few_shots=few_shots,
            context=context[:6000]
        )
        
        # Cache Check
        cache_key = hash(prompt)
        if cache_key in self._cache:
            logger.info(f"Cache Hit for requirement: {req.id}")
            return self._cache[cache_key]
        
        try:
            logger.info(f"Sending request to LLM for requirement: {req.id}")
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
                response_format={"type": "json_object"}
            )
            content = response.choices[0].message.content
            data = json.loads(content)
            result = data.get("test_cases", [])
            
            # Update Cache
            self._cache[cache_key] = result
            return result
        except Exception as e:
            logger.error(f"LLM generation failed: {e}")
            return self._mock_generate(req, parsed=parsed, scenarios=scenarios, error=str(e))

    def refine_cases(self, failed_cases: List[Dict[str, Any]], feedback: str) -> List[Dict[str, Any]]:
        """
        Refines failed test cases based on user feedback.
        """
        if not self.client:
            return failed_cases # Return original in mock mode

        prompt = REFINE_PROMPT_TEMPLATE.format(
            feedback=feedback,
            failed_cases=json.dumps(failed_cases, ensure_ascii=False)
        )

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
                response_format={"type": "json_object"}
            )
            content = response.choices[0].message.content
            data = json.loads(content)
            return data.get("test_cases", [])
        except Exception as e:
            logger.error(f"LLM refinement failed: {e}")
            return []

    def _mock_generate(self, req: Requirement, parsed=None, scenarios=None, error=None) -> List[Dict[str, Any]]:
        """
        Smart Mock Logic:
        If the requirement text already looks like a test case (contains Steps, Expected Result),
        extract and use that content instead of generic placeholders.
        """
        logger.info("Generating Mock Test Cases (Smart Fallback)")
        title_suffix = " (Mock)"
        if error:
            title_suffix += " [Error Fallback]"
            
        text = req.original_text
        
        # 1. Try to extract columns if text is pipe-separated (from Markdown table)
        # Assuming format: ID | Module | Priority | Title | Precondition | Steps | Expected | Data
        parts = [p.strip() for p in text.split('|')]
        
        extracted_steps = []
        extracted_expected = ""
        extracted_pre = "System initialized"
        extracted_title = f"Verify {text[:30].strip()}..."
        extracted_priority = "P2"
        
        # Heuristic mapping based on standard columns seen in user data
        if len(parts) >= 6:
            # Try to find which column is which based on content length or keywords
            # Typical: ID | Module | Prio | Title | Pre | Steps | Expected | Data
            # Index:   0  | 1      | 2    | 3     | 4   | 5     | 6        | 7
            
            # Title is usually 4th (index 3)
            if len(parts) > 3 and len(parts[3]) > 5:
                extracted_title = parts[3]
                
            # Priority usually short P0/P1 at index 2
            if len(parts) > 2 and parts[2].upper() in ["P0", "P1", "P2", "P3"]:
                extracted_priority = parts[2].upper()
                
            # Steps usually index 5 or 6 (long text)
            # Let's look for numbered lists "1. " in parts
            for p in parts:
                if "1." in p and "2." in p:
                    extracted_steps = [s.strip() for s in p.split('\n') if s.strip()]
                    # Cleanup: remove empty strings
                    extracted_steps = [s for s in extracted_steps if len(s) > 2]
                    
            # If no numbered list found, take the longest part as steps
            if not extracted_steps and len(parts) > 5:
                extracted_steps = [parts[5]] # Fallback to index 5
                
            # Expected result usually next to steps
            if len(parts) > 6:
                extracted_expected = parts[6]
                
            # Precondition usually before steps
            if len(parts) > 4:
                extracted_pre = parts[4]

        # 2. Fallback if no table structure found
        if not extracted_steps:
            extracted_steps = [
                "1. Navigate to the relevant module.",
                "2. Perform the action described: " + text[:50] + "...",
                "3. Validate the output."
            ]
            extracted_expected = "System behaves as required."

        return [
            {
                "title": f"{extracted_title}{title_suffix}",
                "precondition": extracted_pre,
                "steps": extracted_steps,
                "expected_result": extracted_expected,
                "test_data": {
                    "valid": {"input": "See extracted constraints"},
                    "invalid": {"input": "Invalid input per constraints"}
                },
                "priority": extracted_priority,
                "type": req.req_spec.type.value if req.req_spec else "functional",
                "methodology": ["Heuristic Extraction (Mock)"]
            }
        ]
