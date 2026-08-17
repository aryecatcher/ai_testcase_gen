import re
import hashlib
from typing import List, TYPE_CHECKING, Optional, Dict, Any
from pathlib import Path
from loguru import logger
from ...models.domain import Requirement, IngestionMetadata, ExtractedEntities, ReqSpec, RequirementType

def _to_req_spec_obj(raw) -> Optional[ReqSpec]:
    """兼容 dict 和 ReqSpec 对象"""
    if raw is None:
        return None
    if isinstance(raw, dict):
        return ReqSpec(**raw)
    return raw

if TYPE_CHECKING:
    from .doc_processor import DocProcessor

class RequirementIngestor:
    def __init__(self):
        self._doc_processor = None
        self._jieba_initialized = False
        self._pseg = None

    @property
    def doc_processor(self) -> "DocProcessor":
        if self._doc_processor is None:
            from .doc_processor import DocProcessor

            logger.info("Initializing DocProcessor (Lazy)...")
            self._doc_processor = DocProcessor()
        return self._doc_processor

    def _ensure_jieba(self):
        if not self._jieba_initialized:
            import jieba
            import jieba.posseg as pseg

            logger.info("Initializing Jieba (Lazy)...")
            jieba.initialize()
            self._pseg = pseg
            self._jieba_initialized = True

    def ingest(self, file_path: str) -> List[Requirement]:
        """
        Ingests a file and converts it into a list of Requirement objects.
        Now supports parsing Docling's structured table data to extract individual test case rows.
        """
        self._ensure_jieba()
        try:
            chunks = self.doc_processor.read_file(file_path)
            requirements = []
            
            for chunk in chunks:
                content = chunk["content"]
                metadata = chunk["metadata"]
                
                # Check for structured table data in Docling metadata
                raw_content = metadata.get("raw_content", {})
                semantic_format = metadata.get("semantic_format")
                
                # Handle Excel specifically
                if semantic_format in {"excel_json", "excel_csv"} and metadata.get("raw_content", {}).get("type") == "excel":
                    table_requirements = self._extract_from_structured_excel(raw_content["data"], metadata, file_path)
                elif metadata.get("raw_content", {}).get("type") == "excel":
                    table_requirements = self._extract_from_excel(raw_content["data"], file_path)
                # Handle Excel chunks from DocProcessor (which have 'sheet' in metadata)
                elif semantic_format == "markdown":
                    table_requirements = self._extract_from_markdown_chunk(content, file_path)
                elif "sheet" in metadata:
                    table_requirements = self._extract_from_markdown_chunk(content, file_path)
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
                         req.id = self._make_req_id(file_path, full_text, chunk_index, prefix="PENDING")
                    else:
                         # Ensure unique ID for chunks
                         if not req.id:
                            req.id = self._make_req_id(file_path, full_text, chunk_index)
                            
                    if req.req_spec:
                        req.req_spec.req_id = req.id
                            
                    requirements.append(req)
                
            return self._dedupe_requirements(requirements)
        except Exception as e:
            logger.error(f"Ingestion failed for {file_path}: {e}")
            raise

    def _make_req_id(self, file_path: str, content: str, index: int = 0, prefix: str = "REQ") -> str:
        stem = Path(file_path).stem[:12].upper() or "FILE"
        digest = hashlib.md5(f"{file_path}|{index}|{content[:200]}".encode("utf-8")).hexdigest()[:8].upper()
        return f"{prefix}-{stem}-{index}-{digest}"

    def _dedupe_requirements(self, requirements: List[Requirement]) -> List[Requirement]:
        deduped: List[Requirement] = []
        seen = set()
        for req in requirements:
            text = (req.original_text or "").strip()
            entities = req.extracted_entities if isinstance(req.extracted_entities, dict) else {}
            module = entities.get("module", "") if isinstance(entities, dict) else ""
            feature = entities.get("feature", "") if isinstance(entities, dict) else ""
            fp_src = f"{module}|{feature}|{text}"
            fp = hashlib.md5(fp_src.encode("utf-8")).hexdigest()
            if fp in seen:
                continue
            seen.add(fp)
            deduped.append(req)
        return deduped

    def _extract_from_markdown_chunk(self, content: str, file_path: str) -> List[Requirement]:
        """
        Parses Markdown content into requirements using heading/list/table semantics.
        """
        requirements: List[Requirement] = []
        text = (content or "").strip()
        if not text:
            return requirements

        # Strip the metadata header added by DocProcessor so we focus on the semantic body.
        if "## 正文内容" in text:
            body = text.split("## 正文内容", 1)[1].strip()
        elif "## JSON Records" in text or "## CSV Data" in text:
            body = text
        else:
            body = re.sub(r"^# 文档语义片段.*?(?=\n\n)", "", text, count=1, flags=re.S).strip()
            body = re.sub(r"^(>\s.*\n)+", "", body, count=1, flags=re.M).strip() or text
        sections = self._split_markdown_sections(body)
        feature_hint = ""
        for idx, section in enumerate(sections):
            heading = self._clean_heading_label(section.get("heading") or "")
            body_lines = [ln for ln in section.get("body", []) if ln.strip()]
            if heading == "业务场景名称":
                _, summary, body_text = self._extract_section_bullets_and_summary(body_lines)
                feature_hint = self._clean_heading_label(summary or body_text)
                continue
            if heading in {"数据规格定义", "数据定义", "术语说明"}:
                if requirements:
                    self._merge_context_section_into_requirement(requirements[-1], heading, body_lines)
                continue
            section_text = "\n".join(section.get("body", []))
            table_blocks = re.findall(r"((?:^\|.*\|\s*$\n?){2,})", section_text, flags=re.M)
            for block in table_blocks:
                requirements.extend(
                    self._markdown_table_to_requirements(
                        block,
                        file_path,
                        len(requirements),
                        context_path=section.get("path") or []
                    )
                )
            if table_blocks:
                continue
            if feature_hint:
                section = dict(section)
                base_path = list(section.get("path") or [])
                if feature_hint not in base_path:
                    section["path"] = base_path + [feature_hint]
            req = self._section_to_requirement(section, file_path, idx)
            if req is not None:
                requirements.append(req)
        if requirements:
            return requirements

        # Fallback: heading-less markdown table blocks
        table_blocks = re.findall(r"((?:^\|.*\|\s*$\n?){2,})", body, flags=re.M)
        for block in table_blocks:
            requirements.extend(self._markdown_table_to_requirements(block, file_path, len(requirements)))
        return requirements

    def _clean_heading_label(self, text: str) -> str:
        text = (text or "").strip()
        text = re.sub(r"^[#\-\*\d\.\)\(（）、\s]+", "", text)
        text = re.sub(r"[*_`]+", "", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip("：:;；- ")

    def _is_generic_heading(self, text: str) -> bool:
        text = self._clean_heading_label(text)
        generic = {
            "概述", "简介", "背景", "范围", "说明", "备注", "附录", "目录",
            "总体说明", "设计说明", "名词解释", "约定", "文档语义片段",
            "项目背景", "补充说明", "背景说明", "需求描述文档"
        }
        return text in generic

    def _is_structural_heading(self, text: str) -> bool:
        text = self._clean_heading_label(text)
        structural = {
            "功能需求说明", "技术要求与业务逻辑", "功能需求", "非功能性需求",
            "验收标准", "边界与特殊说明", "补充说明"
        }
        return text in structural

    def _is_context_heading(self, text: str) -> bool:
        text = self._clean_heading_label(text)
        return text in {"业务场景名称", "数据规格定义", "数据定义", "术语说明"}

    def _extract_section_bullets_and_summary(self, body_lines: List[str]) -> tuple[List[str], str, str]:
        bullets = []
        paragraphs = []
        for ln in body_lines:
            stripped = ln.strip()
            if re.match(r"^[-*]\s+", stripped) or re.match(r"^\d+[.)]\s+", stripped):
                bullets.append(self._clean_heading_label(re.sub(r"^[-*]\s+|^\d+[.)]\s+", "", stripped)))
            elif not stripped.startswith("|"):
                paragraphs.append(stripped)
        summary = " ".join(paragraphs[:3]).strip()
        body_text = " ".join(body_lines)
        return bullets, summary, body_text

    def _merge_context_section_into_requirement(self, req: Requirement, heading: str, body_lines: List[str]) -> None:
        bullets, summary, body_text = self._extract_section_bullets_and_summary(body_lines)
        entities = req.extracted_entities
        if isinstance(entities, dict):
            constraints = entities.setdefault("constraints", [])
            feature = entities.get("feature", "")
        else:
            constraints = getattr(entities, "constraints", None)
            if constraints is None:
                constraints = []
                entities.constraints = constraints
            feature = getattr(entities, "feature", "")

        if heading == "业务场景名称":
            feature_hint = self._clean_heading_label(summary or body_text)
            if feature_hint:
                if isinstance(entities, dict):
                    if not entities.get("feature"):
                        entities["feature"] = feature_hint
                elif not feature:
                    entities.feature = feature_hint
                if req.req_spec and getattr(req.req_spec, "module_path", "") and feature_hint not in req.req_spec.module_path:
                    req.req_spec.module_path = f"{req.req_spec.module_path}/{feature_hint}"
        else:
            for item in bullets:
                constraints.append({"type": "context", "value": item})
            for piece in re.split(r"[；;。]\s*|\n", summary or body_text):
                piece = self._clean_heading_label(piece)
                if len(piece) >= 2:
                    constraints.append({"type": "context", "value": piece})

        req.original_text = (req.original_text or "") + f" | Context[{heading}]: {(summary or body_text)[:300]}"
        self._enrich_requirement(req)

    def _markdown_req_type(self, heading_path: List[str], body_text: str) -> RequirementType:
        text = " | ".join(heading_path) + " | " + (body_text or "")
        if any(k in text for k in ["性能", "响应时间", "吞吐", "并发", "ms", "TPS"]):
            return RequirementType.PERFORMANCE
        if any(k in text for k in ["安全", "权限", "鉴权", "认证", "加密", "脱敏", "审计"]):
            return RequirementType.SECURITY
        if any(k in text.lower() for k in ["api", "接口", "报文", "返回码", "endpoint"]):
            return RequirementType.INTERFACE
        return RequirementType.FUNCTIONAL

    def _derive_markdown_entities(self, heading_path: List[str], heading: str, bullets: List[str], summary: str) -> tuple[str, str, str]:
        cleaned_path = [self._clean_heading_label(p) for p in heading_path if self._clean_heading_label(p)]
        filtered_path = [p for p in cleaned_path if not self._is_generic_heading(p) and not self._is_structural_heading(p)]
        if not filtered_path and heading:
            filtered_path = [self._clean_heading_label(heading)]

        module = ""
        feature = ""
        if len(filtered_path) >= 2:
            module = filtered_path[-2]
            feature = filtered_path[-1]
        elif filtered_path:
            feature = filtered_path[-1]

        if not feature and bullets:
            feature = self._clean_heading_label(bullets[0])[:30]
        if not module and len(filtered_path) >= 1:
            module = filtered_path[0] if len(filtered_path) > 1 else ""

        module_path = "/".join(filtered_path) if filtered_path else (self._clean_heading_label(heading) or "MarkdownSection")
        return module, feature or self._clean_heading_label(heading), module_path

    def _split_markdown_sections(self, markdown_text: str) -> List[Dict[str, Any]]:
        lines = markdown_text.splitlines()
        sections: List[Dict[str, Any]] = []
        current = {"heading": "", "level": 0, "body": []}
        heading_stack: List[tuple[int, str]] = []
        for line in lines:
            stripped = line.strip()
            markdown_heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
            cn_heading = re.match(r"^([一二三四五六七八九十]+)、\s*(.+)$", stripped)
            decimal_heading = re.match(r"^(\d+(?:\.\d+)+)\s+(.+)$", stripped)
            simple_num_heading = re.match(r"^(\d+)[、.]\s*(.+)$", stripped)

            level = 0
            heading = ""
            if markdown_heading:
                level = len(markdown_heading.group(1))
                heading = markdown_heading.group(2).strip()
            elif cn_heading:
                level = 1
                heading = cn_heading.group(2).strip()
            elif decimal_heading:
                level = decimal_heading.group(1).count(".") + 1
                heading = decimal_heading.group(2).strip()
            elif simple_num_heading:
                level = 1
                heading = simple_num_heading.group(2).strip()

            if heading:
                if current["heading"] or current["body"]:
                    sections.append(current)
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                heading_stack.append((level, heading))
                current = {
                    "heading": heading,
                    "level": level,
                    "body": [],
                    "path": [h for _, h in heading_stack],
                    "parent_heading": heading_stack[-2][1] if len(heading_stack) >= 2 else "",
                }
            else:
                current["body"].append(line.rstrip())
        if current["heading"] or current["body"]:
            sections.append(current)
        return [s for s in sections if s["heading"] or any(x.strip() for x in s["body"])]

    def _section_to_requirement(self, section: Dict[str, Any], file_path: str, idx: int) -> Optional[Requirement]:
        heading = self._clean_heading_label(section.get("heading") or "")
        heading_path = [self._clean_heading_label(p) for p in (section.get("path") or []) if self._clean_heading_label(p)]
        parent_heading = (section.get("parent_heading") or "").strip()
        body_lines = [ln for ln in section.get("body", []) if ln.strip()]
        if not heading and not body_lines:
            return None
        if self._is_generic_heading(heading):
            return None

        bullets = []
        paragraphs = []
        for ln in body_lines:
            stripped = ln.strip()
            if re.match(r"^[-*]\s+", stripped) or re.match(r"^\d+[.)]\s+", stripped):
                bullets.append(self._clean_heading_label(re.sub(r"^[-*]\s+|^\d+[.)]\s+", "", stripped)))
            elif not stripped.startswith("|"):
                paragraphs.append(stripped)

        summary = " ".join(paragraphs[:3]).strip()
        body_text = " ".join(body_lines)
        if (not heading and "需求描述文档" in body_text) or "[项目名称/模块名称]" in body_text:
            return None
        if not bullets and not any(k in body_text for k in ["必须", "应", "需", "支持", "规则", "登录", "密码", "验证码", "锁定", "接口", "并发", "加密", "安全"]):
            return None
        parts = []
        if heading_path:
            parts.append("Path: " + " / ".join(heading_path))
        if heading:
            parts.append(f"Section: {heading}")
        if summary:
            parts.append(f"Summary: {summary}")
        if bullets:
            parts.append("要点: " + " | ".join(bullets[:8]))
        full_text = " | ".join(parts).strip()
        if not full_text:
            return None

        req = Requirement(
            id=self._make_req_id(file_path, full_text, idx, prefix="MD"),
            original_text=full_text,
            ingestion_metadata=IngestionMetadata(
                source_file=str(Path(file_path).name),
                parsing_confidence=0.88
            )
        )
        module, feature, module_path = self._derive_markdown_entities(heading_path, heading, bullets, summary)
        constraint_items = [{"type": "bullet", "value": b} for b in bullets[:6]]
        if summary:
            constraint_items.append({"type": "summary", "value": summary[:200]})
        req.extracted_entities = ExtractedEntities(
            module=module,
            feature=feature or heading or parent_heading,
            constraints=constraint_items
        )
        req.req_spec = ReqSpec(
            req_id=req.id,
            module_path=module_path,
            type=self._markdown_req_type(heading_path, body_text)
        )
        self._enrich_requirement(req)
        return req

    def _markdown_table_to_requirements(self, table_block: str, file_path: str, offset: int, context_path: Optional[List[str]] = None) -> List[Requirement]:
        requirements: List[Requirement] = []
        table_lines = [line.strip() for line in table_block.splitlines() if line.strip().startswith("|")]
        if len(table_lines) < 2:
            return requirements

        headers = [h.strip() for h in table_lines[0].strip("|").split("|")]
        header_map = {}
        for i, h in enumerate(headers):
            h_lower = h.lower()
            if any(k in h_lower for k in ["id", "编号", "case"]): header_map["id"] = i
            elif any(k in h_lower for k in ["module", "模块", "system", "系统", "level_2"]): header_map["module"] = i
            elif any(k in h_lower for k in ["feature", "功能", "title", "标题", "point", "level_3", "level_4"]): header_map["feature"] = i
            elif any(k in h_lower for k in ["priority", "level", "优先级", "等级"]): header_map["priority"] = i

        start_idx = 2 if len(table_lines) > 1 and "---" in table_lines[1] else 1
        for row_idx, line in enumerate(table_lines[start_idx:], start=offset):
            values = [v.strip() for v in line.strip("|").split("|")]
            if not any(values):
                continue
            parts = []
            for i, h in enumerate(headers):
                if i < len(values) and values[i] and values[i] != "nan":
                    parts.append(f"{h}: {values[i]}")
            full_text = " | ".join(parts)
            if not full_text:
                continue

            req = Requirement(
                original_text=full_text,
                ingestion_metadata=IngestionMetadata(
                    source_file=str(Path(file_path).name),
                    parsing_confidence=0.95
                )
            )
            entities = ExtractedEntities()
            req_spec = ReqSpec(req_id=req.id)
            context_path = [self._clean_heading_label(p) for p in (context_path or []) if self._clean_heading_label(p)]
            if "module" in header_map and header_map["module"] < len(values):
                entities.module = self._clean_heading_label(values[header_map["module"]])
            elif context_path:
                entities.module = context_path[0]
            if "feature" in header_map and header_map["feature"] < len(values):
                entities.feature = self._clean_heading_label(values[header_map["feature"]])
            elif context_path:
                entities.feature = context_path[-1]
            if "id" in header_map and header_map["id"] < len(values):
                rid = values[header_map["id"]]
                if rid and rid != "nan":
                    req.id = rid
                    req_spec.req_id = rid
            if not req.id:
                req.id = self._make_req_id(file_path, full_text, row_idx)
                req_spec.req_id = req.id
            if "priority" in header_map and header_map["priority"] < len(values):
                req_spec.priority = values[header_map["priority"]]
            if context_path and not req_spec.module_path:
                req_spec.module_path = "/".join(context_path)
            req_spec.type = self._markdown_req_type(context_path, full_text)
            req.extracted_entities = entities
            req.req_spec = req_spec
            self._enrich_requirement(req)
            requirements.append(req)
        return requirements

    def _extract_from_structured_excel(self, excel_data: dict, metadata: Dict[str, Any], file_path: str) -> List[Requirement]:
        extracted_reqs: List[Requirement] = []
        if isinstance(excel_data, list):
            excel_data = {"Sheet1": excel_data}

        hierarchy = metadata.get("structure", {}) if isinstance(metadata.get("structure"), dict) else {}
        for sheet, rows in excel_data.items():
            for row in rows:
                if not isinstance(row, dict):
                    continue
                normalized = {str(k).strip(): ("" if v is None else str(v).strip()) for k, v in row.items()}
                if not any(normalized.values()):
                    continue

                level2 = normalized.get("Level_2") or normalized.get("level_2") or hierarchy.get("level_2") or normalized.get("模块")
                level3 = normalized.get("Level_3") or normalized.get("level_3") or hierarchy.get("level_3")
                level4 = normalized.get("Level_4") or normalized.get("level_4") or hierarchy.get("level_4")
                check_item = normalized.get("检验项目") or normalized.get("项目") or normalized.get("标题")
                tech_req = normalized.get("技术要求") or normalized.get("需求描述") or normalized.get("描述") or normalized.get("说明")

                parts = [f"Sheet: {sheet}"]
                for key in ["检验项目", "Level_2", "Level_3", "Level_4", "技术要求"]:
                    val = normalized.get(key)
                    if val:
                        parts.append(f"{key}: {val}")
                if len(parts) == 1:
                    parts.extend([f"{k}: {v}" for k, v in normalized.items() if v][:8])
                full_text = " | ".join(parts)

                req_id = str(normalized.get("ID") or normalized.get("id") or normalized.get("Case ID") or "").strip()
                req = Requirement(
                    id=req_id or None,
                    original_text=full_text,
                    ingestion_metadata=IngestionMetadata(
                        source_file=str(Path(file_path).name),
                        parsing_confidence=0.98,
                    )
                )
                if not req.id:
                    req.id = self._make_req_id(file_path, full_text, len(extracted_reqs))

                entities = ExtractedEntities(
                    module=level2 or "",
                    feature=level4 or level3 or (f"检验项目: {check_item}" if check_item else ""),
                    constraints=[],
                )
                if tech_req:
                    for piece in re.split(r"[、,，；;]\s*", tech_req):
                        piece = piece.strip()
                        if len(piece) >= 2:
                            entities.constraints.append({"type": "capability", "value": piece})

                module_path = "/".join([p for p in [level2, level3, level4 or (f"检验项目: {check_item}" if check_item else "")] if p])
                req.extracted_entities = entities
                req.req_spec = ReqSpec(
                    req_id=req.id,
                    module_path=module_path or sheet,
                    priority=normalized.get("优先级") or normalized.get("priority") or "P2",
                    type=RequirementType.FUNCTIONAL,
                )
                self._enrich_requirement(req)
                extracted_reqs.append(req)
        return extracted_reqs

    def _extract_from_excel(self, excel_data: dict, file_path: str) -> List[Requirement]:
        """
        Extracts requirements from Excel data (sheet -> rows).
        """
        extracted_reqs = []
        
        # Handle list input (backward compatibility or flat data)
        if isinstance(excel_data, list):
            excel_data = {"Sheet1": excel_data}
            
        for sheet, rows in excel_data.items():
            for row in rows:
                # Convert row dict to text
                # Filter empty values
                parts = [f"{k}: {v}" for k, v in row.items() if v]
                if not parts: continue
                
                full_text = f"Sheet: {sheet} | " + " | ".join(parts)
                
                # Check for ID
                req_id = str(row.get("ID") or row.get("id") or row.get("Case ID") or "")
                
                req_kwargs = {
                    "original_text": full_text,
                    "ingestion_metadata": IngestionMetadata(
                        source_file=str(Path(file_path).name),
                        parsing_confidence=0.95
                    )
                }
                if req_id:
                    req_kwargs["id"] = req_id
                
                req = Requirement(**req_kwargs)
                if not req.id:
                    req.id = self._make_req_id(file_path, full_text, len(extracted_reqs))
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
        """
        Enriches a Requirement object by extracting entities and spec details.
        """
        self._ensure_jieba()
        text = (req.cleaned_text or req.original_text).strip()
        
        # Ensure req.req_spec is a ReqSpec object for consistent access
        req.req_spec = _to_req_spec_obj(req.req_spec)
        if req.req_spec is None:
            req.req_spec = ReqSpec(req_id=req.id) # Initialize if None
        
        req_spec = req.req_spec # Use the updated req.req_spec
        existing_entities = req.extracted_entities
        if isinstance(existing_entities, dict):
            entities = ExtractedEntities(**existing_entities)
        elif existing_entities is not None:
            entities = existing_entities
        else:
            entities = ExtractedEntities()
        
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
        words = self._pseg.cut(text)
        keywords = []
        verbs = []
        nouns = []
        
        for w, flag in words:
            keywords.append(w)
            if flag.startswith('v'):
                verbs.append(w)
            elif flag.startswith('n'):
                nouns.append(w)
        
        # --- NEW: Structured Markdown Table Parsing ---
        # If text looks like a table row, try to extract Module/Feature from columns
        if "|" in text and (text.strip().startswith("|") or "Sheet:" in text):
            # Clean split by pipe
            parts = [p.strip() for p in text.split("|") if p.strip()]
            
            # Heuristic: Excel often has [ID, System, Module, Feature, Description...]
            # We look for short categorical strings before the long description.
            
            # Find the longest part - likely the requirement description
            if parts:
                desc_idx = max(range(len(parts)), key=lambda i: len(parts[i]))
                
                # If we have columns before the description, they are likely hierarchy
                # e.g. [ID, Module, Feature, Desc] -> desc_idx=3
                # e.g. [Module, Feature, Desc] -> desc_idx=2
                
                if desc_idx >= 2:
                    # Assume col before desc is Feature, col before that is Module
                    # But skip col 0 if it looks like an ID (digit)
                    
                    # Candidate for Feature
                    cand_feature = parts[desc_idx-1]
                    if len(cand_feature) < 20: # Feature names are usually short
                        entities.feature = cand_feature
                        
                    # Candidate for Module
                    cand_module = parts[desc_idx-2]
                    # If col 0 is just a number, it's ID, ignore it. 
                    # If desc_idx-2 is index 0 and it's a number, skip.
                    if not cand_module.isdigit() and len(cand_module) < 20:
                         entities.module = cand_module
                
                elif desc_idx == 1:
                    # [Module, Desc]
                    cand_module = parts[0]
                    if not cand_module.isdigit() and len(cand_module) < 20:
                        entities.module = cand_module

        kv_pairs = {}
        for seg in re.split(r"\s*\|\s*", text):
            if ":" in seg:
                k, v = seg.split(":", 1)
                kv_pairs[k.strip()] = v.strip()
            elif "：" in seg:
                k, v = seg.split("：", 1)
                kv_pairs[k.strip()] = v.strip()

        regex_fields = {
            "Level_2": r"Level_2\s*[:：]\s*([^|]+)",
            "Level_3": r"Level_3\s*[:：]\s*([^|]+)",
            "Level_4": r"Level_4\s*[:：]\s*([^|]+)",
            "检验项目": r"检验项目\s*[:：]\s*([^|]+)",
            "技术要求": r"技术要求\s*[:：]\s*([^|]+)",
        }
        for key, pat in regex_fields.items():
            if key not in kv_pairs:
                m = re.search(pat, text)
                if m:
                    kv_pairs[key] = m.group(1).strip()

        if kv_pairs.get("Level_2") and not entities.module:
            entities.module = kv_pairs.get("Level_2")
        if kv_pairs.get("Level_3") and not entities.feature:
            entities.feature = kv_pairs.get("Level_3")
        if kv_pairs.get("Level_4"):
            entities.feature = kv_pairs.get("Level_4")
        if kv_pairs.get("检验项目") and not entities.feature:
            entities.feature = f"检验项目: {kv_pairs.get('检验项目')}"
        if any(k in kv_pairs for k in ["Level_2", "Level_3", "Level_4"]) and not req_spec.module_path:
            req_spec.module_path = "/".join([kv_pairs.get(k) for k in ["Level_2", "Level_3", "Level_4"] if kv_pairs.get(k)])

        # Module/Feature Inference (Fallback to Keywords)
        if not entities.module:
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

        if kv_pairs.get("技术要求"):
            desc = kv_pairs["技术要求"]
            existing_caps = {str(c.get("value", "")) for c in entities.constraints if isinstance(c, dict)}
            for piece in re.split(r"[、,，；;]\s*", desc):
                piece = piece.strip()
                if len(piece) >= 2 and piece not in existing_caps:
                    entities.constraints.append({"type": "capability", "value": piece})

        req.extracted_entities = entities
        req.req_spec = req_spec
        
        # Calculate and set confidence score
        # Using the formula: Confidence = E_matched / E_required
        confidence = req.calculate_confidence()
        # 兼容 dict 或 IngestionMetadata 对象
        if isinstance(req.ingestion_metadata, dict):
            req.ingestion_metadata["parsing_confidence"] = confidence
        elif req.ingestion_metadata is not None:
            req.ingestion_metadata.parsing_confidence = confidence
        else:
            req.ingestion_metadata = IngestionMetadata(parsing_confidence=confidence)
