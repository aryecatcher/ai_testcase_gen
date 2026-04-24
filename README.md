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
- 统一使用 PostgreSQL 持久化需求、用例与生成任务

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
  - 推荐：`2.30+`
- `Python 3.11+`
  - 推荐：`3.11.x`
- `Node.js 20+`
  - 推荐：`20 LTS`
- 建议确认 `python`、`pip`、`npm` 命令可直接使用

本地快速试用：

- 必需：`Python 3.11+`
- 必需：`Node.js 20+`
- 可选：`Ollama` 或其他 OpenAI 兼容模型服务
- 必需：`PostgreSQL 15+`

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

推荐版本汇总：

- `Git`
  - 推荐：`2.30+`
- `Python`
  - 最低：`3.11`
  - 推荐：`3.11.x`
- `Node.js`
  - 最低：`20`
  - 推荐：`20 LTS`
- `npm`
  - 推荐：`10+`
- `PostgreSQL`
  - 最低：`15`
  - 推荐：`15` 或 `16`
- `Nginx`
  - 推荐：`1.18+`
- `Docker`
  - 推荐：`24+`
- `Docker Compose`
  - 推荐：`v2.20+`
- `Ollama`
  - 推荐：使用当前稳定版，建议 `0.3+`

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

### 1.1 Ubuntu 安装方式参考

以下命令适合 Ubuntu 22.04/24.04 一类环境，主要用于安装本项目常见依赖。

安装基础工具：

```bash
sudo apt update
sudo apt install -y git curl rsync
```

建议目标版本：

- `git 2.30+`
- `curl 7.80+`
- `rsync 3.2+`

安装 Python 与虚拟环境：

```bash
sudo apt install -y python3 python3-pip python3-venv
python3 --version
```

推荐版本：

- `Python 3.11.x`

安装 Nginx：

```bash
sudo apt install -y nginx
sudo systemctl enable nginx
sudo systemctl start nginx
nginx -v
```

推荐版本：

- `Nginx 1.18+`

安装 Node.js 与 npm：

```bash
sudo apt install -y nodejs npm
node --version
npm --version
```

如果系统仓库里的 Node.js 版本偏低，建议改用 NodeSource 或 `nvm` 安装 `Node.js 20+`。

推荐版本：

- `Node.js 20 LTS`
- `npm 10+`

安装 PostgreSQL：

```bash
sudo apt install -y postgresql postgresql-contrib
sudo systemctl enable postgresql
sudo systemctl start postgresql
psql --version
```

推荐版本：

- `PostgreSQL 15` 或 `16`

安装 Ollama：

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama --version
```

推荐版本：

- `Ollama 0.3+` 或当前稳定版

拉取本项目默认模型示例：

```bash
ollama pull deepseek-r1:7b
```

如果你不打算在服务器本机运行 Ollama，也可以跳过这一步，改用可访问的 OpenAI 兼容模型服务。

### 1.2 CentOS / Rocky / AlmaLinux 最短部署流程

如果你最终部署在 CentOS 系列服务器，**先看这一节就够了**。其他本地开发、Docker、Windows 章节可以先跳过。

#### 第 0 步：准备系统依赖

```bash
sudo dnf install -y git curl rsync python3 python3-pip nginx nodejs npm postgresql-server postgresql
sudo postgresql-setup --initdb || true
sudo systemctl enable postgresql
sudo systemctl start postgresql
sudo systemctl enable nginx
sudo systemctl start nginx
curl -fsSL https://ollama.com/install.sh | sh
ollama pull deepseek-r1:7b
```

如果系统使用 `yum`，把上面的 `dnf` 换成 `yum` 即可。

#### 第 1 步：克隆项目

```bash
git clone https://github.com/aryecatcher/ai_testcase_gen.git
cd ai_testcase_gen
```

#### 第 2 步：首次配置并安装

推荐直接执行这一条命令：

```bash
bash scripts/install-first-time-centos.sh
```

这一步会依次完成：

- 配置 PostgreSQL 连接信息
- 配置模型服务地址和模型名
- 配置安装目录
- 配置前端部署目录
- 配置域名 `server_name`
- 生成 `.env.production`、systemd、nginx 配置
- 自动执行 CentOS 安装脚本

如果你已经确定全部参数，也可以用无交互方式：

```bash
python3 scripts/configure.py \
  --profile linux \
  --non-interactive \
  --yes \
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

bash deploy/generated/install_centos.sh
```

#### 第 3 步：以后只做启停

首次安装完成后，后续**不需要重新配置环境**，直接使用下面 3 个脚本：

启动：

```bash
bash scripts/start-centos-services.sh
```

查看状态：

```bash
bash scripts/status-centos-services.sh
```

停止：

```bash
bash scripts/stop-centos-services.sh
```

如果你还需要连同 Legacy UI 一起启停，可追加 `--with-legacy`：

```bash
bash scripts/start-centos-services.sh --with-legacy
bash scripts/status-centos-services.sh --with-legacy
bash scripts/stop-centos-services.sh --with-legacy
```

#### 第 4 步：确认访问地址

- 前端：`http://你的域名/`
- 后端健康检查：`http://127.0.0.1:8002/health`
- 如果没配域名，也可以先访问服务器 IP

#### 会生成哪些关键文件

- `.env.production`
- `frontend/.env.production`
- `deploy/generated/ai-testcase-backend.service`
- `deploy/generated/nginx.production.conf`
- `deploy/generated/install_centos.sh`

#### 最常用检查命令

```bash
python3 --version
node --version
npm --version
psql --version
nginx -v
ollama --version
ollama list
ss -lntp | grep 5432 || true
ss -lntp | grep 8002 || true
ss -lntp | grep 11434 || true
sudo systemctl status postgresql --no-pager
sudo systemctl status nginx --no-pager
sudo systemctl status ai-testcase-backend --no-pager
```

#### 常见报错与解决方法

**1. `python3: command not found`**

- 原因：Python 没安装
- 处理：

```bash
sudo dnf install -y python3 python3-pip
```

**2. `psql: command not found`**

- 原因：PostgreSQL 客户端没安装
- 处理：

```bash
sudo dnf install -y postgresql
```

**3. `Failed to enable unit: Unit file postgresql.service does not exist`**

- 原因：PostgreSQL 服务名可能是版本化的，如 `postgresql-15`
- 处理：

```bash
systemctl list-unit-files | grep postgres
sudo systemctl enable postgresql-15
sudo systemctl start postgresql-15
```

**4. `Ollama 未安装` 或 `ollama: command not found`**

- 原因：模型服务未安装
- 处理：

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull deepseek-r1:7b
```

**5. `Ollama 服务未运行或不可访问`**

- 原因：Ollama 没启动，或 `OPENAI_BASE_URL` 填错
- 处理：

```bash
systemctl status ollama --no-pager
curl http://127.0.0.1:11434/api/tags
```

- 如果不是本机 Ollama，请把配置里的 `OPENAI_BASE_URL` 改成真实可访问地址

**6. `未发现模型: deepseek-r1:7b`**

- 原因：模型还没拉取
- 处理：

```bash
ollama pull deepseek-r1:7b
```

**7. `Permission denied`**

- 原因：脚本没有执行权限，或目录权限不足
- 处理：

```bash
chmod +x scripts/*.sh
chmod +x deploy/generated/*.sh
```

- 如果是安装目录或前端目录权限问题，检查：

```bash
ls -ld /opt/ai_testcase_gen
ls -ld /var/www/ai_testcase_gen
```

**8. `nginx: [emerg]` 或 `nginx -t` 失败**

- 原因：nginx 配置错误，常见是 `server_name`、目录路径、端口冲突
- 处理：

```bash
sudo nginx -t
sudo cat /etc/nginx/conf.d/ai-testcase.conf
```

- 然后确认：
  - `root` 目录存在
  - `proxy_pass` 指向 `127.0.0.1:8002`
  - `server_name` 填写正确

**9. `curl http://127.0.0.1:8002/health` 失败**

- 原因：后端服务没起来，或端口没监听
- 处理：

```bash
sudo systemctl status ai-testcase-backend --no-pager
journalctl -u ai-testcase-backend -n 100 --no-pager
ss -lntp | grep 8002 || true
```

**10. `DATABASE_URL` 连接失败 / PostgreSQL 登录失败**

- 原因：数据库账号、密码、库名、地址不对
- 处理：

```bash
sudo -u postgres psql -c "\du"
sudo -u postgres psql -c "\l"
psql "postgresql://ai_testcase_user:change_me@127.0.0.1:5432/ai_testcase_gen" -c "select 1;"
```

**11. 页面打不开，但服务都显示启动**

- 原因：防火墙、SELinux、nginx 目录权限、域名解析有问题
- 处理：

```bash
systemctl status firewalld --no-pager
firewall-cmd --list-all
getenforce
```

- 如需放行 HTTP / HTTPS：

```bash
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --permanent --add-service=https
sudo firewall-cmd --reload
```

#### 一句话记忆

- 第一次：`bash scripts/install-first-time-centos.sh`
- 以后启动：`bash scripts/start-centos-services.sh`
- 看状态：`bash scripts/status-centos-services.sh`
- 停止：`bash scripts/stop-centos-services.sh`

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

### 4.1 CentOS 推荐使用方式

如果你最终部署在 CentOS / Rocky / AlmaLinux，直接按前面的 [CentOS / Rocky / AlmaLinux 最短部署流程](file:///e:/internship/fang/ai_testcase_gen/README.md#L208-L478) 执行即可。

这里只记最短 4 条命令：

```bash
bash scripts/install-first-time-centos.sh
bash scripts/start-centos-services.sh
bash scripts/status-centos-services.sh
bash scripts/stop-centos-services.sh
```

### 4.2 启动后如何使用项目

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
- 数据库需配置为 `PostgreSQL`
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

- 本地试用：`PostgreSQL + Web V2`
- 团队试用：`PostgreSQL + Web V2`
- Linux 服务器：`Nginx + FastAPI + systemd + PostgreSQL`
- CentOS 服务器：`Nginx + FastAPI + systemd + PostgreSQL + install_centos.sh`
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
