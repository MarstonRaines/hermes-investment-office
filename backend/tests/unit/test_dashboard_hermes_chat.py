"""“问 Hermes”薄客户端的纯协议测试，不启动网络服务。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

CHAT_MODULE = Path(__file__).resolve().parents[3] / "dashboard" / "hermes_chat.py"
SPEC = importlib.util.spec_from_file_location("dashboard_hermes_chat", CHAT_MODULE)
assert SPEC and SPEC.loader
chat = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = chat
SPEC.loader.exec_module(chat)


def test_extract_session_token_never_accepts_missing_token():
    assert chat.extract_session_token(
        '<script>window.__HERMES_SESSION_TOKEN__="local-token";</script>'
    ) == "local-token"
    with pytest.raises(chat.HermesChatError, match="会话令牌"):
        chat.extract_session_token("<html></html>")


def test_normalize_transcript_only_keeps_visible_conversation():
    result = chat.normalize_transcript(
        [
            {"role": "system", "text": "隐藏规则"},
            {"role": "user", "text": "研究 600519.SH"},
            {"role": "tool", "name": "resolve_instrument"},
            {"role": "assistant", "text": "这是研究结论", "reasoning": "不展示"},
            {"role": "assistant", "text": ""},
        ]
    )
    assert result == [
        {"role": "user", "content": "研究 600519.SH"},
        {"role": "assistant", "content": "这是研究结论"},
    ]


def test_render_transcript_markdown_is_readable_and_path_safe():
    data, filename = chat.render_transcript_markdown(
        {
            "id": "20260825_010529_02fd7d",
            "title": "贵州茅台 / 长期研究",
            "started_at": 0,
        },
        [
            {"role": "user", "content": "研究 600519.SH"},
            {"role": "assistant", "content": "这是研究结论"},
        ],
    )
    assert "# 贵州茅台 / 长期研究" in data
    assert "## 你\n\n研究 600519.SH" in data
    assert "## Hermes\n\n这是研究结论" in data
    assert filename == "贵州茅台-长期研究-20260825_010529_02fd7d.md"


def test_session_management_stays_on_authenticated_hermes_api():
    calls = []
    client = chat.HermesChatClient(
        base_url="http://127.0.0.1:9119",
        host_header="127.0.0.1:9119",
    )

    def fake_request(method, path, *, timeout=30):
        calls.append((method, path, timeout))
        if path.startswith("/api/sessions?"):
            return {
                "sessions": [
                    {
                        "id": "dashboard-session",
                        "source": chat.SESSION_SOURCE,
                        "message_count": 2,
                    },
                    {"id": "cli-session", "source": "cli", "message_count": 2},
                ]
            }
        if path.endswith("/messages?limit=500&offset=0&order=oldest"):
            return {
                "messages": [
                    {"role": "user", "content": "问题"},
                    {"role": "assistant", "content": "回答"},
                    {"role": "tool", "content": "隐藏"},
                ]
            }
        return {"ok": True}

    client._session_request = fake_request

    assert [item["id"] for item in client.list_sessions()] == ["dashboard-session"]
    assert client.transcript("dashboard-session") == [
        {"role": "user", "content": "问题"},
        {"role": "assistant", "content": "回答"},
    ]
    client.delete_session("dashboard-session")
    assert calls[-1][:2] == ("DELETE", "/api/sessions/dashboard-session")


def test_transport_may_use_docker_host_but_protocol_host_must_be_loopback():
    assert chat._validated_endpoints(
        "http://host.docker.internal:9119", "127.0.0.1:9119"
    ) == ("host.docker.internal", 9119, "127.0.0.1:9119")
    with pytest.raises(chat.HermesChatError, match="回环"):
        chat._validated_endpoints("http://host.docker.internal:9119", "192.0.2.10:9119")


def test_stream_reply_exposes_only_assistant_text():
    class FakeSocket:
        def __init__(self):
            self.sent = []
            self.messages = iter(
                [
                    {"jsonrpc": "2.0", "id": "office-1", "result": {"status": "streaming"}},
                    {
                        "jsonrpc": "2.0",
                        "method": "event",
                        "params": {
                            "type": "reasoning.delta",
                            "session_id": "live-session",
                            "payload": {"text": "不应展示"},
                        },
                    },
                    {
                        "jsonrpc": "2.0",
                        "method": "event",
                        "params": {
                            "type": "message.delta",
                            "session_id": "live-session",
                            "payload": {"text": "研究"},
                        },
                    },
                    {
                        "jsonrpc": "2.0",
                        "method": "event",
                        "params": {
                            "type": "message.complete",
                            "session_id": "live-session",
                            "payload": {"text": "研究完成"},
                        },
                    },
                ]
            )

        def send(self, value):
            self.sent.append(json.loads(value))

        def recv(self, *, timeout):
            assert timeout > 0
            return json.dumps(next(self.messages), ensure_ascii=False)

    client = chat.HermesChatClient(
        base_url="http://127.0.0.1:9119",
        host_header="127.0.0.1:9119",
    )
    client.session_id = "live-session"
    client._ws = FakeSocket()

    assert "".join(client.stream_reply("开始研究")) == "研究完成"
    assert client._ws.sent[0]["method"] == "prompt.submit"
    assert client._ws.sent[0]["params"]["text"] == "开始研究"
