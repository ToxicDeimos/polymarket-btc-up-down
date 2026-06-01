"""
Encuentra el mercado BTC Up/Down 15m activo construyendo el slug
directamente desde el timestamp UTC actual.

Patrón confirmado: slug = btc-updown-15m-{unix_start_of_window}
donde unix_start = inicio de la ventana actual redondeado al cuarto de hora UTC.
"""
import json
import requests
from datetime import datetime, timezone, timedelta
from config import CLOB_HOST, MIN_MINUTES_REMAINING, MAX_MINUTES_REMAINING

GAMMA_URL  = "https://gamma-api.polymarket.com/markets"
SLUG_PREFIX = "btc-updown-15m"


def current_window_start_ts() -> int:
    """Unix timestamp del inicio de la ventana de 15 min en curso."""
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    # Redondear hacia abajo al cuarto de hora más cercano
    aligned_minute = (now.minute // 15) * 15
    return int(now.replace(minute=aligned_minute).timestamp())


def next_window_start_ts() -> int:
    """Unix timestamp del inicio de la siguiente ventana."""
    return current_window_start_ts() + 900   # +15 min


def get_active_btc_market() -> dict | None:
    """
    Busca el mercado BTC 15m de la ventana actual por slug exacto.
    Fallback: también prueba la ventana anterior (por si la API va con retraso).
    """
    for offset in [0, -900, 900]:   # actual, anterior, siguiente
        ts   = current_window_start_ts() + offset
        slug = f"{SLUG_PREFIX}-{ts}"
        market = _fetch_by_slug(slug)
        if market:
            now = datetime.now(timezone.utc)
            end_dt = datetime.fromisoformat(market["end_date"].replace("Z", "+00:00"))
            minutes_left = (end_dt - now).total_seconds() / 60
            if MIN_MINUTES_REMAINING <= minutes_left <= MAX_MINUTES_REMAINING:
                market["minutes_left"] = round(minutes_left, 1)
                return market

    return None


def _fetch_by_slug(slug: str) -> dict | None:
    """Obtiene un mercado por slug exacto desde la API Gamma."""
    try:
        resp = requests.get(GAMMA_URL, params={"slug": slug}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return None
        return _parse_market(data[0])
    except Exception:
        return None


def _parse_market(m: dict) -> dict | None:
    """Extrae tokens Up/Down del objeto de mercado."""
    raw_tokens   = m.get("clobTokenIds", "[]")
    raw_outcomes = m.get("outcomes",     "[]")

    token_ids = json.loads(raw_tokens)   if isinstance(raw_tokens,   str) else raw_tokens
    outcomes  = json.loads(raw_outcomes) if isinstance(raw_outcomes, str) else raw_outcomes

    if not token_ids or not outcomes:
        return None

    up_token = down_token = None
    for token_id, outcome in zip(token_ids, outcomes):
        if outcome.lower() == "up":
            up_token   = token_id
        elif outcome.lower() == "down":
            down_token = token_id

    if not up_token or not down_token:
        return None

    end_date = m.get("endDate") or m.get("endDateIso", "")

    return {
        "condition_id": m.get("conditionId") or m.get("condition_id", ""),
        "question":     m.get("question", "BTC Up/Down 15m"),
        "end_date":     end_date,
        "minutes_left": 0,   # se rellena en get_active_btc_market
        "up_token":     up_token,
        "down_token":   down_token,
    }


def get_best_ask(token_id: str) -> float | None:
    """Retorna el mejor ask del libro de órdenes CLOB para un token."""
    try:
        resp = requests.get(f"{CLOB_HOST}/book",
                            params={"token_id": token_id}, timeout=10)
        resp.raise_for_status()
        asks = resp.json().get("asks", [])
        if not asks:
            return None
        return float(min(asks, key=lambda x: float(x["price"]))["price"])
    except Exception:
        return None
