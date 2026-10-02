from __future__ import annotations


def client_connect_host(host: str) -> str:
    value = str(host or "").strip()
    if value in {"0.0.0.0", "::", ""}:
        return "127.0.0.1"
    return value
