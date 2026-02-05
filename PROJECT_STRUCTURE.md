# AI 测试用例生成器 - 项目结构文档

本文档概述了 `ai_test_case_gen` 项目的组织结构。

## 📂 项目根目录 (`e:\internship\fang\ai_test_case_gen`)

| 文件/文件夹 | 用途 |
|---|---|
| `src/` | **核心源代码**。包含业务逻辑和后端服务。 |
| `ui/` | **用户界面**。包含 Streamlit 前端应用程序。 |
| `data/` | **数据存储**。处理文件 I/O 和临时数据持久化。 |
| `docs/` | **文档**。参考文档、技术规范和示例文件。 |
| `tests/` | **测试脚本**。用于核心模块和集成的验证脚本。 |
| `legacy/` | **遗留代码**。归档的 V1 应用程序代码（仅供参考）。 |
| `temp_archive/` | **临时归档**。旧的导出文件和临时文件（可安全删除）。 |
| `requirements.txt` | **依赖项**。Python 包需求文件。 |
| `README.md` | **项目自述文件**。一般介绍。 |

---

## 🏗️ 详细模块说明

### 1. `src/` (源代码)

*   **`core/`**: V2 架构核心实现。
    *   **`ingestion/`**: 文档解析与理解。
        *   `doc_processor.py`: 使用 **IBM Docling** 的高精度文档解析器，将文档转换为处理块。
        *   `ingestor.py`: 需求摄入器，使用 **Jieba NLP** 和正则表达式从文档块中提取结构化需求，并进行语义丰富。
    *   **`kg/`**: 知识图谱服务。
        *   `graph_service.py`: 图引擎 (NetworkX)，用于管理领域规则（如手机号长度限制）和场景关系的构建与查询。
    *   **`ai/`**: LLM 集成与语义分析。
        *   `llm_service.py`: LLM 服务接口，负责与 OpenAI/DeepSeek 模型交互，执行用例生成和优化任务。
        *   `prompts.py`: 集中管理提示词模板，包含系统提示词和用户提示词模板。
        *   `req_parser.py`: 轻量级 SRL (语义角色标注) 解析器，用于从需求文本中提取主体、动作、条件和结果。
        *   `few_shots.py`: 提供 Few-Shot (少样本) 示例，根据需求类型动态注入高质量示例到提示词中。
        *   `optimizer.py`: 用例优化器，分析生成的用例是否存在方法论缺失（如缺少边界值分析）并提供改进建议。
    *   **`generation/`**: 测试用例生成引擎。
        *   `generator.py`: 生成控制器，编排 LLM 调用、知识图谱查询和数据合成流程。
        *   `data_synthesizer.py`: 数据合成器，用于生成测试所需的具体数据（有效/无效集）。
        *   `validators.py`: 验证拦截器，对 AI 生成的数据进行规则校验和修正（如强制手机号为 11 位）。
    *   **`output/`**: 导出模块。
        *   `exporter.py`: 通用导出器，支持将测试用例导出为 Excel 和 CSV 格式。
        *   `feishu_client.py`: 飞书集成客户端，用于将用例同步到飞书多维表格。
        *   `postman_exporter.py`: Postman 导出器，将接口类型的测试用例转换为 Postman Collection JSON 格式。
    *   **`feedback/`**: 反馈循环。
        *   `manager.py`: 反馈管理器，处理用户对用例的反馈，并调用 LLM 进行用例的迭代优化。

*   **`models/`**: 数据模型。
    *   `domain.py`: 定义核心 Pydantic 数据模型，包括 `Requirement`（需求）、`TestCase`（测试用例）、`ProjectContext`（项目上下文）等。

*   **`utils/`**: 通用工具。
    *   `logger.py`: 日志记录工具配置。
    *   `__init__.py`: 包初始化文件。

### 2. `ui/` (前端)

*   `main.py`: **主应用程序入口点**。
    *   运行命令: `streamlit run ui/main.py`
    *   功能: 专业仪表板、溯源矩阵、分屏评审。

### 3. `docs/` (文档)

*   `TECHNICAL_DOC.md`: 详细的技术实施计划。
*   `1.txt`: 原始技术需求文本。
*   `sample_requirements.docx`: 用于测试的示例输入文件。
*   `需求规格说明书*.docx`: 项目参考规范。

### 4. `tests/` (验证)

*   `test_core_improvements.py`: 用于验证 NLP 提取和图谱逻辑的脚本。
*   `test_docling_integration.py`: 用于验证 IBM Docling 集成的脚本。
*   `create_sample_doc.py`: 用于生成示例 Word 文档的工具。

### 5. `legacy/` (归档 V1)

*   `app.py`: 旧的 V1 入口点（已弃用）。
*   `src/input_layer`, `src/ai_layer`, `src/output_layer`: V1 模块实现。

---

## 🚀 如何运行

1.  **安装依赖**:
    ```bash
    pip install -r requirements.txt
    pip install docling  # 如果尚未在 requirements 中
    ```

2.  **运行应用程序**:
    ```bash
    streamlit run ui/main.py
    ```

3.  **运行测试**:
    ```bash
    python tests/test_core_improvements.py
    ```
