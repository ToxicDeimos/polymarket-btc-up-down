"""
LAB — colector de datasets acumulativos para destilar la lógica real de los ganadores.

Registra EN VIVO lo que el historial de trades no guarda (la dimensión que mató todas las
reconstrucciones anteriores): el estado del LIBRO alrededor de cada fill.

Escribe CSVs diarios en research/lab/ (gitignored):
  books_YYYYMMDD.csv : top-3 niveles bid/ask (+tamaños) de ambos lados de las ventanas BTC
                       5m/15m ACTIVAS, cada ~5s
  spot_YYYYMMDD.csv  : BTC spot Binance, cada ~5s
  fills_YYYYMMDD.csv : trades de las wallets GANADORAS en btc-updown, cada ~20s
  tape_YYYYMMDD.csv  : cinta completa de trades btc-updown (todas las wallets), cada ~20s

Correr 24/7 (systemd lab-collector.service). Autónomo (stdlib). ~15-20 MB/día.
    python lab_collector.py            # loop infinito
    python lab_collector.py --once     # un ciclo y salir (smoke test)
"""
import urllib.request, json, time, csv, os, sys

POLL  = 5     # s: libros + spot
WPOLL = 20    # s: fills de ganadores + cinta
DIR = os.path.join(os.path.dirname(__file__), "lab")
WALLETS = {
    "izzyaussie":  "0x94f471f68396ff4a3cab8cb5c47c86274b8b77a2",
    "13mm-wrench": "0x57f2faf2eb75fd26bce0b5baf5ee7ffaadd66356",
    "zmbabwe":     "0xdfd4ab76f0c86c6dd913d60ccceaff4eaac591f7",
}
BH = ["ts","slug","cid","side","b1","bs1","b2","bs2","b3","bs3","a1","as1","a2","as2","a3","as3","last"]
SH = ["ts","price"]
FH = ["ts_seen","wallet","ts_trade","slug","cid","trade_side","outcome","price","size","tx"]
TH = ["ts_seen","proxy","ts_trade","slug","cid","trade_side","outcome","price","size","tx"]

def get(url, tries=2):
    for i in range(tries):
        try:
            req=urllib.request.Request(url, headers={"User-Agent":"lab/1.0"})
            with urllib.request.urlopen(req, timeout=15) as r: return json.load(r)
        except Exception:
            if i==tries-1: return None
            time.sleep(0.4)

def now(): return int(time.time())
def day(): return time.strftime("%Y%m%d", time.gmtime())
def w(name, header, row):
    path=os.path.join(DIR, f"{name}_{day()}.csv")
    new=not os.path.exists(path)
    with open(path,"a",newline="",encoding="utf-8") as f:
        cw=csv.writer(f)
        if new: cw.writerow(header)
        cw.writerow(row)

windows={}          # key -> {cid,toks,ws,wlen,slug}
def refresh_windows():
    """Descubrimiento DETERMINISTA por slug vía Gamma (el feed de trades va con minutos de
    retraso y no sirve para anclar la ventana en curso desde su inicio)."""
    t=now(); cur={"5m":(t-t%300,300), "15m":(t-t%900,900)}
    for v,(ws,wl) in cur.items():
        key=f"{v}-{ws}"
        if key in windows: continue
        slug=f"btc-updown-{v}-{ws}"
        d=get(f"https://gamma-api.polymarket.com/markets?slug={slug}")
        if not (isinstance(d,list) and d): continue
        m=d[0]
        try:
            outs=json.loads(m.get("outcomes") or "[]")
            tids=json.loads(m.get("clobTokenIds") or "[]")
        except Exception: continue
        cid=m.get("conditionId")
        if cid and len(outs)==2 and len(tids)==2:
            toks=dict(zip(outs,tids))
            if "Up" in toks and "Down" in toks:
                windows[key]={"cid":cid,"toks":toks,"ws":ws,"wlen":wl,"slug":slug}
                print(f"  + ventana {slug}")
    for k in list(windows):
        wn=windows[k]
        if wn["ws"]+wn["wlen"] < t-30: del windows[k]

def snap_books():
    ts=now()
    for wn in list(windows.values()):
        for side in ("Up","Down"):
            b=get(f"https://clob.polymarket.com/book?token_id={wn['toks'][side]}")
            if not isinstance(b,dict): continue
            bids=sorted(((float(x["price"]),float(x["size"])) for x in b.get("bids",[])), reverse=True)[:3]
            asks=sorted(((float(x["price"]),float(x["size"])) for x in b.get("asks",[])))[:3]
            row=[ts,wn["slug"],wn["cid"],side]
            for i in range(3): row += list(bids[i]) if i<len(bids) else ["",""]
            for i in range(3): row += list(asks[i]) if i<len(asks) else ["",""]
            row.append(b.get("last_trade_price") or "")
            w("books",BH,row)

def snap_spot():
    d=get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT")
    if isinstance(d,dict) and d.get("price"): w("spot",SH,[now(),d["price"]])

_seen_f=set(); _seen_t=set()
def snap_fills_tape():
    global _seen_f, _seen_t
    ts=now()
    # fills de ganadores
    for name,addr in WALLETS.items():
        tr=get(f"https://data-api.polymarket.com/trades?user={addr}&limit=25")
        for x in (tr or []):
            slug=x.get("slug","") or ""
            if not slug.startswith("btc-updown-"): continue
            h=(x.get("transactionHash",""), x.get("timestamp"), x.get("price"), x.get("outcome"))
            if h in _seen_f: continue
            _seen_f.add(h)
            w("fills",FH,[ts,name,x.get("timestamp"),slug,x.get("conditionId"),x.get("side"),
                          x.get("outcome"),x.get("price"),x.get("size"),x.get("transactionHash")])
        time.sleep(0.05)
    # cinta completa btc-updown (y de paso sirve para descubrir ventanas nuevas)
    feed=get("https://data-api.polymarket.com/trades?limit=200")
    for x in (feed or []):
        slug=x.get("slug","") or ""
        if not slug.startswith("btc-updown-"): continue
        h=(x.get("transactionHash",""), x.get("timestamp"), x.get("price"), x.get("outcome"), x.get("proxyWallet"))
        if h in _seen_t: continue
        _seen_t.add(h)
        w("tape",TH,[ts,x.get("proxyWallet"),x.get("timestamp"),slug,x.get("conditionId"),
                     x.get("side"),x.get("outcome"),x.get("price"),x.get("size"),x.get("transactionHash")])
    # recortar sets
    if len(_seen_f)>20000: _seen_f=set(list(_seen_f)[-8000:])
    if len(_seen_t)>60000: _seen_t=set(list(_seen_t)[-20000:])
    return feed

def main():
    os.makedirs(DIR, exist_ok=True)
    once = "--once" in sys.argv
    print("="*60+"\n  LAB COLLECTOR — books + spot + fills ganadores + tape\n"+"="*60)
    last_w=0
    while True:
        try:
            if now()-last_w >= WPOLL:
                snap_fills_tape(); last_w=now()
            refresh_windows()
            snap_books()
            snap_spot()
            if once:
                print("ciclo único OK"); break
            time.sleep(POLL)
        except KeyboardInterrupt:
            print("\nparado."); break
        except Exception as ex:
            print("  err:",ex); time.sleep(5)

if __name__=="__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
