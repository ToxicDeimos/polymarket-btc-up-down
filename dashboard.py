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
MOM_FILE      = os.path.join(BASE, "research", "momentum_paper_log.csv")
MAKER3_FILE   = os.path.join(BASE, "research", "maker3_paper_log.csv")
FAVORITE_FILE = os.path.join(BASE, "research", "favorite_paper_log.csv")


# ── Rutas ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return redirect("/favorite")


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


def _m3_stats(rows):
    """EDGE del early-rester = win − target (el precio fijo del bid descansando)."""
    n = len(rows)
    if n == 0:
        return None
    wins = sum(1 for r in rows if r.get("won") == "1")
    tgts = [float(r["target"]) for r in rows if r.get("target")]
    wr = wins / n
    tg = statistics.mean(tgts) if tgts else 0.35
    se = math.sqrt(wr * (1 - wr) / n)
    return {"n": n, "win_rate": round(wr * 100, 1),
            "ci_lo": round(max(0, wr - 1.96 * se) * 100, 1),
            "ci_hi": round(min(1, wr + 1.96 * se) * 100, 1),
            "target": round(tg * 100, 1), "edge": round((wr - tg) * 100, 2),
            "rel": round((wr - tg) / tg * 100, 1) if tg else 0.0}


@app.route("/maker3")
def maker3():
    return render_template("maker3.html")


@app.route("/api/maker3")
def api_maker3():
    rows = _read_csv(MAKER3_FILE)
    if not rows:
        return jsonify({"summary": {"n": 0}, "trades": []})
    from collections import Counter
    st = Counter(r.get("status", "") for r in rows)
    filled = [r for r in rows if r.get("status") == "filled"]
    F = [r for r in filled if r.get("won") in ("0", "1")]
    pending = len(filled) - len(F)
    fillrate = round(len(filled) / len(rows) * 100, 1) if rows else 0.0
    overall = _m3_stats(F)
    by_phase = {lab: _m3_stats([r for r in F if r.get("fill_phase") and lo <= int(r["fill_phase"]) < hi])
                for lo, hi, lab in [(0, 60, "0-60s"), (60, 120, "60-120s"),
                                    (120, 195, "120-195s"), (195, 300, "195-300s")]}
    MIN_VERDICT = 40
    if overall is None or overall["n"] < MIN_VERDICT:
        verdict = ("wait", f"{overall['n'] if overall else 0}/{MIN_VERDICT} fills — sin veredicto. "
                   f"(maker2 posteando tarde dio −6.5pp; aquí llegamos temprano)")
    elif overall["edge"] > 0:
        if overall["ci_lo"] > overall["target"]:
            verdict = ("real", f"EDGE +{overall['edge']}pp SIGNIFICATIVO llegando temprano (win {overall['win_rate']}% "
                       f"> target {overall['target']}%, n={overall['n']}) → el maker SÍ se alcanza desde una Pi. "
                       f"maker2 fallaba por postear TARDE.")
        else:
            verdict = ("maybe", f"EDGE +{overall['edge']}pp positivo pero no significativo aún "
                       f"(n={overall['n']}, IC {overall['ci_lo']}-{overall['ci_hi']}%). Seguir.")
    else:
        verdict = ("dead", f"win {overall['win_rate']}% ≤ target {overall['target']}%: llegar temprano tampoco "
                   f"captura el edge → hay velocidad de por medio que una Pi no vence. Negativo legítimo.")

    def trade(r):
        return {"ws": int(r["ws"]), "slug": r.get("slug"), "side": r.get("side"),
                "target": r.get("target"), "fill_phase": r.get("fill_phase"),
                "status": r.get("status"), "winner": r.get("winner"), "won": r.get("won")}
    shown = [r for r in rows if r.get("status") in ("filled", "no_fill")][-80:][::-1]

    return jsonify({
        "summary": {"n": len(rows), "status": dict(st), "filled": len(filled),
                    "resolved": len(F), "pending": pending, "fillrate": fillrate,
                    "overall": overall, "by_phase": by_phase,
                    "verdict": {"kind": verdict[0], "text": verdict[1]}},
        "trades": [trade(r) for r in shown],
    })


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
    return {"n": n, "win_rate": round(wr * 100, 1),
            "ci_lo": round(max(0, wr - 1.96 * se) * 100, 1),
            "ci_hi": round(min(1, wr + 1.96 * se) * 100, 1),
            "ask": round(ap * 100, 1), "edge": round((wr - ap) * 100, 2),
            "rel": round((wr - ap) / ap * 100, 1) if ap else 0.0}


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
    by_band = {lab: _fav_stats([r for r in B if lo <= float(r["ask"]) < hi])
               for lo, hi, lab in [(0.82, 0.90, "82-90c"), (0.90, 0.95, "90-95c")]}
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
