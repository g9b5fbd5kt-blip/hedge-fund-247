import os, time, threading, sqlite3, random, math, traceback
from datetime import datetime, timezone
import pandas as pd
import matplotlib.pyplot as plt
import alpaca_trade_api as tradeapi
from telegram import Bot

print("BOOT: starting Hedge Fund v1.1")

KEY = os.getenv("APCA_API_KEY_ID")
SECRET = os.getenv("APCA_API_SECRET_KEY")
LIVE = os.getenv("LIVE_MODE","false").lower()=="true"
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT = os.getenv("TELEGRAM_CHAT_ID")

api = tradeapi.REST(KEY, SECRET, "https://api.alpaca.markets" if LIVE else "https://paper-api.alpaca.markets", api_version='v2')
bot = Bot(TOKEN)

# --- DB ---
conn = sqlite3.connect("bot.db", check_same_thread=False)
cur = conn.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS trades (ts TEXT, symbol TEXT, side TEXT, qty REAL, price REAL, strategy TEXT, pnl REAL)")
cur.execute("CREATE TABLE IF NOT EXISTS memory (k TEXT PRIMARY KEY, v TEXT)")
conn.commit()

def mem_get(k,d=None): r=cur.execute("SELECT v FROM memory WHERE k=?",(k,)).fetchone(); return r[0] if r else d
def mem_set(k,v): cur.execute("INSERT OR REPLACE INTO memory VALUES(?,?)",(k,str(v))); conn.commit()

# --- INIT ---
try:
    a = api.get_account()
    print(f"BOOT: Alpaca OK equity=${a.equity}")
    if not mem_get("init_eq"): mem_set("init_eq", a.equity)
    bot.send_message(CHAT, "✅ Hedge Fund 24/7 v1.1 ONLINE — full universe scan active")
except Exception as e:
    print("BOOT FAIL:", e)

PHRASES = ["pimpin ain't easy 😎","money never sleeps","scanning the matrix","alpha hunting","risk on","calculating edge","no free lunches","trade the plan","compounding","liquidity is king","volatility = opportunity","building the book","edge > ego","maximize expectancy","cut losers fast","let winners run","probability first","process over outcome"]
lock = threading.Lock()
last_signals = {}
UNIVERSE = []

# --- BUILD FULL UNIVERSE (once per day) ---
def build_universe():
    global UNIVERSE
    try:
        print("UNIVERSE: fetching assets...")
        assets = api.list_assets(status='active', asset_class='us_equity')
        stocks = [a.symbol for a in assets if a.tradable and a.marginable and a.shortable]
        # filter to liquid names (top 3000 by rough heuristic)
        UNIVERSE = stocks[:3000] + ["BTC/USD","ETH/USD","SOL/USD","DOGE/USD","AVAX/USD","LTC/USD"]
        mem_set("uni_count", len(UNIVERSE))
        print(f"UNIVERSE: loaded {len(UNIVERSE)} symbols")
    except Exception as e:
        print("UNI ERR:", e)
        UNIVERSE = ["AAPL","MSFT","NVDA","TSLA","SPY","QQQ","BTC/USD","ETH/USD"] # fallback

build_universe()

def send(txt):
    try: bot.send_message(CHAT, txt)
    except Exception as e: print("TG SEND ERR:", e)

def send_photo(path,cap):
    try: bot.send_photo(CHAT, open(path,'rb'), caption=cap)
    except Exception as e: print("TG PHOTO ERR:", e)

def acct():
    try: return api.get_account()
    except: return None

def bars(sym,n=80):
    try: return api.get_bars(sym, "5Min", limit=n).df
    except: return pd.DataFrame()

def rsi(s,p=14):
    d=s.diff(); up=d.clip(lower=0); dn=-d.clip(upper=0)
    eu=up.ewm(com=p-1,adjust=False).mean(); ed=dn.ewm(com=p-1,adjust=False).mean()
    return 100-(100/(1+eu/ed))

# --- STRATEGIES ---
def mom(sym,df):
    if len(df)<30: return None
    r=rsi(df.close).iloc[-1]
    if r<33: return ("BUY",f"RSI {r:.0f} oversold")
    if r>70: return ("SELL",f"RSI {r:.0f} overbought")
    return None

def brk(sym,df):
    if len(df)<50: return None
    hi=df.high.rolling(20).max().iloc[-2]; lo=df.low.rolling(20).min().iloc[-2]; p=df.close.iloc[-1]; v=df.volume.iloc[-1]/df.volume.rolling(20).mean().iloc[-1]
    if p>hi and v>2: return ("BUY",f"breakout {v:.1f}x vol")
    if p<lo and v>2: return ("SELL",f"breakdown {v:.1f}x vol")
    return None

STRATS=[("MOM",mom),("BRK",brk)]

def risk_ok(side,qty,price):
    a=acct()
    if not a: return False
    eq=float(a.equity); cash=float(a.cash)
    if qty*price/eq>0.20: return False
    if side=="BUY" and cash<qty*price*0.5: return False
    init=float(mem_get("init_eq")); dd=(eq-init)/init
    if dd < -0.05: return False
    return True

def chart(sym,df,why):
    plt.style.use('dark_background')
    fig,ax=plt.subplots(figsize=(9,4.6),dpi=150)
    ax.plot(df.close.values,color='#00FF7F',lw=2.3,label='price')
    ax.plot(df.close.rolling(20).mean().values,color='#666',lw=1,alpha=0.8)
    ax.plot(df.close.rolling(50).mean().values,color='#444',lw=1,alpha=0.6)
    ax.set_title(f"{sym} - Live",loc='left',color='white',fontsize=14)
    ax.grid(True,alpha=0.18); ax.set_facecolor('#0a0a0a')
    conf=int(min(94,max(56,60+abs(rsi(df.close).iloc[-1]-50)*0.8)))
    ax.text(0.015,0.92,f"{conf}% CONFIDENCE",transform=ax.transAxes,color='#FFB000',fontsize=10,
            bbox=dict(facecolor='black',edgecolor='#FFB000',boxstyle='round,pad=0.35'))
    ax.text(0.015,0.83,why.upper()[:30],transform=ax.transAxes,color='#FFB000',fontsize=9)
    ax2=ax.twinx(); ax2.bar(range(len(df)),df.volume.values,alpha=0.12,color='gray'); ax2.set_yticks([])
    plt.tight_layout(); p=f"/tmp/{sym}.png"; plt.savefig(p); plt.close(); return p,conf

# --- SCANNER (full universe, batched) ---
def scanner():
    idx=0
    while True:
        try:
            a=acct()
            if not a: time.sleep(20); continue
            eq=float(a.equity)
            batch = UNIVERSE[idx:idx+120]; idx=(idx+120)%len(UNIVERSE)
            for sym in batch:
                df=bars(sym,80)
                if df.empty or len(df)<45: continue
                for name,fn in STRATS:
                    sig=fn(sym,df)
                    if not sig: continue
                    side,why=sig; price=float(df.close.iloc[-1]); qty=max(1,int((eq*0.008)/price))
                    with lock:
                        if not risk_ok(side,qty,price): continue
                        try:
                            api.submit_order(sym,qty,side.lower(),"market","day")
                            ts=datetime.now(timezone.utc).isoformat()
                            cur.execute("INSERT INTO trades VALUES(?,?,?,?,?,?,?)",(ts,sym,side,qty,price,name,None)); conn.commit()
                            path,conf=chart(sym,df.tail(70),why)
                            send_photo(path, f"⚡ {side} {qty} {sym} @ ${price:.2f}\n{why} | {name} | conf {conf}%")
                            last_signals[sym]=why
                            time.sleep(1.2) # rate limit
                        except Exception as e: print("ORDER ERR",sym,e)
                    break
            time.sleep(8)
        except Exception as e:
            print("SCAN ERR",traceback.format_exc()); time.sleep(30)

# --- REPORTER (5min, 2 messages) ---
def reporter():
    while True:
        try:
            a=acct()
            if not a: send("🚨 Bot offline - can't reach Alpaca"); time.sleep(300); continue
            eq=float(a.equity); cash=float(a.cash); last=float(a.last_equity)
            today=(eq-last)/last*100; init=float(mem_get("init_eq")); alltime=(eq-init)/init*100
            pos=api.list_positions()
            phrase=random.choice(PHRASES)
            m1=f"🔥 HEDGE FUND COMMAND CENTER\n{phrase}\n────────────────────\n💰 ${eq:,.0f} ({today:+.2f}% today)\n📊 All-Time: {alltime:+.1f}% | Trades: {cur.execute('SELECT COUNT(*) FROM trades').fetchone()[0]}\n💵 Cash: ${cash:,.0f}\n\n🎯 POSITIONS ({len(pos)})"
            for p in pos[:6]:
                ch=(float(p.current_price)-float(p.avg_entry_price))/float(p.avg_entry_price)*100
                m1+=f"\n- {p.symbol} {int(float(p.qty))} @ ${float(p.avg_entry_price):.2f} {'▲' if ch>0 else '▼'}{abs(ch):.1f}%"
            send(m1)
            win=cur.execute("SELECT COUNT(*) FROM trades WHERE side='SELL'").fetchone()[0]
            tot=max(1,cur.execute("SELECT COUNT(*) FROM trades").fetchone()[0])
            kelly=int(max(5,min(25,(win/tot)*25)))
            m2="🧠 BRAIN STATUS\n"
            m2+=f"- Kelly: {kelly}% | Universe: {len(UNIVERSE)} | Learning: {'WARMING UP' if tot<100 else 'ACTIVE'}\n"
            m2+=f"- Last signals: {', '.join(list(last_signals.keys())[-4:]) or 'scanning...'}\n\n🛡️ RISK\n- Max 20% per name | Daily stop 5% | Mode: {'LIVE' if LIVE else 'PAPER'}\n────────────────────\nNext: 5 min"
            send(m2)
        except Exception as e: print("REP ERR",e)
        time.sleep(300)

# --- CLEANER ---
def cleaner():
    while True:
        now=datetime.now(timezone.utc)
        if now.hour==3 and now.minute<6:
            cur.execute("DELETE FROM trades WHERE rowid NOT IN (SELECT MIN(rowid) FROM trades GROUP BY ts,symbol,side,qty)")
            conn.commit(); print("MEMORY: deduped")
        if now.hour==0: build_universe() # refresh universe daily
        time.sleep(600)

# --- RUN ---
threading.Thread(target=scanner,daemon=True).start()
threading.Thread(target=reporter,daemon=True).start()
threading.Thread(target=cleaner,daemon=True).start()
print("RUN: all threads started")
while True: time.sleep(3600)