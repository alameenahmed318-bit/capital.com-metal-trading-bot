"""Offline research only: explicit exit state, symmetric signed target actions."""
import numpy as np
from core import target_for

ACTIONS=("HOLD","BUY","SELL","ADD","COOL","REDUCE","CLOSE","RECOVERY_REDUCE")

def make_env(b,x,atr,valid,split,slippage=.10,commission_atr=.02,seed=42,random_start=False,scale=None):
    import gymnasium as gym
    from gymnasium import spaces
    lo,hi=split
    if hi-lo<100:raise ValueError("Need >=100 bars per partition")
    scale=float(np.median(atr[:lo])) if scale is None else float(scale)
    if not np.isfinite(scale) or scale<=0:raise ValueError("Invalid training scale")
    class Env(gym.Env):
        action_space=spaces.Discrete(len(ACTIONS))
        observation_space=spaces.Box(-20.,20.,shape=(x.shape[1]+8,),dtype=np.float32)
        def unreal(self):return self.pos*(b[self.i,4]-self.entry)/scale if self.pos else 0.
        def obs(self):
            u=self.unreal()
            return np.clip(np.r_[x[self.i],self.pos/2,u,self.peak_trade,
                self.peak_trade-u,self.age/20,self.dd/20,float(valid[self.i]),self.net/50],-20,20).astype(np.float32)
        def reset(self,*,seed=None,options=None):
            super().reset(seed=seed)
            self.i=int(self.np_random.integers(lo,max(lo+1,hi-128))) if random_start else lo
            self.end=min(hi,self.i+256) if random_start else hi
            self.pos=0;self.entry=0.;self.net=0.;self.peak=0.;self.dd=0.;self.max_dd=0.
            self.age=0;self.peak_trade=0.;self.fills=0;self.turnover=0;self.forced=0
            self.opens={"BUY":0,"SELL":0};self.actions={k:0 for k in ACTIONS}
            return self.obs(),{}
        def step(self,action):
            i=self.i;j=i+1;p=self.pos;u=self.unreal();a=int(action)
            gap=b[j,0]-b[i,0]!=60
            # Risk bounds for offline comparison only, not a live stop or owner setting.
            force=bool(p and (self.age>=15 or u<=-2.5*atr[i]/scale or gap))
            target=0 if force or gap else target_for(a,p,u) if valid[i] else p
            cost=(b[j,6]*b[j,7]*.5+(slippage+commission_atr)*atr[i])/scale
            delta=target-p
            reward=p*(b[j,1]-b[i,4])/scale+target*(b[j,4]-b[j,1])/scale-abs(delta)*cost
            if delta:
                self.fills+=1;self.turnover+=abs(delta)
                if force:self.forced+=1
                else:self.actions[ACTIONS[a]]+=1
                if not p and target:self.opens["BUY" if target>0 else "SELL"]+=1
                if target and abs(target)>abs(p):
                    self.entry=(abs(p)*self.entry+abs(delta)*b[j,1])/abs(target)
                    self.peak_trade=0.
                elif not target:self.entry=0.;self.peak_trade=0.
            self.age=(self.age+1 if p else 1) if target else 0
            self.pos=target;self.i=j
            self.peak_trade=max(self.peak_trade,self.unreal()) if target else 0.
            done=j>=self.end-1
            if done and target:
                # Terminal exit at bar close, not a free liquidation.
                reward-=abs(target)*cost;self.fills+=1;self.turnover+=abs(target)
                self.pos=0;self.entry=0.;self.age=0;self.peak_trade=0.
            old_dd=self.dd;self.net+=reward;self.peak=max(self.peak,self.net);self.dd=self.peak-self.net
            self.max_dd=max(self.dd,self.max_dd)
            info={"net_atr_units":float(self.net),"max_drawdown_atr_units":float(self.max_dd),
                "fills":self.fills,"turnover":self.turnover,"forced_exits":self.forced,
                "entries":dict(self.opens),"actions":dict(self.actions)}
            # Cash remains a valid action; no artificial reward for opening.
            shaped=reward-.01*abs(self.pos)-.04*max(0.,self.dd-old_dd)
            return self.obs(),float(shaped),done,False,info
    e=Env()
    e.reset(seed=seed)
    return e

def bounded_momentum(obs):
    d=1 if obs[1]>.5 else -1 if obs[1]<-.5 else 0
    pos=obs[-8]
    if not pos:return 1 if d>0 else 2 if d<0 else 0
    return 6 if d*pos<0 or (obs[-6]>.7 and obs[-5]>.35) else 0
