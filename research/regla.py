"""
regla.py — ¿CUAL ES LA REGLA DE IZZYAUSSIE, Y FUNCIONA PARA CUALQUIERA?

izzy.py dejo demostrado que su edge NO esta en el relleno (+0,03 pp/accion frente a otro comprador
del mismo token en el mismo instante) sino en QUE compra y CUANDO: +4,93 pp/accion. Eso es una
DECISION, y una decision se puede copiar sin ser el. Falta saber cual es.

Aqui se calculan, para cada compra, features OBSERVABLES ESTRICTAMENTE ANTES del instante de la
compra (nada de mirar al futuro — esa es la trampa que mato a dist30 y a multiframe-30m):

    t_rel   segundo de la ventana (0-300)
    p       precio pagado
    d30     p − precio del MISMO token hace 30 s   (¿compra tras una caida?)
    d60     igual a 60 s
    vol30   volumen negociado en ese token en los 30 s previos

Y entonces el test que de verdad decide, que es el que no hicimos nunca con los ganadores:

    la misma regla, aplicada a las compras de TODO EL MUNDO en esas mismas ventanas.

  · si el cubo que el usa gana TAMBIEN para los demas → es una regla PUBLICA y la operamos.
  · si solo gana cuando la ejecuta el → es informacion privada suya y se cierra la via.

El control es enorme (toda la cinta de las 469 ventanas, ~832.000 operaciones) y esta emparejado
por construccion: mismas ventanas, mismos tokens, mismos instantes disponibles.

    cd ~/polymarket-btc-up-down/research && python3 regla.py

Usa la cinta ya cacheada por izzy.py (izzy_cinta2.csv). Cachea las resoluciones en clob_tokens.csv.
"""
import bisect, csv, json, os, sys, time, math, urllib.request, urllib.error
from collections import defaultdict

csv.field_size_limit(10 ** 7)
DIR = os.path.dirname(os.path.abspath(__file__))
CINTA = os.path.join(DIR, "izzy_cinta2.csv")
TOKENS = os.path.join(DIR, "clob_tokens.csv")
IZZY = "0x94f471f68396ff4a3cab8cb5c47c86274b8b77a2"
DESDE = "2026-09-01"
PAUSA = 0.1


def get(url, reintentos=3):
    for i in range(reintentos):
        try:
            r = urllib.request.Request(url, headers={"User-Agent": "research/1.0"})
            with urllib.request.urlopen(r, timeout=40) as f:
                return json.loads(f.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                return None
            if i == reintentos - 1: raise
        except Exception:
            if i == reintentos - 1: raise
        time.sleep(1.5 * (i + 1))
    return None


# ───────────────── 1. sus operaciones (para el mapa cid→ventana) ─────────────────
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


# ───────────────── 2. ganador de cada token, via CLOB ─────────────────
def ganadores(cids):
    gan = {}
    if os.path.exists(TOKENS):
        for r in csv.reader(open(TOKENS, encoding="utf-8", errors="replace")):
            if len(r) == 3:
                gan[(r[0], r[1])] = int(r[2])
    faltan = [c for c in cids if not any(k[0] == c for k in gan)]
    if faltan:
        print(f"  pidiendo resolucion de {len(faltan)} mercados al CLOB…")
        f = open(TOKENS, "a", newline="", encoding="utf-8"); w = csv.writer(f)
        for i, cid in enumerate(faltan, 1):
            d = get(f"https://clob.polymarket.com/markets/{cid}")
            for tk in (d or {}).get("tokens", []):
                v = 1 if tk.get("winner") else 0
                gan[(cid, tk["token_id"])] = v
                w.writerow((cid, tk["token_id"], v))
            f.flush()
            if i % 25 == 0 or i == len(faltan):
                sys.stdout.write(f"\r    {i}/{len(faltan)}"); sys.stdout.flush()
            time.sleep(PAUSA)
        f.close(); print()
    return gan


def cubo(v, cortes):
    for i, c in enumerate(cortes):
        if v < c: return i
    return len(cortes)


def tabla(titulo, filas, etiquetas, corte_tr):
    """filas = [(ts, cubo, pnl_por_accion, size)]"""
    print(f"\n  {titulo}")
    print(f"    {'cubo':<16}{'n':>8}{'acciones':>11}{'pp/accion':>12}"
          f"{'train':>10}{'test':>10}")
    agg = defaultdict(lambda: [0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])  # n, sz, pnl, sz_tr, pnl_tr, sz_te, pnl_te
    for ts, c, r, s in filas:
        a = agg[c]
        a[0] += 1; a[1] += s; a[2] += r * s
        if ts < corte_tr: a[3] += s; a[4] += r * s
        else:             a[5] += s; a[6] += r * s
    for c in sorted(agg):
        n, sz, pnl, str_, ptr, ste, pte = agg[c]
        if sz <= 0: continue
        f_tr = f"{100*ptr/str_:+.2f}" if str_ > 0 else "—"
        f_te = f"{100*pte/ste:+.2f}" if ste > 0 else "—"
        print(f"    {etiquetas[c]:<16}{n:>8}{sz:>11,.0f}{100*pnl/sz:>12.2f}{f_tr:>10}{f_te:>10}")


def main():
    desde = int(time.mktime(time.strptime(DESDE, "%Y-%m-%d")) - time.timezone)
    print(f"Operaciones de izzyaussie desde {DESDE}…")
    ops = sus_operaciones(desde)
    cid2ws, cid2slug = {}, {}
    for t in ops:
        try: cid2ws[t["conditionId"]] = int(t["slug"].rsplit("-", 1)[1])
        except Exception: pass
        cid2slug[t["conditionId"]] = t["slug"]
    print(f"  {len(ops)} ops · {len(cid2ws)} ventanas")

    gan = ganadores(sorted(cid2ws))
    print(f"  ganadores conocidos: {len(gan)} tokens")

    print("\nLeyendo la cinta cacheada…")
    hist = defaultdict(list)          # (cid, asset) -> [(ts, p, s)]  todas las ops
    compras = []                      # (cid, asset, ts, p, s, es_izzy)
    n = 0
    for r in csv.reader(open(CINTA, encoding="utf-8", errors="replace")):
        if len(r) != 7: continue
        cid, ts, asset, side, p, s, wal = r
        if not asset: continue
        try: ts, p, s = int(ts), float(p), float(s)
        except ValueError: continue
        if cid not in cid2ws: continue
        hist[(cid, asset)].append((ts, p, s))
        if side == "BUY":
            compras.append((cid, asset, ts, p, s, wal == IZZY))
        n += 1
        if n % 200000 == 0:
            sys.stdout.write(f"\r  {n:,} operaciones"); sys.stdout.flush()
    print(f"\r  {n:,} operaciones · {len(compras):,} compras")
    for k in hist: hist[k].sort()
    idx = {k: [x[0] for x in v] for k, v in hist.items()}

    def vwap(k, a, b):
        """precio medio ponderado del token k en [a, b). Solo pasado."""
        v, i = hist[k], bisect.bisect_left(idx[k], a)
        ps = sz = 0.0
        while i < len(v) and v[i][0] < b:
            ps += v[i][1] * v[i][2]; sz += v[i][2]; i += 1
        return (ps / sz) if sz > 0 else None, sz

    # ── features (solo pasado estricto) ──
    print("\nCalculando features…")
    izzyf, otrosf = [], []
    for j, (cid, asset, ts, p, s, es_izzy) in enumerate(compras):
        g = gan.get((cid, asset))
        if g is None: continue
        p30, _ = vwap((cid, asset), ts - 35, ts - 25)
        p60, _ = vwap((cid, asset), ts - 65, ts - 55)
        _, v30 = vwap((cid, asset), ts - 30, ts)
        r = g - p                                  # pnl por accion
        fila = (ts, cid2ws[cid], p, p30, p60, v30, r, s)
        (izzyf if es_izzy else otrosf).append(fila)
        if j % 200000 == 0 and j:
            sys.stdout.write(f"\r  {j:,}/{len(compras):,}"); sys.stdout.flush()
    print(f"\r  izzy {len(izzyf):,} compras · otros {len(otrosf):,} compras")

    tss = sorted(f[0] for f in izzyf)
    corte = tss[len(tss) // 2]
    print(f"  corte train/test: {time.strftime('%Y-%m-%d', time.gmtime(corte))}")

    CORTES_D = [-0.05, -0.02, -0.005, 0.005, 0.02, 0.05]
    ETI_D = ["cayo >5c", "cayo 2-5c", "cayo 0,5-2c", "plano", "subio 0,5-2c",
             "subio 2-5c", "subio >5c"]
    CORTES_T = [60, 120, 180, 240]
    ETI_T = ["0-60s", "60-120s", "120-180s", "180-240s", "240-300s"]
    CORTES_P = [0.15, 0.30, 0.45, 0.60, 0.80]
    ETI_P = ["<0,15", "0,15-0,30", "0,30-0,45", "0,45-0,60", "0,60-0,80", ">0,80"]

    for nom, datos in (("IZZYAUSSIE", izzyf), ("TODOS LOS DEMAS (control)", otrosf)):
        print("\n" + "=" * 78)
        print(f"  {nom} — {len(datos):,} compras")
        print("=" * 78)
        sz = sum(f[7] for f in datos); pl = sum(f[6] * f[7] for f in datos)
        print(f"  global: {100*pl/sz:+.2f} pp/accion sobre {sz:,.0f} acciones")
        con30 = [f for f in datos if f[3] is not None]
        tabla("por MOVIMIENTO del token en los 30 s previos",
              [(f[0], cubo(f[2] - f[3], CORTES_D), f[6], f[7]) for f in con30], ETI_D, corte)
        tabla("por SEGUNDO de la ventana",
              [(f[0], cubo(f[0] - f[1], CORTES_T), f[6], f[7]) for f in datos], ETI_T, corte)
        tabla("por PRECIO pagado",
              [(f[0], cubo(f[2], CORTES_P), f[6], f[7]) for f in datos], ETI_P, corte)

    print("\n" + "=" * 78)
    print("  COMO LEERLO: si un cubo gana para IZZY y tambien para TODOS LOS DEMAS, con el")
    print("  mismo signo en train y en test, es una regla PUBLICA y operable. Si solo gana")
    print("  para izzy, la informacion esta en SU decision y no en la feature.")
    print("  Ojo: 'todos los demas' incluye makers, que pagan menos — su nivel base sera")
    print("  mejor que el nuestro. Lo que importa es la DIFERENCIA entre cubos, no el nivel.")
    print("=" * 78)


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
