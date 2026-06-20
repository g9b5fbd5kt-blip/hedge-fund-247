"""
BOSS v2.1 — Money Printer Pro
Sleek design • 20 rotating phrases • HOLD-only-when-holding
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
# CONFIG
# ═══════════════════════════════════════════════════════════
ALPACA_KEY = os.getenv("APCA_API_KEY_ID")
ALPACA_SECRET = os.getenv("APCA_API_SECRET_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT = os.getenv("TELEGRAM_CHAT_ID")

PAPER = True
MAX_POSITION = 300.0
PROFIT_RESERVE_PCT = 0.30

# 20 rotating phrases — your style
PHRASES = [
    "pimpin ain't easy", "stay grounded", "bag secured", "printer go brrr",
    "we eat", "no cap", "locked in", "touch grass later",
    "money printer activated", "built different", "on god", "diamond hands",
    "wagmi", "secure the bag", "we up", "different breed",
    "cookin", "running it up", "stay dangerous", "let's work"
]

STOCKS = ["AAPL", "MSFT", "NVDA", "TSLA", "AMD", "QQQ", "SPY", "TSM", "GOOGL", "AMZN"]
CRYPTO = ["BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD", "DOGE/USD"]

# ═══════════════════════════════════════════════════════════
# SETUP
# ═══════════════════════════════════════════════════════════
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("BOSS")

trading_client = TradingClient(ALPACA_KEY, ALPACA_SECRET, paper=PAPER)
stock_client = StockHistoricalDataClient(ALPACA_KEY, ALPACA_SECRET)
crypto_client = CryptoHistoricalDataClient(ALPACA_KEY, ALPACA_SECRET)

conn = sqlite3.connect("boss.db", check_same_thread=False)
c = conn.cursor()
c.execute("""CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY, ts TEXT, sym TEXT, side TEXT, qty REAL, price REAL, pnl REAL, strat TEXT)""")
c.execute("""CREATE TABLE IF NOT EXISTS reserve (id INTEGER PRIMARY KEY, ts TEXT, amt REAL)""")
conn.commit()

last_api = 0
last_hold_ping = 0

def rate_limit():
    global last_api
    elapsed = time.time() - last_api
    if elapsed < 0.35: time.sleep(0.35 - elapsed)
    last_api = time.time()

def send_tg(text, chart=None):
    try:
        msg = f"{text}\n\n<i>{random.choice(PHRASES)}</i>"
        if chart: msg += f"\n\n<a href='{chart}'>📊 View Chart</a>"
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                     data={"chat_id": TELEGRAM_CHAT, "text": msg, "parse_mode": "HTML"}, timeout=10)
        time.sleep(0.2)
    except Exception as e: logger.error(f"TG: {e}")

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

# ═══════════════════════════════════════════════════════════
# ANALYST
# ═══════════════════════════════════════════════════════════
class Analyst:
    def __init__(self): self.rsi_thresh = 65

    def analyze(self, sym):
        try:
            rate_limit()
            is_crypto = "/" in sym
            req = (CryptoBarsRequest(symbol_or_symbols=sym, timeframe=TimeFrame.Minute, start=datetime.now()-timedelta(hours=2))
                   if is_crypto else StockBarsRequest(symbol_or_symbols=sym, timeframe=TimeFrame.Minute, start=datetime.now()-timedelta(hours=2)))
            bars = (crypto_client.get_crypto_bars(req) if is_crypto else stock_client.get_stock_bars(req)).df

            if bars.empty or len(bars) < 20: return {"score":0, "action":"WAIT"}

            closes = bars['close'].values[-14:]
            deltas = [closes[i+1]-closes[i] for i in range(13)]
            gains = [d if d>0 else 0 for d in deltas]
            losses = [-d if d<0 else 0 for d in deltas]
            rs = (sum(gains)/14) / (sum(losses)/14 if sum(losses)>0 else 0.01)
            rsi = 100 - (100/(1+rs))

            score = 85 if rsi<30 else 15 if rsi>self.rsi_thresh else 60 if 45<rsi<55 else 50
            action = "BUY" if score>70 else "SELL" if score<30 else "HOLD"

            return {"score":score, "rsi":round(rsi,1), "action":action, "price":float(bars['close'].iloc[-1])}
        except: return {"score":0, "action":"WAIT"}

# ═══════════════════════════════════════════════════════════
# RISK
# ═══════════════════════════════════════════════════════════
class Risk:
    def check(self, sym, acct, positions):
        try:
            if float(acct.buying_power) < 50: return False, "Low buying power"
            for p in positions:
                if p.symbol == sym and float(p.unrealized_pl) < -20: return False, "Position losing"
                if "SOL" in sym and "AVAX" in p.symbol: return False, "Correlation guard"
            return True, "Approved"
        except Exception as e: return False, str(e)

# ═══════════════════════════════════════════════════════════
# BOSS
# ═══════════════════════════════════════════════════════════
class Boss:
    def __init__(self):
        self.analyst = Analyst()
        self.risk = Risk()

    def scan(self):
        global last_hold_ping
        acct = get_account()
        if not acct: return

        positions = get_positions()
        now = datetime.now(pytz.timezone('US/Eastern'))
        market_open = now.weekday()<5 and 9 <= now.hour < 16

        opportunities = []
        for sym in CRYPTO + (STOCKS if market_open else []):
            a = self.analyst.analyze(sym)
            if a["score"] > 70: opportunities.append({"sym":sym, "score":a["score"], "price":a["price"]})

        opportunities.sort(key=lambda x: x["score"], reverse=True)
        traded = False

        if opportunities:
            top = opportunities[0]
            approved, reason = self.risk.check(top["sym"], acct, positions)
            if approved:
                self.execute(top, acct)
                traded = True
            else:
                send_tg(f"🚫 <b>HOLD</b>\n\n{top['sym']} • Score {top['score']}/100\nBlocked: {reason}", chart_url(top["sym"]))

        self.check_exits(positions)

        # HOLD ping ONLY when you own something
        if not traded and len(positions) > 0 and time.time() - last_hold_ping > 300:
            p = positions[0]
            pnl = float(p.unrealized_pl)
            pnl_pct = float(p.unrealized_plpc) * 100
            send_tg(f"💎 <b>HOLDING</b>\n\n{p.symbol}\n${pnl:+.2f} ({pnl_pct:+.1f}%)\nQty: {p.qty}", chart_url(p.symbol))
            last_hold_ping = time.time()

    def execute(self, opp, acct):
        try:
            sym, price = opp["sym"], opp["price"]
            size = min(MAX_POSITION, float(acct.buying_power) * 0.95)
            qty = size / price
            qty = round(qty, 6) if "/" in sym else int(qty)
            if qty < 1 and "/" not in sym: return

            rate_limit()
            tif = TimeInForce.GTC if "/" in sym else TimeInForce.DAY
            trading_client.submit_order(MarketOrderRequest(symbol=sym, qty=qty, side=OrderSide.BUY, time_in_force=tif))

            c.execute("INSERT INTO trades VALUES (NULL,?,?,?,?,?,?,?)",
                     (datetime.now().isoformat(), sym, "BUY", qty, price, 0, "momentum"))
            conn.commit()

            send_tg(f"🤖 <b>BUY EXECUTED</b>\n\n{sym}\n${price:.4f} × {qty}\nSize: ${size:.0f}\nScore: {opp['score']}/100", chart_url(sym))
        except Exception as e: logger.error(f"Exec: {e}")

    def check_exits(self, positions):
        for p in positions:
            try:
                pct = float(p.unrealized_plpc) * 100
                if pct <= -5 or pct >= 3:
                    reason = "Stop -5%" if pct <= -5 else "Take +3%"
                    rate_limit()
                    tif = TimeInForce.GTC if "/" in p.symbol else TimeInForce.DAY
                    side = OrderSide.SELL if float(p.qty) > 0 else OrderSide.BUY
                    trading_client.submit_order(MarketOrderRequest(symbol=p.symbol, qty=abs(float(p.qty)), side=side, time_in_force=tif))

                    pnl = float(p.unrealized_pl)
                    if pnl > 0: c.execute("INSERT INTO reserve VALUES (NULL,?,?)", (datetime.now().isoformat(), pnl*0.3)); conn.commit()

                    send_tg(f"💰 <b>SELL EXECUTED</b>\n\n{p.symbol}\n{reason}\nPnL: ${pnl:+.2f}", chart_url(p.symbol))
            except Exception as e: logger.error(f"Exit: {e}")

# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════
def main():
    logger.info("BOSS starting")
    send_tg("🤖 <b>BOSS v2.1 ONLINE</b>\n\nPaper trading active\n20 phrases loaded\nSleek mode: ON")
    boss = Boss()
    while True:
        try: boss.scan(); time.sleep(10)
        except Exception as e: logger.error(f"Main: {e}"); time.sleep(30)

if __name__ == "__main__": main()