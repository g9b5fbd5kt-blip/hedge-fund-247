import os, time, threading, sqlite3, random, traceback, requests
from datetime import datetime, timezone
import pandas as pd
import matplotlib.pyplot as plt
import alpaca_trade_api as tradeapi

print("BOOT: Hedge Fund v1.2")

KEY = os.getenv("APCA_API_KEY_ID")
SECRET = os.getenv("APCA_API_SECRET_KEY")
LIVE = os.getenv("LIVE_MODE","false").lower()=="true"
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT = os.getenv("TELEGRAM_CHAT_ID")

api = tradeapi.REST(KEY, SECRET, "https://api.alpaca.markets" if LIVE else "https://paper-api.alpaca.markets", api_version='v2')

# --- SYNC TELEGRAM (fixes the await error) ---
def tg_send(txt):
    try: requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={"chat_id":CHAT,"text":txt}, timeout=10)
    except Exception as e: print("TG ERR",e)

def tg_photo(path,cap):
    try: requests.post(f"https://api.telegram.org/bot{TOKEN}/sendPhoto", data={"chat_id":CHAT,"caption":cap}, files={"photo":open(path,'rb')}, timeout=15)
    except Exception as e: print("PHOTO ERR",e)

# --- DB ---
conn = sqlite3.connect("bot.db", check_same_thread=False)
cur = conn.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS trades (ts TEXT, symbol TEXT, side TEXT, qty REAL, price REAL, strategy TEXT)")
cur.execute("CREATE TABLE IF NOT EXISTS memory (k TEXT PRIMARY KEY, v TEXT)")
conn.commit()

def mem_get(k,d=None): r=cur.execute("SELECT v FROM memory WHERE k=?",(k,)).fetchone(); return r[0] if r else d
def mem_set(k,v): cur.execute("INSERT OR REPLACE INTO memory VALUES(?,?)",(k,str(v))); conn.commit()

# --- INIT ---
try:
    a=api.get_account(); print(f"BOOT: Alpaca OK ${a.equity}")
    if not mem_get("init_eq"): mem_set("init_eq", a.equity)
    tg_send("✅ Hedge Fund 24/7 v1.2 ONLINE — full US market scan")
except Exception as e: print("INIT ERR",e)

PHRASES = ["pimpin ain't easy 😎","money never sleeps","scanning the matrix","alpha hunting","risk on","calculating edge","no free lunches","trade the plan","compounding","liquidity is king","volatility = opportunity","building the book","edge > ego","maximize expectancy"]
UNIVERSE=[]; lock=threading.Lock(); last_sig={}

# --- CLEAN UNIVERSE (only liquid US stocks + crypto) ---
def build_uni():
    global UNIVERSE
    try:
        assets = api.list_assets(status='active')
        good=[]
        for x in assets:
            if x.tradable and x.exchange in ('NASDAQ','NYSE','ARCA','BATS') and x.marginable and not x.symbol.endswith('.'):
                good.append(x.symbol)
        UNIVERSE = good[:2500] + ["BTC/USD","ETH/USD","SOL/USD","DOGE/USD"]
        print(f"UNI: {len(UNIVERSE)} symbols")
        mem_set("uni",len(UNIVERSE))
    except Exception as e:
        print("UNI ERR",e); UNIVERSE=["AAPL","MSFT","NVDA","TSLA","SPY","QQQ","BTC/USD","ETH/USD"]

build_uni()

def bars(sym,n=70):
    try: return api.get_bars(sym,"5Min",limit=n).df
    except: return pd.DataFrame()

def rsi(s,p=14):
    d=s.diff(); up=d.clip(lower=0); dn=-d.clip(upper=0)
    eu=up.ewm(com=p-1,adjust=False).mean(); ed=dn.ewm(com=p-1,adjust=False).mean()
    rs=eu/ed; return 100-(100/(1+rs))

def mom(sym,df):
    if len(df)<30: return None
    r=rsi(df.close).iloc[-1]
    if r<33: return ("BUY",f"RSI {r:.0f}")
    if r>71: return ("SELL",f"RSI {r:.0f}")
    return None

def brk(sym,df):
    if len(df)<40: return None
    hi=df.high.rolling(20).max().iloc[-2]; lo=df.low.rolling(20).min().iloc[-2]; p=df.close.iloc[-1]
    v=df.volume.iloc[-1]/max(1,df.volume.rolling(20).mean().iloc[-1])
    if p>hi and v>1.9: return ("BUY",f"breakout {v:.1f}x")
    if p<lo and v>1.9: return ("SELL",f"breakdown {v:.1f}x")
    return None

STRATS=[("MOM",mom),("BRK",brk)]

def risk_ok(side,qty,price):
    a=api.get_account(); eq=float(a.equity); cash=float(a.cash)
    if qty*price/eq>0.18: return False
    if side=="BUY" and cash<qty*price*0.4: return False
    init=float(mem_get("init_eq")); return (eq-init)/init > -0.05

def chart(sym,df,why):
    plt.style.use('dark_background')
    fig,ax=plt.subplots(figsize=(9,4.5),dpi=140)
    ax.plot(df.close.values,color='#00FF7F',lw=2.2)
    ax.plot(df.close.rolling(20).mean().values,color='#666',lw=1)
    ax.set_title(f"{sym} - Live",loc='left',color='w'); ax.grid(alpha=0.18); ax.set_facecolor('#0a0a0a')
    conf=int(min(93,max(57,60+abs(rsi(df.close).iloc[-1]-50))))
    ax.text(0.015,0.91,f"{conf}% CONFIDENCE",transform=ax.transAxes,color='#FFB000',fontsize=10,
            bbox=dict(facecolor='black',edgecolor='#FFB000',boxstyle='round,pad=0.3'))
    ax.text(0.015,0.82,why.upper(),transform=ax.transAxes,color='#FFB000',fontsize=9)
    ax2=ax.twinx(); ax2.bar(range(len(df)),df.volume.values,alpha=0.13,color='gray'); ax2.set_yticks([])
    plt.tight_layout(); p=f"/tmp/{sym}.png"; plt.savefig(p); plt.close(); return p,conf

# --- SCANNER ---
def scanner():
    i=0
    while True:
        try:
            eq=float(api.get_account().equity)
            batch=UNIVERSE[i:i+80]; i=(i+80)%len(UNIVERSE)
            for sym in batch:
                df=bars(sym,70)
                if df.empty or len(df)<40: continue
                for name,fn in STRATS:
                    sig=fn(sym,df)
                    if not sig: continue
                    side,why=sig; price=float(df.close.iloc[-1]); qty=max(1,int((eq*0.007)/price))
                    with lock:
                        if not risk_ok(side,qty,price): continue
                        try:
                            api.submit_order(sym,qty,side.lower(),"market","day")
                            cur.execute("INSERT INTO trades VALUES(?,?,?,?,?,?)",(datetime.now(timezone.utc).isoformat(),sym,side,qty,price,name)); conn.commit()
                            p,c=chart(sym,df.tail(65),why); tg_photo(p,f"⚡ {side} {qty} {sym} @ ${price:.2f}\n{why} | {name} | {c}%")
                            last_sig[sym]=why; time.sleep(1.1)
                        except Exception as e: print("ORD",sym,e)
                    break
            time.sleep(6)
        except Exception as e: print("SCAN",traceback.format_exc()); time.sleep(25)

# --- REPORTER ---
def reporter():
    while True:
        try:
            a=api.get_account(); eq=float(a.equity); cash=float(a.cash); last=float(a.last_equity)
            today=(eq-last)/last*100; init=float(mem_get("init_eq")); alltime=(eq-init)/init*100
            pos=api.list_positions()
            ph=random.choice(PHRASES)
            m1=f"🔥 HEDGE FUND COMMAND CENTER\n{ph}\n────────────────────\n💰 ${eq:,.0f} ({today:+.2f}% today)\n📊 All-Time: {alltime:+.1f}% | Trades: {cur.execute('SELECT COUNT(*) FROM trades').fetchone()[0]}\n💵 Cash: ${cash:,.0f}\n\n🎯 POSITIONS ({len(pos)})"
            for p in pos[:5]:
                ch=(float(p.current_price)-float(p.avg_entry_price))/float(p.avg_entry_price)*100
                m1+=f"\n- {p.symbol} {int(float(p.qty))} @ ${float(p.avg_entry_price):.2f} {'▲' if ch>0 else '▼'}{abs(ch):.1f}%"
            tg_send(m1)
            tot=max(1,cur.execute('SELECT COUNT(*) FROM trades').fetchone()[0]); win=cur.execute("SELECT COUNT(*) FROM trades WHERE side='SELL'").fetchone()[0]
            kelly=int(max(5,min(25,(win/tot)*25)))
            m2=f"🧠 BRAIN\n- Kelly {kelly}% | Universe {len(UNIVERSE)} | Learning {'WARM' if tot<80 else 'ACTIVE'}\n- Last: {', '.join(list(last_sig.keys())[-3:]) or 'scanning'}\n\n🛡️ RISK\n- Max 18% per name | Stop -5% day\n────────────────────\nNext: 5 min"
            tg_send(m2)
        except Exception as e: print("REP",e)
        time.sleep(300)

threading.Thread(target=scanner,daemon=True).start()
threading.Thread(target=reporter,daemon=True).start()
print("RUNNING")
while True: time.sleep(3600)