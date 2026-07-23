"""
Panel web del bot — corre independiente del bot principal.
Uso: python dashboard.py
Abre: http://localhost:5000
"""
import csv
import json
import math
import os
import statistics
from flask import Flask, jsonify, render_template

app = Flask(__name__)

BASE = os.path.dirname(__file__)
STATUS_FILE  = os.path.join(BASE, "status.json")
RESULTS_FILE = os.path.join(BASE, "results.csv")
PRICES_FILE  = os.path.join(BASE, "prices.csv")
BRAIN_FILE   = os.path.join(BASE, "brain_stats.json")
MAKER_FILE   = os.path.join(BASE, "research", "maker_paper_log.csv")
MOM_FILE     = os.path.join(BASE, "research", "momentum_paper_log.csv")


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
            "d_recent20":      _wr(dir_ops[-20:]),
            "d_pnl":           d_pnl,
            "up_count":        len(up_ops),
            "down_count":      len(down_ops),
            "up_win_rate":     _wr(up_ops),
            "down_win_rate":   _wr(down_ops),
            "ops_to_adapt":    max(0, 20 - d_total),
            "ops_to_decide":   max(0, 150 - d_total),   # confianza estadística preliminar
        })
    except Exception as e:
        return jsonify({"error": str(e)})


# ── Maker Paper Bot (DRY) ─────────────────────────────────────────────────────

@app.route("/maker")
def maker():
    return render_template("maker.html")


def _maker_mkt(r):
    return "15m" if "-15m-" in r.get("slug", "") else "5m"


def _maker_stats(fills: list) -> dict | None:
    """WIN, IC95%, bid medio (break-even) y EV/fill de un grupo de fills resueltos."""
    n = len(fills)
    if n == 0:
        return None
    wins = sum(1 for r in fills if r.get("won") == "1")
    bids = [float(r["bid"]) for r in fills if r.get("bid")]
    wr = wins / n
    ap = statistics.mean(bids) if bids else 0.0
    se = math.sqrt(wr * (1 - wr) / n)
    ev = sum((1 / float(r["bid"]) - 1) if r.get("won") == "1" else -1 for r in fills) / n
    return {
        "n": n, "wins": wins, "win_rate": round(wr * 100, 1),
        "ci_lo": round(max(0, wr - 1.96 * se) * 100, 1),
        "ci_hi": round(min(1, wr + 1.96 * se) * 100, 1),
        "avg_bid": round(ap * 100, 1), "ev": round(ev * 100, 1),
    }


@app.route("/api/maker")
def api_maker():
    allrows = _read_csv(MAKER_FILE)
    rows = [r for r in allrows if "btc-updown" in (r.get("slug") or "")]
    if not rows:
        return jsonify({"summary": {"n": 0}, "trades": []})

    from collections import Counter
    st = Counter(r.get("status", "") for r in rows)
    posted     = [r for r in rows if r.get("status") in ("filled", "no_fill", "cancelled")]
    filled_all = [r for r in rows if r.get("status") == "filled"]
    fills      = [r for r in filled_all if r.get("won") in ("0", "1")]     # resueltos
    pending    = [r for r in filled_all if r.get("won") not in ("0", "1")]

    def bucket(r):
        p = float(r["cheap_price"])
        return "<30c" if p < 0.30 else ("30-40c" if p < 0.40 else ">=40c")

    overall   = _maker_stats(fills)
    by_market = {m: _maker_stats([f for f in fills if _maker_mkt(f) == m]) for m in ("5m", "15m")}
    by_bucket = {b: _maker_stats([f for f in fills if f.get("cheap_price") and bucket(f) == b])
                 for b in ("<30c", "30-40c", ">=40c")}

    # Contexto de cancelled/no_fill: ¿el lado barato GANÓ? (¿el cancel protege o corta ganadores?)
    def _ctx(status_name):
        b=[r for r in rows if r.get("status")==status_name and r.get("winner") and r.get("cheap")]
        if not b: return None
        won=sum(1 for r in b if r["winner"]==r["cheap"])
        prices=[float(r["cheap_price"]) for r in b if r.get("cheap_price")]
        return {"n":len(b), "won_pct":round(won/len(b)*100,1),
                "avg_price":round(sum(prices)/len(prices)*100,1) if prices else None}
    cancelled_ctx=_ctx("cancelled"); nofill_ctx=_ctx("no_fill")

    # Curva de P&L acumulado por $1 apostado, por bucket, en orden cronológico de fill.
    # Cada bucket arrastra su último valor cuando no tiene fill en ese paso → 3 líneas alineadas.
    res_sorted = sorted(fills, key=lambda r: int(r["ws"]))
    cum = {"<30c": 0.0, "30-40c": 0.0, ">=40c": 0.0}
    curve = {"labels": [], "series": {"<30c": [], "30-40c": [], ">=40c": []}}
    for i, r in enumerate(res_sorted, 1):
        b = bucket(r)
        cum[b] += (1 / float(r["bid"]) - 1) if r.get("won") == "1" else -1
        curve["labels"].append(i)
        for k in curve["series"]:
            curve["series"][k].append(round(cum[k], 3))

    if overall and overall["n"] >= 20:
        if overall["ci_lo"] > overall["avg_bid"]:
            verdict = ("real", "Capturamos el edge de ejecución (WIN > bid, significativo).")
        elif overall["win_rate"] > overall["avg_bid"]:
            verdict = ("maybe", "Positivo pero no significativo — deja correr (más fills).")
        else:
            verdict = ("adverse", "Selección adversa: nos llenan en los perdedores.")
    else:
        verdict = ("wait", f"Aún pocos fills ({overall['n'] if overall else 0}/20) para veredicto.")

    def trade(r):
        return {
            "ws": int(r["ws"]), "market": _maker_mkt(r),
            "spike": r.get("spike"), "typ": r.get("typ_move"), "spike_max": r.get("spike_max"),
            "cheap": r.get("cheap"), "cheap_price": r.get("cheap_price"), "bid": r.get("bid"),
            "status": r.get("status"), "winner": r.get("winner"), "won": r.get("won"),
        }
    trades = [trade(r) for r in posted][-100:][::-1]   # más recientes primero

    return jsonify({
        "summary": {
            "n": len(rows), "discarded": len(allrows) - len(rows), "status": dict(st),
            "posted": len(posted), "filled": len(filled_all),
            "fill_rate": round(len(filled_all) / len(posted) * 100, 1) if posted else 0,
            "fills_resolved": len(fills), "pending": len(pending),
            "overall": overall, "by_market": by_market, "by_bucket": by_bucket,
            "cancelled_ctx": cancelled_ctx, "nofill_ctx": nofill_ctx,
            "verdict": {"kind": verdict[0], "text": verdict[1]},
        },
        "trades": trades,
        "curve": curve,
    })


# ── Momentum Paper Bot (DRY, experimento #3) ──────────────────────────────────

@app.route("/momentum")
def momentum():
    return render_template("momentum.html")


def _mom_stats(trades: list) -> dict | None:
    n = len(trades)
    if n == 0:
        return None
    wins = sum(1 for r in trades if r.get("won") == "1")
    asks = [float(r["ask"]) for r in trades if r.get("ask")]
    wr = wins / n
    ap = statistics.mean(asks) if asks else 0.0
    se = math.sqrt(wr * (1 - wr) / n)
    ev = sum((1 / float(r["ask"]) - 1) if r.get("won") == "1" else -1 for r in trades) / n
    return {"n": n, "win_rate": round(wr * 100, 1),
            "ci_lo": round(max(0, wr - 1.96 * se) * 100, 1),
            "ci_hi": round(min(1, wr + 1.96 * se) * 100, 1),
            "avg_ask": round(ap * 100, 1), "ev": round(ev * 100, 1)}


def _mom_move_bucket(r):
    m = abs(float(r["move"]))
    return "suave" if m < 15 else ("media" if m < 40 else "fuerte")


@app.route("/api/momentum")
def api_momentum():
    rows = _read_csv(MOM_FILE)
    if not rows:
        return jsonify({"summary": {"n": 0}, "trades": [], "curve": None})
    from collections import Counter
    st = Counter(r.get("status", "") for r in rows)
    takers   = [r for r in rows if r.get("status") == "taker"]
    resolved = [r for r in takers if r.get("won") in ("0", "1")]
    pending  = len(takers) - len(resolved)
    takers_b   = [r for r in rows if r.get("status") == "taker_b"]
    resolved_b = [r for r in takers_b if r.get("won") in ("0", "1")]
    arm_b = _mom_stats(resolved_b)
    if arm_b: arm_b["pending"] = len(takers_b) - len(resolved_b)
    # Filtro DIVERGENCIA Chainlink en sombra (lo que SÍ tiene filo según el lab): Chainlink se
    # movió >=$3 EN CONTRA a 240s. 'alineado' (no diverge) = caso normal → MANTIENE; 'diverge' = QUITA.
    arm_c_keep = _mom_stats([r for r in resolved if r.get("cl_div") == "no"])
    arm_c_div  = _mom_stats([r for r in resolved if r.get("cl_div") == "yes"])
    arm_bc_keep = _mom_stats([r for r in resolved_b if r.get("cl_div") == "no"])
    arm_bc_div  = _mom_stats([r for r in resolved_b if r.get("cl_div") == "yes"])
    cl_signals = sum(1 for r in takers + takers_b if r.get("cl_div") in ("yes", "no"))
    # Filtro ACELERACIÓN (el hallazgo sólido del lab) — sobre A y sobre B
    arm_ac_yes = _mom_stats([r for r in resolved if r.get("accel") == "yes"])
    arm_ac_no  = _mom_stats([r for r in resolved if r.get("accel") == "no"])
    arm_bac_yes = _mom_stats([r for r in resolved_b if r.get("accel") == "yes"])
    arm_bac_no  = _mom_stats([r for r in resolved_b if r.get("accel") == "no"])

    def _pnl1(rs):   # P&L acumulado por $1 apostado por trade
        return round(sum((1 / float(r["ask"]) - 1) if r.get("won") == "1" else -1 for r in rs), 3) if rs else 0.0
    overall = _mom_stats(resolved)
    by_move = {b: _mom_stats([r for r in resolved if _mom_move_bucket(r) == b])
               for b in ("suave", "media", "fuerte")}
    def askb(r):
        a = float(r["ask"])
        return "52-62c" if a < 0.62 else ("62-72c" if a < 0.72 else "72-82c")
    by_ask = {b: _mom_stats([r for r in resolved if askb(r) == b])
              for b in ("52-62c", "62-72c", "72-82c")}

    # Veredicto pre-registrado: >=40 resueltos; EV>0; IC a ~80
    if overall is None or overall["n"] < 40:
        verdict = ("wait", f"Aún {overall['n'] if overall else 0}/40 trades resueltos para veredicto.")
    elif overall["win_rate"] > overall["avg_ask"]:
        if overall["ci_lo"] > overall["avg_ask"]:
            verdict = ("real", "LA SEÑAL PREDICE (win > ask, significativo). El edge de momentum se transfiere.")
        else:
            verdict = ("maybe", "Positivo pero no significativo — seguir hasta ~80 y exigir IC.")
    else:
        verdict = ("dead", "≤ break-even con n≥40: 12ª muerte — el edge no se transfiere tal cual.")

    # Veredicto del CANDIDATO Nº1: brazo A-v3 = A filtrado por ACELERACIÓN (accel=="yes"). Es el
    # único lead que separa, con doble respaldo (forward + lab n=250 train/test ✓). Pre-registrado:
    # ≥30 resueltos, EV>0 (win>ask), significativo si IC inferior > ask.
    av = arm_ac_yes
    if av is None or av["n"] < 30:
        accel_verdict = ("wait", f"A-v3 (solo 'acelera'): {av['n'] if av else 0}/30 resueltos. "
                         f"Respaldo lab n=250 train/test ✓; falta confirmarlo en vivo. Sin veredicto aún.")
    elif av["win_rate"] > av["avg_ask"]:
        _ev = f"{'+' if av['ev'] >= 0 else ''}{av['ev']}%"
        if av["ci_lo"] > av["avg_ask"]:
            accel_verdict = ("real", f"A-v3 'acelera' PREDICE: win {av['win_rate']}% > ask {av['avg_ask']}% "
                             f"SIGNIFICATIVO (n={av['n']}, EV {_ev}). Candidato a filtro real + live minúsculo.")
        else:
            accel_verdict = ("maybe", f"A-v3 'acelera' positivo pero no significativo: win {av['win_rate']}% "
                             f"vs ask {av['avg_ask']}% (n={av['n']}, EV {_ev}, IC {av['ci_lo']}-{av['ci_hi']}). Seguir a 30+.")
    else:
        accel_verdict = ("dead", f"A-v3 'acelera' ≤ break-even con n={av['n']} (win {av['win_rate']}% ≤ "
                         f"ask {av['avg_ask']}%) — la aceleración no filtra en vivo. Revisar.")

    # curva P&L acumulado por $1, por bucket de move
    res_sorted = sorted(resolved, key=lambda r: int(r["ws"]))
    cum = {"suave": 0.0, "media": 0.0, "fuerte": 0.0}
    curve = {"labels": [], "series": {"suave": [], "media": [], "fuerte": []}}
    for i, r in enumerate(res_sorted, 1):
        b = _mom_move_bucket(r)
        cum[b] += (1 / float(r["ask"]) - 1) if r.get("won") == "1" else -1
        curve["labels"].append(i)
        for k in curve["series"]:
            curve["series"][k].append(round(cum[k], 3))

    def trade(r):
        return {"ws": int(r["ws"]), "slug": r.get("slug"), "move": r.get("move"),
                "leader": r.get("leader"), "ask": r.get("ask"), "status": r.get("status"),
                "winner": r.get("winner"), "won": r.get("won")}
    shown = [r for r in rows if r.get("status") in ("taker", "taker_b", "skip_price")][-100:][::-1]

    return jsonify({
        "summary": {"n": len(rows), "status": dict(st), "signals": len(takers),
                    "resolved": len(resolved), "pending": pending,
                    "overall": overall, "by_move": by_move, "by_ask": by_ask,
                    "arm_b": arm_b, "arm_b_signals": len(takers_b),
                    "arm_c_keep": arm_c_keep, "arm_c_div": arm_c_div, "cl_signals": cl_signals,
                    "arm_bc_keep": arm_bc_keep, "arm_bc_div": arm_bc_div,
                    "arm_ac_yes": arm_ac_yes, "arm_ac_no": arm_ac_no,
                    "arm_bac_yes": arm_bac_yes, "arm_bac_no": arm_bac_no,
                    "pnl1": _pnl1(resolved), "pnl1_b": _pnl1(resolved_b),
                    "verdict": {"kind": verdict[0], "text": verdict[1]},
                    "accel_verdict": {"kind": accel_verdict[0], "text": accel_verdict[1]}},
        "trades": [trade(r) for r in shown],
        "curve": curve,
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


def _recent_wr(history: list, n: int) -> float:
    recent = history[-n:]
    if not recent:
        return 0.0
    return round(sum(1 for h in recent if h.get("won")) / len(recent) * 100, 1)


if __name__ == "__main__":
    print("Dashboard corriendo en http://localhost:5000")
    # host 0.0.0.0 → accesible desde otros dispositivos de la red (móvil/PC)
    app.run(host="0.0.0.0", debug=False, port=5000, use_reloader=False)
