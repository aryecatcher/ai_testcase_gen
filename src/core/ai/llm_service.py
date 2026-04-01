import os
import json
import hashlib
import threading
import asyncio
from openai import OpenAI, AsyncOpenAI
from loguru import logger
from typing import List, Dict, Any
from .prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE, REFINE_PROMPT_TEMPLATE
from ...models.domain import Requirement, RequirementType
from .req_parser import RequirementParser
from .few_shots import get_examples
from data.storage import save_json, load_json


def _llm_cache_key(req_id: str, kg_constraints: str, scenarios: str) -> str:
    """Stable cache key (do not use builtins.hash — it varies between Python processes)."""
    h = hashlib.sha256()
    for part in (kg_constraints or "", scenarios or ""):
        h.update(part.encode("utf-8"))
    return f"{req_id}_{h.hexdigest()[:24]}"


class LLMService:
    def __init__(self, api_key: str = None, base_url: str = None, model: str = "deepseek-r1:7b"):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "ollama")
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
        self.model = model
        self.client = None
        self.async_client = None
        self.parser = RequirementParser()
        
        # 1. Pre-cache few-shots for performance
        self._few_shots_cache = {
            t: get_examples(t) for t in RequirementType
        }
        self._few_shots_cache[None] = "Examples: None"

        # 2. Persistent cache: Try to load from disk, fallback to in-memory
        self._cache = load_json("llm_cache") or {} 
        self._cache_lock = threading.Lock()
        
        # Always initialize client for local models (using dummy key if needed)
        if self.api_key:
            self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)
            self.async_client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)
        else:
            # Fallback for local dev if env var missing but intended for local
            if "localhost" in self.base_url:
                self.client = OpenAI(api_key="ollama", base_url=self.base_url)
                self.async_client = AsyncOpenAI(api_key="ollama", base_url=self.base_url)
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
        # 1. Early Cache Check: Before any processing
        # Use a simpler cache key for early check (before expensive prompt building)
        cache_key = _llm_cache_key(req.id, kg_constraints, scenarios)
        with self._cache_lock:
            if cache_key in self._cache:
                logger.info(f"Cache Hit for requirement: {req.id}")
                return self._cache[cache_key]

        # 2. Extract context - Prefer structured data
        parsed = self.parser.parse(req)
        parsed_struct = json.dumps(parsed, ensure_ascii=False, indent=2)
        
        # Use cached few-shots
        req_type = req.req_spec.type if (hasattr(req, "req_spec") and req.req_spec) else None
        few_shots = self._few_shots_cache.get(req_type, self._few_shots_cache[None])

        if not self.client:
            return self._mock_generate(req, parsed=parsed, scenarios=scenarios)

        # Prompt content optimization: prioritize cleaned_text, use shorter window
        context = req.cleaned_text or req.original_text
        module_path = req.req_spec.module_path if (hasattr(req, "req_spec") and req.req_spec) else "Unknown"
        priority = req.req_spec.priority if (hasattr(req, "req_spec") and req.req_spec) else "P2"

        # 3. Prompt construction
        prompt = USER_PROMPT_TEMPLATE.format(
            project_name=project_name,
            module_path=module_path,
            priority=priority,
            kg_constraints=kg_constraints,
            parsed_struct=parsed_struct,
            scenarios=scenarios,
            few_shots=few_shots,
            context=context[:4000] # Shorter context to reduce token count
        )
        
        try:
            logger.info(f"Sending request to LLM for requirement: {req.id}")
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0, # Deterministic for speed and stability
                response_format={"type": "json_object"}
            )
            content = response.choices[0].message.content
            # Robust JSON parsing and key normalization
            try:
                data = json.loads(content)
            except Exception:
                # Attempt to extract JSON object from content with fences or prefixes
                start = content.find("{")
                end = content.rfind("}")
                data = {}
                if start != -1 and end != -1 and end > start:
                    try:
                        data = json.loads(content[start:end+1])
                    except Exception as e_json:
                        logger.error(f"JSON extraction failed: {e_json}")
                        data = {}
            
            if isinstance(data, dict):
                normalized = { (k.strip() if isinstance(k, str) else k): v for k, v in data.items() }
                result = normalized.get("test_cases") or normalized.get("cases") or []
            else:
                result = []
            
            # 4. Update cache and persist with Thread Lock
            with self._cache_lock:
                self._cache[cache_key] = result
                save_json("llm_cache", self._cache)
            return result
        except Exception as e:
            logger.error(f"LLM generation failed: {e}")
            return self._mock_generate(req, parsed=parsed, scenarios=scenarios, error=str(e))

    async def async_generate_cases(self, req: Requirement, project_name: str = "Demo Project", kg_constraints: str = "None", scenarios: str = "None") -> List[Dict[str, Any]]:
        """
        Asynchronously generates test cases.
        """
        cache_key = _llm_cache_key(req.id, kg_constraints, scenarios)
        with self._cache_lock:
            if cache_key in self._cache:
                return self._cache[cache_key]

        if not self.async_client:
            return self._mock_generate(req, scenarios=scenarios)

        parsed = self.parser.parse(req)
        parsed_struct = json.dumps(parsed, ensure_ascii=False, indent=2)
        req_type = req.req_spec.type if (hasattr(req, "req_spec") and req.req_spec) else None
        few_shots = self._few_shots_cache.get(req_type, self._few_shots_cache[None])
        context = req.cleaned_text or req.original_text
        module_path = req.req_spec.module_path if (hasattr(req, "req_spec") and req.req_spec) else "Unknown"
        priority = req.req_spec.priority if (hasattr(req, "req_spec") and req.req_spec) else "P2"

        prompt = USER_PROMPT_TEMPLATE.format(
            project_name=project_name,
            module_path=module_path,
            priority=priority,
            kg_constraints=kg_constraints,
            parsed_struct=parsed_struct,
            scenarios=scenarios,
            few_shots=few_shots,
            context=context[:4000]
        )

        try:
            response = await self.async_client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0,
                response_format={"type": "json_object"}
            )
            content = response.choices[0].message.content
            try:
                data = json.loads(content)
            except:
                start, end = content.find("{"), content.rfind("}")
                data = json.loads(content[start:end+1]) if (start != -1 and end != -1) else {}

            if isinstance(data, dict):
                normalized = { (k.strip() if isinstance(k, str) else k): v for k, v in data.items() }
                result = normalized.get("test_cases") or normalized.get("cases") or []
            else:
                result = []

            with self._cache_lock:
                self._cache[cache_key] = result
                save_json("llm_cache", self._cache)
            return result
        except Exception as e:
            logger.error(f"Async LLM generation failed: {e}")
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
                temperature=0.0, # Faster and more deterministic
                response_format={"type": "json_object"}
            )
            content = response.choices[0].message.content
            data = json.loads(content)
            return data.get("test_cases", [])
        except Exception as e:
            logger.error(f"LLM refinement failed: {e}")
            return []

    async def async_refine_cases(self, failed_cases: List[Dict[str, Any]], feedback: str) -> List[Dict[str, Any]]:
        """
        Asynchronously refines test cases.
        """
        if not self.async_client:
            return failed_cases

        prompt = REFINE_PROMPT_TEMPLATE.format(
            feedback=feedback,
            failed_cases=json.dumps(failed_cases, ensure_ascii=False)
        )

        try:
            response = await self.async_client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0,
                response_format={"type": "json_object"}
            )
            content = response.choices[0].message.content
            data = json.loads(content)
            return data.get("test_cases", [])
        except Exception as e:
            logger.error(f"Async LLM refinement failed: {e}")
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
