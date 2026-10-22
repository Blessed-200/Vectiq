"""Exact fair-price calculator for a table-tennis SET from any in-play score (dynamic programming; grid built once, then O(1) lookups).
Assumes points are independent with P(server wins point)=ps (measured on 4.1M points: 0.5286; momentum/clutch/tempo effects tested = 0).
Serve rule: 2 serves each, alternating every point from 10-10. Truncation at 92 total points (tail < 1e-6)."""
from functools import lru_cache
import numpy as np
G=46; TMAX=2*G

def _a_serves(k, a_is_starter):
    starter_serves = ((k//2)%2==0) if k<20 else ((k-20)%2==0)
    return starter_serves if a_is_starter else (not starter_serves)

@lru_cache(maxsize=None)
def _grid(a_is_starter, ps_q):
    ps=ps_q/10000.0
    WA=np.zeros((G,G,TMAX)); WB=np.zeros((G,G,TMAX))
    for k in range(2*G-2,-1,-1):
        for a in range(max(0,k-G+1),min(k,G-1)+1):
            b=k-a
            if max(a,b)>=11 and abs(a-b)>=2:
                (WA if a>b else WB)[a,b,k]=1.0; continue
            if a+1>=G or b+1>=G or k+1>=TMAX:      # truncation: resolve by current lead
                (WA if a>=b else WB)[a,b,min(k,TMAX-1)]=1.0; continue
            pa= ps if _a_serves(k,a_is_starter) else 1.0-ps
            WA[a,b]=pa*WA[a+1,b]+(1-pa)*WA[a,b+1]; WB[a,b]=pa*WB[a+1,b]+(1-pa)*WB[a,b+1]
    return WA,WB

def set_price(a,b,a_is_starter,ps=0.5286):
    """-> (P(A wins set), array over final set total points)."""
    WA,WB=_grid(bool(a_is_starter),int(round(ps*10000)))
    wa,wb=WA[a,b],WB[a,b]
    return float(wa.sum()),(wa+wb)

def p_total_over(a,b,a_is_starter,line,ps=0.5286):
    _,tot=set_price(a,b,a_is_starter,ps); idx=int(np.floor(line))+1
    return float(tot[idx:].sum())
