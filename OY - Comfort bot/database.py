from __future__ import annotations

import aiosqlite
from config import DB_PATH


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id              INTEGER PRIMARY KEY,
                phone                    TEXT UNIQUE,
                name                     TEXT,
                language                 TEXT DEFAULT 'uz',
                moysklad_counterparty_id TEXT,
                balance_usd              REAL DEFAULT 0.0,
                balance_updated_at       TIMESTAMP,
                registered_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS orders (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                moysklad_id     TEXT UNIQUE,
                order_number    TEXT,
                telegram_id     INTEGER,
                customer_name   TEXT,
                customer_phone  TEXT,
                total_usd       REAL DEFAULT 0.0,
                balance_usd     REAL DEFAULT 0.0,
                status          TEXT,
                moment          TIMESTAMP,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS order_items (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER REFERENCES orders(id),
                name     TEXT,
                quantity REAL,
                price    REAL,
                total    REAL
            );

            CREATE TABLE IF NOT EXISTS shipments (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                moysklad_id       TEXT UNIQUE,
                shipment_number   TEXT,
                telegram_id       INTEGER,
                customer_name     TEXT,
                customer_phone    TEXT,
                total_usd         REAL DEFAULT 0.0,
                balance_before    REAL DEFAULT 0.0,
                balance_after     REAL DEFAULT 0.0,
                status            TEXT,
                moment            TIMESTAMP,
                seller_name       TEXT,
                created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS shipment_items (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                shipment_id INTEGER REFERENCES shipments(id),
                name        TEXT,
                quantity    REAL,
                price       REAL,
                total       REAL
            );

            CREATE TABLE IF NOT EXISTS returns (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                moysklad_id       TEXT UNIQUE,
                return_number     TEXT,
                telegram_id       INTEGER,
                customer_name     TEXT,
                customer_phone    TEXT,
                total_usd         REAL DEFAULT 0.0,
                balance_before    REAL DEFAULT 0.0,
                balance_after     REAL DEFAULT 0.0,
                status            TEXT,
                moment            TIMESTAMP,
                created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS return_items (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                return_id INTEGER REFERENCES returns(id),
                name      TEXT,
                quantity  REAL,
                price     REAL,
                total     REAL
            );
        """)
        # Миграции для уже существующих баз (каждая в отдельном try)
        for migration in [
            "ALTER TABLE users ADD COLUMN moysklad_counterparty_id TEXT",
            "ALTER TABLE users ADD COLUMN balance_usd REAL DEFAULT 0.0",
            "ALTER TABLE users ADD COLUMN balance_updated_at TIMESTAMP",
            "ALTER TABLE shipments ADD COLUMN seller_name TEXT",
            # FIX #2: webhook-fed report cache columns + indexes + bookkeeping
            "ALTER TABLE shipments ADD COLUMN moysklad_counterparty_id TEXT",
            "ALTER TABLE shipments ADD COLUMN total_original REAL DEFAULT 0.0",
            "ALTER TABLE shipments ADD COLUMN currency TEXT DEFAULT 'USD'",
            "ALTER TABLE shipment_items ADD COLUMN uom TEXT",
            "ALTER TABLE shipment_items ADD COLUMN price_original REAL DEFAULT 0.0",
            "ALTER TABLE shipment_items ADD COLUMN total_original REAL DEFAULT 0.0",
            "ALTER TABLE returns ADD COLUMN moysklad_counterparty_id TEXT",
            "ALTER TABLE returns ADD COLUMN total_original REAL DEFAULT 0.0",
            "ALTER TABLE returns ADD COLUMN currency TEXT DEFAULT 'USD'",
            "ALTER TABLE return_items ADD COLUMN uom TEXT",
            "ALTER TABLE return_items ADD COLUMN price_original REAL DEFAULT 0.0",
            "ALTER TABLE return_items ADD COLUMN total_original REAL DEFAULT 0.0",
            "CREATE INDEX IF NOT EXISTS idx_shipments_cp_moment ON shipments(moysklad_counterparty_id, moment DESC)",
            "CREATE INDEX IF NOT EXISTS idx_returns_cp_moment ON returns(moysklad_counterparty_id, moment DESC)",
            "CREATE TABLE IF NOT EXISTS report_backfill ("
            "counterparty_id TEXT PRIMARY KEY, "
            "backfilled_from TEXT, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        ]:
            try:
                await db.execute(migration)
                await db.commit()
            except Exception:
                pass  # Колонка уже существует

        await db.commit()


# ── Users ──────────────────────────────────────────────────────────────────

async def get_user(telegram_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_user_by_phone(phone: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE phone = ?", (normalize_phone(phone),)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def register_user(telegram_id: int, phone: str, name: str, language: str = "uz") -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        phone_norm = normalize_phone(phone)
        # Check if another telegram_id uses this phone
        async with db.execute("SELECT telegram_id FROM users WHERE phone = ?", (phone_norm,)) as cur:
            row = await cur.fetchone()
            if row and row[0] != telegram_id:
                await db.execute("DELETE FROM users WHERE phone = ?", (phone_norm,))

        await db.execute(
            """INSERT INTO users (telegram_id, phone, name, language)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(telegram_id) DO UPDATE SET
                 phone = excluded.phone,
                 name = excluded.name""",
            (telegram_id, phone_norm, name, language),
        )
        await db.commit()


async def save_moysklad_counterparty_id(telegram_id: int, counterparty_id: str) -> None:
    """Сохранить ID контрагента МойСклад для пользователя."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET moysklad_counterparty_id = ? WHERE telegram_id = ?",
            (counterparty_id, telegram_id),
        )
        await db.commit()


async def update_user_balance(telegram_id: int, balance: float) -> None:
    """
    Сохранить актуальный баланс пользователя (из МойСклад).
    Вызывается при каждом вебхуке: заказ, отгрузка, оплата.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE users
               SET balance_usd = ?, balance_updated_at = CURRENT_TIMESTAMP
               WHERE telegram_id = ?""",
            (balance, telegram_id),
        )
        await db.commit()


async def save_counterparty_and_balance(
    telegram_id: int,
    counterparty_id: str,
    balance: float,
) -> None:
    """Сохранить ID контрагента и баланс одним запросом."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE users
               SET moysklad_counterparty_id = ?,
                   balance_usd = ?,
                   balance_updated_at = CURRENT_TIMESTAMP
               WHERE telegram_id = ?""",
            (counterparty_id, balance, telegram_id),
        )
        await db.commit()


async def get_moysklad_counterparty_id(telegram_id: int) -> str | None:
    """Получить ID контрагента МойСклад для пользователя."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT moysklad_counterparty_id FROM users WHERE telegram_id = ?",
            (telegram_id,),
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row and row[0] else None


async def set_user_language(telegram_id: int, language: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET language = ? WHERE telegram_id = ?",
            (language, telegram_id),
        )
        await db.commit()


async def count_users_registered_between(utc_from: str, utc_to: str) -> int:
    """Сколько пользователей прошли /start в боте за интервал.

    `registered_at` хранится в UTC (CURRENT_TIMESTAMP). На вход — UTC-границы
    в формате 'YYYY-MM-DD HH:MM:SS' (см. daily_report.run_for_today).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM users WHERE registered_at BETWEEN ? AND ?",
            (utc_from, utc_to),
        ) as cur:
            row = await cur.fetchone()
            return int(row[0]) if row else 0


async def get_all_users() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users") as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


# ── Orders ─────────────────────────────────────────────────────────────────

async def save_order(
    moysklad_id: str,
    order_number: str,
    telegram_id: int | None,
    customer_name: str,
    customer_phone: str,
    total_usd: float,
    balance_usd: float,
    status: str,
    moment: str,
    items: list[dict],
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO orders
               (moysklad_id, order_number, telegram_id, customer_name, customer_phone,
                total_usd, balance_usd, status, moment)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(moysklad_id) DO UPDATE SET
                 status = excluded.status,
                 total_usd = excluded.total_usd,
                 balance_usd = excluded.balance_usd
               RETURNING id""",
            (moysklad_id, order_number, telegram_id, customer_name,
             normalize_phone(customer_phone), total_usd, balance_usd, status, moment),
        )
        row = await cur.fetchone()
        order_id = row[0]
        await db.execute("DELETE FROM order_items WHERE order_id = ?", (order_id,))
        for item in items:
            await db.execute(
                "INSERT INTO order_items (order_id, name, quantity, price, total) VALUES (?,?,?,?,?)",
                (order_id, item["name"], item["quantity"], item["price"], item["total"]),
            )
        await db.commit()
        return order_id


async def get_orders_for_user(telegram_id: int, limit: int = 20) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM orders WHERE telegram_id = ? ORDER BY moment DESC LIMIT ?",
            (telegram_id, limit),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def get_order_items(order_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM order_items WHERE order_id = ?", (order_id,)
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def get_orders_in_period(telegram_id: int, date_from: str, date_to: str) -> list[dict]:
    # moment хранится как '2026-04-14 18:26' — добавляем время конца дня,
    # иначе BETWEEN '2026-04-14' AND '2026-04-14' не найдёт '2026-04-14 18:26'
    date_to_end = date_to + " 23:59:59"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM orders
               WHERE telegram_id = ?
                 AND moment BETWEEN ? AND ?
               ORDER BY moment DESC""",
            (telegram_id, date_from, date_to_end),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


# ── Shipments ──────────────────────────────────────────────────────────────

async def save_shipment(
    moysklad_id: str,
    shipment_number: str,
    telegram_id: int | None,
    customer_name: str,
    customer_phone: str,
    total_usd: float,
    balance_before: float,
    balance_after: float,
    status: str,
    moment: str,
    items: list[dict],
    seller_name: str = "",
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO shipments
               (moysklad_id, shipment_number, telegram_id, customer_name, customer_phone,
                total_usd, balance_before, balance_after, status, moment, seller_name)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(moysklad_id) DO UPDATE SET
                 status = excluded.status,
                 seller_name = COALESCE(NULLIF(TRIM(excluded.seller_name), ''), shipments.seller_name)
               RETURNING id""",
            (moysklad_id, shipment_number, telegram_id, customer_name,
             normalize_phone(customer_phone), total_usd, balance_before, balance_after,
             status, moment, (seller_name or "").strip()),
        )
        row = await cur.fetchone()
        shipment_id = row[0]
        await db.execute("DELETE FROM shipment_items WHERE shipment_id = ?", (shipment_id,))
        for item in items:
            await db.execute(
                "INSERT INTO shipment_items (shipment_id, name, quantity, price, total) VALUES (?,?,?,?,?)",
                (shipment_id, item["name"], item["quantity"], item["price"], item["total"]),
            )
        await db.commit()
        return shipment_id


async def get_shipment_items(shipment_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM shipment_items WHERE shipment_id = ?", (shipment_id,)
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def get_shipments_for_user(telegram_id: int, limit: int = 20) -> list[dict]:
    """Otgruzkalar — faqat shu Telegram foydalanuvchisi (kontragent) bilan bog‘langan."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM shipments
               WHERE telegram_id = ?
               ORDER BY moment DESC
               LIMIT ?""",
            (telegram_id, limit),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def get_shipments_in_period(telegram_id: int, date_from: str, date_to: str) -> list[dict]:
    date_to_end = date_to + " 23:59:59"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM shipments
               WHERE telegram_id = ?
                 AND moment BETWEEN ? AND ?
               ORDER BY moment DESC""",
            (telegram_id, date_from, date_to_end),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


# ── Returns ───────────────────────────────────────────────────────────────

async def save_return(
    moysklad_id: str,
    return_number: str,
    telegram_id: int | None,
    customer_name: str,
    customer_phone: str,
    total_usd: float,
    balance_before: float,
    balance_after: float,
    status: str,
    moment: str,
    items: list[dict],
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO returns
               (moysklad_id, return_number, telegram_id, customer_name, customer_phone,
                total_usd, balance_before, balance_after, status, moment)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(moysklad_id) DO UPDATE SET status = excluded.status
               RETURNING id""",
            (moysklad_id, return_number, telegram_id, customer_name,
             normalize_phone(customer_phone), total_usd, balance_before, balance_after,
             status, moment),
        )
        row = await cur.fetchone()
        return_id = row[0]
        await db.execute("DELETE FROM return_items WHERE return_id = ?", (return_id,))
        for item in items:
            await db.execute(
                "INSERT INTO return_items (return_id, name, quantity, price, total) VALUES (?,?,?,?,?)",
                (return_id, item["name"], item["quantity"], item["price"], item["total"]),
            )
        await db.commit()
        return return_id


async def get_return_items(return_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM return_items WHERE return_id = ?", (return_id,)
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def get_returns_in_period(telegram_id: int, date_from: str, date_to: str) -> list[dict]:
    date_to_end = date_to + " 23:59:59"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM returns
               WHERE telegram_id = ?
                 AND moment BETWEEN ? AND ?
               ORDER BY moment DESC""",
            (telegram_id, date_from, date_to_end),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


# ── Report cache (FIX #2: webhook-fed, queried by counterparty) ──────────────

async def save_shipment_doc(
    moysklad_id: str,
    shipment_number: str,
    moysklad_counterparty_id: str,
    customer_name: str,
    customer_phone: str,
    total_usd: float,
    total_original: float,
    currency: str,
    balance_before: float,
    balance_after: float,
    status: str,
    moment: str,
    items: list[dict],
    seller_name: str = "",
) -> int:
    """Upsert a shipment (demand) document + its items into the report cache.

    Stores moysklad_counterparty_id / total_original / currency so reports can
    be served from SQLite instead of live MoySklad API calls.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO shipments
               (moysklad_id, shipment_number, telegram_id, customer_name, customer_phone,
                total_usd, balance_before, balance_after, status, moment, seller_name,
                moysklad_counterparty_id, total_original, currency)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(moysklad_id) DO UPDATE SET
                 status = excluded.status,
                 seller_name = COALESCE(NULLIF(TRIM(excluded.seller_name), ''), shipments.seller_name),
                 moysklad_counterparty_id = excluded.moysklad_counterparty_id,
                 total_usd = excluded.total_usd,
                 total_original = excluded.total_original,
                 currency = excluded.currency
               RETURNING id""",
            (moysklad_id, shipment_number, None, customer_name,
             normalize_phone(customer_phone), total_usd, balance_before, balance_after,
             status, moment, (seller_name or "").strip(),
             moysklad_counterparty_id, total_original, currency),
        )
        row = await cur.fetchone()
        shipment_id = row[0]
        await db.execute("DELETE FROM shipment_items WHERE shipment_id = ?", (shipment_id,))
        for item in items:
            await db.execute(
                """INSERT INTO shipment_items
                   (shipment_id, name, quantity, price, total, uom, price_original, total_original)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    shipment_id,
                    item.get("name"),
                    item.get("quantity"),
                    item.get("price"),
                    item.get("total"),
                    item.get("uom"),
                    item.get("price_original", 0.0),
                    item.get("total_original", 0.0),
                ),
            )
        await db.commit()
        return shipment_id


async def save_return_doc(
    moysklad_id: str,
    return_number: str,
    moysklad_counterparty_id: str,
    customer_name: str,
    customer_phone: str,
    total_usd: float,
    total_original: float,
    currency: str,
    balance_before: float,
    balance_after: float,
    status: str,
    moment: str,
    items: list[dict],
) -> int:
    """Upsert a salesreturn document + its items into the report cache."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO returns
               (moysklad_id, return_number, telegram_id, customer_name, customer_phone,
                total_usd, balance_before, balance_after, status, moment,
                moysklad_counterparty_id, total_original, currency)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(moysklad_id) DO UPDATE SET
                 status = excluded.status,
                 moysklad_counterparty_id = excluded.moysklad_counterparty_id,
                 total_usd = excluded.total_usd,
                 total_original = excluded.total_original,
                 currency = excluded.currency
               RETURNING id""",
            (moysklad_id, return_number, None, customer_name,
             normalize_phone(customer_phone), total_usd, balance_before, balance_after,
             status, moment,
             moysklad_counterparty_id, total_original, currency),
        )
        row = await cur.fetchone()
        return_id = row[0]
        await db.execute("DELETE FROM return_items WHERE return_id = ?", (return_id,))
        for item in items:
            await db.execute(
                """INSERT INTO return_items
                   (return_id, name, quantity, price, total, uom, price_original, total_original)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    return_id,
                    item.get("name"),
                    item.get("quantity"),
                    item.get("price"),
                    item.get("total"),
                    item.get("uom"),
                    item.get("price_original", 0.0),
                    item.get("total_original", 0.0),
                ),
            )
        await db.commit()
        return return_id


def _row_to_doc_items(item_rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    for r in item_rows:
        out.append({
            "name": r.get("name"),
            "code": "",
            "quantity": r.get("quantity") or 0,
            "uom": r.get("uom") or "",
            "price": r.get("price") or 0.0,
            "total": r.get("total") or 0.0,
            "price_original": r.get("price_original") or 0.0,
            "total_original": r.get("total_original") or 0.0,
        })
    return out


async def get_shipments_by_cp_period(
    moysklad_counterparty_id: str,
    date_lo: str,
    date_hi: str,
) -> list[dict]:
    """Read cached shipments for a counterparty whose date is in [date_lo, date_hi].

    date_lo / date_hi are 'YYYY-MM-DD' (inclusive). We compare on the date part
    of `moment` (substr 1..10) because stored moments may be 16-char
    'YYYY-MM-DD HH:MM' (no seconds when sec==0); a raw lexical BETWEEN against a
    padded '...00:00:00' lower bound would wrongly exclude a midnight document.
    Returns dicts shaped like parse_demand() output (subset used by reports).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM shipments
               WHERE moysklad_counterparty_id = ?
                 AND substr(moment, 1, 10) BETWEEN ? AND ?
               ORDER BY moment DESC""",
            (moysklad_counterparty_id, date_lo, date_hi),
        ) as cur:
            ship_rows = [dict(r) for r in await cur.fetchall()]

        results: list[dict] = []
        for s in ship_rows:
            async with db.execute(
                "SELECT * FROM shipment_items WHERE shipment_id = ?", (s["id"],)
            ) as icur:
                item_rows = [dict(r) for r in await icur.fetchall()]
            results.append({
                "id": s["id"],
                "moysklad_id": s.get("moysklad_id"),
                "shipment_number": s.get("shipment_number"),
                "moment": s.get("moment"),
                "status": s.get("status"),
                "seller_name": s.get("seller_name") or "",
                "total_usd": s.get("total_usd") or 0.0,
                "total_original": s.get("total_original") or 0.0,
                "currency": s.get("currency") or "USD",
                "items": _row_to_doc_items(item_rows),
            })
        return results


async def get_returns_by_cp_period(
    moysklad_counterparty_id: str,
    date_lo: str,
    date_hi: str,
) -> list[dict]:
    """Read cached salesreturns for a counterparty whose date is in [date_lo, date_hi].

    date_lo / date_hi are 'YYYY-MM-DD' (inclusive); compared on substr(moment,1,10)
    for the same midnight-boundary reason as get_shipments_by_cp_period.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM returns
               WHERE moysklad_counterparty_id = ?
                 AND substr(moment, 1, 10) BETWEEN ? AND ?
               ORDER BY moment DESC""",
            (moysklad_counterparty_id, date_lo, date_hi),
        ) as cur:
            ret_rows = [dict(r) for r in await cur.fetchall()]

        results: list[dict] = []
        for s in ret_rows:
            async with db.execute(
                "SELECT * FROM return_items WHERE return_id = ?", (s["id"],)
            ) as icur:
                item_rows = [dict(r) for r in await icur.fetchall()]
            results.append({
                "id": s["id"],
                "moysklad_id": s.get("moysklad_id"),
                "return_number": s.get("return_number"),
                "moment": s.get("moment"),
                "status": s.get("status"),
                "total_usd": s.get("total_usd") or 0.0,
                "total_original": s.get("total_original") or 0.0,
                "currency": s.get("currency") or "USD",
                "items": _row_to_doc_items(item_rows),
            })
        return results


async def delete_shipment_doc(moysklad_id: str) -> None:
    """Remove a cached shipment + its items (MoySklad DELETE webhook)."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id FROM shipments WHERE moysklad_id = ?", (moysklad_id,)
        ) as cur:
            row = await cur.fetchone()
        if row:
            await db.execute("DELETE FROM shipment_items WHERE shipment_id = ?", (row[0],))
            await db.execute("DELETE FROM shipments WHERE id = ?", (row[0],))
            await db.commit()


async def delete_return_doc(moysklad_id: str) -> None:
    """Remove a cached salesreturn + its items (MoySklad DELETE webhook)."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id FROM returns WHERE moysklad_id = ?", (moysklad_id,)
        ) as cur:
            row = await cur.fetchone()
        if row:
            await db.execute("DELETE FROM return_items WHERE return_id = ?", (row[0],))
            await db.execute("DELETE FROM returns WHERE id = ?", (row[0],))
            await db.commit()


async def get_report_backfill_status(
    moysklad_counterparty_id: str,
) -> str | None:
    """ISO date string (backfilled_from) if a record exists, else None."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT backfilled_from FROM report_backfill WHERE counterparty_id = ?",
            (moysklad_counterparty_id,),
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row and row[0] else None


async def set_report_backfill_status(
    moysklad_counterparty_id: str,
    backfilled_from: str,
) -> None:
    """Insert or update the backfill bookkeeping record for a counterparty."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO report_backfill (counterparty_id, backfilled_from, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(counterparty_id) DO UPDATE SET
                 backfilled_from = excluded.backfilled_from,
                 updated_at = CURRENT_TIMESTAMP""",
            (moysklad_counterparty_id, backfilled_from),
        )
        await db.commit()


# ── Helpers ────────────────────────────────────────────────────────────────

def normalize_phone(phone: str) -> str:
    """Strip all non-digits, ensure starts with country code."""
    digits = "".join(c for c in phone if c.isdigit())
    # If starts with 8 and length 11 → Russian format, replace with 7
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    # If 9 digits → Uzbek local, add 998
    if len(digits) == 9:
        digits = "998" + digits
    return digits
