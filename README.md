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

基础通用：

- `Git`
- `Python 3.11+`
- `Node.js 20+`
- 建议确认 `python`、`pip`、`npm` 命令可直接使用

本地快速试用：

- 必需：`Python 3.11+`
- 必需：`Node.js 20+`
- 可选：`Ollama` 或其他 OpenAI 兼容模型服务
- 可选：`PostgreSQL 15+`
- 不装 PostgreSQL 也可以，默认可先用 `SQLite`

Linux 服务器部署：

- 必需：`python3`
- 必需：`python3-venv`
- 必需：`node` / `npm`
- 必需：`nginx`
- 建议：`rsync`
- 如果用 PostgreSQL：需提前安装并启动 `PostgreSQL 15+`
- 如果用本机模型：需提前安装并启动 `Ollama`
- 如果不用本机模型：需准备可访问的 OpenAI 兼容模型服务地址

Docker 部署：

- 必需：`Docker`
- 必需：`Docker Compose`
- 如果模型服务跑在宿主机：要确认容器能访问对应模型地址

建议先手工确认以下命令可用：

```bash
git --version
python --version
node --version
npm --version
```

Linux 服务器建议额外确认：

```bash
python3 --version
nginx -v
psql --version
ollama --version
docker --version
docker compose version
```

### 2. 克隆并准备配置

```bash
git clone https://github.com/aryecatcher/ai_testcase_gen.git
cd ai_testcase_gen
```

Windows PowerShell:

```powershell
git clone https://github.com/aryecatcher/ai_testcase_gen.git
cd ai_testcase_gen
```

推荐先运行配置向导：

```bash
python scripts/configure.py
```

Windows PowerShell:

```powershell
python scripts/configure.py
```

配置向导会根据你选择的模式，自动生成以下文件中的一部分：

- `.env`
- `frontend/.env.local`
- `.env.docker`
- `deploy/generated/ai-testcase-backend.service`
- `deploy/generated/nginx.production.conf`

如果你准备在 Linux 服务器上半自动完成配置，可以直接使用无交互模式：

```bash
python scripts/configure.py \
  --profile linux \
  --non-interactive \
  --yes \
  --database-mode postgres \
  --db-host 127.0.0.1 \
  --db-port 5432 \
  --db-user ai_testcase_user \
  --db-password change_me \
  --db-name ai_testcase_gen \
  --backend-port 8002 \
  --openai-base-url http://127.0.0.1:11434/v1 \
  --openai-api-key ollama \
  --llm-model-gen deepseek-r1:7b \
  --kg-backend networkx \
  --install-dir /opt/ai_testcase_gen \
  --frontend-dist-dir /var/www/ai_testcase_gen/frontend/dist \
  --server-name example.com
```

Linux 无交互模式会额外生成：

- `.env.production`
- `frontend/.env.production`
- `deploy/generated/ai-testcase-backend.service`
- `deploy/generated/ai-testcase-legacy-ui.service`
- `deploy/generated/nginx.production.conf`
- `deploy/generated/LINUX_DEPLOY.md`
- `deploy/generated/init_postgres.sh`
- `deploy/generated/check_ollama_model.sh`
- `deploy/generated/install_linux.sh`

其中：

- `LINUX_DEPLOY.md` 会把后续复制、启用 `systemd`、部署 `nginx` 的命令按顺序列出来
- `init_postgres.sh` 会在本机 PostgreSQL 场景下自动创建业务用户和数据库；远端 PostgreSQL 场景会给出可执行命令
- `check_ollama_model.sh` 会探测 Ollama 是否安装、服务是否可达、模型是否存在；若缺失会提示 `ollama pull`
- `install_linux.sh` 会尝试把仓库同步到部署目录、安装依赖、构建前端、下发 `systemd/nginx` 配置并做一次健康检查

典型用法：

```bash
bash deploy/generated/init_postgres.sh
bash deploy/generated/check_ollama_model.sh
bash deploy/generated/install_linux.sh
```

执行前请确认服务器已安装：

- `python3`
- `python3-venv`
- `node` / `npm`
- `nginx`
- `rsync`
- 按你的配置准备好的 `PostgreSQL`
- 按你的配置准备好的 `Ollama` 或 OpenAI 兼容模型服务

如果你暂时不想使用配置向导，也可以手工复制模板：

```bash
cp .env.example .env
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

首次使用建议至少检查以下配置：

```env
DATABASE_URL=sqlite:///data/app_database.db
OPENAI_API_KEY=ollama
OPENAI_BASE_URL=http://localhost:11434/v1
LLM_MODEL_GEN=deepseek-r1:7b
KG_BACKEND=networkx
```

如果你只是本地快速试用，可以先保持 SQLite。
如果你要部署到服务器，建议改成 PostgreSQL。
如果你用 Docker 部署，建议使用配置向导生成 `.env.docker`，并确认其中的模型地址对容器可达。

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

### 4.1 启动后如何使用项目

推荐按下面顺序体验：

1. 打开 `Web V2` 首页，确认顶部“启动检查”显示后端可用
2. 进入“导入需求”，上传 `docx/xlsx/txt/json/md` 需求文件
3. 等解析完成后，进入“生成用例”选择需求并开始生成
4. 在“生成进度”中查看当前阶段、结果数量和是否生成完毕
5. 进入“评审与导出”查看生成结果，按“最新生成批次”筛选并导出
6. 进入“数据统计”查看质量特性分类结果
7. 如需知识沉淀，再进入“知识图谱”查看模块和规则

如果你只想快速验证链路，建议先导入一份小型需求文档，只生成几条需求对应的用例。

### 5. 启动兼容版本 `Legacy V1`

注意：`Legacy V1` 只是旧版 UI，启动前仍然需要先按上面的步骤启动后端服务。

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

## 使用建议

### 场景 1：本地快速试用

- 使用 `Web V2`
- 数据库保持默认 `SQLite`
- 模型服务可使用本地 `Ollama`
- 适合个人验证功能和调试页面

### 场景 2：团队共享试用

- 使用 `Web V2`
- 数据库改为 `PostgreSQL`
- 后端建议跑在固定机器上
- 前端建议构建后交给 `Nginx`

### 场景 3：历史兼容

- 使用 `Legacy V1`
- 适合继续沿用旧的 Streamlit 操作方式
- 不建议作为后续长期主线

## Docker 部署

项目提供容器化部署入口，适合 Linux 服务器快速落地：

```bash
cp .env.docker.example .env.docker
docker compose --env-file .env.docker up --build
```

Windows PowerShell:

```powershell
Copy-Item .env.docker.example .env.docker
docker compose --env-file .env.docker up --build
```

默认服务：

- `frontend`：Nginx 托管的 Vue 前端
- `backend`：FastAPI API
- `postgres`：PostgreSQL

Linux 服务器使用 Docker 时，请额外注意模型服务地址：

- `OPENAI_BASE_URL` 不要直接照搬 `http://host.docker.internal:11434/v1`
- 这个地址在 Windows / macOS Docker Desktop 下常见可用，但在 Linux 上通常不可直接使用
- Linux 上请改成容器可访问的真实地址，例如宿主机内网地址、同一 Compose 网络中的模型容器地址，或外部模型服务地址
- 如果不修改这个值，容器中的后端即使启动成功，也可能无法连接模型服务

如果你在 Windows 上看到下面这类报错：

```text
open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified
```

说明不是项目配置错误，而是 `Docker Desktop` 的 Linux 引擎还没有启动。
先打开 `Docker Desktop` 并确认 `Engine running`，再重新执行 `docker compose`。

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

## 常见问题

### 1. 启动后页面一直提示后端不可用

- 确认后端进程已经启动
- 确认前端实际连接的后端地址正确
- 先访问 `http://127.0.0.1:8002/health` 看是否返回 `healthy`

### 2. 模型连接异常

- 确认 `OPENAI_BASE_URL` 对应的模型服务已启动
- 如果你使用 Ollama，确认目标模型已经可用
- 机器资源不足时，本地模型也可能加载失败

### 3. Docker 启动失败

- 先执行 `docker info`
- 如果报 `dockerDesktopLinuxEngine` 错误，先启动 Docker Desktop
- 再执行 `docker compose --env-file .env.docker up --build`

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
