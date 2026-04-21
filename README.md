# AI Test Case Generator

一个面向需求解析、测试用例生成、评审导出与知识沉淀的一体化项目。

当前仓库同时提供两套可运行形态：

- `Web V2`：`FastAPI + Vue 3`，推荐新部署使用
- `Legacy V1`：`FastAPI + Streamlit`，保留给历史使用者

## 适合谁

- 想在本地快速试用项目的人
- 想把项目部署到 Windows / Linux 服务器的人
- 想在团队里按统一文档分发和使用的人

## 功能概览

- 导入需求文档并结构化解析
- 生成测试用例并持久化任务状态
- 在评审页按生成批次筛选、导出 Excel / 飞书
- 按质量特性统计需求与用例
- 使用 NetworkX / Neo4j 维护知识图谱
- 支持 SQLite 本地试用与 PostgreSQL 服务器部署

## 版本形态

- `Web V2`
  - 前端目录：`frontend/`
  - 后端目录：`src/`
  - 推荐部署：Linux + Nginx + systemd + PostgreSQL
- `Legacy V1`
  - 前端目录：`ui/`
  - 后端目录：`src/`
  - 推荐用途：历史兼容、单机快速试用

详细说明见 [VERSIONS.md](file:///e:/internship/fang/ai_testcase_gen/docs/VERSIONS.md)。

## 快速开始

### 1. 准备环境

- Python 3.11+
- Node.js 20+
- 可选：PostgreSQL 15+
- 可选：Ollama 或其他 OpenAI 兼容模型服务

### 2. 克隆并准备配置

```bash
git clone https://github.com/aryecatcher/ai_testcase_gen.git
cd ai_testcase_gen
cp .env.example .env
```

Windows PowerShell 可直接复制 `.env.example` 为 `.env`。

### 3. 安装依赖

Linux / macOS:

```bash
bash scripts/bootstrap.sh
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/bootstrap.ps1
```

### 4. 启动推荐版本 `Web V2`

启动后端：

Linux / macOS:

```bash
bash scripts/start-backend.sh
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start-backend.ps1
```

启动前端：

Linux / macOS:

```bash
bash scripts/start-frontend.sh
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start-frontend.ps1
```

默认访问地址：

- 前端：`http://127.0.0.1:5173`
- 后端：`http://127.0.0.1:8002`

### 5. 启动兼容版本 `Legacy V1`

Linux / macOS:

```bash
bash scripts/start-legacy-ui.sh
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start-legacy-ui.ps1
```

默认访问地址：

- Streamlit：`http://127.0.0.1:8504`

## Docker 部署

项目提供容器化部署入口，适合 Linux 服务器快速落地：

```bash
cp .env.docker.example .env.docker
docker compose --env-file .env.docker up --build
```

默认服务：

- `frontend`：Nginx 托管的 Vue 前端
- `backend`：FastAPI API
- `postgres`：PostgreSQL

详细说明见 [DEPLOYMENT_MATRIX.md](file:///e:/internship/fang/ai_testcase_gen/docs/DEPLOYMENT_MATRIX.md) 和 [POSTGRESQL_DEPLOY.md](file:///e:/internship/fang/ai_testcase_gen/docs/POSTGRESQL_DEPLOY.md)。

## 常用环境变量

- `DATABASE_URL`
  - 生产环境建议显式配置为 PostgreSQL
- `OPENAI_BASE_URL`
  - 指向 Ollama 或其他 OpenAI 兼容服务
- `OPENAI_API_KEY`
  - 本地 Ollama 可填占位值 `ollama`
- `LLM_MODEL_GEN`
  - 生成模型名称
- `GENERATION_QUEUE_WORKERS`
  - 后台生成 worker 数量
- `KG_BACKEND`
  - `networkx` / `neo4j` / `hybrid` / `auto`
- `BACKEND_URL`
  - Legacy Streamlit 连接后端地址

## 推荐部署路线

- 本地试用：`SQLite + Web V2`
- 团队试用：`PostgreSQL + Web V2`
- Linux 服务器：`Nginx + FastAPI + systemd + PostgreSQL`
- 历史兼容：`Legacy V1`

## 文档导航

- [PROJECT_STRUCTURE.md](file:///e:/internship/fang/ai_testcase_gen/PROJECT_STRUCTURE.md)
- [DEPLOYMENT_MATRIX.md](file:///e:/internship/fang/ai_testcase_gen/docs/DEPLOYMENT_MATRIX.md)
- [VERSIONS.md](file:///e:/internship/fang/ai_testcase_gen/docs/VERSIONS.md)
- [POSTGRESQL_DEPLOY.md](file:///e:/internship/fang/ai_testcase_gen/docs/POSTGRESQL_DEPLOY.md)
- [RELEASE_PROCESS.md](file:///e:/internship/fang/ai_testcase_gen/docs/RELEASE_PROCESS.md)
- [RELEASE_NOTES_v2.0.0.md](file:///e:/internship/fang/ai_testcase_gen/docs/RELEASE_NOTES_v2.0.0.md)
- [CONTRIBUTING.md](file:///e:/internship/fang/ai_testcase_gen/CONTRIBUTING.md)
- [SECURITY.md](file:///e:/internship/fang/ai_testcase_gen/SECURITY.md)
- [CHANGELOG.md](file:///e:/internship/fang/ai_testcase_gen/CHANGELOG.md)

## 版本发布建议

建议按语义化版本打标签：

- `v2.x.x`：当前 Web 版主线
- `v1.x.x`：Legacy Streamlit 兼容线

如果你准备对外发布，建议为每个稳定里程碑补一条 `Git tag` 和 `Release notes`。

## 开源协作

- 许可证：`MIT`
- 欢迎通过 Issue / Pull Request 参与改进
- 提交前建议先阅读 [CONTRIBUTING.md](file:///e:/internship/fang/ai_testcase_gen/CONTRIBUTING.md)
