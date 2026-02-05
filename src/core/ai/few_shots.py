from ...models.domain import RequirementType

def get_examples(req_type: RequirementType) -> str:
    """
    Return few-shot examples as a JSON-like string to inject into prompt.
    """
    if req_type == RequirementType.FUNCTIONAL:
        return """
Examples:
1) Boundary Value
{
  "title": "密码长度边界值",
  "precondition": "用户在登录页",
  "steps": ["输入7位密码", "点击登录"],
  "expected_result": "提示长度不足",
  "test_data": {"invalid": {"password": "Ab1234!"}, "valid": {"password": "Ab12345!"}},
  "priority": "P1",
  "type": "Functional",
  "methodology": ["Boundary Value"]
}
"""
    if req_type == RequirementType.INTERFACE:
        return """
Examples:
1) API Status Code & Schema
{
  "title": "接口返回校验",
  "precondition": "API可用",
  "steps": ["发送合法请求"],
  "expected_result": "HTTP 200，返回JSON结构符合定义",
  "test_data": {"valid": {"payload": {"user":"u"}}},
  "priority": "P0",
  "type": "Interface",
  "methodology": ["Decision Table"]
}
"""
    if req_type == RequirementType.PERFORMANCE:
        return """
Examples:
1) Concurrency Baseline
{
  "title": "并发性能基线",
  "precondition": "性能环境就绪",
  "steps": ["启动100并发压测"],
  "expected_result": "RT<500ms, CPU<80%",
  "test_data": {"valid": {"users": 100}},
  "priority": "P1",
  "type": "Performance",
  "methodology": ["Performance Benchmark"]
}
"""
    return "Examples: None"
