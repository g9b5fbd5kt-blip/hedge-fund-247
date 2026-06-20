import os, time, threading, sqlite3, math, requests
import alpaca_trade_api as tradeapi
from datetime import datetime, timezone
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

# --- ENV ---
KEY = os.getenv("APCA_API_KEY_ID")
SECRET = os.getenv("APCA_API_SECRET_KEY")
LIVE = os.getenv("LIVE_MODE","false").lower()=="true"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

api = tradeapi.REST(KEY, SECRET, "https://api.alpaca.markets" if LIVE else "https://paper-api.alpaca.markets")
bot = Bot(TELEGRAM_TOKEN)

# --- DB (fixed for Railway: no /data volume needed) ---
conn = sqlite3.connect("bot.db", check_same_thread=False)
cur = conn.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY, ts TEXT, symbol TEXT, side TEXT, qty REAL, price REAL, pnl REAL)")
conn.commit()

TICKERS = ["AAPL","MSFT","TSLA","NVDA","SPY"]
RISK_PCT = 0.01

def send(msg): 
    try: bot.send_message(chat_id=CHAT_ID, text=msg)
    except Exception as e: print("TG err",e)

def get_price(sym):
    try: return float(api.get_latest_trade(sym).price)
    except: return 0

def trade_logic():
    while True:
        try:
            acct = api.get_account()
            equity = float(acct.equity)
            for sym in TICKERS:
                price = get_price(sym)
                if price==0: continue
                # simple momentum: buy if up 0.3% in last minute (demo logic)
                bars = api.get_bars(sym, "1Min", limit=2).df
                if len(bars)<2: continue
                change = (bars['close'].iloc[-1]-bars['close'].iloc[-2])/bars['close'].iloc[-2]
                side = None
                if change>0.003: side="buy"
                elif change<-0.003: side="sell"
                if side:
                    qty = math.floor((equity*RISK_PCT)/price)
                    if qty<1: continue
                    try:
                        api.submit_order(symbol=sym, qty=qty, side=side, type='market', time_in_force='day')
                        cur.execute("INSERT INTO trades (ts,symbol,side,qty,price) VALUES (?,?,?,?,?)",
                                    (datetime.now(timezone.utc).isoformat(), sym, side, qty, price))
                        conn.commit()
                        send(f"🔔 {side.upper()} {qty} {sym} @ ${price:.2f} | Δ{change*100:.2f}%")
                    except Exception as e: print("order err",e)
            time.sleep(60)
        except Exception as e:
            print("loop err",e); time.sleep(10)

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("📊 Status", callback_data="status"),
           InlineKeyboardButton("📈 Chart", callback_data="chart")]]
    await update.message.reply_text("Hedge Fund 24/7 is LIVE (Paper mode)", reply_markup=InlineKeyboardMarkup(kb))

async def button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.data=="status":
        acct = api.get_account()
        await q.edit_message_text(f"Equity: ${float(acct.equity):,.2f}\nP/L: ${float(acct.unrealized_pl):,.2f}\nMode: {'LIVE' if LIVE else 'PAPER'}")
    elif q.data=="chart":
        # send simple price chart
        sym="SPY"; bars=api.get_bars(sym,"5Min",limit=50).df
        import matplotlib.pyplot as plt
        plt.figure(); bars['close'].plot(); plt.title(f"{sym} 5m"); plt.tight_layout()
        plt.savefig("/tmp/chart.png"); plt.close()
        await ctx.bot.send_photo(chat_id=q.message.chat_id, photo=open("/tmp/chart.png","rb"))

def run_bot():
    send("✅ Hedge Fund 24/7 is LIVE (Paper mode) – Send /help")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    app.run_polling()

if __name__=="__main__":
    threading.Thread(target=trade_logic, daemon=True).start()
    run_bot()