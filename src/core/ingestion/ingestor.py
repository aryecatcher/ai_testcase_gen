import re
import jieba
import jieba.posseg as pseg
from typing import List
from pathlib import Path
from loguru import logger
from .doc_processor import DocProcessor
from ...models.domain import Requirement, IngestionMetadata, ExtractedEntities, ReqSpec, RequirementType

class RequirementIngestor:
    def __init__(self):
        self.doc_processor = DocProcessor()
        # Initialize jieba
        jieba.initialize()

    def ingest(self, file_path: str) -> List[Requirement]:
        """
        Ingests a file and converts it into a list of Requirement objects.
        Now supports parsing Docling's structured table data to extract individual test case rows.
        """
        try:
            chunks = self.doc_processor.read_file(file_path)
            requirements = []
            
            for chunk in chunks:
                content = chunk["content"]
                metadata = chunk["metadata"]
                
                # Check for structured table data in Docling metadata
                raw_content = metadata.get("raw_content", {})
                
                # Handle Excel specifically
                if metadata.get("raw_content", {}).get("type") == "excel":
                    table_requirements = self._extract_from_excel(raw_content["data"], file_path)
                # Handle Swagger specifically
                elif metadata.get("type") == "swagger":
                    # For swagger, we already have text content in markdown, so we might rely on whole-doc processing
                    # But we can also do structured extraction if needed. For now, markdown is fine.
                    table_requirements = [] 
                else:
                    # If we have structured tables from Docling, try to extract rows
                    table_requirements = self._extract_from_tables(raw_content, file_path)
                
                if table_requirements:
                    requirements.extend(table_requirements)
                else:
                    # Fallback to whole-doc processing (now chunked)
                    # We inject global context into each chunk to prevent "Lost in the Middle"
                    chunk_index = metadata.get("chunk_index", 0)
                    total_chunks = metadata.get("total_chunks", 1)
                    
                    # Context Injection (Contextual Enrichment)
                    # Add "Logical Map" header
                    global_context_header = f"--- Logic Fragment {chunk_index + 1}/{total_chunks} ---\n"
                    if chunk_index > 0:
                        global_context_header += f"[Previous Context]: ...continued from Part {chunk_index}...\n"
                    
                    full_text = global_context_header + content
                    
                    req = Requirement(
                        original_text=full_text,
                        ingestion_metadata=IngestionMetadata(
                            source_file=str(Path(file_path).name),
                            parsing_confidence=0.85 # Slightly lower than table extraction
                        )
                    )
                    self._enrich_requirement(req)
                    
                    # Mark potential pending logic if no verbs found
                    if not req.extracted_entities.feature and not req.extracted_entities.constraints:
                         req.id = f"PENDING_LOGIC-{chunk_index}"
                    else:
                         # Ensure unique ID for chunks
                         if not req.id:
                            req.id = f"REQ-CHUNK-{chunk_index}"
                            
                    if req.req_spec:
                        req.req_spec.req_id = req.id
                            
                    requirements.append(req)
                
            return requirements
        except Exception as e:
            logger.error(f"Ingestion failed for {file_path}: {e}")
            raise

    def _extract_from_excel(self, excel_data: dict, file_path: str) -> List[Requirement]:
        """
        Extracts requirements from Excel data (sheet -> rows).
        """
        extracted_reqs = []
        for sheet, rows in excel_data.items():
            for row in rows:
                # Convert row dict to text
                # Filter empty values
                parts = [f"{k}: {v}" for k, v in row.items() if v]
                if not parts: continue
                
                full_text = f"Sheet: {sheet} | " + " | ".join(parts)
                
                # Check for ID
                req_id = str(row.get("ID") or row.get("id") or row.get("Case ID") or "")
                
                req = Requirement(
                    id=req_id if req_id else None, # Let domain model generate if empty
                    original_text=full_text,
                    ingestion_metadata=IngestionMetadata(
                        source_file=str(Path(file_path).name),
                        parsing_confidence=0.95
                    )
                )
                self._enrich_requirement(req)
                extracted_reqs.append(req)
        return extracted_reqs

    def _extract_from_tables(self, raw_content: dict, file_path: str) -> List[Requirement]:
        """
        Extracts requirements from tables in Docling JSON output.
        Robust logic to handle various Docling JSON structures.
        """
        extracted_reqs = []
        
        # Helper to process a table node
        def process_table(table_data):
            # Try to identify headers
            headers = []
            rows = []
            
            # Docling table structure varies, usually has 'data' or 'grid'
            # Assuming 'data' is a list of lists [row][col]
            if "data" in table_data:
                grid = table_data["data"]
                
                # Check if grid is a dictionary (unexpected but possible in some formats)
                if isinstance(grid, dict):
                    # Try to find list inside, e.g. grid['rows'] or similar
                    # For safety, let's skip if not a list
                    logger.warning(f"Unexpected table data format: {type(grid)}")
                    return

                if not grid or not isinstance(grid, list): return
                
                # Check if the first item is indexable (list/dict)
                first_row = grid[0]
                if not isinstance(first_row, (list, tuple)):
                     # Maybe a flat list or list of objects
                     # Docling v2: 'data' -> {'grid': [[...]]} or 'data' -> [[...]]
                     # Let's check for 'grid' key if data is dict, but we handled that above.
                     # If it's a list of cells?
                     logger.warning(f"Unexpected row format: {type(first_row)}")
                     return
                
                # Assume first row is header if it contains keywords
                header_row = grid[0]
                
                # Safely get text from cell
                def get_text(cell):
                    if isinstance(cell, dict):
                        return str(cell.get("text", "")).strip()
                    return str(cell).strip()

                header_text = [get_text(cell).lower() for cell in header_row]
                
                if any(k in header_text for k in ["id", "case", "title", "step"]):
                    headers = header_text
                    rows = grid[1:]
                else:
                    # No clear header, treat all as rows
                    rows = grid
            
            # Process rows into Requirements
            for row in rows:
                if not isinstance(row, (list, tuple)): continue
                
                # Convert row cells to text
                def get_text(cell):
                    if isinstance(cell, dict):
                        return str(cell.get("text", "")).strip()
                    return str(cell).strip()
                    
                row_text = [get_text(cell) for cell in row]
                full_row_text = " | ".join(row_text)
                
                if not full_row_text: continue
                
                # Check if row looks like a test case
                # e.g. starts with TC- or has typical columns
                tc_match = re.search(r"(TC-[A-Z0-9-]+)", full_row_text)
                
                if tc_match or len(row_text) >= 3:
                    req = Requirement(
                        original_text=full_row_text,
                        ingestion_metadata=IngestionMetadata(
                            source_file=str(Path(file_path).name),
                            parsing_confidence=0.9 # Higher confidence for table rows
                        )
                    )
                    
                    # Manual extraction if headers are known
                    if headers:
                        entities = ExtractedEntities()
                        req_spec = ReqSpec(req_id=tc_match.group(1) if tc_match else req.id)
                        
                        for i, h in enumerate(headers):
                            val = row_text[i] if i < len(row_text) else ""
                            if "module" in h: entities.module = val
                            if "title" in h: entities.feature = val # Map title to feature roughly
                            if "priority" in h: req_spec.priority = val
                        
                        req.extracted_entities = entities
                        req.req_spec = req_spec
                    
                    # Still run enrichment to catch constraints in text
                    self._enrich_requirement(req)
                    extracted_reqs.append(req)

        # Recursively find tables in the JSON structure
        def find_tables(node):
            if isinstance(node, dict):
                # Check for table structure
                if node.get("type") == "table" and "data" in node:
                     process_table(node)
                # Also check if 'data' exists and looks like a grid (list of lists)
                elif "data" in node and isinstance(node["data"], list) and len(node["data"]) > 0 and isinstance(node["data"][0], list):
                     process_table(node)
                
                for key, value in node.items():
                    # Avoid infinite recursion or re-processing same data
                    if key != "data": 
                        find_tables(value)
            elif isinstance(node, list):
                for item in node:
                    find_tables(item)

        find_tables(raw_content)
        return extracted_reqs

    def _enrich_requirement(self, req: Requirement):
        text = req.original_text
        entities = ExtractedEntities()
        req_spec = ReqSpec(req_id=req.id)
        
        # 1. Split text into logical blocks (e.g. by "TC-" or "Case ID")
        # The user input shows "TC-CRM-01 ...", "TC-CRM-02 ..."
        # If we are processing a large chunk, we should split it.
        
        # Improved Regex for Test Case ID extraction
        # Pattern: TC-[A-Z]+-\d+ or Case ID
        tc_pattern = r"(TC-[A-Za-z0-9-]+)"
        tc_ids = re.findall(tc_pattern, text)
        
        if tc_ids:
            # If multiple IDs found, this might be a bulk requirement.
            # We assign the first one to this req, or maybe we should have split it earlier.
            # For MVP, let's take the first one if not already set.
            req.id = tc_ids[0]
            req_spec.req_id = tc_ids[0]

        # Use jieba posseg for part-of-speech tagging
        words = pseg.cut(text)
        keywords = []
        verbs = []
        nouns = []
        
        for w, flag in words:
            keywords.append(w)
            if flag.startswith('v'):
                verbs.append(w)
            elif flag.startswith('n'):
                nouns.append(w)
        
        # Module/Feature Inference
        if "登录" in text or "Login" in text:
            entities.module = "用户中心"
            entities.feature = "登录"
            req_spec.module_path = "用户中心/安全/登录"
            req_spec.priority = "P0"
            req_spec.type = RequirementType.FUNCTIONAL
        elif "支付" in text or "Pay" in text:
            entities.module = "交易中心"
            entities.feature = "支付"
            req_spec.module_path = "交易中心/支付"
            req_spec.priority = "P0"
            req_spec.type = RequirementType.FUNCTIONAL
        elif "客户" in text or "CRM" in text:
            entities.module = "客户管理"
            entities.feature = "客户录入"
            req_spec.module_path = "CRM/客户管理"
            req_spec.priority = "P1"
            req_spec.type = RequirementType.FUNCTIONAL
            
        # Fallback Module Inference
        if not entities.module:
            potential_modules = [n for n in nouns if len(n) > 1]
            if potential_modules:
                entities.module = potential_modules[0]
                
        if not entities.feature:
             potential_features = [v for v in verbs if len(v) > 1]
             if potential_features:
                 entities.feature = potential_features[0]

        # Regex Extraction for Constraints (Iterative)
        # 1. Length Constraints
        for match in re.finditer(r'(\d+)\s*(?:-|到|to)\s*(\d+)\s*(?:位|chars|characters|bytes)', text):
            min_l, max_l = match.groups()
            entities.constraints.append({
                "type": "length_range", 
                "min": int(min_l), 
                "max": int(max_l),
                "original": match.group(0)
            })
            
        # 2. Single Length
        for match in re.finditer(r'(?<!\d-)(?<!\d到)(\d+)\s*(?:位|chars)', text):
             # Negative lookbehind to avoid matching the second part of a range as single
             entities.constraints.append({
                "type": "length_exact", 
                "value": int(match.group(1)),
                "original": match.group(0)
            })

        # 3. Numeric Range
        for match in re.finditer(r'(?:大于|above|>)[\s:]*(\d+)', text):
            entities.constraints.append({"type": "min_value", "value": int(match.group(1))})
            
        # 4. Mandatory Fields
        if "必须" in text or "must" in text or "required" in text or "必填" in text:
             entities.constraints.append({"type": "mandatory"})

        # 5. Expiry/Time
        for match in re.finditer(r'(\d+)\s*(?:秒|sec|minutes|mins|ms)', text):
             val = int(match.group(1))
             unit = match.group(0)
             if "ms" in unit:
                 entities.constraints.append({"type": "response_time", "value_ms": val})
             else:
                 entities.constraints.append({"type": "timeout", "value": val})

        req.extracted_entities = entities
        req.req_spec = req_spec

    def _calculate_confidence(self, req: Requirement) -> float:
        weights = {"module": 0.3, "feature": 0.4, "constraints": 0.3}
        score = 0.0
        if req.extracted_entities.module:
            score += weights["module"]
        if req.extracted_entities.feature:
            score += weights["feature"]
        if req.extracted_entities.constraints:
            score += weights["constraints"]
        return round(min(score, 1.0), 2)
