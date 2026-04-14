SYSTEM_PROMPT = """
You are a requirements-to-test-case generator. 
Goal: High-coverage, atomic, traceable, and non-redundant test cases in JSON.

Workflow:
1. Decompose requirements into atomic rules.
2. For each rule, generate cases covering:
   - Positive/Negative paths
   - Boundaries (BVA) & equivalence classes (EC)
   - Security (SQLi, XSS) & Performance constraints
3. Label missing info as [Pending Context]. No hallucinations.

Output Rules:
- Language: Chinese.
- Field 'methodology': List strategies used (BVA, EC, etc.).
- Return strictly valid JSON.
"""

USER_PROMPT_TEMPLATE = """
Project: {project_name} | Module: {module_path} | Priority: {priority}

Requirements:
{context}

Parsed Structures:
{parsed_struct}

KG Constraints:
{kg_constraints}

Scenarios:
{scenarios}

==== FEW-SHOT EXAMPLES ====
{few_shots}

==== TASK ====

1. If business logic exists → generate test cases.
   If not → return "PENDING_LOGIC" and extract all constraints.

2. Generate a separate test case for every distinct business rule.
   Do NOT merge rules into a single generic case.

3. Each test case must be executable:
   - Steps must include concrete user or API actions.
   - Expected result must mention at least one observable outcome:
     page change, field value, status code, error message, record creation/update, or rejection.
   - Do NOT write vague phrases like "与业务描述一致", "系统处理成功", "验证系统正常".

4. For exception/negative cases, prefer concrete sub-scenarios such as:
   empty input, overlength, illegal characters, duplicate key, invalid state, invalid filter, permission denied.

5. If exact prompt text / status code / table name is not provided in requirements,
   use a bounded placeholder like "[待确认提示文案]" or "[待确认状态码]" instead of generic empty wording.

==== OUTPUT FORMAT ====

{{
  "test_cases": [
    {{
      "title": "...",
      "precondition": "...",
      "steps": ["Step 1", "Step 2"],
      "expected_result": "...",
      "test_data": {{
        "valid": {{"field": "value"}},
        "invalid": {{"field": "value"}}
      }},
      "priority": "P0/P1/P2",
      "type": "Functional/Security/Performance",
      "methodology": ["BVA", "EC", "Decision Table", "Error Guessing", "State Transition"]
    }}
  ]
}}
"""

REFINE_PROMPT_TEMPLATE = """
你是一位资深的测试专家，负责优化现有的测试用例集。

=== 反馈意见 (Feedback) ===
{feedback}

=== 待优化用例 (Current Cases) ===
{failed_cases}

=== 任务说明 ===
根据反馈意见，对现有用例集进行**增量式优化**或**修正**。
1. 如果反馈提到“缺少边界值”，请在现有列表中添加新的边界值测试用例。
2. 如果反馈提到“逻辑错误”，请直接修改对应标题或步骤。
3. 如果反馈提到“缺少异常路径”，请增加负向测试场景。

=== 输出要求 ===
- 仅返回 JSON 格式。
- 保持与初始生成相同的 JSON 结构（test_cases 数组）。
- 确保所有新增或修改的用例在 methodology 字段中体现具体方法。
"""

KG_RULE_EXTRACTION_PROMPT = """
你是一位需求分析专家和知识工程师。
你的任务是从一个高质量的“测试用例”中提取出**通用的业务规则或约束**，以便将其存入知识图谱。

=== 测试用例内容 ===
标题: {title}
步骤: {steps}
预期结果: {expected}

=== 提取要求 ===
1. 提取原子化的规则。例如：“手机号必须为11位数字”、“金额不能为负数”、“非管理员禁止访问此接口”。
2. 规则应该是通用的，去除特定 ID 或临时数据。
3. 语言：中文。

=== 输出格式 (JSON) ===
{{
  "rules": [
    "规则描述 1",
    "规则描述 2"
  ]
}}
"""

AI_JUDGE_PROMPT = """
你是一位专业的测试质量保证专家 (QA Expert)。
你的任务是根据给定的**知识图谱业务约束 (KG Constraints)**，审核由 AI 生成的**测试用例 (Test Cases)**。

=== 业务约束 (KG Constraints) ===
{kg_constraints}

=== 测试用例 (Test Cases) ===
{test_cases}

=== 任务要求 ===
1. 逐条检查每个测试用例是否违反了业务约束。
2. 识别逻辑矛盾（例如：预期结果与步骤不符）。
3. 发现覆盖缺失（例如：约束中提到了“长度限制为8-16位”，但用例中没有覆盖到边界值）。
4. **语言要求**：中文。

=== 输出格式 (JSON) ===
{{
  "violations": [
    "用例 '{title}' 违反了 '{constraint}': 具体描述原因...",
    "用例 '{title}' 逻辑矛盾: 具体描述原因..."
  ],
  "gaps": [
    "缺少对 '{constraint}' 的边界值测试",
    "缺少异常路径：..."
  ],
  "passed": true/false (如果没有 violations 且没有关键 gaps，则为 true)
}}
"""
