"""
Panel web del bot — corre independiente del bot principal.
Uso: python dashboard.py
Abre: http://localhost:5000
"""
import csv
import json
import os
from flask import Flask, jsonify, render_template

app = Flask(__name__)

BASE = os.path.dirname(__file__)
STATUS_FILE  = os.path.join(BASE, "status.json")
RESULTS_FILE = os.path.join(BASE, "results.csv")
PRICES_FILE  = os.path.join(BASE, "prices.csv")
BRAIN_FILE   = os.path.join(BASE, "brain_stats.json")


# ── Rutas ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    if not os.path.exists(STATUS_FILE):
        return jsonify({"running": False})
    try:
        with open(STATUS_FILE, encoding="utf-8") as f:
            return jsonify(json.load(f))
    except Exception:
        return jsonify({"running": False})


@app.route("/api/results")
def api_results():
    rows = _read_csv(RESULTS_FILE)
    # El acumulado SIEMPRE se recalcula desde 'profit' en orden cronológico.
    # Nunca confiamos en el total_profit guardado (puede estar desfasado por reinicios).
    running = 0.0
    for r in rows:
        try:
            r["profit"] = round(float(r.get("profit") or 0), 2)
        except (ValueError, TypeError):
            r["profit"] = 0.0
        running = round(running + r["profit"], 2)
        r["total_profit"] = running
    return jsonify(rows)


@app.route("/api/prices/current")
def api_prices_current():
    """Últimos 60 snapshots de precios (ventana actual o reciente)."""
    rows = _read_csv(PRICES_FILE)
    if not rows:
        return jsonify([])
    # Agrupar por condition_id más reciente
    last_cid = rows[-1].get("condition_id", "")
    current  = [r for r in rows if r.get("condition_id") == last_cid]
    return jsonify(current[-60:])


@app.route("/api/brain")
def api_brain():
    if not os.path.exists(BRAIN_FILE):
        return jsonify({})
    try:
        with open(BRAIN_FILE, encoding="utf-8") as f:
            data = json.load(f)
        history = data.get("history", [])
        total   = len(history)
        wins    = sum(1 for h in history if h.get("won"))
        by_type = {}
        for h in history:
            et = h.get("edge_type", "unknown")
            if et not in by_type:
                by_type[et] = {"total": 0, "wins": 0}
            by_type[et]["total"] += 1
            if h.get("won"):
                by_type[et]["wins"] += 1
        return jsonify({
            "total":      total,
            "wins":       wins,
            "win_rate":   round(wins / total * 100, 1) if total else 0,
            "threshold":  round(data.get("threshold", 0) * 100, 1),
            "vol":        round(data.get("vol", 0), 4),
            "by_type":    by_type,
            "recent_20":  _recent_wr(history, 20),
        })
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/training")
def api_training():
    """Estado del entrenamiento direccional del Brain."""
    if not os.path.exists(BRAIN_FILE):
        return jsonify({})
    try:
        with open(BRAIN_FILE, encoding="utf-8") as f:
            data = json.load(f)

        # ── Direccional: ops reales con entrada, separadas por lado ───────────
        dir_ops = []
        for r in _read_csv(RESULTS_FILE):
            if r.get("mode") != "directional":
                continue
            up_f = r.get("up_filled") == "True"
            dn_f = r.get("down_filled") == "True"
            if not (up_f or dn_f):
                continue
            side   = "UP" if up_f else "DOWN"
            winner = r.get("winner", "")
            won    = (side == "UP" and winner == "Up") or (side == "DOWN" and winner == "Down")
            try:
                pnl = float(r.get("profit") or 0)
            except (ValueError, TypeError):
                pnl = 0.0
            dir_ops.append({"side": side, "won": won, "pnl": pnl})

        d_total = len(dir_ops)
        d_wins  = sum(1 for o in dir_ops if o["won"])
        d_pnl   = round(sum(o["pnl"] for o in dir_ops), 2)
        up_ops   = [o for o in dir_ops if o["side"] == "UP"]
        down_ops = [o for o in dir_ops if o["side"] == "DOWN"]

        def _wr(ops):
            return round(sum(1 for o in ops if o["won"]) / len(ops) * 100, 0) if ops else None

        return jsonify({
            "vol":             round(data.get("vol", 0), 4),
            "threshold":       round(data.get("threshold", 0) * 100, 1),
            # Direccional (lo importante ahora)
            "d_total":         d_total,
            "d_wins":          d_wins,
            "d_win_rate":      _wr(dir_ops),
            "d_pnl":           d_pnl,
            "up_count":        len(up_ops),
            "down_count":      len(down_ops),
            "up_win_rate":     _wr(up_ops),
            "down_win_rate":   _wr(down_ops),
            "ops_to_adapt":    max(0, 20 - d_total),
            "ops_to_decide":   max(0, 40 - d_total),
        })
    except Exception as e:
        return jsonify({"error": str(e)})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_csv(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def _recent_wr(history: list, n: int) -> float:
    recent = history[-n:]
    if not recent:
        return 0.0
    return round(sum(1 for h in recent if h.get("won")) / len(recent) * 100, 1)


if __name__ == "__main__":
    print("Dashboard corriendo en http://localhost:5000")
    app.run(debug=False, port=5000, use_reloader=False)
