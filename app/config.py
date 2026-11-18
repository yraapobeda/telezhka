"""Все настройки. Секреты — только в .env, в коде их нет."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _env(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    return v.strip() if v and v.strip() else default


def _req(name: str) -> str:
    v = _env(name)
    if not v:
        raise SystemExit(f"[config] не задана переменная {name} в .env")
    return v


def _bool(name: str, default: bool = False) -> bool:
    v = _env(name)
    return default if v is None else v.lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Location:
    key: str
    name: str
    flag: str
    panel_url: str      # https://host:port/секретный_путь  (без /panel в конце)
    api_token: str      # Настройки → Безопасность → API Token (scope admin)
    inbound_ids: tuple[int, ...]  # какие inbound'ы выдаём: "3" или "1,2,3"
    host: str           # адрес, который пойдёт в ключ (IP или домен сервера)
    verify_ssl: bool
    sub_url: str | None  # встроенный сервер подписок панели: https://host/путь  (без subId)

    @property
    def label(self) -> str:
        return f"{self.flag} {self.name}"


@dataclass(frozen=True)
class Plan:
    key: str
    title: str
    rub: int            # цена в рублях (крипта считается от неё)
    stars: int          # цена в Telegram Stars
    traffic_gb: int     # 0 = безлимит
    all_locations: bool = False
    once: bool = False  # можно купить только один раз (пробник)
    days: int = 30


# ── Локации ─────────────────────────────────────────────────────────────
# Новая страна = строка здесь + блок LOC_<KEY>_* в .env. Локация без
# LOC_<KEY>_PANEL_URL в .env просто не показывается.
LOCATION_META = [
    ("pl", "Польша", "🇵🇱"),
    ("de", "Германия", "🇩🇪"),
    ("nl", "Нидерланды", "🇳🇱"),
    ("fi", "Финляндия", "🇫🇮"),
    ("us", "США", "🇺🇸"),
]


def _load_locations() -> list[Location]:
    out = []
    for key, name, flag in LOCATION_META:
        p = f"LOC_{key.upper()}_"
        url = _env(p + "PANEL_URL")
        if not url:
            continue
        out.append(Location(
            key=key, name=name, flag=flag,
            panel_url=url.rstrip("/"),
            api_token=_req(p + "API_TOKEN"),
            inbound_ids=tuple(int(x) for x in _req(p + "INBOUND_ID").replace(" ", "").split(",") if x),
            host=_req(p + "HOST"),
            verify_ssl=_bool(p + "VERIFY_SSL", True),
            sub_url=(_env(p + "SUB_URL") or "").rstrip("/") or None,
        ))
    if not out:
        raise SystemExit("[config] не настроено ни одной локации (нужен хотя бы LOC_PL_PANEL_URL и т.д.)")
    return out


LOCATIONS = _load_locations()
LOC_BY_KEY = {l.key: l for l in LOCATIONS}

# ── Тарифы ──────────────────────────────────────────────────────────────
# Stars: 1⭐ для юзера стоит ~1.5–1.8₽, тебе приходит меньше — подбирай сам.
PLANS: dict[str, Plan] = {p.key: p for p in [
    Plan("trial", "Пробный · 10 ГБ",        49,  35, traffic_gb=10, once=True),
    Plan("std",   "1 страна · 100 ГБ",      99,  70, traffic_gb=100),
    Plan("unlim", "1 страна · безлимит",   199, 140, traffic_gb=0),
    Plan("all",   "Все страны · безлимит", 299, 210, traffic_gb=0, all_locations=True),
]}


def visible_plans(trial_used: bool) -> list[Plan]:
    res = []
    for p in PLANS.values():
        if p.all_locations and len(LOCATIONS) < 2:
            continue  # «все страны» прячем, пока страна одна
        if p.once and trial_used:
            continue
        res.append(p)
    return res


# ── Общие ───────────────────────────────────────────────────────────────
BOT_TOKEN = _req("BOT_TOKEN")
ADMIN_IDS = {int(x) for x in (_env("ADMIN_IDS", "") or "").replace(" ", "").split(",") if x}
BRAND = _env("BRAND", "TelezhkaProxy")
SUPPORT = _env("SUPPORT", "@TelezhkaProxy")
REQUIRED_CHANNEL = _env("REQUIRED_CHANNEL")          # @channel или пусто

CRYPTOPAY_TOKEN = _env("CRYPTOPAY_TOKEN")            # пусто = крипта выключена
CRYPTOPAY_TESTNET = _bool("CRYPTOPAY_TESTNET")
CRYPTOPAY_ASSETS = _env("CRYPTOPAY_ASSETS", "USDT,TON")

DEVICE_LIMIT = int(_env("DEVICE_LIMIT", "3"))        # limitIp в 3X-UI, 0 = без лимита
DB_PATH = _env("DB_PATH", "data/telezhka.db")

# ── Баланс / рефералка ──
REF_PERCENT = int(_env("REF_PERCENT", "10"))          # % с оплат приглашённого на баланс пригласившему
STARS_PER_100_RUB = int(_env("STARS_PER_100_RUB", "71"))  # курс для пополнения баланса звёздами
TOPUP_PRESETS = [int(x) for x in (_env("TOPUP_PRESETS", "100,200,300,500") or "").split(",") if x.strip()]
TOPUP_MIN, TOPUP_MAX = 50, 20000


def rub_to_stars(rub: int) -> int:
    return max(1, -(-rub * STARS_PER_100_RUB // 100))  # округление вверх


def discounted(price: int, pct: int) -> int:
    return price if not pct else max(1, price * (100 - pct) // 100)


SUB_PUBLIC_URL = (_env("SUB_PUBLIC_URL") or "").rstrip("/") or None  # https://sub.твойдомен
WEB_HOST = _env("WEB_HOST", "0.0.0.0")
WEB_PORT = int(_env("WEB_PORT", "0"))                # 0 = сервер подписок выключен
