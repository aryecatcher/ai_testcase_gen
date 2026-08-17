from pathlib import Path

from src.utils.configurator import (
    WizardAnswers,
    build_parser,
    build_postgres_url,
    collect_answers,
    generate_bundle,
    render_nginx_config,
    render_systemd_backend,
)


def make_answers(profile: str) -> WizardAnswers:
    return WizardAnswers(
        profile=profile,
        database_mode="postgres",
        database_url="postgresql+psycopg://user:pass@127.0.0.1:5432/appdb",
        backend_host="127.0.0.1",
        backend_port=8002,
        frontend_port=5173,
        streamlit_port=8504,
        frontend_backend_url="http://127.0.0.1:8002",
        generation_queue_workers=2,
        generation_resume_on_startup=False,
        openai_base_url="http://127.0.0.1:11434/v1",
        openai_api_key="ollama",
        llm_model_gen="deepseek-r1:7b",
        kg_backend="networkx",
        use_semantic_matcher=False,
        neo4j_uri="",
        neo4j_user="",
        neo4j_password="",
        install_dir="/srv/ai_testcase_gen",
        frontend_dist_dir="/srv/ai_testcase_gen/frontend/dist",
        server_name="example.com",
        service_user="nginx" if profile == "linux" else "www-data",
        service_group="nginx" if profile == "linux" else "www-data",
        postgres_port=5432,
        postgres_admin_user="postgres",
        docker_frontend_port=8080,
    )


def test_build_postgres_url():
    assert (
        build_postgres_url("db.example", 5432, "alice", "secret", "cases")
        == "postgresql+psycopg://alice:secret@db.example:5432/cases"
    )


def test_generate_bundle_local_contains_env_and_frontend_env():
    bundle = generate_bundle(Path("E:/repo"), make_answers("local"))
    assert Path("E:/repo/.env") in bundle
    assert Path("E:/repo/frontend/.env.local") in bundle
    assert "VITE_BACKEND_URL=http://127.0.0.1:8002" in bundle[Path("E:/repo/frontend/.env.local")]


def test_generate_bundle_linux_contains_generated_service_files():
    bundle = generate_bundle(Path("E:/repo"), make_answers("linux"))
    assert Path("E:/repo/.env.production") in bundle
    assert Path("E:/repo/frontend/.env.production") in bundle
    assert Path("E:/repo/deploy/generated/ai-testcase-backend.service") in bundle
    assert Path("E:/repo/deploy/generated/ai-testcase-legacy-ui.service") in bundle
    assert Path("E:/repo/deploy/generated/nginx.production.conf") in bundle
    assert Path("E:/repo/deploy/generated/LINUX_DEPLOY.md") in bundle
    assert Path("E:/repo/deploy/generated/init_postgres.sh") in bundle
    assert Path("E:/repo/deploy/generated/check_ollama_model.sh") in bundle
    assert Path("E:/repo/deploy/generated/install_linux.sh") in bundle
    assert Path("E:/repo/deploy/generated/install_centos.sh") in bundle
    assert "VITE_BACKEND_URL=/api" in bundle[Path("E:/repo/frontend/.env.production")]


def test_render_systemd_backend_uses_custom_install_dir_and_port():
    content = render_systemd_backend(make_answers("linux"))
    assert "WorkingDirectory=/srv/ai_testcase_gen" in content
    assert "--port 8002" in content
    assert "User=nginx" in content


def test_render_nginx_config_uses_server_name_and_backend_port():
    content = render_nginx_config(make_answers("linux"))
    assert "server_name example.com;" in content
    assert "proxy_pass http://127.0.0.1:8002/;" in content


def test_collect_answers_non_interactive_linux():
    parser = build_parser()
    args = parser.parse_args(
        [
            "--profile",
            "linux",
            "--non-interactive",
            "--database-mode",
            "postgres",
            "--db-host",
            "10.0.0.8",
            "--db-port",
            "5433",
            "--db-user",
            "case_user",
            "--db-password",
            "case_pass",
            "--db-name",
            "case_db",
            "--postgres-admin-user",
            "rootpg",
            "--backend-port",
            "9000",
            "--openai-base-url",
            "http://127.0.0.1:11434/v1",
            "--openai-api-key",
            "ollama",
            "--llm-model-gen",
            "deepseek-r1:7b",
            "--kg-backend",
            "networkx",
            "--install-dir",
            "/data/ai_testcase_gen",
            "--frontend-dist-dir",
            "/data/www/ai_testcase_gen",
            "--server-name",
            "test.example.com",
        ]
    )
    answers = collect_answers(args)
    assert answers.profile == "linux"
    assert answers.database_url == "postgresql+psycopg://case_user:case_pass@10.0.0.8:5433/case_db"
    assert answers.backend_port == 9000
    assert answers.install_dir == "/data/ai_testcase_gen"
    assert answers.server_name == "test.example.com"
    assert answers.postgres_admin_user == "rootpg"


def test_collect_answers_non_interactive_linux_defaults_to_nginx_service_user():
    parser = build_parser()
    args = parser.parse_args(["--profile", "linux", "--non-interactive"])
    answers = collect_answers(args)
    assert answers.service_user == "nginx"
    assert answers.service_group == "nginx"
