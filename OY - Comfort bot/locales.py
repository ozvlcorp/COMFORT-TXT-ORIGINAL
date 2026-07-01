"""All bot text strings in Uzbek (uz) and Russian (ru)."""

STRINGS: dict[str, dict[str, str]] = {
    # ── Registration ───────────────────────────────────────────────────────
    "welcome_new": {
        "uz": (
            "👋 Comfort Textile botiga xush kelibsiz!\n\n"
            "Ro'yxatdan o'tish uchun telefon raqamingizni ulashing."
        ),
        "ru": (
            "👋 Добро пожаловать в бот Comfort Textile!\n\n"
            "Поделитесь номером телефона для регистрации."
        ),
    },
    "share_phone_btn": {
        "uz": "📱 Telefon raqamni ulashish",
        "ru": "📱 Поделиться номером",
    },
    "already_registered": {
        "uz": "✅ Siz allaqachon ro'yxatdan o'tgansiz!",
        "ru": "✅ Вы уже зарегистрированы!",
    },
    "registered_success": {
        "uz": "✅ Ro'yxatdan o'tish muvaffaqiyatli yakunlandi!",
        "ru": "✅ Регистрация успешно завершена!",
    },

    # ── Main menu ──────────────────────────────────────────────────────────
    "main_menu": {
        "uz": "📋 Asosiy menyu:",
        "ru": "📋 Главное меню:",
    },
    "btn_orders": {
        "uz": "🛒 Buyurtmalar",
        "ru": "🛒 Заказы",
    },
    "btn_balance": {
        "uz": "💰 Balans",
        "ru": "💰 Баланс",
    },
    "btn_report": {
        "uz": "📊 Hisobot",
        "ru": "📊 Отчёт",
    },
    "btn_language": {
        "uz": "🌐 Til",
        "ru": "🌐 Язык",
    },

    # ── Balance ────────────────────────────────────────────────────────────
    "balance": {
        "uz": "💰 <b>Joriy balans:</b> <b>{amount} USD</b>",
        "ru": "💰 <b>Текущий баланс:</b> <b>{amount} USD</b>",
    },

    # ── Orders ─────────────────────────────────────────────────────────────
    "no_orders": {
        "uz": "📭 Buyurtmalar topilmadi.",
        "ru": "📭 Заказы не найдены.",
    },
    "no_shipments": {
        "uz": "📭 Otgruzkalar topilmadi.",
        "ru": "📭 Отгрузки не найдены.",
    },
    "no_counterparty_for_list": {
        "uz": "❌ MoySklad da kontragent bog‘lanmagan. /start yoki 💰 Balans orqali telefonni tekshiring.",
        "ru": "❌ Нет привязки к контрагенту в МойСклад. Проверьте телефон через /start или 💰 Баланс.",
    },
    "orders_ms_error": {
        "uz": "⚠️ MoySklad dan ma'lumot olinmadi (tarmoq yoki vaqt tugashi). Keyinroq qayta urinib ko‘ring.",
        "ru": "⚠️ Не удалось получить данные из МойСклад (сеть или таймаут). Попробуйте позже.",
    },
    "orders_header": {
        "uz": "🛒 Sizning buyurtmalaringiz:",
        "ru": "🛒 Ваши заказы:",
    },
    "shipment_list_header": {
        "uz": "📦 <b>Sizning otgruzkalaringiz</b> (MoySklad, jonli):",
        "ru": "📦 <b>Ваши отгрузки</b> (МойСклад, онлайн):",
    },
    "order_item": {
        "uz": "📋 #{number}  •  🗓 {date}\n💰 {total} USD  •  {status}",
        "ru": "📋 #{number}  •  🗓 {date}\n💰 {total} USD  •  {status}",
    },
    "shipment_list_item": {
        "uz": "📋 <b>#{number}</b>  •  🗓 <b>{date}</b>\n🛍 <b>Sotuvchi:</b> {seller}\n💰 <b>{total} USD</b>  •  {status}",
        "ru": "📋 <b>#{number}</b>  •  🗓 <b>{date}</b>\n🛍 <b>Продавец:</b> {seller}\n💰 <b>{total} USD</b>  •  {status}",
    },

    # ── Report ─────────────────────────────────────────────────────────────
    "report_choose_period": {
        "uz": "📊 Hisobot davrini tanlang:",
        "ru": "📊 Выберите период отчёта:",
    },
    "btn_daily": {"uz": "📅 Kunlik", "ru": "📅 Дневной"},
    "btn_weekly": {"uz": "📅 Haftalik", "ru": "📅 Недельный"},
    "btn_monthly": {"uz": "📅 Oylik", "ru": "📅 Месячный"},
    "btn_quarterly": {"uz": "📅 Chorak", "ru": "📅 Квартал"},
    "btn_yearly": {"uz": "📅 Yillik", "ru": "📅 Годовой"},
    "btn_all": {"uz": "📋 Barcha", "ru": "📋 Все"},
    "btn_prev": {"uz": "◀️ O'tkan", "ru": "◀️ Предыдущий"},
    "btn_current": {"uz": "🔄 Hozir", "ru": "🔄 Текущий"},
    "btn_next": {"uz": "▶️ Keyingi", "ru": "▶️ Следующий"},
    "btn_back": {"uz": "🔙 Orqaga", "ru": "🔙 Назад"},

    "report_empty": {
        "uz": "📭 Bu davrda ma'lumotlar topilmadi.",
        "ru": "📭 За этот период данных не найдено.",
    },
    "report_result": {
        "uz": (
            "📊 Hisobot: {period_label}\n"
            "📅 {date_from} — {date_to}\n\n"
            "📦 Otgruzkalar: {ship_count} ta — {ship_total} USD\n"
            "🔄 Qaytarishlar: {ret_count} ta — {ret_total} USD"
            "{items}\n\n"
            "💰 Jami (otgruzka − qaytarish): {total} USD"
        ),
        "ru": (
            "📊 Отчёт: {period_label}\n"
            "📅 {date_from} — {date_to}\n\n"
            "📦 Отгрузки: {ship_count} шт — {ship_total} USD\n"
            "🔄 Возвраты: {ret_count} шт — {ret_total} USD"
            "{items}\n\n"
            "💰 Итого (отгрузки − возвраты): {total} USD"
        ),
    },

    # ── Language ───────────────────────────────────────────────────────────
    "choose_language": {
        "uz": "🌐 Tilni tanlang / Выберите язык:",
        "ru": "🌐 Tilni tanlang / Выберите язык:",
    },
    "language_set": {
        "uz": "✅ Til o'zgartirildi: O'zbek",
        "ru": "✅ Язык изменён: Русский",
    },

    # ── MoySklad notifications ─────────────────────────────────────────────
    "order_notification": {
        "uz": (
            "✅ <b>Buyurtma qabul qilindi!</b>\n\n"
            "<b>📋</b> #{number}  •  <b>🗓 Sana va vaqt:</b> {date}\n\n"
            "<b>🙍 Mijoz:</b> {name}\n"
            "<b>📞 Telefon:</b> {phone}\n\n"
            "<b>📦 Mahsulotlar:</b>\n"
            "{items}\n"
            "<b>👉 Jami:</b> <b>{total} USD</b>\n\n"
            "<b>Joriy balans:</b> <b>{balance} USD</b>"
        ),
        "ru": (
            "✅ <b>Заказ принят!</b>\n\n"
            "<b>📋</b> #{number}  •  <b>🗓 Дата и время:</b> {date}\n\n"
            "<b>🙍 Клиент:</b> {name}\n"
            "<b>📞 Телефон:</b> {phone}\n\n"
            "<b>📦 Товары:</b>\n"
            "{items}\n"
            "<b>👉 Итого:</b> <b>{total} USD</b>\n\n"
            "<b>Текущий баланс:</b> <b>{balance} USD</b>"
        ),
    },
    "shipment_notification": {
        "uz": (
            "🛒 <b>Yangi sotuv!</b> #{number}\n"
            "🗓 <b>{date}</b>\n"
            "🙋‍♂️ <b>Yaratdi:</b> {created_by}\n"
            "🧑‍💻 <b>Javobgar:</b> {responsible}\n"
            "🛍 <b>Sotuvchi:</b> {seller}\n"
            "🏠 <b>Ombor:</b> {warehouse}\n\n"
            "🙍 <b>Mijoz:</b> {name}\n"
            "📞 <b>Telefon:</b> {phone}\n\n"
            "<b>📦 Mahsulotlar:</b>\n"
            "{items}\n\n"
            "👉 <b>Jami:</b> <b>{total} USD</b>\n"
            "💰 <b>Joriy balans:</b> <b>{balance} USD</b>"
        ),
        "ru": (
            "🛒 <b>Новая продажа!</b> #{number}\n"
            "🗓 <b>{date}</b>\n"
            "🙋‍♂️ <b>Создал:</b> {created_by}\n"
            "🧑‍💻 <b>Ответственный:</b> {responsible}\n"
            "🛍 <b>Продавец:</b> {seller}\n"
            "🏠 <b>Склад:</b> {warehouse}\n\n"
            "🙍 <b>Клиент:</b> {name}\n"
            "📞 <b>Телефон:</b> {phone}\n\n"
            "<b>📦 Товары:</b>\n"
            "{items}\n\n"
            "👉 <b>Итого:</b> <b>{total} USD</b>\n"
            "💰 <b>Текущий баланс:</b> <b>{balance} USD</b>"
        ),
    },
    "payment_notification": {
        "uz": (
            "💰 <b>To'lov qabul qilindi!</b>\n\n"
            "<b>📋 Hujjat:</b> #{number}\n"
            "<b>🗓 Sana va vaqt:</b> {date}\n\n"
            "<b>💵 Miqdor:</b> <b>{amount} {currency}</b>\n"
            "<b>🔖 Usul:</b> {method}\n\n"
            "<b>Joriy balans:</b> <b>{balance} USD</b>"
        ),
        "ru": (
            "💰 <b>Оплата получена!</b>\n\n"
            "<b>📋 Документ:</b> #{number}\n"
            "<b>🗓 Дата и время:</b> {date}\n\n"
            "<b>💵 Сумма:</b> <b>{amount} {currency}</b>\n"
            "<b>🔖 Способ:</b> {method}\n\n"
            "<b>Текущий баланс:</b> <b>{balance} USD</b>"
        ),
    },

    "payout_notification": {
        "uz": (
            "💸 <b>To'lov amalga oshirildi!</b>\n\n"
            "<b>📋 Hujjat:</b> #{number}\n"
            "<b>🗓 Sana va vaqt:</b> {date}\n\n"
            "<b>💵 Miqdor:</b> <b>{amount} {currency}</b>\n"
            "<b>🔖 Usul:</b> {method}\n\n"
            "<b>Joriy balans:</b> <b>{balance} USD</b>"
        ),
        "ru": (
            "💸 <b>Выплата произведена!</b>\n\n"
            "<b>📋 Документ:</b> #{number}\n"
            "<b>🗓 Дата и время:</b> {date}\n\n"
            "<b>💵 Сумма:</b> <b>{amount} {currency}</b>\n"
            "<b>🔖 Способ:</b> {method}\n\n"
            "<b>Текущий баланс:</b> <b>{balance} USD</b>"
        ),
    },

    "supply_notification": {
        "uz": (
            "📦 <b>Yangi ta'minot (priyomka)!</b> #{number}\n"
            "🗓 <b>{date}</b>\n"
            "🙋‍♂️ <b>Yaratdi:</b> {created_by}\n"
            "🧑‍💻 <b>Javobgar:</b> {responsible}\n"
            "🏠 <b>Ombor:</b> {warehouse}\n\n"
            "🙍 <b>Ta'minotchi:</b> {name}\n"
            "📞 <b>Telefon:</b> {phone}\n\n"
            "<b>📦 Mahsulotlar:</b>\n"
            "{items}\n\n"
            "👉 <b>Jami:</b> <b>{total} USD</b>\n"
            "💰 <b>Joriy balans:</b> <b>{balance} USD</b>"
        ),
        "ru": (
            "📦 <b>Новая поставка (приёмка)!</b> #{number}\n"
            "🗓 <b>{date}</b>\n"
            "🙋‍♂️ <b>Создал:</b> {created_by}\n"
            "🧑‍💻 <b>Ответственный:</b> {responsible}\n"
            "🏠 <b>Склад:</b> {warehouse}\n\n"
            "🙍 <b>Поставщик:</b> {name}\n"
            "📞 <b>Телефон:</b> {phone}\n\n"
            "<b>📦 Товары:</b>\n"
            "{items}\n\n"
            "👉 <b>Итого:</b> <b>{total} USD</b>\n"
            "💰 <b>Текущий баланс:</b> <b>{balance} USD</b>"
        ),
    },

    "purchasereturn_notification": {
        "uz": (
            "🔄 <b>Ta'minotchiga qaytarish!</b>\n\n"
            "<b>📋</b> #{number}\n"
            "<b>🗓 Sana va vaqt:</b> {date}\n"
            "<b>🙋‍♂️ Yaratdi:</b> {created_by}\n"
            "<b>🧑‍💻 Javobgar:</b> {responsible}\n\n"
            "<b>🙍 Ta'minotchi:</b> {name}\n"
            "<b>📞 Telefon:</b> {phone}\n\n"
            "<b>🏠 Ombor:</b> {warehouse}\n\n"
            "{items}\n\n"
            "<b>👉 Jami:</b> <b>{total} USD</b>\n\n"
            "<b>Joriy balans:</b> <b>{balance} USD</b>"
        ),
        "ru": (
            "🔄 <b>Возврат поставщику!</b>\n\n"
            "<b>📋</b> #{number}\n"
            "<b>🗓 Дата и время:</b> {date}\n"
            "<b>🙋‍♂️ Создал:</b> {created_by}\n"
            "<b>🧑‍💻 Ответственный:</b> {responsible}\n\n"
            "<b>🙍 Поставщик:</b> {name}\n"
            "<b>📞 Телефон:</b> {phone}\n\n"
            "<b>🏠 Склад:</b> {warehouse}\n\n"
            "{items}\n\n"
            "<b>👉 Итого:</b> <b>{total} USD</b>\n\n"
            "<b>Текущий баланс:</b> <b>{balance} USD</b>"
        ),
    },

    "daily_admin_report": {
        "ru": (
            "📊 Отчёт: Сегодня, {date}\n\n"
            "🛒 Продажи:\n\n"
            "  Заказы: {orders_count} шт. — ${orders_total}\n"
            "  Отгрузки: {ship_count} шт. — ${ship_total}\n\n"
            "💰 Приходы:\n\n"
            "  Безнал: {paymentin_count} шт. — ${paymentin_total}\n"
            "  Наличные: {cashin_count} шт. — ${cashin_total}\n\n"
            "💸 Расходы:\n\n"
            "  Безнал: {paymentout_count} шт. — ${paymentout_total}\n"
            "  Наличные: {cashout_count} шт. — ${cashout_total}\n\n"
            "📦 Поставки: {supply_count} шт. — ${supply_total}\n\n"
            "👤 Новые клиенты:\n"
            "  МойСклад: {new_cp_ms}\n"
            "  Бот: {new_cp_bot}"
        ),
        "uz": (
            "📊 Hisobot: Bugun, {date}\n\n"
            "🛒 Sotuvlar:\n\n"
            "  Buyurtmalar: {orders_count} ta — ${orders_total}\n"
            "  Otgruzkalar: {ship_count} ta — ${ship_total}\n\n"
            "💰 Tushumlar:\n\n"
            "  Bank: {paymentin_count} ta — ${paymentin_total}\n"
            "  Naqd: {cashin_count} ta — ${cashin_total}\n\n"
            "💸 Chiqimlar:\n\n"
            "  Bank: {paymentout_count} ta — ${paymentout_total}\n"
            "  Naqd: {cashout_count} ta — ${cashout_total}\n\n"
            "📦 Ta'minotlar: {supply_count} ta — ${supply_total}\n\n"
            "👤 Yangi mijozlar:\n"
            "  MoySklad: {new_cp_ms}\n"
            "  Bot: {new_cp_bot}"
        ),
    },

    # ── Отчёт по дебиторке (P&L под продажи в долг) ────────────────────────
    "btn_debt_report": {
        "uz": "📉 Qarzdorlik",
        "ru": "📉 Дебиторка",
    },
    "debt_report_empty": {
        "uz": "✅ Ochiq qarzdorlik topilmadi — barcha otgruzkalar to‘langan.",
        "ru": "✅ Открытой дебиторки нет — все отгрузки оплачены.",
    },
    "debt_report_error": {
        "uz": "⚠️ Qarzdorlik hisobotini olishда xatolik. Keyinroq urinib ko‘ring.",
        "ru": "⚠️ Не удалось собрать отчёт по дебиторке. Попробуйте позже.",
    },
    "debt_report_header": {
        "ru": (
            "📉 <b>Дебиторка (продажи в долг)</b>\n"
            "🗓 на {date}  •  окно {lookback} дн.\n\n"
            "🧾 Начислено (Σ отгрузок): <b>${accrued}</b>\n"
            "💵 Собрано (Σ оплат): <b>${collected}</b>\n"
            "🔴 Дебиторка (остаток): <b>${receivable}</b>\n"
            "📄 Открытых документов: {doc_count} • должников: {debtor_count}\n\n"
            "📊 <b>По срокам просрочки:</b>\n"
            "  🟢 Текущая: {b_current_count} — ${b_current_total}\n"
            "  🟡 1–7 дн.: {b_d1_7_count} — ${b_d1_7_total}\n"
            "  🟠 8–30 дн.: {b_d8_30_count} — ${b_d8_30_total}\n"
            "  🔴 31–90 дн.: {b_d31_90_count} — ${b_d31_90_total}\n"
            "  ⚫️ 90+ дн. (риск): {b_d90_plus_count} — ${b_d90_plus_total}"
        ),
        "uz": (
            "📉 <b>Qarzdorlik (nasiyaga sotuv)</b>\n"
            "🗓 {date} holatiga  •  oyna {lookback} kun\n\n"
            "🧾 Hisoblangan (Σ otgruzka): <b>${accrued}</b>\n"
            "💵 Yig‘ilgan (Σ to‘lov): <b>${collected}</b>\n"
            "🔴 Qarzdorlik (qoldiq): <b>${receivable}</b>\n"
            "📄 Ochiq hujjatlar: {doc_count} • qarzdorlar: {debtor_count}\n\n"
            "📊 <b>Muddat bo‘yicha:</b>\n"
            "  🟢 Joriy: {b_current_count} — ${b_current_total}\n"
            "  🟡 1–7 kun: {b_d1_7_count} — ${b_d1_7_total}\n"
            "  🟠 8–30 kun: {b_d8_30_count} — ${b_d8_30_total}\n"
            "  🔴 31–90 kun: {b_d31_90_count} — ${b_d31_90_total}\n"
            "  ⚫️ 90+ kun (xavf): {b_d90_plus_count} — ${b_d90_plus_total}"
        ),
    },
    "debt_report_rows_header": {
        "uz": "\n\n🔎 <b>Eng muddati o‘tgan hujjatlar:</b>",
        "ru": "\n\n🔎 <b>Самые просроченные документы:</b>",
    },
    "debt_report_row": {
        "ru": (
            "• <b>{client}</b> — №{doc} от {date}\n"
            "  остаток <b>${remainder}</b> из ${sum} (оплачено ${paid}) • срок {term} дн. • просрочка {overdue} дн."
        ),
        "uz": (
            "• <b>{client}</b> — №{doc} ({date})\n"
            "  qoldiq <b>${remainder}</b> / ${sum} (to‘landi ${paid}) • muddat {term} kun • kechikish {overdue} kun"
        ),
    },
    "debt_report_more": {
        "uz": "…va yana {n} ta hujjat",
        "ru": "…и ещё {n} документов",
    },

    "return_notification": {
        "uz": (
            "🔄 <b>Qaytarish amalga oshirildi!</b>\n\n"
            "<b>📋</b> #{number}\n"
            "<b>🗓 Sana va vaqt:</b> {date}\n"
            "<b>🙋‍♂️ Yaratdi:</b> {created_by}\n"
            "<b>🧑‍💻 Javobgar:</b> {responsible}\n\n"
            "<b>🙍 Mijoz:</b> {name}\n"
            "<b>📞 Telefon:</b> {phone}\n\n"
            "<b>🏠 Ombor:</b> {warehouse}\n\n"
            "{items}\n\n"
            "<b>👉 Jami:</b> <b>{total} USD</b>\n\n"
            "<b>Joriy balans:</b> <b>{balance} USD</b>"
        ),
        "ru": (
            "🔄 <b>Возврат оформлен!</b>\n\n"
            "<b>📋</b> #{number}\n"
            "<b>🗓 Дата и время:</b> {date}\n"
            "<b>🙋‍♂️ Создал:</b> {created_by}\n"
            "<b>🧑‍💻 Ответственный:</b> {responsible}\n\n"
            "<b>🙍 Клиент:</b> {name}\n"
            "<b>📞 Телефон:</b> {phone}\n\n"
            "<b>🏠 Склад:</b> {warehouse}\n\n"
            "{items}\n\n"
            "<b>👉 Итого:</b> <b>{total} USD</b>\n\n"
            "<b>Текущий баланс:</b> <b>{balance} USD</b>"
        ),
    },

    # ── Errors ─────────────────────────────────────────────────────────────
    "not_registered": {
        "uz": "❌ Siz ro'yxatdan o'tmagansiz. /start bosing.",
        "ru": "❌ Вы не зарегистрированы. Нажмите /start.",
    },
    "menu_fallback": {
        "uz": "Pastdagi tugmalardan foydalaning 👇",
        "ru": "Пользуйтесь кнопками меню ниже 👇",
    },
}


def t(key: str, lang: str = "uz", **kwargs) -> str:
    """Get translated string. Falls back to 'uz' if lang not found."""
    variants = STRINGS.get(key, {})
    text = variants.get(lang) or variants.get("uz") or key
    return text.format(**kwargs) if kwargs else text
