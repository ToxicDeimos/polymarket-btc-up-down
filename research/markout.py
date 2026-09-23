"""
markout.py — LA CONTRADICCIÓN. naiveflow midió la selección adversa en un número limpio: el lado ingenuo se
negocia a 0,460 y gana 39% (7pp de sobreprecio), pero vendiéndolo como maker sale −4,17 ⇒ condicionado al
relleno ese lado gana 50,5%, no 39%. Son 11,5pp en contra, y explican los 19 fracasos: encontramos desajustes
de 1-7pp y quien nos llena se lleva 11.

PERO: si quien nos llena estuviera informado en 11pp, pagando 1,1 de spread + 1,7 de comisión, los takers
serían una máquina de imprimir dinero — y la evidencia externa (y nuestra calibración) dice que PIERDEN.
Las dos cosas no caben juntas. La única conciliación: no nos llenan por listos, nos llenan porque **nuestra
cotización está VIEJA**. Nuestro libro es una foto de hasta 15 s; quien cruza no adivina, se lleva un precio
que ya no existe. Si es eso, los 11,5pp no son del mercado: son el precio de nuestra lentitud, y se arreglan
con WSS (ver en 2 ms) + orden REST (~100 ms).

La cadencia del colector varía sola (mediana 6 s, p90 7-8 s) ⇒ tenemos rellenos a muchas EDADES de cotización.
Medimos la selección adversa EN FUNCIÓN DE LA EDAD, con MARKOUTS (cuánto se movió el medio tras nuestro fill),
que es como lo mide un creador de mercado y que no habíamos calculado nunca. Menos ruidoso que el resultado
binario y separa "me llenaron porque el precio se movió" de "me llenaron y luego no pasó nada".

  A) markout por EDAD de la cotización: si a 0-2 s es ~0 y a 10-20 s es −10pp, la lentitud es TODO el problema
     y mm_ws (ya montado) es la vía. Si es plano y malo a cualquier edad, la velocidad no salva al maker.
  B) lo mismo separando BARRIDO (la operación pasa POR ENCIMA de nuestro nivel: relleno seguro) de TOQUE
     (justo a nuestro precio: dependemos de la cola).
  C) resultado a resolución por edad, para atar el markout con el dinero final.
  D) por zona de precio y por mercado.

    cd ~/polymarket-btc-up-down/research && python3 markout.py
"""
import csv, os, sys, glob, json, time, math, bisect, urllib.request

DIR = os.path.join(os.path.dirname(__file__), "lab")
CACHE = os.path.join(DIR, "clob_reso_mmtoxic.csv")
AGE = [("0-2s", 0, 2), ("2-5s", 2, 5), ("5-8s", 5, 8), ("8-12s", 8, 12),
       ("12-20s", 12, 20), (">20s", 20, 60)]
HOR = (5, 15, 30, 60)
PB = [("<0,30", 0, .30), ("0,30-0,50", .30, .50), ("0,50-0,70", .50, .70),
      ("0,70-0,90", .70, .90), (">0,90", .90, 1.0)]
MAXAGE = 60


def get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "mkout/1.0"})
            with urllib.request.urlopen(req, timeout=12) as r: return json.load(r)
        except Exception:
            if i == tries - 1: return None
            time.sleep(0.3)


def winner_clob(cid):
    d = get(f"https://clob.polymarket.com/markets/{cid}")
    if isinstance(d, dict):
        for t in d.get("tokens", []):
            if t.get("winner") is True: return t.get("outcome")
    return None


def fee(p): return 0.07 * p * (1 - p)
def rebate(p): return 0.20 * fee(p)
def mean(xs): return sum(xs) / len(xs) if xs else float("nan")
def med(xs):
    s = sorted(xs); return s[len(s) // 2] if s else float("nan")
def week(ts): return time.strftime("%Y-%U", time.gmtime(ts))


def load_books():
    """por (slug, side): ts, bid, ask ordenados."""
    tmp = {}
    for path in sorted(glob.glob(os.path.join(DIR, "books_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            rd = csv.reader(fh); next(rd, None)
            for row in rd:
                if len(row) < 11 or not row[1].startswith("btc-updown-") or row[3] not in ("Up", "Down"):
                    continue
                try:
                    ts = int(row[0]); bid = float(row[4]) if row[4] else None
                    ask = float(row[10]) if row[10] else None
                except Exception: continue
                if ask is None or bid is None or not (0 < bid < ask < 1): continue
                tmp.setdefault((row[1], row[3]), {"cid": row[2], "r": []})["r"].append((ts, bid, ask))
    B = {}
    for k, v in tmp.items():
        r = sorted(v["r"])
        B[k] = {"cid": v["cid"], "ts": [x[0] for x in r], "bid": [x[1] for x in r], "ask": [x[2] for x in r]}
    return B


def load_tape():
    """cid -> [(ts, outcome, side, price)]"""
    T = {}; seen = set()
    for path in sorted(glob.glob(os.path.join(DIR, "wintrades_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r.get("outcome") not in ("Up", "Down"): continue
                k = (r.get("tx", ""), r["outcome"], r.get("price"), r.get("trade_side"), r.get("ts_trade"))
                if k in seen: continue
                seen.add(k)
                try: ts = int(float(r["ts_trade"])); pr = float(r["price"])
                except Exception: continue
                sd = (r.get("trade_side") or r.get("side") or "").upper()
                T.setdefault(r["cid"], []).append((ts, r["outcome"], sd, pr))
    for cid in T: T[cid].sort()
    return T


def line(lab, fs, cols):
    if len(fs) < 40:
        print(f"  {lab:>16}{len(fs):>8}{'(pocos)':>10}"); return
    out = f"  {lab:>16}{len(fs):>8}{mean([f['px'] for f in fs]):>8.3f}"
    for h in HOR:
        v = [f["mk"][h] for f in fs if f["mk"].get(h) is not None]
        out += f"{100*mean(v):>+9.2f}" if len(v) >= 20 else f"{'—':>9}"
    if cols:
        rr = [f for f in fs if f["res"] is not None]
        out += f"{100*mean([f['res'] for f in rr]):>+10.2f}" if len(rr) >= 20 else f"{'—':>10}"
        out += f"{100*mean([f['win'] for f in rr]):>8.0f}%" if len(rr) >= 20 else f"{'—':>8}"
    print(out)


def hdr(cols):
    s = f"  {'edad orden':>16}{'n':>8}{'precio':>8}" + "".join(f"{f'mk {h}s':>9}" for h in HOR)
    if cols: s += f"{'a resolución':>10}{'gana%':>8}"
    print(s)


def main():
    B = load_books(); TP = load_tape()
    print(f"series de libro: {len(B)} · cids con cinta: {len(TP)}")

    reso = {}
    for fn in ("clob_reso_mmtoxic.csv", "clob_reso_mmlogs.csv", "clob_reso_mw.csv",
               "clob_reso_win.csv", "clob_reso_tape.csv", "clob_reso_uni.csv"):
        p = os.path.join(DIR, fn)
        if os.path.exists(p):
            for r in csv.DictReader(open(p, encoding="utf-8")):
                if r.get("winner"): reso[r["cid"]] = r["winner"]
    print(f"resoluciones en caché: {len(reso)}")

    FILLS = []
    nb = 0
    for (slug, side), bk in B.items():
        cid = bk["cid"]
        if cid not in TP: continue
        nb += 1
        if nb % 4000 == 0: print(f"   … {nb}")
        v = "5m" if "-5m-" in slug else "15m"
        ws = int(slug.split("-")[-1]); close = ws + (300 if v == "5m" else 900)
        tss, bids, asks = bk["ts"], bk["bid"], bk["ask"]
        win = reso.get(cid)
        res_ok = win in ("Up", "Down")

        def mid_at(t):
            k = bisect.bisect_right(tss, t) - 1
            if k < 0 or t - tss[k] > 20 or tss[k] > close - 5: return None
            return (bids[k] + asks[k]) / 2

        for ts, oc, sd, pr in TP[cid]:
            if oc != side or ts > close - 5: continue
            k = bisect.bisect_right(tss, ts) - 1
            if k < 0: continue
            age = ts - tss[k]
            if age < 0 or age > MAXAGE: continue
            a, b = asks[k], bids[k]
            if sd == "BUY" and pr >= a - 1e-9:
                pos, px, swept = -1, a, pr > a + 1e-9        # nos levantan el ask: quedamos CORTOS
            elif sd == "SELL" and pr <= b + 1e-9:
                pos, px, swept = 1, b, pr < b - 1e-9          # nos pegan al bid: quedamos LARGOS
            else:
                continue
            m0 = (a + b) / 2
            mk = {}
            for h in HOR:
                mh = mid_at(ts + h)
                mk[h] = None if mh is None else (pos * (mh - px) + rebate(px))
            if res_ok:
                w = 1 if win == side else 0
                r = (pos * (w - px)) + rebate(px)
            else:
                w = r = None
            FILLS.append({"v": v, "ws": ws, "age": age, "px": px, "pos": pos, "swept": swept,
                          "mk": mk, "res": r, "win": (w if pos > 0 else (1 - w)) if w is not None else None,
                          "spread": a - b, "m0": m0})
    print(f"rellenos simulados: {len(FILLS)} · cortos {sum(1 for f in FILLS if f['pos']<0)} · "
          f"largos {sum(1 for f in FILLS if f['pos']>0)}")
    if not FILLS:
        print("sin rellenos — revisar etiquetas de lado en la cinta"); return
    ages = sorted(f["age"] for f in FILLS)
    print(f"edad de la cotización al llenarnos: p10 {ages[len(ages)//10]}s · mediana "
          f"{ages[len(ages)//2]}s · p90 {ages[9*len(ages)//10]}s")
    print(f"spread medio en el relleno: {100*mean([f['spread'] for f in FILLS]):.2f}¢")

    print("\n" + "=" * 104)
    print("  A) MARKOUT POR EDAD DE LA COTIZACIÓN (pp por share, rebate incluido, + = ganamos)")
    print("=" * 104)
    for v in ("5m", "15m"):
        sub = [f for f in FILLS if f["v"] == v]
        if len(sub) < 200: continue
        print(f"\n  ── {v} ── (n {len(sub)})")
        hdr(True)
        for nm, lo, hi in AGE:
            line(nm, [f for f in sub if lo <= f["age"] < hi], True)

    print("\n" + "=" * 104)
    print("  B) BARRIDO (pasan por encima: relleno seguro) vs TOQUE (a nuestro precio: manda la cola)")
    print("=" * 104)
    for v in ("5m", "15m"):
        for sw, tag in ((True, "BARRIDO"), (False, "TOQUE")):
            sub = [f for f in FILLS if f["v"] == v and f["swept"] == sw]
            if len(sub) < 200: continue
            print(f"\n  ── {v} · {tag} ── (n {len(sub)})")
            hdr(True)
            for nm, lo, hi in AGE:
                line(nm, [f for f in sub if lo <= f["age"] < hi], True)

    print("\n" + "=" * 104)
    print("  C) POR LADO: ¿nos levantan el ask (cortos) o nos pegan al bid (largos)?")
    print("=" * 104)
    for v in ("5m", "15m"):
        for p, tag in ((-1, "cortos (nos levantan el ask)"), (1, "largos (nos pegan al bid)")):
            sub = [f for f in FILLS if f["v"] == v and f["pos"] == p]
            if len(sub) < 200: continue
            print(f"\n  ── {v} · {tag} ── (n {len(sub)})")
            hdr(True)
            for nm, lo, hi in AGE:
                line(nm, [f for f in sub if lo <= f["age"] < hi], True)

    print("\n" + "=" * 104)
    print("  D) POR ZONA DE PRECIO (solo rellenos frescos, ≤5 s — el objetivo alcanzable con WSS)")
    print("=" * 104)
    for v in ("5m", "15m"):
        sub = [f for f in FILLS if f["v"] == v and f["age"] <= 5]
        if len(sub) < 200: continue
        print(f"\n  ── {v} · edad ≤5 s ── (n {len(sub)})")
        hdr(True)
        for nm, lo, hi in PB:
            line(nm, [f for f in sub if lo <= f["px"] < hi], True)

    print("\nLECTURA: la pregunta es la PENDIENTE. Si el markout a 0-2 s es ~0 (o positivo con el rebate) y se")
    print("hunde con la edad, los 11,5pp eran NUESTRA LENTITUD y no una propiedad del mercado: mm_ws (WSS, ya")
    print("montado) es la vía y sabemos qué latencia hace falta. Si es igual de malo a los 2 s que a los 20, la")
    print("velocidad no salva al maker y hay que dejar de intentarlo por ahí. El markout a 5-15 s es el que")
    print("manda: a 60 s ya se mezcla con el movimiento normal del mercado.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
