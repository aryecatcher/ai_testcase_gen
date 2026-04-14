# AI 测试用例生成 - 项目结构文档

本文档描述当前 `ai_testcase_gen` 的目录结构、运行形态与核心数据流（截至当前仓库代码状态）。

## 项目根目录

根目录示例：`e:\internship\fang\ai_testcase_gen`

| 目录/文件 | 说明 |
|---|---|
| `src/` | 后端与核心业务逻辑（FastAPI + 生成流程 + KG + 导出器）。 |
| `ui/` | Streamlit 前端（需求导入 / 生成 / 人工评审 / 导出 / KG 工作台）。 |
| `data/` | 本地持久化数据（SQLite、KG JSON、审计日志、上下文备份等）。 |
| `docs/` | 需求样例与技术文档。 |
| `tests/` | 核心链路/回归测试脚本。 |
| `legacy/` | V1 归档（仅参考，不参与当前运行）。 |
| `temp_archive/` | 临时文件/历史导出物（可清理）。 |
| `.streamlit/` | Streamlit 配置。 |
| `requirements.txt` | Python 依赖。 |
| `README.md` | 使用说明（面向用户）。 |
| `PROJECT_STRUCTURE.md` | 本文（面向维护者）。 |

## 运行形态（服务与端口）

| 组件 | 入口 | 默认端口 | 说明 |
|---|---|---:|---|
| 后端 API | [src/api/main.py](file:///e:/internship/fang/ai_testcase_gen/src/api/main.py) | 8002 | FastAPI，提供解析后生成/修正流式接口、KG 学习与摘要。 |
| 前端 UI | [ui/main.py](file:///e:/internship/fang/ai_testcase_gen/ui/main.py) | 8504 | Streamlit UI，负责上传解析、展示与导出。 |
| LLM 推理 | Ollama / OpenAI 兼容 API | 11434 | 前端固定把模型与 base_url 写到请求头（默认：Ollama `deepseek-r1:7b`）。 |

## 核心数据流（工作流闭环）

1. 需求导入：`ui/main.py` 上传文档 → `src/core/ingestion` 解析为结构化 `Requirement`
2. 生成用例：UI 调用 `/generate/stream`（SSE）→ `src/core/generation` 生成 `TestCase`
3. 清洗与标准化：`validators.py` 统一清洗标题/前置/预期/步骤，并把步骤规范成 `1. 2. 3...`
4. 人工评审：UI 支持编辑、AI 修正、删除用例；编辑/修正会自动沉淀“待确认 KG 候选”
5. 知识图谱：`/kg/learn*` 入库规则/场景/方法/模板/故障复盘；UI 提供候选池确认入库
6. 导出与集成：Excel / 飞书（Bitable/Sheet/云文档）/ Postman / Pytest / TestLink（入口已预留）

## 详细模块说明

### 1) `src/api/`（后端 API）

- [main.py](file:///e:/internship/fang/ai_testcase_gen/src/api/main.py)：FastAPI 应用
  - `GET /health`：健康检查
  - `POST /generate/stream`：流式生成用例（SSE）
  - `POST /refine/stream`：流式修正用例（SSE）
  - `GET /kg/summary`：KG 模块摘要（规则/场景/方法/模板/复盘统计）
  - `POST /kg/learn`：从用例或文本学习规则
  - `POST /kg/learn/history`：从历史反馈提炼并学习规则
  - `POST /kg/learn/postmortem`：学习故障复盘（FailureMode）
  - `POST /kg/learn/item`：确认入库通用知识项（Rule/Business/TestMethod/Template/FailureMode）

### 2) `src/core/`（核心业务）

#### `ingestion/` 需求解析
- [doc_processor.py](file:///e:/internship/fang/ai_testcase_gen/src/core/ingestion/doc_processor.py)：Docling 文档解析（Word/Docx 等）
- [ingestor.py](file:///e:/internship/fang/ai_testcase_gen/src/core/ingestion/ingestor.py)：需求抽取与结构化（含中文分词/规则提取）
- [smart_hierarchical_parser.py](file:///e:/internship/fang/ai_testcase_gen/src/core/ingestion/smart_hierarchical_parser.py)：层级化解析辅助

#### `ai/` 模型调用与语义分析
- [llm_service.py](file:///e:/internship/fang/ai_testcase_gen/src/core/ai/llm_service.py)：LLM 统一入口（生成/评审/规则提取）
- [prompts.py](file:///e:/internship/fang/ai_testcase_gen/src/core/ai/prompts.py)：提示词模板
- [req_parser.py](file:///e:/internship/fang/ai_testcase_gen/src/core/ai/req_parser.py)：需求轻量结构化解析（过滤噪音片段）
- [optimizer.py](file:///e:/internship/fang/ai_testcase_gen/src/core/ai/optimizer.py)：生成结果方法论补全建议
- [few_shots.py](file:///e:/internship/fang/ai_testcase_gen/src/core/ai/few_shots.py)：Few-shot 示例注入

#### `generation/` 用例生成与校验
- [generator.py](file:///e:/internship/fang/ai_testcase_gen/src/core/generation/generator.py)：生成控制器与增量生产
- [workflow.py](file:///e:/internship/fang/ai_testcase_gen/src/core/generation/workflow.py)：生成/修正工作流编排
- [validators.py](file:///e:/internship/fang/ai_testcase_gen/src/core/generation/validators.py)：生成结果清洗与规范（步骤编号、脏文本剔除、预期改写）
- [data_synthesizer.py](file:///e:/internship/fang/ai_testcase_gen/src/core/generation/data_synthesizer.py)：测试数据合成（有效/无效集）

#### `kg/` 知识图谱（“专家大脑”）
- [graph_service.py](file:///e:/internship/fang/ai_testcase_gen/src/core/kg/graph_service.py)：KG 查询、缓存、学习入口
- [networkx_repo.py](file:///e:/internship/fang/ai_testcase_gen/src/core/kg/networkx_repo.py)：本地 NetworkX 图仓储（含增强本体自升级）
- [neo4j_repo.py](file:///e:/internship/fang/ai_testcase_gen/src/core/kg/neo4j_repo.py)：Neo4j 兼容接口（当前以 NetworkX 为主）
- [matcher.py](file:///e:/internship/fang/ai_testcase_gen/src/core/kg/matcher.py)：关键词/别名匹配
- [repository.py](file:///e:/internship/fang/ai_testcase_gen/src/core/kg/repository.py)：仓储抽象

#### `output/` 导出与第三方集成
- [exporter.py](file:///e:/internship/fang/ai_testcase_gen/src/core/output/exporter.py)：统一导出器（Excel/飞书记录/Sheet values/云文档分节）
- [feishu_client.py](file:///e:/internship/fang/ai_testcase_gen/src/core/output/feishu_client.py)：飞书 OpenAPI 客户端（Bitable/Sheet/Docx）
- [postman_exporter.py](file:///e:/internship/fang/ai_testcase_gen/src/core/output/postman_exporter.py)：Postman collection 导出

#### `feedback/` 反馈闭环
- [manager.py](file:///e:/internship/fang/ai_testcase_gen/src/core/feedback/manager.py)：用例反馈与 AI 修正管理

#### `integration/` 外部工具
- [testlink_service.py](file:///e:/internship/fang/ai_testcase_gen/src/core/integration/testlink_service.py)：TestLink 导入/集成接口（UI 有入口）

### 3) `src/models/`（领域模型）

- [domain.py](file:///e:/internship/fang/ai_testcase_gen/src/models/domain.py)：核心模型
  - `Requirement`：需求条目（含 `ingestion_metadata`、`extracted_entities`）
  - `TestCase`：用例（含 `test_instruction`、`system_env`、`feedback_history`）
  - `ProjectContext`：项目上下文（需求/用例集合、统计信息）

### 4) `src/data/`（持久化）

- [database.py](file:///e:/internship/fang/ai_testcase_gen/src/data/database.py)：SQLite 初始化与查询
- [migration.py](file:///e:/internship/fang/ai_testcase_gen/src/data/migration.py)：迁移辅助

### 5) `ui/`（前端）

- [main.py](file:///e:/internship/fang/ai_testcase_gen/ui/main.py)：Streamlit 应用入口（默认端口 8504）
  - 解析批次隔离：默认只展示“当前文档批次”的用例，并记录真实生成时间
  - 人工评审：编辑/修正/删除；步骤一键规范化；候选 KG 规则自动沉淀
  - 导出：Excel/飞书 Bitable+Sheet+Docx / Postman / Pytest
  - KG 工作台：候选池批量确认入库、故障补充、Excel/CSV 批量导入候选

## 数据与状态文件

| 文件 | 说明 |
|---|---|
| `data/app_database.db` | SQLite 数据库（requirements/test_cases）。 |
| `data/kg_graph.json` | NetworkX KG 图数据（会随版本自升级补齐增强本体）。 |
| `data/kg_audit.json` | KG 审计日志。 |
| `data/project_context.json.bak` | UI 上下文备份。 |
| `data/storage.py` | UI 本地 JSON 存储工具（例如 `kg_candidates.json`）。 |

## 环境变量（常用）

| 变量 | 说明 |
|---|---|
| `BACKEND_URL` | 前端连接后端地址（默认 `http://localhost:8002`）。 |
| `OPENAI_BASE_URL` / `OPENAI_API_KEY` | LLM OpenAI 兼容接口地址与 Key（本地 Ollama 可用占位 key）。 |
| `LLM_MODEL_GEN` / `LLM_MODEL_JUDGE` | 生成模型 / 判官模型。 |
| `KG_BACKEND` | KG 后端：`auto` / `networkx` / `neo4j` / `hybrid`。 |
| `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` | 可选：Neo4j 配置（当 `KG_BACKEND` 使用 neo4j/hybrid/auto 时生效）。 |
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | 飞书自建应用凭证（用于换取 `tenant_access_token`）。 |
| `FEISHU_TENANT_TOKEN` | 可选：直接填写 tenant_access_token。 |
| `FEISHU_APP_TOKEN` / `FEISHU_TABLE_ID` | Bitable 目标表配置。 |
| `FEISHU_SPREADSHEET_TOKEN` / `FEISHU_SHEET_ID` | Sheet 目标配置（支持自动探测/自动创建）。 |
| `FEISHU_DOCUMENT_ID` / `FEISHU_DOC_FOLDER_TOKEN` | 云文档目标配置（可选）。 |
| `FEISHU_REQUIREMENT_LINK_BASE_URL` | 可选：需求链接前缀或模板（如 `.../{req_id}`）。 |

## 快速上手（详细步骤）

### 0) 准备环境

- Python：建议使用虚拟环境（Conda / venv 均可）
- 目录：在项目根目录执行命令：`e:\internship\fang\ai_testcase_gen`
- 可选：准备本地 LLM（默认使用 Ollama 的 OpenAI 兼容接口）

### 1) 安装依赖

```bash
pip install -r requirements.txt
```

### 2) 准备配置（推荐）

1) 从示例复制一份 `.env`（Windows 可在资源管理器里复制重命名）  
2) 按需修改变量（最少通常只需要 `BACKEND_URL`；飞书/Neo4j 可后配）

示例文件：`.env.example`

### 3) 启动后端（FastAPI，8002）

```bash
python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8002 --reload
```

后端启动后可用以下地址验证：

- `GET http://127.0.0.1:8002/health` → `{"status":"healthy"}`

### 4) 启动前端（Streamlit，8504）

```bash
streamlit run ui/main.py --server.port 8504
```

### 5) 前端使用流程（推荐顺序）

1) 左侧边栏 → 点击“初始化服务”（会做模型连接测试）  
2) 进入「导入需求」→ 上传 DOCX / XLSX / TXT / JSON / MD → 点击“解析文档”  
3) 进入「生成用例」→ 选择范围（有版本差异时默认“仅新增/变更需求”）→ 点击“开始生成”  
4) 进入「评审与导出」：
   - 在“AI 质量评估”里可评估当前批次/当前需求
   - 编辑用例、提交修正意见（可触发候选 KG 规则沉淀）
   - 导出 Excel / 飞书 / Postman / Pytest 等
5) 进入「知识图谱」：
   - 查看摘要与模块知识
   - 在“待确认图谱候选”里批量确认入库
   - 入库时会触发 KG 本体自升级（补模块/功能节点、挂全局域、写审计）

### 6) 可选：本地 LLM（Ollama）与端口

- UI 当前默认把模型与 base_url 固定写到请求头（默认：`deepseek-r1:7b` + `http://localhost:11434/v1`）
- 如果本地启用 Ollama，确保 11434 端口可访问且对应模型已可用

### 7) 可选：Neo4j（当需要图数据库能力）

- 设置 `.env`：`KG_BACKEND=neo4j` 或 `hybrid/auto`
- 启动 Neo4j（示例端口通常为 bolt 7687，浏览器 7474）
- 如果未安装 neo4j driver，会提示安装依赖：`pip install neo4j`

### 8) 常见问题排查

- 前端提示“后端服务未启动”：确认后端进程在跑，并检查 `BACKEND_URL` 是否一致（默认 `http://localhost:8002`）
- 评估/生成阶段报模型连接失败：确认本地 Ollama 或远端 OpenAI 兼容服务可用（Base URL / API Key）
- 飞书推送失败：优先检查应用权限、目标资源权限、以及字段名/表头是否匹配

## 测试与回归

```bash
python -m pytest -q
```
