# ── Polymarket BTC Up/Down 15m — Bot direccional ─────────────────────────────
# El Brain apuesta un solo lado cuando detecta edge (lag Chainlink/Binance).

import os
from dotenv import load_dotenv
load_dotenv()

# ── Credenciales (desde .env o variables de entorno) ──────────────────────────
PRIVATE_KEY           = os.getenv("PRIVATE_KEY", "")
POLYMARKET_API_KEY    = os.getenv("POLYMARKET_API_KEY", "")
POLYMARKET_SECRET     = os.getenv("POLYMARKET_SECRET", "")
POLYMARKET_PASSPHRASE = os.getenv("POLYMARKET_PASSPHRASE", "")

# Endpoint CLOB
CLOB_HOST = "https://clob.polymarket.com"

# ── Parámetros ───────────────────────────────────────────────────────────────
# Tamaño de cada apuesta en USDC
ORDER_SIZE_USDC = 1.0

# True → simulación (no envía órdenes reales) | False → dinero real
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

# Segundos entre polls. El edge es de lag (~10-30s): 5s lo captura bien sin
# saturar la API. Debe ser igual en dry y real para que la medición sea fiel.
POLL_INTERVAL = 5

# Filtro de ventana: solo operar la que está en curso (3-25 min restantes)
MIN_MINUTES_REMAINING = 3    # no abrir si quedan menos de 3 min
MAX_MINUTES_REMAINING = 25   # ignorar mercados futuros creados con antelación
