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

    cp .env.example .env     &&  editar .env con la clave
    python3 stalebot.py                      # simulado, no manda nada
    STALEBOT_LIVE=yes python3 stalebot.py --live     # real, tamaño mínimo
"""
import websocket, json, time, threading, csv, os, sys, urllib.request

DIR = os.path.dirname(__file__)
LOG = os.path.join(DIR, "stalebot_log.csv")
STOP = os.path.join(DIR, "STOP")
WSS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
SPOT_WSS = "wss://stream.binance.com:9443/ws/btcusdt@bookTicker"
HOST = "https://clob.polymarket.com"

# ---- parámetros de la señal: IDÉNTICOS a stalepaper, para que el papel y lo real sean comparables ----
TRIG = ((0.2, 3.0), (0.5, 5.0), (1.0, 8.0))
COOL = 10.0
MIN_TTC = 60.0        # con menos de 60 s por delante el mecanismo salía NEGATIVO en el papel

# ---- límites duros ----
SIZE = 5              # mínimo del mercado (minimum_order_size)
MAX_PRICE = 0.95      # no perseguir precios casi resueltos
MIN_PRICE = 0.05
# El tope de GASTO es a la vez el capital necesario y la pérdida máxima: no se puede perder más de lo
# gastado. Se puede bajar sin tocar el código:  STALEBOT_MAX_SPEND=5 STALEBOT_MAX_ORDERS=2 ...
MAX_SPEND_DAY = float(os.environ.get("STALEBOT_MAX_SPEND", "25"))
MAX_ORDERS_DAY = int(os.environ.get("STALEBOT_MAX_ORDERS", "200"))

H = ["ts", "ws", "tok", "token_id", "ttc", "ask_visto", "tam_visto", "precio_pedido", "size_pedido",
     "modo", "ms_envio", "estado", "size_llenado", "precio_medio", "order_id", "error"]
LOCK = threading.Lock()
LIVE = "--live" in sys.argv and os.environ.get("STALEBOT_LIVE") == "yes"
DIA = {"fecha": None, "gasto": 0.0, "ordenes": 0}
CLIENT = [None]


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


def arranca_cliente():
    """Solo se llama en modo real. La clave sale del entorno y NUNCA se imprime."""
    from py_clob_client_v2 import ClobClient
    pk = os.environ.get("POLY_PK")
    if not pk: raise RuntimeError("falta POLY_PK en el entorno (ver .env.example)")
    kw = {"host": HOST, "chain_id": 137, "key": pk}
    st = os.environ.get("POLY_SIGNATURE_TYPE")
    fu = os.environ.get("POLY_FUNDER")
    if st: kw["signature_type"] = int(st)
    if fu: kw["funder"] = fu
    c = ClobClient(**kw)
    creds = c.create_or_derive_api_key()
    kw["creds"] = creds
    c = ClobClient(**kw)
    print("cliente CLOB autenticado (la clave no se muestra)", flush=True)
    return c


def manda_orden(token_id, precio, tick, neg_risk):
    """Compra FAK (inmediata, admite relleno parcial) al precio visto. Devuelve (estado, size, precio, id, err)."""
    from py_clob_client_v2 import OrderArgs, OrderType, PartialCreateOrderOptions, Side
    r = CLIENT[0].create_and_post_order(
        order_args=OrderArgs(token_id=token_id, price=precio, side=Side.BUY, size=SIZE),
        options=PartialCreateOrderOptions(tick_size=tick, neg_risk=neg_risk),
        order_type=OrderType.FAK)
    d = r if isinstance(r, dict) else getattr(r, "__dict__", {"resp": str(r)})
    size = d.get("takingAmount") or d.get("size_matched") or d.get("sizeMatched") or ""
    return (d.get("status") or d.get("success") or "?", size,
            d.get("price") or "", d.get("orderID") or d.get("order_id") or "", d.get("errorMsg") or "")


def ventana(ws, mk):
    toks = mk["toks"]; id2 = {toks["Up"]: "Up", toks["Down"]: "Down"}
    close = ws + 300
    bk = {"Up": [None, None, 0.0], "Down": [None, None, 0.0]}
    lv = {"Up": {}, "Down": {}}
    hist = []; ultimo = [0.0]; hecho = [False]

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
        coste = ask * SIZE
        motivo = puedo_gastar(coste)
        base = [round(t, 3), ws, tok, toks[tok], round(ttc, 1), ask, round(tam),
                ask, SIZE]
        if motivo:
            apunta(base + ["bloqueado", "", motivo, "", "", "", ""]); return
        if not LIVE:
            apunta(base + ["simulado", "", "no enviado", "", "", "", ""])
            print(f"   [simulado] {tok} a {ask} ({tam:.0f} disp.) · quedan {ttc:.0f}s", flush=True)
            return
        t0 = time.time()
        try:
            est, size, px, oid, err = manda_orden(toks[tok], ask, mk["tick"], mk["neg_risk"])
        except Exception as e:
            apunta(base + ["real", round(1000 * (time.time() - t0), 1), "excepcion", "", "", "", str(e)[:180]])
            print(f"   [error] {e}", flush=True); return
        ms = round(1000 * (time.time() - t0), 1)
        DIA["ordenes"] += 1
        try: DIA["gasto"] += float(size or 0) * float(px or ask)
        except Exception: DIA["gasto"] += coste
        apunta(base + ["real", ms, est, size, px, oid, err])
        print(f"   [real] {tok} pedido {SIZE}@{ask} → {est} size={size} en {ms:.0f}ms · "
              f"gastado hoy {DIA['gasto']:.2f}$", flush=True)

    def on_spot(w, msg):
        t = time.time()
        if hecho[0] or t - ultimo[0] < COOL: return
        try:
            d = json.loads(msg); mid = (float(d["b"]) + float(d["a"])) / 2
        except Exception: return
        hist.append((t, mid))
        while hist and hist[0][0] < t - 4.5: hist.pop(0)
        if close - t < MIN_TTC: return

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
        tok = "Up" if disp > 0 else "Down"
        ask = bk[tok][1]
        if ask is None or not (MIN_PRICE <= ask <= MAX_PRICE): return
        ultimo[0] = t; hecho[0] = True          # UNA orden por ventana
        dispara(t, tok)

    app = websocket.WebSocketApp(WSS, on_open=on_open, on_message=on_book, on_error=lambda a, b: None)
    threading.Thread(target=lambda: app.run_forever(ping_interval=20, ping_timeout=10), daemon=True).start()
    sapp = websocket.WebSocketApp(SPOT_WSS, on_message=on_spot, on_error=lambda a, b: None)
    threading.Thread(target=lambda: sapp.run_forever(ping_interval=20, ping_timeout=10), daemon=True).start()
    while time.time() < close - 5: time.sleep(0.4)
    app.close(); sapp.close()


def main():
    modo = "REAL (dinero de verdad)" if LIVE else "SIMULADO (no manda nada)"
    print("=" * 74)
    print(f"  stalebot · modo {modo}")
    print(f"  tamaño {SIZE} acciones · tope {MAX_SPEND_DAY:.0f}$/día · {MAX_ORDERS_DAY} órdenes/día")
    print(f"  una orden por ventana · solo con >{MIN_TTC:.0f}s por delante · precio {MIN_PRICE}-{MAX_PRICE}")
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
            print(f"── {ws} tick {mk['tick']} · neg_risk {mk['neg_risk']} · "
                  f"cierra en {int(ws + 300 - time.time())}s", flush=True)
            ventana(ws, mk)
    except KeyboardInterrupt:
        print("\nparando…", flush=True)


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
