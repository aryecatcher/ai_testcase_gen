import pandas as pd
from io import BytesIO
from typing import List
from loguru import logger
from ...models.domain import TestCase

class TestCaseExporter:
    def __init__(self, test_cases: List[TestCase]):
        self.test_cases = test_cases

    def _to_dataframe(self) -> pd.DataFrame:
        data = []
        for tc in self.test_cases:
            # Flatten structure for Excel
            item = {
                "Case ID": tc.test_case_id,
                "Title": tc.title,
                "Priority": tc.priority,
                "Type": tc.dimension,
                "Precondition": tc.test_instruction.pre_condition,
                "Steps": "\n".join(tc.test_instruction.steps),
                "Expected Result": tc.test_instruction.expected_result,
                "Related Requirement": tc.related_req_id
            }
            data.append(item)
        return pd.DataFrame(data)

    def to_excel(self) -> bytes:
        output = BytesIO()
        df = self._to_dataframe()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='TestCases')
        return output.getvalue()

    def to_feishu_excel(self) -> bytes:
        """
        Export for Feishu Import (Generic mapping).
        """
        output = BytesIO()
        df = self._to_dataframe()
        
        # Rename columns to match Feishu if needed
        rename_map = {
            "Title": "用例标题",
            "Precondition": "前置条件",
            "Steps": "步骤描述",
            "Expected Result": "预期结果",
            "Priority": "优先级",
            "Type": "用例类型"
        }
        # Select only relevant columns or keep all? Keep all for now but ensure mapped ones exist
        df_feishu = df.rename(columns=rename_map)
        
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df_feishu.to_excel(writer, index=False, sheet_name='FeishuImport')
        return output.getvalue()
