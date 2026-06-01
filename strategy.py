"""
Estrategia BTC Up/Down 15m — Brain v2

Al abrir cada ventana se elige UNA estrategia (mutuamente excluyentes):

  MODO ARBITRAGE  → mercado abierto en 42-58c (genuinamente 50/50)
                    Colocar limit 40c en AMBOS lados
                    Si ambos se llenan: +$0.50 garantizado

  MODO DIRECCIONAL → spot ya se movió $40+ respecto al Chainlink de apertura
                     Entrar SOLO en el lado ganador a precio de mercado
                     Edge 1 (T=0-120s) + Edge 2 (oracle lag T=2-10min)

No se mezclan: si el mercado es incierto usamos arbitrage,
si hay señal clara usamos Brain direccional.
"""
import json
import os
import time
import math
from datetime import datetime, timezone
from dataclasses import dataclass, field

import requests

from market import get_active_btc_market
from executor import build_client, place_limit_order, cancel_order, get_open_orders, sell_position
from data_feed import get_chainlink_price, get_btc_spot
from brain import Brain, Signal
from logger import ensure_files, log_price_snapshot, log_cycle_result, resolve_pending
from config import (POLL_INTERVAL, ORDER_SIZE_USDC, DRY_RUN, CLOB_HOST,
                    TARGET_PRICE, ENABLE_ARBITRAGE)

# ── Umbrales de decisión ──────────────────────────────────────────────────────
UNCERTAINTY_BAND   = 0.08   # |UP_ask - 0.50| < 0.08 → candidato a arbitrage
DIRECTIONAL_MOVE   = 40.0   # spot_diff > $40 → señal direccional → Brain
ARB_SCORE_MIN      = 0.25   # con exit barato (~-$0.25), +EV desde ~33% dobles

# ── Salida de fill único (corta pérdidas en arbitrage) ─────────────────────────
SINGLE_EXIT_DROP   = 0.06   # si el bid cae 6c bajo el fill → mercado en contra → salir
SINGLE_EXIT_SECS   = 90     # si quedan <90s y solo un lado lleno → salir (no holdear)


@dataclass
class CycleState:
    condition_id:   str
    question:       str
    end_date:       str
    up_token:       str
    down_token:     str
    mode:           str   = ""      # "arbitrage" | "directional" | "skip"
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
    arb_completed:  bool  = False   # ambos lados llenaron simultáneamente
    arb_score:      float = 0.0     # prob estimada de doble fill al abrir
    realized_pnl:   float = 0.0     # P&L de ventas mid-window (salidas de fill único)
    signals: list = field(default_factory=list)
    profit:  float = 0.0


def _compute_profit(state: "CycleState", winner: str) -> float:
    """
    P&L total de la ventana = ventas realizadas mid-window + resolución de
    posiciones aún en cartera al cierre.
    Para arbitrage completo (ambos lados) da +$0.50 de forma natural.
    """
    profit = state.realized_pnl
    if state.up_filled and state.up_fill_price:
        shares = ORDER_SIZE_USDC / state.up_fill_price
        profit += (shares - ORDER_SIZE_USDC) if winner == "Up" else -ORDER_SIZE_USDC
    if state.down_filled and state.down_fill_price:
        shares = ORDER_SIZE_USDC / state.down_fill_price
        profit += (shares - ORDER_SIZE_USDC) if winner == "Down" else -ORDER_SIZE_USDC
    return round(profit, 2)


def _decide_mode(up_ask: float | None, down_ask: float | None,
                 spot_diff: float, arb_score: float) -> str:
    """
    Decide qué estrategia usar en esta ventana.

    ARBITRAGE   : mercado incierto (42-58¢) Y score predice doble fill probable
    DIRECTIONAL : spot ya confirma dirección ($40+ de movimiento)
    SKIP        : mercado sesgado, o arbitrage con baja prob de doble fill
    """
    if up_ask is None or down_ask is None:
        return "skip"

    market_uncertainty = abs(up_ask - 0.50) < UNCERTAINTY_BAND  # ambos ~50/50
    has_direction      = abs(spot_diff) >= DIRECTIONAL_MOVE

    if market_uncertainty:
        # Arbitrage solo si está habilitado Y el score predice oscilación.
        # Desactivado por defecto: el mercado no oscila (0 dobles en 7 intentos).
        if ENABLE_ARBITRAGE and arb_score >= ARB_SCORE_MIN:
            return "arbitrage"
        return "directional" if has_direction else "skip"
    if has_direction:
        return "directional"
    return "skip"


def run():
    client = build_client()
    brain  = Brain()
    mode_tag = "[DRY RUN]" if DRY_RUN else "[REAL]"

    print("=" * 62)
    print(f"  Polymarket BTC Up/Down 15m  {mode_tag}")
    print(f"  ARBITRAGE   : 42-58c Y ArbScore≥{ARB_SCORE_MIN:.0%} → limit 40c ambos")
    print(f"  DIRECCIONAL : spot >${DIRECTIONAL_MOVE:.0f} movimiento → Brain")
    print(f"  SKIP        : sesgado, o arbitrage con baja prob de doble fill")
    print(f"  EXIT-único  : fill solo de un lado → vender (corta -$1.00 a ~-$0.10)")
    print("=" * 62)

    ensure_files()

    resolved = resolve_pending()
    for row in resolved:
        print(f"  Resuelto: {row['window_end_et']} -> {row['winner']}")

    total_invested = 0.0
    total_profit   = 0.0
    stats = {"arbitrage": [0, 0], "directional": [0, 0], "skip": 0}
    cycle = 0
    seen: set = set()

    while True:
        cycle += 1
        print(f"\n{'─'*62}")
        print(f"  Ciclo #{cycle}  {_now()}")

        market = _wait_for_market(seen)
        seen.add(market["condition_id"])

        state = CycleState(**{k: market[k] for k in
            ["condition_id", "question", "end_date", "up_token", "down_token"]})

        # Precios de apertura
        state.cl_open   = get_chainlink_price() or 0.0
        state.spot_open = get_btc_spot()        or 0.0
        spot_diff_open  = state.spot_open - state.cl_open
        brain.reset_window()

        up_book   = _get_book(state.up_token)
        down_book = _get_book(state.down_token)
        state.up_ask_open   = _best_ask(up_book)
        state.down_ask_open = _best_ask(down_book)

        # ── SCORE de arbitrage (¿es buen momento para oscilación?) ────────────
        arb_features    = brain.arbitrage_features(state.cl_open, state.spot_open)
        state.arb_score = brain.arbitrage_score(arb_features)

        # ── DECISIÓN DE MODO ──────────────────────────────────────────────────
        state.mode = _decide_mode(state.up_ask_open, state.down_ask_open,
                                  spot_diff_open, state.arb_score)

        print(f"  Mercado  : {state.question}")
        print(f"  CL open  : ${state.cl_open:,.2f}  "
              f"spot open: ${state.spot_open:,.2f}  "
              f"diff: {spot_diff_open:+.0f}$")
        print(f"  Ask open : UP={state.up_ask_open}  DOWN={state.down_ask_open}")
        print(f"  ArbScore : {state.arb_score:.0%} "
              f"(diff={arb_features['open_diff']:.0f}$ "
              f"vol=${arb_features['vol']:.2f}/s "
              f"prevTrend={arb_features['prev_trend']:.0f}$)")
        print(f"  MODO     : {state.mode.upper()}")
        print(f"  {brain.summary()} | {brain.arb_summary()}")

        if state.mode == "skip":
            stats["skip"] += 1
            print(f"  Ventana saltada — mercado sesgado sin señal clara")
            # Igual monitoreamos para recoger datos
            state = _monitor(client, state, brain, active=False)
        elif state.mode == "arbitrage":
            state = _run_arbitrage(client, state, brain)
        else:
            state = _run_directional(client, state, brain)

        # ── Ganador por Chainlink (fuente oficial) ────────────────────────────
        cl_close = get_chainlink_price() or state.cl_open
        winner   = "Up" if cl_close >= state.cl_open else "Down"
        diff     = cl_close - state.cl_open

        # ── Profit unificado (incluye salidas mid-window) ─────────────────────
        state.profit = _compute_profit(state, winner)
        fills = sum([state.up_filled, state.down_filled])
        total_invested += ORDER_SIZE_USDC * fills

        if state.arb_completed:
            tag = "ARBITRAGE COMPLETO  +$0.50 garantizado"
            stats[state.mode][0] += 1
            stats[state.mode][1] += 1
        elif state.realized_pnl != 0:
            tag = f"SALIDA fill único (corte de pérdida)"
        elif fills == 1:
            side = "UP" if state.up_filled else "DOWN"
            tag  = f"{side} mantenido a resolución"
        else:
            tag = "Sin fills"

        # Registrar resultado de arbitrage para que el Brain aprenda timing
        if state.mode == "arbitrage":
            brain.record_arbitrage_outcome(
                brain.arbitrage_features(state.cl_open, state.spot_open),
                state.arb_completed)
        brain.set_prev_window(state.cl_open)

        total_profit += state.profit
        roi = total_profit / total_invested * 100 if total_invested else 0.0

        print(f"\n  {tag}")
        print(f"  CL cierre: ${cl_close:,.2f} ({diff:+.2f}) → {winner} gana")
        print(f"  Profit ciclo: ${state.profit:+.2f}")
        print(f"  Total     : profit=${total_profit:+.2f} | ROI={roi:.1f}%")
        print(f"  Stats     : arb={stats['arbitrage']} | "
              f"dir={stats['directional']} | skip={stats['skip']}")

        log_cycle_result(
            condition_id   = state.condition_id,
            question       = state.question,
            up_ask_open    = state.up_ask_open,
            down_ask_open  = state.down_ask_open,
            up_ask_close   = state.up_ask_close,
            down_ask_close = state.down_ask_close,
            up_filled      = state.up_filled,
            down_filled    = state.down_filled,
            minutes_active = state.minutes_active,
            winner         = winner,
            mode           = state.mode,
            profit         = state.profit,
        )
        brain.record_outcome(winner, state.signals)

        time.sleep(2)


# ── Modos de operación ────────────────────────────────────────────────────────

def _run_arbitrage(client, state: CycleState, brain: Brain) -> CycleState:
    """
    Modo ARBITRAGE: coloca limit 40¢ en ambos lados.
    Solo se activa cuando el mercado está genuinamente en 50/50.
    Si ambos se llenan → +$0.50 garantizado sin importar la dirección.
    """
    print(f"  [ARB] Colocando limit 40c en ambos lados…")
    if not DRY_RUN:
        up_r  = place_limit_order(client, state.up_token,   "UP  ")
        dn_r  = place_limit_order(client, state.down_token, "DOWN")
        state.up_order_id   = up_r.get("orderID")
        state.down_order_id = dn_r.get("orderID")
    else:
        print(f"  [DRY][ARB] Límites 40c colocados")
    return _monitor(client, state, brain, active=True)


def _run_directional(client, state: CycleState, brain: Brain) -> CycleState:
    """
    Modo DIRECCIONAL: el Brain decide si hay edge suficiente y en qué lado.
    No se colocan límites en el lado contrario.
    """
    print(f"  [DIR] Brain monitoreando para entrada direccional…")
    return _monitor(client, state, brain, active=True)


# ── Monitor de ventana ────────────────────────────────────────────────────────

def _monitor(client, state: CycleState, brain: Brain,
             active: bool) -> CycleState:
    """
    Polling cada POLL_INTERVAL segundos.
    active=False → solo recopila datos sin entrar.
    """
    end          = datetime.fromisoformat(state.end_date.replace("Z", "+00:00"))
    window_start = datetime.now(timezone.utc)
    last_up  = state.up_ask_open
    last_dn  = state.down_ask_open
    entered_sides: set = set()
    exited_sides: set = set()        # lados ya cerrados (vendidos) — no re-entrar
    fill_times: dict = {}            # side → timestamp del fill

    while True:
        now          = datetime.now(timezone.utc)
        secs_left    = (end - now).total_seconds()
        secs_elapsed = (now - window_start).total_seconds()

        if secs_left <= 0:
            print("\n  Ventana cerrada.")
            break

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
                           secs_elapsed, secs_left, up_book, down_book)
        _write_status(state, cl_now, cl_diff, spot_diff, up_ask, dn_ask, secs_left)

        if active:
            if state.mode == "arbitrage":
                # ── Detectar fills de límite 40¢ ──────────────────────────────
                if DRY_RUN:
                    if (not state.up_filled and "up" not in exited_sides
                            and up_ask is not None and up_ask <= TARGET_PRICE):
                        state.up_filled     = True
                        state.up_fill_price = up_ask
                        fill_times["up"]    = now
                        print(f"\n  [ARB] UP @ {up_ask} llenado")
                    if (not state.down_filled and "down" not in exited_sides
                            and dn_ask is not None and dn_ask <= TARGET_PRICE):
                        state.down_filled     = True
                        state.down_fill_price = dn_ask
                        fill_times["down"]    = now
                        print(f"\n  [ARB] DOWN @ {dn_ask} llenado")
                else:
                    open_ids = {o["id"] for o in get_open_orders(client)}
                    if not state.up_filled and state.up_order_id not in open_ids:
                        state.up_filled = True
                        fill_times["up"] = now
                        print(f"\n  [UP] Fill confirmado")
                    if not state.down_filled and state.down_order_id not in open_ids:
                        state.down_filled = True
                        fill_times["down"] = now
                        print(f"\n  [DOWN] Fill confirmado")

                # ── Doble fill = arbitrage completo (salimos del bucle) ───────
                if state.up_filled and state.down_filled:
                    state.arb_completed = True

                # ── Salida de fill ÚNICO ──────────────────────────────────────
                # Si solo un lado llenó, ese lado es el que el mercado abandona.
                # Salir si: (a) su precio cae bajo el fill (tendencia en contra) o
                #           (b) quedan <90s (no holdear a resolución como apuesta).
                only_up   = state.up_filled   and not state.down_filled
                only_down = state.down_filled and not state.up_filled

                if only_up or only_down:
                    side       = "up" if only_up else "down"
                    token      = state.up_token if only_up else state.down_token
                    fill_price = state.up_fill_price if only_up else state.down_fill_price
                    book       = up_book if only_up else down_book
                    bid_now    = _best_bid(book)
                    secs_since_fill = (now - fill_times.get(side, now)).total_seconds()

                    price_against = (bid_now is not None and fill_price
                                     and bid_now < fill_price - SINGLE_EXIT_DROP)
                    time_running_out = secs_left < SINGLE_EXIT_SECS

                    # Dar margen mínimo (1 poll) para que el 2º lado pueda llenar
                    if secs_since_fill >= POLL_INTERVAL and (price_against or time_running_out):
                        shares    = round(ORDER_SIZE_USDC / fill_price, 4) if fill_price else 2.5
                        sell_bid  = bid_now if (bid_now and bid_now > 0.02) else 0.02
                        recovered = round(sell_bid * shares, 4)
                        pnl       = round(recovered - ORDER_SIZE_USDC, 2)
                        reason    = "precio en contra" if price_against else "fin de ventana"

                        print(f"\n  [EXIT-único] {side.upper()} @ {fill_price} → "
                              f"bid {sell_bid} ({reason})")
                        print(f"  [EXIT-único] vendo {shares} sh → recupero "
                              f"${recovered:.2f} | P&L: ${pnl:+.2f} (vs -$1.00 si holdeaba)")

                        sell_position(client, token, shares, sell_bid, side.upper())
                        state.realized_pnl += pnl
                        exited_sides.add(side)
                        if only_up:
                            state.up_filled = False
                        else:
                            state.down_filled = False

            elif state.mode == "directional":
                # Brain evalúa señales
                signals = brain.evaluate(
                    cl_open=state.cl_open,    cl_now=cl_now,
                    spot_open=state.spot_open, spot_now=spot_now,
                    up_ask=up_ask,             down_ask=dn_ask,
                    secs_elapsed=secs_elapsed, secs_left=secs_left,
                )
                for sig in signals:
                    # Una sola apuesta direccional por ventana: si ya entramos
                    # en un lado, NUNCA entrar en el contrario (apostar a ambos
                    # garantiza pérdida). Esto era el bug del fill doble.
                    if entered_sides:
                        continue
                    entered_sides.add(sig.side)
                    state.signals.append(sig)

                    print(f"\n  [Brain/{sig.edge_type}] {sig.side.upper()} | "
                          f"P={sig.p_true:.0%} mercado={sig.market_price:.2f} "
                          f"edge={sig.edge:+.0%} | "
                          f"CL={cl_diff:+.0f}$ spot={spot_diff:+.0f}$")

                    if DRY_RUN:
                        _mark_fill(state, sig)
                    else:
                        if sig.side == "up" and not state.up_filled:
                            r = place_limit_order(
                                client, state.up_token, f"UP/{sig.edge_type}")
                            state.up_order_id = r.get("orderID")
                        elif sig.side == "down" and not state.down_filled:
                            r = place_limit_order(
                                client, state.down_token, f"DN/{sig.edge_type}")
                            state.down_order_id = r.get("orderID")

        # Status en pantalla
        mode_ch = {"arbitrage": "A", "directional": "D", "skip": "S"}.get(state.mode, "?")
        print(f"  [{mode_ch}] {secs_left/60:4.1f}m | "
              f"CL={cl_diff:+.0f}$ sp={spot_diff:+.0f}$ | "
              f"UP={up_ask or '?'} DN={dn_ask or '?'} | "
              f"fills UP={'v' if state.up_filled else 'o'} "
              f"DN={'v' if state.down_filled else 'o'}",
              end="\r", flush=True)

        # Salida temprana SOLO en arbitrage: doble fill = beneficio garantizado.
        # En direccional jamás se sale antes del cierre por tener ambos lados.
        if active and state.mode == "arbitrage" and state.up_filled and state.down_filled:
            print()
            break

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




def _mark_fill(state: CycleState, sig: Signal) -> None:
    if sig.side == "up":
        state.up_filled     = True
        state.up_fill_price = sig.market_price
    else:
        state.down_filled     = True
        state.down_fill_price = sig.market_price


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


def _best_bid(book: dict) -> float | None:
    bids = book.get("bids", [])
    return float(max(bids, key=lambda x: float(x["price"]))["price"]) if bids else None


def _cleanup(client, state: CycleState) -> None:
    for oid in [state.up_order_id, state.down_order_id]:
        if oid:
            try:
                cancel_order(client, oid)
            except Exception as e:
                print(f"  Error cancelando {oid}: {e}")


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")
