"""
Test favorito-longshot LIMPIO: precio a un HORIZONTE FIJO antes de resolver
(cuando aún hay incertidumbre real), una obs por mercado. Evita el sesgo de
convergencia (precios tardíos pegados al resultado).

Si el patrón gigante del 1er pase se DESPLOMA hacia calibrado (~0% EV) al medir
temprano → era artefacto de convergencia. Si SOBREVIVE un sesgo modesto (~2-5%)
en favoritos → posible edge real favorito-longshot.
"""
import urllib.request, json, sys, time, csv, os
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass

OUT_DIR = os.path.dirname(__file__)
MAX_MARKETS = 300
MIN_VOLUME  = 1000
LEADS = {"7d": 7*86400, "3d": 3*86400, "1d": 1*86400}

def get(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "research/0.1"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except Exception:
            if i == tries - 1: return None
            time.sleep(0.6 * (i + 1))

print("Recolectando candidatos…")
cands, seen = [], set()
for offset in range(0, 5000, 500):
    mk = get(f"https://gamma-api.polymarket.com/markets?closed=true&limit=500&offset={offset}&order=endDate&ascending=false")
    if not mk: break
    for m in mk:
        try:
            outs = json.loads(m.get("outcomes") or "[]")
            prices = json.loads(m.get("outcomePrices") or "[]")
            toks = json.loads(m.get("clobTokenIds") or "[]")
            vol = float(m.get("volumeNum") or 0)
        except Exception:
            continue
        mid = m.get("id")
        if mid in seen: continue
        if outs == ["Yes","No"] and len(toks)==2 and prices in (["1","0"],["0","1"]) and vol>=MIN_VOLUME:
            seen.add(mid); cands.append((mid, toks[0], vol, prices[0]=="1"))
    time.sleep(0.25)
print(f"  candidatos: {len(cands)}")

def price_at(hist, target_ts):
    """precio del punto más cercano a target_ts, exigiendo que exista dato cerca."""
    best, bd = None, 1e18
    for pt in hist:
        d = abs(pt["t"] - target_ts)
        if d < bd: bd, best = d, pt["p"]
    return best if bd < 1.5*86400 else None   # tolerancia 1.5 días

rows = []   # (mid, lead, price, yes_won, vol)
used = 0
for mid, tok, vol, yes_won in cands:
    if used >= MAX_MARKETS: break
    h = get(f"https://clob.polymarket.com/prices-history?market={tok}&interval=max&fidelity=1440")
    hist = (h or {}).get("history", [])
    if len(hist) < 5: continue
    last_ts = hist[-1]["t"]
    lifespan = last_ts - hist[0]["t"]
    if lifespan < 8*86400: continue           # mercado debe vivir > 8 días para tener -7d
    used += 1
    for name, lead in LEADS.items():
        p = price_at(hist[:-1], last_ts - lead)
        if p is not None and 0.02 < p < 0.98:
            rows.append((mid, name, round(p,4), 1 if yes_won else 0, vol))
    if used % 50 == 0: print(f"  {used} mercados…")
    time.sleep(0.2)

print(f"  mercados usados (vida>8d): {used}")
with open(os.path.join(OUT_DIR,"favlongshot_fixed.csv"),"w",newline="",encoding="utf-8") as f:
    w=csv.writer(f); w.writerow(["market_id","lead","yes_price","yes_won","volume"]); w.writerows(rows)

def table(lead):
    data=[(r[2],r[3]) for r in rows if r[1]==lead]
    print("\n"+"="*58+f"\n  PRECIO A {lead} ANTES DE RESOLVER  (n={len(data)})\n"+"="*58)
    print(f"  {'precio Yes':>11} | {'gana':>5} | {'n':>4} | {'EV':>6}")
    for lo,hi in [(0,.1),(.1,.2),(.2,.3),(.3,.4),(.4,.5),(.5,.6),(.6,.7),(.7,.8),(.8,.9),(.9,1.01)]:
        sel=[d for d in data if lo<=d[0]<hi]
        if len(sel)<10: continue
        n=len(sel); freq=sum(d[1] for d in sel)/n; midp=sum(d[0] for d in sel)/n
        ev=freq/midp-1 if midp else 0
        flag="  +EV" if ev>0.03 and n>=25 else ("  caro" if ev<-0.03 and n>=25 else "")
        print(f"  {lo:.1f}-{hi:<4.2f} | {freq:>4.0%} | {n:>4} | {ev:>+5.0%}{flag}")

for lead in ["7d","3d","1d"]:
    table(lead)
print("\nDatos: favlongshot_fixed.csv")
