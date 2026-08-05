"""mm_ws_analyze.py — analiza mm_ws_log.csv (MM WebSocket + skew). ¿El skew controla el inventario y el MM
es +EV en media? Recuerda: el PnL es COTA SUPERIOR (asume ganar la cola en cada fill)."""
import csv, os, sys

LOG = os.path.join(os.path.dirname(__file__), "mm_ws_log.csv")


def main():
    if not os.path.exists(LOG): print("sin log aún"); return
    R = [r for r in csv.DictReader(open(LOG, encoding="utf-8")) if r.get("won") in ("0", "1")]
    n = len(R)
    if n == 0: print("sin ventanas resueltas aún — deja correr mm-ws"); return
    def f(r, k):
        try: return float(r[k])
        except Exception: return 0.0
    pnl = [f(r, "pnl_pp") for r in R]
    rt = sum(int(r["roundtrips"]) for r in R)
    stuck = [r for r in R if int(r["end_inv"]) != 0]
    maxinvs = [int(r["maxinv_seen"]) for r in R]
    ws = sorted(int(r["ws"]) for r in R); mid = ws[n // 2]
    tr = [p for r, p in zip(R, pnl) if int(r["ws"]) < mid]; te = [p for r, p in zip(R, pnl) if int(r["ws"]) >= mid]
    print(f"ventanas resueltas: {n}  ·  round-trips totales: {rt} (media {rt/n:.1f}/ventana)")
    print(f"inventario: máx medio {sum(maxinvs)/n:.1f} · ventanas que NO terminaron planas: {len(stuck)} ({len(stuck)/n*100:.0f}%)")
    print(f"  → si el skew funciona, casi todas terminan planas (inv 0) y el máx es bajo.")
    print(f"\nPnL MEDIO por ventana: {sum(pnl)/n:+.2f}pp", end="")
    if tr and te: print(f"   (train {sum(tr)/len(tr):+.1f} / test {sum(te)/len(te):+.1f})")
    else: print()
    win = sum(1 for p in pnl if p > 0)
    print(f"ventanas con PnL>0: {win}/{n} ({win/n*100:.0f}%)")
    print("\n⚠ El PnL es COTA SUPERIOR: asume que ganas la cola en cada fill. La cifra real sería una fracción")
    print("  (compites con otros makers). Lo que SÍ es fiable: el % plano/máx-inventario (¿el skew controla?).")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
