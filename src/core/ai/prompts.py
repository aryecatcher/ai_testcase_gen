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

QUALITY_CLASSIFICATION_PROMPT = """
你是一位软件质量专家，需要根据 ISO/IEC 25010 风格的质量特性定义，对输入内容进行单一主分类。

=== 本项目采用的原始质量特性定义 ===
- 功能性: 在指定条件下使用时，产品或系统提供满足明确和隐含要求的功能程度。
- 性能效率: 性能与在指定条件下所使用的资源量有关。
- 兼容性: 在共享相同的硬件或软件环境的条件下，产品、系统或组件能够与其他产品、系统或组件交换信息，和/或执行其所需的功能的程度。
- 易用性: 在指定的使用周境中，产品或系统在有效性、效率和满意度特性方面为了指定的目标可为指定用户使用的程度。
- 可靠性: 系统、产品或组件在指定条件下、指定时间内执行指定功能的程度。
- 信息安全性: 产品或系统保护信息和数据的程度，以使用户、其他产品或系统具有与其授权类型和授权级别一致的数据访问度。
- 维护性: 产品或系统能够被预期的维护人员修改的有效性和效率的程度。
- 可移植性: 系统、产品或组件能够从一种硬件、软件、或者其他运行（或使用）环境迁移到另一种环境的有效性和效率的程度。

=== 当前运行时分类定义 ===
{definitions}

=== 待分类文本 ===
主文本:
{text}

补充上下文:
{extra_context}

=== 任务要求 ===
1. 只能从给定分类中选择 1 个最匹配的主分类。
2. 优先判断最主要测试目标，而不是表面关键词。
3. 如果文本同时涉及多个分类，选择最核心、最应优先测试的那个。
4. 如果无法明确判断，默认归到“功能性”。
5. 语言要求：中文。

=== 输出格式(JSON) ===
{{
  "category": "功能性/性能效率/兼容性/易用性/可靠性/信息安全性/维护性/可移植性",
  "reason": "不超过60字，说明判断依据",
  "confidence": 0.0
}}
"""
