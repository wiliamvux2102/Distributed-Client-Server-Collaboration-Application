"""Tạo cấu hình TLS dùng chung cho server và client."""

import ssl
from common.app_config import CONFIG, resolve_project_path


TLS_CONFIG = CONFIG["tls"]
SERVER_CERT_FILE = resolve_project_path(TLS_CONFIG["server_certificate"])
SERVER_KEY_FILE = resolve_project_path(TLS_CONFIG["server_private_key"])
CA_CERT_FILE = resolve_project_path(TLS_CONFIG["ca_certificate"])
TLS_SERVER_NAME = TLS_CONFIG["server_name"]


def _minimum_tls_version() -> ssl.TLSVersion:
    version_name = TLS_CONFIG["minimum_version"].replace(".", "_")
    try:
        return getattr(ssl.TLSVersion, version_name)
    except AttributeError as error:
        raise ValueError(
            f"Phiên bản TLS không hợp lệ: {TLS_CONFIG['minimum_version']}"
        ) from error


def create_server_context() -> ssl.SSLContext:
    """Tạo TLS context để server trình chứng chỉ cho client."""

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = _minimum_tls_version()

    context.load_cert_chain(
        certfile=str(SERVER_CERT_FILE),
        keyfile=str(SERVER_KEY_FILE),
    )

    return context


def create_client_context() -> ssl.SSLContext:
    """Tạo TLS context để client xác minh chứng chỉ server."""

    context = ssl.create_default_context(
        purpose=ssl.Purpose.SERVER_AUTH,
        cafile=str(CA_CERT_FILE),
    )
    context.minimum_version = _minimum_tls_version()

    return context
