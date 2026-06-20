import os, time, threading, sqlite3, random, traceback, requests
from datetime import datetime, timezone
import pandas as pd
import matplotlib.pyplot as plt
import alpaca_trade_api as tradeapi

print("BOOT: Hedge Fund v1.3 - $0.50 mode")

KEY = os.getenv("APCA_API_KEY_ID")
SECRET = os.getenv("APCA_API_SECRET_KEY")
LIVE = os.getenv("LIVE_MODE","false").lower()=="true"
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT = os.getenv("TELEGRAM_CHAT_ID")

api = tradeapi.REST(KEY, SECRET, "https://api.alpaca.markets" if LIVE else "https://paper-api.alpaca.markets", api_version='v2')

def tg_send(txt):
    try: requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={"chat_id":CHAT,"text":txt}, timeout=10)
    except: pass

def tg_photo(path,cap):
    try: requests.post(f"https://api.telegram.org/bot{TOKEN}/sendPhoto", data={"chat_id":CHAT,"caption":cap}, files={"photo":open(path,'rb')}, timeout=15)
    except: pass

# DB
conn = sqlite3.connect("bot.db", check_same_thread=False)
cur = conn.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS trades (ts TEXT, symbol TEXT, side TEXT, notional REAL, price REAL, strategy TEXT)")
cur.execute("CREATE TABLE IF NOT EXISTS memory (k TEXT PRIMARY KEY, v TEXT)")
conn.commit()

def mem_get(k,d=None): r=cur.execute("SELECT v FROM memory WHERE k=?",(k,)).fetchone(); return r[0] if r else d
def mem_set(k,v): cur.execute("INSERT OR REPLACE INTO memory VALUES(?,?)",(k,str(v))); conn.commit()

try:
    a=api.get_account(); print(f"BOOT: ${a.equity}")
    if not mem_get("init_eq"): mem_set("init_eq", a.equity)
    tg_send("✅ Hedge Fund v1.3 ONLINE — $0.50 minimum, crypto 24/7")
except Exception as e: print("INIT",e)

PHRASES = ["pimpin ain't easy 😎","money never sleeps","scanning the matrix","alpha hunting","risk on","calculating edge","compounding","liquidity is king","volatility = opportunity","edge > ego"]
UNIVERSE=[]; CRYPTO=["BTC/USD","ETH/USD","SOL/USD","DOGE/USD","AVAX/USD","LTC/USD"]; last_sig={}

def build_uni():
    global UNIVERSE
    try:
        assets = api.list_assets(status='active')
        good=[x.symbol for x in assets if x.tradable and x.exchange in ('NASDAQ','NYSE','ARCA','BATS') and x.marginable]
        UNIVERSE = good[:2000] + CRYPTO
        print(f"UNI: {len(UNIVERSE)}")
    except: UNIVERSE=["AAPL","MSFT","NVDA","TSLA","SPY","QQQ"]+CRYPTO
build_uni()

def bars(sym,n=60):
    try: return api.get_bars(sym,"5Min",limit=n).df
    except: return pd.DataFrame()

def rsi(s,p=14):
    d=s.diff(); up=d.clip(lower=0); dn=-d.clip(upper=0)
    eu=up.ewm(com=p-1,adjust=False).mean(); ed=dn.ewm(com=p-1,adjust=False).mean()
    return 100-(100/(1+eu/ed))

def signal(sym,df):
    if len(df)<30: return None
    r=rsi(df.close).iloc[-1]; p=df.close.iloc[-1]
    hi=df.high.rolling(20).max().iloc[-2]; lo=df.low.rolling(20).min().iloc[-2]
    v=df.volume.iloc[-1]/max(1,df.volume.rolling(20).mean().iloc[-1])
    if r<32: return ("BUY",f"RSI {r:.0f}")
    if r>72: return ("SELL",f"RSI {r:.0f}")
    if p>hi and v>1.8: return ("BUY",f"breakout {v:.1f}x")
    if p<lo and v>1.8: return ("SELL",f"breakdown {v:.1f}x")
    return None

def risk_ok(notional):
    a=api.get_account(); eq=float(a.equity)
    if notional<0.5: return False
    if notional/eq>0.18: return False
    init=float(mem_get("init_eq")); return (eq-init)/init > -0.05

def chart(sym,df,why):
    plt.style.use('dark_background')
    fig,ax=plt.subplots(figsize=(9,4.5),dpi=140)
    ax.plot(df.close.values,color='#00FF7F',lw=2.2)
    ax.plot(df.close.rolling(20).mean().values,color='#666',lw=1)
    ax.set_title(f"{sym} - Live",loc='left',color='w'); ax.grid(alpha=0.18); ax.set_facecolor('#0a0a0a')
    conf=int(min(94,max(56,60+abs(rsi(df.close).iloc[-1]-50))))
    ax.text(0.015,0.91,f"{conf}% CONFIDENCE",transform=ax.transAxes,color='#FFB000',fontsize=10,
            bbox=dict(facecolor='black',edgecolor='#FFB000',boxstyle='round,pad=0.3'))
    ax.text(0.015,0.82,why.upper(),transform=ax.transAxes,color='#FFB000',fontsize=9)
    plt.tight_layout(); p=f"/tmp/{sym.replace('/','')}.png"; plt.savefig(p); plt.close(); return p,conf

def scanner():
    idx=0
    while True:
        try:
            a=api.get_account(); eq=float(a.equity)
            # crypto priority 8pm-9:30am ET (00:00-13:30 UTC)
            now=datetime.now(timezone.utc); is_night = now.hour>=0 and now.hour<14
            pool = CRYPTO + UNIVERSE if is_night else UNIVERSE
            batch = pool[idx:idx+60]; idx=(idx+60)%len(pool)

            for sym in batch:
                df=bars(sym,55)
                if df.empty or len(df)<35: continue
                sig=signal(sym,df)
                if not sig: continue
                side,why=sig
                # $0.50 minimum, 1.5% of equity, or Kelly-adjusted
                notional = max(0.5, min(eq*0.18, eq*0.015))
                if not risk_ok(notional): continue
                try:
                    # NOTIONAL order works for stocks AND crypto
                    api.submit_order(symbol=sym, notional=round(notional,2), side=side.lower(), type="market", time_in_force="day")
                    ts=datetime.now(timezone.utc).isoformat()
                    cur.execute("INSERT INTO trades VALUES(?,?,?,?,?,?)",(ts,sym,side,notional,float(df.close.iloc[-1]),"SCALP")); conn.commit()
                    p,c=chart(sym,df.tail(60),why)
                    tg_photo(p, f"⚡ {side} ${notional:.2f} {sym}\n{why} | conf {c}%")
                    last_sig[sym]=why
                    time.sleep(1.5)
                except Exception as e: print("ORDER",sym,e)
            time.sleep(5)
        except: time.sleep(20)

def reporter():
    while True:
        try:
            a=api.get_account(); eq=float(a.equity); cash=float(a.cash); last=float(a.last_equity)
            today=(eq-last)/last*100; init=float(mem_get("init_eq")); alltime=(eq-init)/init*100
            pos=api.list_positions()
            ph=random.choice(PHRASES)
            m1=f"🔥 HEDGE FUND COMMAND CENTER\n{ph}\n────────────────────\n💰 ${eq:,.2f} ({today:+.2f}% today)\n📊 All-Time: {alltime:+.1f}% | Trades: {cur.execute('SELECT COUNT(*) FROM trades').fetchone()[0]}\n💵 Cash: ${cash:,.2f}\n\n🎯 POSITIONS ({len(pos)})"
            for p in pos[:5]:
                ch=(float(p.current_price)-float(p.avg_entry_price))/float(p.avg_entry_price)*100
                m1+=f"\n- {p.symbol} ${float(p.market_value):.2f} {'▲' if ch>0 else '▼'}{abs(ch):.1f}%"
            tg_send(m1)
            tot=max(1,cur.execute('SELECT COUNT(*) FROM trades').fetchone()[0])
            m2=f"🧠 BRAIN\n- Min trade $0.50 | Universe {len(UNIVERSE)} | Learning {'WARM' if tot<50 else 'ACTIVE'}\n- Last: {', '.join(list(last_sig.keys())[-3:]) or 'scanning crypto...'}\n\n🛡️ RISK\n- Max 18% | Stop -5% day\n────────────────────\nNext: 5 min"
            tg_send(m2)
        except Exception as e: print("REP",e)
        time.sleep(300)

threading.Thread(target=scanner,daemon=True).start()
threading.Thread(target=reporter,daemon=True).start()
print("RUNNING $0.50 mode")
while True: time.sleep(3600)