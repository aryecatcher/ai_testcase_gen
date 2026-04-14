import pandas as pd
import json
import os
from io import BytesIO
from typing import List, Dict, Any
from datetime import datetime
from urllib.parse import quote
from loguru import logger
from ...models.domain import TestCase, TestInstruction, TestDataSets

class TestCaseExporter:
    def __init__(self, test_cases: List[TestCase], requirement_link_base_url: str = ""):
        self.test_cases = test_cases
        self.requirement_link_base_url = requirement_link_base_url or os.getenv("FEISHU_REQUIREMENT_LINK_BASE_URL", "")

    def _format_steps(self, steps: List[str]) -> str:
        normalized = []
        for idx, step in enumerate(steps or [], start=1):
            step = str(step).strip()
            if not step:
                continue
            if step[:3].strip().startswith(f"{idx}."):
                normalized.append(step)
                continue
            step = pd.Series([step]).str.replace(r"^\d+\s*[.)、．]\s*", "", regex=True).iloc[0]
            normalized.append(f"{idx}. {step}")
        return "\n".join(normalized)

    def _ti(self, tc: TestCase):
        if hasattr(tc, "get_test_instruction"):
            ti = tc.get_test_instruction()
        else:
            ti = getattr(tc, "test_instruction", None)

        if ti is None:
            return TestInstruction()
        if isinstance(ti, dict):
            tds = ti.get("test_data_sets")
            if isinstance(tds, dict):
                ti = dict(ti)
                ti["test_data_sets"] = TestDataSets(**tds)
            return TestInstruction(**ti)
        return ti

    def _pretty_json(self, value: Any) -> str:
        if value in (None, "", {}):
            return ""
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)

    def _requirement_link(self, req_id: str) -> str:
        base = (self.requirement_link_base_url or "").strip()
        req_id = (req_id or "").strip()
        if not base or not req_id:
            return ""
        if "{req_id}" in base:
            return base.replace("{req_id}", quote(req_id))
        if base.endswith("/") or base.endswith("="):
            return f"{base}{quote(req_id)}"
        return f"{base}/{quote(req_id)}"

    def _header_name_map(self) -> Dict[str, str]:
        return {
            "Case ID": "用例ID",
            "Title": "用例标题",
            "Priority": "优先级",
            "Type": "用例类型",
            "Precondition": "前置条件",
            "Steps": "步骤描述",
            "Expected Result": "预期结果",
            "Methodology": "测试策略",
            "Valid Test Data": "正常数据",
            "Invalid Test Data": "异常数据",
            "Related Requirement": "关联需求",
            "Related Requirement Link": "关联需求链接",
            "Generated At": "生成时间",
        }

    def _preferred_header_order(self) -> List[str]:
        return [
            "测试用例 ID",
            "需求对应",
            "优先级",
            "前提条件",
            "测试目的描述",
            "测试步骤概述",
            "期望结果",
            "实测结果",
            "Pass/ Fail/NT",
        ]

    def _display_dataframe(self) -> pd.DataFrame:
        base_df = self._to_dataframe().fillna("")
        display_rows: List[Dict[str, Any]] = []
        for row in base_df.to_dict(orient="records"):
            execution_status = str(
                row.get("Execution Status")
                or row.get("Pass/ Fail/NT")
                or "NT"
            ).strip()
            display_row = {
                "测试用例 ID": row.get("Case ID", ""),
                "需求对应": row.get("Related Requirement", ""),
                "优先级": row.get("Priority", ""),
                "前提条件": row.get("Precondition", ""),
                "测试目的描述": row.get("Title", ""),
                "测试步骤概述": row.get("Steps", ""),
                "期望结果": row.get("Expected Result", ""),
                "实测结果": row.get("Actual Result", ""),
                "Pass/ Fail/NT": execution_status or "NT",
            }
            display_rows.append(display_row)

        ordered_cols = self._preferred_header_order()
        if not display_rows:
            return pd.DataFrame(columns=ordered_cols)
        return pd.DataFrame(display_rows)[ordered_cols]

    def _to_dataframe(self) -> pd.DataFrame:
        data = []
        for tc in self.test_cases:
            ti = self._ti(tc)
            system_env = tc.system_env if isinstance(tc.system_env, dict) else {}
            req_link = self._requirement_link(tc.related_req_id)
            # Flatten structure for Excel
            item = {
                "Case ID": tc.test_case_id,
                "Title": tc.title,
                "Priority": tc.priority,
                "Type": tc.dimension,
                "Precondition": ti.pre_condition,
                "Steps": self._format_steps(ti.steps),
                "Expected Result": ti.expected_result,
                "Methodology": ", ".join(tc.methodology or []),
                "Valid Test Data": self._pretty_json(ti.test_data_sets.valid if ti.test_data_sets else {}),
                "Invalid Test Data": self._pretty_json(ti.test_data_sets.invalid if ti.test_data_sets else {}),
                "Related Requirement": tc.related_req_id,
                "Actual Result": (system_env or {}).get("actual_result", ""),
                "Execution Status": (system_env or {}).get("execution_status", ""),
                "Generated At": (system_env or {}).get("generated_at", ""),
            }
            if req_link:
                item["Related Requirement Link"] = req_link
            data.append(item)
        return pd.DataFrame(data)

    def local_sheet_headers(self) -> List[str]:
        return list(self._display_dataframe().columns)

    def feishu_field_names(self) -> List[str]:
        mapping = self._header_name_map()
        return [mapping.get(col, col) for col in self.local_sheet_headers()]

    def feishu_single_select_options(self) -> Dict[str, List[str]]:
        df = self._to_dataframe().fillna("")
        priority_values = [str(v).strip() for v in df.get("Priority", []).tolist() if str(v).strip()]
        type_values = [str(v).strip() for v in df.get("Type", []).tolist() if str(v).strip()]

        def uniq(values: List[str], defaults: List[str]) -> List[str]:
            ordered = []
            seen = set()
            for item in defaults + values:
                key = item.lower()
                if key in seen:
                    continue
                seen.add(key)
                ordered.append(item)
            return ordered

        return {
            "优先级": uniq(priority_values, ["P0", "P1", "P2", "P3"]),
            "用例类型": uniq(type_values, ["Functional", "Interface", "Security", "Performance", "Compatibility", "Usability"]),
        }

    def to_feishu_records(self) -> List[Dict[str, Any]]:
        df_feishu = self._display_dataframe()
        records: List[Dict[str, Any]] = []
        for row in df_feishu.fillna("").to_dict(orient="records"):
            fields = {k: v for k, v in row.items() if v not in (None, "")}
            if "生成时间" in fields:
                try:
                    dt = datetime.fromisoformat(str(fields["生成时间"]).replace("Z", "+00:00"))
                    fields["生成时间"] = int(dt.timestamp() * 1000)
                except Exception:
                    pass
            records.append({"fields": fields})
        return records

    def to_sheet_values(self) -> List[List[Any]]:
        df = self._display_dataframe().fillna("")
        headers = list(df.columns)
        rows = df.values.tolist()
        return [headers] + rows

    def to_doc_sections(self) -> List[Dict[str, Any]]:
        sections: List[Dict[str, Any]] = []
        for idx, tc in enumerate(self.test_cases, start=1):
            ti = self._ti(tc)
            sections.append({
                "title": f"{idx}. {tc.title or '未命名用例'}",
                "meta": [
                    f"用例ID：{tc.test_case_id}",
                    f"优先级：{tc.priority or ''}",
                    f"类型：{tc.dimension or ''}",
                    f"关联需求：{tc.related_req_id or ''}",
                    *(([f"关联需求链接：{self._requirement_link(tc.related_req_id)}"] if self._requirement_link(tc.related_req_id) else [])),
                    f"前置条件：{ti.pre_condition or ''}",
                ],
                "steps": [str(s).strip() for s in (ti.steps or []) if str(s).strip()],
                "expected_result": ti.expected_result or "",
                "methodology": ", ".join(tc.methodology or []),
                "valid_data": self._pretty_json(ti.test_data_sets.valid if ti.test_data_sets else {}),
                "invalid_data": self._pretty_json(ti.test_data_sets.invalid if ti.test_data_sets else {}),
            })
        return sections

    def to_doc_text(self) -> str:
        blocks: List[str] = []
        for section in self.to_doc_sections():
            blocks.extend([
                f"# {section['title']}",
                *section["meta"],
                "步骤：",
                self._format_steps(section["steps"]),
                f"预期结果：{section['expected_result']}",
                f"测试策略：{section['methodology']}",
                f"正常数据：{section['valid_data']}",
                f"异常数据：{section['invalid_data']}",
                "",
            ])
        return "\n".join(blocks).strip()

    def to_excel(self) -> bytes:
        output = BytesIO()
        df = self._display_dataframe()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='TestCases')
        return output.getvalue()

    def to_feishu_excel(self) -> bytes:
        """
        Export for Feishu Import (Generic mapping).
        """
        output = BytesIO()
        df_feishu = self._display_dataframe()

        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df_feishu.to_excel(writer, index=False, sheet_name='FeishuImport')
        return output.getvalue()

    def to_pytest(self) -> str:
        """
        Generates a Pytest-compatible Python script from test cases.
        Includes @pytest.mark.parametrize for multiple data sets.
        """
        code = [
            "import pytest",
            "import logging",
            "",
            "logging.basicConfig(level=logging.INFO)",
            "logger = logging.getLogger(__name__)",
            "",
            '"""',
            "AI Generated Test Suite (Beta 2.0)",
            f"Total Cases: {len(self.test_cases)}",
            '"""',
            ""
        ]
        
        import re
        for tc in self.test_cases:
            ti = self._ti(tc)
            clean_title = re.sub(r'[^a-zA-Z0-9_]', '_', tc.title or "test_case")
            func_name = f"test_{tc.test_case_id.lower().replace('-', '_')}_{clean_title[:30].lower()}"
            
            # 1. Prepare Parametrize Data
            data_sets = []
            if ti.test_data_sets:
                valid_data = ti.test_data_sets.valid
                invalid_data = ti.test_data_sets.invalid
                if valid_data: data_sets.append((valid_data, "valid"))
                if invalid_data: data_sets.append((invalid_data, "invalid"))

            # 2. Add Markers & Parametrize
            code.append(f"@pytest.mark.{tc.priority.lower()}")
            code.append(f"@pytest.mark.{tc.dimension.lower()}")
            
            if data_sets:
                # Format: @pytest.mark.parametrize("data, data_type", [(...), (...)])
                param_str = ", ".join([f"({json.dumps(d)}, '{t}')" for d, t in data_sets])
                code.append(f"@pytest.mark.parametrize(\"test_input, expected_type\", [{param_str}])")
                func_def = f"def {func_name}(test_input, expected_type):"
            else:
                func_def = f"def {func_name}():"

            # 3. Function definition
            code.append(func_def)
            code.append(f'    """')
            code.append(f'    {tc.title}')
            code.append(f'    Requirement ID: {tc.related_req_id}')
            code.append(f'    Precondition: {ti.pre_condition}')
            code.append(f'    """')
            
            if data_sets:
                code.append(f'    logger.info(f"Running {{expected_type}} test with input: {{test_input}}")')
            
            code.append(f'    logger.info("Starting test: {tc.title}")')
            for i, step in enumerate(ti.steps):
                safe_step = step.replace('"', '\\"')
                code.append(f'    # Step {i+1}: {safe_step}')
            
            safe_expected = ti.expected_result.replace('"', '\\"')
            code.append(f'    # Expected: {safe_expected}')
            code.append(f'    logger.info("Verifying expected result: {safe_expected}")')
            code.append(f'    assert True # Placeholder for {safe_expected}')
            code.append("")
            
        return "\n".join(code)
