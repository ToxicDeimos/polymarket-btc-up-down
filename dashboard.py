"""
Panel web del bot — corre independiente del bot principal.
Uso: python dashboard.py
Abre: http://localhost:5000
"""
import csv
import math
import os
import statistics
from flask import Flask, jsonify, render_template, redirect

app = Flask(__name__)

BASE = os.path.dirname(__file__)
FAVORITE_FILE = os.path.join(BASE, "research", "favorite_paper_log.csv")


# ── Rutas ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return redirect("/favorite")


def _fav_stats(rows):
    """EDGE del favorito = win − ask (el precio pagado). Sesgo favorito-longshot."""
    n = len(rows)
    if n == 0:
        return None
    wins = sum(1 for r in rows if r.get("won") == "1")
    asks = [float(r["ask"]) for r in rows if r.get("ask")]
    wr = wins / n
    ap = statistics.mean(asks) if asks else 0.0
    se = math.sqrt(wr * (1 - wr) / n)
    # P&L apostando $1 por ventana: si gana cobra 1/ask (profit 1/ask−1), si pierde −1.
    # 'staked' = n dólares (un $1 por fill). pnl_share = P&L comprando 1 acción/ventana (win − ask).
    pnl   = sum((1 / float(r["ask"]) - 1) if r.get("won") == "1" else -1
                for r in rows if r.get("won") in ("0", "1"))
    pshare = sum((1 - float(r["ask"])) if r.get("won") == "1" else -float(r["ask"])
                 for r in rows if r.get("won") in ("0", "1"))
    return {"n": n, "win_rate": round(wr * 100, 1),
            "ci_lo": round(max(0, wr - 1.96 * se) * 100, 1),
            "ci_hi": round(min(1, wr + 1.96 * se) * 100, 1),
            "ask": round(ap * 100, 1), "edge": round((wr - ap) * 100, 2),
            "rel": round((wr - ap) / ap * 100, 1) if ap else 0.0,
            "pnl": round(pnl, 2), "pnl_share": round(pshare, 2), "staked": n}


@app.route("/favorite")
def favorite():
    return render_template("favorite.html")


@app.route("/api/favorite")
def api_favorite():
    rows = _read_csv(FAVORITE_FILE)
    if not rows:
        return jsonify({"summary": {"n": 0}, "trades": []})
    from collections import Counter
    st = Counter(r.get("status", "") for r in rows)
    bought = [r for r in rows if r.get("status") == "bought"]
    B = [r for r in bought if r.get("won") in ("0", "1")]
    pending = len(bought) - len(B)
    fillrate = round(len(bought) / len(rows) * 100, 1) if rows else 0.0
    overall = _fav_stats(B)
    # el bot compra con ask <= 0.95 INCLUSIVE, así que el tramo alto llega a 0.9501 para no dejar
    # fuera los fills en el techo exacto de 0.95 (si no, TODO > 82-90 + 90-95).
    by_band = {lab: _fav_stats([r for r in B if lo <= float(r["ask"]) < hi])
               for lo, hi, lab in [(0.82, 0.90, "82-90c"), (0.90, 0.9501, "90-95c")]}
    S = [r for r in rows if r.get("status") == "skip" and r.get("won") in ("0", "1")]
    shadow = {lab: _fav_stats([r for r in S if lo <= float(r["ask"]) < hi])
              for lo, hi, lab in [(0.62, 0.72, "62-72c"), (0.72, 0.82, "72-82c"), (0.95, 1.01, "95-99c")]}

    MIN_VERDICT = 400
    if overall is None or overall["n"] < MIN_VERDICT:
        verdict = ("wait", f"{overall['n'] if overall else 0}/{MIN_VERDICT} fills — sin veredicto "
                   f"(margen fino +0.84pp a ~89¢ = alta varianza → exige n grande).")
    elif overall["edge"] > 0:
        if overall["ci_lo"] > overall["ask"]:
            verdict = ("real", f"win {overall['win_rate']}% > ask {overall['ask']}% SIGNIFICATIVO "
                       f"(n={overall['n']}, EDGE {overall['edge']:+}pp) → el sesgo favorito-longshot es NUESTRO. "
                       f"Primer edge real y confirmado del proyecto.")
        else:
            verdict = ("maybe", f"win {overall['win_rate']}% > ask {overall['ask']}% pero no significativo "
                       f"(n={overall['n']}, IC {overall['ci_lo']}-{overall['ci_hi']}%). Seguir acumulando.")
    else:
        verdict = ("dead", f"win {overall['win_rate']}% ≤ ask {overall['ask']}% con n≥{MIN_VERDICT}: el "
                   f"favorito no bate su precio en vivo. Documentar y cerrar.")

    # Curva de equity ILUSTRATIVA: $1 plano vs ¼-Kelly compuesto, sobre los MISMOS fills en orden.
    # Kelly dimensiona con el edge ROBUSTO del backtest (+0.84pp), no el ruidoso en vivo. Ambas de $100.
    KELLY_EDGE, KELLY_FRAC, BR0 = 0.0084, 0.25, 100.0
    res_sorted = sorted(B, key=lambda r: int(r["ws"]))
    eq = {"labels": [], "flat": [], "kelly": []}
    flat = kelly = BR0
    for i, r in enumerate(res_sorted, 1):
        a = float(r["ask"]); won = r.get("won") == "1"
        flat += (1 / a - 1) if won else -1                       # stake fijo de $1
        f = min(KELLY_FRAC * KELLY_EDGE / (1 - a), 0.25)         # fracción del bankroll (cap 25%)
        kelly *= (1 + f * (1 / a - 1)) if won else (1 - f)
        eq["labels"].append(i)
        eq["flat"].append(round(flat, 2))
        eq["kelly"].append(round(kelly, 2))

    def trade(r):
        return {"ws": int(r["ws"]), "slug": r.get("slug"), "fav": r.get("fav"),
                "ask": r.get("ask"), "ask2": r.get("ask2"), "status": r.get("status"),
                "winner": r.get("winner"), "won": r.get("won")}
    shown = [r for r in rows if r.get("status") in ("bought", "skip")][-80:][::-1]

    return jsonify({
        "summary": {"n": len(rows), "status": dict(st), "bought": len(bought),
                    "resolved": len(B), "pending": pending, "fillrate": fillrate,
                    "overall": overall, "by_band": by_band, "shadow": shadow,
                    "verdict": {"kind": verdict[0], "text": verdict[1]}},
        "trades": [trade(r) for r in shown],
        "equity": eq,
    })


# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_csv(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


if __name__ == "__main__":
    print("Dashboard corriendo en http://localhost:5000")
    # host 0.0.0.0 → accesible desde otros dispositivos de la red (móvil/PC)
    app.run(host="0.0.0.0", debug=False, port=5000, use_reloader=False)
