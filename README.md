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

## 🧠 Estrategias

El bot elige **un modo por ventana** (son mutuamente excluyentes):

| Modo | Cuándo | Lógica |
|------|--------|--------|
| **ARBITRAGE** | mercado abre ~50/50 y el `ArbScore` predice oscilación | Limit a 40¢ en ambos lados. Si ambos llenan → +$0.50 garantizado |
| **DIRECCIONAL** | mercado abre inclinado + señal de movimiento | El `Brain` apuesta un solo lado si detecta edge (lag Chainlink/Binance) |
| **SKIP** | mercado sesgado sin señal clara | No opera (coste $0) |

### El Brain (motor de decisión)

- Modelo probabilístico (random walk tipo Black-Scholes digital) sobre Chainlink.
- Aprende de cada resultado y ajusta su `edge_threshold` (mínima ventaja exigida).
- `ArbScore`: estima la probabilidad de doble fill según la volatilidad y la tendencia previa.
- Persiste su entrenamiento en `brain_stats.json` (sobrevive reinicios).

### Salida de fill único (corte de pérdidas)

Si en arbitrage solo llena un lado (el que el mercado abandona), el bot **vende** la
posición al detectar que el precio va en contra, limitando la pérdida a ~−$0.25 en vez
de −$1.00.

---

## 🔬 Hallazgos empíricos (78+ ventanas en dry run)

El dry run cumplió su función: **dar evidencia antes de arriesgar dinero.**

- **El arbitrage de doble límite NO funciona aquí.** El mercado BTC 15m cierra a un
  extremo (0.01/0.99) el **88%** de las veces — *tiende, no oscila*. Resultado: **0 dobles
  en 7 intentos**. Por eso `ENABLE_ARBITRAGE=false` por defecto.
- **El direccional está en evaluación.** Muestra resultados prometedores pero con sesgo
  de régimen (gana apostando con la tendencia). Veredicto pendiente de ~40 operaciones
  a través de mercados alcistas, bajistas y laterales.

---

## 🗂️ Estructura

```
polymarket-btc-up-down/
├── main.py          # Punto de entrada
├── strategy.py      # Máquina de estados: decide modo, monitorea ventana
├── brain.py         # Motor de decisión + scorer de arbitrage + aprendizaje
├── market.py        # Encuentra el mercado activo (Gamma API, slug por timestamp)
├── executor.py      # Órdenes CLOB: place / sell / cancel (py-clob-client)
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
ENABLE_ARBITRAGE=false             # desactivado por evidencia empírica
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
