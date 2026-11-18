"""@CryptoBot (Crypto Pay API) — счета сразу в рублях, юзер платит USDT/TON по курсу."""
from __future__ import annotations

import httpx


class CryptoPayError(Exception):
    pass


class CryptoPay:
    def __init__(self, token: str, testnet: bool = False, assets: str = "USDT,TON"):
        base = "https://testnet-pay.crypt.bot/api" if testnet else "https://pay.crypt.bot/api"
        self.assets = assets
        self.http = httpx.AsyncClient(base_url=base, headers={"Crypto-Pay-API-Token": token}, timeout=20)

    async def close(self):
        await self.http.aclose()

    async def _call(self, method: str, **params):
        r = await self.http.post(f"/{method}", json=params)
        data = r.json()
        if not data.get("ok"):
            raise CryptoPayError(f"{method}: {data.get('error')}")
        return data["result"]

    async def create_invoice(self, rub: int, description: str, payload: str) -> dict:
        inv = await self._call(
            "createInvoice",
            currency_type="fiat", fiat="RUB", amount=str(rub),
            accepted_assets=self.assets, description=description[:1024],
            payload=payload, expires_in=3600, allow_anonymous=True,
        )
        inv["url"] = inv.get("bot_invoice_url") or inv.get("pay_url")
        return inv

    async def get_invoices(self, ids: list[str]) -> list[dict]:
        if not ids:
            return []
        res = await self._call("getInvoices", invoice_ids=",".join(ids))
        return res.get("items", [])
