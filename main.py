"""
BOSS v2.3 — Full System
✓ Scheduled summaries ✓ Expanded UI ✓ 20 phrases ✓ No spam
"""

import os, time, sqlite3, requests, pytz, random, logging
from datetime import datetime, timedelta
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame

# ═══════════════════════════════════════════════════════════
ALPACA_KEY = os.getenv("APCA_API_KEY_ID")
ALPACA_SECRET = os.getenv("APCA_API_SECRET_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT = os.getenv("TELEGRAM_CHAT_ID")

PAPER = True
MAX_POSITION = 300.0

PHRASES = [
    "pimpin ain't easy", "stay grounded", "bag secured", "printer go brrr",
    "we eat", "no cap", "locked in", "touch grass later",
    "money printer activated", "built different", "on god", "diamond hands",
    "wagmi", "secure the bag", "we up", "different breed",
    "cookin", "running it up", "stay dangerous", "let's work"
]

STOCKS = ["AAPL", "MSFT", "NVDA", "TSLA", "AMD", "QQQ", "SPY", "TSM", "GOOGL", "AMZN"]
CRYPTO = ["BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD", "DOGE/USD"]

logging.basicConfig(level=logging.INFO, format='%(H:%M:%S) %(message)s')
logger = logging.getLogger("BOSS")

trading_client = TradingClient(ALPACA_KEY, ALPACA_SECRET, paper=PAPER)
stock_client = StockHistoricalDataClient(ALPACA_KEY, ALPACA_SECRET)
crypto_client = CryptoHistoricalDataClient(ALPACA_KEY, ALPACA_SECRET)

conn = sqlite3.connect("boss.db", check_same_thread=False)
c = conn.cursor()
c.execute("""CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY, ts TEXT, sym TEXT, side TEXT, qty REAL, entry REAL, exit REAL, pnl REAL)""")
c.execute("""CREATE TABLE IF NOT EXISTS daily_stats (date TEXT PRIMARY KEY, trades INTEGER, wins INTEGER, pnl REAL)""")
conn.commit()

last_api = 0
last_hold_ping = 0

def rate_limit():
    global last_api
    if time.time() - last_api < 0.35: time.sleep(0.35 - (time.time() - last_api))
    last_api = time.time()

def send_tg(text, chart=None):
    try:
        msg = f"{text}\n\n<i>{random.choice(PHRASES)}</i>"
        if chart: msg += f"\n\n<a href='{chart}'>📊 TradingView</a>"
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                     data={"chat_id": TELEGRAM_CHAT, "text": msg, "parse_mode": "HTML", "disable_web_page_preview": False}, timeout=10)
    except: pass

def chart_url(sym):
    clean = sym.replace("/USD", "USD").replace("/", "")
    return f"https://tradingview.com/chart/?symbol=COINBASE:{clean}" if "/" in sym else f"https://tradingview.com/chart/?symbol=NASDAQ:{sym}"

def get_account():
    rate_limit()
    try: return trading_client.get_account()
    except: return None

def get_positions():
    rate_limit()
    try: return trading_client.get_all_positions()
    except: return []

class Analyst:
    def __init__(self): self.rsi_thresh = 65
    def analyze(self, sym):
        try:
            rate_limit()
            is_crypto = "/" in sym
            req = (CryptoBarsRequest(symbol_or_symbols=sym, timeframe=TimeFrame.Minute, start=datetime.now()-timedelta(hours=2))
                   if is_crypto else StockBarsRequest(symbol_or_symbols=sym, timeframe=TimeFrame.Minute, start=datetime.now()-timedelta(hours=2)))
            bars = (crypto_client.get_crypto_bars(req) if is_crypto else stock_client.get_stock_bars(req)).df
            if bars.empty or len(bars) < 20: return {"score":0}
            closes = bars['close'].values[-14:]
            deltas = [closes[i+1]-closes[i] for i in range(13)]
            gains = [d if d>0 else 0 for d in deltas]
            losses = [-d if d<0 else 0 for d in deltas]
            rs = (sum(gains)/14) / (sum(losses)/14 if sum(losses)>0 else 0.01)
            rsi = 100 - (100/(1+rs))
            score = 85 if rsi<30 else 15 if rsi>self.rsi_thresh else 60 if 45<rsi<55 else 50
            return {"score":score, "rsi":round(rsi,1), "price":float(bars['close'].iloc[-1])}
        except: return {"score":0}

class Boss:
    def __init__(self):
        self.analyst = Analyst()

    def scan(self):
        global last_hold_ping
        acct = get_account()
        if not acct: return
        positions = get_positions()
        now = datetime.now(pytz.timezone('US/Eastern'))
        market_open = now.weekday()<5 and 9 <= now.hour < 16

        # Find buys
        for sym in CRYPTO + (STOCKS if market_open else []):
            a = self.analyst.analyze(sym)
            if a["score"] > 70 and float(acct.buying_power) > 50:
                # Check not already holding
                if not any(p.symbol == sym for p in positions):
                    self.execute_buy(sym, a["price"], a["score"], a["rsi"], acct)
                    return

        self.check_exits(positions)

        # HOLD ping - only owned positions, every 10 min
        if len(positions) > 0 and time.time() - last_hold_ping > 600:
            p = positions[0]
            entry, current = float(p.avg_entry_price), float(p.current_price)
            pnl, pnl_pct = float(p.unrealized_pl), float(p.unrealized_plpc)*100
            a = self.analyst.analyze(p.symbol)

            c.execute("SELECT ts FROM trades WHERE sym=? ORDER BY id DESC LIMIT 1", (p.symbol,))
            row = c.fetchone()
            held = ""
            if row:
                mins = int((datetime.now() - datetime.fromisoformat(row[0])).total_seconds() / 60)
                held = f"{mins//60}h {mins%60}m" if mins>60 else f"{mins}m"

            msg = (f"💎 <b>POSITION UPDATE</b>\n"
                   f"━━━━━━━━━━━━━━\n"
                   f"• <b>{p.symbol}</b> | {float(p.qty)} shares\n"
                   f"• ${entry:.4f} → ${current:.4f}\n"
                   f"• <b>PnL: ${pnl:+.2f} ({pnl_pct:+.1f}%)</b>\n"
                   f"• Held: {held} | RSI: {a.get('rsi',0)}\n"
                   f"• Score: {a.get('score',0)}/100")
            send_tg(msg, chart_url(p.symbol))
            last_hold_ping = time.time()

    def execute_buy(self, sym, price, score, rsi, acct):
        try:
            size = min(MAX_POSITION, float(acct.buying_power) * 0.95)
            qty = size / price
            qty = round(qty, 6) if "/" in sym else int(qty)
            if qty < 1 and "/" not in sym: return
            rate_limit()
            tif = TimeInForce.GTC if "/" in sym else TimeInForce.DAY
            trading_client.submit_order(MarketOrderRequest(symbol=sym, qty=qty, side=OrderSide.BUY, time_in_force=tif))
            c.execute("INSERT INTO trades VALUES (NULL,?,?,?,?,?,?,?)", (datetime.now().isoformat(), sym, "BUY", qty, price, None, None))
            conn.commit()
            msg = (f"🤖 <b>BUY EXECUTED</b>\n"
                   f"━━━━━━━━━━━━━━\n"
                   f"• <b>{sym}</b>\n"
                   f"• ${price:.4f} × {qty}\n"
                   f"• Size: ${size:.0f}\n"
                   f"• Score: {score}/100 | RSI: {rsi}\n"
                   f"• {datetime.now().strftime('%I:%M %p ET')}")
            send_tg(msg, chart_url(sym))
        except Exception as e: logger.error(f"Buy: {e}")

    def check_exits(self, positions):
        for p in positions:
            try:
                pct = float(p.unrealized_plpc) * 100
                if pct <= -5 or pct >= 3:
                    reason = "STOP LOSS" if pct <= -5 else "TAKE PROFIT"
                    rate_limit()
                    tif = TimeInForce.GTC if "/" in p.symbol else TimeInForce.DAY
                    side = OrderSide.SELL if float(p.qty) > 0 else OrderSide.BUY
                    trading_client.submit_order(MarketOrderRequest(symbol=p.symbol, qty=abs(float(p.qty)), side=side, time_in_force=tif))
                    pnl, entry, exit_price = float(p.unrealized_pl), float(p.avg_entry_price), float(p.current_price)
                    c.execute("UPDATE trades SET exit=?, pnl=? WHERE sym=? AND exit IS NULL", (exit_price, pnl, p.symbol))
                    conn.commit()
                    msg = (f"💰 <b>SELL EXECUTED</b>\n"
                           f"━━━━━━━━━━━━━━\n"
                           f"• <b>{p.symbol}</b>\n"
                           f"• ${entry:.4f} → ${exit_price:.4f}\n"
                           f"• <b>PnL: ${pnl:+.2f} ({pct:+.1f}%)</b>\n"
                           f"• {reason}")
                    send_tg(msg, chart_url(p.symbol))
            except Exception as e: logger.error(f"Sell: {e}")

def morning_summary():
    try:
        acct = get_account()
        positions = get_positions()
        c.execute("SELECT COUNT(*), SUM(pnl) FROM trades WHERE ts > datetime('now', '-12 hours') AND pnl IS NOT NULL")
        row = c.fetchone()
        trades, pnl = row[0] or 0, row[1] or 0

        total_pnl = sum(float(p.unrealized_pl) for p in positions)
        msg = (f"🌅 <b>MORNING BRIEFING</b> — {datetime.now().strftime('%a %b %d')}\n"
               f"━━━━━━━━━━━━━━\n"
               f"• Portfolio: ${float(acct.portfolio_value):,.2f}\n"
               f"• Buying Power: ${float(acct.buying_power):,.2f}\n"
               f"• Open Positions: {len(positions)}/3\n"
               f"• Overnight PnL: ${total_pnl:+.2f}\n"
               f"• 12h Trades: {trades} | PnL: ${pnl:+.2f}\n"
               f"━━━━━━━━━━━━━━")
        send_tg(msg)
    except Exception as e: logger.error(f"Morning: {e}")

def evening_summary():
    try:
        acct = get_account()
        c.execute("SELECT COUNT(*), SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END), SUM(pnl) FROM trades WHERE date(ts)=date('now') AND pnl IS NOT NULL")
        row = c.fetchone()
        trades, wins, pnl = (row[0] or 0), (row[1] or 0), (row[2] or 0)
        win_rate = (wins/trades*100) if trades>0 else 0

        c.execute("SELECT COUNT(*), SUM(pnl) FROM trades WHERE pnl IS NOT NULL")
        total = c.fetchone()
        all_trades, all_pnl = total[0] or 0, total[1] or 0

        msg = (f"🌙 <b>EVENING CLOSE</b> — {datetime.now().strftime('%a %b %d')}\n"
               f"━━━━━━━━━━━━━━\n"
               f"<b>Today:</b>\n"
               f"• Trades: {trades} | Wins: {wins} ({win_rate:.0f}%)\n"
               f"• PnL: ${pnl:+.2f}\n\n"
               f"<b>All-Time:</b>\n"
               f"• Total Trades: {all_trades}\n"
               f"• Total PnL: ${all_pnl:+.2f}\n"
               f"• Portfolio: ${float(acct.portfolio_value):,.2f}\n"
               f"━━━━━━━━━━━━━━")
        send_tg(msg)
    except Exception as e: logger.error(f"Evening: {e}")

def main():
    logger.info("BOSS v2.3 starting")
    send_tg("🤖 <b>BOSS v2.3 ONLINE</b>\n\n✓ Morning 7AM ET\n✓ Evening 4PM ET\n✓ Position updates\n✓ 20 phrases")
    boss = Boss()
    last_morning = last_evening = None

    while True:
        try:
            now = datetime.now(pytz.timezone('US/Eastern'))

            # 7 AM morning summary
            if now.hour == 7 and now.minute < 5 and last_morning!= now.date():
                morning_summary()
                last_morning = now.date()

            # 4 PM evening summary
            if now.hour == 16 and now.minute < 5 and last_evening!= now.date():
                evening_summary()
                last_evening = now.date()

            boss.scan()
            time.sleep(10)
        except Exception as e:
            logger.error(f"Main: {e}")
            time.sleep(30)

if __name__ == "__main__": main()