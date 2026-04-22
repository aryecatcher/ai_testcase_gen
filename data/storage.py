import json
from pathlib import Path
from typing import Any
from src.config.runtime import APP_DATA_DIR

DATA_DIR = Path(APP_DATA_DIR)

def save_json(name: str, data: Any) -> Path:
    DATA_DIR.mkdir(exist_ok=True)
    path = DATA_DIR / f"{name}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path

def load_json(name: str) -> Any:
    path = DATA_DIR / f"{name}.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
