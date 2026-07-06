"""
Expansión por wallet: coge las wallets más activas en BTC up/down y baja SU historial
completo (/trades?user=) → P&L a largo plazo sobre muchas ventanas → skill vs suerte.
"""
import urllib.request, json, sys, time, csv, os, statistics
from collections import defaultdict, Counter
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass

D = os.path.dirname(__file__)

def get(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent":"research/0.7"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except Exception:
            if i == tries-1: return None
            time.sleep(0.4*(i+1))

# ── A) wallets objetivo: las más activas del muestreo inicial ─────────────────
cnt, names = Counter(), {}
for r in csv.DictReader(open(os.path.join(D,"btc_trades.csv"), encoding="utf-8")):
    cnt[r["wallet"]] += 1
    if r.get("name"): names[r["wallet"]] = r["name"]
targets = [w for w,_ in cnt.most_common(35)]
print(f"Wallets objetivo (top activas): {len(targets)}")

# ── B) historial btc-updown por wallet ────────────────────────────────────────
wtrades = defaultdict(list)   # wallet -> list de trades btc
cids = {}                     # cid -> (wstart, variant)
for wi, wal in enumerate(targets):
    offset = 0
    while offset <= 2500:
        tr = get(f"https://data-api.polymarket.com/trades?user={wal}&limit=500&offset={offset}")
        if not isinstance(tr, list) or not tr: break
        for t in tr:
            slug = t.get("slug","") or ""
            if "btc-updown" not in slug: continue
            cid = t.get("conditionId")
            if cid not in cids:
                var = "15m" if "-15m-" in slug else ("5m" if "-5m-" in slug else "?")
                try: ws = int(slug.split("-")[-1])
                except Exception: ws = 0
                cids[cid] = (ws, var)
            wtrades[wal].append((cid, t.get("side"), t.get("outcome"),
                                 float(t.get("price") or 0), float(t.get("size") or 0),
                                 int(t.get("timestamp") or 0)))
        if len(tr) < 500: break
        offset += 500; time.sleep(0.1)
    time.sleep(0.1)
    if (wi+1) % 10 == 0: print(f"  {wi+1}/{len(targets)} wallets | {len(cids)} cids")

print(f"cids únicos: {len(cids)}")

# ── C) ganador por cid (CLOB) ─────────────────────────────────────────────────
winner = {}
for i, cid in enumerate(cids):
    d = get(f"https://clob.polymarket.com/markets/{cid}")
    win = None
    if d:
        for t in d.get("tokens", []):
            try: pr = float(t.get("price") or 0)
            except Exception: pr = 0
            if t.get("winner") is True or pr >= 0.95:
                win = t.get("outcome"); break
    winner[cid] = win
    if (i+1) % 50 == 0: print(f"  ganadores {i+1}/{len(cids)}")
    time.sleep(0.05)

# ── D) P&L a largo plazo por wallet ───────────────────────────────────────────
res = []
for wal, trs in wtrades.items():
    bymk = defaultdict(lambda: {"cash":0.0,"Up":0.0,"Down":0.0,"buys":0.0,"tim":[],"pr":[]})
    for cid, side, o, price, size, ts in trs:
        m = bymk[cid]; ws = cids[cid][0]
        if side == "BUY": m["cash"] -= price*size; m[o]+=size; m["buys"]+=price*size
        else: m["cash"] += price*size; m[o]-=size
        m["tim"].append(ts-ws); m["pr"].append(price)
    pnl=inv=won=nmk=0; tim=[]; pr=[]
    for cid, m in bymk.items():
        win = winner.get(cid)
        if win not in ("Up","Down"): continue
        p = m["cash"] + m[win]*1.0
        pnl += p; inv += m["buys"]; nmk += 1; won += 1 if p>0 else 0
        tim += m["tim"]; pr += m["pr"]
    if nmk >= 1:
        res.append({"w":wal,"name":names.get(wal,""),"pnl":pnl,"inv":inv,"nmk":nmk,
                    "won":won,"tim":tim,"pr":pr})

res.sort(key=lambda x:-x["pnl"])
print("\n"+"="*96)
print(f"  RANKING P&L A LARGO PLAZO  (wallets activas, su historial completo)")
print("="*96)
print(f"  {'wallet':>14} | {'P&L':>9} | {'ROI':>6} | {'ventanas':>8} | {'win%':>5} | {'timing':>7} | {'precio':>6} | nombre")
print("  "+"-"*92)
for a in res[:25]:
    roi = a["pnl"]/a["inv"] if a["inv"]>0 else 0
    tmed = statistics.median(a["tim"]) if a["tim"] else 0
    pmed = statistics.mean(a["pr"]) if a["pr"] else 0
    print(f"  {a['w'][:12]+'…':>14} | {a['pnl']:>+9.0f} | {roi:>+5.0%} | {a['nmk']:>8} | "
          f"{a['won']/a['nmk']:>4.0%} | {tmed:>6.0f}s | {pmed:>6.3f} | {a['name'][:16]}")
print(f"\n  Total ventanas únicas analizadas: {sum(1 for c in winner.values() if c in ('Up','Down'))}")
