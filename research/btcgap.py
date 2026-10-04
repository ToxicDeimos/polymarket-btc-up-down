"""
btcgap.py — ¿COMPRA IZZYAUSSIE CUANDO EL PRECIO VA POR DETRAS DE BTC?

regla.py dejo el diagnostico asi: su edge esta en QUE compra y CUANDO (+4,93 pp/accion frente al
control emparejado), gana comprando MOMENTUM (token que acaba de subir: +12,96) y las tres features
obvias NO transfieren — el resto del mercado haciendo lo mismo pierde (−1,07). Falta meter lo unico
que de verdad mueve este mercado y que no estaba en el analisis: **BTC**.

Hipotesis: compra el token que acaba de subir cuando NO HA SUBIDO LO SUFICIENTE para lo que BTC ha
hecho. Eso seria publico y calculable en la Pi, porque la regla de resolucion esta clavada al 97%
(D3, ver polymarket-updown-twap-resolution): gana Up si la media de los ultimos 60 s supera la media
de los 60 s ANTERIORES a la apertura.

Se mide, para cada compra (suya y de todos los demas, mismas ventanas):

  margen   = spot en ese instante − media de los 60 s previos a la apertura   [$]
             con el signo del token comprado (+ si compro el lado que va ganando)
  m5, m30  = MARKOUT: cuanto vale el token 5 s y 30 s despues, menos lo que pago
             → separa VELOCIDAD (compra antes del movimiento: markout alto) de
               PREDICCION (markout plano pero acierta la resolucion)

Y el control de siempre: el mismo cubo para las 751.465 compras del resto. Si el cubo que el usa
gana tambien para los demas, es regla publica; si no, su informacion no esta en esta feature.

    cd ~/polymarket-btc-up-down/research && python3 btcgap.py

Cachea velas de 1 s de Binance en btc_1s.csv y los tokens del CLOB en clob_tokens2.csv.
"""
import bisect, csv, json, os, sys, time, urllib.request, urllib.error
from collections import defaultdict

csv.field_size_limit(10 ** 7)
DIR = os.path.dirname(os.path.abspath(__file__))
CINTA = os.path.join(DIR, "izzy_cinta2.csv")
TOK = os.path.join(DIR, "clob_tokens2.csv")
VELAS = os.path.join(DIR, "btc_1s.csv")
IZZY = "0x94f471f68396ff4a3cab8cb5c47c86274b8b77a2"
DESDE = "2026-09-01"
L = 60          # ventana del TWAP en 5m desde el 14-ago
PAUSA = 0.1


def get(url, reintentos=3):
    for i in range(reintentos):
        try:
            r = urllib.request.Request(url, headers={"User-Agent": "research/1.0"})
            with urllib.request.urlopen(r, timeout=40) as f:
                return json.loads(f.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (400, 404): return None
            if i == reintentos - 1: raise
        except Exception:
            if i == reintentos - 1: raise
        time.sleep(1.5 * (i + 1))
    return None


def sus_operaciones(desde_ts):
    out, off = [], 0
    while True:
        d = get(f"https://data-api.polymarket.com/trades?user={IZZY}&limit=500&offset={off}")
        if not d: break
        out += d
        if min(t["timestamp"] for t in d) < desde_ts or len(d) < 500: break
        off += 500
        time.sleep(PAUSA)
    return [t for t in out if t["timestamp"] >= desde_ts
            and (t.get("slug") or "").startswith("btc-updown-5m")]


def tokens(cids):
    """(cid, asset) -> (es_up, gano)."""
    tk = {}
    if os.path.exists(TOK):
        for r in csv.reader(open(TOK, encoding="utf-8", errors="replace")):
            if len(r) == 4: tk[(r[0], r[1])] = (int(r[2]), int(r[3]))
    hechos = {k[0] for k in tk}
    faltan = [c for c in cids if c not in hechos]
    if faltan:
        print(f"  CLOB: {len(faltan)} mercados…")
        f = open(TOK, "a", newline="", encoding="utf-8"); w = csv.writer(f)
        for i, cid in enumerate(faltan, 1):
            d = get(f"https://clob.polymarket.com/markets/{cid}")
            for t in (d or {}).get("tokens", []):
                up = 1 if (t.get("outcome") or "").lower() == "up" else 0
                gg = 1 if t.get("winner") else 0
                tk[(cid, t["token_id"])] = (up, gg)
                w.writerow((cid, t["token_id"], up, gg))
            f.flush()
            if i % 50 == 0 or i == len(faltan):
                sys.stdout.write(f"\r    {i}/{len(faltan)}"); sys.stdout.flush()
            time.sleep(PAUSA)
        f.close(); print()
    return tk


def velas(wss):
    """ws -> {segundo_absoluto: cierre} para [ws−70, ws+310]."""
    cache = defaultdict(dict)
    if os.path.exists(VELAS):
        for r in csv.reader(open(VELAS, encoding="utf-8", errors="replace")):
            if len(r) == 3:
                try: cache[int(r[0])][int(r[1])] = float(r[2])
                except ValueError: pass
    faltan = [w for w in wss if w not in cache]
    if faltan:
        print(f"  Binance: velas de 1 s para {len(faltan)} ventanas…")
        f = open(VELAS, "a", newline="", encoding="utf-8"); w_ = csv.writer(f)
        for i, ws in enumerate(faltan, 1):
            d = get("https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1s"
                    f"&startTime={(ws-70)*1000}&endTime={(ws+310)*1000}&limit=400")
            for k in (d or []):
                t, c = k[0] // 1000, float(k[4])
                cache[ws][t] = c
                w_.writerow((ws, t, c))
            if not d:
                cache[ws] = {}
            f.flush()
            if i % 25 == 0 or i == len(faltan):
                sys.stdout.write(f"\r    {i}/{len(faltan)}"); sys.stdout.flush()
            time.sleep(PAUSA)
        f.close(); print()
    return cache


def cubo(v, cortes):
    for i, c in enumerate(cortes):
        if v < c: return i
    return len(cortes)


def tabla(titulo, filas, etiquetas, corte):
    print(f"\n  {titulo}")
    print(f"    {'cubo':<16}{'n':>8}{'acciones':>11}{'pp/accion':>12}{'train':>9}{'test':>9}"
          f"{'m5':>8}{'m30':>8}")
    agg = defaultdict(lambda: [0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    for ts, c, r, s, m5, m30 in filas:
        a = agg[c]
        a[0] += 1; a[1] += s; a[2] += r * s
        if ts < corte: a[3] += s; a[4] += r * s
        else:          a[5] += s; a[6] += r * s
        if m5 is not None:  a[7] += m5 * s;  a[8] += s
        if m30 is not None: a[9] += m30 * s; a[10] += s
    for c in sorted(agg):
        n, sz, pnl, str_, ptr, ste, pte, p5, s5, p30, s30 = agg[c]
        if sz <= 0: continue
        print(f"    {etiquetas[c]:<16}{n:>8}{sz:>11,.0f}{100*pnl/sz:>12.2f}"
              f"{(f'{100*ptr/str_:+.2f}' if str_ else '—'):>9}"
              f"{(f'{100*pte/ste:+.2f}' if ste else '—'):>9}"
              f"{(f'{100*p5/s5:+.2f}' if s5 else '—'):>8}"
              f"{(f'{100*p30/s30:+.2f}' if s30 else '—'):>8}")


def main():
    desde = int(time.mktime(time.strptime(DESDE, "%Y-%m-%d")) - time.timezone)
    print(f"Operaciones de izzyaussie desde {DESDE}…")
    ops = sus_operaciones(desde)
    cid2ws = {}
    for t in ops:
        try: cid2ws[t["conditionId"]] = int(t["slug"].rsplit("-", 1)[1])
        except Exception: pass
    print(f"  {len(ops)} ops · {len(cid2ws)} ventanas")

    tk = tokens(sorted(cid2ws))
    kl = velas(sorted(set(cid2ws.values())))
    print(f"  velas listas para {sum(1 for v in kl.values() if v)} ventanas")

    # referencia D3 por ventana: media de los 60 s ANTERIORES a la apertura
    ref = {}
    for ws, v in kl.items():
        xs = [v[t] for t in range(ws - L, ws) if t in v]
        if len(xs) >= L // 2: ref[ws] = sum(xs) / len(xs)

    print("\nLeyendo la cinta cacheada…")
    hist = defaultdict(list); compras = []
    n = 0
    for r in csv.reader(open(CINTA, encoding="utf-8", errors="replace")):
        if len(r) != 7: continue
        cid, ts, asset, side, p, s, wal = r
        if not asset or cid not in cid2ws: continue
        try: ts, p, s = int(ts), float(p), float(s)
        except ValueError: continue
        hist[(cid, asset)].append((ts, p, s))
        if side == "BUY": compras.append((cid, asset, ts, p, s, wal == IZZY))
        n += 1
        if n % 200000 == 0: sys.stdout.write(f"\r  {n:,}"); sys.stdout.flush()
    print(f"\r  {n:,} operaciones · {len(compras):,} compras")
    for k in hist: hist[k].sort()
    idx = {k: [x[0] for x in v] for k, v in hist.items()}

    def vwap(k, a, b):
        v, i = hist[k], bisect.bisect_left(idx[k], a)
        ps = sz = 0.0
        while i < len(v) and v[i][0] < b:
            ps += v[i][1] * v[i][2]; sz += v[i][2]; i += 1
        return (ps / sz) if sz > 0 else None

    def spot(ws, t):
        v = kl.get(ws)
        if not v: return None
        for d in range(0, 6):                 # tolera huecos de hasta 5 s hacia atras
            if t - d in v: return v[t - d]
        return None

    print("\nCalculando…")
    izzyf, otrosf = [], []
    for j, (cid, asset, ts, p, s, es_izzy) in enumerate(compras):
        meta = tk.get((cid, asset))
        if meta is None: continue
        es_up, gano = meta
        ws = cid2ws[cid]
        r0 = ref.get(ws); sp = spot(ws, ts)
        if r0 is None or sp is None: continue
        margen = (sp - r0) * (1 if es_up else -1)      # + = compro el lado que va ganando
        m5 = vwap((cid, asset), ts + 3, ts + 8)
        m30 = vwap((cid, asset), ts + 25, ts + 35)
        fila = (ts, margen, gano - p, s,
                (m5 - p) if m5 is not None else None,
                (m30 - p) if m30 is not None else None)
        (izzyf if es_izzy else otrosf).append(fila)
        if j % 200000 == 0 and j:
            sys.stdout.write(f"\r  {j:,}/{len(compras):,}"); sys.stdout.flush()
    print(f"\r  izzy {len(izzyf):,} · otros {len(otrosf):,}")

    tss = sorted(f[0] for f in izzyf); corte = tss[len(tss) // 2]
    CORTES = [-60, -25, -8, 8, 25, 60]
    ETI = ["pierde >60$", "pierde 25-60$", "pierde 8-25$", "empate ±8$",
           "gana 8-25$", "gana 25-60$", "gana >60$"]

    for nom, datos in (("IZZYAUSSIE", izzyf), ("TODOS LOS DEMAS (control)", otrosf)):
        print("\n" + "=" * 86)
        print(f"  {nom} — {len(datos):,} compras")
        print("=" * 86)
        sz = sum(f[3] for f in datos)
        print(f"  global {100*sum(f[2]*f[3] for f in datos)/sz:+.2f} pp/accion · {sz:,.0f} acciones")
        tabla("por MARGEN de BTC frente a la referencia D3, con el signo del token comprado",
              [(f[0], cubo(f[1], CORTES), f[2], f[3], f[4], f[5]) for f in datos], ETI, corte)

    print("\n" + "=" * 86)
    print("  m5/m30 = MARKOUT: lo que vale el token 5 s / 30 s despues, menos lo pagado.")
    print("  markout ALTO  → compra antes del movimiento (velocidad / informacion)")
    print("  markout PLANO pero pp/accion alto → acierta la RESOLUCION, no el siguiente minuto")
    print("  Y como siempre: si el cubo bueno lo es tambien para el control, es regla publica.")
    print("=" * 86)


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
