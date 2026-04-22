from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _normalize_path(raw_path: str) -> str:
    path = Path(raw_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve().as_posix()


def _env_path(name: str, default: str) -> str:
    value = (os.getenv(name) or "").strip() or default
    return _normalize_path(value)


APP_DATA_DIR = _env_path("APP_DATA_DIR", "data")
KG_STORAGE_PATH = _env_path("KG_STORAGE_PATH", str(Path(APP_DATA_DIR) / "kg_graph.json"))
KG_AUDIT_PATH = _env_path("KG_AUDIT_PATH", str(Path(APP_DATA_DIR) / "kg_audit.json"))
PROJECT_CONTEXT_JSON_PATH = _env_path("PROJECT_CONTEXT_JSON_PATH", str(Path(APP_DATA_DIR) / "project_context.json"))
TEMP_UPLOAD_DIR = _env_path("TEMP_UPLOAD_DIR", "temp_upload_api")

LEGACY_LLM_MODEL = (os.getenv("LEGACY_LLM_MODEL") or os.getenv("LLM_MODEL_GEN") or "deepseek-r1:7b").strip()
LEGACY_LLM_BASE_URL = (os.getenv("LEGACY_LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "http://localhost:11434/v1").strip()
LEGACY_LLM_API_KEY = (os.getenv("LEGACY_LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or "ollama").strip()
