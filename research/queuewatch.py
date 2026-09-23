"""
queuewatch.py — ¿SE PUEDE GANAR LA COLA? markout (ya con el desfase de 3 s corregido) dejó el cierre del maker
en una frase concreta: el subconjunto TOQUE da +0,46pp a edad real 0-1 s y es positivo a cualquier edad,
mientras el BARRIDO da −0,72. Es decir, **el dinero del maker vive en la PRIORIDAD DE COLA**: no perdemos por
no saber a dónde va BTC, perdemos porque llegamos los últimos y solo nos llenan cuando alguien se lleva el
nivel entero. Eso no lo arregla un modelo mejor, lo arregla estar delante.

El orden correcto es medir primero CUÁNTO HAY QUE CORRER, no cuánto corremos nosotros: si un nivel nuevo
acumula 500 acciones en 150 ms, nuestra latencia da igual y la vía se cierra sin gastar un céntimo. Y eso se
ve entero por WSS, que es gratis y no opera.

Prioridad precio-tiempo: quien pone primero en un nivel cobra primero. Así que, cada vez que el toque SALTA A
UN PRECIO NUEVO, empieza una cola limpia. Grabamos con marca de milisegundos cómo crece el tamaño en ese nivel
y cuánto volumen acaba negociándose en él. Con eso, para cada latencia X:
      tamaño delante de nosotros = tamaño del nivel en (creación + X)
      nos llenan si el volumen negociado en el nivel supera ese tamaño
y sale directamente "si publicamos en X ms, nos llenan el Y% de las veces". NO se pone ninguna orden.

    cd ~/polymarket-btc-up-down/research && python3 queuewatch.py            # grabar (horas/días)
    python3 queuewatch.py --analyze                                          # veredicto
"""
import websocket, json, time, threading, csv, os, sys, glob, urllib.request
from collections import deque

DIR = os.path.dirname(__file__)
WSS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
# Sin el spot a la MISMA resolución no se puede ver si el libro se queda atrás: el laboratorio graba libro y
# spot cada ~6-7 s y el fenómeno vive en 1-3 s, así que stalepick no podía medirlo (instrumento más lento que
# el fenómeno). bookTicker da el mejor bid/ask de Binance en cada cambio.
SPOT_WSS = "wss://stream.binance.com:9443/ws/btcusdt@bookTicker"
SPOT_MS = 0.1        # como mucho una fila cada 100 ms…
SPOT_JUMP = 1.0      # …salvo que BTC se mueva $1, que entonces se graba igual
H = ["ts", "ws", "tok", "typ", "side", "price", "size", "bb", "ba"]
LAT = (0, 10, 25, 50, 100, 250, 500, 1000, 2000, 5000)      # latencias a evaluar, ms
BAND = 0.05      # solo grabamos cerca del toque: en la primera prueba el 95% de los eventos eran alguien
                 # moviendo 11.000 acciones en 0,01/0,99 con el toque en 0,77 — 3 GB/día de ruido puro.
LOCK = threading.Lock()
BUF = deque()


def logpath(t=None):
    return os.path.join(DIR, "queue_events_" + time.strftime("%Y%m%d", time.gmtime(t or time.time())) + ".csv")


def get(url, tries=2, timeout=6):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "qwatch/1.0"})
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


def flusher(stop):
    """escribe en tandas para no meter latencia de disco en las marcas de tiempo."""
    while not stop.is_set() or BUF:
        time.sleep(1.5)
        rows = []
        with LOCK:
            while BUF: rows.append(BUF.popleft())
        if not rows: continue
        p = logpath()
        new = not os.path.exists(p)
        try:
            with open(p, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if new: w.writerow(H)
                w.writerows(rows)
        except Exception:
            pass


def run_window(ws, mk):
    toks = mk["toks"]; id2 = {toks["Up"]: "Up", toks["Down"]: "Down"}
    close = ws + 300
    touch = {"Up": [None, None], "Down": [None, None]}      # último [bb, ba] conocido por token
    prev = {"Up": [None, None], "Down": [None, None]}       # último [bb, ba] YA grabado
    seen = [0, 0]                                           # [grabadas, descartadas]

    def push(row):
        with LOCK: BUF.append(row)

    def near(tok, px, bb, ba):
        """¿el precio está en la banda del toque? Mantiene el toque conocido por token."""
        t = touch[tok]
        if bb not in (None, ""): t[0] = float(bb)
        if ba not in (None, ""): t[1] = float(ba)
        lo = (t[0] - BAND) if t[0] is not None else None
        hi = (t[1] + BAND) if t[1] is not None else None
        if lo is None and hi is None: return True           # aún sin toque: no descartamos nada
        return ((lo is None or px >= lo) and (hi is None or px <= hi))

    def on_open(w):
        w.send(json.dumps({"type": "market", "assets_ids": [toks["Up"], toks["Down"]]}))

    def on_message(w, msg):
        t = round(time.time(), 3)              # marca ANTES de parsear
        try: data = json.loads(msg)
        except Exception: return
        for d in (data if isinstance(data, list) else [data]):
            et = d.get("event_type"); aid = d.get("asset_id")
            if et == "book" and aid in id2:
                tok = id2[aid]
                bids = sorted(((float(x["price"]), float(x["size"])) for x in d.get("bids", [])), reverse=True)
                asks = sorted((float(x["price"]), float(x["size"])) for x in d.get("asks", []))
                bb = bids[0][0] if bids else ""; ba = asks[0][0] if asks else ""
                near(tok, bb or 0.5, bb, ba)                 # refresca el toque conocido
                for p, s in bids[:5]: push([t, ws, tok, "B", "bid", p, s, bb, ba])
                for p, s in asks[:5]: push([t, ws, tok, "B", "ask", p, s, bb, ba])
            elif et == "price_change":
                for ch in d.get("price_changes", []):
                    a = ch.get("asset_id")
                    if a not in id2: continue
                    try:
                        tok = id2[a]; px = float(ch["price"])
                        bb = ch.get("best_bid", ""); ba = ch.get("best_ask", "")
                        if not near(tok, px, bb, ba):
                            seen[1] += 1; continue           # lejos del toque: no interesa
                        # De todo esto solo usamos el mejor bid/ask y el TAMAÑO EN EL TOQUE. Un cambio de
                        # tamaño en un nivel que no es el toque y que no mueve el toque no aporta nada y
                        # son >2 GB/día. Se graba solo si el toque cambió o si el nivel ES el toque.
                        tt = touch[tok]
                        movido = (prev[tok][0] != tt[0] or prev[tok][1] != tt[1])
                        en_toque = (tt[0] is not None and abs(px - tt[0]) < 1e-9) or \
                                   (tt[1] is not None and abs(px - tt[1]) < 1e-9)
                        if not (movido or en_toque):
                            seen[1] += 1; continue
                        prev[tok][0], prev[tok][1] = tt[0], tt[1]
                        seen[0] += 1
                        push([t, ws, tok, "C", ch.get("side", ""), px,
                              float(ch.get("size", 0)), bb, ba])
                    except Exception: pass
            elif et == "last_trade_price" and aid in id2:
                try:
                    push([t, ws, id2[aid], "T", d.get("side", ""), float(d["price"]),
                          float(d.get("size") or 0), "", ""])
                except Exception: pass

    app = websocket.WebSocketApp(WSS, on_open=on_open, on_message=on_message, on_error=lambda a, b: None)
    th = threading.Thread(target=lambda: app.run_forever(ping_interval=20, ping_timeout=10), daemon=True)
    th.start()

    # ── spot de Binance al milisegundo, en el MISMO reloj y el mismo fichero (typ="S") ──
    last = [0.0, None]

    def on_spot(w, msg):
        t = round(time.time(), 3)
        try:
            d = json.loads(msg)
            mid = (float(d["b"]) + float(d["a"])) / 2
        except Exception: return
        if last[1] is not None and (t - last[0]) < SPOT_MS and abs(mid - last[1]) < SPOT_JUMP:
            return
        last[0] = t; last[1] = mid
        push([t, ws, "BTC", "S", "", round(mid, 2), "", "", ""])

    sapp = websocket.WebSocketApp(SPOT_WSS, on_message=on_spot, on_error=lambda a, b: None)
    sth = threading.Thread(target=lambda: sapp.run_forever(ping_interval=20, ping_timeout=10), daemon=True)
    sth.start()
    print(f"── {ws} grabando (cierra en {int(close - time.time())}s)", flush=True)
    while time.time() < close + 3: time.sleep(0.5)
    app.close(); sapp.close()
    tot = seen[0] + seen[1]
    print(f"   cambios: {seen[0]} grabados · {seen[1]} descartados por lejanos "
          f"({100*seen[1]/tot if tot else 0:.0f}%)", flush=True)


def record():
    stop = threading.Event()
    threading.Thread(target=flusher, args=(stop,), daemon=True).start()
    print("queuewatch: grabando el libro por WSS al milisegundo. NO opera. Ctrl-C para parar.", flush=True)
    try:
        while True:
            t = time.time(); ws = int(t - (t % 300))
            if t - ws > 280:                      # demasiado tarde, esperar a la siguiente
                time.sleep(300 - (t - ws) + 1); continue
            mk = discover(ws)
            if not mk:
                print(f"   {ws}: sin mercado, esperando", flush=True); time.sleep(20); continue
            run_window(ws, mk)
    except KeyboardInterrupt:
        print("\nparando…", flush=True)
    finally:
        stop.set(); time.sleep(2)


# ──────────────────────────── análisis ────────────────────────────
def analyze():
    paths = sorted(glob.glob(os.path.join(DIR, "queue_events_*.csv")))
    if not paths:
        print("no hay ficheros queue_events_*.csv todavía — dejar grabando unas horas"); return
    ev = {}          # (ws, tok, side) -> lista ordenada de eventos
    trades = {}      # (ws, tok) -> [(ts, price, size)]
    nrow = 0
    for p in paths:
        with open(p, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                try:
                    t = float(r["ts"]); ws = int(r["ws"]); tok = r["tok"]; typ = r["typ"]
                    px = float(r["price"]); sz = float(r["size"] or 0)
                except Exception: continue
                nrow += 1
                if typ == "T":
                    trades.setdefault((ws, tok), []).append((t, px, sz))
                else:
                    sd = "bid" if (r["side"] or "").lower().startswith(("b", "buy")) else "ask"
                    bb = r["bb"]; ba = r["ba"]
                    ev.setdefault((ws, tok, sd), []).append(
                        (t, px, sz, float(bb) if bb else None, float(ba) if ba else None))
    print(f"filas: {nrow} · series: {len(ev)} · ventanas: {len(set(k[0] for k in ev))}")
    if nrow < 5000:
        print("muy poca muestra — dejar grabando más tiempo"); return
    for k in ev: ev[k].sort()
    for k in trades: trades[k].sort()

    # El toque cambia de precio. CLAVE: casi nunca nace una cola limpia — el toque DESCIENDE a un nivel que
    # ya existía, con su cola formada mientras estaba en segunda fila. Solo si el precio estaba VACÍO empieza
    # una cola virgen, y solo ahí puede decidir la velocidad. Separamos los dos casos.
    cols = []      # (clase, t0, trayectoria, volumen, vida, tamaño previo)
    for (ws, tok, sd), rows in ev.items():
        touch_prev = None
        lvl = {}                                    # precio -> último tamaño conocido
        births = []                                 # (precio, t0, clase, tamaño previo)
        for t, px, sz, bb, ba in rows:
            touch = bb if sd == "bid" else ba
            if touch is not None and (touch_prev is None or abs(touch - touch_prev) > 1e-9):
                prev = lvl.get(touch)
                if prev is None:   kl, sb = "desconocida", None
                elif prev <= 0:    kl, sb = "virgen", 0.0
                else:              kl, sb = "heredada", prev
                births.append((touch, t, kl, sb))
                touch_prev = touch
            lvl[px] = sz                            # actualizar DESPUÉS de clasificar
        tlast = rows[-1][0]
        for i, (px, t0, kl, sb) in enumerate(births):
            end = births[i + 1][1] if i + 1 < len(births) else tlast
            if end <= t0: continue
            traj = [(t, s) for t, p, s, _, _ in rows if abs(p - px) < 1e-9 and t0 <= t <= end]
            if not traj: traj = [(t0, sb or 0.0)]
            vol = sum(s for t, p, s in trades.get((ws, tok), [])
                      if abs(p - px) < 1e-9 and t0 <= t <= end + 1)
            cols.append((kl, t0, traj, vol, end - t0, sb))
    if not cols:
        print("no se han reconstruido colas — revisar el formato de los eventos"); return

    def size_at(traj, t):
        s = 0.0
        for tt, ss in traj:
            if tt <= t: s = ss
            else: break
        return s

    lives = sorted(c[4] for c in cols)
    vols = sorted(c[3] for c in cols)
    cnt = {}
    for c in cols: cnt[c[0]] = cnt.get(c[0], 0) + 1
    print(f"\ncambios de toque reconstruidos: {len(cols)}")
    print(f"  vida del nivel en el toque: mediana {lives[len(lives)//2]:.1f}s · p90 {lives[int(.9*len(lives))]:.1f}s")
    print(f"  volumen negociado en el nivel: mediana {vols[len(vols)//2]:.0f} · p90 {vols[int(.9*len(vols))]:.0f} shares")
    print("  clase del nivel al pasar a ser el toque: " +
          " · ".join(f"{k} {v} ({100*v/len(cols):.0f}%)" for k, v in sorted(cnt.items(), key=lambda kv: -kv[1])))
    her = [c[5] for c in cols if c[0] == "heredada" and c[5]]
    if her:
        her.sort()
        print(f"  cola YA formada al heredar el nivel: mediana {her[len(her)//2]:.0f} · "
              f"p90 {her[int(.9*len(her))]:.0f} shares")

    for kl in ("virgen", "heredada", "desconocida"):
        sub = [c for c in cols if c[0] == kl]
        if len(sub) < 20: continue
        print("\n" + "=" * 92)
        print(f"  NIVEL {kl.upper()} (n {len(sub)}) — si publicamos X ms después de que pase a ser el toque")
        print("=" * 92)
        print(f"  {'latencia':>10}{'delante (mediana)':>20}{'delante (p90)':>16}{'nos llenan':>13}"
              f"{'llenan ≥5 sh':>14}")
        for X in LAT:
            ahead = []; fill = []; fill5 = []
            for _, t0, traj, vol, _, _ in sub:
                a = size_at(traj, t0 + X / 1000.0)
                ahead.append(a)
                fill.append(1 if vol > a else 0)
                fill5.append(1 if vol - a >= 5 else 0)
            ahead.sort()
            print(f"  {X:>8} ms{ahead[len(ahead)//2]:>20.0f}{ahead[int(.9*len(ahead))]:>16.0f}"
                  f"{100*sum(fill)/len(fill):>12.0f}%{100*sum(fill5)/len(fill5):>13.0f}%")

    print("\nLECTURA: la fila que decide es la de nivel VIRGEN (el toque salta a un precio donde no había nada):")
    print("es el único momento en que la cola empieza de cero y la velocidad puede ganarla. Si ahí el salto")
    print("entre 2000 ms y 50 ms es grande, la prioridad ES alcanzable y toca medir nuestra latencia real de")
    print("publicación (una orden mínima, lejos del mercado, que no puede ejecutarse). En los niveles")
    print("HEREDADOS correr no sirve por construcción: la cola se formó mientras el nivel estaba en segunda")
    print("fila. Si los vírgenes son una minoría y además no dan salto, el maker queda cerrado del todo.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    analyze() if "--analyze" in sys.argv else record()
