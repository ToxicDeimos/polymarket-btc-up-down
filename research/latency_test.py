"""Test de latencia real + velocidad de reprice, buscando un mercado a MITAD de ventana."""
import urllib.request, json, sys, time, statistics, math
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent":"lat/0.2"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)

def book_ask(tok):
    try:
        b = get(f"https://clob.polymarket.com/book?token_id={tok}")
        asks=b.get("asks",[]); bids=b.get("bids",[])
        ba = min(float(a["price"]) for a in asks) if asks else None
        return ba, len(asks), len(bids)
    except Exception:
        return None, 0, 0

def btc_px():
    try: return float(get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT")["price"])
    except Exception: return None

# Buscar un token btc-updown a MITAD de ventana (libro con dos lados, ask 0.2-0.8)
feed = get("https://data-api.polymarket.com/trades?limit=200")
tok = None
tried = set()
for t in feed:
    if "btc-updown" not in (t.get("slug") or ""): continue
    a = t.get("asset")
    if a in tried: continue
    tried.add(a)
    ba, na, nb = book_ask(a)
    if ba and na > 0 and nb > 0 and 0.2 <= ba <= 0.8:
        tok = a; print(f"mercado a mitad de ventana: {t.get('slug')} | bestask={ba} (asks={na} bids={nb})\n"); break
if not tok:
    print("No hay mercado a mitad de ventana ahora mismo; mido solo el suelo de reacción.\n")

def rtt(fn, n=25):
    xs=[]
    for _ in range(n):
        t0=time.perf_counter()
        r=fn()
        if r is not None and r != (None,0,0): xs.append((time.perf_counter()-t0)*1000)
        time.sleep(0.04)
    xs.sort(); return xs

print("="*62)
print("  1) SUELO DE REACCIÓN (RTT ms: mediana / mejor / peor)")
print("="*62)
xb = rtt(btc_px)
if xb: print(f"  Binance leer BTC   : {statistics.median(xb):5.0f}  ({xb[0]:.0f}/{xb[int(len(xb)*.9)]:.0f})")
if tok:
    xo = rtt(lambda: book_ask(tok)[0])
    if xo: print(f"  Polymarket orden   : {statistics.median(xo):5.0f}  ({xo[0]:.0f}/{xo[int(len(xo)*.9)]:.0f})")
    if xb and xo:
        print(f"\n  CAMINO CRÍTICO ≈ leerBTC + firma(3) + enviarOrden ≈ "
              f"{statistics.median(xb)+3+statistics.median(xo):.0f}ms (este PC)")

if tok:
    print("\n" + "="*62)
    print("  2) VELOCIDAD DE REPRICE (sonda 45s en mercado vivo)")
    print("="*62)
    ser=[]; t0=time.perf_counter()
    while time.perf_counter()-t0 < 45:
        tt=time.perf_counter()-t0
        bp=btc_px(); ap,_,_=book_ask(tok)
        if bp and ap: ser.append((tt,bp,ap))
    print(f"  muestras: {len(ser)} en 45s (~{len(ser)/45:.1f}/s, resolución ~{45/max(len(ser),1)*1000:.0f}ms)")
    if len(ser)>15:
        def corr(xs,ys):
            n=len(xs); mx=sum(xs)/n; my=sum(ys)/n
            sx=sum((x-mx)**2 for x in xs); sy=sum((y-my)**2 for y in ys)
            sxy=sum((xs[i]-mx)*(ys[i]-my) for i in range(n))
            return sxy/math.sqrt(sx*sy) if sx>0 and sy>0 else 0
        step=45/len(ser)
        db=[ser[i][1]-ser[i-1][1] for i in range(1,len(ser))]
        da=[ser[i][2]-ser[i-1][2] for i in range(1,len(ser))]
        nz=sum(1 for x in da if abs(x)>1e-9)
        print(f"  el ask cambió en {nz}/{len(da)} pasos")
        for k in range(0,5):
            if len(db)-k<8: break
            print(f"    retraso ~{k*step*1000:4.0f}ms: corr(Δbtc,Δask) {corr(db[:len(db)-k],da[k:]):+.2f}")
