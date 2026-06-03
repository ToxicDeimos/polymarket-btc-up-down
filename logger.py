"""
Gestiona los archivos de datos del bot:

prices.csv   → snapshot de precios cada 15s (bid/ask de ambos lados)
results.csv  → resumen por ventana con winner resuelto

La resolución en Polymarket tarda 1-5 min tras cerrar la ventana.
resolve_pending() se llama al inicio de cada ciclo para rellenar
los winners que quedaron como "pending".
"""
import csv
import json
import os
import requests
from datetime import datetime, timezone
from config import CLOB_HOST, TAKER_FEE_RATE

PRICES_FILE  = os.path.join(os.path.dirname(__file__), "prices.csv")
RESULTS_FILE = os.path.join(os.path.dirname(__file__), "results.csv")


def ensure_files():
    if not os.path.exists(PRICES_FILE):
        with open(PRICES_FILE, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([
                "timestamp_utc", "condition_id", "question",
                "seconds_elapsed", "seconds_remaining",
                "up_ask", "down_ask", "up_bid", "down_bid",
            ])

    if not os.path.exists(RESULTS_FILE):
        with open(RESULTS_FILE, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(RESULTS_COLUMNS)
    else:
        _migrate_results_columns()


# Campos de PREDICCIÓN del Brain en el momento de entrar. Permiten calibrar:
# comparar lo que el Brain predijo (p_true, edge) contra el resultado real.
PRED_FIELDS = [
    "entry_edge_type",     # open_window | oracle_lag
    "entry_p_true",        # P(lado apostado gana) según el Brain → CLAVE de calibración
    "entry_edge",          # ventaja modelo vs mercado (p_true - precio)
    "entry_price",         # precio de señal (vs fill real → slippage)
    "entry_cl_diff",       # chainlink_now - chainlink_open al entrar
    "entry_spot_diff",     # binance_now - binance_open al entrar
    "entry_secs_elapsed",  # segundos transcurridos al entrar
    "entry_secs_left",     # segundos restantes al entrar
    "entry_vol",           # vol $/s que usó el Brain en P(Up)
]

# Esquema canónico de results.csv. Las columnas trend_* y entry_* se añaden
# AL FINAL para no romper filas antiguas. La migración las rellena vacías.
RESULTS_COLUMNS = [
    "timestamp_utc", "condition_id", "question",
    "window_start_et", "window_end_et",
    "mode",
    "up_ask_open", "down_ask_open",
    "up_ask_close", "down_ask_close",
    "up_filled", "down_filled",
    "up_fill_price", "down_fill_price",
    "winner", "profit", "total_profit",
    "minutes_active",
    "trend_dir", "trend_strength",
] + PRED_FIELDS


def _migrate_results_columns() -> None:
    """
    Añade las columnas nuevas (trend_*, entry_*) a un results.csv viejo,
    rellenando vacío en las filas existentes. Idempotente: si ya están, no hace nada.
    """
    try:
        with open(RESULTS_FILE, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames or []
            missing = [c for c in RESULTS_COLUMNS if c not in header]
            if not missing:
                return
            rows = list(reader)
        for r in rows:
            for c in missing:
                r.setdefault(c, "")
        with open(RESULTS_FILE, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=RESULTS_COLUMNS)
            w.writeheader()
            w.writerows(rows)
        print(f"  [Logger] results.csv migrado: +{', '.join(missing)}")
    except Exception as e:
        print(f"  [Logger] migración omitida: {e}")


def log_price_snapshot(condition_id, question,
                       seconds_elapsed, seconds_remaining,
                       up_book, down_book):
    with open(PRICES_FILE, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            condition_id, question,
            round(seconds_elapsed, 1), round(seconds_remaining, 1),
            _best_ask(up_book), _best_ask(down_book),
            _best_bid(up_book), _best_bid(down_book),
        ])


def _sum_profits() -> float:
    """Suma todos los 'profit' del CSV. Fuente única de verdad para el acumulado."""
    if not os.path.exists(RESULTS_FILE):
        return 0.0
    total = 0.0
    try:
        with open(RESULTS_FILE, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    total += float(r.get("profit") or 0)
                except (ValueError, TypeError):
                    pass
    except Exception:
        pass
    return round(total, 2)


def log_cycle_result(condition_id, question,
                     up_ask_open, down_ask_open,
                     up_ask_close, down_ask_close,
                     up_filled, down_filled,
                     minutes_active,
                     winner: str = "pending",
                     mode: str = "",
                     profit: float = 0.0,
                     up_fill_price=None, down_fill_price=None,
                     trend_dir: str = "", trend_strength=None,
                     pred: dict | None = None) -> str:
    """
    Guarda el resumen de la ventana en results.csv.
    Incluye el precio REAL de entrada (fill_price) para poder recalcular el
    P&L con exactitud si el ganador se resuelve más tarde.
    """

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # Extraer horas del título: "Bitcoin Up or Down - May 31, 12:00PM-12:15PM ET"
    # → window_start="12:00PM", window_end="12:15PM"
    import re
    time_match = re.search(r'(\d+:\d+[AP]M)-(\d+:\d+[AP]M)', question)
    if time_match:
        window_start = time_match.group(1)
        window_end   = time_match.group(2)
    else:
        parts = question.split(" - ")
        window_time  = parts[-1] if len(parts) > 1 else ""
        window_start = window_time.split("-")[0].strip()
        window_end   = window_time.split("-")[-1].replace(" ET", "").strip()

    # Asegurar que el archivo termina en newline antes de añadir
    if os.path.exists(RESULTS_FILE):
        with open(RESULTS_FILE, "rb+") as f:
            f.seek(0, 2)
            if f.tell() > 0:
                f.seek(-1, 2)
                if f.read(1) not in (b'\n', b'\r'):
                    f.write(b'\n')

    # Evitar duplicados: si ya existe una fila con este condition_id, no guardar
    if os.path.exists(RESULTS_FILE):
        with open(RESULTS_FILE, encoding="utf-8") as f:
            existing = [r.get("condition_id","") for r in csv.DictReader(f)]
        if condition_id in existing:
            print(f"  [Logger] Ventana ya registrada, omitiendo duplicado")
            return winner

    # Acumulado = suma de todos los profits previos + este (fuente única de verdad)
    running_total = round(_sum_profits() + profit, 2)

    with open(RESULTS_FILE, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([
            now, condition_id, question,
            window_start, window_end,
            mode,
            up_ask_open, down_ask_open,
            up_ask_close, down_ask_close,
            up_filled, down_filled,
            up_fill_price, down_fill_price,
            winner, round(profit, 2), running_total,
            round(minutes_active, 1),
            trend_dir or "",
            round(trend_strength, 3) if trend_strength is not None else "",
            *[(pred or {}).get(k, "") for k in PRED_FIELDS],
        ])

    print(f"  Resolución guardada: {winner}")
    return winner


def resolve_pending() -> list[dict]:
    """
    Rellena winners 'pending' usando los precios de cierre ya guardados.
    Lógica: al resolverse el mercado, el lado ganador sube a ~1.0 y el perdedor a ~0.0.
    Si up_ask_close >= 0.85  → Up ganó
    Si down_ask_close >= 0.85 → Down ganó
    Fallback: consulta API Gamma si los precios no son concluyentes.
    """
    if not os.path.exists(RESULTS_FILE):
        return []

    with open(RESULTS_FILE, "r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    # Solo resolver ventanas cerradas hace >=120s: justo tras el cierre el precio
    # oscila (BTC cerca del target) y puede mostrar el lado equivocado transitoriamente.
    # A los 2 min ya está asentado en la resolución real.
    pending = [r for r in rows
               if r.get("winner") == "pending" and _settled(r)]
    if not pending:
        return []

    print(f"  [Logger] Resolviendo {len(pending)} ventanas pendientes…")
    resolved = []

    for row in pending:
        # Fuente de verdad: resolución oficial de Polymarket (no precios locales).
        winner = get_official_winner(row.get("condition_id", ""))
        if winner != "pending":
            row["winner"] = winner
            # Recalcular P&L EXACTO desde el precio real de entrada guardado
            row["profit"] = _profit_from_fills(row, winner)
            resolved.append(row)

    if resolved:
        fieldnames = list(rows[0].keys())
        resolved_map = {r["condition_id"]: (r["winner"], r["profit"]) for r in resolved}
        for row in rows:
            if row["condition_id"] in resolved_map:
                row["winner"], row["profit"] = resolved_map[row["condition_id"]]

        # Recalcular acumulado en orden
        total = 0.0
        for row in rows:
            try:
                total += float(row.get("profit") or 0)
            except (ValueError, TypeError):
                pass
            row["total_profit"] = round(total, 2)

        with open(RESULTS_FILE, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)

        print(f"  [Logger] {len(resolved)}/{len(pending)} ventanas resueltas")

    return resolved


def _profit_from_fills(row: dict, winner: str) -> float:
    """P&L NETO desde el precio real de entrada (fill_price), menos fee de taker."""
    profit = 0.0
    for side, won_side in (("up", "Up"), ("down", "Down")):
        if row.get(f"{side}_filled") == "True":
            try:
                fp = float(row.get(f"{side}_fill_price") or 0)
            except (ValueError, TypeError):
                fp = 0
            if fp > 0:
                profit += (1.0 / fp - 1.0) if winner == won_side else -1.0
                profit -= TAKER_FEE_RATE * 1.0 * (1 - fp)   # fee market order ($1)
    return round(profit, 2)


def _infer_winner_from_prices(row: dict) -> str:
    """
    Infiere el ganador a partir de los precios de cierre.
    Cuando el mercado resuelve, el lado ganador se pone en ~1.0 y el perdedor en ~0.0.
    Umbral 0.80 para ser robustos ante precios capturados justo al cierre.
    """
    try:
        up_close   = float(row.get("up_ask_close")   or 0)
        down_close = float(row.get("down_ask_close") or 0)
    except (ValueError, TypeError):
        return "pending"

    if up_close >= 0.80:
        return "Up"
    if down_close >= 0.80:
        return "Down"
    # Precios cercanos a 50/50 al cierre — mercado aún no resuelto
    return "pending"


# ── Resolución de mercados ────────────────────────────────────────────────────

def _poll_winner(condition_id: str, attempts: int = 8, wait_secs: int = 30) -> str:
    """
    Reintenta obtener el winner hasta `attempts` veces con pausa entre intentos.
    Polymarket tarda 1-3 min en resolver tras el cierre de ventana.
    """
    import time
    for attempt in range(attempts):
        winner = _get_winner(condition_id)
        if winner and winner != "pending":
            if attempt > 0:
                print(f"\n  Resuelto en intento {attempt+1}: {winner}")
            return winner
        if attempt < attempts - 1:
            print(f"  Esperando resolución… ({attempt+1}/{attempts}) "
                  f"reintento en {wait_secs}s", end="\r")
            time.sleep(wait_secs)
    print()
    return "pending"


def _settled(row: dict, min_secs: int = 120) -> bool:
    """True si la ventana cerró hace >= min_secs (precio ya asentado, no volátil)."""
    try:
        ts = datetime.strptime(row.get("timestamp_utc", ""), "%Y-%m-%d %H:%M:%S")
        ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds() >= min_secs
    except Exception:
        return True   # sin timestamp fiable → no bloquear


def get_official_winner(condition_id: str) -> str:
    """
    Ganador OFICIAL de Polymarket vía CLOB API (por condition_id).
    El endpoint devuelve cada token con un flag `winner` booleano — la fuente
    de verdad definitiva. Funciona tanto justo tras el cierre como días después.

    NO determinar el ganador con lecturas propias de Chainlink: el bot cierra
    unos segundos antes y se pierde movimientos de último segundo (ej. la
    ventana 1:15-1:30 resolvió Up mientras nuestra lectura decía Down).

    Retorna "Up" | "Down" | "pending".
    """
    if not condition_id:
        return "pending"
    try:
        r = requests.get(f"{CLOB_HOST}/markets/{condition_id}", timeout=8)
        if r.ok:
            for t in r.json().get("tokens", []):
                # El flag `winner` es definitivo pero tarda mucho. El PRECIO del
                # token converge a ~0.995 en pocos minutos tras el cierre — y como
                # solo consultamos DESPUÉS del cierre, un precio ≥0.95 = resolución.
                price = float(t.get("price") or 0)
                if t.get("winner") is True or price >= 0.95:
                    return t.get("outcome")   # "Up" o "Down"
    except Exception:
        pass
    return "pending"   # aún no resuelto (precios sin converger)


# Alias interno usado por resolve_pending
_get_winner = get_official_winner


# ── Helpers de libro de órdenes ───────────────────────────────────────────────

def _best_ask(book: dict) -> float | None:
    asks = book.get("asks", [])
    return float(min(asks, key=lambda x: float(x["price"]))["price"]) if asks else None


def _best_bid(book: dict) -> float | None:
    bids = book.get("bids", [])
    return float(max(bids, key=lambda x: float(x["price"]))["price"]) if bids else None
