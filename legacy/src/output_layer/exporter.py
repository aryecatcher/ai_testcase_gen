import pandas as pd
from typing import List, Dict
import io
from loguru import logger

class TestCaseExporter:
    def __init__(self, test_cases: List[Dict]):
        self.test_cases = test_cases
        if not test_cases:
            self.df = pd.DataFrame(columns=["module", "test_point", "precondition", "steps", "expected_result", "test_data", "priority", "type"])
        else:
            self.df = pd.DataFrame(test_cases)

    def to_csv(self) -> str:
        logger.info("Exporting to CSV...")
        return self.df.to_csv(index=False)

    def to_excel(self) -> bytes:
        logger.info("Exporting to Excel...")
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            self.df.to_excel(writer, index=False, sheet_name='TestCases')
        return output.getvalue()
    
    def to_zentao_csv(self) -> str:
        """
        导出适配禅道导入格式的 CSV
        """
        logger.info("Exporting to ZenTao format...")
        # 简单映射，实际禅道导入可能需要更严格的表头
        mapping = {
            "module": "所属模块",
            "test_point": "用例标题",
            "precondition": "前置条件",
            "steps": "步骤",
            "expected_result": "预期",
            "priority": "优先级",
            "type": "用例类型",
            "test_data": "测试数据" # 禅道可能不需要这个字段，或者放在备注里
        }
        
        # 复制一份，避免修改原 DF
        zentao_df = self.df.copy()
        
        # 确保所有列都存在，不存在的填空
        for col in mapping.keys():
            if col not in zentao_df.columns:
                zentao_df[col] = ""
                
        # 重命名
        zentao_df = zentao_df.rename(columns=mapping)
        
        # 只保留映射后的列
        valid_columns = [v for k, v in mapping.items() if k in self.df.columns or k in mapping]
        zentao_df = zentao_df[valid_columns]
        
        return zentao_df.to_csv(index=False)

    def to_feishu_excel(self) -> bytes:
        """
        导出适配飞书导入格式的 Excel
        飞书用例管理通常需要：用例标题、前置条件、步骤描述、预期结果、优先级、用例类型等
        """
        logger.info("Exporting to Feishu format...")
        mapping = {
            "test_point": "用例标题",
            "module": "所属模块", # 飞书可能需要映射到目录结构，这里先保留
            "precondition": "前置条件",
            "steps": "步骤描述",
            "expected_result": "预期结果",
            "priority": "优先级",
            "type": "用例类型"
        }
        
        feishu_df = self.df.copy()
        
        # 补全
        for col in mapping.keys():
            if col not in feishu_df.columns:
                feishu_df[col] = ""
                
        feishu_df = feishu_df.rename(columns=mapping)
        
        # 筛选
        valid_columns = [v for k, v in mapping.items() if k in self.df.columns or k in mapping]
        feishu_df = feishu_df[valid_columns]
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            feishu_df.to_excel(writer, index=False, sheet_name='ImportData')
        return output.getvalue()
