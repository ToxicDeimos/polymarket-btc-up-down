"""
BOT MAKER EN PAPEL (DRY) — mide en VIVO si podemos capturar el edge de ejecución.

Para cada ventana BTC Up/Down (SOLO 5m y 15m — se excluyen 4h/1h/diarios):
  1. En el momento de entrada (te), mide el spike de BTC desde el inicio de la ventana.
  2. Umbrales ADAPTATIVOS a la volatilidad: solo fadea si el spike es pequeño RELATIVO
     al movimiento típico del régimen (no un umbral fijo en $). Así opera en cualquier
     régimen — tranquilo o hiper-volátil — y recoge datos en proporción a la vol.
  3. Identifica el lado BARATO y "postea" un bid a (precio_barato − 2¢) [simulado].
  4. Vigila EN VIVO: si el mercado vende a <= nuestro bid → NOS LLENAMOS (fill real medido).
     Si BTC continúa el spike más de CANCEL_K× el movimiento típico → CANCELAMOS.
  5. A la resolución: ¿el fill ganó o perdió? → mide la selección adversa que sufrimos.

Se loguea spike, typ_move y spike_max por ventana → se puede RE-FILTRAR en análisis por el
ratio |spike|/típico sin importar el umbral en vivo. Estrategia: ser generoso en vivo, cortar
fino en el análisis. No necesita claves (es papel). Correr 24/7:
    python maker_paper.py
Autónomo (urllib, stdlib). Analiza el log con analyze_paper.py cuando tengas ~50-100 fills.
"""
import urllib.request, json, time, csv, os, sys, math

MARKET     = "both"    # "15m" | "5m" | "both" — los ganadores operan ambos mercados
ENTRY      = {"5m": 195, "15m": 315}   # s: entrada POR MERCADO (mediana de los ganadores)
BID_OFFSET = 0.02      # postear 2¢ por debajo del precio del lado barato
SPIKE_K    = 0.6       # fadear solo si |spike| < 0.6 × movimiento típico del régimen (en te s)
CANCEL_K   = 0.5       # cancelar si BTC continúa > 0.5 × movimiento típico
SPIKE_FLOOR = 8        # suelo $ del umbral de spike (régimen calmado ≈ config original)
CANCEL_FLOOR = 5       # suelo $ del umbral de cancelación
POLL       = 4         # s entre sondeos
LOG = os.path.join(os.path.dirname(__file__), "maker_paper_log.csv")
HEADER = ["ws","slug","spike","typ_move","spike_max","cheap","cheap_price","bid",
          "status","fill_price","winner","won"]
def entry_of(wlen): return ENTRY["15m"] if wlen>=900 else ENTRY["5m"]

def get(url, tries=2):
    for i in range(tries):
        try:
            req=urllib.request.Request(url, headers={"User-Agent":"maker-paper/1.0"})
            with urllib.request.urlopen(req, timeout=15) as r: return json.load(r)
        except Exception:
            if i==tries-1: return None
            time.sleep(0.5)

def now(): return int(time.time())
def spot():
    d=get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT")
    return float(d["price"]) if d else None
def spot_at(ts):
    k=get(f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1s&startTime={ts*1000}&endTime={(ts+2)*1000}&limit=1")
    return float(k[0][4]) if k else None

_volc={"s1m":None}
def typ_move(te):
    """Movimiento típico de BTC en `te` segundos ($): std close-to-close de 1min sobre la
    última hora, escalada por sqrt(te/60). Cachea el último valor bueno por si falla la API."""
    k=get("https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=60")
    if isinstance(k,list) and len(k)>=10:
        c=[float(x[4]) for x in k]
        d=[c[i]-c[i-1] for i in range(1,len(c))]
        m=sum(d)/len(d)
        _volc["s1m"]=(sum((x-m)**2 for x in d)/len(d))**0.5
    s1m=_volc["s1m"]
    if s1m is None: return None
    return s1m*math.sqrt(te/60.0)

def book(tok):
    b=get(f"https://clob.polymarket.com/book?token_id={tok}")
    if not b: return None,None
    asks=b.get("asks",[]); bids=b.get("bids",[])
    ba=min((float(a["price"]) for a in asks),default=None)
    bb=max((float(x["price"]) for x in bids),default=None)
    return ba,bb
def market_tokens(cid):
    d=get(f"https://clob.polymarket.com/markets/{cid}")
    if not d: return None
    tk={}
    for t in d.get("tokens",[]): tk[t.get("outcome")]=t.get("token_id")
    return tk if "Up" in tk and "Down" in tk else None
def winner(cid):
    d=get(f"https://clob.polymarket.com/markets/{cid}")
    if not d: return None
    for t in d.get("tokens",[]):
        if t.get("winner") is True or float(t.get("price") or 0)>=0.95: return t.get("outcome")
    return None

def is_target(slug):
    """Solo 5m y 15m — excluir 4h/1h/diarios que también empiezan por 'btc-updown'."""
    if "-5m-"  in slug: return MARKET in ("both","5m")
    if "-15m-" in slug: return MARKET in ("both","15m")
    return False

def current_window(seen):
    feed=get("https://data-api.polymarket.com/trades?limit=60")
    if not isinstance(feed,list): return None
    for t in feed:
        slug=t.get("slug","") or ""
        if not is_target(slug): continue
        cid=t.get("conditionId")
        if cid in seen: continue
        try: ws=int(slug.split("-")[-1])
        except Exception: continue
        wlen=900 if "-15m-" in slug else 300
        te=entry_of(wlen)
        if ws+wlen <= now()+40: continue           # ya casi cerrada
        if now() > ws + te - 5: continue            # ya pasó el momento de postear
        return {"cid":cid,"ws":ws,"wlen":wlen,"slug":slug,"te":te}
    return None

def ensure_log():
    """Si el log existente tiene otro esquema (versión vieja), lo archiva y empieza limpio.
    Evita mezclar datos pre-fix (otro filtro) con post-fix — contaminaría el veredicto."""
    if os.path.exists(LOG):
        with open(LOG,encoding="utf-8") as f: first=f.readline().strip()
        if first!=",".join(HEADER):
            bak=LOG.replace(".csv",f"_old_{int(time.time())}.csv")
            os.rename(LOG,bak)
            print(f"log con esquema viejo archivado -> {os.path.basename(bak)}")

def log(row):
    new=not os.path.exists(LOG)
    with open(LOG,"a",newline="",encoding="utf-8") as f:
        w=csv.writer(f)
        if new: w.writerow(HEADER)
        w.writerow(row)

def run_window(win):
    cid,ws,wlen,slug,te=win["cid"],win["ws"],win["wlen"],win["slug"],win["te"]
    tk=market_tokens(cid)
    if not tk: return
    print(f"\n── {slug} (ventana {wlen//60}m) — esperando a los {te}s")
    while now() < ws+te: time.sleep(2)
    o=spot_at(ws); e=spot()
    if o is None or e is None: return
    spike=e-o
    typ=typ_move(te)
    if typ is None:
        print("   skip: sin vol (API)"); log([ws,slug,round(spike,1),"","","","","","skip_novol","","",""]); return
    spike_max  = max(SPIKE_FLOOR,  SPIKE_K*typ)
    cancel_thr = max(CANCEL_FLOOR, CANCEL_K*typ)
    if abs(spike)>spike_max:
        print(f"   skip: spike ${spike:+.0f} > umbral ${spike_max:.0f} (típico ${typ:.0f})")
        log([ws,slug,round(spike,1),round(typ,1),round(spike_max,1),"","","","skip_spike","","",""]); return
    ua,ub=book(tk["Up"]); da,db=book(tk["Down"])
    if None in (ua,da): return
    cheap = "Up" if ua<da else "Down"
    cprice = ua if cheap=="Up" else da            # ask del lado barato (referencia)
    bid = round(cprice-BID_OFFSET,3)
    if not (0.15<=bid<=0.48):
        print(f"   skip: bid {bid} fuera de rango")
        log([ws,slug,round(spike,1),round(typ,1),round(spike_max,1),cheap,cprice,bid,"skip_price","","",""]); return
    print(f"   POST bid {bid} en {cheap} (ask {cprice}, spike ${spike:+.0f}, típico ${typ:.0f}, cancel>${cancel_thr:.0f})")
    seen=set(); filled=False; status="no_fill"
    while now() < ws+wlen-5:
        # cancelar por continuación de BTC (relativa a la vol)
        s=spot()
        if s is not None:
            cont=(s-e) if spike>0 else (e-s)
            if cont>cancel_thr:
                status="cancelled"; print(f"   CANCEL: BTC continúa +${cont:.0f} (>${cancel_thr:.0f})"); break
        # detectar fill: alguien vende el lado barato a <= nuestro bid
        tr=get(f"https://data-api.polymarket.com/trades?market={cid}&limit=100")
        if isinstance(tr,list):
            for t in tr:
                h=t.get("transactionHash","")+str(t.get("timestamp"))
                if h in seen: continue
                seen.add(h)
                if t.get("outcome")==cheap and float(t.get("price") or 1)<=bid and int(t.get("timestamp") or 0)>=ws+te:
                    filled=True; status="filled"; print(f"   FILL @ {bid}"); break
        if filled: break
        time.sleep(POLL)
    # resolver: esperar al cierre y reintentar hasta que resuelva (o +180s)
    while now() < ws+wlen+5: time.sleep(5)
    win_side=None
    while now() < ws+wlen+180 and win_side is None:
        win_side=winner(cid)
        if win_side is None: time.sleep(15)
    # won: 1 gana / 0 pierde / "" no aplica (no llenado) o SIN resolver (no contar perdido)
    if   not filled:        won=""
    elif win_side is None:  won=""
    elif win_side==cheap:   won=1
    else:                   won=0
    print(f"   -> {status} | winner {win_side} | won {won}")
    log([ws,slug,round(spike,1),round(typ,1),round(spike_max,1),cheap,cprice,bid,
         status,bid if filled else "",win_side or "",won])

def main():
    print("="*60+"\n  MAKER PAPER BOT (DRY) — fills reales + selección adversa\n"+"="*60)
    ensure_log()
    seen=set()
    while True:
        try:
            w=current_window(seen)
            if w:
                seen.add(w["cid"]); run_window(w)
                if len(seen)>500: seen=set(list(seen)[-200:])
            else:
                time.sleep(15)
        except KeyboardInterrupt:
            print("\nparado."); break
        except Exception as ex:
            print("  err:",ex); time.sleep(10)

if __name__=="__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
