from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter, _derive_chat_session_id
from hermes_state import SessionDB

KEY = "openwebui:chat-session"


@pytest.fixture
def chat_server(tmp_path):
    adapter = APIServerAdapter(
        PlatformConfig(enabled=True, extra={"host": "127.0.0.1", "port": 0, "key": "test-key"})
    )
    db = SessionDB(tmp_path / "state.db")
    adapter._session_db = db
    created = []
    turns = []

    def create_agent(**kwargs):
        created.append(kwargs)
        session_id = kwargs["session_id"]
        agent = MagicMock()
        agent.session_id = session_id
        agent.session_prompt_tokens = 0
        agent.session_completion_tokens = 0
        agent.session_total_tokens = 0

        def run_conversation(**turn):
            turns.append(turn)
            if db.get_session(session_id) is None:
                db.create_session(session_id=session_id, source="api_server", model="m")
            return {"final_response": "ok", "messages": [], "api_calls": 1}

        agent.run_conversation.side_effect = run_conversation
        return agent

    adapter._create_agent = create_agent
    app = web.Application()
    app.router.add_post("/v1/chat/completions", adapter._handle_chat_completions)
    try:
        yield adapter, db, app, created, turns
    finally:
        db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("selection", ["declared", "explicit", "undeclared"])
async def test_chat_session_precedence_preserves_history(chat_server, stream, selection):
    adapter, db, app, created, turns = chat_server
    db.create_session(session_id="responses-session", source="api_server", model="m")
    db.record_gateway_session_peer("responses-session", source="api_server", session_key=KEY)
    headers = {"Authorization": "Bearer test-key"}
    expected = "responses-session"
    history = [{"role": "user", "content": "first"}, {"role": "assistant", "content": "reply"}]
    if selection != "undeclared":
        headers["X-Hermes-Session-Key"] = KEY
    else:
        expected = _derive_chat_session_id(None, "first", None)
    if selection == "explicit":
        headers["X-Hermes-Session-Id"] = expected = "explicit-session"
        db.create_session(session_id=expected, source="api_server", model="m")
        history = []
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/v1/chat/completions",
            headers=headers,
            json={"messages": [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "reply"},
                {"role": "user", "content": "continue"},
            ], "stream": stream},
        )
        assert response.status == 200
        await response.read()
        assert response.headers["X-Hermes-Session-Id"] == expected
    assert created[0]["session_id"] == expected
    assert turns[0]["conversation_history"] == history
    assert adapter._declared_conversation_session(KEY) == "responses-session"
    if selection == "explicit":
        assert not db.get_session(expected)["session_key"]


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_unknown_declared_key_binds_fingerprint_session(chat_server, stream):
    adapter, db, app, created, _turns = chat_server
    expected = _derive_chat_session_id(None, "first", None)
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer test-key", "X-Hermes-Session-Key": KEY},
            json={"messages": [{"role": "user", "content": "first"}], "stream": stream},
        )
        assert response.status == 200
        await response.read()
    assert created[0]["session_id"] == expected
    assert db.get_session(expected)["session_key"] == KEY
    assert adapter._declared_conversation_session(KEY) == expected
