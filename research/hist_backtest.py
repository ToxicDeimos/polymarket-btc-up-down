"""
EXPERIMENTO #5 — BACKTEST HISTÓRICO MASIVO de la señal momentum sobre AÑOS de BTC.

EL DESBLOQUEO: el RESULTADO de cada ventana 5m (¿ganó el líder?) es PRECIO PURO de BTC.
Polymarket solo aporta el precio que pagas. Así que la señal se puede reconstruir sobre años de
velas de Binance: ~105.000 ventanas/año frente a las 98 del paper bot. Eso permite responder lo
que con 8 días era imposible:
  1. ¿El acierto de la señal supera el ~65% que pagamos?
  2. ¿Cómo varía por RÉGIMEN? → el filtro de régimen que con 8 días no se podía construir.

REGLA reconstruida (idéntica al bot, ver momentum_paper.py):
  ventana 5m alineada (ws % 300 == 0); velas de 1m dan EXACTOS los 3 puntos que hacen falta:
    o = precio en ws · e = precio en ws+240 (entrada) · c = precio en ws+300 (cierre)
  move = e−o · líder = Up si move>0 · GANA Up si c >= o (regla de empate de Polymarket: tie → Up)

BANDA en BPS, no en $: el $8-45 del bot vale a BTC≈$118k (~0,68-3,81 bps), pero BTC ha estado a
$16k. Sobre años hay que normalizar o el filtro no significa lo mismo.

RESOLUCIÓN por Binance: LEGÍTIMA aquí. Nuestra regla permanente prohíbe Binance para veredictos
POR-TRADE, pero la valida para análisis MASIVOS (92% de coincidencia con Chainlink, sin sesgo
direccional). Con cientos de miles de ventanas ese 8% de ruido no mueve el mapa de regímenes.

SIN LOOKAHEAD: los indicadores de régimen de cada ventana se calculan SOLO con ventanas ANTERIORES.

    python hist_backtest.py [días]      # por defecto 365; cachea las velas en lab/
Autónomo (stdlib).
"""
import urllib.request, json, time, csv, os, sys, math

DIR   = os.path.join(os.path.dirname(__file__), "lab")
CACHE = os.path.join(DIR, "klines_1m_btc.csv")

BREAKEVEN = 0.65        # SOLO fallback si no hay log del bot para el mapa empírico move→ask
LOG       = os.path.join(os.path.dirname(__file__), "momentum_paper_log.csv")
REF_PRICE = 118000.0    # BTC de referencia para traducir el $8-45 del bot a bps
BAND_LO   = 8.0  / REF_PRICE * 10000     # ≈ 0,68 bps
BAND_HI   = 45.0 / REF_PRICE * 10000     # ≈ 3,81 bps
TRAIL     = 288         # ventanas 5m de historia para el régimen (288 = 24 h)
REGIME_EVERY = 12       # recalcular régimen cada 12 ventanas (1 h): es variable lenta

def get(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "histbt/1.0"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except Exception:
            if i == tries - 1:
                return None
            time.sleep(1.0 + i)

# ── velas 1m con caché en disco ────────────────────────────────────────────────────────────────
def load_cache():
    d = {}
    if os.path.exists(CACHE):
        with open(CACHE, encoding="utf-8") as f:
            for ln in f:
                a = ln.split(",")
                try: d[int(a[0])] = float(a[1])
                except Exception: pass
    return d

def save_cache(d):
    os.makedirs(DIR, exist_ok=True)
    with open(CACHE, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for ts in sorted(d):
            w.writerow([ts, d[ts]])

def fetch(t0, t1, cache):
    """Descarga velas 1m que falten en [t0,t1). Salta bloques ya cacheados."""
    cur, got, calls = t0, 0, 0
    while cur < t1:
        # bloque de 1000 min ya cubierto? (comprueba extremos y centro)
        blk_end = min(cur + 1000 * 60, t1)
        if cur in cache and (blk_end - 60) in cache and (cur + 500 * 60) in cache:
            cur = blk_end; continue
        d = get(f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m"
                f"&startTime={cur*1000}&endTime={t1*1000}&limit=1000")
        calls += 1
        if not d:
            print(f"   (fallo de descarga en {cur}; sigo con lo que hay)"); break
        for k in d:
            cache[int(k[0]) // 1000] = float(k[1])      # openTime -> open
        got += len(d)
        last = int(d[-1][0]) // 1000
        if last <= cur: break
        cur = last + 60
        if calls % 50 == 0:
            print(f"   ... {got} velas ({time.strftime('%Y-%m-%d', time.gmtime(cur))})")
        time.sleep(0.12)
    return got

# ── mapa empírico move→ask desde NUESTRO log = el precio REAL que cobra el mercado ─────────────
# CRÍTICO: el ask NO es fijo, SUBE con el move. Comparar el hit rate contra un 65% constante
# fabrica "edge" de la nada (un move de >10 bps acierta 99% ¡pero el mercado lo cobra a ~97¢!).
# El break-even de cada bucket es su propio ask, y lo sacamos de las 1393 ventanas que ya logueamos.
ASK_EDGES = [(0, 0.34), (0.34, 0.68), (0.68, 1.5), (1.5, 2.5),
             (2.5, 3.81), (3.81, 6), (6, 10), (10, 1e9)]      # bps
def load_ask_map():
    if not os.path.exists(LOG): return None
    obs = [[] for _ in ASK_EDGES]
    try:
        for r in csv.DictReader(open(LOG, encoding="utf-8")):
            try: mv = abs(float(r["move"])); ak = float(r["ask"])
            except Exception: continue
            if not (0 < ak < 1): continue
            bps = mv / REF_PRICE * 10000
            for i, (lo, hi) in enumerate(ASK_EDGES):
                if lo <= bps < hi: obs[i].append(ak); break
    except Exception: return None
    m = [(lo, hi, (sorted(o)[len(o)//2] if o else None), len(o))
         for (lo, hi), o in zip(ASK_EDGES, obs)]
    return m if any(x[2] is not None for x in m) else None

def ask_for(bps, amap):
    if not amap: return BREAKEVEN
    for lo, hi, med, _ in amap:
        if lo <= bps < hi and med is not None: return med
    return BREAKEVEN

# ── estadística ────────────────────────────────────────────────────────────────────────────────
def hit(rs):
    return (sum(r["won"] for r in rs) / len(rs)) if rs else None

def line(label, rs):
    """hit rate vs el ASK EMPÍRICO de esas mismas ventanas (no vs una constante)."""
    if not rs:
        print(f"  {label:>26}  (sin datos)"); return
    n = len(rs); p = hit(rs); se = math.sqrt(p * (1 - p) / n)
    lo, hi = max(0, p - 1.96 * se), min(1, p + 1.96 * se)
    a = sum(r["ask_est"] for r in rs) / n
    flag = "EDGE" if lo > a else ("+" if p > a else "")
    print(f"  {label:>26}  n={n:>7}  hit {p:6.2%} (IC {lo:.2%}-{hi:.2%})  ask {a:6.2%}  → {(p-a)*100:+6.2f}pp {flag}")

def autocorr(xs):
    n = len(xs)
    if n < 3: return None
    m = sum(xs) / n
    den = sum((x - m) ** 2 for x in xs)
    if not den: return None
    return sum((xs[i] - m) * (xs[i + 1] - m) for i in range(n - 1)) / den

def eff_ratio(xs):
    s = sum(abs(x) for x in xs)
    return abs(sum(xs)) / s if s else None

def buckets(rs, key, labels_edges, title):
    """Reparte rs en buckets por key(r) usando bordes; imprime hit rate de cada uno."""
    print(f"\n  {title}")
    for lo, hi, lab in labels_edges:
        seg = [r for r in rs if key(r) is not None and lo <= key(r) < hi]
        if seg: line(lab, seg)

def quintiles(rs, key, title):
    """Buckets por QUINTIL del propio dato (robusto: no hay que adivinar bordes)."""
    vals = sorted(r[key] for r in rs if r.get(key) is not None)
    if len(vals) < 100:
        print(f"\n  {title}: pocos datos"); return
    qs = [vals[int(len(vals) * f)] for f in (0.2, 0.4, 0.6, 0.8)]
    print(f"\n  {title}  (cortes: {', '.join(f'{q:+.4f}' for q in qs)})")
    edges = [(-1e18, qs[0], "Q1 (más bajo)"), (qs[0], qs[1], "Q2"), (qs[1], qs[2], "Q3"),
             (qs[2], qs[3], "Q4"), (qs[3], 1e18, "Q5 (más alto)")]
    for lo, hi, lab in edges:
        seg = [r for r in rs if r.get(key) is not None and lo <= r[key] < hi]
        if seg: line(lab, seg)

# ── main ───────────────────────────────────────────────────────────────────────────────────────
def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 365
    t1 = int(time.time()) // 60 * 60
    t0 = t1 - days * 86400
    print(f"BACKTEST HISTÓRICO — {days} días  ({time.strftime('%Y-%m-%d', time.gmtime(t0))} → "
          f"{time.strftime('%Y-%m-%d', time.gmtime(t1))})")

    cache = load_cache()
    print(f"caché: {len(cache)} velas 1m — descargando lo que falte…")
    got = fetch(t0, t1, cache)
    if got: save_cache(cache)
    print(f"caché tras descarga: {len(cache)} velas 1m ({got} nuevas)")

    # ── mapa empírico move→ask (precio real del mercado por tamaño de move) ───────────────────
    amap = load_ask_map()
    if amap:
        print("\nMAPA EMPÍRICO move→ask (de nuestro momentum_paper_log.csv) = break-even REAL por bucket:")
        for lo, hi, med, n in amap:
            if med is not None:
                print(f"   {lo:5.2f}-{hi if hi<1e8 else float('inf'):>5.2f} bps  (${lo*REF_PRICE/10000:>5.0f}-"
                      f"{hi*REF_PRICE/10000 if hi<1e8 else 9999:>5.0f})  ask mediano {med:.1%}  (n={n})")
    else:
        print(f"\n(!) sin momentum_paper_log.csv — se usa un break-even FIJO de {BREAKEVEN:.0%}; "
              f"los buckets de move grande saldrán con 'edge' FALSO (el mercado los cobra caros).")

    # ── reconstrucción de ventanas ────────────────────────────────────────────────────────────
    W = []
    ws0 = (t0 // 300 + 1) * 300
    for ws in range(ws0, t1 - 300, 300):
        o = cache.get(ws); e = cache.get(ws + 240); c = cache.get(ws + 300)
        if o is None or e is None or c is None or not o: continue
        move_bps = (e - o) / o * 10000
        if move_bps == 0: continue
        winner = "Up" if c >= o else "Down"          # regla Polymarket: empate → Up
        leader = "Up" if move_bps > 0 else "Down"
        W.append({"ws": ws, "o": o, "bps": move_bps, "abps": abs(move_bps),
                  "won": 1 if leader == winner else 0, "ret": (c - o) / o,
                  "ask_est": ask_for(abs(move_bps), amap)})
    if len(W) < 1000:
        print(f"solo {len(W)} ventanas reconstruidas — ¿descarga incompleta?"); return
    print(f"ventanas 5m reconstruidas: {len(W)}")

    # ── régimen SIN LOOKAHEAD (solo ventanas anteriores) ──────────────────────────────────────
    rets = [w["ret"] for w in W]
    ac = er = vol = None
    for i, w in enumerate(W):
        if i < TRAIL:
            w["ac"] = w["er"] = w["vol"] = None; continue
        if (i - TRAIL) % REGIME_EVERY == 0 or ac is None:
            prev = rets[i - TRAIL:i]                 # ESTRICTAMENTE anterior
            ac = autocorr(prev); er = eff_ratio(prev)
            m = sum(prev) / len(prev)
            vol = math.sqrt(sum((x - m) ** 2 for x in prev) / len(prev)) * 10000  # bps
        w["ac"], w["er"], w["vol"] = ac, er, vol

    print("\n" + "=" * 78)
    print("1) HIT RATE por |move| a 240s (bps) — ¿dónde predice el líder?")
    print("=" * 78)
    line("TODAS las ventanas", W)
    buckets(W, lambda r: r["abps"],
            [(0, 0.34, "<0.34 bps (<$4)"), (0.34, 0.68, "0.34-0.68 ($4-8)"),
             (0.68, 1.5, "0.68-1.5 ($8-18)"), (1.5, 2.5, "1.5-2.5 ($18-30)"),
             (2.5, 3.81, "2.5-3.81 ($30-45)"), (3.81, 6, "3.81-6 ($45-71)"),
             (6, 10, "6-10 ($71-118)"), (10, 1e9, ">10 bps (>$118)")],
            "por |move| (equivalencia en $ a BTC≈$118k):")

    B = [w for w in W if BAND_LO <= w["abps"] <= BAND_HI]
    print("\n" + "=" * 78)
    print(f"2) NUESTRA BANDA del bot: {BAND_LO:.2f}-{BAND_HI:.2f} bps  (≈ $8-45 a BTC ${REF_PRICE:,.0f})")
    print("=" * 78)
    line("BANDA (todo el periodo)", B)
    print("\n  por AÑO-MES (¿es estable o vive de una época?):")
    seen = {}
    for w in B:
        k = time.strftime("%Y-%m", time.gmtime(w["ws"])); seen.setdefault(k, []).append(w)
    for k in sorted(seen): line(k, seen[k])

    Bd = [w for w in B if w["ac"] is not None]
    print("\n" + "=" * 78)
    print("3) RÉGIMEN — hit rate DENTRO de la banda según el estado del mercado (sin lookahead)")
    print("=" * 78)
    print("   autocorrelación de retornos 5m = termómetro directo: >0 persiste (tendencia), <0 revierte (chop)")
    quintiles(Bd, "ac",  "por AUTOCORRELACIÓN trailing 24h:")
    quintiles(Bd, "er",  "por EFFICIENCY RATIO trailing 24h (alto = tendencia limpia):")
    quintiles(Bd, "vol", "por VOLATILIDAD trailing 24h (bps):")

    print("\n" + "=" * 78)
    print("4) TRAIN / TEST temporal (60/40) — ¿el régimen generaliza o es sobreajuste?")
    print("=" * 78)
    cut = Bd[int(len(Bd) * 0.6)]["ws"] if Bd else 0
    tr = [w for w in Bd if w["ws"] < cut]; te = [w for w in Bd if w["ws"] >= cut]
    print(f"  corte: {time.strftime('%Y-%m-%d', time.gmtime(cut))}   train {len(tr)} / test {len(te)}")
    for nm, seg in (("TRAIN", tr), ("TEST", te)):
        if not seg: continue
        print(f"\n  ── {nm} ──"); line("banda completa", seg)
        vals = sorted(w["ac"] for w in seg)
        q = vals[int(len(vals) * 0.8)] if vals else 0
        line(f"solo autocorr>Q4({q:+.3f})", [w for w in seg if w["ac"] >= q])
        line(f"solo autocorr<Q4", [w for w in seg if w["ac"] < q])

    print("\n" + "=" * 78)
    print("CÓMO LEER ESTO — y sus DOS SESGOS (importantes)")
    print("=" * 78)
    print("'EDGE' = el IC inferior del hit rate supera el ASK EMPÍRICO de esas ventanas.")
    print()
    print("SESGO 1 — el ask sube con el move. Por eso NO comparamos contra un 65% fijo: un move")
    print("  de >10 bps acierta ~99% pero el mercado lo cobra a ~97¢. Sin esto, 'edge' en todo.")
    print()
    print("SESGO 2 (el gordo) — SELECCIÓN POR ASK. El bot solo entra si el ask cae en 52-72¢, o sea")
    print("  se queda con el subconjunto que el mercado ve MÁS DIFÍCIL dentro de la banda. Prueba:")
    print("  en nuestra sombra, las ventanas descartadas por ask alto ganaron 77.8/90.2/97.9%.")
    print("  Aquí el ask se estima SOLO a partir del move, así que ignora el resto de info que el")
    print("  mercado sí usa → el EV que salga es un TECHO, no lo que ganaríamos. Contraste real:")
    print("  banda histórica ~72% vs brazo A en vivo 64.3% — esa brecha ES este sesgo.")
    print()
    print("POR ESO lo que vale aquí NO es el nivel absoluto, sino la COMPARACIÓN ENTRE RÉGIMENES:")
    print("  si el hit rate de la banda sube y baja con la autocorrelación/ER y AGUANTA EN TEST,")
    print("  ese diferencial es real y es el filtro de régimen que buscamos (el sesgo por ask")
    print("  afecta a todos los regímenes por igual, así que se cancela al comparar entre ellos).")

if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
