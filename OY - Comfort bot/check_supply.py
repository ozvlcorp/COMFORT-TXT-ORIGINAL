"""Диагностика приёмок (supply) для дневного отчёта.

Запуск на сервере, где есть .env с MOYSKLAD_TOKEN:

    python check_supply.py

Скрипт отвечает на вопрос «почему в отчёте 20:00 приёмка = 0, хотя приёмка была»:
  1) есть ли у API-токена доступ к /entity/supply (или это 403 — нет прав);
  2) сколько приёмок и на какую сумму попадает в окно «сегодня» — ровно так,
     как считает дневной отчёт (daily_report).
"""
import asyncio

import httpx

import moysklad_api as ms
from daily_report import _today_bounds


async def main() -> None:
    msk_from, msk_to, _utc_from, _utc_to, date_label = _today_bounds()
    print(f"Окно отчёта (МСК): {msk_from} … {msk_to}  (день {date_label})\n")

    url = f"{ms.MOYSKLAD_API}/entity/supply"

    # 1) Прямой запрос — увидеть HTTP-статус (403 = нет прав на «Закупки/Приёмки»).
    print("1) Доступ к /entity/supply:")
    try:
        data = await ms._get(url, params={"limit": 1})
        size = (data.get("meta") or {}).get("size")
        print(f"   ✓ доступ есть, всего приёмок в аккаунте: {size}")
    except httpx.HTTPStatusError as e:
        print(f"   ✗ HTTP {e.response.status_code}: {e.response.text[:200]}")
        if e.response.status_code == 403:
            print("   → У токена НЕТ прав на раздел «Закупки/Приёмки» — это и есть")
            print("     причина нулей в отчёте. Исправить в МойСклад:")
            print("     Настройки → Сотрудники → роль пользователя токена →")
            print("     «Закупки → Приёмки → Просмотр».")
        return
    except Exception as e:  # noqa: BLE001 — диагностика, печатаем как есть
        print(f"   ✗ ошибка: {e!r}")
        return

    # 2) Агрегация за сегодня — ровно как в дневном отчёте.
    print("\n2) Приёмки за сегодня (как в отчёте 20:00):")
    count, total = await ms.aggregate_documents(
        "supply", moment_from_msk=msk_from, moment_to_msk=msk_to
    )
    print(f"   📦 Поставки: {count} шт. — ${total:,.2f}")
    if count == 0:
        print("   (0 — приёмок в сегодняшнем окне нет; проверьте дату/время приёмки")
        print("    и часовой пояс: окно строится по Asia/Tashkent и переводится в МСК.)")


if __name__ == "__main__":
    asyncio.run(main())
