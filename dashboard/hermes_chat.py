"""Hermes Agent 本地会话客户端。

业务事实仍由 Dashboard 通过 Backend REST 读取；本模块只负责把“问 Hermes”页面
接到本机 Hermes Agent 的 WebSocket JSON-RPC 网关。传输端可以是 Docker Desktop 的
``host.docker.internal``，但协议 Host 必须保持本机回环地址，避免扩大信任边界。
"""

from __future__ import annotations

import contextlib
import json
import re
import socket
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

SESSION_SOURCE = "investment-office-dashboard"
_TOKEN_PATTERN = re.compile(r'__HERMES_SESSION_TOKEN__="([^"]+)"')
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class HermesChatError(RuntimeError):
    """面向 Dashboard 的可恢复 Hermes 会话错误。"""


@dataclass(frozen=True)
class AgentHealth:
    ok: bool
    version: str = ""
    message: str = ""


def extract_session_token(html: str) -> str:
    """从 Hermes 自己生成的聊天页中读取当次进程的短期会话令牌。"""

    match = _TOKEN_PATTERN.search(html)
    if not match:
        raise HermesChatError("Hermes 未返回本机会话令牌，请重启对话服务。")
    return match.group(1)


def normalize_transcript(messages: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    """把 Hermes 历史投影成 Dashboard 需要的用户/助手消息。"""

    transcript: list[dict[str, str]] = []
    for item in messages or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        text = item.get("text") if item.get("text") is not None else item.get("content")
        if not isinstance(text, str) or not text.strip():
            continue
        transcript.append({"role": role, "content": text})
    return transcript


def render_transcript_markdown(
    session: dict[str, Any], messages: list[dict[str, str]]
) -> tuple[str, str]:
    """把可见对话导出为中文 Markdown，并返回安全文件名。"""

    session_id = str(session.get("id") or "unknown")
    title = str(session.get("title") or session.get("preview") or "Hermes 研究会话").strip()
    try:
        started_at = datetime.fromtimestamp(float(session.get("started_at") or 0)).astimezone()
        started_text = started_at.strftime("%Y-%m-%d %H:%M %Z")
    except (OSError, OverflowError, TypeError, ValueError):
        started_text = "未知"
    lines = [
        f"# {title}",
        "",
        f"- 会话 ID：`{session_id}`",
        f"- 创建时间：{started_text}",
        f"- 导出时间：{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %Z')}",
        "",
        "---",
    ]
    for message in messages:
        role = "你" if message.get("role") == "user" else "Hermes"
        lines.extend(["", f"## {role}", "", str(message.get("content") or "").strip()])
    safe_title = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", title, flags=re.UNICODE).strip("-")
    filename = f"{safe_title or 'Hermes-研究会话'}-{session_id}.md"
    return "\n".join(lines).rstrip() + "\n", filename


def _validated_endpoints(base_url: str, host_header: str) -> tuple[str, int, str]:
    transport = urlsplit(base_url.rstrip("/"))
    if transport.scheme != "http" or not transport.hostname:
        raise HermesChatError("Hermes 对话地址必须是本机 HTTP 地址。")
    logical = urlsplit(f"//{host_header}")
    if logical.hostname not in _LOOPBACK_HOSTS:
        raise HermesChatError("Hermes 协议 Host 必须保持在本机回环地址。")
    transport_port = transport.port or 80
    logical_port = logical.port or 80
    logical_netloc = logical.hostname or "127.0.0.1"
    if ":" in logical_netloc and not logical_netloc.startswith("["):
        logical_netloc = f"[{logical_netloc}]"
    if logical_port != 80:
        logical_netloc = f"{logical_netloc}:{logical_port}"
    return transport.hostname, transport_port, logical_netloc


def _http_json(
    base_url: str,
    host_header: str,
    path: str,
    *,
    timeout: float,
    method: str = "GET",
    token: str = "",
) -> Any:
    headers = {"Accept": "application/json", "Host": host_header}
    if token:
        headers["X-Hermes-Session-Token"] = token
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        headers=headers,
        method=method,
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def check_agent_health(base_url: str, host_header: str, *, timeout: float = 3) -> AgentHealth:
    """检查 Hermes 服务；失败信息只包含可操作原因，不包含会话令牌。"""

    try:
        _validated_endpoints(base_url, host_header)
        payload = _http_json(base_url, host_header, "/api/health", timeout=timeout)
        if isinstance(payload, dict) and payload.get("ok") is True:
            return AgentHealth(True, str(payload.get("version") or ""))
        return AgentHealth(False, message="Hermes 健康检查返回异常状态。")
    except (OSError, TimeoutError, ValueError, json.JSONDecodeError, HermesChatError) as exc:
        return AgentHealth(False, message=str(exc))


class HermesChatClient:
    """单个浏览器会话专用的 Hermes 持久聊天连接。"""

    def __init__(
        self,
        *,
        base_url: str,
        host_header: str,
        cwd: str = "",
        stored_session_id: str = "",
        turn_timeout: float = 600,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.host_header = host_header
        self.cwd = cwd
        self.stored_session_id = stored_session_id
        self.turn_timeout = turn_timeout
        self.session_id = ""
        self._request_counter = 0
        self._ws: Any = None

    def _next_id(self) -> str:
        self._request_counter += 1
        return f"office-{self._request_counter}"

    def _fetch_token(self) -> str:
        request = Request(
            f"{self.base_url}/chat",
            headers={"Accept": "text/html", "Host": self.host_header},
        )
        try:
            with urlopen(request, timeout=5) as response:
                return extract_session_token(response.read().decode("utf-8"))
        except HermesChatError:
            raise
        except (OSError, TimeoutError, UnicodeDecodeError) as exc:
            raise HermesChatError(f"无法读取 Hermes 会话入口：{exc}") from exc

    def _session_request(self, method: str, path: str, *, timeout: float = 30) -> Any:
        """调用 Hermes 自己的本机会话 API，不绕过其持久化层。"""

        try:
            return _http_json(
                self.base_url,
                self.host_header,
                path,
                timeout=timeout,
                method=method,
                token=self._fetch_token(),
            )
        except HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
                detail = payload.get("detail") or payload.get("message")
            except (ValueError, UnicodeDecodeError):
                detail = None
            raise HermesChatError(str(detail or f"Hermes 会话请求失败（HTTP {exc.code}）。")) from exc
        except (URLError, OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            raise HermesChatError(f"无法读取 Hermes 会话：{exc}") from exc

    def _open_websocket(self) -> Any:
        transport_host, transport_port, logical_netloc = _validated_endpoints(
            self.base_url, self.host_header
        )
        token = self._fetch_token()
        raw_socket = socket.create_connection((transport_host, transport_port), timeout=5)
        try:
            return connect(
                f"ws://{logical_netloc}/api/ws?token={quote(token, safe='')}",
                sock=raw_socket,
                origin=f"http://{logical_netloc}",
                proxy=None,
                open_timeout=8,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=3,
                max_size=4 * 1024 * 1024,
            )
        except Exception:
            raw_socket.close()
            raise

    def _disconnect(self) -> None:
        if self._ws is not None:
            with contextlib.suppress(ConnectionClosed, OSError, RuntimeError, TimeoutError):
                self._ws.close()
        self._ws = None
        self.session_id = ""

    def close(self) -> None:
        """断开显示连接；Hermes 中的持久会话保留。"""

        self._disconnect()

    def new_session(self) -> None:
        """从当前页面开始一段新会话，不删除任何历史会话。"""

        self._disconnect()
        self.stored_session_id = ""

    def list_sessions(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """列出由 Investment Office 创建的最近会话。"""

        query = urlencode(
            {
                "limit": min(max(limit, 1), 100),
                "order": "recent",
                "source": SESSION_SOURCE,
                "min_messages": 1,
            }
        )
        payload = self._session_request("GET", f"/api/sessions?{query}")
        rows = payload.get("sessions") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise HermesChatError("Hermes 未返回有效的历史会话列表。")
        return [
            row
            for row in rows
            if isinstance(row, dict) and row.get("id") and row.get("source") == SESSION_SOURCE
        ]

    def transcript(self, stored_session_id: str) -> list[dict[str, str]]:
        """读取一个持久会话的完整可见对话，用于本地导出。"""

        if not stored_session_id:
            raise HermesChatError("会话 ID 不能为空。")
        raw_messages: list[dict[str, Any]] = []
        offset = 0
        while True:
            query = urlencode({"limit": 500, "offset": offset, "order": "oldest"})
            payload = self._session_request(
                "GET",
                f"/api/sessions/{quote(stored_session_id, safe='')}/messages?{query}",
                timeout=45,
            )
            batch = payload.get("messages") if isinstance(payload, dict) else None
            if not isinstance(batch, list):
                raise HermesChatError("Hermes 未返回有效的会话消息。")
            raw_messages.extend(item for item in batch if isinstance(item, dict))
            if len(batch) < 500:
                break
            offset += len(batch)
        return normalize_transcript(raw_messages)

    def delete_session(self, stored_session_id: str) -> None:
        """永久删除一个 Hermes 持久会话；当前会话会先安全关闭。"""

        if not stored_session_id:
            raise HermesChatError("会话 ID 不能为空。")
        deleting_current = stored_session_id == self.stored_session_id
        if deleting_current and self._ws is not None and self.session_id:
            self._rpc("session.close", {"session_id": self.session_id})
            self.session_id = ""
        payload = self._session_request(
            "DELETE",
            f"/api/sessions/{quote(stored_session_id, safe='')}",
        )
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise HermesChatError("Hermes 未确认删除这段会话。")
        if deleting_current:
            self._disconnect()
            self.stored_session_id = ""

    def _send_request(self, method: str, params: dict[str, Any]) -> str:
        if self._ws is None:
            raise HermesChatError("Hermes 尚未连接。")
        request_id = self._next_id()
        self._ws.send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params,
                },
                ensure_ascii=False,
            )
        )
        return request_id

    def _receive(self, *, timeout: float) -> dict[str, Any]:
        if self._ws is None:
            raise HermesChatError("Hermes 连接已关闭。")
        raw = self._ws.recv(timeout=timeout)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise HermesChatError("Hermes 返回了无法识别的消息。")
        return payload

    def _rpc(self, method: str, params: dict[str, Any], *, timeout: float = 30) -> dict[str, Any]:
        request_id = self._send_request(method, params)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HermesChatError(f"Hermes {method} 请求超时。")
            message = self._receive(timeout=remaining)
            if message.get("id") != request_id:
                continue
            if error := message.get("error"):
                detail = error.get("message") if isinstance(error, dict) else str(error)
                raise HermesChatError(str(detail or f"Hermes {method} 请求失败。"))
            result = message.get("result")
            if not isinstance(result, dict):
                raise HermesChatError(f"Hermes {method} 未返回有效结果。")
            return result

    def connect(self) -> list[dict[str, str]]:
        """连接并新建或恢复会话，返回恢复出的可见历史。"""

        if self._ws is not None and self.session_id:
            return []
        try:
            self._ws = self._open_websocket()
            if self.stored_session_id:
                result = self._rpc(
                    "session.resume",
                    {
                        "session_id": self.stored_session_id,
                        "source": SESSION_SOURCE,
                    },
                    timeout=45,
                )
                self.session_id = str(result.get("session_id") or "")
                self.stored_session_id = str(
                    result.get("session_key") or result.get("resumed") or self.stored_session_id
                )
                return normalize_transcript(result.get("messages"))

            params: dict[str, Any] = {
                "source": SESSION_SOURCE,
                "close_on_disconnect": False,
            }
            if self.cwd:
                params["cwd"] = self.cwd
            result = self._rpc("session.create", params, timeout=45)
            self.session_id = str(result.get("session_id") or "")
            self.stored_session_id = str(result.get("stored_session_id") or "")
            if not self.session_id or not self.stored_session_id:
                raise HermesChatError("Hermes 未能创建持久会话。")
            return normalize_transcript(result.get("messages"))
        except HermesChatError:
            self._disconnect()
            raise
        except (ConnectionClosed, OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            self._disconnect()
            raise HermesChatError(f"无法连接 Hermes：{exc}") from exc

    def stream_reply(
        self,
        text: str,
        *,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> Iterator[str]:
        """提交一个用户问题，只把助手正文流式交给 Dashboard。"""

        if not text.strip():
            raise HermesChatError("问题不能为空。")
        if self._ws is None or not self.session_id:
            self.connect()

        request_id = self._send_request(
            "prompt.submit",
            {"session_id": self.session_id, "text": text},
        )
        deadline = time.monotonic() + self.turn_timeout
        chunks: list[str] = []
        accepted = False

        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise HermesChatError("Hermes 研究超时；会话已保留，可稍后继续追问。")
                message = self._receive(timeout=remaining)

                if message.get("id") == request_id:
                    if error := message.get("error"):
                        detail = error.get("message") if isinstance(error, dict) else str(error)
                        raise HermesChatError(str(detail or "Hermes 拒绝了这次提问。"))
                    accepted = True
                    continue

                if message.get("method") != "event":
                    continue
                params = message.get("params") or {}
                if params.get("session_id") not in {None, "", self.session_id}:
                    continue
                event_type = str(params.get("type") or "")
                payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}
                if on_event:
                    on_event(event_type, payload)

                if event_type == "message.delta":
                    delta = payload.get("text")
                    if isinstance(delta, str) and delta:
                        chunks.append(delta)
                        yield delta
                elif event_type == "message.complete":
                    final = payload.get("text")
                    streamed = "".join(chunks)
                    if isinstance(final, str) and final and final.startswith(streamed):
                        suffix = final[len(streamed) :]
                        if suffix:
                            yield suffix
                    return
                elif event_type == "approval.request":
                    raise HermesChatError(
                        "Hermes 请求执行额外批准操作；本页面只处理研究对话，请改为只读问题。"
                    )
                elif event_type == "error":
                    detail = payload.get("message") or payload.get("text") or "Hermes 研究失败。"
                    raise HermesChatError(str(detail))
        except HermesChatError:
            raise
        except (ConnectionClosed, OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            self._disconnect()
            phase = "已接收问题但连接中断" if accepted else "发送前连接中断"
            raise HermesChatError(f"Hermes {phase}；会话已保留，请重新发送。") from exc
