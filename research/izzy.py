"""
izzy.py — ¿QUE HACE EXACTAMENTE EL UNICO GANADOR QUE SIGUE VIVO?

siguen.py dejo esto: de los 6 ganadores fichados en agosto, 5 se fueron y izzyaussie no solo sigue
sino que MEJORA fuera de muestra (jul −4,88 → ago +6,12 → sep +8,12 → oct +11,84 pp/accion). Como
los fichamos a primeros de agosto, septiembre y octubre son fuera de muestra puro.

Aqui se desmonta COMO lo hace, con la descomposicion que ya le hicimos a 13mm-wrench (cuyo edge
resulto estar ENTERO en pagar ~4¢ bajo el ask, es decir ejecucion, no criterio):

    pnl = s·(gano − p)  =  s·(gano − q)   +   s·(q − p)
                           ─────────────     ───────────
                             MOMENTO           PRECIO
                        ¿era buen momento  ¿pago MENOS que
                        para comprar ese   el resto en ESE
                        token?             MISMO instante?

🐛 PRIMERA VERSION MAL (4-oct): q era el VWAP de TODA la ventana. El precio se mueve mucho en 300 s,
asi que "comprar por debajo del VWAP" puede ser solo *comprar en un instante en que el precio estaba
bajo*, no conseguir mejor relleno. Daba una mejora de precio de 9,5¢/accion, demasiado grande para
ser real. Si los precios son una martingala, comprar bajo el VWAP no predice nada por si solo.

q CORRECTO = **control emparejado**: lo que pagaron OTROS compradores en el MISMO token dentro de
±VENTANA_REF segundos de su operacion. Mismo mercado, mismo lado, mismo instante, distinto operador
⇒ lo unico que cambia es quien es. Ver [[control-emparejado-no-solo-aleatorio]].

  · si casi todo es EJECUCION → es un maker cobrando el spread: sabemos que no lo alcanzamos
    (prioridad de cola, ver mm-market-making-pivot) y estamos donde estabamos.
  · si hay SELECCION de verdad → elige QUE ventanas operar, y eso es observable sin ser el.

Mide ademas: ida y vuelta (¿revende lo que compra?), en que segundo de la ventana entra, a que
precios, cuantas de las 288 ventanas diarias toca, y el z **agrupando por ventana** — una ventana es
UNA observacion, que es la leccion que nos costo un z falso de +8,4 (observaciones-repetidas-inflan-la-z).

    cd ~/polymarket-btc-up-down/research && python3 izzy.py [--desde AAAA-MM-DD] [--max N]

La cinta de cada ventana se cachea en izzy_cinta.csv (los *.csv de research no van al repo).
"""
import calendar, csv, json, os, sys, time, math, urllib.request, urllib.error
from collections import defaultdict

DIR = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(DIR, "izzy_cinta2.csv")      # v2: la v1 no guardaba la hora de cada operacion
IZZY = "0x94f471f68396ff4a3cab8cb5c47c86274b8b77a2"
PAGINA = 500
PAUSA = 0.12
VENTANA_REF = 10        # +-s para buscar el gemelo; si no hay, se amplia a 30 y si no se descarta


def arg(nombre, pordefecto=None):
    if nombre in sys.argv:
        return sys.argv[sys.argv.index(nombre) + 1]
    return pordefecto


DESDE = arg("--desde", "2026-09-01")
MAXV = int(arg("--max", "0"))        # 0 = todas


def get(url, reintentos=3):
    for i in range(reintentos):
        try:
            r = urllib.request.Request(url, headers={"User-Agent": "research/1.0"})
            with urllib.request.urlopen(r, timeout=40) as f:
                return json.loads(f.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 400:
                return None
            if i == reintentos - 1:
                raise
        except Exception:
            if i == reintentos - 1:
                raise
        time.sleep(1.5 * (i + 1))
    return None


def ts_de(fecha):
    # timegm interpreta la tupla como UTC; mktime la interpretaria como hora local y el corte
    # de mes se desplazaria un par de horas.
    return calendar.timegm(time.strptime(fecha, "%Y-%m-%d"))


def ws_de(slug):
    try:
        return int(slug.rsplit("-", 1)[1])
    except Exception:
        return None


# ───────────────────────────── 1. sus operaciones ─────────────────────────────
def sus_operaciones(desde_ts):
    out, off = [], 0
    while True:
        d = get(f"https://data-api.polymarket.com/trades?user={IZZY}&limit={PAGINA}&offset={off}")
        if not d:
            break
        out += d
        if min(t["timestamp"] for t in d) < desde_ts or len(d) < PAGINA:
            break
        off += PAGINA
        time.sleep(PAUSA)
    return [t for t in out
            if t["timestamp"] >= desde_ts and (t.get("slug") or "").startswith("btc-updown-5m")]


# ───────────────────────────── 2. resoluciones ─────────────────────────────
def resoluciones(slugs):
    gan, slugs = {}, sorted(slugs)
    for i in range(0, len(slugs), 100):
        u = ("https://gamma-api.polymarket.com/markets?closed=true&limit=100&"
             + "&".join(f"slug={s}" for s in slugs[i:i + 100]))
        for m in (get(u) or []):
            try:
                gan[m["slug"]] = 0 if float(json.loads(m["outcomePrices"])[0]) > 0.5 else 1
            except Exception:
                pass
        sys.stdout.write(f"\r  resoluciones {min(i+100, len(slugs))}/{len(slugs)}"); sys.stdout.flush()
        time.sleep(PAUSA)
    print()
    return gan


# ───────────────────────────── 3. cinta de cada ventana ─────────────────────────────
def cinta(cids):
    """conditionId -> [(ts, asset, side, price, size, wallet)], cacheada en disco."""
    cache, vistos = defaultdict(list), set()
    if os.path.exists(CACHE):
        for r in csv.reader(open(CACHE, encoding="utf-8", errors="replace")):
            if len(r) == 7:
                vistos.add(r[0])
                cache[r[0]].append((int(r[1]), r[2], r[3], float(r[4]), float(r[5]), r[6]))
    faltan = [c for c in cids if c not in vistos]
    print(f"  cinta: {len(cids)-len(faltan)} en cache, {len(faltan)} por descargar")
    if faltan:
        f = open(CACHE, "a", newline="", encoding="utf-8")
        w = csv.writer(f)
        for i, cid in enumerate(faltan, 1):
            filas, off = [], 0
            while True:
                d = get(f"https://data-api.polymarket.com/trades?market={cid}"
                        f"&limit={PAGINA}&offset={off}")
                if not d:
                    break
                filas += d
                if len(d) < PAGINA:
                    break
                off += PAGINA
                time.sleep(PAUSA)
            for t in filas:
                fila = (cid, t["timestamp"], t["asset"], t["side"], t["price"], t["size"],
                        (t.get("proxyWallet") or "").lower())
                cache[cid].append((int(fila[1]), fila[2], fila[3], float(fila[4]),
                                   float(fila[5]), fila[6]))
                w.writerow(fila)
            # marcador para no volver a pedir un mercado que de verdad no tiene operaciones
            if not filas:
                w.writerow((cid, 0, "", "", 0, 0, ""))
            f.flush()
            if i % 10 == 0 or i == len(faltan):
                sys.stdout.write(f"\r  descargando cinta {i}/{len(faltan)}"); sys.stdout.flush()
            time.sleep(PAUSA)
        f.close(); print()
    return cache


def pct(v, q):
    if not v: return float("nan")
    v = sorted(v); return v[min(len(v) - 1, int(q * len(v)))]


def main():
    desde = ts_de(DESDE)
    print(f"Operaciones de izzyaussie desde {DESDE}…")
    ops = sus_operaciones(desde)
    print(f"  {len(ops)} operaciones en btc-updown-5m")
    if not ops:
        print("sin datos"); return

    porv = defaultdict(list)
    for t in ops:
        porv[t["slug"]].append(t)
    slugs = sorted(porv, key=lambda s: ws_de(s) or 0)
    if MAXV:
        slugs = slugs[-MAXV:]
        porv = {s: porv[s] for s in slugs}
        ops = [t for s in slugs for t in porv[s]]
    print(f"  {len(slugs)} ventanas distintas")

    print(f"\nResolviendo {len(slugs)} mercados…")
    gan = resoluciones(set(slugs))
    print(f"  resueltos {len(gan)}")

    print(f"\nCinta completa de cada ventana…")
    cids = sorted({t["conditionId"] for t in ops})
    tape = cinta(cids)

    # ── gemelos: operaciones de OTROS en el mismo token y el mismo lado, ordenadas por hora ──
    otros = defaultdict(list)
    for cid, filas in tape.items():
        for ts, asset, side, p, s, wal in filas:
            if wal == IZZY or not asset:
                continue
            otros[(cid, asset, side)].append((ts, p, s))
    for k in otros:
        otros[k].sort()

    def gemelo(cid, asset, side, t):
        """Precio medio ponderado que pagaron OTROS en ese token/lado cerca de ese instante."""
        lista = otros.get((cid, asset, side))
        if not lista:
            return None, 0
        for radio in (VENTANA_REF, 30):
            ps = sz = 0.0
            for ts, p, s in lista:
                if abs(ts - t) <= radio:
                    ps += p * s; sz += s
            if sz > 0:
                return ps / sz, radio
        return None, 0

    # ── descomposicion con control emparejado ──
    tot = mom = pre = shares = 0.0
    sh_con = 0.0                                # acciones que SI tienen gemelo
    singem = 0
    porvent = defaultdict(float)                # una ventana = una observacion
    segundos, precios, mejoras = [], [], []
    comprado = defaultdict(float); vendido = defaultdict(float)
    for t in ops:
        g = gan.get(t["slug"])
        if g is None: continue
        p, s = float(t["price"]), float(t["size"])
        d = 1.0 if t["side"] == "BUY" else -1.0
        gano = 1.0 if int(t["outcomeIndex"]) == g else 0.0
        pnl = d * s * (gano - p)
        tot += pnl; shares += s
        porvent[t["slug"]] += pnl
        ws = ws_de(t["slug"])
        if ws: segundos.append(t["timestamp"] - ws)
        precios.append(p)
        (comprado if d > 0 else vendido)[(t["slug"], t["asset"])] += s
        qq, _ = gemelo(t["conditionId"], t["asset"], t["side"], t["timestamp"])
        if qq is None:
            singem += 1; continue
        sh_con += s
        mom += d * s * (gano - qq)              # lo que habria ganado el gemelo
        pre += d * s * (qq - p)                 # lo que gana por pagar menos que el gemelo
        mejoras.append(d * (qq - p))

    print("\n" + "=" * 78)
    print(f"  IZZYAUSSIE desde {DESDE} — {len(ops)} ops · {len(porvent)} ventanas · {shares:,.0f} acciones")
    print("=" * 78)
    print(f"\n  P&L total                 {tot:>12,.2f} $   ({100*tot/shares:+.2f} pp/accion)")
    print(f"\n  ── DE DONDE SALE (control emparejado: otro comprador, mismo token, mismo instante) ──")
    print(f"  ops con gemelo a ±{VENTANA_REF}-30 s: {len(ops)-singem} de {len(ops)}"
          f"  ({sh_con:,.0f} acciones de {shares:,.0f})")
    if sh_con:
        print(f"  MOMENTO (lo que gano el gemelo){mom:>11,.2f} $   "
              f"({100*mom/sh_con:+.2f} pp/accion)")
        print(f"  PRECIO  (pago menos que el)   {pre:>11,.2f} $   "
              f"({100*pre/sh_con:+.2f} pp/accion)")
        mejoras.sort()
        print(f"  mejora de precio por accion: mediana {100*mejoras[len(mejoras)//2]:+.2f} pp"
              f" · p25 {100*pct(mejoras,.25):+.2f} · p75 {100*pct(mejoras,.75):+.2f}")
        print(f"  ⇒ si el MOMENTO es ~0 o negativo y el PRECIO carga todo, su edge es un relleno")
        print(f"    mejor que el del resto: ejecucion de maker, no criterio.")

    # ── ida y vuelta ──
    cv = sum(min(comprado[k], vendido.get(k, 0.0)) for k in comprado)
    tc = sum(comprado.values()); tv = sum(vendido.values())
    print(f"\n  ── IDA Y VUELTA ──")
    print(f"  compradas {tc:>10,.0f}   vendidas {tv:>10,.0f}   casadas en la misma ventana {cv:>10,.0f}"
          f"  ({100*cv/tc if tc else 0:.1f}% de lo comprado)")

    # ── significancia: UNA VENTANA = UNA OBSERVACION ──
    v = list(porvent.values()); n = len(v)
    m = sum(v) / n
    sd = (sum((x - m) ** 2 for x in v) / (n - 1)) ** 0.5 if n > 1 else float("nan")
    z = m / (sd / math.sqrt(n)) if sd else float("nan")
    print(f"\n  ── ¿ES REAL? (una ventana = una observacion) ──")
    print(f"  {n} ventanas · media {m:+.3f} $/ventana · sd {sd:.2f} · **z = {z:+.2f}**")
    print(f"  ventanas ganadoras {100*sum(1 for x in v if x > 0)/n:.0f}%"
          f" · mediana {sorted(v)[n//2]:+.2f} $"
          f" · peor {min(v):+.2f} · mejor {max(v):+.2f}")
    sv = sorted(v, reverse=True)
    sin1 = sum(sv[max(1, n // 100):])
    print(f"  quitando el 1% mejor de ventanas: {sin1:+,.2f} $ "
          f"({'SIGUE POSITIVO' if sin1 > 0 else 'SE CAE'})")

    # ── cuando y a que precio ──
    print(f"\n  ── CUANDO ENTRA (segundo de la ventana de 300 s) ──")
    print(f"  p10 {pct(segundos,.10):.0f}s · p25 {pct(segundos,.25):.0f}s · mediana "
          f"{pct(segundos,.50):.0f}s · p75 {pct(segundos,.75):.0f}s · p90 {pct(segundos,.90):.0f}s")
    print(f"\n  ── A QUE PRECIO ──")
    print(f"  p10 {pct(precios,.10):.3f} · p25 {pct(precios,.25):.3f} · mediana "
          f"{pct(precios,.50):.3f} · p75 {pct(precios,.75):.3f} · p90 {pct(precios,.90):.3f}")

    # ── selecciona o va a todas ──
    dias = {time.strftime("%Y-%m-%d", time.gmtime(ws_de(s))) for s in porvent}
    print(f"\n  ── ¿SELECCIONA? ──")
    print(f"  {len(porvent)} ventanas en {len(dias)} dias = {len(porvent)/len(dias):.0f}/dia "
          f"de las 288 disponibles ({100*len(porvent)/len(dias)/288:.0f}%)")
    print("=" * 78)


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
