"""Клиент нового API 3X-UI v3.x: Bearer-токен + /panel/api/clients/*."""
from __future__ import annotations

import json
import logging
import time

import httpx

from .config import Location

log = logging.getLogger(__name__)


def _as_dict(v) -> dict:
    """3.11 отдаёт settings объектом, старые версии — JSON-строкой."""
    if isinstance(v, dict):
        return v
    if isinstance(v, (str, bytes)) and v:
        return json.loads(v)
    return {}


class XUIError(Exception):
    pass


class XUI:
    def __init__(self, loc: Location):
        self.loc = loc
        self.http = httpx.AsyncClient(
            base_url=loc.panel_url + "/panel/api",
            headers={"Authorization": f"Bearer {loc.api_token}", "Accept": "application/json"},
            verify=loc.verify_ssl, timeout=20,
        )
        self._inbounds: list[dict] | None = None
        self._inbounds_ts = 0.0

    async def close(self):
        await self.http.aclose()

    async def _req(self, method: str, path: str, **kw) -> dict:
        try:
            r = await self.http.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise XUIError(f"[{self.loc.key}] {path}: {e}") from e
        if r.status_code == 401:
            raise XUIError(f"[{self.loc.key}] токен не принят (401) — проверь API_TOKEN")
        if r.status_code == 403:
            raise XUIError(f"[{self.loc.key}] у токена мало прав (403) — нужен scope admin")
        if r.status_code == 404:
            raise XUIError(f"[{self.loc.key}] 404 на {path} — неверный API_TOKEN или PANEL_URL (панель отвечает 404 на чужой токен)")
        try:
            data = r.json()
        except ValueError:
            raise XUIError(f"[{self.loc.key}] {path}: не JSON (HTTP {r.status_code})")
        return data

    async def _ok(self, method: str, path: str, **kw) -> dict:
        data = await self._req(method, path, **kw)
        if not data.get("success"):
            raise XUIError(f"[{self.loc.key}] {path}: {data.get('msg')}")
        return data

    # ── inbounds ──
    async def get_inbounds(self, fresh: bool = False) -> list[dict]:
        """Inbound'ы локации в порядке из .env, settings/stream уже распарсены."""
        if not fresh and self._inbounds and time.time() - self._inbounds_ts < 300:
            return self._inbounds
        out = []
        for iid in self.loc.inbound_ids:
            obj = (await self._ok("GET", f"/inbounds/get/{iid}"))["obj"]
            obj["settings"] = _as_dict(obj.get("settings"))
            obj["stream"] = _as_dict(obj.get("streamSettings"))
            out.append(obj)
        self._inbounds, self._inbounds_ts = out, time.time()
        return out

    # ── clients ──
    async def get_client(self, email: str) -> dict | None:
        """{'client': {...}, 'inboundIds': [...]} или None, если клиента нет."""
        data = await self._req("GET", f"/clients/get/{email}")
        return data.get("obj") if data.get("success") else None

    async def upsert_client(self, client: dict):
        email = client["email"]
        ids = list(self.loc.inbound_ids)
        existing = await self.get_client(email)
        if not existing:
            await self._ok("POST", "/clients/add", json={"inboundIds": ids, "client": client})
            return
        await self._ok("POST", f"/clients/update/{email}", json=client)
        # поменял INBOUND_ID в .env — синхронизируем привязки
        have = existing.get("inboundIds") or []
        missing = [i for i in ids if i not in have]
        extra = [i for i in have if i not in ids]
        if missing:
            await self._ok("POST", f"/clients/{email}/attach", json={"inboundIds": missing})
        if extra:
            await self._ok("POST", f"/clients/{email}/detach", json={"inboundIds": extra})

    async def del_client(self, email: str):
        data = await self._req("POST", f"/clients/del/{email}")
        if not data.get("success") and await self.get_client(email):
            raise XUIError(f"[{self.loc.key}] del {email}: {data.get('msg')}")

    async def reset_traffic(self, email: str):
        await self._ok("POST", f"/clients/resetTraffic/{email}")

    async def get_traffic(self, email: str) -> dict | None:
        try:
            data = await self._req("GET", f"/clients/traffic/{email}")
        except XUIError:
            return None
        return data.get("obj") if data.get("success") else None
