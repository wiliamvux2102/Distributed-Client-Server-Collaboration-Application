"""Load shared application settings from config.json or config.example.json."""

import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOCAL_CONFIG_FILE = PROJECT_ROOT / "config.json"
EXAMPLE_CONFIG_FILE = PROJECT_ROOT / "config.example.json"


def load_config() -> dict[str, Any]:
    """Load local settings, falling back to the tracked example file."""

    config_file = (
        LOCAL_CONFIG_FILE
        if LOCAL_CONFIG_FILE.exists()
        else EXAMPLE_CONFIG_FILE
    )
    try:
        with config_file.open("r", encoding="utf-8") as file:
            config = json.load(file)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Cấu hình {config_file} không phải JSON hợp lệ: {error}"
        ) from error
    except OSError as error:
        raise OSError(
            f"Không đọc được file cấu hình {config_file}: {error}"
        ) from error

    if not isinstance(config, dict):
        raise ValueError("File cấu hình phải chứa một JSON object.")
    return config


def resolve_project_path(value: str) -> Path:
    """Resolve a configured path relative to the project root."""

    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


CONFIG = load_config()
