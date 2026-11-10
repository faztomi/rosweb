"""Monte Carlo de facturación opcional, con parámetros explícitos (no predicción ROS)."""
from __future__ import annotations
import math
import random


def simulate(mean_annual, target_annual, cv=0.30, n=10000, seed=2026):
    mean, target, cv = float(mean_annual),float(target_annual),float(cv)
    if not (mean>0 and target>0 and 0<=cv<=2 and 100<=n<=100000):
        raise ValueError('Parámetros de simulación fuera de rango')
    sigma=math.sqrt(math.log1p(cv**2))
    mu=math.log(mean)-sigma*sigma/2
    rng=random.Random(int(seed))
    sample=sorted(math.exp(rng.gauss(mu,sigma)) for _ in range(int(n)))
    def quant(p):
        pos=(len(sample)-1)*p
        lo=int(pos); hi=min(lo+1,len(sample)-1)
        return sample[lo]+(sample[hi]-sample[lo])*(pos-lo)
    return {'p10':quant(.10),'p50':quant(.50),'p90':quant(.90),
            'average':sum(sample)/len(sample),'prob_target':sum(x>=target for x in sample)/len(sample),
            'prob_80pct':sum(x>=.8*target for x in sample)/len(sample),
            'scenarios':len(sample),'seed':int(seed),'cv':cv}
