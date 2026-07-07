"""
Backfill de ganadores en maker_paper_log.csv.

La resolución EN VIVO del bot reintenta solo +180s tras el cierre; si Polymarket tarda más,
el ganador se pierde. Este script resuelve A POSTERIORI por Binance: winner = Up si el cierre
de la ventana (spot en ws+wlen) es mayor que la apertura (spot en ws), si no Down. Es la misma
referencia que usa el bot para el spike, y el mercado sigue a Binance <5s (ver memoria).

Rellena `winner` (y `won` en los fills) de todas las filas posteadas que aún no lo tengan.
Idempotente — se puede correr cuando sea, cuantas veces sea:
    python resolve_log.py [ruta_csv]
Flujo típico en la Pi:  python resolve_log.py && python analyze_paper.py
"""
import urllib.request, json, time, csv, os, sys
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

P=os.path.join(os.path.dirname(__file__),"maker_paper_log.csv")
if len(sys.argv)>1: P=sys.argv[1]

def get(url, tries=3):
    for i in range(tries):
        try:
            req=urllib.request.Request(url, headers={"User-Agent":"resolve/1.0"})
            with urllib.request.urlopen(req, timeout=15) as r: return json.load(r)
        except Exception:
            if i==tries-1: return None
            time.sleep(0.5)

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
changed=0; pending=0
for r in rows:
    if not r.get("cheap"):        continue      # solo filas posteadas (tienen lado barato)
    if r.get("winner"):           continue      # ya resuelto
    slug=r.get("slug","")
    if "btc-updown" not in slug:  continue      # solo BTC (se resuelve con Binance)
    try: ws=int(r["ws"])
    except Exception: continue
    we=ws+wlen_of(slug)
    if we > int(time.time())-2:   pending+=1; continue   # ventana aún no cerrada
    o=spot_at(ws); c=spot_at(we)
    if o is None or c is None:     pending+=1; continue
    win="Up" if c>o else "Down"
    r["winner"]=win
    if r.get("status")=="filled":
        r["won"]= "1" if win==r["cheap"] else "0"
    changed+=1
    time.sleep(0.03)

if changed:
    with open(P,"w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(rows)
print(f"resueltas {changed} filas; {pending} pendientes (ventanas recientes/sin datos) -> {os.path.basename(P)}")
