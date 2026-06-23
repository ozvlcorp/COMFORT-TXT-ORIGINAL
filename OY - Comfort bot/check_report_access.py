"""Диагностика доступа токена к данным дневного отчёта (20:00).

Запуск на сервере, где есть .env с MOYSKLAD_TOKEN:

    python check_report_access.py

Отвечает на вопрос «почему в отчёте 20:00 какие-то строки = 0 / н/д»:
проверяет доступ токена к каждому типу документа из отчёта и считает
сегодняшние count/сумму ровно так, как это делает daily_report.

Типичная причина нулей — у роли пользователя токена не включены целые
разделы МойСклад (Деньги, Закупки), и API отдаёт 403 на эти типы, а
остальные (Продажи) работают.
"""
import asyncio

import httpx

import moysklad_api as ms
from daily_report import _today_bounds

# (entity, строка в отчёте, раздел прав в МойСклад)
ENTITIES = [
    ("customerorder", "Заказы",             "Продажи"),
    ("demand",        "Отгрузки",           "Продажи"),
    ("paymentin",     "Приход · Безнал",    "Деньги"),
    ("cashin",        "Приход · Наличные",  "Деньги"),
    ("paymentout",    "Расход · Безнал",    "Деньги"),
    ("cashout",       "Расход · Наличные",  "Деньги"),
    ("supply",        "Поставки (приёмка)", "Закупки"),
]


async def _probe(entity: str):
    """None — доступ есть; иначе HTTP-код (напр. 403) или текст ошибки."""
    url = f"{ms.MOYSKLAD_API}/entity/{entity}"
    try:
        await ms._get(url, params={"limit": 1})
        return None
    except httpx.HTTPStatusError as e:
        return e.response.status_code
    except Exception as e:  # noqa: BLE001 — диагностика, печатаем как есть
        return repr(e)


async def main() -> None:
    msk_from, msk_to, _uf, _ut, date_label = _today_bounds()
    print(f"Окно отчёта (МСК): {msk_from} … {msk_to}  (день {date_label})\n")

    denied_sections: set[str] = set()
    print(f"{'Строка отчёта':<22}{'Раздел':<10}{'Доступ':<10}Сегодня")
    print("-" * 64)
    for entity, label, section in ENTITIES:
        status = await _probe(entity)
        if status is None:
            count, total = await ms.aggregate_documents(
                entity, moment_from_msk=msk_from, moment_to_msk=msk_to
            )
            access = "✓"
            today = f"{count} шт. — ${total:,.2f}"
        else:
            access = f"✗ {status}"
            today = "н/д"
            if status == 403:
                denied_sections.add(section)
        print(f"{label:<22}{section:<10}{access:<10}{today}")

    if denied_sections:
        print("\n⚠️  Нет доступа (403) к разделам: " + ", ".join(sorted(denied_sections)))
        print("    Это и есть причина нулей/«н/д» в отчёте. Исправить в МойСклад:")
        print("    Настройки → Сотрудники → роль пользователя токена → включить")
        print("    просмотр для разделов:")
        for s in sorted(denied_sections):
            print(f"      • {s}")
        print("    После этого строки отчёта покажут реальные цифры.")
    else:
        print("\n✓ Доступ есть ко всем типам. Если строка = 0 — значит за сегодня")
        print("  таких документов действительно не было (проверьте дату/время")
        print("  и часовой пояс: окно строится по Asia/Tashkent → переводится в МСК).")


if __name__ == "__main__":
    asyncio.run(main())
