"""
Mide el SPREAD REAL de favoritos profundos en mercados VIVOS (con libro activo).
La estrategia compra el favorito y aguanta a resolución → el único coste es el de
ENTRADA: pagas el bestAsk. Coste = bestAsk - mid (medio spread).

Combina el spread real con el win-rate histórico (~97% para favoritos ~0.95) y la
fee de Polymarket (≈0.07·(1-p), minúscula en extremos) → ¿queda edge NETO o lo mata?
"""
import urllib.request, json, sys, time, statistics
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass

FEE_RATE = 0.07   # fee = 0.07·importe·(1-p); en favoritos profundos es ~0.2-0.5%

def get(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "research/0.3"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except Exception:
            if i == tries-1: return None
            time.sleep(0.6*(i+1))

def book(tok):
    b = get(f"https://clob.polymarket.com/book?token_id={tok}")
    if not b: return None
    asks = b.get("asks", []); bids = b.get("bids", [])
    if not asks or not bids: return None
    ba = min(float(a["price"]) for a in asks)
    bb = max(float(x["price"]) for x in bids)
    return ba, bb

print("Buscando favoritos profundos vivos…")
samples = []   # (mid, ask, bid, spread, vol)
seen = set()
for offset in range(0, 6000, 500):
    if len(samples) >= 250: break
    mk = get(f"https://gamma-api.polymarket.com/markets?closed=false&limit=500&offset={offset}&order=volumeNum&ascending=false")
    if not mk: break
    for m in mk:
        if len(samples) >= 250: break
        try:
            outs = json.loads(m.get("outcomes") or "[]")
            pr = json.loads(m.get("outcomePrices") or "[]")
            toks = json.loads(m.get("clobTokenIds") or "[]")
            vol = float(m.get("volumeNum") or 0)
        except Exception:
            continue
        mid_id = m.get("id")
        if mid_id in seen or outs != ["Yes","No"] or len(toks) != 2: continue
        try: p0 = float(pr[0])
        except Exception: continue
        # pre-filtro: favorito profundo por precio actual
        favp = p0 if p0 >= 0.5 else 1-p0
        if not (0.85 <= favp <= 0.985): continue
        seen.add(mid_id)
        favtok = toks[0] if p0 >= 0.5 else toks[1]
        bk = book(favtok); time.sleep(0.2)
        if not bk: continue
        ask, bid = bk
        if not (0.80 < ask < 0.999 and 0 < bid < ask): continue
        mid = (ask+bid)/2
        if 0.88 <= mid <= 0.98:
            samples.append((round(mid,4), round(ask,4), round(bid,4), round(ask-bid,4), vol))
    if len(samples) % 50 < 5 and samples:
        print(f"  muestras: {len(samples)} (offset~{offset})")

n = len(samples)
print(f"\nFavoritos profundos vivos medidos: {n}")
if n < 10:
    print("Muy pocas muestras."); sys.exit()

spreads = sorted(s[3] for s in samples)
halfs   = [s[3]/2 for s in samples]
asks    = [s[1] for s in samples]
mids    = [s[0] for s in samples]
def med(x): return statistics.median(x)
print(f"  spread (ask-bid):   mediana {med(spreads)*100:.2f}¢  | p25 {spreads[n//4]*100:.1f}¢  p75 {spreads[3*n//4]*100:.1f}¢")
print(f"  medio spread:       mediana {med(halfs)*100:.2f}¢   (= coste de entrada sobre el mid)")
print(f"  mid favorito:       mediana {med(mids):.3f}")

# ── EV neto con datos reales ──────────────────────────────────────────────────
# win-rate histórico de favoritos profundos (de nuestro markets_large.csv): ~97%.
WIN = 0.97
print(f"\n=== EV NETO (win-rate histórico {WIN:.0%}, pagando bestAsk real + fee) ===")
print(f"  {'percentil spread':>17} | {'entras a':>9} | {'fee':>5} | {'EV neto':>8}")
for label, idx in [("optimista p25", n//4), ("mediana p50", n//2), ("pesimista p75", 3*n//4)]:
    ask = asks[ sorted(range(n), key=lambda i: samples[i][3])[idx] ]  # ask del spread en ese percentil
    fee = FEE_RATE * (1-ask)
    ev = WIN/ask - 1 - fee
    print(f"  {label:>17} | {ask:>8.3f} | {fee*100:>4.1f}% | {ev:>+7.1%}")
print("\n  (EV>0 = sobrevive al coste real | EV<0 = el spread lo mata)")
