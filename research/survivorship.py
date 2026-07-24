"""
EXPERIMENTO #6 — ¿HABILIDAD o SUPERVIVENCIA? El test que debimos hacer ANTES de copiarlos.

Encontramos a izzyaussie / 13mm-wrench / zmbabwe BUSCANDO QUIÉN HABÍA GANADO. Entre miles de
wallets operando miles de ventanas, los mejores VAN a parecer excelentes por puro azar: su ROI
medido está sesgado al alza por construcción de la muestra. Nunca lo comprobamos — y montamos
tres experimentos encima de esa base.

Aquí se comprueba, usando tape_*.csv (cinta COMPLETA: todas las wallets, no solo las nuestras):

  1. ROI de TODAS las wallets (misma metodología que lab_edge: solo BUY, se aguanta a resolución).
  2. La distribución completa: percentiles, cuántas ganan, cuántas pierden.
  3. TEST NULO (mercado eficiente, CERO habilidad): si el precio del mercado FUERA la probabilidad
     verdadera, cada apuesta es EV=0 y el ROI de cada wallet es puro ruido con sigma conocida:
         sigma_roi = sqrt( Σ size²·p·(1−p) ) / coste_total
     De ahí un z-score por wallet. Y como elegimos al MEJOR de N, la pregunta correcta no es
     "¿el mejor tiene z alto?" sino "¿es su z mayor que el MÁXIMO DE N wallets por azar?".
     Monte Carlo con el resultado de cada VENTANA compartido entre wallets (respeta la correlación:
     dos wallets que apuestan lo mismo ganan o pierden juntas).

  Si el ROI de los ganadores cae DENTRO del azar → supervivencia: no había nada que copiar, y todo
  encaja sin contradicción con que comprar al ask sea −EV.
  Si está MUY por encima → habilidad real, y toca excavar en el CÓMO (maker vs taker, timing).

    python survivorship.py
Autónomo (stdlib). Resuelve por CHAINLINK donde hay dato, spot local si no.
"""
import os, sys, csv, glob, math, bisect, random, datetime as dt

DIR = os.path.join(os.path.dirname(__file__), "lab")
MIN_TRADES = 30      # wallets con menos no entran al test (ruido puro)
SIMS       = 400     # simulaciones Monte Carlo del nulo

def load(name, days):
    rows = []
    for d in days:
        p = os.path.join(DIR, f"{name}_{d}.csv")
        if os.path.exists(p):
            rows += list(csv.DictReader(open(p, encoding="utf-8")))
    return rows

def series(rows):
    idx = sorted((int(r["ts"]), float(r["price"])) for r in rows if r.get("price"))
    ks = [x[0] for x in idx]
    def at(ts, maxage):
        i = bisect.bisect_right(ks, ts) - 1
        return idx[i][1] if i >= 0 and ts - idx[i][0] <= maxage else None
    return at

def main():
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    days = sorted({os.path.basename(p).split("_")[1][:8] for p in glob.glob(os.path.join(DIR, "tape_*.csv"))})
    if not days:
        print("sin tape_*.csv — el colector debe llevar corriendo"); return
    tape = load("tape", days)
    lcl  = series(load("chainlink", days))
    lsp  = series(load("spot", days))
    print(f"días: {', '.join(days)}  |  filas de cinta: {len(tape)}")

    # ── resolver cada compra ──────────────────────────────────────────────────────────────────
    seen, T = set(), []
    ncl = nbin = 0
    for x in tape:
        if x.get("trade_side") != "BUY": continue
        key = (x.get("tx"), x.get("ts_trade"), x.get("price"), x.get("outcome"), x.get("proxy"))
        if key in seen: continue
        seen.add(key)
        slug = x.get("slug", "") or ""
        try:
            ws = int(slug.split("-")[-1]); p = float(x["price"]); sz = float(x.get("size") or 0)
        except Exception: continue
        if p <= 0 or p >= 1 or sz <= 0: continue
        wlen = 900 if "-15m-" in slug else 300
        o, c = lcl(ws, 60), lcl(ws + wlen, 60)
        if o is not None and c is not None: ncl += 1
        else:
            o, c = lsp(ws, 12), lsp(ws + wlen, 12); nbin += 1
        if o is None or c is None: continue
        winner = "Up" if c >= o else "Down"          # regla Polymarket: empate → Up
        T.append({"w": x.get("proxy"), "ws": ws, "p": p, "sz": sz,
                  "won": 1 if x.get("outcome") == winner else 0,
                  "up": 1 if x.get("outcome") == "Up" else 0})
    if len(T) < 500:
        print(f"solo {len(T)} compras resueltas — deja acumular el colector"); return
    print(f"compras resueltas: {len(T)}  (por Chainlink {ncl}, por spot {nbin})")

    # ── ROI por wallet ────────────────────────────────────────────────────────────────────────
    WA = {}
    for t in T:
        a = WA.setdefault(t["w"], {"n": 0, "cost": 0.0, "pnl": 0.0, "var": 0.0, "tr": []})
        a["n"] += 1
        a["cost"] += t["sz"] * t["p"]
        a["pnl"] += t["sz"] * (t["won"] - t["p"])
        a["var"] += (t["sz"] ** 2) * t["p"] * (1 - t["p"])     # varianza bajo el nulo
        a["tr"].append(t)
    for a in WA.values():
        a["roi"]   = a["pnl"] / a["cost"] if a["cost"] else 0.0
        a["sigma"] = math.sqrt(a["var"]) / a["cost"] if a["cost"] else 0.0   # sigma del ROI bajo nulo
        a["z"]     = a["roi"] / a["sigma"] if a["sigma"] else 0.0
    BIG = {w: a for w, a in WA.items() if a["n"] >= MIN_TRADES}
    print(f"wallets totales: {len(WA)}  |  con ≥{MIN_TRADES} compras: {len(BIG)}")
    if len(BIG) < 5:
        print("muy pocas wallets con volumen — deja acumular más días"); return

    rois = sorted(a["roi"] for a in BIG.values())
    def pct(q): return rois[min(len(rois) - 1, int(len(rois) * q))]
    pos = sum(1 for r in rois if r > 0)
    print("\n" + "=" * 84)
    print(f"1) DISTRIBUCIÓN de ROI entre las {len(BIG)} wallets con ≥{MIN_TRADES} compras")
    print("=" * 84)
    print(f"   p10 {pct(.10):+7.1%}   p25 {pct(.25):+7.1%}   MEDIANA {pct(.50):+7.1%}   "
          f"p75 {pct(.75):+7.1%}   p90 {pct(.90):+7.1%}")
    print(f"   en positivo: {pos}/{len(BIG)} ({pos/len(BIG):.0%})   "
          f"·  ROI agregado del mercado: {sum(a['pnl'] for a in BIG.values())/sum(a['cost'] for a in BIG.values()):+.2%}")
    print("   (si la mediana es negativa y ~la mitad gana, es exactamente lo que produce el azar + vig)")

    print("\n" + "=" * 84)
    print("2) TOP 10 por ROI  —  y su z-score bajo el nulo 'el precio es la probabilidad'")
    print("=" * 84)
    print(f"   {'wallet':<44} {'n':>5} {'ROI':>9} {'sigma':>8} {'z':>7}")
    for w, a in sorted(BIG.items(), key=lambda kv: -kv[1]["roi"])[:10]:
        print(f"   {w:<44} {a['n']:>5} {a['roi']:>+8.1%} {a['sigma']:>7.1%} {a['z']:>+7.2f}")
    print("\n   TOP 10 por Z-SCORE (el ranking que IMPORTA: pondera por nº de operaciones —")
    print("   un ROI alto con 30 apuestas es ruido; con 500 ya no):")
    print(f"   {'wallet':<44} {'n':>5} {'ROI':>9} {'sigma':>8} {'z':>7}")
    for w, a in sorted(BIG.items(), key=lambda kv: -kv[1]["z"])[:10]:
        print(f"   {w:<44} {a['n']:>5} {a['roi']:>+8.1%} {a['sigma']:>7.1%} {a['z']:>+7.2f}")

    NAMED = {"izzyaussie":  "0x94f471f68396ff4a3cab8cb5c47c86274b8b77a2",
             "13mm-wrench": "0x57f2faf2eb75fd26bce0b5baf5ee7ffaadd66356",
             "zmbabwe":     "0xdfd4ab76f0c86c6dd913d60ccceaff4eaac591f7"}
    print("\n   NUESTROS 'ganadores' (los que intentamos copiar):")
    for nm, ad in NAMED.items():
        a = WA.get(ad) or WA.get(ad.lower())
        if not a: print(f"   {nm:<14} (no aparece en la cinta capturada)"); continue
        better = sum(1 for b in BIG.values() if b["roi"] > a["roi"])
        print(f"   {nm:<14} n={a['n']:>4}  ROI {a['roi']:+.1%}  sigma {a['sigma']:.1%}  "
              f"z {a['z']:+.2f}   ({better} wallets lo superan)")

    # ── 3) Monte Carlo del nulo, con resultado de VENTANA compartido ─────────────────────────
    print("\n" + "=" * 84)
    print(f"3) TEST NULO — ¿el MEJOR de {len(BIG)} wallets se explica por azar? ({SIMS} simulaciones)")
    print("=" * 84)
    # prob implícita de Up por ventana (ponderada por tamaño) para simular con correlación real
    WIN = {}
    for t in T:
        d = WIN.setdefault(t["ws"], [0.0, 0.0])
        d[0] += t["sz"] * (t["p"] if t["up"] else 1 - t["p"]); d[1] += t["sz"]
    pup = {ws: (v[0] / v[1] if v[1] else 0.5) for ws, v in WIN.items()}
    # agrega por (wallet, ventana): tamaño apostado a Up y a Down
    AGG = {}
    for w, a in BIG.items():
        g = {}
        for t in a["tr"]:
            e = g.setdefault(t["ws"], [0.0, 0.0])
            e[0 if t["up"] else 1] += t["sz"]
        AGG[w] = (list(g.items()), a["cost"], sum(x["sz"] * x["p"] for x in a["tr"]))
    obs_best = max(a["roi"] for a in BIG.values())
    obs_bz   = max(a["z"] for a in BIG.values())
    best_rois, best_zs = [], []
    rnd = random.Random(12345)
    for _ in range(SIMS):
        up = {ws: (1 if rnd.random() < q else 0) for ws, q in pup.items()}
        br, bz = -9, -9
        for w, (glist, cost, paid) in AGG.items():
            pnl = 0.0
            for ws, (su, sd) in glist:
                u = up.get(ws, 0)
                pnl += (su * u + sd * (1 - u))
            pnl -= paid
            r = pnl / cost if cost else 0
            br = max(br, r)
            s = BIG[w]["sigma"]
            if s: bz = max(bz, r / s)
        best_rois.append(br); best_zs.append(bz)
    best_rois.sort(); best_zs.sort()
    # El estadístico correcto es el MÁXIMO Z, no el máximo ROI: el ROI más alto se lo llevan
    # wallets pequeñas con suerte (pocas apuestas = varianza enorme). El z pondera por nº de
    # operaciones, que es justo lo que distingue habilidad de racha.
    pval_r = sum(1 for b in best_rois if b >= obs_best) / SIMS
    pval   = sum(1 for b in best_zs if b >= obs_bz) / SIMS
    print(f"   (referencia) MEJOR ROI observado: {obs_best:+.1%}  ·  por azar mediana "
          f"{best_rois[SIMS//2]:+.1%}, p95 {best_rois[int(SIMS*.95)]:+.1%}  → p={pval_r:.3f}")
    print()
    print(f"   MEJOR Z observado             : {obs_bz:+.2f}")
    print(f"   MEJOR Z por AZAR (mediana)    : {best_zs[SIMS//2]:+.2f}")
    print(f"   MEJOR Z por AZAR (p95 / p99)  : {best_zs[int(SIMS*.95)]:+.2f} / {best_zs[int(SIMS*.99)]:+.2f}")
    print(f"   p-valor (sobre el MÁXIMO Z)   : {pval:.3f}   ← el que decide")
    print()
    if pval > 0.10:
        print("   → El mejor wallet NO supera lo que el azar produce eligiendo al mejor de tantas.")
        print("     SUPERVIVENCIA: no había habilidad que copiar. Encaja sin contradicción con que")
        print("     comprar al ask sea −EV: los 'ganadores' eran los afortunados de la distribución.")
    elif pval > 0.01:
        print("   → Al límite. Sugiere algo de habilidad pero no es concluyente: más días de cinta.")
    else:
        print("   → HABILIDAD REAL: el mejor supera claramente al azar incluso corrigiendo por")
        print("     haber elegido al mejor de tantas. Toca excavar el CÓMO (maker vs taker, timing,")
        print("     precio de ejecución) — hay algo que aprender de verdad.")

    print("\nCAVEATS:")
    print(f"  · la cinta se muestrea cada 20s (limit=200): si el mercado va rápido se pierden trades,")
    print(f"    así que los n son una COTA INFERIOR. No sesga el ROI, pero resta potencia.")
    print("  · solo compras (BUY) aguantadas a resolución, igual que lab_edge — quien VENDE antes de")
    print("    resolver no se mide bien. Es la MISMA metodología que produjo el +9.7%, así que la")
    print("    comparación es justa: se está testeando ESA cifra.")
    print("  · el nulo asume que el precio es la probabilidad real (EV=0). Con vig el EV real es algo")
    print("    negativo, así que el nulo es CONSERVADOR: favorece detectar habilidad, no ocultarla.")

if __name__ == "__main__":
    main()
