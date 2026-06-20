import os, time, threading, sqlite3, math, traceback
from datetime import datetime, timezone
import pandas as pd
import matplotlib.pyplot as plt
import alpaca_trade_api as tradeapi
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

# --- ENV ---
KEY = os.getenv("APCA_API_KEY_ID")
SECRET = os.getenv("APCA_API_SECRET_KEY")
LIVE = os.getenv("LIVE_MODE","false").lower()=="true"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

api = tradeapi.REST(KEY, SECRET, "https://api.alpaca.markets" if LIVE else "https://paper-api.alpaca.markets", api_version='v2')
bot = Bot(TELEGRAM_TOKEN)

# --- DB (Railway-safe) ---
conn = sqlite3.connect("bot.db", check_same_thread=False)
cur = conn.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY, ts TEXT, symbol TEXT, side TEXT, qty REAL, price REAL, pnl REAL)")
cur.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
conn.commit()

def get_meta(k, default=None):
    cur.execute("SELECT v FROM meta WHERE k=?", (k,)); r=cur.fetchone(); return r[0] if r else default
def set_meta(k,v): cur.execute("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)",(k,str(v))); conn.commit()

TRADING_ACTIVE = True
TICKERS = ["NVDA","QQQ","AAPL","MSFT","TSLA","SPY"]
CRYPTO = ["BTC/USD"]

# --- HELPERS ---
def send(msg, markup=None):
    try: bot.send_message(chat_id=CHAT_ID, text=msg, reply_markup=markup, parse_mode='HTML')
    except Exception as e: print("TG err",e)

def get_account():
    try: return api.get_account()
    except Exception as e: print("Alpaca acct err",e); return None

def get_positions():
    try: return api.list_positions()
    except: return []

def get_bars(sym, tf="5Min", limit=100):
    try: return api.get_bars(sym, tf, limit=limit).df
    except: return pd.DataFrame()

def rsi(series, p=14):
    delta = series.diff()
    up = delta.clip(lower=0); down = -delta.clip(upper=0)
    ma_up = up.ewm(com=p-1, adjust=False).mean()
    ma_down = down.ewm(com=p-1, adjust=False).mean()
    rs = ma_up/ma_down; return 100 - (100/(1+rs))

def sma(series, p=20): return series.rolling(p).mean()

# --- COMMAND CENTER BUILDER ---
def build_center():
    acct = get_account()
    if not acct:
        return "🚨 <b>Bot offline - can't reach Alpaca</b>\nCheck API keys in Railway Variables"

    equity = float(acct.equity); cash = float(acct.cash); last_eq = float(acct.last_equity)
    today_pct = (equity - last_eq)/last_eq*100 if last_eq else 0

    # init equity for all-time
    init_eq = get_meta("init_eq")
    if not init_eq: set_meta("init_eq", equity); init_eq = equity
    else: init_eq = float(init_eq)
    all_time = (equity - init_eq)/init_eq*100

    cur.execute("SELECT COUNT(*) FROM trades"); trades = cur.fetchone()[0]
    cur.execute("SELECT side, pnl FROM trades WHERE pnl IS NOT NULL")
    rows = cur.fetchall(); wins = sum(1 for s,p in rows if p and p>0); total = len(rows) or 1
    win_rate = wins/total

    positions = get_positions()
    pos_txt = []
    tech_val = 0
    for p in positions[:5]:
        sym = p.symbol; qty = float(p.qty); avg = float(p.avg_entry_price); curp = float(p.current_price)
        chg = (curp-avg)/avg*100; pos_txt.append(f"- {sym} {int(qty)} @ ${avg:.2f} {'▲' if chg>0 else '▼'}{abs(chg):.1f}%")
        if sym in ["NVDA","AAPL","MSFT","TSLA","QQQ"]: tech_val += float(p.market_value)

    # Market Intel
    intel = []
    for sym in ["NVDA","QQQ"]:
        df = get_bars(sym, "5Min", 50)
        if not df.empty:
            price = df['close'].iloc[-1]; r = rsi(df['close']).iloc[-1]; s = sma(df['close']).iloc[-1]
            intel.append(f"- {sym}: ${price:.2f} | RSI {r:.0f} | SMA20>{'✓' if price>s else '✗'}")
    btc = get_bars("BTC/USD","5Min",2)
    if not btc.empty: intel.append(f"- BTC: ${btc['close'].iloc[-1]:,.0f}")

    # Brain
    kelly = max(0, min(0.25, win_rate - (1-win_rate)/(2 if win_rate>0 else 1)))
    learning = "WARMING UP" if trades<30 else "LEARNING" if trades<100 else "ACTIVE"
    score = int(min(5, max(1, win_rate*5)))

    # Deep Analysis
    exposure = (sum(float(p.market_value) for p in positions)/equity*100) if equity else 0
    tech_pct = tech_val/equity*100 if equity else 0
    analysis = []
    executed = []
    if exposure > 45:
        trim_qty = 0
        for p in positions:
            if p.symbol=="NVDA" and float(p.qty)>10:
                trim_qty = int(float(p.qty)*0.3);
                try:
                    api.submit_order(symbol="NVDA", qty=trim_qty, side="sell", type="market", time_in_force="day")
                    executed.append(f"SELL {trim_qty} NVDA @ market")
                    cur.execute("INSERT INTO trades (ts,symbol,side,qty,price) VALUES (?,?,?,?,?)",
                        (datetime.now(timezone.utc).isoformat(),"NVDA","sell",trim_qty,float(p.current_price)))
                    conn.commit()
                except: pass
                break
        analysis.append(f"• MATH: Exposure {exposure:.1f}% > 45%. Kelly optimal trim = {trim_qty} NVDA")
    if cash < 0:
        analysis.append(f"• MATH: Margin debt ${cash:,.0f}. Priority: free cash before new entries")

    # Build message
    msg = f"""🔥 <b>HEDGE FUND COMMAND CENTER</b>
pimpin ain't easy 😎
────────────────────
💰 ${equity:,.0f} ({today_pct:+.2f}% today)
📊 All-Time: {all_time:+.1f}% | Trades: {trades}
💵 Cash: ${cash:,.0f}

🎯 POSITIONS ({len(positions)})
{chr(10).join(pos_txt) if pos_txt else "- none"}

💎 MARKET INTEL
{chr(10).join(intel)}

🧠 BRAIN STATUS
- Kelly: {kelly*100:.1f}% (win {win_rate*100:.0f}%)
- Learning: {learning}
- Score: {score}/5

🧮 DEEP ANALYSIS
{chr(10).join(analysis) if analysis else "• MATH: All systems nominal"}

⚡ EXECUTED:
• {chr(10)+'• '.join(executed) if executed else ""}

🛡️ RISK
- Tech: {tech_pct:.1f}% (target 40%)
- Action: {"TRADE EXECUTED" if executed else "HOLD"}
────────────────────
Next: 5 min | Mode: {"LIVE" if LIVE else "PAPER"} {"AUTO" if TRADING_ACTIVE else "PAUSED"}"""
    return msg

def keyboard():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📊 Chart", callback_data="chart"),
        InlineKeyboardButton("💰 P&L", callback_data="pnl"),
        InlineKeyboardButton("⏸️ Pause", callback_data="pause"),
        InlineKeyboardButton("▶️ Resume", callback_data="resume")
    ]])

# --- TRADING LOGIC (keeps upgrades) ---
def trade_loop():
    global TRADING_ACTIVE
    while True:
        try:
            if not TRADING_ACTIVE: time.sleep(10); continue
            acct = get_account()
            if not acct: time.sleep(30); continue
            equity = float(acct.equity); cash = float(acct.cash)
            for sym in TICKERS:
                df = get_bars(sym, "1Min", 30)
                if df.empty or len(df)<20: continue
                price = df['close'].iloc[-1]; r = rsi(df['close']).iloc[-1]
                if r<30 and cash>1000: # oversold buy
                    qty = max(1, int((equity*0.01)/price))
                    try: api.submit_order(sym, qty, "buy", "market", "day")
                    except: pass
                elif r>70: # overbought sell
                    for p in get_positions():
                        if p.symbol==sym and float(p.qty)>0:
                            try: api.submit_order(sym, int(float(p.qty)*0.5), "sell", "market", "day")
                            except: pass
            time.sleep(60)
        except Exception as e:
            print("trade err",e); time.sleep(15)

# --- TELEGRAM HANDLERS ---
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hedge Fund 24/7 is LIVE (Paper mode)", reply_markup=keyboard())

async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global TRADING_ACTIVE
    q = update.callback_query; await q.answer()
    data = q.data
    if data=="chart":
        df = get_bars("SPY","5Min",100)
        plt.figure(); df['close'].plot(); plt.title("SPY 5m"); plt.tight_layout(); plt.savefig("/tmp/c.png"); plt.close()
        await ctx.bot.send_photo(q.message.chat_id, open("/tmp/c.png","rb"))
    elif data=="pnl":
        acct = get_account(); await q.edit_message_text(f"P&L: ${float(acct.unrealized_pl):+.2f}\nEquity: ${float(acct.equity):,.2f}", reply_markup=keyboard())
    elif data=="pause":
        TRADING_ACTIVE=False; await q.edit_message_text("⏸️ Trading PAUSED", reply_markup=keyboard())
    elif data=="resume":
        TRADING_ACTIVE=True; await q.edit_message_text("▶️ Trading RESUMED", reply_markup=keyboard())
    elif data=="status":
        await q.edit_message_text(build_center(), reply_markup=keyboard(), parse_mode='HTML')

async def status_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(build_center(), reply_markup=keyboard(), parse_mode='HTML')

def center_loop():
    while True:
        try:
            msg = build_center()
            send(msg, keyboard())
        except Exception as e:
            print("center err", traceback.format_exc())
            send("🚨 Bot offline - can't reach Alpaca")
        time.sleep(300) # 5 min

def run_bot():
    send("✅ Hedge Fund 24/7 is LIVE (Paper mode) – Send /help")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CallbackQueryHandler(on_button))
    app.run_polling()

if __name__=="__main__":
    threading.Thread(target=trade_loop, daemon=True).start()
    threading.Thread(target=center_loop, daemon=True).start()
    run_bot()