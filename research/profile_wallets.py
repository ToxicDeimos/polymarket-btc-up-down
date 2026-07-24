"""
EXPERIMENTO #7 — PERFILAR A LOS CANDIDATOS DE VERDAD (no a los que elegimos mal).

survivorship.py destapó dos cosas: (a) izzyaussie (n=80, ROI −9.4%) y 13mm-wrench (n=95, +4.7%)
NO destacan — los elegimos por volumen/notoriedad, no por rendimiento ajustado; (b) en la cinta hay
wallets con MUCHA más muestra y mejor z que nunca miramos (n=743 +47.7%, n=442 +30.5%, n=416 +14.9%).

Este script coge a los wallets con muestra GRANDE y mejor z-score, y describe QUÉ HACEN, usando la
misma maquinaria del lab (books_*.csv + spot_*.csv). Es DESCRIPTIVO: no depende de ningún supuesto
estadístico, solo cuenta lo que hicieron.

  · MAKER vs TAKER con libro fresco (<=12s): BUY p>=mejor_ask -> cruzó = TAKER
                                             BUY p<=mejor_bid -> pasiva golpeada = MAKER
    (clave: si son MAKERS, su edge es cobrar el spread que nosotros pagábamos — 5-10pp según ask_vs_vol)
  · FASE de entrada, ZONA de precio, MOMENTUM (compran el líder) vs FADE, mercado 5m/15m
  · ROI por cada corte, para ver de dónde sale su dinero

Compara al final con izzy/13mm para ver si el patrón que perseguíamos era siquiera el correcto.

    python profile_wallets.py
Autónomo (stdlib).
"""
import os, sys, csv, glob, math, bisect
from collections import Counter

DIR    = os.path.join(os.path.dirname(__file__), "lab")
MIN_N  = 100     # solo wallets con muestra seria
TOPK   = 6       # cuántos perfilar
MAXAGE = 12      # s de frescura del libro para maker/taker
NAMED  = {"izzyaussie":  "0x94f471f68396ff4a3cab8cb5c47c86274b8b77a2",
          "13mm-wrench": "0x57f2faf2eb75fd26bce0b5baf5ee7ffaadd66356",
          "zmbabwe":     "0xdfd4ab76f0c86c6dd913d60ccceaff4eaac591f7"}

def load(name, days):
    rows = []
    for d in days:
        p = os.path.join(DIR, f"{name}_{d}.csv")
        if os.path.exists(p): rows += list(csv.DictReader(open(p, encoding="utf-8")))
    return rows

def series(rows):
    idx = sorted((int(r["ts"]), float(r["price"])) for r in rows if r.get("price"))
    ks = [x[0] for x in idx]
    def at(ts, maxage):
        i = bisect.bisect_right(ks, ts) - 1
        return idx[i][1] if i >= 0 and ts - idx[i][0] <= maxage else None
    return at

def pctl(rs, key):
    v = sorted(x[key] for x in rs)
    return v[len(v) // 2] if v else None

def roi_of(rs):
    cost = sum(t["sz"] * t["p"] for t in rs)
    pnl  = sum(t["sz"] * (t["won"] - t["p"]) for t in rs)
    return (pnl / cost) if cost else None

def dist(rs, keyfn, labels):
    """reparto % + ROI por categoría"""
    out = []
    for lab in labels:
        seg = [t for t in rs if keyfn(t) == lab]
        if not seg: continue
        r = roi_of(seg)
        out.append(f"{lab} {len(seg)*100//len(rs)}%" + (f"/{r:+.0%}" if r is not None else ""))
    return "  ".join(out) if out else "—"

def main():
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    days = sorted({os.path.basename(p).split("_")[1][:8] for p in glob.glob(os.path.join(DIR, "tape_*.csv"))})
    if not days: print("sin tape_*.csv"); return
    tape  = load("tape", days)
    books = load("books", days)
    lcl   = series(load("chainlink", days))
    lsp   = series(load("spot", days))
    print(f"días: {', '.join(days)}  |  cinta {len(tape)}  |  libros {len(books)}")

    # índice de libro por (cid, side)
    bidx = {}
    for b in books:
        try:
            ts = int(b["ts"])
            b1 = float(b["b1"]) if b.get("b1") else None
            a1 = float(b["a1"]) if b.get("a1") else None
        except Exception: continue
        bidx.setdefault((b.get("cid"), b.get("side")), []).append((ts, b1, a1))
    for k in bidx: bidx[k].sort()
    def book_before(cid, side, t):
        arr = bidx.get((cid, side))
        if not arr: return None
        i = bisect.bisect_right([x[0] for x in arr], t) - 1
        if i < 0: return None
        ts, b1, a1 = arr[i]
        return (b1, a1) if t - ts <= MAXAGE else None

    # ── resolver y anotar cada compra ────────────────────────────────────────────────────────
    seen, T = set(), []
    for x in tape:
        key = (x.get("tx"), x.get("ts_trade"), x.get("price"), x.get("outcome"), x.get("proxy"))
        if key in seen: continue
        seen.add(key)
        slug = x.get("slug", "") or ""
        try:
            ws = int(slug.split("-")[-1]); t = int(x["ts_trade"])
            p = float(x["price"]); sz = float(x.get("size") or 0)
        except Exception: continue
        if p <= 0 or p >= 1 or sz <= 0: continue
        wlen = 900 if "-15m-" in slug else 300
        o, c = lcl(ws, 60), lcl(ws + wlen, 60)
        if o is None or c is None:
            o, c = lsp(ws, 12), lsp(ws + wlen, 12)
        if o is None or c is None: continue
        winner = "Up" if c >= o else "Down"
        out = x.get("outcome")
        # momentum: ¿compró el lado hacia el que iba el spot al entrar?
        so, se = lsp(ws, 12), lsp(t, 12)
        lead = None
        if so is not None and se is not None and abs(se - so) > 1:
            lead = "Up" if se > so else "Down"
        # maker/taker
        mt = None
        bk = book_before(x.get("cid"), out, t)
        if bk:
            b1, a1 = bk
            if x.get("trade_side") == "BUY":
                if a1 is not None and p >= a1 - 1e-9: mt = "taker"
                elif b1 is not None and p <= b1 + 1e-9: mt = "maker"
        T.append({"w": x.get("proxy"), "ws": ws, "t": t, "p": p, "sz": sz, "out": out,
                  "side": x.get("trade_side"), "mkt": "15m" if wlen == 900 else "5m",
                  "won": 1 if out == winner else 0, "fase": t - ws, "mt": mt,
                  "mom": (None if lead is None else ("momentum" if out == lead else "fade"))})
    B = [t for t in T if t["side"] == "BUY"]
    print(f"operaciones resueltas: {len(T)}  (compras {len(B)})")

    # ── ROI / z por wallet (solo compras, como survivorship) ────────────────────────────────
    WA = {}
    for t in B:
        a = WA.setdefault(t["w"], {"n": 0, "cost": 0.0, "pnl": 0.0, "var": 0.0, "tr": []})
        a["n"] += 1; a["cost"] += t["sz"] * t["p"]; a["pnl"] += t["sz"] * (t["won"] - t["p"])
        a["var"] += (t["sz"] ** 2) * t["p"] * (1 - t["p"]); a["tr"].append(t)
    for a in WA.values():
        a["roi"]   = a["pnl"] / a["cost"] if a["cost"] else 0.0
        a["sigma"] = math.sqrt(a["var"]) / a["cost"] if a["cost"] else 0.0
        a["z"]     = a["roi"] / a["sigma"] if a["sigma"] else 0.0
    CAND = sorted([(w, a) for w, a in WA.items() if a["n"] >= MIN_N],
                  key=lambda kv: -kv[1]["z"])[:TOPK]
    print(f"wallets con >={MIN_N} compras: {sum(1 for a in WA.values() if a['n']>=MIN_N)}"
          f"  ·  perfilando el top {len(CAND)} por z\n")

    def profile(tag, w, a):
        rs = a["tr"]
        allw = [t for t in T if t["w"] == w]
        nb = sum(1 for t in allw if t["side"] == "BUY"); ns = len(allw) - nb
        mt = [t for t in rs if t["mt"]]
        mk = sum(1 for t in mt if t["mt"] == "maker")
        mo = [t for t in rs if t["mom"]]
        mm = sum(1 for t in mo if t["mom"] == "momentum")
        print("=" * 96)
        print(f"{tag}  {w}")
        print(f"  n={a['n']} compras · ROI {a['roi']:+.1%} · sigma {a['sigma']:.1%} · z {a['z']:+.2f}"
              f" · BUY/SELL {nb}/{ns} · tamaño mediano ${pctl(rs,'sz'):.0f}")
        if mt:
            print(f"  MAKER/TAKER (libro fresco, n={len(mt)}): maker {mk*100//len(mt)}% / "
                  f"taker {(len(mt)-mk)*100//len(mt)}%"
                  f"   [maker ROI {roi_of([t for t in mt if t['mt']=='maker']) or 0:+.0%} · "
                  f"taker ROI {roi_of([t for t in mt if t['mt']!='maker']) or 0:+.0%}]")
        else:
            print("  MAKER/TAKER: sin libro fresco para estas operaciones")
        if mo:
            print(f"  MOMENTUM/FADE (n={len(mo)}): momentum {mm*100//len(mo)}% / fade {(len(mo)-mm)*100//len(mo)}%"
                  f"   [mom ROI {roi_of([t for t in mo if t['mom']=='momentum']) or 0:+.0%} · "
                  f"fade ROI {roi_of([t for t in mo if t['mom']=='fade']) or 0:+.0%}]")
        print(f"  MERCADO : {dist(rs, lambda t: t['mkt'], ['5m','15m'])}")
        print(f"  FASE    : {dist(rs, lambda t: ('0-60' if t['fase']<60 else '60-120' if t['fase']<120 else '120-180' if t['fase']<180 else '180-240' if t['fase']<240 else '240-300' if t['fase']<300 else '>300'), ['0-60','60-120','120-180','180-240','240-300','>300'])}")
        print(f"  PRECIO  : {dist(rs, lambda t: ('<20c' if t['p']<.20 else '20-40c' if t['p']<.40 else '40-52c' if t['p']<.52 else '52-72c' if t['p']<.72 else '72-82c' if t['p']<.82 else '82-95c' if t['p']<.95 else '>95c'), ['<20c','20-40c','40-52c','52-72c','72-82c','82-95c','>95c'])}")
        print("           (cada celda = % de sus operaciones / ROI en esa celda)")

    for i, (w, a) in enumerate(CAND, 1): profile(f"CANDIDATO #{i}", w, a)

    print("\n" + "=" * 96)
    print("LOS QUE PERSEGUÍAMOS (para comparar el patrón)")
    for nm, ad in NAMED.items():
        a = WA.get(ad) or WA.get(ad.lower())
        if a: profile(f"«{nm}»", ad, a)
        else: print(f"  «{nm}»: sin datos suficientes en la cinta")

    print("\n" + "=" * 96)
    print("QUÉ BUSCAR")
    print("=" * 96)
    print("· Si los candidatos salen MAKER y nosotros éramos taker: su edge es COBRAR el spread")
    print("  (ask_vs_vol midió que el líder se paga 5-10pp por encima de su probabilidad real).")
    print("  Eso es replicable en principio, pero exige poner órdenes pasivas y comerse la")
    print("  selección adversa — que es lo que mató a maker_paper (11ª muerte).")
    print("· Si salen TAKER y aun así ganan, mira FASE y PRECIO: estarían entrando donde nosotros no.")
    print("· Si su patrón NO se parece al de izzy/13mm, confirma que perseguíamos el modelo equivocado.")

if __name__ == "__main__":
    main()
