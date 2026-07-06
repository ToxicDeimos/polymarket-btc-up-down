"""Sonda: ¿la Data API pública da los trades de un mercado BTC 15m, con wallet/precio/lado?"""
import urllib.request, json, sys, csv
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "research/0.4"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.load(r), r.status
    except urllib.error.HTTPError as e:
        return e.read().decode()[:200], e.code
    except Exception as e:
        return str(e), "ERR"

# condition_ids reales de mercados BTC 15m (del results.csv del fade bot)
cids = []
try:
    for r in csv.DictReader(open(r"C:\Users\alexr\Desktop\results.csv", encoding="utf-8")):
        cid = r.get("condition_id","")
        if cid and cid not in cids:
            cids.append(cid)
        if len(cids) >= 3: break
except Exception as e:
    print("no leí results.csv:", e)

print("condition_ids de muestra:", cids[:3], "\n")
cid = cids[0] if cids else ""

# Probar endpoints de trades
for name, url in [
    ("data-api /trades?market", f"https://data-api.polymarket.com/trades?market={cid}&limit=5"),
    ("data-api /trades?conditionId", f"https://data-api.polymarket.com/trades?conditionId={cid}&limit=5"),
]:
    data, code = get(url)
    print(f"── {name}  [HTTP {code}]")
    if isinstance(data, list):
        print(f"   {len(data)} trades")
        if data:
            print("   claves:", ", ".join(sorted(data[0].keys())))
            t = data[0]
            for k in ["proxyWallet","side","price","size","outcome","timestamp","name","pseudonym"]:
                if k in t: print(f"     {k:12} = {t[k]}")
    else:
        print("   resp:", str(data)[:180])
    print()
