import json
import re
import pandas as pd
from pathlib import Path
from typing import List, Dict, Any
from loguru import logger
from docling.document_converter import DocumentConverter

class DocProcessor:
    """
    Handles file reading using IBM Docling and other libraries.
    Supports: .docx, .pdf, .html, .pptx, .md, .txt, .xlsx, .json (Swagger/OpenAPI)
    """
    def __init__(self):
        self.converter = DocumentConverter()

    def read_file(self, file_path: str) -> List[Dict[str, Any]]:
        """
        Reads a file and returns a list of raw data chunks.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
            
        logger.info(f"Processing file: {file_path}")
        
        ext = path.suffix.lower()
        
        try:
            if ext in ['.xlsx', '.xls']:
                return self._read_excel(path)
            elif ext == '.json':
                return self._read_json(path)
            else:
                # Default to Docling for document formats
                return self._read_docling(path)

        except Exception as e:
            logger.error(f"Processing failed for {file_path}: {e}")
            raise

    def _read_docling(self, path: Path) -> List[Dict[str, Any]]:
        # 1. Use Docling for high-precision parsing
        result = self.converter.convert(path)
        
        # 2. Export to AI-readable formats
        markdown_content = result.document.export_to_markdown()
        doc_json = result.document.export_to_dict()
        
        # 3. Intelligent Semantic Chunking
        # Check for large tables that need special handling
        table_chunks = self._table_aware_chunking(doc_json, path)
        if table_chunks:
            return table_chunks
            
        # Fallback to text chunking if no large tables found
        chunks = self._semantic_chunking(markdown_content)
        
        chunk_data = []
        for i, chunk_text in enumerate(chunks):
            chunk_data.append({
                "content": chunk_text,
                "metadata": {
                    "raw_content": doc_json,
                    "source": str(path),
                    "chunk_index": i,
                    "total_chunks": len(chunks),
                    "status": "ready_for_ai",
                    "docling_version": "v2"
                }
            })
            
        return chunk_data

    def _table_aware_chunking(self, doc_json: dict, path: Path) -> List[Dict[str, Any]]:
        """
        Special logic for splitting large tables while preserving headers.
        """
        chunks = []
        
        # Recursively find tables in doc_json
        tables = []
        def find_tables_recursive(node):
            if isinstance(node, dict):
                if node.get("type") == "table" and "data" in node:
                    tables.append(node)
                for v in node.values():
                    find_tables_recursive(v)
            elif isinstance(node, list):
                for item in node:
                    find_tables_recursive(item)
                    
        find_tables_recursive(doc_json)
        
        if not tables:
            return []
            
        logger.info(f"Found {len(tables)} tables. Applying smart table splitting.")
        
        for t_idx, table in enumerate(tables):
            grid = table.get("data", {}).get("grid", []) if "grid" in table.get("data", {}) else table.get("data", [])
            if not grid or not isinstance(grid, list): continue
            
            # Heuristic: If table is small (<30 rows), let semantic chunking handle it
            if len(grid) < 30:
                continue
                
            # Smart Splitting for Large Tables
            header = grid[0:1] # Assume row 0 is header
            body = grid[1:]
            
            chunk_size = 20 # Rows per chunk
            overlap = 5 # Overlap rows
            
            for i in range(0, len(body), chunk_size):
                # Slice with overlap logic (start slightly earlier)
                start = max(0, i - overlap) if i > 0 else 0
                end = min(len(body), i + chunk_size)
                
                # Construct chunk: Header + Overlap/Body
                chunk_rows = header + body[start:end]
                
                # Convert back to Markdown table string
                md_table = self._grid_to_markdown(chunk_rows)
                
                # Add Context Header
                context_header = f"# Table Part {i//chunk_size + 1} (Rows {start}-{end})\n"
                context_header += f"> Context: Continuation of Table {t_idx+1} from {path.name}\n\n"
                
                full_content = context_header + md_table
                
                chunks.append({
                    "content": full_content,
                    "metadata": {
                        "raw_content": table,
                        "source": str(path),
                        "table_id": f"TBL-{t_idx}",
                        "is_table_part": True,
                        "part_index": i // chunk_size,
                        "status": "ready_for_ai"
                    }
                })
                
        return chunks

    def _grid_to_markdown(self, grid: List[List[Any]]) -> str:
        """Helper to convert list of lists to MD table"""
        if not grid: return ""
        
        def cell_text(c):
            if isinstance(c, dict): return str(c.get("text", "")).strip().replace("\n", " ")
            return str(c).strip().replace("\n", " ")

        lines = []
        # Header
        headers = [cell_text(c) for c in grid[0]]
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
        
        # Body
        for row in grid[1:]:
            row_txt = [cell_text(c) for c in row]
            lines.append("| " + " | ".join(row_txt) + " |")
            
        return "\n".join(lines)

    def _semantic_chunking(self, text: str, chunk_size: int = 2000, overlap: int = 300) -> List[str]:
        """
        Splits text based on semantic anchors when standard headers (H1/H2) are missing.
        Uses a heuristic approach to find 'Business Entities' or 'Action Verbs'.
        """
        # 1. Cleaning: Remove noise characters
        text = re.sub(r'[ \t]+', ' ', text) # Merge spaces
        text = re.sub(r'\n\s*\n', '\n\n', text) # Merge newlines
        
        # 2. Check if standard headers exist
        has_headers = bool(re.search(r'^#{1,2}\s', text, re.MULTILINE))
        
        if has_headers:
            # Use standard header splitting
            sections = re.split(r'(^#{1,2}\s.*$)', text, flags=re.MULTILINE)
        else:
            # Fallback: Keyword-based splitting (Semantic Anchors)
            # We look for common requirement keywords to start a new section
            # e.g. "功能：", "需求：", "Function:", "Rule:"
            keywords = r'(功能|需求|规则|Constraint|Function|Rule|Scenario|Case)[:：]'
            sections = re.split(f'(^{keywords}.*$)', text, flags=re.MULTILINE)
            
            if len(sections) < 2:
                 # If still no sections, just split by size blindly
                 return [text[i:i+chunk_size] for i in range(0, len(text), chunk_size-overlap)]

        final_chunks = []
        current_chunk = ""
        
        # Re-attach headers/anchors to their content
        if sections[0].strip():
            current_chunk += sections[0]
            
        for i in range(1, len(sections), 2):
            header = sections[i]
            content = sections[i+1] if i+1 < len(sections) else ""
            block = header + content
            
            # If adding this block exceeds chunk size, save current and start new
            if len(current_chunk) + len(block) > chunk_size:
                if current_chunk:
                    final_chunks.append(current_chunk)
                    # Start new chunk with overlap (last N chars of previous)
                    overlap_text = current_chunk[-overlap:] if len(current_chunk) > overlap else current_chunk
                    current_chunk = overlap_text + "\n...[Context Overlap]...\n" + block
                else:
                    current_chunk = block
            else:
                current_chunk += block
                
        if current_chunk:
            final_chunks.append(current_chunk)
            
        return final_chunks if final_chunks else [text]

    def _read_excel(self, path: Path) -> List[Dict[str, Any]]:
        # Read Excel using Pandas
        dfs = pd.read_excel(path, sheet_name=None) # Read all sheets
        content_parts = []
        raw_data = {}
        
        for sheet_name, df in dfs.items():
            # Convert to markdown table for LLM
            md_table = df.to_markdown(index=False)
            content_parts.append(f"## Sheet: {sheet_name}\n\n{md_table}")
            
            # Keep raw records
            # Handle NaN with fillna
            df = df.fillna("")
            raw_data[sheet_name] = df.to_dict(orient="records")
            
        return [{
            "content": "\n\n".join(content_parts),
            "metadata": {
                "raw_content": {"type": "excel", "data": raw_data},
                "source": str(path),
                "status": "ready_for_ai"
            }
        }]

    def _read_json(self, path: Path) -> List[Dict[str, Any]]:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        # Check if Swagger/OpenAPI
        is_swagger = "swagger" in data or "openapi" in data
        
        content = ""
        if is_swagger:
            content = self._parse_swagger(data)
        else:
            content = json.dumps(data, indent=2, ensure_ascii=False)
            
        return [{
            "content": content,
            "metadata": {
                "raw_content": data,
                "source": str(path),
                "type": "swagger" if is_swagger else "json",
                "status": "ready_for_ai"
            }
        }]

    def _parse_swagger(self, data: dict) -> str:
        """Simple Swagger/OpenAPI flattener"""
        lines = []
        info = data.get("info", {})
        lines.append(f"# API Doc: {info.get('title', 'Unknown')} ({info.get('version', '')})")
        lines.append(f"Description: {info.get('description', '')}\n")
        
        paths = data.get("paths", {})
        for path, methods in paths.items():
            for method, details in methods.items():
                summary = details.get("summary", "")
                desc = details.get("description", "")
                lines.append(f"## {method.upper()} {path}")
                lines.append(f"Summary: {summary}")
                if desc: lines.append(f"Description: {desc}")
                
                # Parameters
                params = details.get("parameters", [])
                if params:
                    lines.append("Parameters:")
                    for p in params:
                        lines.append(f"- {p.get('name')} ({p.get('in')}): {p.get('description', '')} [Required: {p.get('required', False)}]")
                
                lines.append("")
                
        return "\n".join(lines)

