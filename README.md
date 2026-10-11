# Polymarket BTC Up/Down — buscar un edge real y saber cuándo no lo hay

Investigación cuantitativa sobre los mercados **"Bitcoin Up or Down"** (5 y 15 minutos) de
[Polymarket](https://polymarket.com): un binario que paga 1 $ si BTC cierra la ventana por encima
de donde la abrió.

Cinco meses (mayo–octubre 2026), 280 commits, servicios corriendo 24/7 en una Raspberry Pi y
~20 hipótesis probadas con reglas fijadas **antes** de ver el dato.

> **Estado: cerrado.** Se encontró un edge real y medido, y se demostró que es **inalcanzable**
> por un motivo estructural, no por falta de velocidad. Este README cuenta cómo se llegó ahí.

---

## TL;DR

| | |
|---|---|
| **Hipótesis inicial** | Polymarket va por detrás de Binance → apostar con ventaja. **Falsa**: el libro reprecia en < 5 s. |
| **Lo que sí existe** | Tras un salto de BTC, los creadores de mercado tardan en recotizar. A los 200 ms al libro le quedan **+4 pp** por recorrer. Comprar esa cotización vieja y aguantar a resolución da **+6,3 ± 1,1 pp** por operación en papel (z ≈ 6). |
| **Por qué no se puede cobrar** | Con órdenes reales emparejadas con su gemela en papel: las que se llenaron dan **−2,6 pp**; las que no, **+7,9 pp** (diferencia z = −3,65). Solo te llenan cuando el precio *no* se está moviendo, y entonces no hay nada que ganar. |
| **Lección** | Un edge medido en papel no es un edge cobrable. Lo que decide es **qué órdenes te llenan**, y eso solo lo responde dinero real. |

---

## 1. La hipótesis que se cayó primero: el "lag"

El mercado resuelve con **Chainlink BTC/USD**, que actualiza cada ~27 s. La idea obvia: Binance
se mueve primero, Polymarket sigue después, compras en medio.

Medido sobre 268 ventanas y 38 k pares de precios: `corr(Δspot, Δask)` es **+0,63 a 0 s y ~0 desde
5 s**. El libro reprecia a la vez que Binance. No hay retraso explotable sondeando cada pocos
segundos.

Por el camino apareció un bug: el estimador de volatilidad usaba `media(|ΔP|/Δt)` donde la fórmula
exige `sqrt(media(ΔP²/Δt))`. Infraestimaba la vol **~10×** y el modelo decía "93 % seguro" en
apuestas que ganaban el 63 %. El "beneficio" inicial era el bug más una tendencia bajista limpia.

## 2. Mirar a quién gana (y no a qué indicador)

Si el mercado es eficiente, ¿alguien gana de forma persistente? Con la Data API pública se
reconstruyeron **55.745 wallets** y se partió su historial en dos mitades.

- Hay persistencia: el mejor cuartil de la 1.ª mitad sigue ganando en la 2.ª.
- La wallet más grande (9.366 operaciones) bate el precio que paga en **+2,6 a +3,6 pp en las siete
  franjas de precio**. Una ventaja *plana* en todo el rango no es una señal direccional, que se
  concentraría en ciertas zonas: es una propiedad de **la orden** que compra.
- Seguimiento fuera de muestra (fichados en agosto, medidos en septiembre y octubre): 5 de 6
  desaparecieron, que es lo que hace la suerte. **Uno siguió ganando y mejorando**. Su ventaja:
  compra 1,0–1,6 pp más barato que quien opera el mismo token en el mismo segundo
  (correlación comprar-barato ↔ ganar **+0,64**, n = 165 wallets). No acierta más: **lo llenan mejor**.

## 3. El mecanismo: la cotización vieja

Grabando el libro de Polymarket por WebSocket y el *bookTicker* de Binance al milisegundo en el mismo
reloj (24 h, 17,8 M de eventos):

- Tras un salto de BTC ≥ 10 $ en 1 s, el libro ha recorrido el 14 % a los 100 ms, el 38 % a los 200 ms
  y el 98 % a los 5 s.
- Comprar el ask a los 100 ms del salto: **+2,1 ± 0,4 pp** contra el precio medio asentado (z 5,4).
  Decae de forma monótona con la latencia y cruza cero hacia los 300 ms.
- **Control espejo** (mismo instante, el token contrario): **−14,4 pp**. La señal es direccional;
  no es un artefacto simétrico.
- Tres gradientes monótonos en la dirección esperada (umbral del salto, rapidez del salto, latencia).
  No es una casilla con suerte: es una superficie.

Una sorpresa que corrigió mi propia hipótesis: predije **más** edge en momentos agitados (más saltos,
más cotizaciones viejas). Sale **al revés**, de forma monótona: +7,9 pp en régimen tranquilo y
+1,8 pp en agitado. Con movimiento los creadores de mercado están atentos; cuando está tranquilo, se
despistan. La primera versión de esa medida tenía *look-ahead* en la etiqueta del régimen. Al
corregirlo, el gradiente aguantó, pero el umbral pasó de 64 a 102.

## 4. Dinero real: por qué no se puede cobrar

Un bot en papel (`stalepaper`) y uno real (`stalebot`) dispararon en paralelo sobre la misma señal.
`cruce.py` emparejó **347 de 353 órdenes reales** con su disparo gemelo del papel (mediana de
desfase: 0,0 s) y comparó qué pasó después:

```
se llenó       n =  21    −2,57 ± 2,79 pp   (≈ la comisión)
no se llenó    n = 326    +7,87 ± 0,65 pp
diferencia                −10,44 ± 2,86 pp   z = −3,65
```

**Todo el margen está en las órdenes que no conseguimos.** El motivo es casi una tautología:
solo puedes comprar cuando nadie quiere vender más barato, eso pasa cuando el precio *no* se está
moviendo y, si no se mueve, no hay cotización vieja que aprovechar.

¿Se arregla con velocidad? Se midió cada pieza del camino:

| tramo | ms | ¿reducible? |
|---|---|---|
| Binance → nosotros | ~140 | No. Otras fuentes están más cerca pero actualizan 15–100× menos a menudo. |
| red hasta el CLOB | ~60 | No. Polymarket ya está en Europa (tras Cloudflare, nodo MAD); un VPS en EE. UU. empeora. |
| proceso de la orden en su servidor | ~180 | No. Lo paga todo el mundo. |
| nuestro código (señal + firma) | ~13 | Ya optimizado. Se eliminaron 200 ms de llamadas ocultas del SDK y **la tasa de relleno no se movió**. |

Operar desde el PC en vez de la Pi (55 ms más rápido) tampoco cambió el relleno: 7 ± 2 % frente a
6 ± 2 %. **La palanca de la latencia está agotada a esta escala.** Coste total del experimento
real: −8,97 $.

---

## Método: lo que hizo creíble cada número

- **Reglas y criterios de abandono fijados antes de ver el dato**, escritos en el commit.
- **Controles en cada medida**: espejo (token contrario), azar emparejado y división en mitades
  temporales para la persistencia.
- **Cazar mis propios errores**, que fabricaron edges que no existían:
  - *Relojes desalineados*: las marcas de la cinta de Polymarket van **~3 s por delante** del reloj
    del colector. Cruzarlas sin corregir fabricó un edge de *maker* de +0,7 pp.
  - *Look-ahead* en la etiqueta de régimen (la hora entera incluía el futuro).
  - *Bug de volatilidad* (×10) que hacía parecer rentable una estrategia −EV.
  - *Estimador ruidoso*: el resultado binario da ±1,7 pp. Como el mercado está calibrado, el precio
    medio es un estimador insesgado del resultado con **4× menos ruido**, y se usó como medida principal.
- **El papel no basta**: la pregunta decisiva (qué órdenes te llenan) solo se pudo contestar con
  órdenes reales emparejadas una a una con su gemela simulada.

## Ingeniería

- **Ingesta en tiempo real**: WebSocket del libro de Polymarket y de Binance, Gamma API, CLOB API,
  Data API y Chainlink vía RPC de Polygon.
- **Ejecución**: órdenes firmadas (EIP-712) con `py-clob-client` v1/v2, *deposit wallet flow* del
  CLOB V2 y comisión verificada contra el propio CLOB hasta el sexto decimal.
- **Operación 24/7 en una Raspberry Pi** con systemd y un panel web Flask en la LAN.
- **Recuperación ante desastres**: la tarjeta SD murió tras 8 días grabando el libro al milisegundo.
  Se rescató la imagen con `dd` y se reconstruyeron los CSV fila a fila por huella, porque los
  ficheros no estaban contiguos en el disco: se pasó de recuperar el 8 % a recuperar el **98,4 %**.
  Desde entonces corre sobre un SSD por USB.

---

## Estructura

```
├── main.py, strategy.py, brain.py   # bot original (15m): ciclo por ventana y modelo de decisión
├── market.py, executor.py           # localizar el mercado (Gamma) y órdenes (CLOB)
├── data_feed.py                     # Chainlink (Polygon RPC) + Binance
├── dashboard.py, templates/         # panel web Flask
├── analysis.py, validate.py         # calibración, régimen y prueba del lag
└── research/                        # ~190 scripts de investigación; los principales:
    ├── whowins / persist / siguen.py   # wallets ganadoras y su persistencia
    ├── queuewatch.py, booklag.py       # grabación al ms y lag libro↔spot
    ├── stalepaper.py, stalebot.py      # bot en papel y bot real de la cotización vieja
    ├── fillsel.py, cruce.py            # selección del relleno: papel vs real, orden a orden
    ├── regimen.py, asienta.py, salida.py  # régimen, asentamiento del libro y cuándo salir
    └── clocklag.py, latency.py         # desfase de relojes y presupuesto de latencia
```

Los datos (`*.csv`) se generan en ejecución y no se suben al repositorio.

## Ejecutar

Requiere Python 3.10+. Copia `.env.example` a `.env` (las claves solo hacen falta para operar en
real; por defecto `DRY_RUN=true`).

```bash
pip install -r requirements.txt
python main.py        # bot
python dashboard.py   # panel en http://localhost:5000
```

---

*Proyecto de investigación personal. No es asesoramiento financiero.*
