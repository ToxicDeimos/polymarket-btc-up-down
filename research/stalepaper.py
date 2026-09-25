"""
stalepaper.py — EL BOT, EN PAPEL. NO OPERA: reacciona de verdad y anota qué habría comprado.

Todo lo demás ya está medido (ver [[btc-updown-libro-rancio]]):
  · mecanismo    comprar el ask a los 100 ms de un salto de BTC ≥$10 → +2,12 ± 0,39pp (z 5,4), espejo −6,00
  · ruta viable  comprar y AGUANTAR a resolución (salir cruzando deja cero: el viaje de vuelta cuesta
                 medio spread + otra comisión)
  · comisión     verificada: fee = C × 0,07 × p × (1−p) en cripto; el maker paga 0
  · latencia     firmar 0,9 ms + mandar la orden 52 + recibir el libro 25 = 77 ms
  · ventana      a los 77 ms sigue viva el 67% de las cotizaciones (mediana 116 ms)

Lo que NINGUNA simulación puede dar, y esto sí:
  1) nuestro tiempo de reacción REAL — cuándo decide el código que eso es un salto, no cuándo lo decide un
     bucle offline que ya conoce la serie entera;
  2) el ask que tendríamos EN MEMORIA en ese instante, que es el que de verdad podríamos levantar, no el
     que un bisect encuentra a toro pasado;
  3) si las dos cosas coinciden con lo medido offline. Si el papel sale bastante peor que stalebt, la
     diferencia es fricción real y hay que entenderla ANTES de arriesgar nada.

Por cada disparo anota el libro a +0, +52 (nuestra latencia de orden), +100 y +200 ms, y el medio asentado
a los 8 s. La resolución se cruza después, offline, como en stalebt.

    cd ~/polymarket-btc-up-down/research && python3 stalepaper.py            # vivo
    python3 stalepaper.py --analyze                                          # qué habría dado
"""
import websocket, json, time, threading, csv, os, sys, glob, bisect, urllib.request

DIR = os.path.dirname(__file__)
# El fichero va VERSIONADO. Al añadir las 4 columnas de movimiento, el CSV ya existía con la cabecera
# vieja de 15 y write() solo la escribe si el fichero no existe: las columnas nuevas se guardaban pero sin
# nombre, y DictReader las tiraba — por eso el barrido salía vacío. Los datos viejos NO se tocan: siguen en
# stalepaper.csv y --analyze lee todos los ficheros, cada uno con su propia cabecera.
LOG = os.path.join(DIR, "stalepaper_v4.csv")
WSS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
SPOT_WSS = "wss://stream.binance.com:9443/ws/btcusdt@bookTicker"

# No se eligen ventanas: se graba el movimiento en una rejilla FINA y el barrido calcula la que quiera.
# El coste es una columna por ventana y cero CPU (sale del historial que ya esta en memoria).
# OJO AL LEER: 12 ventanas x 6 umbrales son 72 casillas y alguna brillara por azar. El criterio NO es
# "cual gana" sino la FORMA: una pendiente suave significa algo, una casilla suelta entre vecinas planas
# es ruido. Es el error que mato al candidato del 15m.
MVW = (0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0)

def mvcol(w): return "mv%d" % round(w * 1000)      # en MILISEGUNDOS: mv50, mv100, ... mv3000

# Los ficheros viejos nombran las mismas ventanas de otra forma (mv05 = 0,5 s, mv1 = 1 s...). Al pasar a
# milisegundos se quedaron fuera del barrido y perdimos 750 disparos ya grabados. El lector acepta ambos.
ALIAS = {0.1: "mv01", 0.2: "mv02", 0.3: "mv03", 0.5: "mv05", 1.0: "mv1", 2.0: "mv2", 3.0: "mv3"}

def mvget(r, w):
    """movimiento de la ventana w en esa fila, venga con el nombre nuevo o con el viejo."""
    for c in (mvcol(w), ALIAS.get(w)):
        if not c: continue
        v = r.get(c)
        if v not in (None, ""):
            try: return float(v)
            except Exception: pass
    return None

# Se dispara si se cumple CUALQUIERA de estas condiciones (ventana_s, umbral_$):
TRIG = ((0.2, 3.0), (0.5, 5.0), (1.0, 8.0))
JW = 1.0            # solo para el campo "salto" del registro

# El conjunto de momentos que llegamos a VER depende del disparador, asi que una misma regla evaluada
# sobre disparos de dos disparadores distintos NO es la misma muestra. Se graba cual estaba activo en
# cada fila y el barrido no mezcla.
TRIGID = ";".join(f"{w}/{t:g}" for w, t in TRIG)

# La cabecera se GENERA de MVW. Dos veces se me desincronizaron a mano y el analisis quedo ciego.
H = (["ts_salto", "ws", "tok", "salto", "react_ms", "trig"]
     + [c for k in (0, 52, 100, 200) for c in (f"ask{k}", f"sz{k}")]
     + ["mid8s", "spread0"] + [mvcol(w) for w in MVW])
COOL = 10.0
SNAPS = (0.0, 0.052, 0.100, 0.200)     # 52 ms = nuestra latencia medida de envío
SETTLE = 8.0
LOCK = threading.Lock()


def get(url, tries=2, timeout=6):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "spaper/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r: return json.load(r)
        except Exception:
            if i == tries - 1: return None
            time.sleep(0.2)


def discover(ws):
    d = get(f"https://gamma-api.polymarket.com/markets?slug=btc-updown-5m-{ws}")
    if not (isinstance(d, list) and d): return None
    m = d[0]
    try:
        return {"cid": m.get("conditionId"),
                "toks": dict(zip(json.loads(m["outcomes"]), json.loads(m["clobTokenIds"])))}
    except Exception: return None


def write(row):
    """Si el fichero existe con OTRA cabecera, se aparta (no se borra) y se empieza uno nuevo.
    Escribir a ciegas sobre una cabecera vieja ya dejo el analisis ciego dos veces."""
    with LOCK:
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


def run_window(ws, mk):
    toks = mk["toks"]; id2 = {toks["Up"]: "Up", toks["Down"]: "Down"}
    close = ws + 300
    bk = {"Up": [None, None, 0.0], "Down": [None, None, 0.0]}   # [bid, ask, tam del ask]
    lv = {"Up": {}, "Down": {}}
    hist = []                                                    # (ts, precio BTC)
    last_fire = [0.0]
    nfire = [0]

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
                        bb = ch.get("best_bid"); ba = ch.get("best_ask")
                        if bb not in (None, ""): bk[tok][0] = float(bb)
                        if ba not in (None, ""):
                            bk[tok][1] = float(ba); bk[tok][2] = lv[tok].get(float(ba), 0.0)
                    except Exception: pass

    def snap(tok):
        b, a, s = bk[tok]
        return (a, s, (b + a) / 2 if (b and a) else None, (a - b) if (b and a) else None)

    def disparo(t0, tok, salto, react, movs):
        """anota el libro a cada latencia y el medio asentado; NO opera."""
        row = [round(t0, 3), ws, tok, round(salto, 1), round(1000 * react, 1), TRIGID]
        got = {}
        def toma(k):
            got[k] = snap(tok)
        for k in SNAPS:
            if k <= 0: toma(k)
            else: threading.Timer(k, toma, args=(k,)).start()
        def cerrar():
            for k in SNAPS:
                a, s, _, sp = got.get(k, (None, None, None, None))
                row.extend([a if a else "", round(s) if s else ""])
            row.append(round(snap(tok)[2], 4) if snap(tok)[2] else "")
            _, _, _, sp0 = got.get(0.0, (None, None, None, None))
            row.append(round(sp0, 4) if sp0 else "")
            row.extend(round(m, 1) if m is not None else "" for m in movs)
            write(row)
        threading.Timer(SETTLE, cerrar).start()

    def on_spot(w, msg):
        t = time.time()
        try:
            d = json.loads(msg); mid = (float(d["b"]) + float(d["a"])) / 2
        except Exception: return
        hist.append((t, mid))
        # margen sobre la ventana más larga: para medir 3 s atrás hace falta guardar MÁS de 3 s
        while hist and hist[0][0] < t - (max(MVW) + 1.5): hist.pop(0)
        if t - last_fire[0] < COOL: return

        def mv(w):
            """movimiento acumulado en los últimos w s, con signo. Todo conocido AHORA."""
            r = None
            for tt, pp in hist:
                if tt <= t - w: r = pp
                else: break
            return (mid - r) if r is not None else None

        ref = mv(JW)
        disp = None
        for w_, thr_ in TRIG:
            m_ = mv(w_)
            if m_ is not None and abs(m_) >= thr_: disp = m_; break
        if disp is None: return
        if ref is None: ref = disp
        tok = "Up" if disp > 0 else "Down"
        if bk[tok][1] is None: return
        s = 1 if tok == "Up" else -1
        movs = [(s * m if m is not None else None) for m in (mv(w) for w in MVW)]
        last_fire[0] = t; nfire[0] += 1
        disparo(t, tok, ref, time.time() - t, movs)        # react = lo que tardamos en decidir

    app = websocket.WebSocketApp(WSS, on_open=on_open, on_message=on_book, on_error=lambda a, b: None)
    threading.Thread(target=lambda: app.run_forever(ping_interval=20, ping_timeout=10), daemon=True).start()
    sapp = websocket.WebSocketApp(SPOT_WSS, on_message=on_spot, on_error=lambda a, b: None)
    threading.Thread(target=lambda: sapp.run_forever(ping_interval=20, ping_timeout=10), daemon=True).start()
    print(f"── {ws} (cierra en {int(close - time.time())}s)", flush=True)
    while time.time() < close - 12: time.sleep(0.5)
    app.close(); sapp.close()
    print(f"   disparos: {nfire[0]}", flush=True)


def vivo():
    print("stalepaper: reacciona de verdad y anota lo que HABRÍA comprado. NO OPERA. Ctrl-C para parar.",
          flush=True)
    try:
        while True:
            t = time.time(); ws = int(t - (t % 300))
            if t - ws > 270: time.sleep(300 - (t - ws) + 1); continue
            mk = discover(ws)
            if not mk: time.sleep(15); continue
            run_window(ws, mk)
    except KeyboardInterrupt:
        print("\nparando…", flush=True)


def fee(p): return 0.07 * p * (1 - p)




_RESCACHE = os.path.join(DIR, "lab", "clob_reso_paper.csv")


def _resol(wss):
    """ws -> ganador. Puente ws->cid por los books del laboratorio; lo que falte se pide al CLOB."""
    ws2cid = {}
    for path in sorted(glob.glob(os.path.join(DIR, "lab", "books_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) < 3 or not row[1].startswith("btc-updown-5m-"): continue
                try: w = int(row[1].split("-")[-1])
                except Exception: continue
                if w in wss and w not in ws2cid: ws2cid[w] = row[2]
    reso = {}
    for fn in ("clob_reso_paper.csv", "clob_reso_stale.csv", "clob_reso_mmtoxic.csv"):
        fp = os.path.join(DIR, "lab", fn)
        if os.path.exists(fp):
            for r in csv.DictReader(open(fp, encoding="utf-8")):
                if r.get("winner"): reso[r["cid"]] = r["winner"]
    falta = [w for w in wss if w in ws2cid and ws2cid[w] not in reso]
    print(f"  resoluciones: {len(wss)-len(falta)} listas, {len(falta)} por pedir", flush=True)
    if falta:
        nuevo = not os.path.exists(_RESCACHE)
        with open(_RESCACHE, "a", newline="", encoding="utf-8") as fo:
            cw = csv.writer(fo)
            if nuevo: cw.writerow(["cid", "winner"])
            for w in falta:
                d = get(f"https://clob.polymarket.com/markets/{ws2cid[w]}", timeout=10)
                if isinstance(d, dict):
                    for tk in d.get("tokens", []):
                        if tk.get("winner") is True:
                            reso[ws2cid[w]] = tk.get("outcome")
                            cw.writerow([ws2cid[w], tk.get("outcome")]); fo.flush()
                time.sleep(0.12)
    return {w: reso[c] for w, c in ws2cid.items() if c in reso}


def analizar():
    # cada fichero con SU cabecera: los antiguos no tienen las columnas de movimiento y no pasa nada
    R = []
    for p in sorted(glob.glob(os.path.join(DIR, "stalepaper*.csv"))):
        n0 = len(R)
        with open(p, encoding="utf-8") as fh: R.extend(csv.DictReader(fh))
        print(f"  {os.path.basename(p)}: {len(R) - n0} disparos")
    if not R: print("aún no hay disparos"); return
    print(f"disparos anotados: {len(R)}")
    if len(R) < 20: print("muestra corta — dejarlo correr"); return
    rs = [float(r["react_ms"]) for r in R if r.get("react_ms")]
    rs.sort()
    print(f"tiempo de DECISIÓN (salto → decidido): mediana {rs[len(rs)//2]:.2f} ms · "
          f"p90 {rs[int(.9*len(rs))]:.2f}")
    # La tabla se parte POR DISPARADOR. Antes era un promedio de todas las épocas, y yo mismo lo usé para
    # sacar conclusiones sobre la configuración actual cuando el 96% de las filas venían de las anteriores.
    # Mezclar disparadores invalida la comparación: lo había dicho dos días antes y aun así lo hice.
    import statistics as st
    grupos = {}
    for r in R: grupos.setdefault(r.get("trig") or "antiguos (1s, mezcla)", []).append(r)
    for g in sorted(grupos, key=lambda k: -len(grupos[k])):
        print(f"\n  ── disparador {g} ──  ({len(grupos[g])} disparos)")
        print(f"  {'compramos a':>16}{'n':>7}{'ask':>8}{'tam.':>7}{'medio 8s':>10}{'MECANISMO':>18}")
        for k, nm in ((0, "al instante"), (52, "a los 52 ms"), (100, "a los 100 ms"),
                      (200, "a los 200 ms")):
            v = []
            for r in grupos[g]:
                try:
                    a = float(r[f"ask{k}"]); m = float(r["mid8s"]); s = float(r[f"sz{k}"] or 0)
                except Exception: continue
                if not (0 < a < 1 and 0 < m < 1): continue
                v.append((a, s, m - a - fee(a)))
            if len(v) < 15: print(f"  {nm:>16}{len(v):>7}   (pocos)"); continue
            pl = [x[2] for x in v]
            sd = st.pstdev(pl) / (len(pl) ** 0.5) if len(pl) > 1 else float("nan")
            print(f"  {nm:>16}{len(v):>7}{st.mean([x[0] for x in v]):>8.3f}"
                  f"{st.median([x[1] for x in v]):>7.0f}"
                  f"{st.mean([x[2] + x[0] + fee(x[0]) for x in v]):>10.3f}"
                  f"{f'{100*st.mean(pl):+.2f} ± {100*sd:.2f}':>18}")
    # El desglose por tamano no servia: disparabamos al cruzar el umbral, asi que el salto era ~umbral
    # siempre (363 de 376 en el cajon 10-15). Ahora se anota el movimiento en varias ventanas y se puede
    # BARRER cualquier regla sin volver a esperar. Todo lo que se filtra aqui es conocido AL DECIDIR.
    print()
    print("=" * 86)
    print(f"  BARRIDO DE REGLAS (compra a los 52 ms) - SOLO disparador {TRIGID}")
    print("=" * 86)
    print(f"  {'regla':>22}{'n':>7}{'ask':>8}{'MECANISMO':>18}{'z':>7}")
    import statistics as st
    for w in MVW:
        for thr in (3, 5, 8, 10, 15, 20):
            v = []
            for r in R:
                if r.get("trig") != TRIGID: continue
                try:
                    m = mvget(r, w); a = float(r["ask52"]); s8 = float(r["mid8s"])
                except Exception: continue
                if m is None or not (m >= thr and 0 < a < 1 and 0 < s8 < 1): continue
                v.append((a, s8 - a - fee(a)))
            if len(v) < 25: continue
            pl = [x[1] for x in v]
            sd = st.pstdev(pl) / (len(pl) ** 0.5) if len(pl) > 1 else float("nan")
            z = st.mean(pl) / sd if sd else float("nan")
            print(f"  {str(thr) + '$ en ' + str(w) + 's':>22}{len(v):>7}"
                  f"{st.mean([x[0] for x in v]):>8.3f}"
                  f"{f'{100*st.mean(pl):+.2f} +- {100*sd:.2f}':>18}{z:>+7.2f}")
    print("  -> si el edge crece con el umbral y con la ventana, el problema era disparar sobre ruido.")
    print("     Las reglas con menos de 25 casos no se imprimen: el filtro se elige con muestra.")

    # REPLICACION dia a dia (solo disparador actual). Es el gate que mato al candidato anterior: el de
    # $10 dio +1,02 su primer dia y no se repitio. Un edge real se repite.
    # Y el resultado a RESOLUCION, que es el dinero: el markout contra el medio es su estimador de baja
    # varianza (el mercado esta calibrado), pero conviene ver que el dinero real va en el mismo sentido.
    import time as _t
    cur = [r for r in R if r.get("trig") == TRIGID]
    print()
    print("=" * 84)
    print(f"  DIA A DIA (52 ms) - solo disparador {TRIGID}")
    print("=" * 84)
    RES = _resol({int(r["ws"]) for r in cur if r.get("ws")})
    print(f"  {'dia':>12}{'n':>7}{'ask':>8}{'MECANISMO':>18}{'n res':>7}{'A RESOLUCION':>20}")
    byd = {}
    for r in cur:
        try:
            d = _t.strftime("%Y-%m-%d", _t.gmtime(float(r["ts_salto"])))
            a = float(r["ask52"]); m = float(r["mid8s"])
        except Exception: continue
        if not (0 < a < 1 and 0 < m < 1): continue
        w = RES.get(int(r["ws"])) if r.get("ws") else None
        won = None if w is None else (1.0 if w == r.get("tok") else 0.0)
        byd.setdefault(d, []).append((a, m - a - fee(a), won))
    tot = []
    for d in sorted(byd) + ["TOTAL"]:
        v = byd[d] if d != "TOTAL" else tot
        if d != "TOTAL": tot.extend(v)
        if len(v) < 20: print(f"  {d:>12}{len(v):>7}   (pocos)"); continue
        pl = [x[1] for x in v]
        sd = st.pstdev(pl) / (len(pl) ** 0.5) if len(pl) > 1 else float("nan")
        rr = [x for x in v if x[2] is not None]
        if len(rr) >= 20:
            rp = [x[2] - x[0] - fee(x[0]) for x in rr]
            rsd = st.pstdev(rp) / (len(rp) ** 0.5)
            res = f"{100*st.mean(rp):+.2f} +- {100*rsd:.2f}"
        else: res = "-"
        print(f"  {d:>12}{len(v):>7}{st.mean([x[0] for x in v]):>8.3f}"
              f"{f'{100*st.mean(pl):+.2f} +- {100*sd:.2f}':>18}{len(rr):>7}{res:>20}")
    print("  -> un edge real se REPITE. Si un dia da +1,2 y el siguiente -0,3, era una tirada afortunada.")

    print("\nLECTURA: el tiempo de DECISIÓN debería salir en microsegundos (es solo CPU); lo que cuenta es")
    print("la fila de 52 ms, que es nuestra latencia medida de envío de orden. Si el MECANISMO ahí se")
    print("parece al +2,12 ± 0,39 que dio stalebt offline, la simulación era fiel y lo único que falta es")
    print("comprobar con una orden real que nos llenan. Si sale bastante peor, la diferencia es fricción")
    print("que la simulación no veía, y hay que entenderla antes de arriesgar un euro.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    analizar() if "--analyze" in sys.argv else vivo()
