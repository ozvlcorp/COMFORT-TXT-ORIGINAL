"""MoySklad API client (async)."""
from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import deque

import httpx
from config import (
    MOYSKLAD_API,
    MOYSKLAD_ENRICH_CONCURRENCY,
    MOYSKLAD_TOKEN,
    MS_MOMENT_LOG,
    MOYSKLAD_MAX_PARALLEL,
    MOYSKLAD_RATE_LIMIT_MAX_TOKENS,
    MOYSKLAD_RATE_LIMIT_WINDOW_SEC,
    MOYSKLAD_MAX_RETRIES,
    MOYSKLAD_MAX_RATE_LIMIT_WAITS,
    MOYSKLAD_RETRY_BACKOFF_BASE,
    MOYSKLAD_RETRY_BACKOFF_MAX,
    BALANCE_CACHE_TTL_SECONDS,
    BALANCE_CACHE_MAX_SIZE,
    COUNTERPARTY_ID_CACHE_TTL_SECONDS,
    COUNTERPARTY_ID_CACHE_MAX_SIZE,
)
from moysklad_cache import TTLCache

logger = logging.getLogger(__name__)

_http_lock: asyncio.Lock | None = None
_http_client: httpx.AsyncClient | None = None

# ─── Rate limit enforcement (FIX #1) ─────────────────────────────────────────
# Concurrency semaphore: blocks if MOYSKLAD_MAX_PARALLEL requests are in-flight
_concurrency_sem: asyncio.Semaphore | None = None
# Token bucket state: deque of request timestamps (monotonic) in sliding window
_token_bucket_deque: deque[float] | None = None
# Lock to protect token bucket mutations (asyncio-safe)
_token_bucket_lock: asyncio.Lock | None = None

# ─── Caches (FIX #3) ─────────────────────────────────────────────────────────
balance_cache: TTLCache | None = None
cp_id_phone_cache: TTLCache | None = None


async def init_caches() -> None:
    """Initialize cache instances (call from bot.py startup)."""
    global balance_cache, cp_id_phone_cache
    balance_cache = TTLCache(
        ttl_seconds=BALANCE_CACHE_TTL_SECONDS,
        max_size=BALANCE_CACHE_MAX_SIZE,
    )
    cp_id_phone_cache = TTLCache(
        ttl_seconds=COUNTERPARTY_ID_CACHE_TTL_SECONDS,
        max_size=COUNTERPARTY_ID_CACHE_MAX_SIZE,
    )
    # Eagerly build the rate limiters at startup so the first concurrent burst
    # of requests can't race two of them into existence.
    await _get_or_init_rate_limiters()
    logger.info(
        "Caches initialized: balance (TTL=%ds, max=%d), cp_id_phone (TTL=%ds, max=%d)",
        BALANCE_CACHE_TTL_SECONDS,
        BALANCE_CACHE_MAX_SIZE,
        COUNTERPARTY_ID_CACHE_TTL_SECONDS,
        COUNTERPARTY_ID_CACHE_MAX_SIZE,
    )


async def close_caches() -> None:
    """Cleanup caches (no resources held, but good for consistency)."""
    global balance_cache, cp_id_phone_cache
    balance_cache = None
    cp_id_phone_cache = None


async def _get_or_init_rate_limiters():
    """Lazily initialize (once per module lifetime) the global rate limiters.

    Returns: (concurrency_semaphore, token_bucket_deque, token_bucket_lock)
    """
    global _concurrency_sem, _token_bucket_deque, _token_bucket_lock
    if _concurrency_sem is None:
        _concurrency_sem = asyncio.Semaphore(MOYSKLAD_MAX_PARALLEL)
        _token_bucket_deque = deque()
        _token_bucket_lock = asyncio.Lock()
    return _concurrency_sem, _token_bucket_deque, _token_bucket_lock


async def _acquire_token() -> None:
    """Wait until a token is available in the sliding-window bucket (~45/3s)."""
    sem, bucket, lock = await _get_or_init_rate_limiters()

    max_toks = MOYSKLAD_RATE_LIMIT_MAX_TOKENS

    while True:
        async with lock:
            now = time.monotonic()
            window_start = now - MOYSKLAD_RATE_LIMIT_WINDOW_SEC
            # Purge expired timestamps
            while bucket and bucket[0] < window_start:
                bucket.popleft()
            # Check if token available
            if len(bucket) < max_toks:
                bucket.append(time.monotonic())
                return
        # Wait and retry (avoid tight busy-loop)
        await asyncio.sleep(0.01)


def _is_idempotent_safe(method: str, url: str) -> bool:
    """True if a request is safe to retry on transient errors.

    Safe: GET (always); POST to /report/* (read-only reports).
    NOT safe: entity-creation POSTs (duplicate risk), PUT, DELETE.
    """
    method_upper = (method or "").upper()
    if method_upper == "GET":
        return True
    if method_upper == "POST" and "/report/" in (url or ""):
        return True
    return False


async def _request(
    method: str,
    url: str,
    *,
    json_data: dict | None = None,
    params: dict | None = None,
    retry_safe: bool | None = None,
) -> dict:
    """Execute an HTTP request with rate-limit and retry protection.

    - Token bucket (45/3s) + concurrency semaphore (5 parallel) gate every call.
    - 429 → read X-Lognex-Retry-After (ms), release semaphore during sleep, retry
      without incrementing attempt count.
    - 5xx / timeout / transport errors → retry with exponential backoff + jitter
      ONLY if retry_safe; otherwise re-raise immediately.
    - 4xx (except 429) → re-raise immediately.
    """
    sem, bucket, lock = await _get_or_init_rate_limiters()

    if retry_safe is None:
        retry_safe = _is_idempotent_safe(method, url)

    attempt = 0
    rate_limit_waits = 0
    last_exception: Exception | None = None

    while attempt < MOYSKLAD_MAX_RETRIES:
        attempt += 1
        try:
            # Step 1: acquire token (rate limit)
            await _acquire_token()

            # Step 2: acquire semaphore (concurrency limit)
            async with sem:
                client = await _shared_http()
                m = method.upper()
                if m == "GET":
                    resp = await client.get(url, params=params)
                elif m == "POST":
                    resp = await client.post(url, json=json_data)
                elif m == "PUT":
                    resp = await client.put(url, json=json_data)
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")

                # Step 4: handle 429 rate limit
                if resp.status_code == 429:
                    retry_after_ms = None
                    try:
                        header_val = resp.headers.get("X-Lognex-Retry-After", "")
                        if header_val:
                            retry_after_ms = int(header_val)
                    except (ValueError, TypeError):
                        retry_after_ms = None
                    if retry_after_ms is None:
                        backoff = MOYSKLAD_RETRY_BACKOFF_BASE * (2 ** rate_limit_waits)
                        retry_after_ms = int(backoff * 1000)
                    # Cap the wait — even a server-supplied X-Lognex-Retry-After is
                    # bounded so a misbehaving header can never hang the request.
                    wait_sec = min(retry_after_ms / 1000.0, MOYSKLAD_RETRY_BACKOFF_MAX)
                    # Semaphore released by exiting `async with sem` before sleeping
                    # so we don't block other requests while waiting.
                    _429_wait = wait_sec
                else:
                    _429_wait = None

                    # Step 5: raise for other HTTP errors
                    resp.raise_for_status()

                    # Step 6: success
                    return resp.json()

            # 429 path: semaphore released above. A 429 is flow-control, not a
            # transient error, so it does not consume the transient-retry budget
            # (attempt -= 1). Termination is guaranteed by a SEPARATE bounded
            # counter so a persistently-throttled account can't spin forever.
            if _429_wait is not None:
                rate_limit_waits += 1
                if rate_limit_waits > MOYSKLAD_MAX_RATE_LIMIT_WAITS:
                    logger.error(
                        "_request giving up after %d rate-limit (429) waits: url=%s",
                        rate_limit_waits - 1, url,
                    )
                    resp.raise_for_status()  # raises HTTPStatusError for 429
                logger.warning(
                    "_request 429 (wait %d/%d): url=%s, sleeping %.2fs",
                    rate_limit_waits, MOYSKLAD_MAX_RATE_LIMIT_WAITS, url, _429_wait,
                )
                await asyncio.sleep(_429_wait)
                attempt -= 1
                continue

        except (httpx.TimeoutException, httpx.TransportError) as net_err:
            last_exception = net_err
            if not retry_safe:
                raise
            if attempt < MOYSKLAD_MAX_RETRIES:
                backoff = min(
                    MOYSKLAD_RETRY_BACKOFF_BASE * (2 ** (attempt - 1)),
                    MOYSKLAD_RETRY_BACKOFF_MAX,
                )
                jitter = backoff * 0.1 * (2 * random.random() - 1)
                wait_sec = backoff + jitter
                logger.warning(
                    "_request transient error (attempt %d/%d): %s, retrying in %.2fs",
                    attempt, MOYSKLAD_MAX_RETRIES, type(net_err).__name__, wait_sec,
                )
                await asyncio.sleep(wait_sec)
                continue
            raise

        except httpx.HTTPStatusError as http_err:
            last_exception = http_err
            if not (500 <= http_err.response.status_code < 600):
                raise
            if not retry_safe:
                raise
            if attempt < MOYSKLAD_MAX_RETRIES:
                backoff = min(
                    MOYSKLAD_RETRY_BACKOFF_BASE * (2 ** (attempt - 1)),
                    MOYSKLAD_RETRY_BACKOFF_MAX,
                )
                jitter = backoff * 0.1 * (2 * random.random() - 1)
                wait_sec = backoff + jitter
                logger.warning(
                    "_request 5xx (attempt %d/%d): status=%d, url=%s, retrying in %.2fs",
                    attempt, MOYSKLAD_MAX_RETRIES,
                    http_err.response.status_code, url, wait_sec,
                )
                await asyncio.sleep(wait_sec)
                continue
            raise

    if last_exception:
        raise last_exception
    raise RuntimeError(f"_request exhausted {MOYSKLAD_MAX_RETRIES} attempts for {url}")


def _to_default_amount(sum_minor, rate) -> float:
    """Convert a MoySklad sum from doc-currency minor units (kopecks/tijin)
    to account default currency main units (e.g. USD).

    MoySklad: amount_in_default_currency = (sum / 100) * rate.value
    Если rate.value отсутствует — документ оформлен в валюте по умолчанию,
    factor=1.
    """
    if not sum_minor:
        return 0.0
    factor = 1.0
    if isinstance(rate, dict):
        v = rate.get("value")
        if v is not None:
            try:
                factor = float(v)
            except (TypeError, ValueError):
                factor = 1.0
    return round((float(sum_minor) / 100.0) * factor, 2)


def _to_original_amount(sum_minor) -> float:
    """Sum in document's own currency main units (NOT converted), e.g. сум."""
    if not sum_minor:
        return 0.0
    return round(float(sum_minor) / 100.0, 2)


def _doc_currency_name(rate) -> str:
    """Display name of document currency from rate.currency.name (e.g. 'сум', 'USD')."""
    if isinstance(rate, dict):
        cur = rate.get("currency") or {}
        name = (cur.get("name") or "").strip()
        if name:
            return name
    return "USD"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {MOYSKLAD_TOKEN}",
        "Content-Type": "application/json",
        "Accept-Encoding": "gzip",
    }


async def _shared_http() -> httpx.AsyncClient:
    """Bitta AsyncClient — har so‘rovda TLS qayta ochilmaydi (server/VPS uchun)."""
    global _http_client, _http_lock
    if _http_lock is None:
        _http_lock = asyncio.Lock()
    async with _http_lock:
        if _http_client is None or _http_client.is_closed:
            _http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(25.0, connect=10.0),
                limits=httpx.Limits(max_keepalive_connections=24, max_connections=6),
                headers=_headers(),
            )
        return _http_client


async def close_moysklad_http() -> None:
    """bot.py finally: ulanishlarni yopish + reset rate limiters."""
    global _http_client, _http_lock
    global _concurrency_sem, _token_bucket_deque, _token_bucket_lock
    if _http_lock is None:
        return
    async with _http_lock:
        if _http_client is not None:
            await _http_client.aclose()
            _http_client = None

    # Reset rate limiter state (for clean restart)
    _concurrency_sem = None
    _token_bucket_deque = None
    _token_bucket_lock = None


async def _get(url: str, params: dict | None = None) -> dict:
    """GET wrapper — always idempotent-safe."""
    return await _request("GET", url, params=params, retry_safe=True)


async def _post(url: str, json_data: dict | None = None) -> dict:
    """POST wrapper — auto-detects retry safety (/report/* safe, creates not)."""
    return await _request("POST", url, json_data=json_data)


async def _put(url: str, json_data: dict | None = None) -> dict:
    """PUT wrapper — generally NOT retryable (mutations)."""
    return await _request("PUT", url, json_data=json_data, retry_safe=False)


async def sync_counterparty(name: str, phone: str, telegram_id: int) -> dict:
    """
    Найти контрагента по телефону и добавить Telegram ID в атрибуты.

    ВАЖНО: если контрагент уже существует — обновляем ТОЛЬКО атрибут telegram_id,
    не трогая имя и телефон (они управляются менеджером в МойСклад).
    Если контрагент не найден — создаём нового.
    """
    url = f"{MOYSKLAD_API}/entity/counterparty"

    # Атрибут Telegram ID (кастомный атрибут в МойСклад)
    tg_attribute = {
        "meta": {
            "href": f"{MOYSKLAD_API}/entity/counterparty/metadata/attributes/8666aeb7-192b-11f1-0a80-00f20005e1af",
            "type": "attributemetadata",
            "mediaType": "application/json",
        },
        "value": str(telegram_id),
    }

    try:
        # Prefer robust multi-format search to avoid duplicate counterparties.
        existing_cp_id = await find_counterparty_id_by_phone(phone)
        if existing_cp_id:
            matched = await _get(f"{url}/{existing_cp_id}")
            logger.info(
                "Found existing counterparty '%s' (id=%s) by phone — no new counterparty created",
                matched.get("name"), existing_cp_id,
            )
            return matched

        # Контрагент не найден — создаём нового
        logger.info("Counterparty not found for phone %s, creating new", phone)
        new_data = {
            "name": name,
            "phone": phone,
            "attributes": [tg_attribute],
        }
        return await _post(url, json_data=new_data)

    except Exception as e:
        logger.error("sync_counterparty error for phone %s: %s", phone, e)
        raise


async def fetch_entity(href: str) -> dict:
    """Fetch any MoySklad entity by its full href URL."""
    params = {
        "expand": (
            "agent,positions.assortment,store,rate.currency,owner,salesChannel,attributes"
        ),
        "limit": 100,
    }
    return await _get(href, params=params)


def parse_payment(data: dict, payment_type: str) -> dict:
    """Parse cashin/paymentin/cashout/paymentout response.

    Direction:
      • cashin / paymentin  — мы получили деньги (приход)
      • cashout / paymentout — мы выплатили деньги (расход)
    """
    agent = data.get("agent", {}) or {}
    agent_phone = agent.get("phone", "")
    rate = data.get("rate")
    # Сумму платежа показываем клиенту в валюте документа.
    amount_original = _to_original_amount(data.get("sum"))

    currency_name = _doc_currency_name(rate)

    is_cash = payment_type in ("cashin", "cashout")
    method = "Наличные" if is_cash else "Электронные"
    method_uz = "Naqd pul" if is_cash else "Elektron"
    direction = "in" if payment_type in ("cashin", "paymentin") else "out"

    return {
        "moysklad_id": data.get("id", ""),
        "payment_number": data.get("name", ""),
        "moment": _moment_compact_storage(data.get("moment")),
        "agent_name": agent.get("name", ""),
        "agent_phone": agent_phone,
        "agent_id": _extract_id(agent.get("meta", {}).get("href", "")),
        "amount": amount_original,
        "currency": currency_name,
        "payment_method_ru": method,
        "payment_method_uz": method_uz,
        "direction": direction,
        "payment_type": payment_type,
    }


async def fetch_counterparty(href: str) -> dict:
    """Fetch a counterparty (agent) by href."""
    return await _get(href)


async def find_counterparty_id_by_phone(phone: str) -> str | None:
    """
    Найти ID контрагента в МойСклад по номеру телефона.

    Использует cp_id_phone_cache (TTL ~1h) для избежания повторных запросов.
    Нормализованный телефон (только цифры) — ключ кеша.
    """
    phone_digits = "".join(c for c in phone if c.isdigit())
    if not phone_digits:
        return None

    # Try cache first
    if cp_id_phone_cache is not None:
        cached = await cp_id_phone_cache.get(phone_digits)
        if cached is not None:
            logger.debug(
                "find_counterparty_id_by_phone: cache HIT for phone=%s → cp_id=%s",
                phone_digits, cached,
            )
            return cached

    url = f"{MOYSKLAD_API}/entity/counterparty"
    suffix9 = phone_digits[-9:]  # последние 9 цифр для сравнения

    # Несколько форматов поиска — от точного к широкому
    search_variants = list(dict.fromkeys([
        f"+{phone_digits}",   # +998909623393  (как в sync_counterparty — точно работает)
        phone_digits,          # 998909623393
        suffix9,               # 909623393
    ]))

    result_cp_id = None
    for search_term in search_variants:
        try:
            resp = await _get(url, params={"search": search_term, "limit": 50})
            rows = resp.get("rows", [])
            for row in rows:
                row_phone_d = "".join(c for c in (row.get("phone") or "") if c.isdigit())
                if row_phone_d and row_phone_d.endswith(suffix9):
                    result_cp_id = row.get("id")
                    logger.info(
                        "find_counterparty_id_by_phone: found '%s' (id=%s) via search '%s'",
                        row.get("name"), result_cp_id, search_term,
                    )
                    break
            if result_cp_id:
                break
        except Exception as e:
            logger.debug("Search '%s' failed: %s", search_term, e)

    # Store positive result in cache (TTL ~1h)
    if cp_id_phone_cache is not None and result_cp_id is not None:
        await cp_id_phone_cache.set(phone_digits, result_cp_id)

    if result_cp_id is None:
        logger.warning("find_counterparty_id_by_phone: NOT FOUND for phone=%s", phone)

    return result_cp_id


async def fetch_counterparty_balance(counterparty_id: str) -> float:
    """
    Получить баланс взаиморасчётов контрагента из отчёта МойСклад.

    POST /report/counterparty  (GET с filter= даёт 412, используем POST)
    rows[0].balance — в копейках (1/100 валюты): -786 → -7.86 USD

    Кеш (balance_cache, TTL ~45s) хранит balance float и обновляется при
    каждом фетче. invalidate_balance_cache(cp_id) форсирует перефетч.

    Raises httpx.HTTPStatusError on API errors so callers can handle them.
    """
    # Try cache first
    if balance_cache is not None:
        cached = await balance_cache.get(counterparty_id)
        if cached is not None:
            logger.debug(
                "fetch_counterparty_balance: cache HIT for cp=%s → %.2f USD",
                counterparty_id, cached,
            )
            return cached

    counterparty_href = f"{MOYSKLAD_API}/entity/counterparty/{counterparty_id}"
    url = f"{MOYSKLAD_API}/report/counterparty"
    body = {
        "counterparties": [
            {
                "counterparty": {
                    "meta": {
                        "href": counterparty_href,
                        "type": "counterparty",
                        "mediaType": "application/json",
                    }
                }
            }
        ]
    }
    data = await _post(url, json_data=body)
    rows = data.get("rows", [])
    logger.debug("fetch_counterparty_balance: cp=%s rows_count=%d", counterparty_id, len(rows))
    if not rows:
        # Empty rows is usually a transient/stale-cp_id quirk, not a real 0 balance.
        # Do NOT cache it, otherwise we'd pin the displayed balance to 0 for the TTL.
        logger.warning("fetch_counterparty_balance: empty rows for cp=%s (not cached)", counterparty_id)
        return 0.0

    row = rows[0]
    balance_raw = row.get("balance", 0) or 0
    balance = round(balance_raw / 100, 2)
    logger.debug("fetch_counterparty_balance: cp=%s raw=%s → %.2f USD", counterparty_id, balance_raw, balance)

    # Store in cache (only real report rows are cached)
    if balance_cache is not None:
        await balance_cache.set(counterparty_id, balance)

    return balance


async def invalidate_balance_cache(counterparty_id: str) -> None:
    """
    Invalidate balance cache for a specific counterparty.

    Called immediately after a balance-changing webhook event
    (shipment, payment, return) so the next read re-fetches from MoySklad.
    """
    if balance_cache is not None:
        await balance_cache.invalidate(counterparty_id)
        logger.debug("Balance cache invalidated for cp=%s", counterparty_id)


def _agent_filter(counterparty_id: str) -> str:
    href = f"{MOYSKLAD_API}/entity/counterparty/{counterparty_id}"
    return f"agent={href}"


def _moment_cmp_key(moment_compact: str) -> str:
    if not moment_compact:
        return ""
    s = str(moment_compact).strip().replace("T", " ")
    if len(s) >= 19:
        return s[:19]
    if len(s) == 16:
        return s + ":00"
    if len(s) == 10:
        return s + " 00:00:00"
    return (s + " 00:00:00")[:19] if s else ""


def _moment_in_closed_range(moment_compact: str, lo: str | None, hi: str | None) -> bool:
    """inclusive; lo/hi — 'YYYY-MM-DD HH:MM:SS' yoki None."""
    k = _moment_cmp_key(moment_compact)
    if not k:
        return False
    if lo and k < _moment_cmp_key(lo):
        return False
    if hi and k > _moment_cmp_key(hi):
        return False
    return True


async def _fetch_agent_documents_paginated(
    entity: str,
    counterparty_id: str,
    *,
    parse_row,
    moment_lo: str | None,
    moment_hi: str | None,
    result_limit: int | None,
    max_api_scan: int = 8000,
    enrich_demand_row: bool = False,
) -> list[dict]:
    """
    entity: 'demand' | 'salesreturn'
    result_limit: None = barcha mos keluvchilar (max_api_scan gacha sahifalar).
    """
    url = f"{MOYSKLAD_API}/entity/{entity}"
    collected: list[dict] = []
    offset = 0
    page = 500
    scanned = 0
    while offset < max_api_scan:
        if result_limit is not None and len(collected) >= result_limit:
            break
        expand = (
            "agent,owner,salesChannel,store,state,positions.assortment,attributes,rate.currency"
            if entity == "demand"
            else "agent,owner,salesChannel,store,state,positions.assortment,rate.currency"
        )
        params = {
            "filter": _agent_filter(counterparty_id),
            "limit": page,
            "offset": offset,
            "order": "moment,desc",
            "expand": expand,
        }
        data = await _get(url, params=params)
        batch = data.get("rows") or []
        if not batch:
            break
        scanned += len(batch)
        parsed: list[tuple[dict, dict]] = []
        for row in batch:
            try:
                p = parse_row(row)
            except Exception as ex:
                logger.debug("parse %s row skip: %s", entity, ex)
                continue
            parsed.append((row, p))

        if enrich_demand_row and entity == "demand" and parsed:
            sem = asyncio.Semaphore(MOYSKLAD_ENRICH_CONCURRENCY)

            async def _enrich_one(r: dict, pp: dict) -> None:
                async with sem:
                    try:
                        await enrich_demand_from_moysklad(r, pp)
                    except Exception as ex:
                        logger.warning("enrich_demand_from_moysklad list row: %s", ex)

            await asyncio.gather(*(_enrich_one(r, p) for r, p in parsed))

        for row, p in parsed:
            if moment_lo or moment_hi:
                if not _moment_in_closed_range(p.get("moment") or "", moment_lo, moment_hi):
                    continue
            collected.append(p)
            if result_limit is not None and len(collected) >= result_limit:
                break
        if result_limit is not None and len(collected) >= result_limit:
            break
        if len(batch) < page:
            break
        offset += page
    return collected


async def fetch_demands_for_counterparty(
    counterparty_id: str,
    *,
    moment_lo: str | None = None,
    moment_hi: str | None = None,
    result_limit: int | None = 30,
    max_api_scan: int = 8000,
) -> list[dict]:
    """Otgruzkalar — MoySklad, mahalliy DB emas."""
    return await _fetch_agent_documents_paginated(
        "demand",
        counterparty_id,
        parse_row=parse_demand,
        moment_lo=moment_lo,
        moment_hi=moment_hi,
        result_limit=result_limit,
        max_api_scan=max_api_scan,
        enrich_demand_row=True,
    )


async def fetch_salesreturns_for_counterparty(
    counterparty_id: str,
    *,
    moment_lo: str | None = None,
    moment_hi: str | None = None,
    result_limit: int | None = None,
    max_api_scan: int = 8000,
) -> list[dict]:
    """Qaytarishlar — MoySklad."""
    return await _fetch_agent_documents_paginated(
        "salesreturn",
        counterparty_id,
        parse_row=parse_salesreturn,
        moment_lo=moment_lo,
        moment_hi=moment_hi,
        result_limit=result_limit,
        max_api_scan=max_api_scan,
    )


def parse_order(data: dict) -> dict:
    """
    Parse MoySklad customerorder response into a clean dict.

    Документ может быть оформлен в любой валюте; конвертируем в валюту
    по умолчанию аккаунта через rate.value (см. _to_default_amount).
    """
    agent = data.get("agent", {}) or {}
    agent_phone = agent.get("phone", "") or ""
    rate = data.get("rate")

    pos_block = data.get("positions") or {}
    rows = pos_block.get("rows", []) if isinstance(pos_block, dict) else []
    items = []
    for pos in rows:
        assortment = pos.get("assortment", {}) or {}
        qty = pos.get("quantity", 0) or 0
        # Двойная картина: price/total остаются в валюте по умолчанию
        # (USD) — для совместимости с базой и старыми вызовами,
        # price_original/total_original в валюте документа — для отображения.
        price = _to_default_amount(pos.get("price"), rate)
        total = round(qty * price, 2)
        price_original = _to_original_amount(pos.get("price"))
        total_original = round(qty * price_original, 2)
        unit_name = (assortment.get("uom") or {}).get("name") or ""
        items.append({
            "name": assortment.get("name", "—"),
            "code": (assortment.get("article") or assortment.get("code") or "").strip(),
            "quantity": qty,
            "uom": unit_name,
            "price": price,
            "total": total,
            "price_original": price_original,
            "total_original": total_original,
        })

    total_sum = _to_default_amount(data.get("sum"), rate)
    total_sum_original = _to_original_amount(data.get("sum"))
    currency_name = _doc_currency_name(rate)

    return {
        "moysklad_id": data.get("id", ""),
        "order_number": data.get("name", ""),
        "moment": _moment_compact_storage(data.get("moment")),
        "status": _state_name(data),
        "agent_name": agent.get("name", ""),
        "agent_phone": agent_phone,
        "agent_href": agent.get("meta", {}).get("href", ""),
        "agent_id": _extract_id(agent.get("meta", {}).get("href", "")),
        "total_usd": total_sum,
        "total_original": total_sum_original,
        "currency": currency_name,
        "items": items,
    }


def _embedded_name(obj: dict | None) -> str:
    if not obj:
        return ""
    return (obj.get("name") or "").strip()


def _person_name(obj: dict | None) -> str:
    """Сотрудник / контрагент — имя из name или ФИО из полей API."""
    if not obj or not isinstance(obj, dict):
        return ""
    n = (obj.get("name") or "").strip()
    if n:
        return n
    fn = (obj.get("firstName") or "").strip()
    ln = (obj.get("lastName") or "").strip()
    mi = (obj.get("middleName") or "").strip()
    parts = " ".join(p for p in (fn, mi, ln) if p).strip()
    if parts:
        return parts
    return (obj.get("shortFio") or obj.get("email") or "").strip()


def _attribute_value_display(attr: dict) -> str:
    """Доп. поле demand — человекочитаемое значение (строка / сотрудник / ссылка)."""
    val = attr.get("value")
    if val is None:
        return ""
    if isinstance(val, dict):
        name = (val.get("name") or "").strip()
        if name:
            return name
        return _embedded_name(val)
    if isinstance(val, bool):
        return ""
    return str(val).strip()


def _seller_attribute_label_keys() -> tuple[str, ...]:
    return (
        "продавец",
        "sotuvchi",
        "сотув",
        "сотувчи",
        "seller",
        "vendor",
        "менеджер",
        "manager",
        "ответственн",
        "javobgar",
        "консульт",
        "consultant",
        "торгов",
    )


def _employee_href_from_attribute(attr: dict) -> str | None:
    """Доп. поле (employee): meta.type=employee и href на сущность employee."""
    val = attr.get("value")
    if not isinstance(val, dict):
        return None
    meta = val.get("meta") or {}
    if (meta.get("type") or "").lower() != "employee":
        return None
    href = (meta.get("href") or "").strip()
    return href or None


def _first_employee_attribute_value(attrs: list) -> str:
    """Первое доп. поле типа employee с непустым отображаемым значением."""
    if not isinstance(attrs, list):
        return ""
    for a in attrs:
        if not isinstance(a, dict):
            continue
        if (a.get("type") or "").lower() != "employee":
            continue
        v = _attribute_value_display(a)
        if v:
            return v
    return ""


def _demand_seller_name(data: dict, owner_name: str) -> str:
    """
    «Продавец» / Sotuvchi: доп. атрибут по названию → тип employee → канал продаж → владелец.
    """
    attrs = data.get("attributes") or []
    if isinstance(attrs, list):
        keys = _seller_attribute_label_keys()
        for a in attrs:
            if not isinstance(a, dict):
                continue
            label = (a.get("name") or "").strip().lower()
            if not label:
                continue
            if any(k in label for k in keys):
                v = _attribute_value_display(a)
                if v:
                    return v
        v2 = _first_employee_attribute_value(attrs)
        if v2:
            return v2
    ch = _embedded_name(data.get("salesChannel"))
    if ch:
        return ch
    return (owner_name or "").strip()


async def _enrich_seller_from_employee_attributes(raw: dict, shipment: dict) -> None:
    """Если в значении employee только meta — подтянуть ФИО по href (список / PDF)."""
    attrs = raw.get("attributes") or []
    if not isinstance(attrs, list):
        return
    keys = _seller_attribute_label_keys()
    scored: list[tuple[int, dict]] = []
    for a in attrs:
        if not isinstance(a, dict):
            continue
        href = _employee_href_from_attribute(a)
        if not href:
            continue
        label = (a.get("name") or "").strip().lower()
        score = 0
        if label and any(k in label for k in keys):
            score = 2
        elif (a.get("type") or "").lower() == "employee":
            score = 1
        scored.append((score, a))
    scored.sort(key=lambda x: -x[0])
    for _, a in scored:
        href = _employee_href_from_attribute(a)
        if not href:
            continue
        try:
            emp = await _get(href)
            nm = _person_name(emp)
            if nm:
                shipment["seller_name"] = nm
                return
        except Exception as e:
            logger.debug("demand seller attr employee fetch href=%s: %s", href, e)


async def enrich_demand_from_moysklad(raw: dict, shipment: dict) -> None:
    """
    owner в ответе без ФИО (только meta) — подгружаем сотрудника; пересчитываем seller.
    Также: список отгрузок MS возвращает `positions` без rows (только meta-href),
    поэтому если items пуст — дофетчиваем отгрузку целиком.
    """
    owner = raw.get("owner") or {}
    oname = _person_name(owner)
    if not oname:
        href = (owner.get("meta") or {}).get("href")
        if href:
            try:
                emp = await _get(href)
                oname = _person_name(emp)
                if oname:
                    shipment["owner_name"] = oname
                    raw["owner"] = {**owner, **{k: v for k, v in emp.items() if k != "meta"}}
            except Exception as e:
                logger.warning("demand owner expand fetch failed href=%s: %s", href, e)
    elif not (shipment.get("owner_name") or "").strip():
        shipment["owner_name"] = oname

    if not (shipment.get("seller_name") or "").strip():
        shipment["seller_name"] = _demand_seller_name(raw, (shipment.get("owner_name") or oname or "").strip())

    if not (shipment.get("seller_name") or "").strip():
        await _enrich_seller_from_employee_attributes(raw, shipment)

    # Подтягиваем позиции и/или валюту, если в листинговом ответе их не было.
    needs_items = not shipment.get("items")
    needs_currency = not shipment.get("currency") or shipment.get("currency") == "USD" and not (
        ((raw.get("rate") or {}).get("currency") or {}).get("name")
    )
    if needs_items or needs_currency:
        href = (raw.get("meta") or {}).get("href")
        if href:
            try:
                full = await fetch_entity(href)
                full_parsed = parse_demand(full)
                if needs_items and full_parsed.get("items"):
                    shipment["items"] = full_parsed["items"]
                # Перезаписываем валюту/итог из полного объекта — там
                # rate.currency.name гарантированно populated через expand.
                full_currency = full_parsed.get("currency") or "USD"
                shipment["currency"] = full_currency
                if full_parsed.get("total_original") is not None:
                    shipment["total_original"] = full_parsed["total_original"]
            except Exception as e:
                logger.warning("demand items/currency fetch failed for %s: %s", href, e)


def _moment_compact_storage(raw: str | None) -> str:
    """MoySklad moment → DB/ko‘rinish (offset bor bo‘lsa APP_TIMEZONE ga aylantiriladi)."""
    from time_utils import _normalize_moysklad_moment

    compact, rule = _normalize_moysklad_moment(raw)
    if raw and (MS_MOMENT_LOG or logger.isEnabledFor(logging.DEBUG)):
        logger.log(
            logging.INFO if MS_MOMENT_LOG else logging.DEBUG,
            "MoySklad moment raw=%r → stored=%r (%s)",
            raw,
            compact,
            rule,
        )
    return compact


def parse_demand(data: dict) -> dict:
    """Parse MoySklad demand (shipment) response."""
    agent = data.get("agent", {}) or {}
    agent_phone = agent.get("phone", "") or ""
    owner = data.get("owner") or {}
    owner_name = _person_name(owner)
    warehouse_name = _embedded_name(data.get("store") or {})
    rate = data.get("rate")

    pos_block = data.get("positions") or {}
    rows = pos_block.get("rows", []) if isinstance(pos_block, dict) else []
    items = []
    for pos in rows:
        assortment = pos.get("assortment", {}) or {}
        qty = pos.get("quantity", 0) or 0
        # Двойная картина: price/total остаются в валюте по умолчанию
        # (USD) — для совместимости с базой и старыми вызовами,
        # price_original/total_original в валюте документа — для отображения.
        price = _to_default_amount(pos.get("price"), rate)
        total = round(qty * price, 2)
        price_original = _to_original_amount(pos.get("price"))
        total_original = round(qty * price_original, 2)
        unit_name = (assortment.get("uom") or {}).get("name") or ""
        items.append({
            "name": assortment.get("name", "—"),
            "code": (assortment.get("article") or assortment.get("code") or "").strip(),
            "quantity": qty,
            "uom": unit_name,
            "price": price,
            "total": total,
            "price_original": price_original,
            "total_original": total_original,
        })

    total_sum = _to_default_amount(data.get("sum"), rate)
    total_sum_original = _to_original_amount(data.get("sum"))
    currency_name = _doc_currency_name(rate)

    seller_name = _demand_seller_name(data, owner_name)

    return {
        "moysklad_id": data.get("id", ""),
        "shipment_number": data.get("name", ""),
        "moment": _moment_compact_storage(data.get("moment")),
        "status": _state_name(data),
        "agent_name": agent.get("name", ""),
        "agent_phone": agent_phone,
        "agent_href": agent.get("meta", {}).get("href", ""),
        "agent_id": _extract_id(agent.get("meta", {}).get("href", "")),
        "total_usd": total_sum,
        "total_original": total_sum_original,
        "currency": currency_name,
        "items": items,
        "owner_name": owner_name,
        "seller_name": seller_name,
        "warehouse_name": warehouse_name,
    }


def parse_salesreturn(data: dict) -> dict:
    """Parse MoySklad salesreturn (возврат покупателя) response."""
    agent = data.get("agent", {}) or {}
    agent_phone = agent.get("phone", "") or ""
    owner = data.get("owner") or {}
    owner_name = _person_name(owner)
    warehouse_name = _embedded_name(data.get("store") or {})
    rate = data.get("rate")

    pos_block = data.get("positions") or {}
    rows = pos_block.get("rows", []) if isinstance(pos_block, dict) else []
    items = []
    for pos in rows:
        assortment = pos.get("assortment", {}) or {}
        qty = pos.get("quantity", 0) or 0
        # Двойная картина: price/total остаются в валюте по умолчанию
        # (USD) — для совместимости с базой и старыми вызовами,
        # price_original/total_original в валюте документа — для отображения.
        price = _to_default_amount(pos.get("price"), rate)
        total = round(qty * price, 2)
        price_original = _to_original_amount(pos.get("price"))
        total_original = round(qty * price_original, 2)
        unit_name = (assortment.get("uom") or {}).get("name") or ""
        items.append({
            "name": assortment.get("name", "—"),
            "code": (assortment.get("article") or assortment.get("code") or "").strip(),
            "quantity": qty,
            "uom": unit_name,
            "price": price,
            "total": total,
            "price_original": price_original,
            "total_original": total_original,
        })

    total_sum = _to_default_amount(data.get("sum"), rate)
    total_sum_original = _to_original_amount(data.get("sum"))
    currency_name = _doc_currency_name(rate)

    return {
        "moysklad_id": data.get("id", ""),
        "return_number": data.get("name", ""),
        "moment": _moment_compact_storage(data.get("moment")),
        "status": _state_name(data),
        "agent_name": agent.get("name", ""),
        "agent_phone": agent_phone,
        "agent_href": agent.get("meta", {}).get("href", ""),
        "agent_id": _extract_id(agent.get("meta", {}).get("href", "")),
        "total_usd": total_sum,
        "total_original": total_sum_original,
        "currency": currency_name,
        "items": items,
        "owner_name": owner_name,
        "warehouse_name": warehouse_name,
    }


def parse_supply(data: dict) -> dict:
    """Parse MoySklad supply (приёмка) response.

    `agent` — поставщик. Структура совпадает с demand/salesreturn,
    в качестве идентификатора документа используем supply_number.
    """
    agent = data.get("agent", {}) or {}
    agent_phone = agent.get("phone", "") or ""
    owner = data.get("owner") or {}
    owner_name = _person_name(owner)
    warehouse_name = _embedded_name(data.get("store") or {})
    rate = data.get("rate")

    pos_block = data.get("positions") or {}
    rows = pos_block.get("rows", []) if isinstance(pos_block, dict) else []
    items = []
    for pos in rows:
        assortment = pos.get("assortment", {}) or {}
        qty = pos.get("quantity", 0) or 0
        # Двойная картина: price/total остаются в валюте по умолчанию
        # (USD) — для совместимости с базой и старыми вызовами,
        # price_original/total_original в валюте документа — для отображения.
        price = _to_default_amount(pos.get("price"), rate)
        total = round(qty * price, 2)
        price_original = _to_original_amount(pos.get("price"))
        total_original = round(qty * price_original, 2)
        unit_name = (assortment.get("uom") or {}).get("name") or ""
        items.append({
            "name": assortment.get("name", "—"),
            "code": (assortment.get("article") or assortment.get("code") or "").strip(),
            "quantity": qty,
            "uom": unit_name,
            "price": price,
            "total": total,
            "price_original": price_original,
            "total_original": total_original,
        })

    total_sum = _to_default_amount(data.get("sum"), rate)
    total_sum_original = _to_original_amount(data.get("sum"))
    currency_name = _doc_currency_name(rate)

    return {
        "moysklad_id": data.get("id", ""),
        "supply_number": data.get("name", ""),
        "moment": _moment_compact_storage(data.get("moment")),
        "status": _state_name(data),
        "agent_name": agent.get("name", ""),
        "agent_phone": agent_phone,
        "agent_href": agent.get("meta", {}).get("href", ""),
        "agent_id": _extract_id(agent.get("meta", {}).get("href", "")),
        "total_usd": total_sum,
        "total_original": total_sum_original,
        "currency": currency_name,
        "items": items,
        "owner_name": owner_name,
        "warehouse_name": warehouse_name,
    }


def parse_purchasereturn(data: dict) -> dict:
    """Parse MoySklad purchasereturn (возврат поставщику) response."""
    agent = data.get("agent", {}) or {}
    agent_phone = agent.get("phone", "") or ""
    owner = data.get("owner") or {}
    owner_name = _person_name(owner)
    warehouse_name = _embedded_name(data.get("store") or {})
    rate = data.get("rate")

    pos_block = data.get("positions") or {}
    rows = pos_block.get("rows", []) if isinstance(pos_block, dict) else []
    items = []
    for pos in rows:
        assortment = pos.get("assortment", {}) or {}
        qty = pos.get("quantity", 0) or 0
        # Двойная картина: price/total остаются в валюте по умолчанию
        # (USD) — для совместимости с базой и старыми вызовами,
        # price_original/total_original в валюте документа — для отображения.
        price = _to_default_amount(pos.get("price"), rate)
        total = round(qty * price, 2)
        price_original = _to_original_amount(pos.get("price"))
        total_original = round(qty * price_original, 2)
        unit_name = (assortment.get("uom") or {}).get("name") or ""
        items.append({
            "name": assortment.get("name", "—"),
            "code": (assortment.get("article") or assortment.get("code") or "").strip(),
            "quantity": qty,
            "uom": unit_name,
            "price": price,
            "total": total,
            "price_original": price_original,
            "total_original": total_original,
        })

    total_sum = _to_default_amount(data.get("sum"), rate)
    total_sum_original = _to_original_amount(data.get("sum"))
    currency_name = _doc_currency_name(rate)

    return {
        "moysklad_id": data.get("id", ""),
        "return_number": data.get("name", ""),
        "moment": _moment_compact_storage(data.get("moment")),
        "status": _state_name(data),
        "agent_name": agent.get("name", ""),
        "agent_phone": agent_phone,
        "agent_href": agent.get("meta", {}).get("href", ""),
        "agent_id": _extract_id(agent.get("meta", {}).get("href", "")),
        "total_usd": total_sum,
        "total_original": total_sum_original,
        "currency": currency_name,
        "items": items,
        "owner_name": owner_name,
        "warehouse_name": warehouse_name,
    }


def _state_name(data: dict) -> str:
    state = data.get("state", {}) or {}
    return state.get("name", "Noma'lum")


def _extract_id(href: str) -> str:
    return href.rstrip("/").split("/")[-1] if href else ""


def format_moment(moment: str) -> str:
    """Buyurtmalar ro'yxati: sana + soat (bot va PDF bilan bir xil)."""
    from formatting import fmt_datetime_display

    return fmt_datetime_display(moment)


# ─── Daily report aggregation ───────────────────────────────────────────────
# MoySklad хранит документы со временем в МСК (Europe/Moscow). Фильтр по
# `moment` принимает наивную строку, без таймзоны, в МСК. Для дневного отчёта
# рамки сначала строятся в Asia/Tashkent, потом переводятся в МСК.

async def aggregate_documents(
    entity_type: str,
    *,
    moment_from_msk: str,
    moment_to_msk: str,
) -> tuple[int, float]:
    """Считать count и сумму (в валюте по умолчанию аккаунта) документов за интервал.

    Документ может быть оформлен в любой валюте (например, в сумах при
    основной USD). MoySklad возвращает поле `sum` в минимальных единицах
    валюты документа, а в `rate.value` — коэффициент пересчёта в валюту по
    умолчанию (`amount_default = amount_doc * rate.value`). Если rate.value
    отсутствует — документ в валюте по умолчанию (множитель 1).

    entity_type: customerorder | demand | retaildemand | paymentin | cashin |
                 paymentout | cashout | supply | purchasereturn | salesreturn
    Возвращает (count, total_default_currency).
    """
    url = f"{MOYSKLAD_API}/entity/{entity_type}"
    flt = f"moment>={moment_from_msk};moment<={moment_to_msk}"
    page = 1000
    offset = 0
    total_count = 0
    total_default = 0.0
    sample_logged = False
    while True:
        params = {"filter": flt, "limit": page, "offset": offset, "order": "moment,asc"}
        try:
            data = await _get(url, params=params)
        except Exception as e:
            logger.error("aggregate_documents %s failed: %s", entity_type, e)
            break
        rows = data.get("rows") or []
        if offset == 0:
            total_count = int(((data.get("meta") or {}).get("size")) or 0)
        if rows and not sample_logged:
            sample = rows[0]
            logger.info(
                "aggregate_documents %s sample: sum=%s rate=%s",
                entity_type, sample.get("sum"), sample.get("rate"),
            )
            sample_logged = True
        for r in rows:
            sum_minor = float(r.get("sum") or 0)
            rate = r.get("rate") or {}
            rate_value = rate.get("value")
            try:
                factor = float(rate_value) if rate_value is not None else 1.0
            except (TypeError, ValueError):
                factor = 1.0
            total_default += (sum_minor / 100.0) * factor
        if len(rows) < page:
            break
        offset += page
        if offset >= 50000:  # safety cap
            logger.warning("aggregate_documents %s hit safety cap", entity_type)
            break
    return total_count, round(total_default, 2)


# ─── Receivables / debt aging (P&L под продажи в долг) ───────────────────────
# MoySklad не даёт готового отчёта по дебиторке с разбивкой по срокам, но у
# каждой отгрузки (demand) есть поля `sum` (начислено) и `payedSum` (оплачено).
# Дебиторка документа = sum − payedSum. Срок оплаты у клиентов разный —
# берём его из доп. поля отгрузки «Срок оплаты» (в днях), иначе — дефолт.

def _payment_term_attribute_label_keys() -> tuple[str, ...]:
    """Подписи доп. поля отгрузки, задающего срок оплаты в днях."""
    return (
        "срок оплаты",
        "срок",
        "отсрочка",
        "to'lov muddati",
        "tolov muddati",
        "to‘lov muddati",
        "muddat",
        "payment term",
        "credit term",
        "term",
    )


def _demand_term_days(data: dict, default_days: int) -> int:
    """Срок оплаты (дни) из доп. поля отгрузки; иначе — default_days.

    Значение может быть числом (30) или строкой ('30 дней') — вытаскиваем
    первое целое. Ноль/отрицательное игнорируем, откатываясь на дефолт.
    """
    attrs = data.get("attributes") or []
    if isinstance(attrs, list):
        keys = _payment_term_attribute_label_keys()
        for a in attrs:
            if not isinstance(a, dict):
                continue
            label = (a.get("name") or "").strip().lower()
            if not label or not any(k in label for k in keys):
                continue
            val = a.get("value")
            if isinstance(val, bool) or val is None:
                continue
            try:
                n = int(float(val))
            except (TypeError, ValueError):
                digits = "".join(c for c in str(val) if c.isdigit())
                n = int(digits) if digits else 0
            if n > 0:
                return n
    return default_days


def _aging_bucket(overdue_days: int) -> str:
    """Зона старения долга по числу дней просрочки (см. отчёт «Дебиторка»)."""
    if overdue_days <= 0:
        return "current"
    if overdue_days <= 7:
        return "d1_7"
    if overdue_days <= 30:
        return "d8_30"
    if overdue_days <= 90:
        return "d31_90"
    return "d90_plus"


AGING_BUCKET_KEYS: tuple[str, ...] = ("current", "d1_7", "d8_30", "d31_90", "d90_plus")


async def aggregate_receivables(
    *,
    moment_from_msk: str,
    moment_to_msk: str,
    today_local,
    default_term_days: int,
    min_remainder: float = 0.01,
    max_scan: int = 50000,
) -> dict:
    """Собрать дебиторку по отгрузкам (продажам в долг) за интервал.

    Для каждой отгрузки: начислено = sum, оплачено = payedSum (оба в валюте
    по умолчанию аккаунта через rate.value), остаток = начислено − оплачено.
    Просрочка = (сегодня − дата отгрузки) − срок оплаты. Документы с остатком
    ниже `min_remainder` считаются закрытыми и в дебиторку не попадают.

    Возвращает словарь:
      accrued / collected / receivable — суммы Σsum, ΣpayedSum, Σостаток;
      buckets — {зона: {count, total}} по AGING_BUCKET_KEYS;
      rows — список строк по документам, отсортирован по просрочке убыв.;
      doc_count / debtor_count — число открытых документов и должников.
    """
    from datetime import datetime as _dt

    url = f"{MOYSKLAD_API}/entity/demand"
    flt = f"moment>={moment_from_msk};moment<={moment_to_msk}"
    page = 1000
    offset = 0

    buckets: dict[str, dict] = {b: {"count": 0, "total": 0.0} for b in AGING_BUCKET_KEYS}
    rows_out: list[dict] = []
    accrued = 0.0
    collected = 0.0
    receivable = 0.0
    debtor_ids: set[str] = set()

    while True:
        params = {
            "filter": flt,
            "limit": page,
            "offset": offset,
            "order": "moment,asc",
            "expand": "agent,attributes,rate.currency",
        }
        try:
            data = await _get(url, params=params)
        except Exception as e:
            logger.error("aggregate_receivables demand fetch failed: %s", e)
            break
        rows = data.get("rows") or []
        for r in rows:
            rate = r.get("rate") or {}
            sum_def = _to_default_amount(r.get("sum"), rate)
            paid_def = _to_default_amount(r.get("payedSum"), rate)
            accrued += sum_def
            collected += paid_def
            remainder = round(sum_def - paid_def, 2)
            if remainder < min_remainder:
                continue
            receivable += remainder

            agent = r.get("agent") or {}
            agent_id = _extract_id((agent.get("meta") or {}).get("href", ""))
            if agent_id:
                debtor_ids.add(agent_id)

            term = _demand_term_days(r, default_term_days)
            moment_raw = r.get("moment") or ""
            try:
                doc_date = _dt.strptime(moment_raw[:10], "%Y-%m-%d").date()
                days_since = (today_local - doc_date).days
            except (ValueError, TypeError):
                days_since = 0
            overdue = days_since - term
            bkey = _aging_bucket(overdue)
            buckets[bkey]["count"] += 1
            buckets[bkey]["total"] += remainder

            rows_out.append({
                "client": (agent.get("name") or "—"),
                "agent_id": agent_id,
                "doc": r.get("name") or "",
                "date": moment_raw[:10],
                "sum": sum_def,
                "paid": paid_def,
                "remainder": remainder,
                "term": term,
                "days_since": days_since,
                "overdue": max(0, overdue),
                "currency": _doc_currency_name(rate),
            })
        if len(rows) < page:
            break
        offset += page
        if offset >= max_scan:
            logger.warning("aggregate_receivables hit scan cap %d", max_scan)
            break

    for b in buckets.values():
        b["total"] = round(b["total"], 2)
    # Самые проблемные — сверху: сначала по просрочке, затем по остатку.
    rows_out.sort(key=lambda x: (x["overdue"], x["remainder"]), reverse=True)

    return {
        "accrued": round(accrued, 2),
        "collected": round(collected, 2),
        "receivable": round(receivable, 2),
        "buckets": buckets,
        "rows": rows_out,
        "doc_count": len(rows_out),
        "debtor_count": len(debtor_ids),
    }


async def count_new_counterparties(
    *, created_from_msk: str, created_to_msk: str
) -> int:
    """Сколько контрагентов создано в МойСклад за интервал (МСК-границы)."""
    url = f"{MOYSKLAD_API}/entity/counterparty"
    flt = f"created>={created_from_msk};created<={created_to_msk}"
    try:
        data = await _get(url, params={"filter": flt, "limit": 1})
    except Exception as e:
        logger.error("count_new_counterparties failed: %s", e)
        return 0
    return int(((data.get("meta") or {}).get("size")) or 0)
