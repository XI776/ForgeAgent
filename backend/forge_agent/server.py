from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import logging
import re
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .application import Application, RpcFault
from .config import Settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("forgeagent_next.server")
HEX_ID = re.compile(r"^[0-9a-f]{32}$")


def trusted_client(host: str, entries: tuple[str, ...]) -> bool:
    if not host:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host in entries
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    for entry in entries:
        try:
            network = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            if entry == host:
                return True
            continue
        if network.version == address.version and address in network:
            return True
    return False


def origin_allowed(origin: str | None, host_header: str, entries: tuple[str, ...], allowed_origins: tuple[str, ...]) -> bool:
    # Missing origins come from non-browser clients; "null" comes from file://
    # pages such as the packaged Electron app.
    if not origin or origin == "null":
        return True
    if origin in allowed_origins:
        return True
    parsed = urlsplit(origin)
    if parsed.scheme not in {"http", "https"}:
        return False
    if parsed.netloc == host_header:
        return True
    return bool(parsed.hostname and trusted_client(parsed.hostname, entries))


class Hub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self.lock = asyncio.Lock()

    async def add(self, ws: WebSocket) -> None:
        async with self.lock:
            self.clients.add(ws)

    async def remove(self, ws: WebSocket) -> None:
        async with self.lock:
            self.clients.discard(ws)

    async def broadcast(self, method: str, params: dict[str, Any]) -> None:
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        dead: list[WebSocket] = []
        async with self.lock:
            clients = list(self.clients)
        for client in clients:
            try:
                await client.send_json(payload)
            except Exception:
                dead.append(client)
        for client in dead:
            await self.remove(client)


def create_app(application: Application | None = None) -> FastAPI:
    application = application or Application()
    hub = Hub()
    application.broadcast = hub.broadcast

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await application.start()
        logger.info("ForgeAgent Host started")
        try:
            yield
        finally:
            await application.close()

    app = FastAPI(title="ForgeAgent Host", version=__version__, lifespan=lifespan)
    app.state.application, app.state.hub = application, hub

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "provider": application.settings.provider,
            "model": application.settings.model,
            "version": __version__,
        }

    trusted_entries = tuple(
        entry.strip() for entry in application.settings.trusted_clients.split(",") if entry.strip()
    )
    allowed_origins = tuple(
        entry.strip() for entry in application.settings.allowed_origins.split(",") if entry.strip()
    )

    def require_trusted(request: Request) -> None:
        if not trusted_client(request.client.host if request.client else "", trusted_entries):
            raise HTTPException(403, "forbidden")

    @app.get("/artifacts/{artifact_id}/content")
    async def artifact_content(artifact_id: str, request: Request) -> Response:
        require_trusted(request)
        if not HEX_ID.fullmatch(artifact_id):
            raise HTTPException(404, "not found")
        path = await application.artifact_path(artifact_id)
        if not path:
            raise HTTPException(404, "not found")
        result = await application.artifact_get({"id": artifact_id})
        artifact = result["artifact"]
        return FileResponse(
            path,
            media_type=artifact.get("mime_type") or "application/octet-stream",
            filename=artifact.get("filename") or "artifact",
            headers={"Cache-Control": "no-store"},
        )

    async def handle(ws: WebSocket, payload: Any) -> None:
        request_id: str | int | None = payload.get("id") if isinstance(payload, dict) else None
        if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
            await ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32600, "message": "Invalid Request"},
                }
            )
            return
        method, params = payload.get("method"), payload.get("params", {})
        if not isinstance(method, str) or not isinstance(params, dict):
            await ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32600, "message": "Invalid Request"},
                }
            )
            return
        fn = application.methods().get(method)
        if fn is None:
            if request_id is not None:
                await ws.send_json(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32601, "message": f"Method not found: {method}"},
                    }
                )
            return
        try:
            result = await fn(params)
            if request_id is not None:
                await ws.send_json({"jsonrpc": "2.0", "id": request_id, "result": result})
        except RpcFault as exc:
            if request_id is not None:
                error = {"code": exc.code, "message": exc.message}
                if exc.data is not None:
                    error["data"] = exc.data
                await ws.send_json({"jsonrpc": "2.0", "id": request_id, "error": error})
        except asyncio.CancelledError:
            if request_id is not None:
                await ws.send_json(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32001, "message": "Request cancelled"},
                    }
                )
        except Exception:
            logger.exception("RPC method %s failed", method)
            if request_id is not None:
                await ws.send_json(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32603, "message": "Internal error"},
                    }
                )

    @app.websocket("/rpc")
    async def rpc(ws: WebSocket) -> None:
        if not trusted_client(ws.client.host if ws.client else "", trusted_entries):
            await ws.close(code=1008)
            return
        if not origin_allowed(
            ws.headers.get("origin"), ws.headers.get("host", ""), trusted_entries, allowed_origins
        ):
            logger.warning("Rejected WebSocket origin %s", ws.headers.get("origin"))
            await ws.close(code=1008)
            return
        await ws.accept()
        await hub.add(ws)
        tasks: set[asyncio.Task[Any]] = set()
        try:
            while True:
                text = await ws.receive_text()
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    await ws.send_json(
                        {
                            "jsonrpc": "2.0",
                            "id": None,
                            "error": {"code": -32700, "message": "Parse error"},
                        }
                    )
                    continue
                task = asyncio.create_task(handle(ws, payload))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        except WebSocketDisconnect:
            pass
        finally:
            await hub.remove(ws)
            for task in tasks:
                task.cancel()

    if application.settings.web_dir and (application.settings.web_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=str(application.settings.web_dir), html=True), name="web")
    else:
        logger.info("Web UI directory is unavailable; serving the RPC API only")

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="ForgeAgent local host")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    settings = Settings()
    uvicorn.run(
        create_app(Application(settings)),
        host=args.host or settings.host,
        port=args.port or settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
