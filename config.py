# ── Polymarket BTC Up/Down 15m Arbitrage Bot ─────────────────────────────────
# Estrategia: colocar limit orders a 40¢ en AMBOS lados (Up y Down).
# Resultado garantizado: $2.50 de payout sobre $2 invertidos = +$0.50 por ciclo.

# ── Credenciales ──────────────────────────────────────────────────────────────
PRIVATE_KEY = ""          # Tu private key de Polygon (con 0x)
POLYMARKET_API_KEY    = ""
POLYMARKET_SECRET     = ""
POLYMARKET_PASSPHRASE = ""

# Endpoint CLOB
CLOB_HOST = "https://clob.polymarket.com"

# ── Parámetros de estrategia ─────────────────────────────────────────────────
# Precio límite al que queremos entrar (en centavos decimales)
TARGET_PRICE = 0.40          # 40¢

# Tamaño de cada orden en USDC
ORDER_SIZE_USDC = 1.0        # $1 por lado → $2 total → payout $2.50

# Margen de precio: acepta fills hasta TARGET_PRICE + SLIPPAGE
SLIPPAGE = 0.01              # 1¢ de tolerancia

# Cuántos segundos esperar entre polls del libro de órdenes
POLL_INTERVAL = 15           # segundos

# Mínimo de minutos restantes en la ventana para abrir nuevas órdenes
MIN_MINUTES_REMAINING = 3    # no abrir si quedan menos de 3 min

# ── Filtro de mercado ────────────────────────────────────────────────────────
MARKET_SLUG_CONTAINS = "btc-updown-15m"   # fragmento del slug en Polymarket
