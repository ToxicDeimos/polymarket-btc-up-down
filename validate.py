"""
Validación de ineficiencias del mercado BTC Up/Down 15m.

Uso:
    python validate.py                      # lee ./results.csv y ./prices.csv
    python validate.py C:\\ruta\\a\\datos     # lee los CSV de otra carpeta (copia de la Pi)

Tres bloques:
  1) LAG        — ¿el ask de Polymarket sigue al spot de Binance con retraso?
                  (corr Δspot vs Δask por lag). VERDICTO previo: lag ≈ 0 → mercado eficiente.
  2) PRECIO     — ¿comprar al precio de mercado es +EV en algún rango/momento?
                  (sesgo favorito-longshot + convergencia)
  3) RÉGIMEN    — ¿el edge "fade temprano / follow medio" sobrevive en TENDENCIA,
                  o es un artefacto del chop? (la pregunta que decide si es real)

REGLAS PRE-REGISTRADAS (congeladas — NO re-tunear con datos futuros):
  FADE   : en T∈[30,60]s, comprar el lado con ask ∈ [0.35, 0.48]
  FOLLOW : en T∈[420,480]s, comprar el lado con ask ∈ [0.65, 0.85]
  Aprobación: +EV (tras fees) en ≥2 regímenes macro distintos, uno de ellos tendencia
              fuerte (trend_strength>0.35%), con n≥30 e IC del EV > 0.
"""
import csv, math, os, sys
from collections import defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DATA_DIR = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(__file__)
RESULTS = os.path.join(DATA_DIR, "results.csv")
PRICES  = os.path.join(DATA_DIR, "prices.csv")


def _read(p):
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return list(csv.DictReader(f))

def f(v):
    try:
        return float(v)
    except (ValueError, TypeError):
        return None

def _hr(t):
    print("\n" + "=" * 64 + f"\n  {t}\n" + "=" * 64)


# ── prices indexados por ventana ──────────────────────────────────────────────

def _by_cid(prices):
    d = defaultdict(list)
    for r in prices:
        d[r["condition_id"]].append(r)
    return d

def _asks_at(rows, target, tol=60):
    """(up_ask, down_ask) del snapshot más cercano a `target` s (None si > tol)."""
    best, bd = None, 1e9
    for r in rows:
        se = f(r.get("seconds_elapsed"))
        if se is None:
            continue
        if abs(se - target) < bd:
            bd, best = abs(se - target), (f(r.get("up_ask")), f(r.get("down_ask")))
    return best if bd < tol else None


# ── 1) LAG ────────────────────────────────────────────────────────────────────

def lag(prices):
    _hr("1) LAG  (¿el ask sigue al spot, con retraso?)")
    wins = defaultdict(list)
    for r in prices:
        se, sp, ua = f(r.get("seconds_elapsed")), f(r.get("spot_price")), f(r.get("up_ask"))
        if se is not None and sp and ua:
            wins[r["condition_id"]].append((se, sp, ua))
    dts = []
    for s in wins.values():
        s.sort()
        dts += [s[i][0] - s[i-1][0] for i in range(1, len(s))]
    if not dts:
        print("  Sin spot_price en prices.csv (logging aún sin desplegar).")
        return
    step = sorted(dts)[len(dts)//2]
    pairs = defaultdict(lambda: ([], []))
    for s in wins.values():
        s.sort()
        sp = [x[1] for x in s]; ua = [x[2] for x in s]
        dsp = [sp[i]-sp[i-1] for i in range(1, len(sp))]
        dua = [ua[i]-ua[i-1] for i in range(1, len(ua))]
        for k in range(7):
            for i in range(len(dsp)-k):
                pairs[k][0].append(dsp[i]); pairs[k][1].append(dua[i+k])
    def corr(xs, ys):
        n = len(xs)
        if n < 10: return 0
        mx, my = sum(xs)/n, sum(ys)/n
        sx = sum((x-mx)**2 for x in xs); sy = sum((y-my)**2 for y in ys)
        sxy = sum((xs[i]-mx)*(ys[i]-my) for i in range(n))
        return sxy/math.sqrt(sx*sy) if sx > 0 and sy > 0 else 0
    print(f"  paso ~{step:.0f}s | {sum(1 for s in wins.values() if len(s)>5)} ventanas")
    for k in range(7):
        c = corr(*pairs[k])
        print(f"   retraso {int(k*step):>3}s | corr {c:+.3f} {'#'*int(abs(c)*40)}")
    print("  → pico en 0s = mercado reprecia al instante = NO hay lag explotable.")


# ── 2) PRECIO: favorito-longshot / convergencia ───────────────────────────────

def price_edge(results, bycid):
    _hr("2) PRECIO  (¿comprar a precio P es +EV?  EV = gana/P − 1)")
    winner = {r["condition_id"]: r["winner"] for r in results if r["winner"] in ("Up", "Down")}
    for label, T in [("APERTURA ~30s", 30), ("TEMPRANO ~120s", 120),
                     ("MEDIO ~450s", 450), ("TARDE ~800s", 800)]:
        b = defaultdict(lambda: [0, 0])
        for cid, w in winner.items():
            a = _asks_at(bycid.get(cid, []), T)
            if not a or a[0] is None or a[1] is None:
                continue
            for price, side in ((a[0], "Up"), (a[1], "Down")):
                if price is None or price <= 0.05 or price >= 0.95:
                    continue
                bk = round(price*10)/10
                b[bk][1] += 1
                if w == side: b[bk][0] += 1
        print(f"  ── {label} " + "─"*36)
        for bk in sorted(b):
            win, tot = b[bk]
            if tot < 8 or bk <= 0.05:
                continue
            wf = win/tot; ev = wf/bk - 1
            flag = "  <-- +EV" if (ev > 0.05 and tot >= 15) else ("  (caro)" if ev < -0.05 and tot >= 15 else "")
            print(f"     precio {bk:.1f} | gana {wf:4.0%} | n={tot:>4} | EV {ev:+4.0%}{flag}")


# ── 3) RÉGIMEN: ¿sobrevive el fade/follow? ────────────────────────────────────

def _regime_persist(results, N=6):
    """Etiqueta cada ventana por persistencia de los N winners previos."""
    res = [r for r in results if r["winner"] in ("Up", "Down")]
    res.sort(key=lambda r: r["timestamp_utc"])
    for i, r in enumerate(res):
        prev = res[max(0, i-N):i]
        r["_reg"] = None
        if len(prev) == N:
            ups = sum(1 for p in prev if p["winner"] == "Up")
            if ups >= 5 or ups <= 1:   r["_reg"] = "TREND"
            elif 2 <= ups <= 4:        r["_reg"] = "RANGE"
    return res

def _ts_regime(r):
    ts = f(r.get("trend_strength"))
    if ts is None: return None
    return "RANGE" if ts < 0.15 else ("WEAK" if ts < 0.35 else "STRONG")

def regime(results, bycid):
    _hr("3) RÉGIMEN  (¿fade/follow sobrevive fuera del chop?)")
    res = _regime_persist(results)

    def ev_by(T, pick, label_fn):
        cells = defaultdict(lambda: [0, 0, 0.0])
        for r in res:
            lab = label_fn(r)
            if not lab:
                continue
            a = _asks_at(bycid.get(r["condition_id"], []), T)
            if not a or a[0] is None or a[1] is None:
                continue
            up, dn = a
            side, price = (("Up", up) if up >= dn else ("Down", dn)) if pick == "fav" \
                else (("Up", up) if up < dn else ("Down", dn))
            if price <= 0.05 or price >= 0.95:
                continue
            won = (r["winner"] == side)
            cells[lab][0] += won; cells[lab][1] += 1; cells[lab][2] += (1/price if won else 0) - 1
        return cells

    for clf_name, clf in [("régimen por persistencia de winners", lambda r: r.get("_reg")),
                          ("régimen por trend_strength (EMA)",   _ts_regime)]:
        cov = sum(1 for r in res if clf(r))
        print(f"\n  — {clf_name} — ({cov} ventanas etiquetadas)")
        if not cov:
            print("    (sin etiquetas: trend_strength aún sin datos suficientes)")
            continue
        print("    FADE temprano (~30s) — comprar FAVORITO. EV<0 = fadear es +EV:")
        for reg, c in sorted(ev_by(30, "fav", clf).items()):
            if c[1] >= 8:
                print(f"      {reg:7} | favorito gana {c[0]/c[1]:4.0%} | EV {c[2]/c[1]:+5.0%} | n={c[1]}")
        print("    FOLLOW medio (~450s) — comprar FAVORITO. EV>0 = follow OK:")
        for reg, c in sorted(ev_by(450, "fav", clf).items()):
            if c[1] >= 8:
                print(f"      {reg:7} | favorito gana {c[0]/c[1]:4.0%} | EV {c[2]/c[1]:+5.0%} | n={c[1]}")

    print("\n  VEREDICTO: el fade pasa solo si el favorito temprano es -EV también en")
    print("  STRONG trend (no solo en RANGE). Si en tendencia fuerte el favorito gana")
    print("  >precio, el edge es artefacto del chop → descartar (como el lag).")


def main():
    results = _read(RESULTS)
    prices  = _read(PRICES)
    bycid   = _by_cid(prices)
    print(f"\nLeído: {len(results)} results · {len(prices)} prices")
    lag(prices)
    price_edge(results, bycid)
    regime(results, bycid)
    print()


if __name__ == "__main__":
    main()
