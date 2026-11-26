"""Offline, single-symbol M1 simulation. No network, broker or server imports."""
import json
from pathlib import Path
import numpy as np

ACTIONS=("HOLD","BUY","SELL","ADD","COOL","REDUCE","CLOSE","RECOVERY_REDUCE")
def load_bars(path):
    data=json.loads(Path(path).read_text(encoding="utf-8"))
    cols=data["columns"]
    b=np.asarray(data["bars"],dtype=np.float64)
    idx=[cols.index(k) for k in ("time","open","high","low","close","tick_volume","spread_points","point_size")]
    b=b[:,idx]
    if not np.isfinite(b).all() or len(b)<800 or np.any(np.diff(b[:,0])<=0):
        raise ValueError("Need at least 800 strictly ordered valid M1 bars")
    if np.any(b[:,1:5]<=0) or np.any(b[:,2]<np.maximum(b[:,1],b[:,4])) or np.any(b[:,3]>np.minimum(b[:,1],b[:,4])) or np.any(b[:,6]<0) or np.any(b[:,7]<=0):
        raise ValueError("Invalid OHLC or spread metadata")
    return data["symbol"],b

def features(b):
    # Feature i uses only data through bar i close. Never use next bar here.
    c=b[:,4]; prev=np.r_[c[0],c[:-1]]
    tr=np.maximum(b[:,2]-b[:,3],np.maximum(abs(b[:,2]-prev),abs(b[:,3]-prev)))
    atr=np.array([max(np.mean(tr[max(0,i-13):i+1]),1e-9) for i in range(len(b))])
    def ret(k):
        return (c-c[np.maximum(0,np.arange(len(c))-k)])/atr
    x=np.column_stack([ret(1),ret(5),ret(15),(b[:,4]-b[:,1])/atr,
        (b[:,2]-b[:,3])/atr,b[:,6]*b[:,7]/atr,
        np.log1p(np.maximum(b[:,5],0))/10,
        np.sin(b[:,0]/86400*2*np.pi),np.cos(b[:,0]/86400*2*np.pi)])
    # Gaps are never filled with fabricated bars. No decisions until 32 fresh bars.
    age=0; valid=np.zeros(len(b),dtype=bool)
    for i in range(1,len(b)):
        age=age+1 if b[i,0]-b[i-1,0]==60 else 0
        valid[i]=age>=32
    return np.clip(x,-12,12).astype(np.float32),atr,valid

def segments(n):
    # First 35% fit features; then 35% RL training, 15% validation, 15% untouched test.
    return {"feature_fit":(0,int(.35*n)),"train":(int(.35*n)+5,int(.70*n)),
            "validation":(int(.70*n)+5,int(.85*n)),"test":(int(.85*n)+5,n)}

def target_for(action,pos,unreal):
    # One signed position, not hedge or exact MT5 recovery basket.
    if action==1: return 1 if pos==0 else pos
    if action==2: return -1 if pos==0 else pos
    sign=int(np.sign(pos))
    if action==3 and pos and unreal>0: return sign*min(2,abs(pos)+1)
    if action==4 and pos and unreal<0: return sign*min(2,abs(pos)+1)
    if action==5 and pos: return pos-sign
    if action==6:return 0
    if action==7 and unreal<0:return 0
    return pos

def make_env(b,x,atr,valid,split,slippage=.10,commission_atr=.02):
    import gymnasium as gym
    from gymnasium import spaces
    lo,hi=split
    if hi-lo<50: raise ValueError("Split too short")
    scale=float(np.median(atr[:max(1,int(.35*len(b)))]))
    class TradingEnv(gym.Env):
        metadata={"render_modes":[]}
        action_space=spaces.Discrete(len(ACTIONS))
        observation_space=spaces.Box(-20.,20.,shape=(x.shape[1]+4,),dtype=np.float32)
        def obs(self):
            unreal=self.pos*(b[self.i,4]-self.entry)/scale
            return np.clip(np.r_[x[self.i],self.pos/2,unreal,self.drawdown/10,
                float(valid[self.i])],-20,20).astype(np.float32)
        def reset(self,*,seed=None,options=None):
            super().reset(seed=seed)
            self.i=lo;self.pos=0;self.entry=0.;self.net=0.;self.peak=0.;self.drawdown=0.;self.max_dd=0.
            self.turnover=0;self.fills=0;self.action_counts={a:0 for a in ACTIONS}
            return self.obs(),{}
        def step(self,action):
            action=int(action);i=self.i;j=i+1
            p=int(self.pos); close=b[i,4];op=b[j,1]; nxt=b[j,4]
            unreal=p*(close-self.entry)/scale
            gap=b[j,0]-b[i,0]!=60
            target=target_for(action,p,unreal) if valid[i] and not gap else (0 if gap else p)
            # Decision at i close; execution at j open; existing exposure incurs gap P&L.
            cost=(b[j,6]*b[j,7]*.5+(slippage+commission_atr)*atr[i])/scale
            delta=target-p
            reward=p*(op-close)/scale+target*(nxt-op)/scale-abs(delta)*cost
            if delta:
                self.turnover+=abs(delta);self.fills+=1
                self.action_counts[ACTIONS[action]]+=1
                if target and abs(target)>abs(p):
                    self.entry=(abs(p)*self.entry+abs(delta)*op)/abs(target)
                elif not target:self.entry=0.
            self.pos=target;self.i=j
            done=j>=hi-1
            if done and target:
                reward-=abs(target)*cost;self.turnover+=abs(target);self.fills+=1;self.pos=0;self.entry=0.
            self.net+=reward;self.peak=max(self.peak,self.net);self.drawdown=self.peak-self.net
            self.max_dd=max(self.max_dd,self.drawdown)
            info={"net_atr_units":float(self.net),"max_drawdown_atr_units":float(self.max_dd),
                "turnover":int(self.turnover),"fills":int(self.fills),"actions":dict(self.action_counts)}
            # Reward penalizes increased drawdown/exposure; report unpenalized net separately.
            return self.obs(),float(reward-.005*abs(self.pos)-.002*self.drawdown),done,False,info
    return TradingEnv()

def evaluate(env,policy):
    obs,_=env.reset();done=False
    while not done:
        obs,_,done,truncated,info=env.step(policy(obs))
        done=done or truncated
    return info

def momentum(obs):
    # Reproducible simple baseline; not the deployed AL-ZOOL policy.
    direction=1 if obs[1]>.5 else -1 if obs[1]<-.5 else 0
    pos=obs[-4]
    if pos==0:return 1 if direction>0 else 2 if direction<0 else 0
    if direction*pos<0:return 6
    return 0
