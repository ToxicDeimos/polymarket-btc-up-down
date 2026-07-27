"""
LAB — colector de datasets acumulativos para destilar la lógica real de los ganadores.

Registra EN VIVO lo que el historial de trades no guarda (la dimensión que mató todas las
reconstrucciones anteriores): el estado del LIBRO alrededor de cada fill.

Escribe CSVs diarios en research/lab/ (gitignored):
  books_YYYYMMDD.csv    : top-3 niveles bid/ask (+tamaños) de ambos lados de las ventanas BTC
                          5m/15m ACTIVAS, cada ~5s
  bookdepth_YYYYMMDD.csv: PROFUNDIDAD del libro COMPLETO (no solo top-3): nº de niveles, tamaño total
                          bid/ask, tamaño dentro de 2¢ del tope, imbalance de todo el libro; cada ~5s
  spot_YYYYMMDD.csv     : BTC spot Binance, cada ~5s
  fills_YYYYMMDD.csv    : trades de las wallets GANADORAS en btc-updown, cada ~20s
  tape_YYYYMMDD.csv     : cinta muestreada global de btc-updown (todas las wallets), cada ~20s
  wintrades_YYYYMMDD.csv: cinta COMPLETA por ventana activa (filtro market=cid) → aggressor flow real
                          (la global muestreada perdía trades y degeneraba el aflow del minero); cada ~10s

Correr 24/7 (systemd lab-collector.service). Autónomo (stdlib). ~20-30 MB/día.
Los flujos nuevos (bookdepth/wintrades) son ADITIVOS: no tocan books_/tape_ ya grabados. Al reiniciar el
servicio empiezan a acumular desde cero; la data previa sigue válida (esquema de books_/tape_ intacto).
    python lab_collector.py            # loop infinito
    python lab_collector.py --once     # un ciclo y salir (smoke test: imprime conteos de los flujos nuevos)
"""
import urllib.request, json, time, csv, os, sys

POLL   = 5     # s: libros + spot
WPOLL  = 20    # s: fills de ganadores + cinta global muestreada
WT_POLL = 10   # s: cinta COMPLETA por ventana (aggressor flow)
DIR = os.path.join(os.path.dirname(__file__), "lab")
WALLETS = {
    "izzyaussie":  "0x94f471f68396ff4a3cab8cb5c47c86274b8b77a2",
    "13mm-wrench": "0x57f2faf2eb75fd26bce0b5baf5ee7ffaadd66356",
    "zmbabwe":     "0xdfd4ab76f0c86c6dd913d60ccceaff4eaac591f7",
    # + los 3 grandes confirmados por z (winners_deep) — para tener también sus fills en vivo
    "w-f3a6":      "0xf3a6ef82d0904db48c0ad8016ca62c556fee8c6c",
    "w-9a2f":      "0x9a2f9100cd8accb9bb8ab1e3e025b042c0d5c62b",
    "w-0445":      "0x04454d6a686c5909724dc6a27555875eb86ebbf9",
}
BH = ["ts","slug","cid","side","b1","bs1","b2","bs2","b3","bs3","a1","as1","a2","as2","a3","as3","last"]
BAH = ["ts","slug","cid","side","nb","na","bdepth","adepth","bdepth2c","adepth2c","fullimb"]
WTH = ["ts_seen","cid","slug","ts_trade","trade_side","outcome","price","size","tx"]
SH = ["ts","price"]
CH = ["ts","price","updated_at"]
# Chainlink BTC/USD on-chain (Polygon) — proxy del Data Stream con el que RESUELVE Polymarket.
# Cadencia ~30s (heartbeat/desviación). Observado en vivo: hasta ~$45 de divergencia vs Binance.
CL_FEED = "0xc907E116054Ad103354f2D350FD2514433D57F6f"
RPCS = ["https://polygon-bor-rpc.publicnode.com","https://polygon.drpc.org",
        "https://polygon-mainnet.public.blastapi.io"]
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
            rawb=[(float(x["price"]),float(x["size"])) for x in b.get("bids",[])]
            rawa=[(float(x["price"]),float(x["size"])) for x in b.get("asks",[])]
            bids=sorted(rawb, reverse=True)[:3]
            asks=sorted(rawa)[:3]
            row=[ts,wn["slug"],wn["cid"],side]
            for i in range(3): row += list(bids[i]) if i<len(bids) else ["",""]
            for i in range(3): row += list(asks[i]) if i<len(asks) else ["",""]
            row.append(b.get("last_trade_price") or "")
            w("books",BH,row)
            # PROFUNDIDAD del libro COMPLETO (no solo top-3): agregados que el top-3 no captura.
            if rawb or rawa:
                bb=max(p for p,_ in rawb) if rawb else None
                ba=min(p for p,_ in rawa) if rawa else None
                bdepth=sum(s for _,s in rawb); adepth=sum(s for _,s in rawa)
                bd2=sum(s for p,s in rawb if bb is not None and p>=bb-0.02)   # tamaño a ≤2¢ del tope bid
                ad2=sum(s for p,s in rawa if ba is not None and p<=ba+0.02)   # tamaño a ≤2¢ del tope ask
                fullimb=round(bdepth/(bdepth+adepth),4) if (bdepth+adepth)>0 else ""
                w("bookdepth",BAH,[ts,wn["slug"],wn["cid"],side,len(rawb),len(rawa),
                                   round(bdepth,2),round(adepth,2),round(bd2,2),round(ad2,2),fullimb])

_seen_wt=set()
def snap_wintrades():
    """Cinta COMPLETA por ventana ACTIVA (filtro market=cid) → aggressor flow real. La cinta global
    (limit200/20s) muestrea y pierde trades: por eso el aflow del minero salía degenerado. Aquí, por
    cada ventana viva, se piden sus trades y se deduplica → registro completo del flujo agresor."""
    global _seen_wt
    nnew=0; ts=now()
    for wn in list(windows.values()):
        d=get(f"https://data-api.polymarket.com/trades?market={wn['cid']}&limit=100")
        for x in (d or []):
            h=(x.get("transactionHash",""), x.get("timestamp"), x.get("price"), x.get("outcome"), x.get("side"))
            if h in _seen_wt: continue
            _seen_wt.add(h); nnew+=1
            w("wintrades",WTH,[ts,wn["cid"],wn["slug"],x.get("timestamp"),x.get("side"),
                               x.get("outcome"),x.get("price"),x.get("size"),x.get("transactionHash")])
        time.sleep(0.05)
    if len(_seen_wt)>40000: _seen_wt=set(list(_seen_wt)[-15000:])
    return nnew

def snap_spot():
    d=get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT")
    if isinstance(d,dict) and d.get("price"): w("spot",SH,[now(),d["price"]])

def snap_chainlink():
    """Lee el feed on-chain Chainlink BTC/USD (latestRoundData) vía RPC público gratuito."""
    payload=json.dumps({"jsonrpc":"2.0","id":1,"method":"eth_call","params":[
        {"to":CL_FEED,"data":"0xfeaf968c"},"latest"]}).encode()
    for url in RPCS:
        try:
            req=urllib.request.Request(url, data=payload,
                headers={"Content-Type":"application/json","User-Agent":"lab/1.0"})
            with urllib.request.urlopen(req, timeout=10) as r: res=json.load(r)
            h=res["result"][2:]; words=[h[i:i+64] for i in range(0,len(h),64)]
            price=int(words[1],16)/1e8; upd=int(words[3],16)
            if 1000<price<1e7:
                w("chainlink",CH,[now(),round(price,2),upd]); return
        except Exception:
            continue

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
    last_w=0; last_wt=0
    while True:
        try:
            if now()-last_w >= WPOLL:
                snap_fills_tape(); last_w=now()
            refresh_windows()
            snap_books()
            snap_spot()
            snap_chainlink()
            nwt=None
            if once or now()-last_wt >= WT_POLL:
                nwt=snap_wintrades(); last_wt=now()
            if once:
                print(f"ciclo único OK · {len(windows)} ventanas activas · {nwt} wintrades nuevos "
                      f"(si es 0, revisar el endpoint market=cid)"); break
            time.sleep(POLL)
        except KeyboardInterrupt:
            print("\nparado."); break
        except Exception as ex:
            print("  err:",ex); time.sleep(5)

if __name__=="__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
