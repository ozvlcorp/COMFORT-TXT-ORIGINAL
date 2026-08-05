"""Кто нагрузил API МойСклада — разбор логов бота.

Бот логирует каждый HTTP-запрос к МойСкладу строкой httpx, а в URL видно, за
кого именно шёл запрос: телефон (поиск контрагента) или UUID контрагента
(баланс, отгрузки, возвраты). Скрипт собирает это из логов, считает, кто
сгенерировал больше всего запросов, и связывает с пользователями из БД.

Использование:

    # из файла с выгруженными логами
    python analyze_api_usage.py bot.log

    # или потоком (например, прямо из docker logs)
    docker logs <container> 2>&1 | python analyze_api_usage.py

    # только нужное окно времени (бан выдан в 18:39 — смотрим час до него)
    python analyze_api_usage.py bot.log --since "2026-08-05 17:39" --until "2026-08-05 18:39"

Что показывает:
  • ТОП пользователей по числу запросов (кто «спамил кнопками»);
  • распределение запросов по типам эндпоинтов;
  • сколько ответов завершились ошибкой 429/403 — именно за накопление
    ошибок МойСклад и отключает доступ к API.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from collections import Counter, defaultdict

try:
    import aiosqlite
except ImportError:  # разбор логов работает и без БД
    aiosqlite = None  # type: ignore[assignment]

DB_PATH = os.getenv("DB_PATH", "comfort_bot.db")

# 2026-08-05 13:51:05,235 [INFO] httpx: HTTP Request: GET <url> "HTTP/1.1 403 Forbidden"
LOG_LINE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})"
    r".*?HTTP Request:\s+(?P<method>[A-Z]+)\s+(?P<url>\S+)"
    r'(?:.*?"HTTP/[\d.]+\s+(?P<status>\d{3}))?'
)
UUID_RE = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
COUNTERPARTY_ID = re.compile(rf"counterparty[/%]{{0,3}}({UUID_RE})")
AGENT_FILTER = re.compile(rf"agent(?:%3D|=)[^&]*?({UUID_RE})")
SEARCH_PHONE = re.compile(r"[?&]search=([%2B+\d]{6,})")


def _endpoint(url: str) -> str:
    """Короткое имя эндпоинта для сводки."""
    m = re.search(r"/remap/1\.2/(report/[a-z/]+|entity/[a-z]+)", url)
    return m.group(1) if m else "other"


def _norm_phone(raw: str) -> str:
    digits = "".join(c for c in raw.replace("%2B", "+") if c.isdigit())
    return digits[-9:] if len(digits) >= 9 else digits


def parse(stream, since: str | None, until: str | None):
    per_phone: Counter[str] = Counter()
    per_cp: Counter[str] = Counter()
    per_endpoint: Counter[str] = Counter()
    per_status: Counter[str] = Counter()
    per_minute: Counter[str] = Counter()
    errors_per_minute: Counter[str] = Counter()
    total = 0

    for line in stream:
        m = LOG_LINE.search(line)
        if not m:
            continue
        ts = m.group("ts").replace("T", " ")
        if since and ts < since:
            continue
        if until and ts > until:
            continue

        url = m.group("url")
        status = m.group("status") or "?"
        total += 1
        minute = ts[:16]
        per_minute[minute] += 1
        per_endpoint[_endpoint(url)] += 1
        per_status[status] += 1
        if status in ("429", "403"):
            errors_per_minute[minute] += 1

        if phone := SEARCH_PHONE.search(url):
            per_phone[_norm_phone(phone.group(1))] += 1
        for rx in (AGENT_FILTER, COUNTERPARTY_ID):
            if cp := rx.search(url):
                per_cp[cp.group(1)] += 1
                break

    return {
        "total": total,
        "per_phone": per_phone,
        "per_cp": per_cp,
        "per_endpoint": per_endpoint,
        "per_status": per_status,
        "per_minute": per_minute,
        "errors_per_minute": errors_per_minute,
    }


async def resolve_users(phones: list[str], cp_ids: list[str]) -> dict[str, str]:
    """Сопоставляет телефоны и ID контрагентов с пользователями бота."""
    if aiosqlite is None or not os.path.exists(DB_PATH):
        return {}
    out: dict[str, str] = {}
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT telegram_id, phone, name, moysklad_counterparty_id FROM users"
        ) as cur:
            async for row in cur:
                label = f"{row['name'] or '—'} (tg={row['telegram_id']}, {row['phone'] or '—'})"
                if row["phone"]:
                    out[_norm_phone(row["phone"])] = label
                if row["moysklad_counterparty_id"]:
                    out[row["moysklad_counterparty_id"]] = label
    return out


def _print_top(title: str, counter: Counter[str], users: dict[str, str], limit: int) -> None:
    if not counter:
        return
    print(f"\n{title}")
    for key, count in counter.most_common(limit):
        who = users.get(key, "не найден в БД")
        print(f"  {count:6d}  {key:38s}  {who}")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("logfile", nargs="?", help="файл с логами (по умолчанию — stdin)")
    ap.add_argument("--since", help='начало окна, "YYYY-MM-DD HH:MM"')
    ap.add_argument("--until", help='конец окна, "YYYY-MM-DD HH:MM"')
    ap.add_argument("--top", type=int, default=15, help="сколько строк в топе (по умолчанию 15)")
    args = ap.parse_args()

    stream = open(args.logfile, encoding="utf-8", errors="replace") if args.logfile else sys.stdin
    try:
        stats = parse(stream, args.since, args.until)
    finally:
        if args.logfile:
            stream.close()

    if not stats["total"]:
        print("В логах не найдено ни одного запроса к МойСкладу.")
        print("Проверьте окно времени (--since/--until) и что переданы логи сервиса bot.")
        return

    print(f"Всего запросов к МойСкладу: {stats['total']}")

    print("\nОтветы по кодам:")
    for status, count in stats["per_status"].most_common():
        mark = "  ← ошибки, за их накопление МойСклад и отключает API" if status in ("429", "403") else ""
        print(f"  {count:6d}  HTTP {status}{mark}")

    print("\nСамые нагруженные минуты (лимит МойСклада — 200 ошибок в минуту):")
    for minute, count in stats["per_minute"].most_common(10):
        errs = stats["errors_per_minute"].get(minute, 0)
        flag = "  ⚠ ПРЕВЫШЕН порог ошибок" if errs >= 200 else ""
        print(f"  {minute}  запросов: {count:5d}  из них ошибок: {errs:5d}{flag}")

    users = await resolve_users(list(stats["per_phone"]), list(stats["per_cp"]))
    _print_top("ТОП по телефону (поиск контрагента — жмут «Баланс»):", stats["per_phone"], users, args.top)
    _print_top("ТОП по контрагенту (баланс/отгрузки/отчёты):", stats["per_cp"], users, args.top)

    print("\nПо эндпоинтам:")
    for ep, count in stats["per_endpoint"].most_common(args.top):
        print(f"  {count:6d}  {ep}")


if __name__ == "__main__":
    asyncio.run(main())
