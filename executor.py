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


def place_limit_order(client: ClobClient, token_id: str,
                      side_label: str, price: float) -> dict:
    """
    Coloca una orden límite de compra al `price` indicado (precio de mercado
    del lado que el Brain quiere apostar). size = ORDER_SIZE_USDC / price.
    """
    size = round(ORDER_SIZE_USDC / price, 4)

    if DRY_RUN:
        fake_id = f"DRY-{side_label.strip()}-{token_id[:6]}"
        print(f"  [DRY][{side_label}] SIMULADO — {size} shares @ {price} "
              f"| coste: ${ORDER_SIZE_USDC}")
        return {"orderID": fake_id, "dry_run": True}

    order_args = OrderArgs(token_id=token_id, price=price, size=size, side="BUY")
    signed_order = client.create_order(order_args)
    response = client.post_order(signed_order, OrderType.GTC)

    order_id = response.get("orderID") or response.get("order_id", "???")
    print(f"  [{side_label}] Orden colocada — ID: {order_id} | "
          f"{size} shares @ {price} | coste: ${ORDER_SIZE_USDC}")
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
