"""
Coloca órdenes límite en Polymarket via py-clob-client.
"""
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType, ApiCreds
from py_clob_client.constants import POLYGON

from config import (
    PRIVATE_KEY,
    POLYMARKET_API_KEY,
    POLYMARKET_SECRET,
    POLYMARKET_PASSPHRASE,
    CLOB_HOST,
    TARGET_PRICE,
    ORDER_SIZE_USDC,
    DRY_RUN,
)


def build_client() -> ClobClient:
    client = ClobClient(
        host=CLOB_HOST,
        key=PRIVATE_KEY,
        chain_id=POLYGON,
        signature_type=1,
        funder=None,
    )
    client.set_api_creds(ApiCreds(
        api_key=POLYMARKET_API_KEY,
        api_secret=POLYMARKET_SECRET,
        api_passphrase=POLYMARKET_PASSPHRASE,
    ))
    return client


def place_limit_order(client: ClobClient, token_id: str, side_label: str) -> dict:
    """
    Coloca una orden límite de compra a TARGET_PRICE.
    size = ORDER_SIZE_USDC / TARGET_PRICE  (número de shares)
    """
    size = round(ORDER_SIZE_USDC / TARGET_PRICE, 4)   # p.ej. 2.5 shares a 40¢

    if DRY_RUN:
        fake_id = f"DRY-{side_label.strip()}-{token_id[:6]}"
        print(f"  [DRY][{side_label}] SIMULADO — {size} shares @ {TARGET_PRICE} "
              f"| coste: ${ORDER_SIZE_USDC} | payout si gana: ${size:.2f}")
        return {"orderID": fake_id, "dry_run": True}

    order_args = OrderArgs(
        token_id=token_id,
        price=TARGET_PRICE,
        size=size,
        side="BUY",
    )
    signed_order = client.create_order(order_args)
    response = client.post_order(signed_order, OrderType.GTC)

    order_id = response.get("orderID") or response.get("order_id", "???")
    print(f"  [{side_label}] Orden colocada — ID: {order_id} | "
          f"{size} shares @ {TARGET_PRICE} | coste: ${ORDER_SIZE_USDC}")
    return response


def sell_position(client: ClobClient, token_id: str,
                  shares: float, current_bid: float, side_label: str) -> dict:
    """
    Vende shares ya compradas al precio bid actual (stop loss).
    Se usa cuando Brain detecta que la posición va a perder.
    Recuperamos current_bid * shares en vez de perder todo.
    """
    if DRY_RUN:
        recovered = round(current_bid * shares, 4)
        loss      = round(ORDER_SIZE_USDC - recovered, 4)
        print(f"  [DRY][SELL {side_label}] {shares} shares @ {current_bid} "
              f"→ recupero ${recovered:.2f} | pérdida: -${loss:.2f}")
        return {"sold": True, "recovered": recovered, "loss": loss}

    order_args = OrderArgs(
        token_id=token_id,
        price=current_bid,
        size=shares,
        side="SELL",
    )
    signed_order = client.create_order(order_args)
    response = client.post_order(signed_order, OrderType.GTC)
    print(f"  [SELL {side_label}] {shares} shares @ {current_bid} → stop loss ejecutado")
    return response


def cancel_order(client: ClobClient, order_id: str) -> None:
    if DRY_RUN:
        print(f"  [DRY] Cancelación simulada: {order_id}")
        return
    client.cancel(order_id)
    print(f"  Orden {order_id} cancelada.")


def get_open_orders(client: ClobClient) -> list[dict]:
    if DRY_RUN:
        return []
    return client.get_orders() or []
