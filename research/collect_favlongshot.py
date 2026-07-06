"""
Test favorito-longshot en Polymarket (multi-mercado, diverso).

Para cada mercado binario Yes/No resuelto: histórico de precios del token "Yes" +
resultado real. Se mira si el PRECIO predice la frecuencia real:
  - favoritos (precio alto)  → ¿ganan MÁS que su precio?  (infravalorados = +EV comprarlos)
  - longshots (precio bajo)  → ¿ganan MENOS que su precio? (sobrevalorados)

Guarda los datos crudos para re-analizar sin volver a bajar nada.
"""
import urllib.request, json, sys, time, csv, os, statistics
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass

OUT_DIR = os.path.dirname(__file__)
MAX_MARKETS = 250         # mercados con histórico a procesar (1er pase)
MIN_VOLUME  = 1000        # $ mínimo para asegurar trading real

def get(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "research/0.1"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except Exception as e:
            if i == tries - 1:
                return None
            time.sleep(0.6 * (i + 1))

# ── 1) Recolectar candidatos binarios resueltos, diversos ─────────────────────
print("Recolectando mercados resueltos…")
cands = []
seen = set()
for offset in range(0, 4000, 500):
    mk = get(f"https://gamma-api.polymarket.com/markets"
             f"?closed=true&limit=500&offset={offset}&order=endDate&ascending=false")
    if not mk:
        break
    for m in mk:
        try:
            outs = json.loads(m.get("outcomes") or "[]")
            prices = json.loads(m.get("outcomePrices") or "[]")
            toks = json.loads(m.get("clobTokenIds") or "[]")
            vol = float(m.get("volumeNum") or 0)
        except Exception:
            continue
        mid = m.get("id")
        if mid in seen:
            continue
        if (outs == ["Yes", "No"] and len(toks) == 2
                and prices in (["1", "0"], ["0", "1"]) and vol >= MIN_VOLUME):
            seen.add(mid)
            cands.append((mid, m.get("question", "")[:80], toks[0], vol, prices[0] == "1"))
    time.sleep(0.25)
print(f"  candidatos binarios decisivos con vol>=${MIN_VOLUME}: {len(cands)}")

# ── 2) Bajar histórico, agrupar puntos (precio, ganó) ─────────────────────────
pooled = []     # (mid, price, yes_won, vol)
permkt = []     # (mid, median_price, yes_won, vol)   una obs independiente por mercado
used = 0
for mid, q, tok, vol, yes_won in cands:
    if used >= MAX_MARKETS:
        break
    h = get(f"https://clob.polymarket.com/prices-history?market={tok}&interval=max&fidelity=1440")
    hist = (h or {}).get("history", [])
    if len(hist) < 3:
        continue
    used += 1
    ps = [pt["p"] for pt in hist[:-1] if 0.02 < pt["p"] < 0.98]   # excluir el último (converge a 0/1)
    if not ps:
        continue
    for p in ps:
        pooled.append((mid, round(p, 4), 1 if yes_won else 0, vol))
    permkt.append((mid, round(statistics.median(ps), 4), 1 if yes_won else 0, vol))
    if used % 50 == 0:
        print(f"  procesados {used} mercados con histórico…")
    time.sleep(0.2)

print(f"  mercados con histórico usados: {used} | puntos diarios: {len(pooled)}")

# Guardar crudo
with open(os.path.join(OUT_DIR, "favlongshot_pooled.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f); w.writerow(["market_id", "yes_price", "yes_won", "volume"]); w.writerows(pooled)
with open(os.path.join(OUT_DIR, "favlongshot_markets.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f); w.writerow(["market_id", "median_yes_price", "yes_won", "volume"]); w.writerows(permkt)

# ── 3) Calibración ────────────────────────────────────────────────────────────
def table(data, title):
    print("\n" + "=" * 60 + f"\n  {title}\n" + "=" * 60)
    print(f"  {'precio Yes':>11} | {'gana Yes':>9} | {'n':>5} | {'EV comprar Yes':>14}")
    print("  " + "-" * 52)
    buckets = [(0.0,.1),(.1,.2),(.2,.3),(.3,.4),(.4,.5),(.5,.6),(.6,.7),(.7,.8),(.8,.9),(.9,1.01)]
    for lo, hi in buckets:
        sel = [d for d in data if lo <= d[1] < hi]
        if len(sel) < 10:
            continue
        n = len(sel)
        freq = sum(d[2] for d in sel) / n
        midp = sum(d[1] for d in sel) / n
        ev = freq / midp - 1 if midp > 0 else 0
        flag = ""
        if ev > 0.03 and n >= 30: flag = "  <-- infravalorado (+EV)"
        if ev < -0.03 and n >= 30: flag = "  (sobrevalorado)"
        print(f"  {lo:.1f}-{hi:>4.2f}  | {freq:>8.0%} | {n:>5} | {ev:>+13.0%}{flag}")

table(pooled, "CALIBRACIÓN — puntos diarios agrupados (mucho dato, autocorrelado)")
table(permkt, "CALIBRACIÓN — 1 obs por mercado (precio mediano, independiente)")
print("\nDatos guardados: favlongshot_pooled.csv / favlongshot_markets.csv")
