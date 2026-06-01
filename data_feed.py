"""
Feed de precio BTC.

Fuente primaria : Chainlink BTC/USD en Polygon (mismo oráculo que usa Polymarket)
Fuente fallback : Binance spot (si Chainlink no responde)

Chainlink en Polygon:
  Contrato : 0xc907E116054Ad103354f2D350FD2514433D57F6f
  Función  : latestRoundData() → (roundId, answer, startedAt, updatedAt, answeredInRound)
  Precio   : answer / 1e8
  RPC      : https://polygon-rpc.com (público, sin auth)
"""
import time
import requests
from web3 import Web3

# ── Chainlink ─────────────────────────────────────────────────────────────────
POLYGON_RPC   = "https://polygon-rpc.com"
CHAINLINK_ADDR = "0xc907E116054Ad103354f2D350FD2514433D57F6f"

# ABI mínimo — solo necesitamos latestRoundData y decimals
CHAINLINK_ABI = [
    {
        "inputs": [],
        "name": "latestRoundData",
        "outputs": [
            {"name": "roundId",         "type": "uint80"},
            {"name": "answer",          "type": "int256"},
            {"name": "startedAt",       "type": "uint256"},
            {"name": "updatedAt",       "type": "uint256"},
            {"name": "answeredInRound", "type": "uint80"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
]

_w3 = None
_contract = None
_chainlink_decimals = 8

def _get_contract():
    global _w3, _contract, _chainlink_decimals, POLYGON_RPC
    if _contract is None:
        import os
        from dotenv import load_dotenv
        load_dotenv()
        rpc = os.getenv("POLYGON_RPC", POLYGON_RPC)
        _w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 8}))
        addr = Web3.to_checksum_address(CHAINLINK_ADDR)
        _contract = _w3.eth.contract(address=addr, abi=CHAINLINK_ABI)
        try:
            _chainlink_decimals = _contract.functions.decimals().call()
        except Exception:
            _chainlink_decimals = 8
    return _contract


def get_chainlink_price() -> float | None:
    """Precio BTC/USD desde Chainlink en Polygon. Fuente oficial de resolución."""
    try:
        contract = _get_contract()
        _, answer, _, updated_at, _ = contract.functions.latestRoundData().call()
        # Rechazar precios con más de 60s de antigüedad (nodo caído)
        if time.time() - updated_at > 60:
            return None
        return answer / (10 ** _chainlink_decimals)
    except Exception:
        return None


def get_btc_spot() -> float | None:
    """Precio BTC/USDT desde Binance. Usado como fallback si Chainlink falla."""
    try:
        resp = requests.get(
            "https://api.binance.com/api/v3/ticker/price",
            params={"symbol": "BTCUSDT"},
            timeout=5,
        )
        if resp.ok:
            return float(resp.json()["price"])
    except Exception:
        pass
    return None


def get_btc_price() -> tuple[float | None, str]:
    """
    Devuelve (precio, fuente) intentando Chainlink primero.
    Usar esta función en todo el bot para consistencia.
    """
    price = get_chainlink_price()
    if price:
        return price, "chainlink"
    price = get_btc_spot()
    if price:
        return price, "binance"
    return None, "none"
