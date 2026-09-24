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
LOG = os.path.join(DIR, "stalepaper.csv")
WSS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
SPOT_WSS = "wss://stream.binance.com:9443/ws/btcusdt@bookTicker"
H = ["ts_salto", "ws", "tok", "salto", "react_ms",
     "ask0", "sz0", "ask52", "sz52", "ask100", "sz100", "ask200", "sz200", "mid8s", "spread0",
     "mv05", "mv1", "mv2", "mv3"]
# 🔑 Disparábamos en el instante EXACTO en que se cruzaban los $10, así que el salto registrado era ~10
# siempre (363 de 376 en el cajón 10-15) y el desglose por tamaño no podía decir nada. Peor: explica la
# diferencia con el offline, que usaba el spot grabado cada 100 ms y por tanto seleccionaba sin querer
# saltos MÁS GRANDES (más retraso de libro que capturar) — de ahí su +2,12 frente a nuestro +1,02.
# Ahora se baja el umbral y se anota el movimiento en VARIAS ventanas, todo conocido en el instante de
# decidir, para poder evaluar cualquier regla después sin volver a esperar.
JUMP = 5.0           # $ en JW; umbral bajo a propósito: el filtro se elige luego, no ahora
JW = 1.0
MVW = (0.5, 1.0, 2.0, 3.0)      # ventanas del movimiento que se anotan
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
    with LOCK:
        new = not os.path.exists(LOG)
        with open(LOG, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new: w.writerow(H)
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
        row = [round(t0, 3), ws, tok, round(salto, 1), round(1000 * react, 1)]
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
        if ref is None or abs(ref) < JUMP: return
        tok = "Up" if ref > 0 else "Down"
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


def analizar():
    if not os.path.exists(LOG): print("aún no hay disparos"); return
    R = list(csv.DictReader(open(LOG, encoding="utf-8")))
    print(f"disparos anotados: {len(R)}")
    if len(R) < 20: print("muestra corta — dejarlo correr"); return
    rs = [float(r["react_ms"]) for r in R if r.get("react_ms")]
    rs.sort()
    print(f"tiempo de DECISIÓN (salto → decidido): mediana {rs[len(rs)//2]:.2f} ms · "
          f"p90 {rs[int(.9*len(rs))]:.2f}")
    print(f"\n  {'compramos a':>16}{'n':>7}{'ask':>8}{'tam.':>7}{'medio 8s':>10}"
          f"{'MECANISMO':>18}")
    for k, nm in ((0.0, "al instante"), (0.052, "a los 52 ms"), (0.100, "a los 100 ms"),
                  (0.200, "a los 200 ms")):
        key = f"ask{int(k*1000)}" if k else "ask0"
        skey = f"sz{int(k*1000)}" if k else "sz0"
        v = []
        for r in R:
            try:
                a = float(r[key]); m = float(r["mid8s"]); s = float(r[skey] or 0)
            except Exception: continue
            if not (0 < a < 1 and 0 < m < 1): continue
            v.append((a, s, m - a - fee(a)))
        if len(v) < 15: print(f"  {nm:>16}{len(v):>7}   (pocos)"); continue
        import statistics as st
        pl = [x[2] for x in v]
        sd = st.pstdev(pl) / (len(pl) ** 0.5) if len(pl) > 1 else float("nan")
        print(f"  {nm:>16}{len(v):>7}{st.mean([x[0] for x in v]):>8.3f}"
              f"{st.median([x[1] for x in v]):>7.0f}{st.mean([x[2] + x[0] + fee(x[0]) for x in v]):>10.3f}"
              f"{f'{100*st.mean(pl):+.2f} ± {100*sd:.2f}':>18}")
    # El desglose por tamano no servia: disparabamos al cruzar el umbral, asi que el salto era ~umbral
    # siempre (363 de 376 en el cajon 10-15). Ahora se anota el movimiento en varias ventanas y se puede
    # BARRER cualquier regla sin volver a esperar. Todo lo que se filtra aqui es conocido AL DECIDIR.
    print()
    print("=" * 86)
    print("  BARRIDO DE REGLAS (compra a los 52 ms, nuestra latencia)")
    print("=" * 86)
    print(f"  {'regla':>22}{'n':>7}{'ask':>8}{'MECANISMO':>18}{'z':>7}")
    import statistics as st
    for w, col in ((0.5, "mv05"), (1.0, "mv1"), (2.0, "mv2"), (3.0, "mv3")):
        for thr in (5, 8, 10, 15, 20):
            v = []
            for r in R:
                try:
                    m = float(r.get(col) or "nan"); a = float(r["ask52"]); s8 = float(r["mid8s"])
                except Exception: continue
                if not (m == m and m >= thr and 0 < a < 1 and 0 < s8 < 1): continue
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

    print("\nLECTURA: el tiempo de DECISIÓN debería salir en microsegundos (es solo CPU); lo que cuenta es")
    print("la fila de 52 ms, que es nuestra latencia medida de envío de orden. Si el MECANISMO ahí se")
    print("parece al +2,12 ± 0,39 que dio stalebt offline, la simulación era fiel y lo único que falta es")
    print("comprobar con una orden real que nos llenan. Si sale bastante peor, la diferencia es fricción")
    print("que la simulación no veía, y hay que entenderla antes de arriesgar un euro.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    analizar() if "--analyze" in sys.argv else vivo()
