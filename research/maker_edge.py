"""
EXPERIMENTO #8 — ¿el edge MAKER es ESTRUCTURAL del mercado, o suerte de los 6 que yo elegí?

profile_wallets mostró que los 6 mejores wallets ganan TODOS por el lado maker (y tres pierden como
taker), con el dinero concentrado en opciones baratas (<20-40¢). Pero esos 6 los seleccioné por
z-score alto: construir sobre eso sería repetir el error de izzy/13mm.

Este script mide el edge maker sobre la POBLACIÓN ENTERA de operaciones, no por wallet, y hace la
prueba que de verdad decide:

  LEAVE-OUT: repetir el análisis EXCLUYENDO a los wallets top por z.
             · si el edge maker SOBREVIVE sin ellos → es una característica ESTRUCTURAL del
               mercado (el spread), replicable en principio por cualquiera que ponga órdenes pasivas
             · si DESAPARECE sin ellos → era selección, y no había nada que copiar (otra vez)

Métrica: EDGE = win_rate − precio_medio (en puntos porcentuales), que es el EV por share, con IC.
Se reporta por ZONA DE PRECIO porque ahí es donde el perfilado vio el dinero (el spread relativo en
opciones baratas es enorme: comprar a 6¢ en vez de 9¢ es 33% menos coste por el mismo pago).

    python maker_edge.py
Autónomo (stdlib).
"""
import os, sys, csv, glob, math, bisect

DIR    = os.path.join(os.path.dirname(__file__), "lab")
# frescura del libro para clasificar maker/taker. Con libro viejo, un taker que cruzó DESPUÉS de que
# el libro se moviera puede contarse como maker → control: correr también con 5 (python maker_edge.py 5)
MAXAGE = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 12
MIN_N  = 100     # para identificar el "top por z" que se excluye en el leave-out
TOPK   = 6
ZONES  = [(0, .20, "<20c"), (.20, .40, "20-40c"), (.40, .52, "40-52c"), (.52, .72, "52-72c"),
          (.72, .82, "72-82c"), (.82, .95, "82-95c"), (.95, 1.01, ">95c")]

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

def line(label, rs):
    """EDGE = win_rate − precio medio (EV por share), con IC del win rate."""
    if not rs:
        print(f"    {label:<26} (sin datos)"); return
    n = len(rs)
    wr = sum(t["won"] for t in rs) / n
    ap = sum(t["p"] for t in rs) / n
    se = math.sqrt(wr * (1 - wr) / n)
    lo, hi = wr - 1.96 * se, wr + 1.96 * se
    edge = (wr - ap) * 100
    nw = len({t["w"] for t in rs})
    sig = "SIG" if (lo > ap) else ("+" if wr > ap else "")
    print(f"    {label:<26} n={n:>6} ({nw:>4} wallets)  win {wr:6.2%}  precio {ap:6.2%}  "
          f"EDGE {edge:+6.2f}pp  [IC win {lo:5.1%}-{hi:5.1%}] {sig}")

def block(title, T):
    print(f"\n  {title}")
    mk = [t for t in T if t["mt"] == "maker"]
    tk = [t for t in T if t["mt"] == "taker"]
    line("MAKER (todo)", mk)
    line("TAKER (todo)", tk)
    print("    — MAKER por zona de precio:")
    for lo, hi, lab in ZONES:
        line(f"      maker {lab}", [t for t in mk if lo <= t["p"] < hi])
    print("    — TAKER por zona (referencia: lo que pagábamos nosotros):")
    for lo, hi, lab in ZONES:
        line(f"      taker {lab}", [t for t in tk if lo <= t["p"] < hi])

def main():
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    days = sorted({os.path.basename(p).split("_")[1][:8] for p in glob.glob(os.path.join(DIR, "tape_*.csv"))})
    if not days: print("sin tape_*.csv"); return
    tape, books = load("tape", days), load("books", days)
    lcl, lsp = series(load("chainlink", days)), series(load("spot", days))
    print(f"días: {', '.join(days)}  |  cinta {len(tape)}  |  libros {len(books)}")

    bidx = {}
    for b in books:
        try:
            ts = int(b["ts"])
            b1 = float(b["b1"]) if b.get("b1") else None
            a1 = float(b["a1"]) if b.get("a1") else None
        except Exception: continue
        bidx.setdefault((b.get("cid"), b.get("side")), []).append((ts, b1, a1))
    for k in bidx: bidx[k].sort()

    seen, T = set(), []
    for x in tape:
        if x.get("trade_side") != "BUY": continue
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
        if o is None or c is None: o, c = lsp(ws, 12), lsp(ws + wlen, 12)
        if o is None or c is None: continue
        arr = bidx.get((x.get("cid"), x.get("outcome")))
        mt = None
        if arr:
            i = bisect.bisect_right([y[0] for y in arr], t) - 1
            if i >= 0 and t - arr[i][0] <= MAXAGE:
                b1, a1 = arr[i][1], arr[i][2]
                if a1 is not None and p >= a1 - 1e-9: mt = "taker"
                elif b1 is not None and p <= b1 + 1e-9: mt = "maker"
        if mt is None: continue
        T.append({"w": x.get("proxy"), "p": p, "sz": sz, "mt": mt,
                  "won": 1 if x.get("outcome") == ("Up" if c >= o else "Down") else 0})
    if len(T) < 1000:
        print(f"solo {len(T)} operaciones clasificadas con libro fresco — deja acumular"); return
    print(f"operaciones con libro fresco clasificadas: {len(T)}  "
          f"(maker {sum(1 for t in T if t['mt']=='maker')}, taker {sum(1 for t in T if t['mt']=='taker')})")

    # top por z (los que perfilé) — para excluirlos después
    WA = {}
    for t in T:
        a = WA.setdefault(t["w"], {"n": 0, "cost": 0.0, "pnl": 0.0, "var": 0.0})
        a["n"] += 1; a["cost"] += t["sz"] * t["p"]; a["pnl"] += t["sz"] * (t["won"] - t["p"])
        a["var"] += (t["sz"] ** 2) * t["p"] * (1 - t["p"])
    for a in WA.values():
        a["z"] = (a["pnl"] / a["cost"]) / (math.sqrt(a["var"]) / a["cost"]) if a["cost"] and a["var"] else 0.0
    top = {w for w, _ in sorted([(w, a) for w, a in WA.items() if a["n"] >= MIN_N],
                                key=lambda kv: -kv[1]["z"])[:TOPK]}

    print("\n" + "=" * 104)
    print("1) POBLACIÓN COMPLETA")
    print("=" * 104)
    block("todas las wallets:", T)

    print("\n" + "=" * 104)
    print(f"2) LEAVE-OUT — EXCLUYENDO a los {len(top)} wallets top por z (los que perfilé)")
    print("=" * 104)
    print("   Si el edge maker sobrevive aquí, NO era suerte de los elegidos: es del mercado.")
    block("resto de wallets:", [t for t in T if t["w"] not in top])

    # ¿lo lleva una minoría? distribución del edge maker por wallet
    print("\n" + "=" * 104)
    print("3) ¿ES DE UNOS POCOS? — edge maker por wallet (solo wallets con >=30 fills maker)")
    print("=" * 104)
    per = {}
    for t in T:
        if t["mt"] != "maker": continue
        per.setdefault(t["w"], []).append(t)
    es = []
    for w, rs in per.items():
        if len(rs) < 30: continue
        es.append(sum(t["won"] for t in rs) / len(rs) - sum(t["p"] for t in rs) / len(rs))
    if es:
        es.sort()
        pos = sum(1 for e in es if e > 0)
        print(f"   wallets: {len(es)}   ·   con edge maker POSITIVO: {pos} ({pos*100//len(es)}%)")
        print(f"   p25 {es[len(es)//4]*100:+.2f}pp   MEDIANA {es[len(es)//2]*100:+.2f}pp   "
              f"p75 {es[3*len(es)//4]*100:+.2f}pp")
        print("   (si la MEDIANA es claramente positiva, el edge lo tiene el maker MEDIO,")
        print("    no un puñado de afortunados → estructural)")
    else:
        print("   pocas wallets con >=30 fills maker todavía")

    print("\n" + "=" * 104)
    print("CÓMO LEER  ·  'SIG' = el IC del win rate queda por encima del precio pagado")
    print("=" * 104)
    print("· El bloque 2 es el que decide. Si el edge maker aguanta SIN los 6 elegidos → estructural.")
    print("· Compara maker vs taker en la MISMA zona: la diferencia es el spread que se cobra o se paga.")
    print("· OJO, esto NO es todavía una estrategia: mide fills maker que YA ocurrieron. Poner la orden")
    print("  y que te la llenen es otra cosa — la SELECCIÓN ADVERSA (te llenan cuando el mercado va en")
    print("  tu contra) es lo que mató a maker_paper. Este test dice si merece la pena intentarlo,")
    print("  no que funcione. La prueba real sería un maker en papel en la zona <40c.")

if __name__ == "__main__":
    main()
