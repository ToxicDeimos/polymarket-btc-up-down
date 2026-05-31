"""
Máquina de estados del ciclo de arbitrage por ventana de 15 minutos.

Estados:
  WAITING   → buscando mercado activo con tiempo suficiente
  OPEN      → órdenes colocadas, esperando fills
  FILLED    → ambos lados llenados, esperando resolución
  RESOLVED  → ciclo completado, profit registrado
"""
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field

from market import get_active_btc_market, get_best_ask
from executor import build_client, place_limit_order, cancel_order, get_open_orders
from config import POLL_INTERVAL, TARGET_PRICE, SLIPPAGE


@dataclass
class CycleState:
    condition_id: str
    question: str
    end_date: str
    up_token: str
    down_token: str
    up_order_id:   str | None = None
    down_order_id: str | None = None
    up_filled:   bool = False
    down_filled: bool = False
    profit: float = 0.0


def run():
    client = build_client()
    print("=== Polymarket BTC Up/Down 15m Arbitrage Bot ===")
    print(f"    Estrategia: limit buy @ {TARGET_PRICE}¢ en AMBOS lados")
    print(f"    Payout garantizado: $2.50 | Inversión: $2.00 | Profit: +$0.50\n")

    total_profit = 0.0
    cycle = 0

    while True:
        cycle += 1
        print(f"── Ciclo #{cycle} ── {_now()} ──────────────────────────")

        # ── 1. Buscar mercado activo ──────────────────────────────────────────
        market = _wait_for_market()
        print(f"  Mercado: {market['question']}")
        print(f"  Tokens: UP={market['up_token'][:10]}…  DOWN={market['down_token'][:10]}…")
        print(f"  Tiempo restante: {market['minutes_left']} min")

        state = CycleState(**{k: market[k] for k in
            ["condition_id","question","end_date","up_token","down_token"]})

        # ── 2. Colocar órdenes límite en ambos lados ──────────────────────────
        print("  Colocando órdenes límite…")
        up_resp   = place_limit_order(client, state.up_token,   "UP  ")
        down_resp = place_limit_order(client, state.down_token, "DOWN")
        state.up_order_id   = up_resp.get("orderID")   or up_resp.get("order_id")
        state.down_order_id = down_resp.get("orderID") or down_resp.get("order_id")

        # ── 3. Monitorear fills hasta que expire la ventana ───────────────────
        state = _monitor_fills(client, state)

        # ── 4. Cancelar órdenes no llenadas al cierre ─────────────────────────
        _cleanup(client, state)

        # ── 5. Contabilizar ───────────────────────────────────────────────────
        if state.up_filled and state.down_filled:
            state.profit = 0.50   # $2.50 payout - $2.00 invertido
            print(f"  ✓ AMBOS LADOS LLENOS — profit esperado: +${state.profit:.2f}")
        elif state.up_filled or state.down_filled:
            filled = "UP" if state.up_filled else "DOWN"
            print(f"  ⚠ Solo {filled} fue llenado — posición abierta, riesgo 50/50")
            state.profit = 0.0    # resultado incierto
        else:
            print("  ✗ Ninguna orden llenada en este ciclo.")

        total_profit += state.profit
        print(f"  Profit acumulado: ${total_profit:.2f}\n")

        # Pausa breve antes de buscar el siguiente ciclo
        time.sleep(5)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _wait_for_market() -> dict:
    """Espera hasta encontrar un mercado activo con tiempo suficiente."""
    while True:
        market = get_active_btc_market()
        if market and market["up_token"] and market["down_token"]:
            return market
        print(f"  No hay mercado activo. Reintentando en {POLL_INTERVAL}s…")
        time.sleep(POLL_INTERVAL)


def _monitor_fills(client, state: CycleState) -> CycleState:
    """Polling del libro de órdenes hasta que ambos lados se llenen o expire."""
    while True:
        now = datetime.now(timezone.utc)
        end = datetime.fromisoformat(state.end_date.replace("Z", "+00:00"))
        secs_left = (end - now).total_seconds()

        if secs_left <= 0:
            print("  Ventana expirada.")
            break

        open_orders = {o["id"]: o for o in get_open_orders(client)}

        if not state.up_filled:
            if state.up_order_id not in open_orders:
                state.up_filled = True
                print(f"  [UP  ] ✓ Fill confirmado")

        if not state.down_filled:
            if state.down_order_id not in open_orders:
                state.down_filled = True
                print(f"  [DOWN] ✓ Fill confirmado")

        if state.up_filled and state.down_filled:
            print("  Ambas órdenes llenadas.")
            break

        print(f"  Esperando fills… {int(secs_left)}s restantes "
              f"(UP={'✓' if state.up_filled else '…'} "
              f"DOWN={'✓' if state.down_filled else '…'})")
        time.sleep(POLL_INTERVAL)

    return state


def _cleanup(client, state: CycleState) -> None:
    """Cancela órdenes pendientes al cierre de la ventana."""
    if not state.up_filled and state.up_order_id:
        try:
            cancel_order(client, state.up_order_id)
        except Exception as e:
            print(f"  Error cancelando UP: {e}")

    if not state.down_filled and state.down_order_id:
        try:
            cancel_order(client, state.down_order_id)
        except Exception as e:
            print(f"  Error cancelando DOWN: {e}")


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")
