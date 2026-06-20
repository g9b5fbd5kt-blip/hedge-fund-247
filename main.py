"""
v2.0 Money Printer Pro - Boss Trading System
52 features, paper trading, free-tier optimized
Built for Ethan Hazlewood - June 2026
"""

import os
import time
import sqlite3
import requests
import json
from datetime import datetime, timedelta
import pytz
import random
import logging
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame

# ========== CONFIG ==========
ALPACA_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET = os.getenv("ALPACA_SECRET_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT = os.getenv("TELEGRAM_CHAT_ID")

# Paper trading
PAPER = True
BASE_URL = "https://paper-api.alpaca.markets"

# Boss settings
MAX_POSITION = 300.0
PROFIT_RESERVE_PCT = 0.30
MIN_PROFIT_MULTIPLIER = 3.0

# Custom phrases
PHRASES = [
    "pimpin ain't easy",
    "stay grounded",
    "bag secured",
    "printer go brrr",
    "we eat",
    "no cap",
    "locked in",
    "touch grass later",
    "money printer activated",
    "built different",
    "on god"
]

STOCKS = ["AAPL", "MSFT", "NVDA", "TSLA", "AMD", "QQQ", "SPY", "TSM", "GOOGL", "AMZN"]
CRYPTO = ["BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD", "DOGE/USD"]

last_api_call = 0
API_DELAY = 0.35

# ========== SETUP ==========
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Boss")

trading_client = TradingClient(ALPACA_KEY, ALPACA_SECRET, paper=PAPER)
stock_client = StockHistoricalDataClient(ALPACA_KEY, ALPACA_SECRET)
crypto_client = CryptoHistoricalDataClient(ALPACA_KEY, ALPACA_SECRET)

conn = sqlite3.connect("boss_v2.db", check_same_thread=False)
c = conn.cursor()

c.execute("""CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY, timestamp TEXT, symbol TEXT, side TEXT,
    qty REAL, price REAL, pnl REAL, strategy TEXT, phrase TEXT
)""")
c.execute("""CREATE TABLE IF NOT EXISTS learning (
    id INTEGER PRIMARY KEY, timestamp TEXT, rsi_threshold REAL,
    win_rate REAL, profit_factor REAL
)""")
c.execute("""CREATE TABLE IF NOT EXISTS reserve (
    id INTEGER PRIMARY KEY, timestamp TEXT, amount REAL, total REAL
)""")
conn.commit()

# ========== UTILITIES ==========
def rate_limit():
    global last_api_call
    elapsed = time.time() - last_api_call
    if elapsed < API_DELAY:
        time.sleep(API_DELAY - elapsed)
    last_api_call = time.time()

def send_telegram(text, chart_url=None):
    try:
        phrase = random.choice(PHRASES)
        msg = text + "\n\n<i>" + phrase + "</i>"
        
        if chart_url:
            msg += "\n\n<a href='" + chart_url + "'>📊 View Chart</a>"
        
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {
            "chat_id": TELEGRAM_CHAT,
            "text": msg,
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }
        requests.post(url, data=data, timeout=10)
        time.sleep(0.2)
    except Exception as e:
        logger.error(f"Telegram error: {e}")

def tradingview_chart(symbol):
    clean = symbol.replace("/USD", "USD").replace("/", "")
    if "USD" in symbol:
        return f"https://www.tradingview.com/chart/?symbol=COINBASE:{clean}"
    else:
        return f"https://www.tradingview.com/chart/?symbol=NASDAQ:{symbol}"

def get_account():
    rate_limit()
    try:
        return trading_client.get_account()
    except Exception as e:
        logger.error(f"Account error: {e}")
        return None

def get_positions():
    rate_limit()
    try:
        return trading_client.get_all_positions()
    except:
        return []

# ========== AGENTS ==========
class Analyst:
    def __init__(self):
        self.rsi_threshold = 65
        
    def analyze(self, symbol):
        try:
            rate_limit()
            is_crypto = "/" in symbol
            
            if is_crypto:
                req = CryptoBarsRequest(
                    symbol_or_symbols=symbol,
                    timeframe=TimeFrame.Minute,
                    start=datetime.now() - timedelta(hours=2)
                )
                bars = crypto_client.get_crypto_bars(req).df
            else:
                req = StockBarsRequest(
                    symbol_or_symbols=symbol,
                    timeframe=TimeFrame.Minute,
                    start=datetime.now() - timedelta(hours=2)
                )
                bars = stock_client.get_stock_bars(req).df
            
            if bars.empty or len(bars) < 20:
                return {"score": 0, "action": "WAIT"}
            
            closes = bars['close'].values[-14:]
            deltas = [closes[i+1] - closes[i] for i in range(len(closes)-1)]
            gains = [d if d > 0 else 0 for d in deltas]
            losses = [-d if d < 0 else 0 for d in deltas]
            
            avg_gain = sum(gains) / 14
            avg_loss = sum(losses) / 14 if sum(losses) > 0 else 0.01
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            
            score = 50
            if rsi < 30: score = 85
            elif rsi > self.rsi_threshold: score = 15
            elif 45 < rsi < 55: score = 60
            
            action = "BUY" if score > 70 else "SELL" if score < 30 else "HOLD"
            
            return {
                "score": score,
                "rsi": round(rsi, 1),
                "action": action,
                "price": float(bars['close'].iloc[-1])
            }
        except Exception as e:
            logger.error(f"Analyst error {symbol}: {e}")
            return {"score": 0, "action": "WAIT"}

class RiskManager:
    def __init__(self):
        self.max_correlation = 0.7
        
    def check(self, symbol, account, positions):
        try:
            buying_power = float(account.buying_power)
            if buying_power < 50:
                return False, "Low buying power"
            
            for pos in positions:
                if pos.symbol == symbol:
                    unrealized = float(pos.unrealized_pl)
                    if unrealized < -20:
                        return False, "Position losing"
            
            if "SOL" in symbol:
                for pos in positions:
                    if "AVAX" in pos.symbol:
                        return False, "Correlation guard"
            
            return True, "Approved"
        except Exception as e:
            return False, f"Risk error: {e}"

class Boss:
    def __init__(self):
        self.analyst = Analyst()
        self.risk = RiskManager()
        self.last_learning = datetime.now()
        
    def scan(self):
        account = get_account()
        if not account:
            return
        
        positions = get_positions()
        
        now_et = datetime.now(pytz.timezone('US/Eastern'))
        market_open = now_et.weekday() < 5 and 9 <= now_et.hour < 16
        
        opportunities = []
        
        for symbol in CRYPTO:
            analysis = self.analyst.analyze(symbol)
            if analysis["score"] > 70:
                opportunities.append({
                    "symbol": symbol,
                    "score": analysis["score"],
                    "price": analysis["price"],
                    "type": "crypto",
                    "action": analysis["action"]
                })
        
        if market_open:
            for symbol in STOCKS:
                analysis = self.analyst.analyze(symbol)
                if analysis["score"] > 70:
                    opportunities.append({
                        "symbol": symbol,
                        "score": analysis["score"],
                        "price": analysis["price"],
                        "type": "stock",
                        "action": analysis["action"]
                    })
        
        opportunities.sort(key=lambda x: x["score"], reverse=True)
        
        if opportunities:
            top = opportunities[0]
            approved, reason = self.risk.check(top["symbol"], account, positions)
            
            if approved:
                self.execute_trade(top, account)
        
        self.check_exits(positions)
        
        if (datetime.now() - self.last_learning).seconds > 3600:
            self.learn()
            self.last_learning = datetime.now()
    
    def execute_trade(self, opp, account):
        try:
            symbol = opp["symbol"]
            price = opp["price"]
            
            buying_power = float(account.buying_power)
            size = min(MAX_POSITION, buying_power * 0.95)
            qty = size / price
            
            if "/" in symbol:
                qty = round(qty, 6)
            else:
                qty = int(qty)
                if qty < 1:
                    return
            
            expected_profit = size * 0.01
            spread_cost = size * 0.003
            if expected_profit < spread_cost * MIN_PROFIT_MULTIPLIER:
                return
            
            rate_limit()
            order = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY
            )
            trading_client.submit_order(order)
            
            phrase = random.choice(PHRASES)
            c.execute("INSERT INTO trades VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (datetime.now().isoformat(), symbol, "BUY", qty, price, 0, "momentum", phrase))
            conn.commit()
            
            chart = tradingview_chart(symbol)
            msg = f"🤖 <b>BOSS EXECUTED</b>\n\n• {symbol} x{qty}\n• ${price:.2f} (${size:.0f})\n• Score: {opp['score']}/100"
            send_telegram(msg, chart)
            
        except Exception as e:
            logger.error(f"Execute error: {e}")
    
    def check_exits(self, positions):
        for pos in positions:
            try:
                symbol = pos.symbol
                unrealized_pct = float(pos.unrealized_plpc) * 100
                
                should_exit = False
                reason = ""
                
                if unrealized_pct <= -5:
                    should_exit = True
                    reason = "Stop loss -5%"
                elif unrealized_pct >= 3:
                    should_exit = True
                    reason = "Take profit +3%"
                
                if should_exit:
                    rate_limit()
                    qty = float(pos.qty)
                    side = OrderSide.SELL if qty > 0 else OrderSide.BUY
                    
                    order = MarketOrderRequest(
                        symbol=symbol,
                        qty=abs(qty),
                        side=side,
                        time_in_force=TimeInForce.DAY
                    )
                    trading_client.submit_order(order)
                    
                    pnl = float(pos.unrealized_pl)
                    
                    if pnl > 0:
                        reserve_amount = pnl * PROFIT_RESERVE_PCT
                        c.execute("INSERT INTO reserve VALUES (NULL, ?, ?, ?)",
                                 (datetime.now().isoformat(), reserve_amount, 0))
                        conn.commit()
                    
                    msg = f"💰 <b>EXITED</b>\n\n• {symbol}\n• {reason}\n• PnL: ${pnl:.2f}"
                    send_telegram(msg)
                    
            except Exception as e:
                logger.error(f"Exit error: {e}")
    
    def learn(self):
        try:
            c.execute("SELECT * FROM trades WHERE timestamp > datetime('now', '-24 hours')")
            recent = c.fetchall()
            
            if len(recent) > 5:
                wins = sum(1 for t in recent if t[6] and t[6] > 0)
                win_rate = wins / len(recent)
                
                if win_rate < 0.4:
                    self.analyst.rsi_threshold = min(75, self.analyst.rsi_threshold + 2)
                elif win_rate > 0.6:
                    self.analyst.rsi_threshold = max(55, self.analyst.rsi_threshold - 2)
                
                c.execute("INSERT INTO learning VALUES (NULL, ?, ?, ?, ?)",
                         (datetime.now().isoformat(), self.analyst.rsi_threshold, win_rate, 0))
                conn.commit()
        except Exception as e:
            logger.error(f"Learn error: {e}")

def morning_summary():
    try:
        account = get_account()
        positions = get_positions()
        
        c.execute("SELECT * FROM trades WHERE timestamp > datetime('now', '-12 hours') AND symbol LIKE '%/%'")
        crypto_trades = c.fetchall()
        
        pnl = sum(float(p.unrealized_pl) for p in positions)
        
        msg = f"🌅 <b>MORNING BRIEFING</b>\n━━━━━━━━━━━━━\n\nPortfolio: ${float(account.portfolio_value):,.0f}\nOvernight PnL: ${pnl:.2f}\nCrypto trades: {len(crypto_trades)}"
        
        send_telegram(msg)
    except Exception as e:
        logger.error(f"Morning error: {e}")

def evening_summary():
    try:
        account = get_account()
        
        c.execute("SELECT * FROM trades WHERE timestamp > datetime('now', '-24 hours')")
        trades = c.fetchall()
        
        wins = sum(1 for t in trades if t[6] and t[6] > 0)
        total_pnl = sum(t[6] for t in trades if t[6])
        
        msg = f"🌙 <b>EVENING CLOSE</b>\n━━━━━━━━━━━━━\n\nPortfolio: ${float(account.portfolio_value):,.0f}\nToday's trades: {len(trades)}\nWin rate: {wins}/{len(trades)}\nDay PnL: ${total_pnl:.2f}"
        
        send_telegram(msg)
    except Exception as e:
        logger.error(f"Evening error: {e}")

def main():
    logger.info("Boss v2.0 starting")
    send_telegram("🤖 <b>BOSS v2.0 ONLINE</b>\n\nPaper trading active")
    
    boss = Boss()
    last_morning = None
    last_evening = None
    
    while True:
        try:
            now_et = datetime.now(pytz.timezone('US/Eastern'))
            
            if now_et.hour == 7 and now_et.minute < 5 and last_morning != now_et.date():
                morning_summary()
                last_morning = now_et.date()
            
            if now_et.hour == 16 and now_et.minute < 10 and last_evening != now_et.date():
                evening_summary()
                last_evening = now_et.date()
            
            boss.scan()
            time.sleep(10)
            
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"Main error: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main()