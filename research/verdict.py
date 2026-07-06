import csv, os, statistics, sys
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass
rows = list(csv.DictReader(open(os.path.join(os.path.dirname(__file__),"markets_large.csv"), encoding="utf-8")))

# Favoritos profundos por el estimador limpio 'mid'
deep = []
for r in rows:
    try:
        mid = float(r["mid"]); npre = int(r["n_pre"]); won = int(r["yes_won"])
    except (ValueError, KeyError):
        continue
    favp = mid if mid >= 0.5 else 1 - mid
    favwon = won if mid >= 0.5 else (1 - won)
    if 0.90 <= favp <= 0.98:
        deep.append((favp, favwon, npre))

n = len(deep)
winrate = sum(d[1] for d in deep) / n
avg_price = sum(d[0] for d in deep) / n
lifes = sorted(d[2] for d in deep)
med_life = statistics.median(lifes)
print(f"Favoritos profundos (0.90-0.98), estimador mid:  n={n}")
print(f"  win rate real:     {winrate:.1%}")
print(f"  precio medio:      {avg_price:.3f}")
print(f"  vida (días): mediana {med_life:.0f}  | p25 {lifes[n//4]}  p75 {lifes[3*n//4]}")

# EV por operación neto (spread real 0.5¢ + fee)
half_spread = 0.005
for label, wr in [("histórico in-sample", winrate), ("conservador (OOS test)", 0.965)]:
    pe = avg_price + half_spread
    fee = 0.07 * (1 - pe)
    ev = wr / pe - 1 - fee
    # hold realista: ~mitad de la vida (compras cuando ya es favorito) → conservador: media vida
    hold = med_life / 2
    ann = (1 + ev) ** (365 / max(hold, 1)) - 1 if ev > -1 else -1
    print(f"\n  [{label}] win={wr:.1%}")
    print(f"     EV neto/operación: {ev:+.2%}")
    print(f"     hold ~{hold:.0f} días (media vida)  →  ANUALIZADO ≈ {ann:+.0%}")
