"""
Recogida a GRAN ESCALA para el test favorito-longshot (limpio, resumible).

Por mercado binario resuelto guarda UNA observación independiente:
  - estimadores de probabilidad excluyendo los últimos 2 días (anti-convergencia):
      mid   = precio al 50% de la vida pre-convergencia
      q1    = precio al 25% (más temprano, menos deriva)
      twap  = media temporal pre-convergencia
      first = primer precio observado
  - yes_won, volumen, fecha de resolución (para split out-of-sample)

Guarda incrementalmente en markets_large.csv y SALTA los ya procesados → resumible.
"""
import urllib.request, json, sys, time, csv, os
from datetime import datetime, timezone
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass

OUT = os.path.join(os.path.dirname(__file__), "markets_large.csv")
TARGET = 2500          # mercados a recolectar
MIN_VOLUME = 1000
MAX_OFFSET = 20000

def get(url, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "research/0.2"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except Exception:
            if i == tries - 1: return None
            time.sleep(0.7 * (i + 1))

# Ya procesados (resumir)
done = set()
if os.path.exists(OUT):
    with open(OUT, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            done.add(r["market_id"])
    print(f"Resumiendo: {len(done)} mercados ya en CSV")

fh = open(OUT, "a", newline="", encoding="utf-8")
w = csv.writer(fh)
if not done:
    w.writerow(["market_id","resolution_ts","yes_won","volume","n_pre","first","q1","mid","twap"])

collected = len(done)
scanned = 0
for offset in range(0, MAX_OFFSET, 500):
    if collected >= TARGET: break
    mk = get(f"https://gamma-api.polymarket.com/markets?closed=true&limit=500&offset={offset}&order=endDate&ascending=false")
    if not mk: break
    for m in mk:
        if collected >= TARGET: break
        mid_id = m.get("id")
        if mid_id in done: continue
        try:
            outs = json.loads(m.get("outcomes") or "[]")
            prices = json.loads(m.get("outcomePrices") or "[]")
            toks = json.loads(m.get("clobTokenIds") or "[]")
            vol = float(m.get("volumeNum") or 0)
        except Exception:
            continue
        if not (outs == ["Yes","No"] and len(toks)==2 and prices in (["1","0"],["0","1"]) and vol>=MIN_VOLUME):
            continue
        done.add(mid_id); scanned += 1
        h = get(f"https://clob.polymarket.com/prices-history?market={toks[0]}&interval=max&fidelity=1440")
        hist = (h or {}).get("history", [])
        time.sleep(0.22)
        if len(hist) < 6: continue
        last_ts = hist[-1]["t"]
        if last_ts - hist[0]["t"] < 6*86400: continue
        pre = [pt for pt in hist if pt["t"] <= last_ts - 2*86400]
        ps = [pt["p"] for pt in pre]
        if len(ps) < 4: continue
        rec = [mid_id, last_ts, 1 if prices[0]=="1" else 0, round(vol,1), len(ps),
               round(ps[0],4), round(ps[len(ps)//4],4), round(ps[len(ps)//2],4),
               round(sum(ps)/len(ps),4)]
        w.writerow(rec); collected += 1
        if collected % 25 == 0:
            fh.flush(); print(f"  recolectados {collected} (escaneados {scanned}) offset~{offset}")
fh.close()
print(f"FIN: {collected} mercados en {OUT}")
