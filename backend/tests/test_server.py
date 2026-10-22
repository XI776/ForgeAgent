import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from forge_agent.application import Application
from forge_agent.config import Settings
from forge_agent.server import create_app, origin_allowed, trusted_client


def test_health_and_json_rpc(tmp_path):
    application = Application(Settings(data_dir=tmp_path / "data", workspace_dir=tmp_path / "workspace"))
    with TestClient(create_app(application)) as client:
        assert client.get("/health").json()["status"] == "ok"
        with client.websocket_connect("/rpc") as ws:
            ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "system.info", "params": {}})
            response = ws.receive_json()
            assert response["id"] == 1
            assert response["result"]["name"] == "ForgeAgent"


def test_rpc_errors_are_structured(tmp_path):
    application = Application(Settings(data_dir=tmp_path / "data", workspace_dir=tmp_path / "workspace"))
    with TestClient(create_app(application)) as client:
        with client.websocket_connect("/rpc") as ws:
            ws.send_json({"jsonrpc": "2.0", "id": "x", "method": "missing", "params": {}})
            assert ws.receive_json()["error"]["code"] == -32601
            ws.send_text("not json")
            assert ws.receive_json()["error"]["code"] == -32700


def test_built_web_ui_is_served_without_shadowing_the_api(tmp_path):
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text('<!doctype html><div id="root"></div>', encoding="utf-8")
    (web / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    application = Application(
        Settings(data_dir=tmp_path / "data", workspace_dir=tmp_path / "workspace", web_dir=web)
    )
    with TestClient(create_app(application)) as client:
        index = client.get("/")
        assert index.status_code == 200 and 'id="root"' in index.text
        assert client.get("/assets/app.js").status_code == 200
        assert client.get("/health").json()["status"] == "ok"


def test_trusted_clients_and_websocket_origins():
    entries = ("127.0.0.0/8", "::1/128", "localhost", "testclient")
    assert trusted_client("127.0.0.1", entries)
    assert trusted_client("::ffff:127.0.0.1", entries)
    assert trusted_client("localhost", entries)
    assert not trusted_client("192.0.2.10", entries)
    assert origin_allowed(None, "127.0.0.1:8765", entries, ())
    assert origin_allowed("null", "127.0.0.1:8765", entries, ())
    assert origin_allowed("http://127.0.0.1:5173", "127.0.0.1:8765", entries, ())
    assert origin_allowed("http://192.0.2.10:8765", "192.0.2.10:8765", entries, ())
    assert not origin_allowed("https://evil.example", "127.0.0.1:8765", entries, ())
    assert origin_allowed("https://agent.example.test", "127.0.0.1:8765", entries, ("https://agent.example.test",))


def test_websocket_rejects_cross_site_origins(tmp_path):
    application = Application(Settings(data_dir=tmp_path / "data", workspace_dir=tmp_path / "workspace"))
    with TestClient(create_app(application)) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/rpc", headers={"origin": "https://evil.example"}):
                pass
