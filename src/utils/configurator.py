from __future__ import annotations

import argparse
import os
import stat
import shutil
import socket
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse


PROFILE_LABELS = {
    "local": "本地试用 / Web V2",
    "linux": "Linux 服务器部署",
    "docker": "Docker 部署",
    "legacy": "Legacy V1",
}


@dataclass
class WizardAnswers:
    profile: str
    database_mode: str
    database_url: str
    backend_host: str
    backend_port: int
    frontend_port: int
    streamlit_port: int
    frontend_backend_url: str
    generation_queue_workers: int
    generation_resume_on_startup: bool
    openai_base_url: str
    openai_api_key: str
    llm_model_gen: str
    kg_backend: str
    use_semantic_matcher: bool
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str
    install_dir: str
    frontend_dist_dir: str
    server_name: str
    service_user: str
    service_group: str
    postgres_port: int
    postgres_admin_user: str
    docker_frontend_port: int


def detect_os_name() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def prompt_text(label: str, default: str = "", allow_empty: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{label}{suffix}: ").strip()
        if value:
            return value
        if default:
            return default
        if allow_empty:
            return ""
        print("请输入内容。")


def prompt_int(label: str, default: int) -> int:
    while True:
        raw = input(f"{label} [{default}]: ").strip()
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            print("请输入整数。")


def prompt_bool(label: str, default: bool = False) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        raw = input(f"{label} [{hint}]: ").strip().lower()
        if not raw:
            return default
        if raw in {"y", "yes", "1", "true"}:
            return True
        if raw in {"n", "no", "0", "false"}:
            return False
        print("请输入 y 或 n。")


def prompt_choice(label: str, options: List[Tuple[str, str]], default: str) -> str:
    print(label)
    for idx, (value, text) in enumerate(options, start=1):
        marker = " (默认)" if value == default else ""
        print(f"  {idx}. {text}{marker}")
    allowed = {str(i): value for i, (value, _) in enumerate(options, start=1)}
    value_set = {value for value, _ in options}
    while True:
        raw = input("请选择编号或直接输入值: ").strip()
        if not raw:
            return default
        if raw in allowed:
            return allowed[raw]
        if raw in value_set:
            return raw
        print("无效选择，请重新输入。")


def choose_value(
    current: Optional[str],
    interactive: bool,
    *,
    label: str,
    default: str,
    allow_empty: bool = False,
) -> str:
    if current is not None:
        return current
    if interactive:
        return prompt_text(label, default, allow_empty=allow_empty)
    if default or allow_empty:
        return default
    raise ValueError(f"{label} 未提供。")


def choose_int(current: Optional[int], interactive: bool, *, label: str, default: int) -> int:
    if current is not None:
        return current
    if interactive:
        return prompt_int(label, default)
    return default


def choose_bool(current: Optional[bool], interactive: bool, *, label: str, default: bool) -> bool:
    if current is not None:
        return current
    if interactive:
        return prompt_bool(label, default)
    return default


def choose_choice(
    current: Optional[str],
    interactive: bool,
    *,
    label: str,
    options: List[Tuple[str, str]],
    default: str,
) -> str:
    if current is not None:
        return current
    if interactive:
        return prompt_choice(label, options, default)
    return default


def build_postgres_url(host: str, port: int, user: str, password: str, database: str) -> str:
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{database}"


def choose_model_base_url(profile: str, os_name: str) -> str:
    if profile == "docker":
        if os_name in {"windows", "macos"}:
            return "http://host.docker.internal:11434/v1"
        return "http://172.17.0.1:11434/v1"
    return "http://127.0.0.1:11434/v1"


def collect_answers(args: argparse.Namespace) -> WizardAnswers:
    os_name = detect_os_name()
    interactive = not args.non_interactive
    profile = choose_choice(
        args.profile,
        interactive,
        label="请选择配置模式",
        options=[(key, text) for key, text in PROFILE_LABELS.items()],
        default="local",
    )

    print(f"\n当前模式: {PROFILE_LABELS[profile]}")

    default_db_mode = "postgres" if profile in {"linux", "docker"} else "sqlite"
    database_mode = choose_choice(
        args.database_mode,
        interactive,
        label="请选择数据库",
        options=[("sqlite", "SQLite"), ("postgres", "PostgreSQL")],
        default=default_db_mode,
    )

    postgres_port = choose_int(args.db_port, interactive, label="PostgreSQL 端口", default=5432)
    if args.database_url:
        database_url = args.database_url
    elif database_mode == "sqlite":
        database_url = "sqlite:///data/app_database.db"
    else:
        db_host_default = "127.0.0.1" if profile != "docker" else "postgres"
        db_host = choose_value(args.db_host, interactive, label="PostgreSQL 主机", default=db_host_default)
        db_user = choose_value(args.db_user, interactive, label="PostgreSQL 用户", default="ai_testcase_user")
        db_password = choose_value(
            args.db_password,
            interactive,
            label="PostgreSQL 密码",
            default="change_me",
        )
        db_name = choose_value(args.db_name, interactive, label="PostgreSQL 数据库名", default="ai_testcase_gen")
        database_url = build_postgres_url(db_host, postgres_port, db_user, db_password, db_name)

    backend_host_default = "127.0.0.1" if profile != "docker" else "0.0.0.0"
    backend_host = choose_value(args.backend_host, interactive, label="后端监听地址", default=backend_host_default)
    backend_port = choose_int(args.backend_port, interactive, label="后端端口", default=8002)
    frontend_port = choose_int(
        args.frontend_port,
        interactive,
        label="Web V2 前端端口",
        default=5173 if profile != "docker" else 8080,
    )
    streamlit_port = choose_int(args.streamlit_port, interactive, label="Legacy UI 端口", default=8504)
    frontend_backend_url = choose_value(
        args.frontend_backend_url,
        interactive,
        label="前端开发代理后端地址",
        default=f"http://127.0.0.1:{backend_port}",
    )

    generation_queue_workers = choose_int(
        args.generation_queue_workers,
        interactive,
        label="生成队列 worker 数量",
        default=2 if profile == "linux" else 1,
    )
    generation_resume_on_startup = choose_bool(
        args.generation_resume_on_startup,
        interactive,
        label="启动时自动恢复未完成生成任务",
        default=False,
    )

    model_mode = choose_choice(
        args.model_mode,
        interactive,
        label="请选择模型服务类型",
        options=[
            ("ollama", "本地 Ollama / OpenAI 兼容服务"),
            ("openai", "远端 OpenAI 兼容服务"),
            ("later", "稍后自行配置"),
        ],
        default="ollama",
    )
    if model_mode == "openai":
        openai_base_url_default = "https://api.deepseek.com/v1"
        openai_api_key_default = ""
        llm_model_default = "deepseek-chat"
    else:
        openai_base_url_default = choose_model_base_url(profile, os_name)
        openai_api_key_default = "ollama"
        llm_model_default = "deepseek-r1:7b"
    openai_base_url = choose_value(
        args.openai_base_url,
        interactive,
        label="模型 Base URL",
        default=openai_base_url_default,
    )
    openai_api_key = choose_value(
        args.openai_api_key,
        interactive,
        label="模型 API Key",
        default=openai_api_key_default,
        allow_empty=model_mode == "openai",
    )
    llm_model_gen = choose_value(args.llm_model_gen, interactive, label="模型名", default=llm_model_default)

    kg_backend = choose_choice(
        args.kg_backend,
        interactive,
        label="请选择知识图谱后端",
        options=[
            ("networkx", "仅 NetworkX"),
            ("auto", "自动尝试 Neo4j，失败回退"),
            ("neo4j", "仅 Neo4j"),
            ("hybrid", "Neo4j + NetworkX"),
        ],
        default="networkx" if profile in {"linux", "docker"} else "auto",
    )
    use_semantic_matcher = choose_bool(
        args.use_semantic_matcher,
        interactive,
        label="启用语义匹配器",
        default=False,
    )

    neo4j_uri = ""
    neo4j_user = ""
    neo4j_password = ""
    if kg_backend in {"auto", "neo4j", "hybrid"}:
        neo4j_uri = choose_value(args.neo4j_uri, interactive, label="Neo4j URI", default="bolt://127.0.0.1:7687")
        neo4j_user = choose_value(args.neo4j_user, interactive, label="Neo4j 用户", default="neo4j")
        neo4j_password = choose_value(args.neo4j_password, interactive, label="Neo4j 密码", default="change_me")

    install_dir = choose_value(
        args.install_dir,
        interactive,
        label="Linux 部署目录",
        default="/opt/ai_testcase_gen",
    )
    frontend_dist_dir = choose_value(
        args.frontend_dist_dir,
        interactive,
        label="前端 dist 部署目录",
        default="/var/www/ai_testcase_gen/frontend/dist",
    )
    server_name = choose_value(args.server_name, interactive, label="Nginx server_name", default="_")
    service_user = choose_value(args.service_user, interactive, label="systemd User", default="www-data")
    service_group = choose_value(args.service_group, interactive, label="systemd Group", default="www-data")
    postgres_admin_user = choose_value(
        args.postgres_admin_user,
        interactive,
        label="PostgreSQL 管理员用户",
        default="postgres",
    )
    docker_frontend_port = choose_int(
        args.docker_frontend_port,
        interactive,
        label="Docker 前端宿主机端口",
        default=8080,
    )

    return WizardAnswers(
        profile=profile,
        database_mode=database_mode,
        database_url=database_url,
        backend_host=backend_host,
        backend_port=backend_port,
        frontend_port=frontend_port,
        streamlit_port=streamlit_port,
        frontend_backend_url=frontend_backend_url,
        generation_queue_workers=generation_queue_workers,
        generation_resume_on_startup=generation_resume_on_startup,
        openai_base_url=openai_base_url,
        openai_api_key=openai_api_key,
        llm_model_gen=llm_model_gen,
        kg_backend=kg_backend,
        use_semantic_matcher=use_semantic_matcher,
        neo4j_uri=neo4j_uri,
        neo4j_user=neo4j_user,
        neo4j_password=neo4j_password,
        install_dir=install_dir,
        frontend_dist_dir=frontend_dist_dir,
        server_name=server_name,
        service_user=service_user,
        service_group=service_group,
        postgres_port=postgres_port,
        postgres_admin_user=postgres_admin_user,
        docker_frontend_port=docker_frontend_port,
    )


def bool_text(value: bool) -> str:
    return "true" if value else "false"


def render_env(values: Dict[str, str]) -> str:
    lines = [f"{key}={value}" for key, value in values.items()]
    return "\n".join(lines) + "\n"


def common_env_values(answers: WizardAnswers) -> Dict[str, str]:
    return {
        "BACKEND_URL": f"http://127.0.0.1:{answers.backend_port}",
        "DATABASE_URL": answers.database_url,
        "GENERATION_QUEUE_WORKERS": str(answers.generation_queue_workers),
        "GENERATION_RESUME_ON_STARTUP": bool_text(answers.generation_resume_on_startup),
        "OPENAI_API_KEY": answers.openai_api_key,
        "OPENAI_BASE_URL": answers.openai_base_url,
        "LLM_MODEL_GEN": answers.llm_model_gen,
        "KG_BACKEND": answers.kg_backend,
        "USE_SEMANTIC_MATCHER": bool_text(answers.use_semantic_matcher),
        "NEO4J_URI": answers.neo4j_uri,
        "NEO4J_USER": answers.neo4j_user,
        "NEO4J_PASSWORD": answers.neo4j_password,
        "FEISHU_APP_ID": "",
        "FEISHU_APP_SECRET": "",
        "FEISHU_APP_TOKEN": "",
        "FEISHU_TABLE_ID": "",
        "FEISHU_SPREADSHEET_TOKEN": "",
        "FEISHU_SHEET_ID": "",
        "FEISHU_DOCUMENT_ID": "",
        "FEISHU_TENANT_TOKEN": "",
        "FEISHU_OPEN_BASE_URL": "https://open.feishu.cn",
        "FEISHU_REQUIREMENT_LINK_BASE_URL": "",
    }


def render_frontend_env_local(answers: WizardAnswers) -> str:
    return render_env({"VITE_BACKEND_URL": answers.frontend_backend_url})


def render_frontend_env_production() -> str:
    return render_env({"VITE_BACKEND_URL": "/api"})


def render_env_production(answers: WizardAnswers) -> str:
    return render_env(common_env_values(answers))


def parse_database_url(answers: WizardAnswers) -> Dict[str, str]:
    if answers.database_mode != "postgres":
        return {}
    parsed = urlparse(answers.database_url)
    return {
        "host": parsed.hostname or "127.0.0.1",
        "port": str(parsed.port or 5432),
        "user": parsed.username or "ai_testcase_user",
        "password": parsed.password or "change_me",
        "database": parsed.path.lstrip("/") or "ai_testcase_gen",
    }


def render_systemd_backend(answers: WizardAnswers) -> str:
    return (
        "[Unit]\n"
        "Description=AI Test Case Generator Backend\n"
        "After=network.target postgresql.service\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"WorkingDirectory={answers.install_dir}\n"
        f"EnvironmentFile={answers.install_dir}/.env\n"
        f"ExecStart={answers.install_dir}/.venv/bin/python -m uvicorn src.api.main:app --host 0.0.0.0 --port {answers.backend_port}\n"
        "Restart=always\n"
        "RestartSec=5\n"
        f"User={answers.service_user}\n"
        f"Group={answers.service_group}\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def render_systemd_legacy(answers: WizardAnswers) -> str:
    return (
        "[Unit]\n"
        "Description=AI Test Case Generator Legacy UI\n"
        "After=network.target ai-testcase-backend.service\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"WorkingDirectory={answers.install_dir}\n"
        f"EnvironmentFile={answers.install_dir}/.env\n"
        f"ExecStart={answers.install_dir}/.venv/bin/streamlit run ui/main.py --server.port {answers.streamlit_port}\n"
        "Restart=always\n"
        "RestartSec=5\n"
        f"User={answers.service_user}\n"
        f"Group={answers.service_group}\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def render_nginx_config(answers: WizardAnswers) -> str:
    return (
        "server {\n"
        "    listen 80;\n"
        f"    server_name {answers.server_name};\n\n"
        f"    root {answers.frontend_dist_dir};\n"
        "    index index.html;\n\n"
        "    client_max_body_size 50m;\n\n"
        "    location /api/ {\n"
        f"        proxy_pass http://127.0.0.1:{answers.backend_port}/;\n"
        "        proxy_http_version 1.1;\n"
        "        proxy_set_header Host $host;\n"
        "        proxy_set_header X-Real-IP $remote_addr;\n"
        "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
        "        proxy_set_header X-Forwarded-Proto $scheme;\n"
        "        proxy_read_timeout 300s;\n"
        "    }\n\n"
        "    location / {\n"
        "        try_files $uri $uri/ /index.html;\n"
        "    }\n"
        "}\n"
    )


def render_docker_env(answers: WizardAnswers) -> str:
    if answers.database_mode == "postgres":
        parsed = urlparse(answers.database_url)
        postgres_user = parsed.username or "ai_testcase_user"
        postgres_password = parsed.password or "change_me"
        postgres_db = parsed.path.lstrip("/") or "ai_testcase_gen"
    else:
        postgres_user = "ai_testcase_user"
        postgres_password = "change_me"
        postgres_db = "ai_testcase_gen"
    values = {
        "POSTGRES_DB": postgres_db,
        "POSTGRES_USER": postgres_user,
        "POSTGRES_PASSWORD": postgres_password,
        "DATABASE_URL": answers.database_url,
        "GENERATION_QUEUE_WORKERS": str(answers.generation_queue_workers),
        "GENERATION_RESUME_ON_STARTUP": bool_text(answers.generation_resume_on_startup),
        "OPENAI_API_KEY": answers.openai_api_key,
        "OPENAI_BASE_URL": answers.openai_base_url,
        "LLM_MODEL_GEN": answers.llm_model_gen,
        "KG_BACKEND": answers.kg_backend,
        "USE_SEMANTIC_MATCHER": bool_text(answers.use_semantic_matcher),
        "NEO4J_URI": answers.neo4j_uri,
        "NEO4J_USER": answers.neo4j_user,
        "NEO4J_PASSWORD": answers.neo4j_password,
        "POSTGRES_PORT": str(answers.postgres_port),
        "BACKEND_PORT": str(answers.backend_port),
        "FRONTEND_PORT": str(answers.docker_frontend_port),
    }
    return render_env(values)


def render_linux_deploy_notes(answers: WizardAnswers) -> str:
    db = parse_database_url(answers)
    postgres_init_note = ""
    if db:
        postgres_init_note = f"""

## 2.1 初始化 PostgreSQL

```bash
bash deploy/generated/init_postgres.sh
```
"""
    return f"""# Linux 部署执行清单

## 1. 上传配置

- 将项目放到 `{answers.install_dir}`
- 将生成的 `.env` 放到 `{answers.install_dir}/.env`

## 2. 安装依赖

```bash
cd {answers.install_dir}
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
cd frontend
npm install
npm run build
```
{postgres_init_note}

## 3. 部署前端产物

- 将 `frontend/dist` 发布到 `{answers.frontend_dist_dir}`

## 4. 部署 systemd

```bash
sudo cp deploy/generated/ai-testcase-backend.service /etc/systemd/system/ai-testcase-backend.service
sudo systemctl daemon-reload
sudo systemctl enable ai-testcase-backend
sudo systemctl restart ai-testcase-backend
```

Legacy UI 如需启用：

```bash
sudo cp deploy/generated/ai-testcase-legacy-ui.service /etc/systemd/system/ai-testcase-legacy-ui.service
sudo systemctl daemon-reload
sudo systemctl enable ai-testcase-legacy-ui
sudo systemctl restart ai-testcase-legacy-ui
```

## 5. 部署 Nginx

```bash
sudo cp deploy/generated/nginx.production.conf /etc/nginx/conf.d/ai-testcase.conf
sudo nginx -t
sudo systemctl reload nginx
```

## 6. 核对项目

- 后端地址：`http://127.0.0.1:{answers.backend_port}`
- 前端反代：`server_name {answers.server_name}`
- 数据库：`{answers.database_url}`
- 模型服务：`{answers.openai_base_url}`
"""


def render_postgres_init_script(answers: WizardAnswers) -> str:
    db = parse_database_url(answers)
    if not db:
        return "#!/usr/bin/env bash\nset -euo pipefail\necho 'Current profile does not use PostgreSQL.'\n"
    host = db["host"]
    port = db["port"]
    app_user = db["user"]
    app_password = db["password"]
    app_db = db["database"]
    is_local = host in {"127.0.0.1", "localhost"} or host.startswith("/var/run/postgresql")
    if is_local:
        init_block = f"""sudo apt-get update
sudo apt-get install -y postgresql postgresql-contrib
sudo systemctl enable postgresql
sudo systemctl start postgresql
sudo -u {answers.postgres_admin_user} psql -d postgres \\
  -v app_db='{app_db}' \\
  -v app_user='{app_user}' \\
  -v app_password='{app_password}' \\
  -f "$INSTALL_DIR/scripts/init_postgres.sql"
"""
    else:
        init_block = f"""echo "Remote PostgreSQL detected: {host}:{port}"
echo "Run the following command with a superuser that can create roles/databases:"
echo "psql -h {host} -p {port} -U {answers.postgres_admin_user} -d postgres -v app_db='{app_db}' -v app_user='{app_user}' -v app_password='***' -f $INSTALL_DIR/scripts/init_postgres.sql"
"""
    return f"""#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="{answers.install_dir}"

echo "Initializing PostgreSQL for AI Test Case Generator"
{init_block}
echo "PostgreSQL initialization step finished."
"""


def render_ollama_probe_script(answers: WizardAnswers) -> str:
    parsed_model = urlparse(answers.openai_base_url)
    host = parsed_model.hostname or ""
    port = parsed_model.port or (443 if parsed_model.scheme == "https" else 80)
    is_local_ollama = host in {"127.0.0.1", "localhost"} and port == 11434
    if not is_local_ollama:
        return f"""#!/usr/bin/env bash
set -euo pipefail

echo "当前模型服务地址为 {answers.openai_base_url}"
echo "不是本机默认 Ollama 地址，无法自动探测本地模型。"
echo "请确认远端服务可访问，且已提供模型 {answers.llm_model_gen}。"
"""
    return f"""#!/usr/bin/env bash
set -euo pipefail

MODEL="{answers.llm_model_gen}"
BASE_URL="{answers.openai_base_url}"

if ! command -v ollama >/dev/null 2>&1; then
  echo "Ollama 未安装。"
  echo "请先安装 Ollama，然后执行："
  echo "  ollama pull $MODEL"
  exit 0
fi

if ! curl -fsS "{answers.openai_base_url.replace('/v1', '').rstrip('/')}/api/tags" >/dev/null 2>&1; then
  echo "Ollama 服务未运行或不可访问: $BASE_URL"
  echo "启动 Ollama 后，可执行："
  echo "  ollama pull $MODEL"
  exit 0
fi

if ollama list | awk 'NR > 1 {{print $1}}' | grep -Fxq "$MODEL"; then
  echo "Ollama 模型已存在: $MODEL"
else
  echo "未发现模型: $MODEL"
  echo "请执行："
  echo "  ollama pull $MODEL"
fi
"""


def render_linux_install_script(answers: WizardAnswers) -> str:
    db = parse_database_url(answers)
    init_postgres_step = ""
    if db:
        init_postgres_step = f"""
echo "[4/10] Initializing PostgreSQL"
bash "$ROOT_DIR/deploy/generated/init_postgres.sh"
"""
    ollama_step = f"""
echo "[8/10] Probing Ollama and model"
bash "$ROOT_DIR/deploy/generated/check_ollama_model.sh"
"""
    return f"""#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")/../.." && pwd)"
INSTALL_DIR="{answers.install_dir}"
FRONTEND_DIST_DIR="{answers.frontend_dist_dir}"

echo "[1/10] Installing system dependencies"
sudo apt-get update
sudo apt-get install -y python3-venv nginx nodejs npm rsync curl

echo "[2/10] Ensuring install directories"
sudo mkdir -p "$INSTALL_DIR"
sudo mkdir -p "$FRONTEND_DIST_DIR"

echo "[3/10] Syncing repository to install directory"
if command -v rsync >/dev/null 2>&1; then
  sudo rsync -a --delete \\
    --exclude '.git' \\
    --exclude '.venv' \\
    --exclude 'frontend/node_modules' \\
    --exclude 'frontend/dist' \\
    "$ROOT_DIR"/ "$INSTALL_DIR"/
else
  echo "rsync not found, falling back to cp -a"
  sudo cp -a "$ROOT_DIR"/. "$INSTALL_DIR"/
fi

{init_postgres_step}

echo "[5/10] Installing Python dependencies"
cd "$INSTALL_DIR"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -r requirements.txt

echo "[6/10] Building frontend"
cd "$INSTALL_DIR/frontend"
npm install
cat > .env.production <<'EOF'
{render_frontend_env_production().rstrip()}
EOF
npm run build

echo "[7/10] Publishing frontend dist"
sudo rm -rf "$FRONTEND_DIST_DIR"
sudo mkdir -p "$FRONTEND_DIST_DIR"
sudo cp -a "$INSTALL_DIR/frontend/dist"/. "$FRONTEND_DIST_DIR"/

{ollama_step}

echo "[9/10] Installing environment and generated configs"
sudo cp "$ROOT_DIR/.env.production" "$INSTALL_DIR/.env"
sudo cp "$ROOT_DIR/deploy/generated/ai-testcase-backend.service" /etc/systemd/system/ai-testcase-backend.service
sudo cp "$ROOT_DIR/deploy/generated/nginx.production.conf" /etc/nginx/conf.d/ai-testcase.conf

echo "[10/10] Reloading services"
sudo systemctl daemon-reload
sudo systemctl enable ai-testcase-backend
sudo systemctl restart ai-testcase-backend
sudo nginx -t
sudo systemctl reload nginx

echo "Running health check"
curl --fail "http://127.0.0.1:{answers.backend_port}/health" || {{
  echo "Health check failed. Please inspect 'systemctl status ai-testcase-backend'."
  exit 1
}}

echo "Deployment complete."
"""


def generate_bundle(project_root: Path, answers: WizardAnswers) -> Dict[Path, str]:
    bundle: Dict[Path, str] = {}
    env_content = render_env(common_env_values(answers))

    if answers.profile in {"local", "linux", "legacy"}:
        bundle[project_root / ".env"] = env_content
    if answers.profile == "local":
        bundle[project_root / "frontend" / ".env.local"] = render_frontend_env_local(answers)
    if answers.profile == "docker":
        bundle[project_root / ".env.docker"] = render_docker_env(answers)
    if answers.profile == "linux":
        bundle[project_root / ".env.production"] = render_env_production(answers)
        bundle[project_root / "frontend" / ".env.production"] = render_frontend_env_production()
        generated_dir = project_root / "deploy" / "generated"
        bundle[generated_dir / "ai-testcase-backend.service"] = render_systemd_backend(answers)
        bundle[generated_dir / "ai-testcase-legacy-ui.service"] = render_systemd_legacy(answers)
        bundle[generated_dir / "nginx.production.conf"] = render_nginx_config(answers)
        bundle[generated_dir / "LINUX_DEPLOY.md"] = render_linux_deploy_notes(answers)
        if answers.database_mode == "postgres":
            bundle[generated_dir / "init_postgres.sh"] = render_postgres_init_script(answers)
        bundle[generated_dir / "check_ollama_model.sh"] = render_ollama_probe_script(answers)
        bundle[generated_dir / "install_linux.sh"] = render_linux_install_script(answers)
    if answers.profile == "legacy":
        generated_dir = project_root / "deploy" / "generated"
        bundle[generated_dir / "ai-testcase-legacy-ui.service"] = render_systemd_legacy(answers)

    return bundle


def backup_if_exists(path: Path) -> None:
    if not path.exists():
        return
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_name(f"{path.name}.{timestamp}.bak")
    shutil.copy2(path, backup)


def write_bundle(bundle: Dict[Path, str]) -> None:
    for path, content in bundle.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        backup_if_exists(path)
        path.write_text(content, encoding="utf-8", newline="\n")
        if path.suffix == ".sh":
            current_mode = path.stat().st_mode
            path.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def check_tcp_endpoint(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def run_checks(answers: WizardAnswers) -> List[str]:
    reports: List[str] = []

    parsed_model = urlparse(answers.openai_base_url)
    model_host = parsed_model.hostname
    model_port = parsed_model.port or (443 if parsed_model.scheme == "https" else 80)
    if model_host:
        ok = check_tcp_endpoint(model_host, model_port)
        status = "OK" if ok else "FAIL"
        reports.append(f"[{status}] 模型服务 {model_host}:{model_port}")

    if answers.database_mode == "postgres":
        parsed_db = urlparse(answers.database_url)
        db_host = parsed_db.hostname
        db_port = parsed_db.port or 5432
        if db_host:
            ok = check_tcp_endpoint(db_host, db_port)
            status = "OK" if ok else "FAIL"
            reports.append(f"[{status}] PostgreSQL {db_host}:{db_port}")

    if answers.neo4j_uri:
        parsed_neo4j = urlparse(answers.neo4j_uri.replace("bolt://", "tcp://", 1))
        neo4j_host = parsed_neo4j.hostname
        neo4j_port = parsed_neo4j.port or 7687
        if neo4j_host:
            ok = check_tcp_endpoint(neo4j_host, neo4j_port)
            status = "OK" if ok else "FAIL"
            reports.append(f"[{status}] Neo4j {neo4j_host}:{neo4j_port}")

    return reports


def print_summary(answers: WizardAnswers, bundle: Dict[Path, str], project_root: Path) -> None:
    print("\n=== 配置摘要 ===")
    print(f"模式: {PROFILE_LABELS[answers.profile]}")
    print(f"数据库: {answers.database_url}")
    print(f"模型服务: {answers.openai_base_url}")
    print(f"模型名: {answers.llm_model_gen}")
    print(f"KG 后端: {answers.kg_backend}")
    print("\n将写入以下文件:")
    for path in bundle:
        print(f"- {path.relative_to(project_root)}")

    print("\n建议下一步:")
    if answers.profile == "local":
        print("- bash scripts/start-backend.sh 或 PowerShell 对应脚本")
        print("- bash scripts/start-frontend.sh 或 PowerShell 对应脚本")
    elif answers.profile == "linux":
        print("- 打开 deploy/generated/LINUX_DEPLOY.md 按顺序执行")
        print("- 检查 deploy/generated 下的 systemd 与 nginx 配置")
        print("- 执行前端构建并部署 dist")
    elif answers.profile == "docker":
        print("- 检查 .env.docker 中 OPENAI_BASE_URL 是否为容器可访问地址")
        print("- docker compose --env-file .env.docker up --build -d")
    else:
        print("- 先启动后端")
        print("- 再运行 Legacy UI 脚本")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI Test Case Generator 配置向导")
    parser.add_argument("--profile", choices=sorted(PROFILE_LABELS), help="配置模式")
    parser.add_argument("--non-interactive", action="store_true", help="无交互模式，使用参数与默认值生成配置")
    parser.add_argument("--yes", action="store_true", help="跳过写入确认")
    parser.add_argument("--check", action="store_true", help="生成后做基础连通性检查")

    parser.add_argument("--database-mode", choices=["sqlite", "postgres"])
    parser.add_argument("--database-url")
    parser.add_argument("--db-host")
    parser.add_argument("--db-port", type=int)
    parser.add_argument("--db-user")
    parser.add_argument("--db-password")
    parser.add_argument("--db-name")
    parser.add_argument("--postgres-admin-user")

    parser.add_argument("--backend-host")
    parser.add_argument("--backend-port", type=int)
    parser.add_argument("--frontend-port", type=int)
    parser.add_argument("--streamlit-port", type=int)
    parser.add_argument("--frontend-backend-url")
    parser.add_argument("--docker-frontend-port", type=int)

    parser.add_argument("--generation-queue-workers", type=int)
    parser.add_argument("--generation-resume-on-startup", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--model-mode", choices=["ollama", "openai", "later"])
    parser.add_argument("--openai-base-url")
    parser.add_argument("--openai-api-key")
    parser.add_argument("--llm-model-gen")
    parser.add_argument("--kg-backend", choices=["networkx", "auto", "neo4j", "hybrid"])
    parser.add_argument("--use-semantic-matcher", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--neo4j-uri")
    parser.add_argument("--neo4j-user")
    parser.add_argument("--neo4j-password")

    parser.add_argument("--install-dir")
    parser.add_argument("--frontend-dist-dir")
    parser.add_argument("--server-name")
    parser.add_argument("--service-user")
    parser.add_argument("--service-group")
    return parser


def main(argv: List[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    project_root = Path(__file__).resolve().parents[2]

    print("AI Test Case Generator 配置向导")
    answers = collect_answers(args)
    bundle = generate_bundle(project_root, answers)
    print_summary(answers, bundle, project_root)

    if not args.yes and not prompt_bool("\n确认写入这些文件", True):
        print("已取消写入。")
        return 0

    write_bundle(bundle)
    print("\n配置文件已生成。")

    if args.check or (not args.non_interactive and prompt_bool("是否执行基础连通性检查", False)):
        print("\n=== 连通性检查 ===")
        for line in run_checks(answers):
            print(line)

    return 0
