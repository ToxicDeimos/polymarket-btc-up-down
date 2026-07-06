"""
Aprende un SELECTOR de todas las features (regresión logística L2, numpy).
Split train/test por fecha. El modelo predice P(el lado barato gana). Se FADEA solo
cuando P_modelo > precio (EV>0). Se juzga UNA vez en TEST held-out: ¿las apuestas
que elige el modelo ganan por encima del precio, out-of-sample?
"""
import csv, os, sys, math
import numpy as np
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass
D=os.path.dirname(__file__)
rows=list(csv.DictReader(open(os.path.join(D,"rich.csv"),encoding="utf-8")))
rows=[r for r in rows if r.get("won") in ("0","1")]
rows.sort(key=lambda r:int(r["ws"]))
print(f"ventanas: {len(rows)}")

FEATS=["wlen","spike","abs_spike","vol_pre","peak_dev","reverting","btc_cont60",
       "cheap_price","divergence","buy_cheap","sell_cheap","buy_exp","sell_exp",
       "flow_imb","sellpress","n_trades","total_vol","hour"]
def num(r,k):
    try: return float(r[k])
    except: return 0.0
X=np.array([[num(r,k) for k in FEATS] for r in rows])
y=np.array([int(r["won"]) for r in rows])
price=np.array([num(r,"cheap_price") for r in rows])

h=int(len(rows)*0.6)
Xtr,Xte=X[:h],X[h:]; ytr,yte=y[:h],y[h:]; ptr,pte=price[:h],price[h:]
mu,sd=Xtr.mean(0),Xtr.std(0)+1e-9
Xtr=(Xtr-mu)/sd; Xte=(Xte-mu)/sd

def fit(X,y,l2,lr=0.2,it=4000):
    n,d=X.shape; w=np.zeros(d); b=0.0
    for _ in range(it):
        p=1/(1+np.exp(-(X@w+b)))
        w-=lr*(X.T@(p-y)/n+l2*w/n); b-=lr*(p-y).mean()
    return w,b

# elegir l2 por validación dentro de train
hv=int(h*0.75); best=None
for l2 in [0.01,0.1,0.5,1,3,10]:
    w,b=fit(Xtr[:hv],ytr[:hv],l2)
    pv=1/(1+np.exp(-(Xtr[hv:]@w+b)))
    # EV en validación de fadear cuando pv>precio
    pr=price[:h][hv:]; sel=pv>pr
    if sel.sum()>=8:
        ev=np.mean((1/pr[sel]-1)*ytr[hv:][sel] + (-1)*(1-ytr[hv:][sel]))
    else: ev=-1
    if best is None or ev>best[1]: best=(l2,ev)
l2=best[0]
w,b=fit(Xtr,ytr,l2)
print(f"l2 elegido: {l2}")

# TEST held-out
pte_hat=1/(1+np.exp(-(Xte@w+b)))
def report(name, mask):
    n=int(mask.sum())
    if n<10: print(f"  {name}: n={n} (insuficiente)"); return
    wr=yte[mask].mean(); ap=pte[mask].mean()
    ev=np.mean((1/pte[mask]-1)*yte[mask] + (-1)*(1-yte[mask]))
    se=math.sqrt(wr*(1-wr)/n)
    print(f"  {name}: n={n} | WIN {wr:.1%} (±{1.96*se:.1%}) | precio {ap:.1%} | EV {ev:+.1%}"
          + ("  <== +EV significativo" if wr-1.96*se>ap else ""))

print("\n=== TEST HELD-OUT ===")
report("TODAS (fade siempre)", np.ones(len(yte),bool))
report("SELECTOR: modelo dice P>precio", pte_hat>pte)
report("SELECTOR fuerte: P>precio+0.05", pte_hat>pte+0.05)
# importancia de features (|peso| estandarizado)
imp=sorted(zip(FEATS,w),key=lambda x:-abs(x[1]))[:6]
print("\n  features más influyentes:", ", ".join(f"{k}({v:+.2f})" for k,v in imp))
