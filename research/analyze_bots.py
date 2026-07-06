"""
Ingeniería inversa de los ganadores en BTC Up/Down 15m.
Lee btc_trades.csv → P&L por wallet → ranking → caracteriza CÓMO ganan.
"""
import csv, os, sys, statistics
from collections import defaultdict
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass

P = os.path.join(os.path.dirname(__file__), "btc_trades.csv")
rows = list(csv.DictReader(open(P, encoding="utf-8")))
print(f"Trades leídos: {len(rows)}")

def f(x):
    try: return float(x)
    except: return 0.0

# ── P&L por (wallet, mercado) ─────────────────────────────────────────────────
# clave: (wallet, cid). Acumular cash y shares; al final sumar shares[winner].
pos = defaultdict(lambda: {"cash":0.0,"Up":0.0,"Down":0.0,"winner":None,"wstart":0,
                            "buys":0.0,"sells":0.0,"trades":[]})
markets = set()
for r in rows:
    wal, cid = r["wallet"], r["cid"]
    markets.add(cid)
    key = (wal, cid)
    p = pos[key]; p["winner"] = r["winner"]; p["wstart"] = int(r["wstart"] or 0)
    price, size = f(r["price"]), f(r["size"])
    o = r["outcome"]
    if r["side"] == "BUY":
        p["cash"] -= price*size; p[o] += size; p["buys"] += price*size
    else:
        p["cash"] += price*size; p[o] -= size; p["sells"] += price*size
    p["trades"].append((int(r["ts"] or 0)-p["wstart"], r["side"], o, price, size))

# ── Agregar por wallet ────────────────────────────────────────────────────────
wal = defaultdict(lambda: {"pnl":0.0,"mk":0,"won":0,"inv":0.0,"tr":0,"buys":0.0,"sells":0.0,
                            "timings":[],"prices":[],"sides":[],"sizes":[],"name":"","netdir":[]})
for (w_, cid), p in pos.items():
    pnl = p["cash"] + p[p["winner"]]*1.0
    a = wal[w_]
    a["pnl"] += pnl; a["mk"] += 1; a["won"] += 1 if pnl > 0 else 0
    a["inv"] += p["buys"]; a["buys"] += p["buys"]; a["sells"] += p["sells"]
    a["tr"] += len(p["trades"])
    a["netdir"].append(p["Up"] - p["Down"])   # posición neta direccional final
    for tim, side, o, price, size in p["trades"]:
        a["timings"].append(tim); a["prices"].append(price)
        a["sides"].append(side); a["sizes"].append(size)

for r in rows:
    if r.get("name"): wal[r["wallet"]]["name"] = r["name"]

print(f"Mercados: {len(markets)} | wallets: {len(wal)}")
total_pnl = sum(a["pnl"] for a in wal.values())
print(f"Suma P&L de TODAS las wallets: {total_pnl:+.0f}  "
      f"(≈0 → tengo ambos lados; muy != 0 → me faltan makers/contrapartes)\n")

# ── Ranking de ganadores consistentes ─────────────────────────────────────────
MIN_MK = int(sys.argv[1]) if len(sys.argv) > 1 else 10
cand = [(w_, a) for w_, a in wal.items() if a["mk"] >= MIN_MK]
cand.sort(key=lambda x: -x[1]["pnl"])
print("="*94)
print(f"  TOP GANADORES (>= {MIN_MK} mercados)")
print("="*94)
print(f"  {'wallet':>14} | {'P&L':>8} | {'ROI':>6} | {'mkts':>4} | {'win%':>5} | {'trades':>6} | {'churn':>6} | nombre")
print("  " + "-"*90)
def churn(a):   # sells/buys: ~1 = market maker (compra y vende); ~0 = compra y aguanta
    return a["sells"]/a["buys"] if a["buys"]>0 else 0
for w_, a in cand[:20]:
    roi = a["pnl"]/a["inv"] if a["inv"]>0 else 0
    print(f"  {w_[:12]+'…':>14} | {a['pnl']:>+8.0f} | {roi:>+5.0%} | {a['mk']:>4} | "
          f"{a['won']/a['mk']:>4.0%} | {a['tr']:>6} | {churn(a):>5.2f} | {a['name'][:16]}")

# ── Caracterizar los 5 mejores: ¿CÓMO ganan? ─────────────────────────────────
print("\n" + "="*94)
print("  ¿CÓMO GANAN? (perfil de los 5 mejores)")
print("="*94)
for w_, a in cand[:5]:
    tim = a["timings"]; pr = a["prices"]
    buyfrac = sum(1 for s in a["sides"] if s=="BUY")/len(a["sides"]) if a["sides"] else 0
    avg_abs_net = statistics.mean(abs(x) for x in a["netdir"]) if a["netdir"] else 0
    print(f"\n  {w_[:16]}…  ({a['name']})  P&L {a['pnl']:+.0f} en {a['mk']} mercados")
    print(f"    timing entrada:  mediana {statistics.median(tim):.0f}s  (0-900 = dónde en la ventana)")
    print(f"    precio medio:    {statistics.mean(pr):.3f}")
    print(f"    %BUY vs SELL:    {buyfrac:.0%} buy   | churn(sell/buy $): {churn(a):.2f}")
    print(f"    tamaño medio:    {statistics.mean(a['sizes']):.1f} shares  | ${a['inv']:.0f} invertidos")
    print(f"    posición neta media |Up-Down|: {avg_abs_net:.1f}  (alta=direccional, ~0=market maker)")
    print(f"    perfil probable: {'MARKET MAKER' if churn(a)>0.5 else ('DIRECCIONAL TARDÍO' if statistics.median(tim)>500 else 'DIRECCIONAL')}")
