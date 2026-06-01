# ── Polymarket BTC Up/Down 15m Arbitrage Bot ─────────────────────────────────
# Estrategia: colocar limit orders a 40¢ en AMBOS lados (Up y Down).
# Resultado garantizado: $2.50 de payout sobre $2 invertidos = +$0.50 por ciclo.

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

# ── Parámetros de estrategia ─────────────────────────────────────────────────
# Precio límite al que queremos entrar (en centavos decimales)
TARGET_PRICE = 0.40          # 40¢

# Tamaño de cada orden en USDC
ORDER_SIZE_USDC = 1.0        # $1 por lado → $2 total → payout $2.50

# ── Modo simulación ───────────────────────────────────────────────────────────
# True  → no envía órdenes reales, simula fills y calcula P&L
# False → opera en real con fondos reales
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

# ── Estrategias activas ───────────────────────────────────────────────────────
# Arbitrage de doble límite 40¢: DESACTIVADO por evidencia empírica.
# El mercado BTC 15m cierra a extremo el 88% de las veces (no oscila),
# así que ambos lados nunca llenan a la vez (0 dobles en 7 intentos = -$5.85).
ENABLE_ARBITRAGE = os.getenv("ENABLE_ARBITRAGE", "false").lower() == "true"

# Cuántos segundos esperar entre polls del libro de órdenes
POLL_INTERVAL = 15           # segundos

# Filtro de ventana: solo operar la que está en curso (3-25 min restantes)
MIN_MINUTES_REMAINING = 3    # no abrir si quedan menos de 3 min
MAX_MINUTES_REMAINING = 25   # ignorar mercados futuros creados con antelación
