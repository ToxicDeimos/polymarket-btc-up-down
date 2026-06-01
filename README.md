# Polymarket BTC Up/Down 15m — Trading Bot

Bot de investigación y trading para el mercado **"Bitcoin Up or Down - 15 min"** de
[Polymarket](https://polymarket.com). Monitorea precios reales, ejecuta estrategias,
aprende de los resultados y muestra todo en un panel web en tiempo real.

> ⚠️ **Estado actual: DRY RUN (simulación).** No opera con dinero real por defecto.
> El bot se usa para validar si existe un *edge* explotable **antes** de arriesgar capital.

---

## 📊 Cómo funciona el mercado

Cada 15 minutos se abre una ventana. El mercado resuelve a **"Up"** si el precio de
Bitcoin al cierre es ≥ al de apertura, y a **"Down"** en caso contrario.

- Fuente de resolución: **Chainlink BTC/USD** en Polygon (no el spot de exchanges).
- Up + Down siempre suman ~$1.00 (precio justo de un binario).

---

## 🧠 Estrategia

El bot opera un único enfoque: **direccional**.

| Modo | Cuándo | Lógica |
|------|--------|--------|
| **DIRECCIONAL** | hay precios y movimiento spot en la apertura | El `Brain` apuesta **un solo lado** si detecta edge durante la ventana |
| **SKIP** | sin precios/señal | No opera (coste $0) |

### El Brain (motor de decisión)

- Modelo probabilístico (random walk tipo Black-Scholes digital) sobre Chainlink.
- Dos edges, ambos basados en el **lag** entre Binance (tiempo real) y Chainlink
  (actualiza cada ~27s), que el precio de Polymarket no refleja al instante:
  - **Edge 1** (apertura, T<120s): el spot ya se movió pero el mercado sigue ~50/50.
  - **Edge 2** (oracle lag, T=2-10min): Chainlink confirma dirección que el mercado no ha repreciado.
- Una sola apuesta por ventana (nunca ambos lados).
- Aprende de cada resultado y ajusta su `edge_threshold` (a partir de 20 ops).
- Persiste su entrenamiento en `brain_stats.json` (sobrevive reinicios).

---

## 🔬 Hallazgos empíricos (dry run)

El dry run cumplió su función: **dar evidencia antes de arriesgar dinero.**

- **El arbitrage de doble límite 40¢ NO funciona aquí** (estrategia original, descartada y
  eliminada del código). El mercado BTC 15m cierra a un extremo (0.01/0.99) el **88%** de las
  veces — *tiende, no oscila* — así que ambos lados nunca llenan a la vez: **0 dobles en 7
  intentos**.
- **El direccional está en evaluación.** Resultados prometedores pero con sesgo de régimen
  (ganó apostando con la tendencia bajista). Veredicto pendiente de ~40 operaciones a través
  de mercados alcistas, bajistas y laterales — el test clave es si **gana también apostando
  UP** cuando BTC sube.

---

## 🗂️ Estructura

```
polymarket-btc-up-down/
├── main.py          # Punto de entrada
├── strategy.py      # Bucle por ventana: decide modo, monitorea, apuesta
├── brain.py         # Motor de decisión direccional + aprendizaje
├── market.py        # Encuentra el mercado activo (Gamma API, slug por timestamp)
├── executor.py      # Órdenes CLOB: place / cancel (py-clob-client)
├── data_feed.py     # Precios: Chainlink (Polygon RPC) + Binance (fallback)
├── logger.py        # Persistencia: results.csv + prices.csv
├── dashboard.py     # Panel web Flask (http://localhost:5000)
├── config.py        # Configuración y flags
├── templates/
│   └── index.html   # UI del dashboard
├── requirements.txt
└── .env             # Claves (NO se sube al repo)
```

**Archivos de datos generados:**
- `results.csv` — resumen por ventana (precios, fills, ganador, P&L acumulado).
- `prices.csv` — snapshot de precios cada 15s.
- `brain_stats.json` — estado de entrenamiento del Brain.
- `status.json` — estado en vivo para el dashboard.

---

## ⚙️ Instalación

Requiere **Python 3.10+**.

```bash
git clone https://github.com/ToxicDeimos/polymarket-btc-up-down.git
cd polymarket-btc-up-down
pip install -r requirements.txt
```

### Configuración (`.env`)

Copia `.env.example` a `.env` y rellena tus valores:

```env
PRIVATE_KEY=0x...                  # Wallet de Polygon (solo necesaria en modo real)
POLYMARKET_API_KEY=...
POLYMARKET_SECRET=...
POLYMARKET_PASSPHRASE=...

# RPC de Polygon para leer Chainlink (gratis en alchemy.com → Polygon PoS Mainnet)
POLYGON_RPC=https://polygon-mainnet.g.alchemy.com/v2/TU_API_KEY

DRY_RUN=true                       # true = simulación | false = dinero real
```

---

## 🚀 Uso

**Terminal 1 — el bot:**
```bash
python main.py
```

**Terminal 2 — el dashboard:**
```bash
python dashboard.py
```

Abre **http://localhost:5000** para ver P&L, win rate, precios en vivo, entrenamiento
del Brain y el historial de ventanas.

---

## 🥧 Despliegue en Raspberry Pi

El bot es ligero (un poll cada 15s) e ideal para correr 24/7 sin depender del PC.

```bash
sudo apt install -y build-essential python3-dev python3-venv
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
# Copiar tu .env y crear servicios systemd para arranque automático
```

El dashboard queda accesible en la red local: `http://IP-de-la-raspberry:5000`.

---

## ⚠️ Aviso

Software experimental con fines educativos y de investigación. Operar en mercados de
predicción implica riesgo de pérdida total del capital. Úsalo bajo tu propia
responsabilidad. **Mantén `DRY_RUN=true` hasta tener evidencia sólida de un edge real.**
