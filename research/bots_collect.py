"""
Recoge trades de mercados BTC Up/Down para ingeniería inversa de ganadores.
1) Descubre cids desde el feed global de trades (BTC up/down = ~40% del feed).
2) Ganador por cid vía CLOB /markets/{cid}.
3) Trades completos por cid vía Data API.
"""
import urllib.request, json, sys, time, csv, os
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass

OUT = os.path.join(os.path.dirname(__file__), "btc_trades.csv")

def get(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent":"research/0.6"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except Exception:
            if i == tries-1: return None
            time.sleep(0.4*(i+1))

# ── 1) Descubrir cids BTC up/down desde el feed global ────────────────────────
print("Descubriendo cids desde el feed…")
meta = {}   # cid -> (wstart, variant)
for offset in range(0, 5000, 500):
    tr = get(f"https://data-api.polymarket.com/trades?limit=500&offset={offset}")
    if not isinstance(tr, list): continue
    for t in tr:
        slug = t.get("slug","") or ""
        if "btc-updown" not in slug: continue
        cid = t.get("conditionId")
        if cid and cid not in meta:
            variant = "15m" if "-15m-" in slug else ("5m" if "-5m-" in slug else "?")
            try: wstart = int(slug.split("-")[-1])
            except Exception: wstart = 0
            meta[cid] = (wstart, variant)
    time.sleep(0.15)
print(f"  cids BTC descubiertos: {len(meta)}")

def winner_of(cid):
    d = get(f"https://clob.polymarket.com/markets/{cid}")
    if not d: return None
    for t in d.get("tokens", []):
        try: pr = float(t.get("price") or 0)
        except Exception: pr = 0
        if t.get("winner") is True or pr >= 0.95:
            return t.get("outcome")
    return None

# ── 2+3) Ganador + trades completos por cid ──────────────────────────────────
fh = open(OUT, "w", newline="", encoding="utf-8")
w = csv.writer(fh)
w.writerow(["wallet","cid","wstart","variant","winner","side","outcome","price","size","ts","name"])
mk = tt = 0
for cid, (wstart, variant) in meta.items():
    win = winner_of(cid); time.sleep(0.1)
    if win not in ("Up","Down"): continue
    mk += 1
    offset = 0
    while True:
        tr = get(f"https://data-api.polymarket.com/trades?market={cid}&limit=500&offset={offset}")
        if not tr: break
        for t in tr:
            w.writerow([t.get("proxyWallet"), cid, wstart, variant, win, t.get("side"),
                        t.get("outcome"), t.get("price"), t.get("size"),
                        t.get("timestamp"), t.get("name","")])
        tt += len(tr)
        if len(tr) < 500: break
        offset += 500; time.sleep(0.1)
    time.sleep(0.1)
fh.close()
print(f"FIN: {mk} mercados resueltos, {tt} trades -> {OUT}")
