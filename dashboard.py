"""
Panel web — ema-favorite-paper (favorito 52-82¢ filtrado por EMA9 1m).
Uso: python dashboard.py  →  http://localhost:5000
"""
import csv
import math
import os
import statistics
from flask import Flask, jsonify, render_template, redirect

app = Flask(__name__)

BASE = os.path.dirname(__file__)
EMA_FILE = os.path.join(BASE, "research", "ema_favorite_log.csv")
MIN_VERDICT = 80


def FEE(p):
    return 0.07 * p * (1 - p)   # comisión taker crypto de Polymarket


@app.route("/")
def index():
    return redirect("/ema")


def _ema_stats(rows):
    """win, ask, edge CRUDO y NETO de comisión (win − ask − fee), con IC del neto."""
    rows = [r for r in rows if r.get("won") in ("0", "1") and r.get("ask")]
    n = len(rows)
    if n == 0:
        return None
    asks = [float(r["ask"]) for r in rows]
    wr = sum(1 for r in rows if r["won"] == "1") / n
    ap = statistics.mean(asks)
    fee = statistics.mean(FEE(a) for a in asks)
    se = math.sqrt(wr * (1 - wr) / n)              # SE del win ≈ SE del edge (ask/fee ~fijos)
    net = (wr - ap - fee) * 100
    return {"n": n, "win_rate": round(wr * 100, 1),
            "ci_lo": round(max(0, wr - 1.96 * se) * 100, 1),
            "ci_hi": round(min(1, wr + 1.96 * se) * 100, 1),
            "ask": round(ap * 100, 1), "raw": round((wr - ap) * 100, 2),
            "fee": round(fee * 100, 2), "net": round(net, 2),
            "net_lo": round(net - 1.96 * se * 100, 2)}


@app.route("/ema")
def ema():
    return render_template("ema.html")


@app.route("/api/ema")
def api_ema():
    rows = _read_csv(EMA_FILE)
    if not rows:
        return jsonify({"summary": {"n": 0}, "trades": []})
    from collections import Counter
    st = Counter(r.get("status", "") for r in rows)
    B = [r for r in rows if r.get("status") == "bought"]    # alineado → COMPRA
    A = [r for r in rows if r.get("status") == "against"]   # contra → SOMBRA (debe perder)
    resolved = len([r for r in B if r.get("won") in ("0", "1")])
    aligned = _ema_stats(B)
    against = _ema_stats(A)
    fillrate = round(len(B) / len(rows) * 100, 1) if rows else 0.0
    by_band = {lab: _ema_stats([r for r in B if r.get("ask") and lo <= float(r["ask"]) < hi])
               for lo, hi, lab in [(0.52, 0.62, "52-62c"), (0.62, 0.72, "62-72c"), (0.72, 0.821, "72-82c")]}

    # Veredicto: ≥80 comprados; alineado NETO>0 significativo Y contra NETO<0
    if aligned is None or aligned["n"] < MIN_VERDICT:
        verdict = ("wait", f"{aligned['n'] if aligned else 0}/{MIN_VERDICT} comprados — sin veredicto "
                   f"(el efecto esperado es enorme → confirma rápido).")
    elif aligned["net"] > 0 and aligned["net_lo"] > 0:
        extra = f" y CONTRA pierde (NETO {against['net']:+}pp)" if against and against["net"] < 0 else ""
        verdict = ("real", f"✅ REGLA CONFIRMADA: alineado NETO {aligned['net']:+}pp SIGNIFICATIVO tras "
                   f"comisión (n={aligned['n']}){extra}. Replicable con klines 1m → candidato REAL a live.")
    elif aligned["net"] > 0:
        verdict = ("maybe", f"alineado NETO {aligned['net']:+}pp positivo pero no significativo "
                   f"(n={aligned['n']}, IC neto desde {aligned['net_lo']:+}pp). Seguir acumulando.")
    else:
        verdict = ("dead", f"alineado NETO {aligned['net']:+}pp ≤0 con n≥{MIN_VERDICT}: la regla no se "
                   f"sostiene forward. Revisar.")

    # Equity: P&L NETO acumulado por share (won − ask − fee), alineado (sube) vs contra (baja)
    def cumnet(rs):
        rs = sorted([r for r in rs if r.get("won") in ("0", "1") and r.get("ask")], key=lambda r: int(r["ws"]))
        out = []; c = 0.0
        for r in rs:
            a = float(r["ask"]); won = 1 if r["won"] == "1" else 0
            c += won - a - FEE(a)
            out.append(round(c, 3))
        return out
    eq_al, eq_ct = cumnet(B), cumnet(A)
    equity = {"labels": list(range(1, max(len(eq_al), len(eq_ct), 1) + 1)),
              "aligned": eq_al, "against": eq_ct}

    def trade(r):
        return {"ws": int(r["ws"]), "slug": r.get("slug"), "fav": r.get("fav"), "ask": r.get("ask"),
                "aligned": r.get("aligned"), "px": r.get("px"), "ema": r.get("ema"),
                "status": r.get("status"), "winner": r.get("winner"), "won": r.get("won")}
    shown = [r for r in rows if r.get("status") in ("bought", "against")][-80:][::-1]

    return jsonify({
        "summary": {"n": len(rows), "status": dict(st), "bought": len(B), "against": len(A),
                    "resolved": resolved, "fillrate": fillrate,
                    "aligned": aligned, "against_s": against, "by_band": by_band,
                    "verdict": {"kind": verdict[0], "text": verdict[1]}},
        "trades": [trade(r) for r in shown],
        "equity": equity,
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
    app.run(host="0.0.0.0", debug=False, port=5000, use_reloader=False)
