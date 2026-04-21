# PostgreSQL 初始化与部署说明

本文档面向当前项目的服务器部署场景，目标是把默认 `SQLite` 切换为 `PostgreSQL`，并保留现有的 `FastAPI + 前端 + 生成任务后台队列` 运行方式。

## 1. 前置说明

- 当前项目已支持通过环境变量 `DATABASE_URL` 切换数据库
- 项目启动时会自动执行 `SQLModel.metadata.create_all(...)`
- 这意味着：
  - 你只需要先创建数据库和账号
  - 应用首次启动时会自动建表
- 生成任务状态现在也会落库到 `GenerationJob` 表，适合服务器部署

## 2. 安装 PostgreSQL

以 Ubuntu 为例：

```bash
sudo apt update
sudo apt install -y postgresql postgresql-contrib
sudo systemctl enable postgresql
sudo systemctl start postgresql
```

确认服务状态：

```bash
sudo systemctl status postgresql
```

## 3. 初始化数据库

项目已提供初始化脚本：

- [init_postgres.sql](file:///E:/internship/fang/ai_testcase_gen/scripts/init_postgres.sql)

执行方式：

```bash
psql -U postgres -d postgres \
  -v app_db=ai_testcase_gen \
  -v app_user=ai_testcase_user \
  -v app_password='change_me_strong_password' \
  -f scripts/init_postgres.sql
```

该脚本会完成：

- 创建业务账号
- 创建业务数据库
- 设置数据库 owner
- 授予 `public schema` 权限
- 配置默认表/序列权限

## 4. 配置环境变量

参考 `.env.example`，至少配置以下内容：

```env
DATABASE_URL=postgresql+psycopg://ai_testcase_user:change_me_strong_password@127.0.0.1:5432/ai_testcase_gen
GENERATION_QUEUE_WORKERS=2
OPENAI_API_KEY=ollama
OPENAI_BASE_URL=http://127.0.0.1:11434/v1
LLM_MODEL_GEN=deepseek-r1:7b
KG_BACKEND=auto
```

说明：

- `DATABASE_URL`
  - 正式部署时建议显式配置，不再使用默认 SQLite
- `GENERATION_QUEUE_WORKERS`
  - 表示后台生成任务 worker 数量
  - 建议从 `1` 或 `2` 开始，根据服务器 CPU / 内存 / 模型能力逐步调优

## 5. 安装 Python 依赖

项目已补充 PostgreSQL 驱动依赖：

- `psycopg[binary]`

安装方式：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

## 6. 启动后端

开发验证：

```bash
python -m uvicorn src.api.main:app --host 0.0.0.0 --port 8002
```

首次启动时应用会自动：

- 连接 PostgreSQL
- 初始化表结构
- 启动生成任务后台队列 worker
- 扫描未完成任务并重新入队

健康检查：

```bash
curl http://127.0.0.1:8002/health
```

## 7. 启动前端

开发模式：

```bash
cd frontend
npm install
npm run dev -- --port 5173
```

生产模式：

```bash
cd frontend
npm install
npm run build
```

然后把 `dist/` 交给 Nginx 或其他静态服务器。

## 8. 推荐生产部署方式

建议拆成两部分：

- 后端 API
  - `FastAPI + Uvicorn`
- 前端静态资源
  - `Nginx` 托管 `frontend/dist`

推荐结构：

- `Nginx`
  - 前端静态资源
  - `/api` 反向代理到 `127.0.0.1:8002`
- `systemd`
  - 守护后端进程

## 9. systemd 示例

后端服务示例：

```ini
[Unit]
Description=AI Test Case Generator Backend
After=network.target postgresql.service

[Service]
Type=simple
WorkingDirectory=/opt/ai_testcase_gen
EnvironmentFile=/opt/ai_testcase_gen/.env
ExecStart=/opt/ai_testcase_gen/.venv/bin/python -m uvicorn src.api.main:app --host 0.0.0.0 --port 8002
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

启用：

```bash
sudo systemctl daemon-reload
sudo systemctl enable ai-testcase-gen
sudo systemctl start ai-testcase-gen
```

## 10. 上线后重点检查

- 数据库是否连通
- `/health` 是否返回 `200`
- 首次启动是否自动建表成功
- 生成功能是否能正常创建 `GenerationJob`
- 切页后生成任务是否继续执行
- “评审与导出”是否能按最新生成批次过滤

## 11. 当前架构边界

现在这版已经是：

- PostgreSQL 可切换
- 生成任务状态持久化
- 后台队列 worker 消费

但它仍然是“应用内后台队列”，不是独立消息中间件。

如果后面要继续扩到：

- 多实例部署
- 更强重试能力
- 更稳定的分布式任务消费

建议下一步升级到：

- Redis + Celery
- 或 Redis + Dramatiq / RQ
