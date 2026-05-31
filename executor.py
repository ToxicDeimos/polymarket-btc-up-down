"""
Coloca órdenes límite en Polymarket via py-clob-client.
"""
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType, Side
from py_clob_client.constants import POLYGON

from config import (
    PRIVATE_KEY,
    POLYMARKET_API_KEY,
    POLYMARKET_SECRET,
    POLYMARKET_PASSPHRASE,
    CLOB_HOST,
    TARGET_PRICE,
    ORDER_SIZE_USDC,
)


def build_client() -> ClobClient:
    client = ClobClient(
        host=CLOB_HOST,
        key=PRIVATE_KEY,
        chain_id=POLYGON,
        signature_type=1,          # EOA
        funder=None,
    )
    client.set_api_creds(
        api_key=POLYMARKET_API_KEY,
        api_secret=POLYMARKET_SECRET,
        api_passphrase=POLYMARKET_PASSPHRASE,
    )
    return client


def place_limit_order(client: ClobClient, token_id: str, side_label: str) -> dict:
    """
    Coloca una orden límite de compra a TARGET_PRICE.
    size = ORDER_SIZE_USDC / TARGET_PRICE  (número de shares)
    """
    size = round(ORDER_SIZE_USDC / TARGET_PRICE, 4)   # 2.5 shares a 40¢

    order_args = OrderArgs(
        token_id=token_id,
        price=TARGET_PRICE,
        size=size,
        side=Side.BUY,
    )
    signed_order = client.create_order(order_args)
    response = client.post_order(signed_order, OrderType.GTC)  # Good Till Cancelled

    order_id = response.get("orderID") or response.get("order_id", "???")
    print(f"  [{side_label}] Orden colocada — ID: {order_id} | "
          f"{size} shares @ {TARGET_PRICE} | size USDC: ${ORDER_SIZE_USDC}")
    return response


def cancel_order(client: ClobClient, order_id: str) -> None:
    client.cancel(order_id)
    print(f"  Orden {order_id} cancelada.")


def get_open_orders(client: ClobClient) -> list[dict]:
    return client.get_orders() or []
