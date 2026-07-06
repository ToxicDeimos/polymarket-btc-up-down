"""Timing de entrada de los ganadores, separado por mercado 5m vs 15m (para T_ENTRY por mercado)."""
import urllib.request, json, sys, time, statistics
from collections import defaultdict
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass
WALLETS={"izzyaussie":"0x94f471f68396ff4a3cab8cb5c47c86274b8b77a2",
         "13mm-wrench":"0x57f2faf2eb75fd26bce0b5baf5ee7ffaadd66356",
         "zmbabwe":"0xdfd4ab76f0c86c6dd913d60ccceaff4eaac591f7"}
def get(url):
    req=urllib.request.Request(url,headers={"User-Agent":"t/1"})
    try:
        with urllib.request.urlopen(req,timeout=20) as r: return json.load(r)
    except Exception: return None

byvar=defaultdict(list)   # variant -> lista de (segundos de primera entrada)
for name,w in WALLETS.items():
    win=defaultdict(lambda:{"ts":9e18,"ws":0,"var":"?"})
    off=0
    while off<=3000:
        tr=get(f"https://data-api.polymarket.com/trades?user={w}&limit=500&offset={off}")
        if not isinstance(tr,list) or not tr: break
        for t in tr:
            slug=t.get("slug","") or ""
            if "btc-updown" not in slug: continue
            try: ws=int(slug.split("-")[-1])
            except: continue
            var="15m" if "-15m-" in slug else ("5m" if "-5m-" in slug else "?")
            m=win[t.get("conditionId")];m["ws"]=ws;m["var"]=var
            m["ts"]=min(m["ts"],int(t.get("timestamp") or 0))
        if len(tr)<500: break
        off+=500;time.sleep(0.05)
    for cid,m in win.items():
        if m["var"] in ("5m","15m"):
            sec=m["ts"]-m["ws"]
            if 0<=sec<=900: byvar[m["var"]].append(sec)

print("Timing de PRIMERA entrada de los ganadores, por mercado:\n")
for var in ("5m","15m"):
    s=sorted(byvar[var])
    if s:
        print(f"  {var:>3} (ventana {300 if var=='5m' else 900}s):  n={len(s)}  "
              f"mediana {statistics.median(s):.0f}s  "
              f"| p25 {s[len(s)//4]}s  p75 {s[3*len(s)//4]}s  "
              f"= {statistics.median(s)/(300 if var=='5m' else 900)*100:.0f}% de la ventana")
