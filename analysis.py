"""
Análisis del Brain — calibración, régimen y volatilidad.

Uso:
    python analysis.py                # lee ./results.csv y ./prices.csv
    python analysis.py C:\\ruta\\datos  # lee los CSV de otra carpeta (ej. copia de la Pi)

Responde a tres preguntas:
  1) ¿El Brain está bien calibrado? (lo que predice vs lo que pasa)
  2) ¿La volatilidad que usa es realista? (vol estimada vs vol REALIZADA del BTC)
  3) ¿En qué régimen gana/pierde? (fuerza de tendencia, a favor/contra)

NOTA: las columnas entry_* y cl_price/spot_price solo existen desde que se
desplegó el logging nuevo. Las filas viejas salen como "sin dato".
"""
import csv
import math
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")   # box chars / σ / → en Windows
except Exception:
    pass

DATA_DIR = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(__file__)
RESULTS = os.path.join(DATA_DIR, "results.csv")
PRICES  = os.path.join(DATA_DIR, "prices.csv")


# ── utilidades ────────────────────────────────────────────────────────────────

def _read(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _f(v, default=None):
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def _bet(r):
    """Normaliza una fila de results.csv a una apuesta resuelta, o None."""
    up = r.get("up_filled") == "True"
    dn = r.get("down_filled") == "True"
    if not (up or dn):
        return None
    side = "up" if up else "down"
    winner = r.get("winner", "")
    if winner not in ("Up", "Down"):
        return None
    return {
        "cid":   r.get("condition_id", ""),
        "side":  side,
        "won":   (side == "up") == (winner == "Up"),
        "pnl":   _f(r.get("profit"), 0.0),
        "trend": r.get("trend_dir", ""),
        "strength": _f(r.get("trend_strength")),
        "p_true": _f(r.get("entry_p_true")),
        "edge":   _f(r.get("entry_edge")),
        "etype":  r.get("entry_edge_type", ""),
        "cl_diff":   _f(r.get("entry_cl_diff")),
        "spot_diff": _f(r.get("entry_spot_diff")),
        "secs_elapsed": _f(r.get("entry_secs_elapsed")),
        "secs_left":    _f(r.get("entry_secs_left")),
        "vol":   _f(r.get("entry_vol")),
        "end":   r.get("window_end_et", ""),
    }


def _wr(ops):
    return 100 * sum(o["won"] for o in ops) / len(ops) if ops else 0.0


def _hr(title):
    print("\n" + "=" * 64)
    print("  " + title)
    print("=" * 64)


# ── 1) Resumen P&L ────────────────────────────────────────────────────────────

def resumen(ops):
    _hr("1) RESUMEN")
    if not ops:
        print("  Sin apuestas resueltas.")
        return
    pnl = sum(o["pnl"] for o in ops)
    print(f"  Apuestas resueltas: {len(ops)}")
    print(f"  Win rate global:    {_wr(ops):.0f}%  ({sum(o['won'] for o in ops)}/{len(ops)})")
    print(f"  P&L total:          {pnl:+.2f}")
    for n in (20, 10, 6):
        if len(ops) >= n:
            last = ops[-n:]
            print(f"    últimas {n:2d}: WR {_wr(last):3.0f}% | P&L {sum(o['pnl'] for o in last):+.2f}")


# ── 2) Calibración: lo que predice vs lo que pasa ─────────────────────────────

def calibracion(ops):
    _hr("2) CALIBRACIÓN  (entry_p_true vs win rate real)")
    cal = [o for o in ops if o["p_true"] is not None]
    if not cal:
        print("  Sin apuestas con entry_p_true (logging aún sin desplegar o sin datos).")
        return
    print(f"  Apuestas con predicción registrada: {len(cal)}")
    print(f"  {'bucket P':>12} | {'n':>3} | {'P media':>8} | {'WR real':>8} | gap")
    print("  " + "-" * 52)
    buckets = [(0, .6), (.6, .7), (.7, .8), (.8, .9), (.9, .95), (.95, 1.01)]
    for lo, hi in buckets:
        b = [o for o in cal if lo <= o["p_true"] < hi]
        if not b:
            continue
        pmean = sum(o["p_true"] for o in b) / len(b)
        wr = _wr(b) / 100
        gap = pmean - wr
        flag = "  <-- sobreconfía" if gap > 0.10 and len(b) >= 3 else ""
        print(f"  {lo:.2f}-{hi:>4.2f}  | {len(b):>3} | {pmean:>7.0%} | {wr:>7.0%} | {gap:+.0%}{flag}")
    overall_p = sum(o["p_true"] for o in cal) / len(cal)
    print("  " + "-" * 52)
    print(f"  GLOBAL: predice {overall_p:.0%} de media, gana {_wr(cal):.0f}% real "
          f"→ gap {overall_p - _wr(cal)/100:+.0%}")
    if len(cal) < 20:
        print(f"  (Aún pocas para concluir — objetivo ~20-30 con predicción)")


# ── 3) Régimen: fuerza de tendencia y a favor / contra ────────────────────────

def regimen(ops):
    _hr("3) RÉGIMEN  (¿dónde gana/pierde?)")
    with_s = [o for o in ops if o["strength"] is not None]
    if with_s:
        print("  Por FUERZA de tendencia (trend_strength):")
        for lo, hi in [(0, .10), (.10, .15), (.15, .20), (.20, 99)]:
            b = [o for o in with_s if lo <= o["strength"] < hi]
            if b:
                print(f"    {lo:.2f}-{hi:<5.2f}: {len(b):>2} apuestas | "
                      f"WR {_wr(b):3.0f}% | P&L {sum(o['pnl'] for o in b):+.2f}")
    else:
        print("  Sin trend_strength registrado todavía.")
    # A favor / contra de la tendencia mayor
    aligned = [o for o in ops if o["trend"] in ("up", "down")]
    if aligned:
        fav  = [o for o in aligned if o["side"] == o["trend"]]
        cont = [o for o in aligned if o["side"] != o["trend"]]
        print("\n  A favor vs contra de la tendencia:")
        if fav:
            print(f"    a favor : {len(fav):>2} | WR {_wr(fav):3.0f}% | P&L {sum(o['pnl'] for o in fav):+.2f}")
        if cont:
            print(f"    contra  : {len(cont):>2} | WR {_wr(cont):3.0f}% | P&L {sum(o['pnl'] for o in cont):+.2f}")


# ── 4) Vol realizada (prices.csv) vs vol estimada (entry_vol) ─────────────────

def _realized_sigma(series):
    """
    σ realizada en $/√s a partir de una serie [(secs_elapsed, price), ...].
    Modelo random walk: Var(move en T) = σ²·T  →  σ = sqrt(mean(ΔP²/Δt)).
    Es lo que el Brain DEBERÍA usar en z = diff/(σ·√secs_left).
    """
    pts = [(s, p) for s, p in series if p is not None]
    pts.sort()
    contribs = []
    for i in range(1, len(pts)):
        dt = pts[i][0] - pts[i-1][0]
        dp = pts[i][1] - pts[i-1][1]
        if dt > 0:
            contribs.append(dp * dp / dt)
    if not contribs:
        return None
    return math.sqrt(sum(contribs) / len(contribs))


def vol_check(ops, price_rows):
    _hr("4) VOLATILIDAD  (estimada por el Brain vs REALIZADA)")
    have_btc = any((r.get("cl_price") or r.get("spot_price")) for r in price_rows)
    if not have_btc:
        print("  prices.csv aún no tiene cl_price/spot_price (logging recién añadido).")
        print("  Tras desplegar y acumular ventanas, aquí saldrá:")
        print("    vol_estimada (entry_vol)  vs  σ realizada de Chainlink y Binance.")
        print("  Si σ_realizada >> entry_vol de forma sistemática → el Brain")
        print("  infraestima la vol → z infladas → P sobreconfiadas.")
        return
    # σ realizada por ventana
    by_cid = {}
    for r in price_rows:
        cid = r.get("condition_id", "")
        by_cid.setdefault(cid, []).append(r)
    rows = []
    for o in ops:
        if o["vol"] is None:
            continue
        snaps = by_cid.get(o["cid"], [])
        cl = _realized_sigma([(_f(r.get("seconds_elapsed")), _f(r.get("cl_price"))) for r in snaps])
        sp = _realized_sigma([(_f(r.get("seconds_elapsed")), _f(r.get("spot_price"))) for r in snaps])
        rows.append((o, cl, sp))
    rows = [r for r in rows if r[1] or r[2]]
    if not rows:
        print("  Aún no hay ventanas con precio BTC + apuesta para comparar.")
        return
    print(f"  {'ventana':>8} | {'vol_est':>7} | {'σ_CL':>6} | {'σ_spot':>7} | {'x infra (spot)':>14}")
    print("  " + "-" * 58)
    ratios = []
    for o, cl, sp in rows:
        ratio = (sp / o["vol"]) if (sp and o["vol"]) else None
        if ratio:
            ratios.append(ratio)
        print(f"  {o['end']:>8} | {o['vol']:>7.3f} | "
              f"{(cl if cl else 0):>6.3f} | {(sp if sp else 0):>7.3f} | "
              f"{(f'{ratio:.1f}x' if ratio else '-'):>14}")
    if ratios:
        avg = sum(ratios) / len(ratios)
        print("  " + "-" * 58)
        print(f"  Media: la vol REAL (spot) es {avg:.1f}x la que usa el Brain.")
        if avg > 1.5:
            print(f"  → INFRAESTIMA la vol ~{avg:.1f}x → probabilidades sobreconfiadas.")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    results = _read(RESULTS)
    prices  = _read(PRICES)
    ops = [b for b in (_bet(r) for r in results) if b]
    print(f"\nLeído: {len(results)} filas results · {len(prices)} filas prices · "
          f"{len(ops)} apuestas resueltas")
    resumen(ops)
    calibracion(ops)
    regimen(ops)
    vol_check(ops, prices)
    print()


if __name__ == "__main__":
    main()
