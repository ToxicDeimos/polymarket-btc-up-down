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
H = ["ts", "ws", "tok", "typ", "side", "price", "size", "bb", "ba"]
LAT = (0, 100, 250, 500, 1000, 2000, 5000)      # latencias a evaluar, ms
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

    def push(row):
        with LOCK: BUF.append(row)

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
                for p, s in bids[:5]: push([t, ws, tok, "B", "bid", p, s, bb, ba])
                for p, s in asks[:5]: push([t, ws, tok, "B", "ask", p, s, bb, ba])
            elif et == "price_change":
                for ch in d.get("price_changes", []):
                    a = ch.get("asset_id")
                    if a not in id2: continue
                    try:
                        push([t, ws, id2[a], "C", ch.get("side", ""), float(ch["price"]),
                              float(ch.get("size", 0)), ch.get("best_bid", ""), ch.get("best_ask", "")])
                    except Exception: pass
            elif et == "last_trade_price" and aid in id2:
                try:
                    push([t, ws, id2[aid], "T", d.get("side", ""), float(d["price"]),
                          float(d.get("size") or 0), "", ""])
                except Exception: pass

    app = websocket.WebSocketApp(WSS, on_open=on_open, on_message=on_message, on_error=lambda a, b: None)
    th = threading.Thread(target=lambda: app.run_forever(ping_interval=20, ping_timeout=10), daemon=True)
    th.start()
    print(f"── {ws} grabando (cierra en {int(close - time.time())}s)", flush=True)
    while time.time() < close + 3: time.sleep(0.5)
    app.close()


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

    # colas nuevas: el toque salta a un precio que no era el toque
    cols = []      # (t0, tamaño inicial, trayectoria [(t,size)], volumen negociado en el nivel, vida)
    for (ws, tok, sd), rows in ev.items():
        touch_prev = None
        lvl = {}                                    # precio -> último tamaño conocido
        births = {}                                 # precio -> t de nacimiento como toque
        for t, px, sz, bb, ba in rows:
            lvl[px] = sz
            touch = bb if sd == "bid" else ba
            if touch is None: continue
            if touch_prev is None or abs(touch - touch_prev) > 1e-9:
                if touch not in births: births[touch] = t
                touch_prev = touch
        for px, t0 in births.items():
            traj = [(t, s) for t, p, s, _, _ in rows if abs(p - px) < 1e-9 and t >= t0]
            if not traj: continue
            end = max(t for t, _ in traj)
            vol = sum(s for t, p, s in trades.get((ws, tok), []) if abs(p - px) < 1e-9 and t0 <= t <= end + 1)
            cols.append((t0, traj, vol, end - t0))
    if not cols:
        print("no se han reconstruido colas — revisar el formato de los eventos"); return

    def size_at(traj, t):
        s = 0.0
        for tt, ss in traj:
            if tt <= t: s = ss
            else: break
        return s

    lives = sorted(c[3] for c in cols)
    vols = sorted(c[2] for c in cols)
    print(f"\ncolas nuevas reconstruidas: {len(cols)}")
    print(f"  vida del nivel en el toque: mediana {lives[len(lives)//2]:.1f}s · p90 {lives[int(.9*len(lives))]:.1f}s")
    print(f"  volumen negociado en el nivel: mediana {vols[len(vols)//2]:.0f} · p90 {vols[int(.9*len(vols))]:.0f} shares")

    print("\n" + "=" * 92)
    print("  SI PUBLICAMOS X ms DESPUÉS DE QUE NAZCA EL NIVEL (prioridad precio-tiempo)")
    print("=" * 92)
    print(f"  {'latencia':>10}{'delante (mediana)':>20}{'delante (p90)':>16}{'nos llenan':>13}"
          f"{'llenan ≥5 sh':>14}")
    for X in LAT:
        ahead = []; fill = []; fill5 = []
        for t0, traj, vol, _ in cols:
            a = size_at(traj, t0 + X / 1000.0)
            ahead.append(a)
            fill.append(1 if vol > a else 0)
            fill5.append(1 if vol - a >= 5 else 0)
        ahead.sort()
        print(f"  {X:>8} ms{ahead[len(ahead)//2]:>20.0f}{ahead[int(.9*len(ahead))]:>16.0f}"
              f"{100*sum(fill)/len(fill):>12.0f}%{100*sum(fill5)/len(fill5):>13.0f}%")

    print("\nLECTURA: si a 250 ms ya hay cientos de acciones delante y el 'nos llenan' apenas sube respecto a")
    print("2000 ms, la cola está perdida de antemano y correr no sirve: el maker queda cerrado del todo. Si el")
    print("salto entre 2000 ms y 250 ms es grande, la prioridad ES alcanzable y el siguiente paso es medir")
    print("nuestra latencia real de publicación (una orden mínima, lejos del mercado, que no puede ejecutarse).")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    analyze() if "--analyze" in sys.argv else record()
