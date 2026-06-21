#!/usr/bin/env python3
import os, asyncio, json, time, sqlite3, logging, random
from datetime import datetime, timedelta
from collections import deque, defaultdict
import numpy as np
import pandas as pd
from aiohttp import web, WSMsgType
from loguru import logger
import pytz
from functools import lru_cache
import hashlib

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import LimitOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest, CryptoBarsRequest
    from alpaca.data.timeframe import TimeFrame
    ALPACA_OK = True
except ImportError:
    ALPACA_OK = False

try:
    from telegram import Bot
    TELEGRAM_OK = True
except ImportError:
    TELEGRAM_OK = False

PORT = int(os.getenv('PORT', 8080))
APCA_KEY = os.getenv('APCA_API_KEY_ID', '')
APCA_SECRET = os.getenv('APCA_API_SECRET_KEY', '')
TG_TOKEN = os.getenv('TELEGRAM_TOKEN', '')
TG_CHAT = os.getenv('TELEGRAM_CHAT_ID', '')
PAPER = os.getenv('PAPER_TRADING', 'true').lower() == 'true'

CONFIG = {
    'version': '5.1.0', 'crypto_focus': 0.70, 'scan_interval': 8, 'heartbeat_minutes': 3,
    'max_daily_loss': 20, 'max_trades_per_day': 40, 'min_notional': 10.0,
    'buy_score_min': 58, 'sell_score_max': 25, 'max_positions': 12,
    'risk_per_trade': 0.015, 'kelly_fraction': 0.25, 'spread_limit': 0.005,
    'min_volume_24h': 1000000,
    'tier_thresholds': [0, 1100, 5000, 10000, 25000, 50000, 100000, 250000, 500000, 1000000],
    'tier_max_pos': [50, 50, 200, 500, 1000, 2000, 5000, 10000, 25000, 50000],
}

CORE_PHRASES = ["checkin stocks, not flipping rocks","real bosses don't talk they just sit back and listen","First you get the money then you get the power","get up and get some money","bag secured","paper chaser","clean money over here","generational wealth","Stack that paper up and then make boss moves","countin' dividends, not sheep"]
BUY_PHRASES = ["🐕 BIG DOG BUY","💎 DIAMOND HANDS","🚀 TO THE MOON","🔥 FIRE ENTRY","💰 MONEY PRINTER"]
SELL_PHRASES = ["💸 SECURED BAG","🏦 BANK IT","✌️ PEACE OUT","💵 CASH OUT","🎰 HOUSE MONEY"]

CRYPTO_SYMBOLS = ['BTC/USD','ETH/USD','SOL/USD','AVAX/USD','LINK/USD','DOGE/USD','ADA/USD','DOT/USD','MATIC/USD','UNI/USD','ATOM/USD','ALGO/USD','FIL/USD','XRP/USD','LTC/USD']
STOCK_SYMBOLS = ['SPY','QQQ','AAPL','MSFT','NVDA','TSLA','AMD']
ALL_SYMBOLS = CRYPTO_SYMBOLS + STOCK_SYMBOLS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

trading = TradingClient(APCA_KEY, APCA_SECRET, paper=PAPER) if ALPACA_OK and APCA_KEY else None
stock_data = StockHistoricalDataClient(APCA_KEY, APCA_SECRET) if ALPACA_OK and APCA_KEY else None
crypto_data = CryptoHistoricalDataClient(APCA_KEY, APCA_SECRET) if ALPACA_OK and APCA_KEY else None
tg = Bot(token=TG_TOKEN) if TELEGRAM_OK and TG_TOKEN else None

conn = sqlite3.connect('/tmp/beast_v51.db', check_same_thread=False, isolation_level=None)
conn.executescript('CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY, timestamp TEXT, symbol TEXT, side TEXT, quantity REAL, price REAL, notional REAL, reason TEXT, phrase TEXT, score INTEGER, rsi REAL, tier INTEGER);')

class PhraseManager:
    def __init__(self):
        self.used = deque(maxlen=10)
    def get_core(self):
        p = random.choice([x for x in CORE_PHRASES if x not in self.used] or CORE_PHRASES)
        self.used.append(p)
        return p
    def get_buy(self): return random.choice(BUY_PHRASES)
    def get_sell(self): return random.choice(SELL_PHRASES)

class BeastEngine:
    def __init__(self):
        self.phrases = PhraseManager()
        self.positions = {}
        self.trades_today = 0
        self.daily_pnl = 0.0
        self.start_equity = 0.0
        self.last_heartbeat = datetime.now()
        self.winning_trades = 0
        self.losing_trades = 0
        self.ws_clients = set()

    async def send_telegram(self, text, silent=False):
        if not tg or not TG_CHAT: return
        try:
            await tg.send_message(chat_id=TG_CHAT, text=f"{self.phrases.get_core()}\n\n{text}", parse_mode='Markdown', disable_notification=silent)
        except: pass

    def get_tier(self, equity):
        for i in range(len(CONFIG['tier_thresholds'])-1, -1, -1):
            if equity >= CONFIG['tier_thresholds'][i]: return i
        return 0

    def is_market_hours(self, is_crypto=False):
        et = datetime.now(pytz.timezone('US/Eastern'))
        if 0 <= et.hour < 8: return False
        if is_crypto: return True
        if et.weekday() >= 5: return False
        return 9 <= et.hour < 16 or (et.hour == 9 and et.minute >= 30)

    async def fetch_data(self, symbol, timeframe='1h'):
        try:
            if not ALPACA_OK: return self.generate_demo(symbol)
            is_crypto = '/' in symbol
            end = datetime.now()
            start = end - timedelta(days=30)
            tf = TimeFrame.Hour
            if is_crypto and crypto_data:
                req = CryptoBarsRequest(symbol_or_symbols=symbol, timeframe=tf, start=start, end=end)
                bars = crypto_data.get_crypto_bars(req)
            elif stock_data:
                req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=tf, start=start, end=end)
                bars = stock_data.get_stock_bars(req)
            else:
                return self.generate_demo(symbol)
            df = bars.df.reset_index()
            return df if len(df) >= 50 else None
        except:
            return self.generate_demo(symbol)

    def generate_demo(self, symbol):
        base = 65000 if 'BTC' in symbol else 3500 if 'ETH' in symbol else 150
        price = base
        data = []
        now = datetime.now()
        for i in range(200):
            change = (np.random.random()-0.5)*base*0.002
            close = price + change
            data.append({'timestamp': now - timedelta(minutes=200-i), 'open': price, 'high': max(price,close)*1.001, 'low': min(price,close)*0.999, 'close': close, 'volume': np.random.randint(1000,10000)})
            price = close
        return pd.DataFrame(data)

    def analyze(self, symbol, df):
        try:
            closes = df['close']
            price = float(closes.iloc[-1])
            delta = closes.diff()
            gain = delta.where(delta>0,0).rolling(14).mean()
            loss = -delta.where(delta<0,0).rolling(14).mean()
            rs = gain/loss.replace(0,1e-10)
            rsi = float(100-(100/(1+rs.iloc[-1])))
            ema20 = float(closes.ewm(span=20).mean().iloc[-1])
            ema50 = float(closes.ewm(span=50).mean().iloc[-1])
            score = 50
            if price > ema20 > ema50: score += 15
            elif price < ema20 < ema50: score -= 15
            if rsi < 30: score += 10
            elif rsi > 70: score -= 10
            score = max(0, min(100, score))
            signal = 'BUY' if score >= CONFIG['buy_score_min'] else 'SELL' if score <= CONFIG['sell_score_max'] else 'HOLD'
            return {'symbol': symbol, 'price': round(price,2), 'rsi': round(rsi,1), 'score': int(score), 'signal': signal, 'reason': 'Trend', 'confidence': 0.7, 'atr': price*0.02, 'kelly_size': 50}
        except:
            return None

    async def execute_trade(self, symbol, side, analysis, tier):
        try:
            if self.trades_today >= CONFIG['max_trades_per_day']: return False
            account = trading.get_account()
            equity = float(account.equity)
            price = analysis['price']
            qty = max(CONFIG['min_notional']/price, 0.001)
            if '/' in symbol: qty = round(qty,6)
            else: qty = int(qty)
            limit_price = round(price*1.0005 if side=='BUY' else price*0.9995,4)
            order = LimitOrderRequest(symbol=symbol, qty=qty, side=OrderSide.BUY if side=='BUY' else OrderSide.SELL, time_in_force=TimeInForce.DAY, limit_price=limit_price)
            trading.submit_order(order)
            phrase = self.phrases.get_buy() if side=='BUY' else self.phrases.get_sell()
            conn.execute("INSERT INTO trades (timestamp,symbol,side,quantity,price,notional,reason,phrase,score,rsi,tier) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (datetime.now().isoformat(),symbol,side,qty,price,qty*price,analysis['reason'],phrase,analysis['score'],analysis['rsi'],tier))
            self.trades_today += 1
            await self.send_telegram(f"{'🟢' if side=='BUY' else '🔴'} **{symbol} {side}**\n{phrase}\n\n${price} × {qty}")
            return True
        except Exception as e:
            logger.error(f"Trade error: {e}")
            return False

    async def run(self):
        logger.info("BEAST v5.1 ACTIVATING")
        account = trading.get_account()
        equity = float(account.equity)
        tier = self.get_tier(equity)
        self.start_equity = equity
        max_pos = CONFIG['tier_max_pos'][tier]
        await self.send_telegram(f"🤖 **BEAST MODE v5.1 ACTIVATED**\n\n{'PAPER' if PAPER else 'LIVE'}\n\n💵 ${equity:,.2f}\n📊 Tier {tier} • Max ${max_pos:,}\n🏛️ Tennessee 0% tax")

        while True:
            try:
                et = datetime.now(pytz.timezone('US/Eastern'))
                if 0 <= et.hour < 8:
                    await asyncio.sleep(60)
                    continue
                account = trading.get_account()
                equity = float(account.equity)
                self.daily_pnl = equity - self.start_equity

                symbols = random.sample(CRYPTO_SYMBOLS, 10) + random.sample(STOCK_SYMBOLS, 4)
                for symbol in symbols:
                    if not self.is_market_hours('/' in symbol): continue
                    df = await self.fetch_data(symbol)
                    if df is None: continue
                    analysis = self.analyze(symbol, df)
                    if analysis and analysis['signal'] == 'BUY' and analysis['score'] >= 58:
                        if len(self.positions) < CONFIG['max_positions'] and symbol not in self.positions:
                            await self.execute_trade(symbol, 'BUY', analysis, tier)
                            await asyncio.sleep(2)

                if (datetime.now() - self.last_heartbeat).seconds >= 180:
                    self.last_heartbeat = datetime.now()
                    await self.send_telegram(f"💓 ${equity:,.2f} ({self.daily_pnl:+.2f}) | {self.trades_today} trades", silent=True)

                await asyncio.sleep(CONFIG['scan_interval'])
            except Exception as e:
                logger.error(f"Loop: {e}")
                await asyncio.sleep(30)

engine = BeastEngine()

async def health(request): return web.json_response({'status':'online'})
async def api_portfolio(request):
    account = trading.get_account()
    equity = float(account.equity)
    cash = float(account.cash)
    positions = trading.get_all_positions()
    return web.json_response({
        'equity': equity, 'cash': cash, 'daily_pnl': engine.daily_pnl,
        'positions': [{'symbol': p.symbol, 'qty': float(p.qty), 'price': float(p.avg_entry_price), 'market_value': float(p.market_value), 'unrealized_pl': float(p.unrealized_pl), 'unrealized_plpc': float(p.unrealized_plpc)*100} for p in positions],
        'win_rate': 0, 'tier': engine.get_tier(equity)
    })

def create_app():
    app = web.Application()
    app.router.add_get('/health', health)
    app.router.add_get('/api/portfolio', api_portfolio)
    app.router.add_get('/', lambda r: web.FileResponse('index.html'))
    return app

async def main():
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    logger.info(f"Server on {PORT}")
    await engine.run()

if __name__ == '__main__':
    asyncio.run(main())