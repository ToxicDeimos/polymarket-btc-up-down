"""
stalebot.py — EL BOT REAL, A TAMAÑO MÍNIMO. Su propósito NO es ganar dinero: es MEDIR EL RELLENO.

Todo lo demás está medido y validado en papel (ver [[btc-updown-libro-rancio]]):
  · mecanismo    +1,18 ± 0,19 de markout contra el medio asentado (z 6,2)
  · a resolución +6,30 ± 1,05 · compramos a 0,553 y ganamos el 63,3% cuando el libro asentado dice 57,8%
  · replica      dos días seguidos, mecanismo +1,24 / +1,16 y resolución +4,56 / +6,91
  · espejo       −14,35 ± 2,39 frente a +10,77 de la señal ⇒ la señal es direccional, no un fallo de cruce
  · cierre       descartado: el edge es MAYOR cuanto más tiempo queda de ventana
  · latencia     77 ms (firmar 0,9 + orden 52 + libro 25), dentro de la mejor fila

⚠️ LO QUE NO CUADRA, Y ES LA RAZÓN DE ESTE BOT: a 5 acciones y ~1.200 disparos/día, ese +6,30 daría
+378 $/día sobre ~40 $ de capital. Eso es IMPOSIBLE. El edge por operación está medido; lo que no sabemos es
cuántas operaciones existen de verdad. El límite invisible solo puede ser el RELLENO: que no nos llenen, que
nos den mucho menos tamaño del que muestra el libro, o que comprar mueva el precio.

El producto de este bot es el REGISTRO, no el P&L: qué pedimos, qué nos dieron, a qué precio y en cuánto
tiempo. Con eso se sabe si hay negocio o si el libro enseñaba un escaparate.

NO VENDE NUNCA: compra y deja que la ventana resuelva sola. Sin lógica de salida = una clase entera de
errores que no puede ocurrir.

SEGURIDAD (dos cerrojos, y por defecto NO opera):
  · sin --live  → modo simulado: hace todo menos mandar la orden
  · además hace falta  STALEBOT_LIVE=yes  en el entorno
  · tope de GASTO diario: como máximo se puede perder lo gastado, así que capar el gasto capa la pérdida
  · una orden por ventana · solo con >60 s de ventana por delante (con menos, el mecanismo era NEGATIVO)
  · fichero STOP en este directorio → deja de operar inmediatamente
  · la clave sale del entorno, nunca del código, y no se imprime jamás

⚠ El .env que vale es el de ESTE directorio (research/.env), nunca el de la raiz del proyecto: el de
  la raiz es de otra epoca y le faltan POLY_SIGNATURE_TYPE y POLY_FUNDER, sin las cuales el CLOB V2
  rechaza las ordenes con "maker address not allowed". Cargar el que no es falla de forma que no lo
  parece. Desde el 4-oct-2026 lo carga el propio bot (carga_env), asi que ya no hay que hacerlo a
  mano — antes olvidarlo imprimia el banner en modo REAL y se moria despues con "falta PRIVATE_KEY",
  dejando creer que estaba corriendo.

  STALEBOT_LIVE es la UNICA que no se coge del fichero: el modo real necesita el flag --live Y la
  variable, y si el .env pudiera poner la segunda bastaria el flag. Se escribe a mano, siempre.

    cp .env.example .env     &&  editar .env con la clave
    python3 stalebot.py                              # simulado, no manda nada
    STALEBOT_LIVE=yes python3 stalebot.py --live     # real, tamaño mínimo
"""
import websocket, json, time, threading, csv, os, sys, urllib.request, math

DIR = os.path.dirname(__file__)


def carga_env():
    """Mete research/.env en el entorno. NUNCA imprime valores.

    Antes el bot no leia ningun fichero y habia que lanzarlo con `set -a && . ./.env && set +a`
    delante. Olvidarlo no fallaba al arrancar: imprimia el banner en modo REAL y se moria despues
    con "falta PRIVATE_KEY", dejando creer que estaba corriendo. Paso el 4-oct-2026.

    Tres reglas:
    · SOLO el .env de ESTE directorio. El de la raiz del proyecto es del executor viejo (SDK v1) y
      trae otro signature_type/funder: cargarlo aqui firmaria con la cuenta equivocada.
    · Lo que YA esta en el entorno manda, para que una variable de la linea de comandos pise al
      fichero y no al reves.
    · STALEBOT_LIVE se EXCLUYE a proposito. El modo real exige DOS llaves (el flag --live y la
      variable); si el fichero pudiera poner la segunda, bastaria el flag y la guarda dejaria de
      existir. Que el bot cargue su clave solo es comodidad; que decida solo operar con dinero
      real, no.
    """
    p = os.path.join(DIR, ".env")
    if not os.path.exists(p): return
    for linea in open(p, encoding="utf-8", errors="replace"):
        linea = linea.strip()
        if not linea or linea.startswith("#"): continue
        if linea.startswith("export "): linea = linea[7:]
        k, sep, v = linea.partition("=")
        k = k.strip()
        if not sep or not k or k == "STALEBOT_LIVE" or k in os.environ: continue
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'": v = v[1:-1]
        os.environ[k] = v


carga_env()          # antes de leer ninguna variable, que varias son parametros del bot

LOG = os.path.join(DIR, "stalebot_log.csv")
STOP = os.path.join(DIR, "STOP")
WSS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
SPOT_WSS = "wss://stream.binance.com:9443/ws/btcusdt@bookTicker"
HOST = "https://clob.polymarket.com"

# ---- parámetros de la señal: IDÉNTICOS a stalepaper, para que el papel y lo real sean comparables ----
TRIG = ((0.2, 3.0), (0.5, 5.0), (1.0, 8.0))
COOL = 10.0
MIN_TTC = 60.0        # con menos de 60 s por delante el mecanismo salía NEGATIVO en el papel

# 🔑 FILTRO DE ACTIVIDAD. Mi hipotesis era que los ratos movidos darian mas margen; el papel dice lo
# CONTRARIO, y de forma monotona en cuatro cajones (regimen.py D, 16.461 disparos, actividad CAUSAL:
# disparos en los 60 min ANTERIORES, que es lo unico que se sabe en vivo):
#     tranquila <=102/h  mecanismo +3,46 ± 0,17  resolucion  +7,89 ± 0,62
#     media-baja                   +0,75 ± 0,12              +5,84 ± 0,65
#     media-alta                   +0,17 ± 0,11              +3,41 ± 0,65
#     agitada   >193/h             -0,23 ± 0,10              +1,80 ± 0,73
# El apartado B, por hora de RELOJ, daba el umbral en 64 y era dos veces mal: etiquetaba cada disparo con
# la hora entera INCLUIDO SU FUTURO, y una hora en la que el grabador solo estuvo 20 min cuenta pocos
# disparos y se colaba en "tranquila" sin serlo. El numero bueno es el Q1 causal.
# Cuando hay mucho movimiento los creadores de mercado estan atentos y repreciando; cuando esta tranquilo
# se despistan y dejan cotizaciones viejas. Se cuentan las deteccciones de los ultimos 60 min (ventana
# movil, no hora de reloj) y solo se opera por debajo del umbral. Con el tope de gasto limitando a ~9
# ordenes/dia, las ordenes son el recurso escaso: mejor pocas y buenas.
# ⚠ Al arrancar la lista esta VACIA: durante la primera hora contar a secas da siempre "tranquilo" y el
# filtro no muerde justo despues de un reinicio. Se cuenta como TASA: detecciones por hora extrapoladas al
# rato observado. Y hasta llevar WARM encendido no se opera, porque con 2 minutos la tasa es ruido puro.
MAX_ACT = int((os.environ.get("STALEBOT_MAX_ACT") or "102").strip())
DETS = []             # marcas de tiempo de las detecciones, para la ventana movil de 60 min
# 🔇 VIGILANTE DE SORDERA. Tres veces nos ha pasado ya que un proceso siga vivo, siga recorriendo
# ventanas y precargando caches, y NO DETECTE NADA durante horas: el 1-oct el colector estuvo 11,3 h
# mudo, y el 2-oct el bot 3,6 h sin una sola orden con los dos feeds funcionando perfectamente desde
# otro proceso. La deteccion muere en silencio porque "ask is None" descarta sin imprimir y los
# on_error son no-ops. Un bot con dinero que deja de funcionar y no lo dice es inaceptable: si pasan
# VACIAS ventanas seguidas, avisa; y si sigue, SALE, que es la unica forma de que alguien se entere.
VACIAS = [0]
MAX_VACIAS = 3
WARM = 300.0          # 5 min de calentamiento antes de la primera orden
T0 = time.time()

# ⏱️ A/B DE LATENCIA (4-oct-2026). La pregunta que queda viva en este mercado: ¿nos llena poco (6%)
# porque llegamos tarde, o porque perdemos la cola pase lo que pase? Las dos medidas que tenemos se
# contradicen y ninguna tiene potencia:
#   · el papel dice que el relleno cae del 92% al 61% entre 52 y 200 ms  → la velocidad lo es todo
#   · el PC es 55 ms mas rapido que la Pi y llena IGUAL (6% vs 7%)       → la velocidad da igual
#     pero con 175 y 140 ordenes ese test no distingue un 6% de un 9%.
# Aqui se zanja con un experimento controlado: ventanas PARES normales, ventanas IMPARES con el envio
# retrasado AB_MS ms a proposito. Mismo bot, misma maquina, mismas señales, mismo filtro de regimen;
# lo unico que cambia es la latencia. Se apunta en la columna 'retraso' y lo lee botlog.py.
#   · si la rama retrasada llena MUCHO menos → la velocidad es la restriccion y vale la pena medir
#     cuanto se gana acercandose al origen de Polymarket (un VPS europeo ahorra ~30 ms de red).
#   · si llenan IGUAL con 250 ms de diferencia → no es velocidad, es la cola, y entonces ningun
#     VPS ni ninguna optimizacion sirve. Pregunta cerrada con dato, no con opinion.
# Se eligen 250 ms a proposito: es mucho mas que los 30-55 que podriamos ganar, asi que si ESTO no
# mueve el relleno, lo pequeño tampoco. Potencia primero; el tamaño fino del efecto, despues.
AB_MS = int((os.environ.get("STALEBOT_AB_MS") or "250").strip())


def tasa(t):
    """Detecciones por hora. Antes de llevar una hora encendido se extrapola el rato observado."""
    return len(DETS) * 3600.0 / min(max(t - T0, 1.0), 3600.0)

# ---- límites duros ----
SIZE = 5              # mínimo del mercado (minimum_order_size)
MAX_PRICE = 0.95      # no perseguir precios casi resueltos
MIN_PRICE = 0.05
# El tope de GASTO es a la vez el capital necesario y la pérdida máxima: no se puede perder más de lo
# gastado. Se puede bajar sin tocar el código:  STALEBOT_MAX_SPEND=5 STALEBOT_MAX_ORDERS=2 ...
MAX_SPEND_DAY = float(os.environ.get("STALEBOT_MAX_SPEND", "25"))
MAX_ORDERS_DAY = int(os.environ.get("STALEBOT_MAX_ORDERS", "200"))

# "cid" = conditionId del mercado. Lo conocemos al descubrir la ventana y NO lo guardabamos: sin el,
# pasadas unas horas no hay forma de resolver nuestras propias operaciones, porque Gamma solo devuelve
# mercados recientes y el puente ws->cid del laboratorio ya no existe. Sin resolver no hay margen
# realizado, y el margen realizado es LO UNICO que decide si esto paga.
H = ["ts", "ws", "tok", "token_id", "cid", "ttc", "ask_visto", "tam_visto", "precio_pedido",
     "size_pedido", "actividad", "ms_precarga", "retraso", "modo", "ms_envio", "estado",
     "size_llenado", "precio_medio",
     "order_id", "error", "respuesta_cruda"]
LOCK = threading.Lock()
LIVE = "--live" in sys.argv and os.environ.get("STALEBOT_LIVE") == "yes"
DIA = {"fecha": None, "gasto": 0.0, "ordenes": 0}
CLIENT = [None]
avisado = [False]     # el aviso de tope agotado se da UNA vez, no una por deteccion


def get(url, tries=2, timeout=6):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "sbot/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r: return json.load(r)
        except Exception:
            if i == tries - 1: return None
            time.sleep(0.2)


def discover(ws):
    d = get(f"https://gamma-api.polymarket.com/markets?slug=btc-updown-5m-{ws}")
    if not (isinstance(d, list) and d): return None
    m = d[0]
    try:
        cid = m.get("conditionId")
        toks = dict(zip(json.loads(m["outcomes"]), json.loads(m["clobTokenIds"])))
    except Exception:
        return None
    # tick y neg_risk se leen del CLOB, que es quien casa la orden. Gamma puede dar otro tick y estos
    # mercados usan 0,001: si mandamos 0,01 con un ask de 0,553, la orden se rechaza o se redondea mal.
    c = get(f"https://clob.polymarket.com/markets/{cid}") or {}
    tick = str(c.get("minimum_tick_size") or m.get("orderPriceMinTickSize") or "0.01")
    return {"cid": cid, "toks": toks, "tick": tick, "neg_risk": bool(c.get("neg_risk", False)),
            "acepta": c.get("accepting_orders", True)}


def apunta(row):
    with LOCK:
        # Si el fichero existe con OTRA cabecera se aparta (no se borra) y se empieza uno nuevo: escribir
        # a ciegas sobre una cabecera vieja deja columnas sin nombre y el analisis las descarta en silencio.
        if os.path.exists(LOG):
            try:
                with open(LOG, encoding="utf-8") as f: vieja = next(csv.reader(f), [])
            except Exception: vieja = []
            if vieja and vieja != H:
                os.rename(LOG, LOG.replace(".csv", time.strftime("_%Y%m%d%H%M%S.csv")))
        nuevo = not os.path.exists(LOG)
        with open(LOG, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if nuevo: w.writerow(H)
            w.writerow(row)


def hoy(): return time.strftime("%Y-%m-%d", time.gmtime())


def puedo_gastar(coste):
    """Devuelve el motivo por el que NO se puede operar, o None si se puede."""
    if os.path.exists(STOP): return "fichero STOP"
    if DIA["fecha"] != hoy():
        DIA.update(fecha=hoy(), gasto=0.0, ordenes=0)
    if DIA["ordenes"] >= MAX_ORDERS_DAY: return f"tope de {MAX_ORDERS_DAY} ordenes/dia"
    if DIA["gasto"] + coste > MAX_SPEND_DAY: return f"tope de {MAX_SPEND_DAY:.0f}$/dia"
    return None


def _api_creds():
    """Usa las credenciales de API que ya existen en el .env, si estan. Los nombres de los campos de
    ApiCreds se leen del propio SDK: es codigo de terceros y no quiero adivinarlos."""
    from py_clob_client_v2 import ApiCreds
    k = os.environ.get("POLYMARKET_API_KEY")
    sec = os.environ.get("POLYMARKET_SECRET")
    pas = os.environ.get("POLYMARKET_PASSPHRASE")
    if not (k and sec and pas): return None
    campos = list(getattr(ApiCreds, "__annotations__", {}) or {})
    vale = {"api_key": k, "key": k, "api_secret": sec, "secret": sec,
            "api_passphrase": pas, "passphrase": pas}
    kw = {c: vale[c] for c in campos if c in vale}
    if len(kw) < 3:
        raise RuntimeError(f"no se rellenar ApiCreds; sus campos son {campos}")
    return ApiCreds(**kw)


def arranca_cliente():
    """Solo se llama en modo real. La clave sale del entorno y NUNCA se imprime."""
    from py_clob_client_v2 import ClobClient
    pk = os.environ.get("PRIVATE_KEY") or os.environ.get("POLY_PK")
    if not pk: raise RuntimeError("falta PRIVATE_KEY en el entorno (ver .env.example)")
    kw = {"host": HOST, "chain_id": 137, "key": pk}
    st = os.environ.get("POLY_SIGNATURE_TYPE")
    fu = os.environ.get("POLY_FUNDER")
    if st: kw["signature_type"] = int(st)
    if fu: kw["funder"] = fu
    creds = _api_creds()
    if creds is not None:
        kw["creds"] = creds
        c = ClobClient(**kw)
        print("cliente CLOB con las credenciales del .env (nada de esto se imprime)", flush=True)
    else:
        c = ClobClient(**kw)
        kw["creds"] = c.create_or_derive_api_key()
        c = ClobClient(**kw)
        print("cliente CLOB con credenciales derivadas de la clave", flush=True)
    return c


def cuantas(ask):
    """Acciones a pedir. Hay DOS minimos y solo conociamos uno:
         · 5 acciones  (minimum_order_size del mercado)
         · 1,00 $ de importe  ->  "invalid amount for a marketable BUY order ($0.95), min size: 1"
       Con el ask a 0,19 las 5 acciones son 0,95 $ y el CLOB la rechaza. Se sube lo justo."""
    return max(SIZE, math.ceil(1.01 / ask)) if ask > 0 else SIZE


def manda_orden(token_id, precio, tick, neg_risk, n):
    """Compra FAK (inmediata, admite relleno parcial) al precio visto. Devuelve (estado, size, precio, id, err)."""
    from py_clob_client_v2 import OrderArgs, OrderType, PartialCreateOrderOptions, Side
    r = CLIENT[0].create_and_post_order(
        order_args=OrderArgs(token_id=token_id, price=precio, side=Side.BUY, size=n),
        options=PartialCreateOrderOptions(tick_size=tick, neg_risk=neg_risk),
        order_type=OrderType.FAK)
    d = r if isinstance(r, dict) else getattr(r, "__dict__", {"resp": str(r)})
    # Formato REAL, visto por fin el 2-oct-2026 en el primer relleno:
    #   {'errorMsg': '', 'orderID': '0x…', 'takingAmount': '5', 'makingAmount': '2.9',
    #    'status': 'matched', 'transactionsHashes': ['0x…'], 'success': True}
    # takingAmount = acciones recibidas · makingAmount = USDC pagados. NO hay campo 'price':
    # lo adiviné y el contador caía al respaldo size*ask, que aquel dia coincidio por suerte.
    # makingAmount es el dato bueno para el gasto, porque es lo que de verdad salio de la cuenta.
    size = d.get("takingAmount") or d.get("size_matched") or d.get("sizeMatched") or ""
    pagado = d.get("makingAmount")                      # None si no viene: ahi NO se puede suponer 0
    try: px = round(float(pagado) / float(size), 4) if (pagado and size) else ""
    except Exception: px = ""
    return (d.get("status") or d.get("success") or "?", size, px,
            d.get("orderID") or d.get("order_id") or "",
            d.get("errorMsg") or "", str(d)[:400], pagado)


def ventana(ws, mk):
    toks = mk["toks"]; id2 = {toks["Up"]: "Up", toks["Down"]: "Down"}
    close = ws + 300
    # Rama del A/B: alternar por VENTANA y no por orden, para que las dos ramas vean mercados
    # distintos pero equivalentes en media. Alternar dentro de la misma ventana las emparejaria
    # mejor, pero las ordenes de una ventana no son independientes (mismo salto, mismo libro).
    retraso = AB_MS if (ws // 300) % 2 else 0
    bk = {"Up": [None, None, 0.0], "Down": [None, None, 0.0]}
    lv = {"Up": {}, "Down": {}}
    hist = []; ultimo = [0.0]; hecho = [False]; dicho = [False]   # dicho: ya avisamos en esta ventana
    ndet = [0]; sinlibro = [0]; nticks = [0]    # detecciones / descartadas sin libro / ticks de spot
    # La precarga mide el MISMO camino que recorrera la orden, pero al abrir la ventana y sin
    # nada en juego: es una sonda gratuita del estado de la red. Se registra para comprobar si
    # predice los envios lentos (una precarga de 1.128 ms precedio a una orden de 5.379). Si la
    # correlacion existe, saltarse esas ventanas sale gratis. Con n=1 todavia no se toca nada.
    msprec = [None]

    # ⛔ Antes aqui se calentaba la CONEXION con un get_ok(). Medido y NO servia: 402 ms de mediana
    # sin calentar (n=8), 401 calentando (n=9). Lo que habia que calentar era otra cosa.
    #
    # ✅ Lo que SI cuesta: create_order() hace DOS peticiones HTTP antes de firmar, aunque le demos
    # el tick y el neg_risk hechos — GET /tick-size?token_id=… y GET /version. Medido: 100 + 106 ms.
    # De los ~450 ms del envio, 206 eran el SDK preguntando lo que ya sabiamos. Y LAS CACHEA: la
    # segunda firma del mismo token baja a 8 ms con cero peticiones. El tick se cachea por token, la
    # version es global; el lado contrario cuesta su propio tick-size (81 ms).
    # Asi que se firma una orden de mentira de CADA lado al abrir la ventana — nunca se envia, se
    # tira — y la de verdad sale con las cachés llenas. Aqui sobran 300 s; luego sobran 100 ms.
    if LIVE and CLIENT[0] is not None:
        from py_clob_client_v2 import OrderArgs, PartialCreateOrderOptions, Side
        t0 = time.time()
        try:
            op = PartialCreateOrderOptions(tick_size=mk["tick"], neg_risk=mk["neg_risk"])
            for tk in ("Up", "Down"):
                CLIENT[0].create_order(
                    order_args=OrderArgs(token_id=toks[tk], price=0.50, side=Side.BUY, size=10),
                    options=op)                      # firmada y descartada: no sale de aqui
            msprec[0] = round(1000 * (time.time() - t0), 1)
            print(f"   [cachés] tick y version precargados en {msprec[0]:.0f} ms", flush=True)
        except Exception as e:
            print(f"   [cachés] no se pudieron precargar: {str(e)[:70]}", flush=True)

    def on_open(w): w.send(json.dumps({"type": "market", "assets_ids": [toks["Up"], toks["Down"]]}))

    def on_book(w, msg):
        try: data = json.loads(msg)
        except Exception: return
        for d in (data if isinstance(data, list) else [data]):
            et = d.get("event_type"); aid = d.get("asset_id")
            if et == "book" and aid in id2:
                tok = id2[aid]
                bids = [float(x["price"]) for x in d.get("bids", [])]
                asks = sorted((float(x["price"]), float(x["size"])) for x in d.get("asks", []))
                lv[tok] = {p: s for p, s in asks}
                bk[tok][0] = max(bids) if bids else None
                bk[tok][1] = asks[0][0] if asks else None
                bk[tok][2] = asks[0][1] if asks else 0.0
            elif et == "price_change":
                for ch in d.get("price_changes", []):
                    a = ch.get("asset_id")
                    if a not in id2: continue
                    tok = id2[a]
                    try:
                        if (ch.get("side") or "").upper().startswith("S"):
                            lv[tok][float(ch["price"])] = float(ch.get("size", 0))
                        ba = ch.get("best_ask"); bb = ch.get("best_bid")
                        if bb not in (None, ""): bk[tok][0] = float(bb)
                        if ba not in (None, ""):
                            bk[tok][1] = float(ba); bk[tok][2] = lv[tok].get(float(ba), 0.0)
                    except Exception: pass

    def dispara(t, tok):
        ask, tam = bk[tok][1], bk[tok][2]
        ttc = close - t
        n = cuantas(ask)
        coste = ask * n
        motivo = puedo_gastar(coste)
        base = [round(t, 3), ws, tok, toks[tok], mk.get("cid", ""), round(ttc, 1), ask, round(tam),
                ask, n, round(tasa(t)), msprec[0] if msprec[0] is not None else "", retraso]
        if motivo:
            # 🔇 Otra puerta muda: al agotarse el tope el bot dejaba de operar sin decir nada y
            # parecia que se habia quedado sordo. Se avisa UNA vez (no una por deteccion).
            if not avisado[0]:
                avisado[0] = True
                print(f"   🛑 {motivo} · gastado {DIA['gasto']:.2f}$ en {DIA['ordenes']} ordenes. "
                      f"Sigue vigilando pero YA NO OPERA — relanzar para otra tanda.", flush=True)
            apunta(base + ["bloqueado", "", motivo, "", "", "", "", ""]); return
        if not LIVE:
            apunta(base + ["simulado", "", "no enviado", "", "", "", "", ""])
            print(f"   [simulado] {tok} a {ask} · {tam:.0f} acciones en el ask · "
                  f"{tasa(t):.0f} det/h · quedan {ttc:.0f}s", flush=True)
            return
        # 🔧 PENDIENTE: una orden fallo con "The read operation timed out". Con un edge que dura 116 ms,
        # una orden que sale 3 s tarde compra al precio YA corregido. Hay que ponerle plazo corto y abortar
        # si se pasa, pero el plazo se elige viendo cuanto tardan las que SI pasan: falta una sesion con
        # saldo suficiente para tener esos ms_envio.
        # ⏱️ El retraso del A/B va AQUI, justo antes de enviar y despues de decidir: asi la rama
        # lenta ve exactamente la misma señal y toma exactamente la misma decision que la rapida,
        # y lo unico que las separa es cuando llega la orden. ms_envio sigue midiendo solo el envio.
        if retraso: time.sleep(retraso / 1000.0)
        t0 = time.time()
        try:
            est, size, px, oid, err, crudo, pagado = manda_orden(toks[tok], ask, mk["tick"],
                                                                  mk["neg_risk"], n)
        except Exception as e:
            ms = round(1000 * (time.time() - t0), 1); txt = str(e)
            # Un FAK sin contraparte es un NO rotundo del propio CLOB: no se compro nada, no se gasto
            # nada, y contarlo como gasto quema el presupuesto en operaciones que no existieron.
            # Distinto de un plazo agotado, que se agota en NUESTRO lado y deja la duda de si entro:
            # 🔒 ahi se sigue asumiendo lo peor. Solo esta frase exacta cuenta como gasto cero.
            matada = "no orders found to match" in txt
            DIA["ordenes"] += 1
            if not matada: DIA["gasto"] += coste
            apunta(base + ["real", ms, "matada" if matada else "excepcion",
                           0 if matada else "", "", "", txt[:180], ""])
            if matada:
                print(f"   [matada] nadie vendia {tok} a {ask} cuando llego la orden · {ms:.0f} ms · "
                      f"habia {tam:.0f} acciones al enviarla", flush=True)
            else:
                print(f"   [error] {e}", flush=True)
            return
        ms = round(1000 * (time.time() - t0), 1)
        DIA["ordenes"] += 1
        # makingAmount son los USDC que REALMENTE salieron de la cuenta: si viene, manda, aunque sea 0
        # (un 0 explicito es "no se lleno nada", informacion buena). Si NO viene, no se puede suponer
        # cero -> el tope dejaria de morder. 🔒 Ante la duda, lo peor posible.
        if pagado is not None:
            try: gastado = float(pagado)
            except Exception: gastado = coste
        else:
            try: gastado = float(size) * float(px or ask)
            except Exception: gastado = coste
            if not (gastado > 0): gastado = coste
        DIA["gasto"] += gastado
        apunta(base + ["real", ms, est, size, px, oid, err, crudo])
        print(f"   [real] {tok} pedido {n}@{ask} → {est} size={size} en {ms:.0f}ms · "
              f"gastado hoy {DIA['gasto']:.2f}$", flush=True)

    def on_spot(w, msg):
        t = time.time()
        # Se cuenta ANTES de cualquier filtro: un tick recibido demuestra que el feed VIVE, aunque
        # BTC este plano y no dispare nada. Sin esto el vigilante confunde "mercado tranquilo" con
        # "feed muerto" y apagaria el bot de madrugada por las buenas.
        nticks[0] += 1
        # El enfriamiento va sobre la ULTIMA DETECCION, no sobre la ultima orden: hay que seguir contando
        # aunque ya hayamos operado en esta ventana, porque la actividad se mide igual que en el papel.
        if t - ultimo[0] < COOL: return
        try:
            d = json.loads(msg); mid = (float(d["b"]) + float(d["a"])) / 2
        except Exception: return
        hist.append((t, mid))
        while hist and hist[0][0] < t - 4.5: hist.pop(0)

        def mv(wn):
            r = None
            for tt, pp in hist:
                if tt <= t - wn: r = pp
                else: break
            return (mid - r) if r is not None else None

        disp = None
        for wn, thr in TRIG:
            m = mv(wn)
            if m is not None and abs(m) >= thr: disp = m; break
        if disp is None: return

        # DETECCION: se cuenta SIEMPRE, aunque no vayamos a operar. Ventana movil de 60 min.
        ultimo[0] = t
        DETS.append(t); ndet[0] += 1
        while DETS and DETS[0] < t - 3600: DETS.pop(0)

        if hecho[0]: return                      # UNA orden por ventana
        if close - t < MIN_TTC: return
        tok = "Up" if disp > 0 else "Down"
        ask = bk[tok][1]
        # 🔇 Esta puerta era MUDA, y por eso el 2-oct nos quedamos sin diagnostico: si el libro de
        # Polymarket se vacia, Binance puede seguir detectando a destajo y no sale ni una orden, sin
        # distinguirse de "no hubo detecciones". Se cuentan aparte para que la proxima vez se sepa
        # CUAL de los dos feeds murio.
        if ask is None: sinlibro[0] += 1; return
        if not (MIN_PRICE <= ask <= MAX_PRICE): return
        if t - T0 < WARM:                        # aun no se sabe si el rato es tranquilo o agitado
            if not dicho[0]:
                dicho[0] = True
                print(f"   [espera] calentando, quedan {WARM - (t - T0):.0f}s", flush=True)
            return
        act = tasa(t)
        if act > MAX_ACT:                        # rato agitado: el papel dice que ahi no hay margen
            if not dicho[0]:                     # una linea por ventana, no una por deteccion
                dicho[0] = True
                print(f"   [saltado] rato agitado · {act:.0f} detecciones/hora > {MAX_ACT}", flush=True)
            return
        hecho[0] = True
        dispara(t, tok)

    app = websocket.WebSocketApp(WSS, on_open=on_open, on_message=on_book, on_error=lambda a, b: None)
    threading.Thread(target=lambda: app.run_forever(ping_interval=20, ping_timeout=10), daemon=True).start()
    sapp = websocket.WebSocketApp(SPOT_WSS, on_message=on_spot, on_error=lambda a, b: None)
    threading.Thread(target=lambda: sapp.run_forever(ping_interval=20, ping_timeout=10), daemon=True).start()
    while time.time() < close - 5: time.sleep(0.4)
    app.close(); sapp.close()
    # SORDO no es lo mismo que TRANQUILO, y confundirlos apaga el bot de madrugada:
    #   · 0 ticks de spot            -> el feed de Binance esta muerto           -> SORDO
    #   · ticks pero 0 detecciones   -> BTC plano, todo correcto                 -> normal
    #   · detecciones sin libro      -> el feed de Polymarket esta muerto        -> SORDO
    mudo = (nticks[0] == 0) or (ndet[0] > 0 and sinlibro[0] >= ndet[0])
    VACIAS[0] = VACIAS[0] + 1 if mudo else 0
    if nticks[0] == 0:
        print(f"   ⚠ ventana sin UN SOLO tick de Binance ({VACIAS[0]}/{MAX_VACIAS}) — feed muerto",
              flush=True)
    elif ndet[0] and sinlibro[0] >= ndet[0]:
        print(f"   ⚠ {ndet[0]} detecciones y NINGUNA con libro ({VACIAS[0]}/{MAX_VACIAS}) — "
              f"el feed de POLYMARKET esta mudo", flush=True)
    elif not ndet[0]:
        print(f"   (sin disparos: {nticks[0]} ticks, BTC plano — el feed va bien)", flush=True)
    elif sinlibro[0]:
        print(f"   ({sinlibro[0]} de {ndet[0]} detecciones sin libro)", flush=True)


def main():
    modo = "REAL (dinero de verdad)" if LIVE else "SIMULADO (no manda nada)"
    print("=" * 74)
    print(f"  stalebot · modo {modo}")
    print(f"  tamaño {SIZE} acciones · tope {MAX_SPEND_DAY:.0f}$/día · {MAX_ORDERS_DAY} órdenes/día")
    print(f"  una orden por ventana · solo con >{MIN_TTC:.0f}s por delante · precio {MIN_PRICE}-{MAX_PRICE}")
    print(f"  solo en ratos TRANQUILOS: <={MAX_ACT} detecciones/hora "
          f"(el papel: tranquilo +7,89 a resolucion · agitado +1,80)")
    print(f"  los primeros {WARM/60:.0f} min no se opera: hace falta rato para saber si esta tranquilo")
    print(f"  para parar en caliente:  touch {STOP}")
    print("=" * 74, flush=True)
    if LIVE:
        if os.path.exists(STOP):
            print("existe el fichero STOP: no arranco. Bórralo si quieres operar."); return
        CLIENT[0] = arranca_cliente()
    elif "--live" in sys.argv:
        print("  ⚠ pasaste --live pero falta STALEBOT_LIVE=yes en el entorno → sigo en simulado", flush=True)
    try:
        while True:
            t = time.time(); ws = int(t - (t % 300))
            if t - ws > 230: time.sleep(300 - (t - ws) + 1); continue
            mk = discover(ws)
            if not mk: time.sleep(10); continue
            if not mk.get("acepta", True):
                print(f"── {ws} el mercado NO acepta ordenes, la salto", flush=True)
                time.sleep(20); continue
            rama = "LENTA +%d ms" % AB_MS if (ws // 300) % 2 else "RAPIDA"
            print(f"── {ws} tick {mk['tick']} · neg_risk {mk['neg_risk']} · "
                  f"cierra en {int(ws + 300 - time.time())}s · rama {rama}", flush=True)
            ventana(ws, mk)
            if VACIAS[0] >= MAX_VACIAS:
                print("", flush=True)
                print(f"⛔ {VACIAS[0]} ventanas seguidas SIN UNA SOLA DETECCION.", flush=True)
                print("   Los feeds estan mudos aunque el proceso siga vivo. Salgo para que se note.",
                      flush=True)
                print("   Relanzar; si se repite enseguida, mirar las conexiones WSS.", flush=True)
                return
    except KeyboardInterrupt:
        print("\nparando…", flush=True)


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
