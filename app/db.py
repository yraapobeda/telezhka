from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    id          INTEGER PRIMARY KEY,
    username    TEXT,
    created_at  INTEGER NOT NULL,
    trial_used  INTEGER NOT NULL DEFAULT 0,
    sub_token   TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS subscriptions(
    user_id     INTEGER PRIMARY KEY,
    plan_key    TEXT NOT NULL,
    locations   TEXT NOT NULL,
    uuid        TEXT NOT NULL,
    expires_at  INTEGER NOT NULL,
    notified    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS payments(
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    plan_key    TEXT NOT NULL,
    location    TEXT NOT NULL,
    method      TEXT NOT NULL,
    amount      INTEGER NOT NULL,
    currency    TEXT NOT NULL,
    external_id TEXT,
    status      TEXT NOT NULL,
    created_at  INTEGER NOT NULL,
    paid_at     INTEGER,
    UNIQUE(method, external_id)
);
CREATE TABLE IF NOT EXISTS promos(
    code        TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,          -- sale (%), money (₽ на баланс), days (дни подписки)
    value       INTEGER NOT NULL,
    max_uses    INTEGER NOT NULL DEFAULT 0,   -- 0 = без лимита
    uses        INTEGER NOT NULL DEFAULT 0,
    expires_at  INTEGER NOT NULL DEFAULT 0,   -- 0 = бессрочно
    created_at  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS promo_uses(
    code        TEXT NOT NULL,
    user_id     INTEGER NOT NULL,
    used_at     INTEGER NOT NULL,
    PRIMARY KEY(code, user_id)
);
"""

# Колонки, добавленные после первой версии (ALTER для старых баз)
MIGRATIONS = [
    ("users", "balance", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "referrer_id", "INTEGER"),
    ("users", "discount", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "discount_code", "TEXT"),
    ("users", "ref_earned", "INTEGER NOT NULL DEFAULT 0"),
    ("payments", "rub", "INTEGER NOT NULL DEFAULT 0"),
]


@dataclass
class Sub:
    user_id: int
    plan_key: str
    locations: list[str]
    uuid: str
    expires_at: int
    notified: int

    @property
    def active(self) -> bool:
        return self.expires_at > time.time()


def _sub(row) -> Sub | None:
    if not row:
        return None
    return Sub(row["user_id"], row["plan_key"], row["locations"].split(","),
               row["uuid"], row["expires_at"], row["notified"])


class DB:
    def __init__(self, conn: aiosqlite.Connection):
        self.c = conn

    @classmethod
    async def open(cls, path: str) -> "DB":
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        conn = await aiosqlite.connect(path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.executescript(SCHEMA)
        for table, col, decl in MIGRATIONS:
            async with conn.execute(f"PRAGMA table_info({table})") as cur:
                cols = {r[1] for r in await cur.fetchall()}
            if col not in cols:
                await conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        await conn.commit()
        return cls(conn)

    async def close(self):
        await self.c.close()

    async def _one(self, sql, args=()):
        async with self.c.execute(sql, args) as cur:
            return await cur.fetchone()

    async def _all(self, sql, args=()):
        async with self.c.execute(sql, args) as cur:
            return await cur.fetchall()

    async def _exec(self, sql, args=()) -> int:
        cur = await self.c.execute(sql, args)
        await self.c.commit()
        return cur.rowcount

    # ── users ──
    async def ensure_user(self, uid: int, username: str | None, referrer_id: int | None = None) -> bool:
        """Создаёт юзера, если его нет. True — если юзер новый."""
        n = await self._exec(
            "INSERT OR IGNORE INTO users(id, username, created_at, sub_token, referrer_id) VALUES(?,?,?,?,?)",
            (uid, username, int(time.time()), secrets.token_urlsafe(18),
             referrer_id if referrer_id and referrer_id != uid else None))
        if not n and username:
            await self._exec("UPDATE users SET username=? WHERE id=? AND IFNULL(username,'')<>?",
                             (username, uid, username))
        return n == 1

    async def get_user(self, uid: int):
        return await self._one("SELECT * FROM users WHERE id=?", (uid,))

    async def user_by_token(self, token: str):
        return await self._one("SELECT * FROM users WHERE sub_token=?", (token,))

    async def set_trial_used(self, uid: int):
        await self._exec("UPDATE users SET trial_used=1 WHERE id=?", (uid,))

    async def all_user_ids(self) -> list[int]:
        return [r[0] for r in await self._all("SELECT id FROM users")]

    async def count_users(self) -> int:
        return (await self._one("SELECT COUNT(*) FROM users"))[0]

    async def ref_stats(self, uid: int) -> tuple[int, int]:
        n = (await self._one("SELECT COUNT(*) FROM users WHERE referrer_id=?", (uid,)))[0]
        earned = (await self._one("SELECT ref_earned FROM users WHERE id=?", (uid,)))[0]
        return n, earned

    # ── баланс ──
    async def add_balance(self, uid: int, rub: int):
        await self._exec("UPDATE users SET balance=MAX(0, balance+?) WHERE id=?", (rub, uid))

    async def take_balance(self, uid: int, rub: int) -> bool:
        """Атомарно списывает, только если денег хватает."""
        return await self._exec("UPDATE users SET balance=balance-? WHERE id=? AND balance>=?",
                                (rub, uid, rub)) == 1

    async def add_ref_earned(self, uid: int, rub: int):
        await self._exec("UPDATE users SET balance=balance+?, ref_earned=ref_earned+? WHERE id=?", (rub, rub, uid))

    # ── скидка из промокода ──
    async def set_discount(self, uid: int, pct: int, code: str | None):
        await self._exec("UPDATE users SET discount=?, discount_code=? WHERE id=?", (pct, code, uid))

    # ── промокоды ──
    async def create_promo(self, code, kind, value, max_uses, expires_at) -> bool:
        try:
            await self._exec("INSERT INTO promos(code, kind, value, max_uses, expires_at, created_at) "
                             "VALUES(?,?,?,?,?,?)", (code, kind, value, max_uses, expires_at, int(time.time())))
            return True
        except aiosqlite.IntegrityError:
            return False

    async def get_promo(self, code: str):
        return await self._one("SELECT * FROM promos WHERE code=?", (code,))

    async def list_promos(self):
        return await self._all("SELECT * FROM promos ORDER BY created_at DESC LIMIT 50")

    async def delete_promo(self, code: str) -> bool:
        return await self._exec("DELETE FROM promos WHERE code=?", (code,)) == 1

    async def use_promo(self, code: str, uid: int) -> str | None:
        """Занимает активацию. None — успех, иначе текст ошибки."""
        p = await self.get_promo(code)
        now = int(time.time())
        if not p:
            return "Такого промокода нет"
        if p["expires_at"] and p["expires_at"] < now:
            return "Промокод истёк"
        try:
            await self.c.execute("INSERT INTO promo_uses(code, user_id, used_at) VALUES(?,?,?)", (code, uid, now))
        except aiosqlite.IntegrityError:
            return "Ты уже активировал этот промокод"
        cur = await self.c.execute(
            "UPDATE promos SET uses=uses+1 WHERE code=? AND (max_uses=0 OR uses<max_uses)", (code,))
        if cur.rowcount != 1:
            await self.c.rollback()
            return "Активации этого промокода закончились"
        await self.c.commit()
        return None

    async def unuse_promo(self, code: str, uid: int):
        """Откат активации, если применить промокод не вышло."""
        n = await self._exec("DELETE FROM promo_uses WHERE code=? AND user_id=?", (code, uid))
        if n:
            await self._exec("UPDATE promos SET uses=MAX(0, uses-1) WHERE code=?", (code,))

    # ── subscriptions ──
    async def get_sub(self, uid: int) -> Sub | None:
        return _sub(await self._one("SELECT * FROM subscriptions WHERE user_id=?", (uid,)))

    async def save_sub(self, uid: int, plan_key: str, locations: list[str], uuid: str, expires_at: int):
        await self._exec(
            "INSERT OR REPLACE INTO subscriptions(user_id, plan_key, locations, uuid, expires_at, notified) "
            "VALUES(?,?,?,?,?,0)", (uid, plan_key, ",".join(locations), uuid, expires_at))

    async def set_notified(self, uid: int, val: int):
        await self._exec("UPDATE subscriptions SET notified=? WHERE user_id=?", (val, uid))

    async def delete_sub(self, uid: int):
        await self._exec("DELETE FROM subscriptions WHERE user_id=?", (uid,))

    async def subs_expiring(self, now: int, within: int) -> list[Sub]:
        rows = await self._all(
            "SELECT * FROM subscriptions WHERE notified=0 AND expires_at>? AND expires_at<=?",
            (now, now + within))
        return [_sub(r) for r in rows]

    async def subs_just_expired(self, now: int) -> list[Sub]:
        rows = await self._all("SELECT * FROM subscriptions WHERE notified<2 AND expires_at<=?", (now,))
        return [_sub(r) for r in rows]

    async def subs_expired_before(self, ts: int) -> list[Sub]:
        return [_sub(r) for r in await self._all("SELECT * FROM subscriptions WHERE expires_at<?", (ts,))]

    async def count_active_subs(self) -> int:
        return (await self._one("SELECT COUNT(*) FROM subscriptions WHERE expires_at>?", (int(time.time()),)))[0]

    # ── payments ──
    async def create_payment(self, uid, plan_key, location, method, amount, currency,
                             external_id=None, status="pending", rub=0) -> int:
        """plan_key='topup' — пополнение баланса. rub — сумма в рублях (для рефералки)."""
        now = int(time.time())
        cur = await self.c.execute(
            "INSERT INTO payments(user_id, plan_key, location, method, amount, currency, external_id, status, "
            "created_at, rub, paid_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (uid, plan_key, location, method, amount, currency, external_id, status, now, rub,
             now if status == "paid" else None))
        await self.c.commit()
        return cur.lastrowid

    async def set_external(self, pid: int, external_id: str):
        await self._exec("UPDATE payments SET external_id=? WHERE id=?", (external_id, pid))

    async def get_payment(self, pid: int):
        return await self._one("SELECT * FROM payments WHERE id=?", (pid,))

    async def mark_paid(self, pid: int) -> bool:
        """True только у первого, кто перевёл pending → paid (защита от двойной выдачи)."""
        n = await self._exec("UPDATE payments SET status='paid', paid_at=? WHERE id=? AND status='pending'",
                             (int(time.time()), pid))
        return n == 1

    async def mark_status(self, pid: int, status: str):
        await self._exec("UPDATE payments SET status=? WHERE id=? AND status='pending'", (status, pid))

    async def pending_crypto(self):
        return await self._all(
            "SELECT * FROM payments WHERE method='crypto' AND status='pending' AND external_id IS NOT NULL")

    async def revenue(self, since: int):
        return await self._all(
            "SELECT currency, COUNT(*) n, SUM(amount) s FROM payments WHERE status='paid' AND paid_at>=? "
            "GROUP BY currency", (since,))
