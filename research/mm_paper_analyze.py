"""
mm_paper_analyze.py — analiza mm_paper_log.csv (el market-maker en paper). ¿Es +EV cotizando con quote
rancio de 3s, o los inventarios atascados (selección adversa) lo hunden?

    cd ~/polymarket-btc-up-down/research && python3 mm_paper_analyze.py
"""
import csv, os, sys

LOG = os.path.join(os.path.dirname(__file__), "mm_paper_log.csv")


def main():
    if not os.path.exists(LOG): print("sin log aún"); return
    rows = [r for r in csv.DictReader(open(LOG, encoding="utf-8")) if r.get("won") in ("0", "1")]
    n = len(rows)
    if n == 0: print("sin ventanas resueltas aún — deja correr mm-paper"); return
    def f(r, k):
        try: return float(r[k])
        except Exception: return 0.0
    pnl = [f(r, "pnl_pp") for r in rows]
    rts = sum(int(r["roundtrips"]) for r in rows)
    buys = sum(int(r["buys"]) for r in rows); sells = sum(int(r["sells"]) for r in rows)
    stuck = [r for r in rows if int(r["end_inv"]) > 0]
    traded = [r for r in rows if int(r["buys"]) > 0]
    ws = sorted(int(r["ws"]) for r in rows); mid = ws[n // 2]
    tr = [p for r, p in zip(rows, pnl) if int(r["ws"]) < mid]; te = [p for r, p in zip(rows, pnl) if int(r["ws"]) >= mid]
    print(f"ventanas resueltas: {n}  ·  con trade: {len(traded)}  ·  spread medio: {sum(f(r,'spread_c') for r in rows)/n:.1f}¢")
    print(f"buys {buys} · sells {sells} · round-trips {rts}  ·  ventanas atascadas (inv>0): {len(stuck)} ({len(stuck)/max(1,len(traded))*100:.0f}%)")
    print(f"\nPnL MEDIO por ventana: {sum(pnl)/n:+.2f}pp   (train {sum(tr)/len(tr):+.1f} / test {sum(te)/len(te):+.1f})" if tr and te else f"\nPnL medio: {sum(pnl)/n:+.2f}pp")
    if stuck:
        print(f"PnL de las atascadas: {sum(f(r,'pnl_pp') for r in stuck)/len(stuck):+.2f}pp/ventana (el coste de la selección adversa)")
    rt_only = [r for r in traded if int(r["end_inv"]) == 0]
    if rt_only:
        print(f"PnL de las que cerraron plano (round-trip limpio): {sum(f(r,'pnl_pp') for r in rt_only)/len(rt_only):+.2f}pp")
    print("\n→ si PnL medio >0 y generaliza → el MM es jugable desde la Pi a 3s. Si las atascadas lo hunden →")
    print("  nos pican el quote rancio = necesitamos más velocidad (cotizar más rápido) o no es para la Pi.")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
