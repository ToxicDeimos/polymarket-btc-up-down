"""
EXPERIMENTO #14 — PIPELINE PROFESIONAL: historial COMPLETO de los ganadores, no la cinta muestreada.

Toda la ingeniería inversa previa fue sobre tape_*.csv = cinta muestreada cada 20s (limit 200) =
INCOMPLETA (se pierde trades). Los z-scores, ROIs y bandas están sobre datos parciales y sesgados.

Esto lo hace bien: baja el historial COMPLETO de cada wallet ganador vía la Data API
(/trades?user=ADDR, paginado = CADA trade, todos sus btc-updown-5m), lo cachea, resuelve cada ventana
con las velas 1m de Binance ya cacheadas (klines_1m_btc.csv, un año), y saca las conclusiones del
dataset masivo y completo:
  · ROI y z REALES (no sesgados por muestreo) → ¿quién es ganador de verdad?
  · SCALP vs HOLD (¿vende dentro de la ventana = spread, o aguanta = resultado?)
  · DINERO por banda de precio de entrada (win vs precio = EV/share) → ¿dónde está su edge?

    python winners_deep.py
Necesita internet (Data API) + lab/klines_1m_btc.csv (lo genera hist_backtest.py). Cachea en lab/.
Autónomo (stdlib).
"""
import os, sys, csv, json, time, math, urllib.request
from collections import defaultdict

DIR   = os.path.join(os.path.dirname(__file__), "lab")
KL    = os.path.join(DIR, "klines_1m_btc.csv")
MIN_N = 50

# Candidatos ganadores (originales + top-z de la cinta + top-z de survivorship). Fácil añadir más.
CANDIDATES = {
    "izzyaussie":  "0x94f471f68396ff4a3cab8cb5c47c86274b8b77a2",
    "13mm-wrench": "0x57f2faf2eb75fd26bce0b5baf5ee7ffaadd66356",
    "zmbabwe":     "0xdfd4ab76f0c86c6dd913d60ccceaff4eaac591f7",
    "w-8805":      "0x8805da933e457de807f9788493c3394f2035721f",
    "w-0445":      "0x04454d6a686c5909724dc6a27555875eb86ebbf9",
    "w-f3a6":      "0xf3a6ef82d0904db48c0ad8016ca62c556fee8c6c",
    "w-f27d":      "0xf27d40745542dc871e127acff3a1c9d3910d9a88",
    "w-8856":      "0x8856e15c9bbdd939cb98e2776f725f502aaef6ad",
    "w-27eb":      "0x27eb7b85c51a254a81fcdb3e43033bfce90ab0c4",
    "w-9a2f":      "0x9a2f9100cd8accb9bb8ab1e3e025b042c0d5c62b",
}

def get(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "winners-deep/1.0"})
            with urllib.request.urlopen(req, timeout=25) as r: return json.load(r)
        except Exception:
            if i == tries - 1: return None
            time.sleep(1.0 + i)

def load_klines():
    if not os.path.exists(KL):
        print(f"falta {KL} — corre antes: python3 hist_backtest.py 365"); sys.exit(1)
    d = {}
    with open(KL, encoding="utf-8") as f:
        for ln in f:
            a = ln.split(",")
            try: d[int(a[0])] = float(a[1])
            except Exception: pass
    return d

def pull_all(name, addr):
    """Historial COMPLETO de btc-updown-5m de un wallet (paginado). Cachea en lab/wtrades_{name}.csv."""
    cache = os.path.join(DIR, f"wtrades_{name}.csv")
    if os.path.exists(cache):
        return list(csv.DictReader(open(cache, encoding="utf-8")))
    rows = []; offset = 0
    while True:
        d = get(f"https://data-api.polymarket.com/trades?user={addr}&limit=100&offset={offset}")
        if not isinstance(d, list) or not d: break
        for x in d:
            slug = x.get("slug", "") or ""
            if not slug.startswith("btc-updown-5m-"): continue
            rows.append({"ts": x.get("timestamp"), "slug": slug, "side": x.get("side"),
                         "outcome": x.get("outcome"), "price": x.get("price"), "size": x.get("size"),
                         "cid": x.get("conditionId")})
        if len(d) < 100: break
        offset += 100
        if offset > 40000: print(f"   ({name}: tope de paginación 40k)"); break
        if offset % 2000 == 0: print(f"   ... {name} offset {offset} ({len(rows)} btc)")
        time.sleep(0.15)
    os.makedirs(DIR, exist_ok=True)
    with open(cache, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["ts", "slug", "side", "outcome", "price", "size", "cid"])
        w.writeheader(); w.writerows(rows)
    return rows

def main():
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    kl = load_klines()
    print(f"velas 1m en caché: {len(kl)}  |  bajando historial COMPLETO de {len(CANDIDATES)} wallets…\n")

    def winner(ws):
        o, c = kl.get(ws), kl.get(ws + 300)
        return ("Up" if c >= o else "Down") if (o is not None and c is not None) else None

    results = []
    for name, addr in CANDIDATES.items():
        raw = pull_all(name, addr)
        # normaliza + resuelve
        byw = defaultdict(list)  # ws -> ops
        for r in raw:
            try:
                ws = int(r["slug"].split("-")[-1]); p = float(r["price"]); sz = float(r["size"] or 0)
            except Exception: continue
            if not (0 < p < 1) or sz <= 0: continue
            byw[ws].append({"side": r["outcome"], "bs": r["side"], "p": p, "sz": sz})
        # métricas COMPLETAS (solo BUY, aguantado, para ROI/z)
        n = cost = pnl = var = 0
        scalp = hold = 0; band = []
        for ws, ops in byw.items():
            wn = winner(ws)
            buys = [o for o in ops if o["bs"] == "BUY"]; sells = [o for o in ops if o["bs"] == "SELL"]
            for o in buys:
                if wn is None: continue
                won = 1 if o["side"] == wn else 0
                n += 1; cost += o["sz"] * o["p"]; pnl += o["sz"] * (won - o["p"]); var += (o["sz"] ** 2) * o["p"] * (1 - o["p"])
            if not buys: continue
            bsz = sum(o["sz"] for o in buys); ssz = sum(o["sz"] for o in sells)
            if ssz >= 0.8 * bsz and sells: scalp += 1
            else:
                hold += 1
                first = buys[0]
                if wn is not None: band.append((first["p"], 1 if first["side"] == wn else 0))
        if n < MIN_N:
            print(f"  {name:<12} solo {n} compras completas — skip"); continue
        roi = pnl / cost if cost else 0; z = (roi) / (math.sqrt(var) / cost) if (cost and var) else 0
        results.append((z, name, addr, n, roi, scalp, hold, band))

    results.sort(reverse=True)
    print("\n" + "=" * 98)
    print("GANADORES sobre su historial COMPLETO (no la cinta muestreada) — ROI y z REALES")
    print("=" * 98)
    for z, name, addr, n, roi, scalp, hold, band in results:
        nw = scalp + hold
        print(f"\n{name}  {addr}")
        print(f"  {n} compras COMPLETAS  ·  ROI {roi:+.1%}  ·  z {z:+.2f}  ·  "
              f"SCALP {scalp*100//nw if nw else 0}% / HOLD {hold*100//nw if nw else 0}%")
        if band:
            print("  DINERO por banda de precio (win vs precio = EV/share):")
            for lo, hi, lab in [(0, .20, "<20¢"), (.20, .40, "20-40¢"), (.40, .62, "40-62¢"),
                                (.62, .82, "62-82¢"), (.82, .95, "82-95¢"), (.95, 1.01, "95-99¢")]:
                seg = [(p, w) for p, w in band if lo <= p < hi]
                if not seg: continue
                m = len(seg); wr = sum(w for _, w in seg) / m; ap = sum(p for p, _ in seg) / m; ev = (wr - ap) * 100
                print(f"     {lab:<8} n={m:>4}  win {wr:5.1%}  precio {ap:5.1%}  EV/share {ev:+6.2f}pp"
                      + ("  +EV" if ev > 1 else ("  −EV" if ev < -1 else "")))

    print("\n" + "=" * 98)
    print("CONCLUSIÓN PROFESIONAL")
    print("=" * 98)
    print("· ROI/z sobre historial COMPLETO = quién gana de verdad (sin sesgo de muestreo de la cinta).")
    print("· SCALP alto → edge del spread (postear bid+ask, plano). HOLD alto → apuestan al resultado.")
    print("· DINERO por banda EN VARIOS GANADORES: si TODOS son +EV en la MISMA banda → ahí está el edge")
    print("  común y replicable. Si cada uno gana en bandas distintas → es selección/estilo, no una regla.")

if __name__ == "__main__":
    main()
