from __future__ import annotations

import asyncio
import html
import json
import mimetypes
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from src.common.network import client_connect_host


class JsonTcpError(RuntimeError):
    pass


@dataclass
class JsonTcpResponse:
    ok: bool
    result: Any = None
    error: Any = None
    raw: dict[str, Any] | None = None


async def send_json_request(
    host: str,
    port: int,
    command: str,
    params: dict[str, Any] | None = None,
    timeout_s: float = 2.0,
    request_id: str | None = None,
) -> JsonTcpResponse:
    request_id = request_id or f"core-{uuid.uuid4().hex[:12]}"
    connect_host = client_connect_host(host)
    payload = {
        "id": request_id,
        "command": command,
        "params": params or {},
    }

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(connect_host, port), timeout=timeout_s
        )
    except OSError as exc:
        raise JsonTcpError(f"connect failed: {connect_host}:{port}: {exc}") from exc
    except asyncio.TimeoutError as exc:
        raise JsonTcpError(f"connect timeout: {connect_host}:{port}") from exc

    try:
        writer.write(json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n")
        await asyncio.wait_for(writer.drain(), timeout=timeout_s)

        while True:
            raw_line = await asyncio.wait_for(reader.readline(), timeout=timeout_s)
            if not raw_line:
                raise JsonTcpError("connection closed before response")
            try:
                message = json.loads(raw_line.decode("utf-8"))
            except json.JSONDecodeError:
                continue
            if message.get("id") != request_id:
                continue
            if message.get("ok") is True:
                return JsonTcpResponse(ok=True, result=message.get("result"), raw=message)
            return JsonTcpResponse(
                ok=False,
                error=message.get("error") or message.get("message"),
                raw=message,
            )
    except asyncio.TimeoutError as exc:
        raise JsonTcpError(f"request timeout: {command}") from exc
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


JsonCommandHandler = Callable[[str, dict[str, Any]], Awaitable[JsonTcpResponse]]


class JsonLineServer:
    def __init__(
        self,
        host: str,
        port: int,
        handler: JsonCommandHandler,
        static_root: Path | str | None = None,
        banner: dict[str, Any] | None = None,
        allow_remote_process_control: bool = False,
    ) -> None:
        self.host = host
        self.port = port
        self.handler = handler
        self.static_root = Path(static_root).resolve() if static_root else None
        self.banner = banner
        self.allow_remote_process_control = allow_remote_process_control
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_client, self.host, self.port)

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    async def serve_forever(self) -> None:
        if self._server is None:
            await self.start()
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        first_line: bytes | None = None
        try:
            first_line = await asyncio.wait_for(reader.readline(), timeout=0.15)
        except asyncio.TimeoutError:
            first_line = None

        if first_line and self._is_http_request(first_line):
            await self._handle_http_request(first_line, reader, writer)
            return

        try:
            if self.banner:
                writer.write(
                    json.dumps(self.banner, separators=(",", ":")).encode("utf-8") + b"\n"
                )
                await writer.drain()

            while True:
                raw_line = first_line if first_line is not None else await reader.readline()
                first_line = None
                if not raw_line:
                    break
                try:
                    request = json.loads(raw_line.decode("utf-8"))
                    if not isinstance(request, dict):
                        raise ValueError("request must be an object")
                    request_id = request.get("id")
                    command = request.get("command")
                    params = request.get("params") or {}
                    if not isinstance(command, str) or not isinstance(params, dict):
                        raise ValueError("command must be string and params must be object")
                    if self._requires_local_process_control(command, writer):
                        response = JsonTcpResponse(
                            False,
                            error={
                                "code": "LOCAL_ONLY_COMMAND",
                                "message": "process start/stop/restart is allowed only from localhost",
                            },
                        )
                    else:
                        response = await self.handler(command, params)
                    message = {
                        "id": request_id,
                        "ok": response.ok,
                    }
                    if response.ok:
                        message["result"] = response.result
                    else:
                        message["error"] = response.error
                except Exception as exc:  # Keep the server alive for malformed clients.
                    message = {
                        "id": None,
                        "ok": False,
                        "error": {
                            "code": "BAD_REQUEST",
                            "message": str(exc),
                        },
                    }
                writer.write(json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n")
                await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass

        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass

    def _requires_local_process_control(
        self,
        command: str,
        writer: asyncio.StreamWriter,
    ) -> bool:
        return (
            _is_local_only_command(command)
            and not self.allow_remote_process_control
            and not _is_loopback_peer(writer)
        )

    @staticmethod
    def _is_http_request(raw_line: bytes) -> bool:
        return raw_line.startswith((b"GET ", b"HEAD ", b"POST ", b"PUT ", b"PATCH ", b"OPTIONS "))

    async def _handle_http_request(
        self,
        first_line: bytes,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        request_line = first_line.decode("iso-8859-1", errors="replace").strip()
        parts = request_line.split()
        method = parts[0].upper() if parts else "GET"
        target = parts[1] if len(parts) > 1 else "/"
        route = unquote(urlparse(target).path or "/")

        headers: dict[str, str] = {}
        while True:
            line = await reader.readline()
            if not line or line in {b"\r\n", b"\n"}:
                break
            decoded = line.decode("iso-8859-1", errors="replace")
            name, _, value = decoded.partition(":")
            if name:
                headers[name.strip().lower()] = value.strip()

        try:
            content_length = int(headers.get("content-length", "0") or "0")
        except ValueError:
            content_length = 0
        body_bytes = b""
        if content_length > 0:
            body_bytes = await reader.readexactly(content_length)

        if method == "OPTIONS":
            await self._write_http_response(
                writer,
                "204 No Content",
                b"",
                "text/plain; charset=utf-8",
                method,
            )
            return

        if route in {"/api/status", "/status.json"}:
            response = await self.handler("get_status", {})
            await self._write_json_response(writer, response, method)
            return
        if route == "/api/devices":
            response = await self.handler("get_devices", {})
            await self._write_json_response(writer, response, method)
            return
        if route == "/api/processes":
            response = await self.handler("get_processes", {})
            await self._write_json_response(writer, response, method)
            return
        if route in {"/api/logs", "/api/logs/export"} and method == "GET":
            query = {key: values[-1] for key, values in parse_qs(urlparse(target).query).items()}
            response = await self.handler("logs.export" if route.endswith("export") else "logs.history", query)
            await self._write_json_response(writer, response, method)
            return
        if route == "/api/logs/import" and method == "POST":
            response = await self.handler("logs.import", _decode_json_body(body_bytes))
            await self._write_json_response(writer, response, method)
            return
        if route in {"/api/config", "/config.json"} and method == "GET":
            response = await self.handler("get_config", {})
            await self._write_json_response(writer, response, method)
            return
        if route == "/api/config" and method in {"POST", "PUT", "PATCH"}:
            payload = _decode_json_body(body_bytes)
            patch = payload.get("patch", payload)
            response = await self.handler("update_config", {"patch": patch})
            await self._write_json_response(writer, response, method)
            return
        if route == "/api/command" and method == "POST":
            payload = _decode_json_body(body_bytes)
            command = str(payload.get("command") or "")
            if self._requires_local_process_control(command, writer):
                response = _local_only_response()
            else:
                response = await self.handler(command, _dict(payload.get("params")))
            await self._write_json_response(writer, response, method)
            return
        if route.startswith("/api/process/") and method == "POST":
            parts = [part for part in route.split("/") if part]
            module_id = parts[2] if len(parts) >= 4 else ""
            action = parts[3] if len(parts) >= 4 else ""
            if action in {"start", "stop", "restart"} and not self.allow_remote_process_control and not _is_loopback_peer(writer):
                response = _local_only_response()
            else:
                response = await self.handler(f"process.{action}", {"module": module_id})
            await self._write_json_response(writer, response, method)
            return
        if route.startswith("/api/device/") and route.endswith("/command") and method == "POST":
            parts = [part for part in route.split("/") if part]
            device_id = parts[2] if len(parts) >= 4 else ""
            payload = _decode_json_body(body_bytes)
            response = await self.handler(
                "device.command",
                {
                    "device": device_id,
                    "command": str(payload.get("command") or ""),
                    "params": _dict(payload.get("params")),
                },
            )
            await self._write_json_response(writer, response, method)
            return
        if route.startswith("/api/"):
            await self._write_http_response(
                writer,
                "404 Not Found",
                json.dumps(
                    {"ok": False, "error": {"code": "NOT_FOUND", "message": route}},
                    ensure_ascii=False,
                ).encode("utf-8"),
                "application/json; charset=utf-8",
                method,
            )
            return

        if await self._try_static_response(route, writer, method):
            return

        status_response = await self.handler("get_status", {})
        status_json = json.dumps(
            status_response.result if status_response.ok else {"error": status_response.error},
            ensure_ascii=False,
            indent=2,
        )
        body = _render_tcp_help_page(status_json).encode("utf-8")
        await self._write_http_response(
            writer,
            "200 OK",
            body,
            "text/html; charset=utf-8",
            method,
        )

    async def _write_json_response(
        self,
        writer: asyncio.StreamWriter,
        response: JsonTcpResponse,
        method: str,
    ) -> None:
        status = "200 OK" if response.ok else "500 Internal Server Error"
        body = json.dumps(
            {
                "ok": response.ok,
                "result": response.result if response.ok else None,
                "error": None if response.ok else response.error,
            },
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        await self._write_http_response(
            writer,
            status,
            body,
            "application/json; charset=utf-8",
            method,
        )

    async def _try_static_response(
        self,
        route: str,
        writer: asyncio.StreamWriter,
        method: str,
    ) -> bool:
        if self.static_root is None:
            return False
        root = self.static_root
        relative = "index.html" if route == "/" else route.lstrip("/")
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            await self._write_http_response(
                writer,
                "403 Forbidden",
                b"Forbidden",
                "text/plain; charset=utf-8",
                method,
            )
            return True
        if target.is_dir():
            target = target / "index.html"
        if not target.exists() or not target.is_file():
            return False
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if target.suffix == ".js":
            content_type = "text/javascript; charset=utf-8"
        elif target.suffix in {".html", ".css", ".json", ".svg"}:
            content_type = f"{content_type}; charset=utf-8"
        await self._write_http_response(
            writer,
            "200 OK",
            target.read_bytes(),
            content_type,
            method,
        )
        return True

    async def _write_http_response(
        self,
        writer: asyncio.StreamWriter,
        status: str,
        body: bytes,
        content_type: str,
        method: str,
    ) -> None:
        response_body = b"" if method == "HEAD" else body
        header = (
            f"HTTP/1.1 {status}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(response_body)}\r\n"
            "Connection: close\r\n"
            "Cache-Control: no-store\r\n"
            "Access-Control-Allow-Origin: *\r\n"
            "Access-Control-Allow-Headers: Content-Type\r\n"
            "Access-Control-Allow-Methods: GET,POST,PUT,PATCH,OPTIONS,HEAD\r\n"
            "\r\n"
        ).encode("ascii")
        try:
            writer.write(header + response_body)
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            return
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


def _render_tcp_help_page(status_json: str) -> str:
    escaped_status = html.escape(status_json)
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Tracker Core TCP Port</title>
  <style>
    body {{
      margin: 0;
      font-family: Consolas, "Malgun Gothic", monospace;
      background: #101418;
      color: #e5edf5;
    }}
    main {{
      max-width: 980px;
      margin: 0 auto;
      padding: 40px 24px;
    }}
    h1 {{ font-size: 24px; margin: 0 0 10px; }}
    p {{ color: #a9b6c3; line-height: 1.6; }}
    code, pre {{
      background: #18212b;
      border: 1px solid #263545;
      border-radius: 6px;
    }}
    code {{ padding: 2px 6px; }}
    pre {{
      padding: 16px;
      overflow: auto;
      white-space: pre-wrap;
    }}
    a {{ color: #7db7ff; }}
  </style>
</head>
<body>
  <main>
    <h1>Tracker Core TCP Server</h1>
    <p>이 포트는 기본적으로 TCP JSON Lines 제어 포트입니다. 브라우저 접속은 점검용 안내 화면만 제공합니다.</p>
    <p>상태 JSON: <a href="/api/status">/api/status</a> / 설정 JSON: <a href="/api/config">/api/config</a></p>
    <p>TCP 요청 예: <code>{{"id":"req-1","command":"get_status","params":{{}}}}</code></p>
    <h2>Current Status</h2>
    <pre>{escaped_status}</pre>
  </main>
</body>
</html>
"""


def _decode_json_body(body: bytes) -> dict[str, Any]:
    if not body:
        return {}
    data = json.loads(body.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("HTTP JSON body must be an object")
    return data


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _is_local_only_command(command: str) -> bool:
    return command in {"process.start", "process.stop", "process.restart"}


def _is_loopback_peer(writer: asyncio.StreamWriter) -> bool:
    peer = writer.get_extra_info("peername")
    if not peer:
        return False
    host = peer[0] if isinstance(peer, tuple) else str(peer)
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return host in {"localhost", "::1"} or host.startswith("127.")


def _local_only_response() -> JsonTcpResponse:
    return JsonTcpResponse(
        False,
        error={
            "code": "LOCAL_ONLY_COMMAND",
            "message": "process start/stop/restart is allowed only from localhost",
        },
    )
