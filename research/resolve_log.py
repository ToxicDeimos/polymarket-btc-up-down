"""
Backfill de ganadores en maker_paper_log.csv, con la resolución REAL de Polymarket.

Para cada fila posteada sin resolver:
  - si tiene conditionId (cid): consulta el ganador REAL en el CLOB de Polymarket (flag
    `winner`). Es la verdad. Si aún no ha resuelto pero la ventana cerró hace >20min, cae al
    proxy de Binance para no quedarse colgada.
  - si NO tiene cid (filas anteriores al logueo de cid): proxy de Binance, validado 15/15
    contra la resolución real en la prueba (winner=Up si spot(ws+wlen) > spot(ws)).

Idempotente — se puede correr cuando sea, cuantas veces sea:
    python resolve_log.py [ruta_csv]
Flujo típico en la Pi:  python resolve_log.py && python analyze_paper.py
"""
import urllib.request, json, time, csv, os, sys
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

P=os.path.join(os.path.dirname(__file__),"maker_paper_log.csv")
if len(sys.argv)>1: P=sys.argv[1]
FALLBACK_AFTER=1200   # s tras el cierre sin resolución real → usar Binance

def get(url, tries=3):
    for i in range(tries):
        try:
            req=urllib.request.Request(url, headers={"User-Agent":"resolve/1.0"})
            with urllib.request.urlopen(req, timeout=15) as r: return json.load(r)
        except Exception:
            if i==tries-1: return None
            time.sleep(0.5)

def clob_winner(cid):
    """Ganador REAL de Polymarket: flag `winner` del token en el CLOB."""
    if not cid: return None
    d=get(f"https://clob.polymarket.com/markets/{cid}")
    if not d: return None
    for t in d.get("tokens",[]):
        if t.get("winner") is True: return t.get("outcome")
    return None

_cache={}
def spot_at(ts):
    if ts in _cache: return _cache[ts]
    k=get(f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1s&startTime={ts*1000}&endTime={(ts+2)*1000}&limit=1")
    _cache[ts]=float(k[0][4]) if k else None
    return _cache[ts]

def wlen_of(slug): return 900 if "-15m-" in slug else 300

rows=list(csv.DictReader(open(P,encoding="utf-8")))
if not rows:
    print("log vacío"); sys.exit()
fields=list(rows[0].keys())
now=int(time.time())
real=binance=pending=0
for r in rows:
    if not r.get("cheap"):        continue      # solo filas posteadas (tienen lado barato)
    if r.get("winner"):           continue      # ya resuelto
    slug=r.get("slug","")
    if "btc-updown" not in slug:  continue      # solo BTC
    try: ws=int(r["ws"])
    except Exception: continue
    we=ws+wlen_of(slug)
    if we > now-2: pending+=1; continue          # aún no cerrada

    win=None; src=None
    cid=(r.get("cid") or "").strip()
    if cid:
        win=clob_winner(cid)                     # REAL Polymarket
        if win: src="real"
    if win is None and we < now-FALLBACK_AFTER:  # sin real y cerró hace rato → Binance
        o=spot_at(ws); c=spot_at(we)
        if o and c:
            win="Up" if c>o else "Down"; src="binance"
    if win is None: pending+=1; continue

    r["winner"]=win
    if r.get("status")=="filled":
        r["won"]="1" if win==r["cheap"] else "0"
    if src=="real": real+=1
    else: binance+=1
    time.sleep(0.05)

if real or binance:
    with open(P,"w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(rows)
print(f"resueltas: {real} REAL(Polymarket) + {binance} Binance(respaldo); "
      f"{pending} pendientes (recientes/sin datos) -> {os.path.basename(P)}")
