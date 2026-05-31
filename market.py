"""
Encuentra y devuelve los token IDs de Up y Down del mercado BTC 15m activo.
"""
import requests
from datetime import datetime, timezone
from config import CLOB_HOST, MARKET_SLUG_CONTAINS, MIN_MINUTES_REMAINING


def get_active_btc_market() -> dict | None:
    """
    Consulta la API Gamma de Polymarket y retorna el mercado BTC 15m
    que esté activo y tenga suficiente tiempo restante.
    """
    url = "https://gamma-api.polymarket.com/markets"
    params = {
        "slug_contains": MARKET_SLUG_CONTAINS,
        "active": "true",
        "closed": "false",
        "limit": 10,
    }
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    markets = resp.json()

    now = datetime.now(timezone.utc)
    for m in markets:
        end_ts = m.get("endDate") or m.get("end_date_iso")
        if not end_ts:
            continue
        end_dt = datetime.fromisoformat(end_ts.replace("Z", "+00:00"))
        minutes_left = (end_dt - now).total_seconds() / 60
        if minutes_left >= MIN_MINUTES_REMAINING:
            return _parse_market(m, minutes_left)

    return None


def _parse_market(m: dict, minutes_left: float) -> dict:
    """Extrae token IDs de Up y Down del objeto de mercado."""
    tokens = m.get("tokens", m.get("clob_token_ids", []))

    up_token = down_token = None
    for t in tokens:
        outcome = (t.get("outcome") or "").lower()
        if outcome == "up":
            up_token = t["token_id"]
        elif outcome == "down":
            down_token = t["token_id"]

    # Fallback si los tokens vienen como lista plana de IDs
    if not up_token and len(tokens) >= 2:
        up_token   = tokens[0] if isinstance(tokens[0], str) else tokens[0]["token_id"]
        down_token = tokens[1] if isinstance(tokens[1], str) else tokens[1]["token_id"]

    return {
        "condition_id": m.get("conditionId") or m.get("condition_id"),
        "question":     m.get("question", "BTC Up/Down 15m"),
        "end_date":     m.get("endDate") or m.get("end_date_iso"),
        "minutes_left": round(minutes_left, 1),
        "up_token":     up_token,
        "down_token":   down_token,
    }


def get_best_ask(token_id: str) -> float | None:
    """Retorna el mejor ask (precio más bajo de venta) para un token."""
    url = f"{CLOB_HOST}/book"
    resp = requests.get(url, params={"token_id": token_id}, timeout=10)
    resp.raise_for_status()
    book = resp.json()
    asks = book.get("asks", [])
    if not asks:
        return None
    return float(min(asks, key=lambda x: float(x["price"]))["price"])
