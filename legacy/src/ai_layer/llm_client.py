import os
import json
from openai import OpenAI
from loguru import logger
from .prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE

class LLMClient:
    def __init__(self, api_key: str = None, base_url: str = None, model: str = "gpt-3.5-turbo"):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL")
        self.model = model
        self.client = None
        
        if self.api_key:
            self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        else:
            logger.warning("No OpenAI API Key provided. Running in MOCK mode.")

    def generate_test_cases(self, context: str) -> list:
        if not self.client:
            logger.info("Using Mock LLM response.")
            return self._mock_response(context)

        logger.info("Calling LLM to generate test cases...")
        # 简单截断以防 Token 溢出，实际生产中应使用 Token 计算器
        prompt = USER_PROMPT_TEMPLATE.format(context=context[:6000]) 
        
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
            logger.error(f"LLM call failed: {e}")
            return self._mock_response(context, error=str(e))

    def refine_test_cases(self, failed_cases: list, feedback: str) -> list:
        """
        根据用户反馈优化失败的测试用例
        """
        if not self.client:
            logger.info("Using Mock LLM for refinement.")
            return failed_cases  # Mock 模式直接返回原样或简单修改

        logger.info("Calling LLM to refine test cases...")
        
        # 构造 Refine Prompt
        refine_prompt = f"""
        以下是初次生成的测试用例中被标记为“不通过”的用例及其反馈：
        ---
        Failed Cases: {json.dumps(failed_cases, ensure_ascii=False)}
        Feedback: {feedback}
        ---
        
        请根据反馈对上述用例进行修正，返回修正后的用例列表。
        保持输出格式与之前一致：{{"test_cases": [...]}}
        """
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是一个专业的测试用例评审专家，擅长根据反馈优化用例。"},
                    {"role": "user", "content": refine_prompt}
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

    def _mock_response(self, context, error=None):
        """
        Mock response for demo purposes when no API key is available or on error.
        """
        logger.info("Generating mock data based on context length...")
        base_cases = [
            {
                "module": "示例模块(Mock)",
                "test_point": "正常业务流程测试",
                "precondition": "系统正常运行",
                "steps": "1. 进入功能页面\n2. 输入合法数据\n3. 提交表单",
                "expected_result": "操作成功，数据正确保存",
                "test_data": "Input: Valid Data",
                "priority": "P0",
                "type": "功能测试"
            },
             {
                "module": "示例模块(Mock)",
                "test_point": "输入边界值测试",
                "precondition": "系统正常运行",
                "steps": "1. 进入功能页面\n2. 输入最大长度字符\n3. 提交表单",
                "expected_result": "系统应能正常处理或提示超长",
                "test_data": "Input: Max Length String",
                "priority": "P1",
                "type": "边界测试"
            }
        ]
        
        if error:
            base_cases.append({
                "module": "系统错误提示",
                "test_point": "LLM 调用失败",
                "precondition": "无",
                "steps": "检查 API Key 配置",
                "expected_result": f"Error: {error}",
                "test_data": "N/A",
                "priority": "P0",
                "type": "异常"
            })
            
        return base_cases
