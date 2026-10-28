"""Minimal Directus REST client.

The agents read and write business data THROUGH Directus with the agent's
service-account token, so they are bound by the same role/permission model and
audit trail as human users (the agent role cannot modify solutions or cases
beyond what its policy allows).
"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

import httpx

from .config import get_settings


class DirectusError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"Directus HTTP {status}: {body[:500]}")
        self.status = status


class Directus:
    def __init__(self, base_url: str, token: str, timeout: float = 30):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(base_url=self.base_url, timeout=timeout,
                                   headers={"Authorization": f"Bearer {token}"} if token else {})

    @classmethod
    def login(cls, base_url: str, email: str, password: str) -> "Directus":
        r = httpx.post(f"{base_url.rstrip('/')}/auth/login", json={"email": email, "password": password}, timeout=30)
        if r.status_code != 200:
            raise DirectusError(r.status_code, r.text)
        return cls(base_url, r.json()["data"]["access_token"])

    # -- generic -------------------------------------------------------------
    def request(self, method: str, path: str, *, params: dict | None = None, body: Any = None,
                ok_statuses: tuple[int, ...] = ()) -> Any:
        if params:
            params = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in params.items()}
        r = self.client.request(method, path, params=params, json=body)
        if r.status_code >= 400 and r.status_code not in ok_statuses:
            raise DirectusError(r.status_code, r.text)
        if r.status_code == 204 or not r.content:
            return None
        data = r.json()
        return data.get("data", data) if isinstance(data, dict) else data

    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params=params or None)

    def post(self, path: str, body: Any) -> Any:
        return self.request("POST", path, body=body)

    def patch(self, path: str, body: Any) -> Any:
        return self.request("PATCH", path, body=body)

    def delete(self, path: str) -> Any:
        return self.request("DELETE", path)

    # -- items -----------------------------------------------------------------
    def items(self, collection: str, *, fields: list[str] | None = None, filter: dict | None = None,
              sort: list[str] | None = None, limit: int = -1, **extra: Any) -> list[dict]:
        params: dict[str, Any] = {"limit": limit}
        if fields:
            params["fields"] = ",".join(fields)
        if filter:
            params["filter"] = filter
        if sort:
            params["sort"] = ",".join(sort)
        params.update(extra)
        return self.get(f"/items/{collection}", **params) or []

    def item(self, collection: str, key: str, fields: list[str] | None = None) -> dict:
        params = {"fields": ",".join(fields)} if fields else {}
        return self.get(f"/items/{collection}/{key}", **params)

    def create(self, collection: str, data: dict | list[dict]) -> Any:
        return self.post(f"/items/{collection}", data)

    def update(self, collection: str, key: str, data: dict) -> Any:
        return self.patch(f"/items/{collection}/{key}", data)

    def update_many(self, collection: str, keys: list[str], data: dict) -> Any:
        return self.patch(f"/items/{collection}", {"keys": keys, "data": data})


@lru_cache
def get_directus() -> Directus:
    s = get_settings()
    return Directus(s.directus_url, s.directus_agent_token)
