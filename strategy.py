"""
Estrategia BTC Up/Down 15m — Direccional.

Cada ventana se monitorea y el Brain decide si entrar en UN solo lado cuando
detecta un edge real (lag Chainlink/Binance). Si no hay edge, no se opera.

  DIRECCIONAL : el Brain busca un edge durante la ventana y apuesta un lado.
  SKIP        : no se pueden leer precios → no se opera.

(El arbitrage de doble límite 40¢ se eliminó: el mercado cierra a extremo el
 88% de las veces — no oscila — así que nunca se completaba. Ver README.)
"""
import json
import os
import time
import math
from datetime import datetime, timezone
from dataclasses import dataclass, field

import requests

from market import get_active_btc_market
from executor import build_client, place_market_order
from data_feed import get_chainlink_price, get_btc_spot, get_btc_trend
from brain import Brain, Signal
from logger import (ensure_files, log_price_snapshot, log_cycle_result,
                    resolve_pending)
from config import POLL_INTERVAL, ORDER_SIZE_USDC, DRY_RUN, CLOB_HOST, TAKER_FEE_RATE

# Movimiento spot mínimo en la apertura para vigilar la ventana (si no, skip)
DIRECTIONAL_MOVE = 40.0

# Latencia estimada de ejecución real (firma + envío de la orden al CLOB).
# Se simula también en dry para que el fill refleje el precio ~2s después.
EXEC_LATENCY_SECS = 2

# Filtro de tendencia: solo apostar a favor de la tendencia mayor (EMA 7/25 en 15m).
# Corta las apuestas contra-tendencia (rebotes) que son la mayor sangría.
TREND_FILTER = True

# Fuerza mínima de tendencia (% separación EMA) para operar. Por debajo = lateral,
# donde la estrategia no tiene edge (no hay continuación que explotar) → skip.
# Conservador: 0.10% (la bajada actual da ~0.49%), solo salta el lateral evidente.
MIN_TREND_STRENGTH = 0.10


@dataclass
class CycleState:
    condition_id:   str
    question:       str
    end_date:       str
    up_token:       str
    down_token:     str
    mode:           str   = ""      # "directional" | "skip"
    trend:          str | None = None   # "up" | "down" — tendencia mayor (filtro)
    trend_strength: float = 0.0         # % separación EMA (fuerza de la tendencia)
    cl_open:        float = 0.0
    spot_open:      float = 0.0
    up_ask_open:    float | None = None
    down_ask_open:  float | None = None
    up_ask_close:   float | None = None
    down_ask_close: float | None = None
    minutes_active: float = 0.0
    up_order_id:    str | None = None
    down_order_id:  str | None = None
    up_filled:      bool  = False
    down_filled:    bool  = False
    up_fill_price:  float | None = None
    down_fill_price: float | None = None
    entry_vol:      float = 0.0          # vol $/s del Brain al entrar (para calibrar)
    signals: list = field(default_factory=list)
    profit:  float = 0.0


def _compute_profit(state: "CycleState", winner: str) -> float:
    """
    P&L neto de la ventana = resolución de la posición − fee de taker.
    La fee (market order) se paga al entrar: 0.07 × importe × (1 − precio_entrada).
    """
    profit = 0.0
    if state.up_filled and state.up_fill_price:
        shares = ORDER_SIZE_USDC / state.up_fill_price
        profit += (shares - ORDER_SIZE_USDC) if winner == "Up" else -ORDER_SIZE_USDC
        profit -= TAKER_FEE_RATE * ORDER_SIZE_USDC * (1 - state.up_fill_price)
    if state.down_filled and state.down_fill_price:
        shares = ORDER_SIZE_USDC / state.down_fill_price
        profit += (shares - ORDER_SIZE_USDC) if winner == "Down" else -ORDER_SIZE_USDC
        profit -= TAKER_FEE_RATE * ORDER_SIZE_USDC * (1 - state.down_fill_price)
    return round(profit, 2)


def _decide_mode(up_ask: float | None, down_ask: float | None,
                 spot_diff: float) -> str:
    """DIRECTIONAL si hay precios y movimiento spot; SKIP en otro caso."""
    if up_ask is None or down_ask is None:
        return "skip"
    return "directional" if abs(spot_diff) >= DIRECTIONAL_MOVE else "skip"


def run():
    client = build_client()
    brain  = Brain()
    mode_tag = "[DRY RUN]" if DRY_RUN else "[REAL]"

    print("=" * 62)
    print(f"  Polymarket BTC Up/Down 15m — Direccional  {mode_tag}")
    print(f"  El Brain apuesta un lado solo si detecta edge (lag CL/Binance)")
    print("=" * 62)

    ensure_files()

    # Reconstruir el aprendizaje del Brain desde el registro completo (CSV),
    # para que no tenga huecos por reinicios y su threshold se ajuste con datos
    # reales (no con un win rate inflado por las ops que perdió al reiniciar).
    try:
        import csv as _csv
        with open(os.path.join(os.path.dirname(__file__), "results.csv"),
                  encoding="utf-8") as _f:
            brain.sync_from_results(list(_csv.DictReader(_f)))
    except Exception:
        pass

    total_invested = 0.0
    total_profit   = 0.0
    stats = {"directional": 0, "skip": 0}
    cycle = 0
    seen: set = set()
    pending_learn: dict = {}   # condition_id -> CycleState (signals para el Brain)

    while True:
        cycle += 1
        print(f"\n{'─'*62}")
        print(f"  Ciclo #{cycle}  {_now()}")

        # Resolver pendientes ya asentadas (>=2min) y alimentar al Brain
        _resolve_and_learn(brain, pending_learn)

        market = _wait_for_market(seen)
        seen.add(market["condition_id"])

        state = CycleState(**{k: market[k] for k in
            ["condition_id", "question", "end_date", "up_token", "down_token"]})

        # Precios de apertura
        state.cl_open   = get_chainlink_price() or 0.0
        state.spot_open = get_btc_spot()        or 0.0
        spot_diff_open  = state.spot_open - state.cl_open
        if TREND_FILTER:
            state.trend, state.trend_strength = get_btc_trend()
        brain.reset_window()

        up_book   = _get_book(state.up_token)
        down_book = _get_book(state.down_token)
        state.up_ask_open   = _best_ask(up_book)
        state.down_ask_open = _best_ask(down_book)

        state.mode = _decide_mode(state.up_ask_open, state.down_ask_open, spot_diff_open)

        # Gate de FUERZA: si la tendencia es débil (lateral), no hay continuación
        # que explotar → no operar (solo recoger datos).
        ranging = TREND_FILTER and state.trend and state.trend_strength < MIN_TREND_STRENGTH
        if ranging:
            state.mode = "skip"

        print(f"  Mercado  : {state.question}")
        print(f"  CL open  : ${state.cl_open:,.2f}  "
              f"spot open: ${state.spot_open:,.2f}  diff: {spot_diff_open:+.0f}$")
        print(f"  Ask open : UP={state.up_ask_open}  DOWN={state.down_ask_open}")
        print(f"  Tendencia: {(state.trend or '-').upper()} "
              f"(fuerza {state.trend_strength:.2f}%{' — LATERAL, skip' if ranging else ''})")
        print(f"  MODO     : {state.mode.upper()}")
        print(f"  {brain.summary()}")

        active = state.mode == "directional"
        stats[state.mode] += 1
        if not active:
            print(f"  Ventana saltada — sin precios/señal")
        state = _monitor(client, state, brain, active=active,
                         pending_learn=pending_learn)

        # ── Al cierre NO se resuelve (el precio aún oscila) ───────────────────
        # Se guarda como pending y se deja el state para aprender cuando el
        # precio se asiente (~2 min). La resolución la hace _resolve_and_learn,
        # que se llama al inicio del ciclo y a mitad de la ventana siguiente.
        fills = sum([state.up_filled, state.down_filled])
        if fills == 1:
            side = "UP" if state.up_filled else "DOWN"
            print(f"\n  {side} apostado @ "
                  f"{state.up_fill_price or state.down_fill_price:.2f} — pendiente de resolución")
        else:
            print(f"\n  Sin entrada")

        # Predicción del Brain en el momento de entrar (para calibrar después)
        pred = None
        if state.signals:
            s = state.signals[0]
            pred = {
                "entry_edge_type":    s.edge_type,
                "entry_p_true":       round(s.p_true, 4),
                "entry_edge":         round(s.edge, 4),
                "entry_price":        round(s.market_price, 4),
                "entry_cl_diff":      round(s.btc_diff, 1),
                "entry_spot_diff":    round(s.spot_diff, 1),
                "entry_secs_elapsed": round(s.secs_elapsed, 1),
                "entry_secs_left":    round(s.secs_left, 1),
                "entry_vol":          round(state.entry_vol, 4),
            }

        log_cycle_result(
            condition_id=state.condition_id, question=state.question,
            up_ask_open=state.up_ask_open, down_ask_open=state.down_ask_open,
            up_ask_close=state.up_ask_close, down_ask_close=state.down_ask_close,
            up_filled=state.up_filled, down_filled=state.down_filled,
            up_fill_price=state.up_fill_price, down_fill_price=state.down_fill_price,
            minutes_active=state.minutes_active, winner="pending",
            mode=state.mode, profit=0.0,
            trend_dir=state.trend or "", trend_strength=state.trend_strength,
            pred=pred)

        # Guardar para aprender cuando se resuelva (lleva los signals en memoria)
        if state.signals:
            pending_learn[state.condition_id] = state

        time.sleep(2)


# ── Monitor de ventana ────────────────────────────────────────────────────────

def _monitor(client, state: CycleState, brain: Brain, active: bool,
             pending_learn: dict | None = None) -> CycleState:
    """Polling cada POLL_INTERVAL s. active=False → solo recopila datos."""
    end          = datetime.fromisoformat(state.end_date.replace("Z", "+00:00"))
    window_start = datetime.now(timezone.utc)
    last_up  = state.up_ask_open
    last_dn  = state.down_ask_open
    entered  = False   # ya apostamos un lado esta ventana
    trend_blocked = False   # ya avisamos de señal contra-tendencia
    resolved_prev = False   # ya resolvimos la pendiente anterior esta ventana

    while True:
        now          = datetime.now(timezone.utc)
        secs_left    = (end - now).total_seconds()
        secs_elapsed = (now - window_start).total_seconds()

        if secs_left <= 0:
            print("\n  Ventana cerrada.")
            break

        # A ~2.5 min de la ventana, la anterior ya se asentó en Polymarket:
        # resolvemos y aprendemos aquí para no esperar al siguiente ciclo.
        if not resolved_prev and secs_elapsed > 150:
            resolved_prev = True
            _resolve_and_learn(brain, pending_learn if pending_learn is not None else {})

        cl_now   = get_chainlink_price() or state.cl_open
        spot_now = get_btc_spot()        or state.spot_open
        cl_diff   = cl_now   - state.cl_open
        spot_diff = spot_now - state.spot_open

        brain.record_price(cl_now, secs_elapsed)

        up_book   = _get_book(state.up_token)
        down_book = _get_book(state.down_token)
        up_ask = _best_ask(up_book)  or last_up
        dn_ask = _best_ask(down_book) or last_dn
        last_up, last_dn = up_ask, dn_ask

        log_price_snapshot(state.condition_id, state.question,
                           secs_elapsed, secs_left, up_book, down_book,
                           cl_price=cl_now, spot_price=spot_now)
        _write_status(state, cl_now, cl_diff, spot_diff, up_ask, dn_ask, secs_left)

        # ── Evaluación del Brain: una sola apuesta por ventana ────────────────
        if active and not entered:
            signals = brain.evaluate(
                cl_open=state.cl_open,    cl_now=cl_now,
                spot_open=state.spot_open, spot_now=spot_now,
                up_ask=up_ask,             down_ask=dn_ask,
                secs_elapsed=secs_elapsed, secs_left=secs_left,
            )
            if signals:
                sig = signals[0]
                # ── Filtro de tendencia ───────────────────────────────────────
                # Solo apostar a favor de la tendencia mayor. Si la señal va
                # contra ella (un rebote), se salta — son las que pierden.
                contra_tendencia = (TREND_FILTER and state.trend in ("up", "down")
                                    and sig.side != state.trend)
                if contra_tendencia:
                    if not trend_blocked:
                        trend_blocked = True
                        print(f"\n  [Tendencia] señal {sig.side.upper()} contra "
                              f"tendencia {state.trend.upper()} → saltada (rebote)")
                    # no entrar; seguir vigilando por si aparece señal a favor
                else:
                    entered = True
                    state.entry_vol = brain.vol_per_sec   # vol usada en P(Up) al entrar
                    state.signals.append(sig)
                    print(f"\n  [Brain/{sig.edge_type}] {sig.side.upper()} | "
                          f"P={sig.p_true:.0%} mercado={sig.market_price:.2f} "
                          f"edge={sig.edge:+.0%} | CL={cl_diff:+.0f}$ spot={spot_diff:+.0f}$ "
                          f"| tend {(state.trend or '-').upper()}")
                    token = state.up_token if sig.side == "up" else state.down_token

                    # Latencia real de ejecución (~2s): el precio puede moverse.
                    # Se simula también en dry para que el fill sea fiel a real.
                    time.sleep(EXEC_LATENCY_SECS)

                    if DRY_RUN:
                        avg = _simulate_market_fill(token, ORDER_SIZE_USDC) or sig.market_price
                        slip = (avg - sig.market_price) * 100
                        print(f"  [DRY] fill MERCADO @ {avg} "
                              f"(ask señal {sig.market_price} | slippage {slip:+.1f}c)")
                        if sig.side == "up":
                            state.up_filled = True;  state.up_fill_price = avg
                        else:
                            state.down_filled = True; state.down_fill_price = avg
                    else:
                        r = place_market_order(client, token, sig.side.upper(), ORDER_SIZE_USDC)
                        fp = r.get("avg_price", sig.market_price)
                        if sig.side == "up":
                            state.up_filled = True;  state.up_fill_price = fp
                        else:
                            state.down_filled = True; state.down_fill_price = fp

        mode_ch = {"directional": "D", "skip": "S"}.get(state.mode, "?")
        print(f"  [{mode_ch}] {secs_left/60:4.1f}m | "
              f"CL={cl_diff:+.0f}$ sp={spot_diff:+.0f}$ | "
              f"UP={up_ask or '?'} DN={dn_ask or '?'} | "
              f"apostado={'sí' if entered else 'no'}",
              end="\r", flush=True)

        time.sleep(POLL_INTERVAL)

    state.up_ask_close   = last_up
    state.down_ask_close = last_dn
    state.minutes_active = (datetime.now(timezone.utc) - window_start).total_seconds() / 60
    print()
    return state


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_status(state: CycleState, cl_now: float, cl_diff: float,
                  spot_diff: float, up_ask, dn_ask, secs_left: float) -> None:
    """Escribe status.json para el dashboard web."""
    try:
        data = {
            "running":    True,
            "mode":       state.mode,
            "question":   state.question,
            "cl_open":    round(state.cl_open, 2),
            "cl_now":     round(cl_now, 2),
            "cl_diff":    round(cl_diff, 2),
            "spot_diff":  round(spot_diff, 2),
            "up_ask":     up_ask,
            "down_ask":   dn_ask,
            "up_filled":  state.up_filled,
            "down_filled": state.down_filled,
            "secs_left":  int(secs_left),
            "updated_at": datetime.now().isoformat(),
        }
        path = os.path.join(os.path.dirname(__file__), "status.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def _resolve_and_learn(brain: Brain, pending_learn: dict) -> None:
    """
    Resuelve las ventanas pendientes ya asentadas (>=2min tras cierre) actualizando
    el CSV, y alimenta al Brain con las que aún tenemos signals en memoria.
    Nunca resuelve durante el periodo volátil (lo garantiza resolve_pending).
    """
    resolved = resolve_pending()
    for row in resolved:
        cid = row.get("condition_id", "")
        winner = row.get("winner", "")
        print(f"\n  Resuelto: {row['window_end_et']} -> {winner} | P&L {row.get('profit')}")
        st = pending_learn.pop(cid, None)
        if st is not None and winner in ("Up", "Down"):
            brain.record_outcome(winner, st.signals, condition_id=cid)
            print(f"  Brain aprendió de {row['window_end_et']} ({winner})")


def _simulate_market_fill(token_id: str, usdc: float) -> float | None:
    """
    Precio medio REAL de un market buy de `usdc` USDC, caminando el libro de asks
    (nivel a nivel, del más barato al más caro) como haría una orden de mercado.
    Captura el slippage real cuando el mejor nivel no tiene profundidad suficiente.
    Retorna None si no hay liquidez para cubrir el importe.
    """
    asks = sorted(_get_book(token_id).get("asks", []), key=lambda x: float(x["price"]))
    remaining = usdc
    shares = 0.0
    for a in asks:
        price = float(a["price"]); size = float(a["size"])
        cap = price * size                # USDC disponibles en este nivel
        if remaining <= cap:
            shares += remaining / price
            remaining = 0.0
            break
        shares += size
        remaining -= cap
    if shares <= 0 or remaining > 0.001:
        return None
    return round(usdc / shares, 4)        # precio medio ponderado real


def _wait_for_market(seen: set) -> dict:
    first = True
    while True:
        market = get_active_btc_market()
        if market and market["up_token"] and market["down_token"]:
            if market["condition_id"] not in seen:
                return market
            secs = _secs_to_end(market["end_date"])
            if first:
                print(f"  Ventana en curso — proxima en ~{math.ceil(secs/60)} min")
                first = False
            time.sleep(min(secs, 30))
        else:
            secs = _next_quarter()
            if first:
                print(f"  Sin ventana. Proxima ~{math.ceil(secs/60)} min -> {_hhmm(secs)}")
                first = False
            time.sleep(min(secs, 30))


def _secs_to_end(end_date: str) -> float:
    end = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
    return max(5, (end - datetime.now(timezone.utc)).total_seconds())


def _next_quarter() -> float:
    now = datetime.now(timezone.utc)
    return max(10, (15 - now.minute % 15) * 60 - now.second)


def _hhmm(secs: float) -> str:
    from datetime import timedelta
    return (datetime.now() + timedelta(seconds=secs)).strftime("%H:%M")


def _get_book(token_id: str) -> dict:
    try:
        r = requests.get(f"{CLOB_HOST}/book", params={"token_id": token_id}, timeout=8)
        return r.json() if r.ok else {}
    except Exception:
        return {}


def _best_ask(book: dict) -> float | None:
    asks = book.get("asks", [])
    return float(min(asks, key=lambda x: float(x["price"]))["price"]) if asks else None


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")
