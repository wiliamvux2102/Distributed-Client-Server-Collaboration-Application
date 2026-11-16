"""Load shared application settings from config.json, falling back to config.example.json."""

import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOCAL_CONFIG_FILE = PROJECT_ROOT / "config.json"
EXAMPLE_CONFIG_FILE = PROJECT_ROOT / "config.example.json"
DEFAULT_DOWNLOADS_DIR = PROJECT_ROOT / "downloads"


def _read_json_object(config_file: Path) -> dict[str, Any]:
    """Read a JSON configuration file and ensure the root value is an object."""
    try:
        with config_file.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Cấu hình {config_file} không phải JSON hợp lệ: {error}"
        ) from error
    except OSError as error:
        raise OSError(
            f"Không đọc được file cấu hình {config_file}: {error}"
        ) from error

    if not isinstance(data, dict):
        raise ValueError("File cấu hình phải chứa một JSON object.")
    return data


def _require_non_empty_str(section: dict[str, Any], key: str, full_name: str) -> None:
    value = section.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Thiếu hoặc sai định dạng tham số cấu hình '{full_name}' (cần chuỗi không rỗng).")


def _require_port(section: dict[str, Any], key: str, full_name: str) -> None:
    value = section.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise ValueError(f"Tham số cấu hình '{full_name}' phải là số nguyên trong [1, 65535].")


def _require_positive_int(section: dict[str, Any], key: str, full_name: str) -> None:
    value = section.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"Tham số cấu hình '{full_name}' phải là số nguyên dương.")


def _require_positive_number(section: dict[str, Any], key: str, full_name: str) -> None:
    value = section.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"Tham số cấu hình '{full_name}' phải là số dương.")


def _validate_config(config: dict[str, Any], config_file: Path) -> None:
    """Validate that all required sections, keys, types, and ranges exist."""
    required_sections = ("server", "udp", "heartbeat", "tls", "protocol")
    for section in required_sections:
        if not isinstance(config.get(section), dict):
            raise ValueError(f"Thiếu hoặc sai định dạng nhóm cấu hình '{section}' trong {config_file.name}.")

    server = config["server"]
    _require_non_empty_str(server, "tcp_bind_host", "server.tcp_bind_host")
    _require_port(server, "tcp_port", "server.tcp_port")
    _require_positive_number(server, "tcp_connect_timeout_seconds", "server.tcp_connect_timeout_seconds")
    _require_positive_number(server, "accept_timeout_seconds", "server.accept_timeout_seconds")
    _require_positive_number(server, "pre_login_timeout_seconds", "server.pre_login_timeout_seconds")

    udp = config["udp"]
    _require_non_empty_str(udp, "bind_host", "udp.bind_host")
    _require_port(udp, "port", "udp.port")
    _require_non_empty_str(udp, "discovery_target", "udp.discovery_target")
    _require_positive_int(udp, "discovery_attempts", "udp.discovery_attempts")
    _require_positive_number(udp, "discovery_timeout_seconds", "udp.discovery_timeout_seconds")
    _require_positive_number(udp, "wait_interval_seconds", "udp.wait_interval_seconds")
    _require_positive_int(udp, "protocol_version", "udp.protocol_version")

    heartbeat = config["heartbeat"]
    _require_positive_number(heartbeat, "interval_seconds", "heartbeat.interval_seconds")
    _require_positive_number(heartbeat, "timeout_seconds", "heartbeat.timeout_seconds")

    tls = config["tls"]
    _require_non_empty_str(tls, "minimum_version", "tls.minimum_version")
    _require_non_empty_str(tls, "server_name", "tls.server_name")
    _require_non_empty_str(tls, "ca_certificate", "tls.ca_certificate")
    _require_non_empty_str(tls, "server_certificate", "tls.server_certificate")
    _require_non_empty_str(tls, "server_private_key", "tls.server_private_key")

    protocol = config["protocol"]
    _require_positive_int(protocol, "max_payload_bytes", "protocol.max_payload_bytes")
    _require_positive_int(protocol, "max_direct_message_bytes", "protocol.max_direct_message_bytes")


def load_config(
    example_file: Path = EXAMPLE_CONFIG_FILE,
    local_file: Path = LOCAL_CONFIG_FILE,
) -> dict[str, Any]:
    """Load settings from config.json (or config.example.json if absent) and validate."""
    config_file = local_file if local_file.exists() else example_file
    config = _read_json_object(config_file)
    _validate_config(config, config_file)
    return config


def resolve_project_path(value: str) -> Path:
    """Resolve a configured path relative to the project root."""

    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


CONFIG = load_config()
