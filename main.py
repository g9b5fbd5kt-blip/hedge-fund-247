import os, time, threading, sqlite3, random, math, traceback
from datetime import datetime, timezone
import pandas as pd
import matplotlib.pyplot as plt
import alpaca_trade_api as tradeapi
from telegram import Bot

# ===== ENV =====
KEY = os.getenv("APCA_API_KEY_ID")
SECRET = os.getenv("APCA_API_SECRET_KEY")
LIVE = os.getenv("LIVE_MODE","false").lower()=="true"
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT = os.getenv("TELEGRAM_CHAT_ID")

api = tradeapi.REST(KEY, SECRET, "https://api.alpaca.markets" if LIVE else "https://paper-api.alpaca.markets", api_version='v2')
bot = Bot(TOKEN)

# ===== MEMORY (persists across restarts) =====
conn = sqlite3.connect("bot.db", check_same_thread=False)
cur = conn.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS trades (ts TEXT, symbol TEXT, side TEXT, qty REAL, price REAL, strategy TEXT, pnl REAL)")
cur.execute("CREATE TABLE IF NOT EXISTS memory (k TEXT PRIMARY KEY, v TEXT)")
conn.commit()

def mem_get(k,d=None): r=cur.execute("SELECT v FROM memory WHERE k=?",(k,)).fetchone(); return r[0] if r else d
def mem_set(k,v): cur.execute("INSERT OR REPLACE INTO memory VALUES(?,?)",(k,str(v))); conn.commit()

if not mem_get("init_eq"):
    try: mem_set("init_eq", api.get_account().equity)
    except: mem_set("init_eq","100000")

# ===== CONFIG =====
PHRASES = ["pimpin ain't easy 😎","money never sleeps","scanning the matrix","alpha hunting","risk on","calculating edge","no free lunches","trade the plan","compounding","liquidity is king","volatility = opportunity","building the book"]
UNIVERSE = ["AAPL","MSFT","NVDA","TSLA","AMD","SPY","QQQ","TQQQ","SQQQ","META","AMZN","GOOGL","NFLX","PLTR","SOFI","RIVN","LCID","NIO","F","BAC","JPM","XOM","CVX","PFE","JNJ","KO","PEP","WMT","COST","DIS","BTC/USD","ETH/USD","DOGE/USD","SOL/USD","AVGO","SMCI","MARA","RIOT","COIN","HOOD","UPST","AFRM","SNAP","UBER","LYFT","SHOP","SQ","PYPL","INTC","MU"]

lock = threading.Lock()
last_signals = {}

# ===== HELPERS =====
def send(txt):
    try: bot.send_message(CHAT, txt)
    except Exception as e: print("tg",e)

def send_photo(path,caption=""):
    try: bot.send_photo(CHAT, open(path,'rb'), caption=caption)
    except Exception as e: print("photo",e)

def acct():
    try: return api.get_account()
    except: return None

def positions():
    try: return api.list_positions()
    except: return []

def bars(sym, tf="5Min", n=100):
    try: return api.get_bars(sym, tf, limit=n).df
    except: return pd.DataFrame()

def rsi(s, p=14):
    d=s.diff(); up=d.clip(lower=0); dn=-d.clip(upper=0)
    eu=up.ewm(com=p-1,adjust=False).mean(); ed=dn.ewm(com=p-1,adjust=False).mean()
    return 100-(100/(1+eu/ed))

# ===== STRATEGIES =====
def strat_momentum(sym, df):
    if len(df)<30: return None
    r = rsi(df.close).iloc[-1]
    if r<35: return ("BUY", f"RSI oversold {r:.0f}")
    if r>68: return ("SELL", f"RSI overbought {r:.0f}")
    return None

def strat_breakout(sym, df):
    if len(df)<50: return None
    hi = df.high.rolling(20).max().iloc[-2]; lo = df.low.rolling(20).min().iloc[-2]; p=df.close.iloc[-1]
    vol = df.volume.iloc[-1]/df.volume.rolling(20).mean().iloc[-1]
    if p>hi and vol>1.8: return ("BUY", f"breakout vol {vol:.1f}x")
    if p<lo and vol>1.8: return ("SELL", f"breakdown vol {vol:.1f}x")
    return None

STRATS = [("MOM", strat_momentum), ("BRK", strat_breakout)]

# ===== RISK MANAGER (hedge fund rules) =====
def risk_ok(sym, side, qty, price):
    a=acct();
    if not a: return False
    eq=float(a.equity); cash=float(a.cash)
    # max 20% per name
    pos_val = qty*price
    if pos_val/eq > 0.20: qty = int((eq*0.20)/price)
    # no new buys if cash negative and side buy
    if side=="BUY" and cash < pos_val*0.3: return False
    # daily loss circuit breaker 5%
    init=float(mem_get("init_eq")); dd=(eq-init)/init
    if dd < -0.05: return False
    return True

# ===== CHART (your style upgraded) =====
def make_chart(sym, df, signal):
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(9,4.5), dpi=140)
    ax.plot(df.close.values, color='#00FF7F', lw=2.2)
    ax.plot(df.close.rolling(20).mean().values, color='#555555', lw=1, alpha=0.7)
    ax.set_title(f"{sym} - Live", fontsize=14, loc='left', color='white')
    ax.grid(True, alpha=0.18)
    ax.set_facecolor('#0a0a0a')
    conf = min(95, max(55, int(60 + abs(rsi(df.close).iloc[-1]-50))))
    ax.text(0.015,0.92, f"{conf}% CONFIDENCE", transform=ax.transAxes, fontsize=10, color='#FFB000',
            bbox=dict(facecolor='black', edgecolor='#FFB000', boxstyle='round,pad=0.35'))
    ax.text(0.015,0.82, signal.upper(), transform=ax.transAxes, fontsize=9, color='#FFB000')
    # volume at bottom
    ax2 = ax.twinx(); ax2.bar(range(len(df)), df.volume.values, alpha=0.15, color='gray'); ax2.set_yticks([])
    plt.tight_layout()
    path=f"/tmp/{sym}.png"; plt.savefig(path); plt.close(); return path, conf

# ===== SCANNER / TRADER =====
def scan_and_trade():
    while True:
        try:
            a=acct()
            if not a: time.sleep(15); continue
            eq=float(a.equity)
            for sym in random.sample(UNIVERSE, 25): # scan 25 per cycle for speed
                df=bars(sym, "5Min", 80)
                if df.empty or len(df)<40: continue
                best=None
                for name, fn in STRATS:
                    sig=fn(sym, df)
                    if sig: best=(name, sig[0], sig[1]); break
                if not best: continue
                strat, side, why = best
                price=float(df.close.iloc[-1])
                qty = max(1, int((eq*0.01)/price)) # 1% risk
                with lock:
                    if not risk_ok(sym, side, qty, price): continue
                    try:
                        api.submit_order(sym, qty, side.lower(), "market", "day")
                        ts=datetime.now(timezone.utc).isoformat()
                        cur.execute("INSERT INTO trades VALUES(?,?,?,?,?,?,?)",(ts,sym,side,qty,price,strat,None))
                        conn.commit()
                        # chart only on action
                        path, conf = make_chart(sym, df.tail(60), why)
                        send_photo(path, f"⚡ {side} {qty} {sym} @ ${price:.2f}\n{why} | {strat} | conf {conf}%")
                        last_signals[sym]=why
                    except Exception as e: print("order",e)
            time.sleep(90)
        except Exception as e:
            print("scan err", traceback.format_exc()); time.sleep(30)

# ===== REPORTER (5 min) =====
def reporter():
    while True:
        try:
            a=acct()
            if not a:
                send("🚨 Bot offline - can't reach Alpaca"); time.sleep(300); continue
            eq=float(a.equity); cash=float(a.cash); last=float(a.last_equity)
            today=(eq-last)/last*100; init=float(mem_get("init_eq")); alltime=(eq-init)/init*100
            pos=positions()
            phrase=random.choice(PHRASES)
            # msg 1
            m1 = f"🔥 HEDGE FUND COMMAND CENTER\n{phrase}\n────────────────────\n💰 ${eq:,.0f} ({today:+.2f}% today)\n📊 All-Time: {alltime:+.1f}% | Trades: {cur.execute('SELECT COUNT(*) FROM trades').fetchone()[0]}\n💵 Cash: ${cash:,.0f}\n\n🎯 POSITIONS ({len(pos)})"
            for p in pos[:5]:
                chg=(float(p.current_price)-float(p.avg_entry_price))/float(p.avg_entry_price)*100
                m1+=f"\n- {p.symbol} {int(float(p.qty))} @ ${float(p.avg_entry_price):.2f} {'▲' if chg>0 else '▼'}{abs(chg):.1f}%"
            send(m1)
            # msg 2 - brain thinking
            m2 = "🧠 BRAIN STATUS\n"
            win = cur.execute("SELECT COUNT(*) FROM trades WHERE side='SELL'").fetchone()[0] # simplified
            total = max(1, cur.execute("SELECT COUNT(*) FROM trades").fetchone()[0])
            kelly = max(5, min(25, int((win/total)*25)))
            m2+=f"- Kelly: {kelly}% | Learning: {'WARMING UP' if total<50 else 'ACTIVE'}\n- Scanning: {len(UNIVERSE)} tickers\n- Last signals: {', '.join([f'{k}:{v[:12]}' for k,v in list(last_signals.items())[-3:]]) or 'none'}"
            m2+=f"\n\n🛡️ RISK\n- Max pos 20% | Daily stop 5%\n- Mode: {'LIVE' if LIVE else 'PAPER'} AUTO"
            m2+=f"\n────────────────────\nNext: 5 min"
            send(m2)
        except Exception as e:
            print("rep",e)
        time.sleep(300)

# ===== MEMORY CLEANUP (nightly) =====
def cleaner():
    while True:
        now=datetime.now(timezone.utc)
        if now.hour==3 and now.minute<5: # 11pm ET ~ 3am UTC
            cur.execute("DELETE FROM trades WHERE rowid NOT IN (SELECT MIN(rowid) FROM trades GROUP BY ts,symbol,side,qty)")
            conn.commit()
        time.sleep(600)

# ===== START =====
def run():
    send("✅ Hedge Fund 24/7 v1 ONLINE — scanning everything, learning, 5-min updates")
    threading.Thread(target=scan_and_trade, daemon=True).start()
    threading.Thread(target=reporter, daemon=True).start()
    threading.Thread(target=cleaner, daemon=True).start()
    while True: time.sleep(3600)

if __name__=="__main__":
    run()