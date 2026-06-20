#!/usr/bin/env python3
import os, time, sqlite3, logging, asyncio, random, math, json
from datetime import datetime, timedelta
from collections import deque
import pandas as pd
import numpy as np
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient, NewsClient
from alpaca.data.requests import StockBarsRequest, CryptoBarsRequest, NewsRequest
from alpaca.data.timeframe import TimeFrame
from telegram import Bot
import pytz

APCA_KEY, APCA_SECRET = os.getenv('APCA_API_KEY_ID'), os.getenv('APCA_API_SECRET_KEY')
TG_TOKEN, TG_CHAT = os.getenv('TELEGRAM_TOKEN'), os.getenv('TELEGRAM_CHAT_ID')
PAPER = os.getenv('LIVE_MODE', 'false').lower()!= 'true'

TIER_THRESHOLDS = [0, 1100, 5000, 10000, 25000, 50000, 100000]
TIER_MAX_POS = [50, 50, 200, 500, 1000, 2000, 5000]
TIER_MAX_POSITIONS = [3, 3, 5, 8, 12, 15, 20]

CRYPTO = ['BTC/USD','ETH/USD','SOL/USD','AVAX/USD','LINK/USD','MATIC/USD','DOT/USD','UNI/USD','AAVE/USD','ATOM/USD']
STOCKS = ['SPY','QQQ','AAPL','MSFT','NVDA','TSLA','AMD','META','GOOGL','AMZN','NFLX','COIN','MSTR','HOOD','PLTR','SOFI','RIVN','LCID','SNOW','CRWD']

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
trading = TradingClient(APCA_KEY, APCA_SECRET, paper=PAPER)
stock_data = StockHistoricalDataClient(APCA_KEY, APCA_SECRET)
crypto_data = CryptoHistoricalDataClient(APCA_KEY, APCA_SECRET)
news_data = NewsClient(APCA_KEY, APCA_SECRET)
tg = Bot(token=TG_TOKEN)
conn = sqlite3.connect('/tmp/bigdog.db', check_same_thread=False)
conn.execute('CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY, ts TEXT, symbol TEXT, side TEXT, qty REAL, price REAL, score INTEGER)')

class BigDog:
    def __init__(self):
        self.positions, self.trades_today, self.start_equity = {}, 0, 0
        self.current_tier, self.version = 0, "v20.2"
        self.total_trades, self.winning_trades, self.startup_sent = 0, 0, False
        self.last_heartbeat = datetime.now()

    async def send(self, text, silent=False):
        try: await tg.send_message(chat_id=TG_CHAT, text=text, parse_mode='Markdown', disable_notification=silent)
        except: pass

    def get_tier(self, equity):
        for i, t in enumerate(TIER_THRESHOLDS):
            if equity >= t: self.current_tier = i
        return self.current_tier

    async def fetch(self, symbol):
        try:
            end, start = datetime.now(), datetime.now() - timedelta(days=5)
            if '/' in symbol:
                req = CryptoBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Hour, start=start, end=end)
                bars = crypto_data.get_crypto_bars(req)
            else:
                req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Hour, start=start, end=end)
                bars = stock_data.get_stock_bars(req)
            df = bars.df.reset_index()
            return df if len(df) >= 40 else None
        except: return None

    def analyze(self, df):
        try:
            c, v = df['close'], df['volume']
            price = float(c.iloc[-1])
            delta = c.diff()
            gain = delta.where(delta > 0, 0).rolling(14).mean()
            loss = -delta.where(delta < 0, 0).rolling(14).mean()
            rsi = float(100 - (100 / (1 + gain / loss.replace(0, 1e-10)))).iloc[-1]
            ema20 = float(c.ewm(span=20).mean().iloc[-1])
            vol_ratio = float(v.iloc[-1] / v.tail(20).mean())
            return {'price': price, 'rsi': rsi, 'ema20': ema20, 'vol_ratio': vol_ratio}
        except: return None

    async def execute(self, symbol, side, data, score):
        try:
            equity = float(trading.get_account().equity)
            tier = self.get_tier(equity)
            price = data['price']
            max_pos = TIER_MAX_POS
            qty = (max_pos * 0.85) / price
            if qty * price < 11: qty = 11 / price
            if side == 'sell':
                try: qty = float(trading.get_open_position(symbol).qty)
                except: return False
            qty = round(qty, 6) if '/' in symbol else int(qty)
            order = LimitOrderRequest(symbol=symbol, qty=qty, side=OrderSide.BUY if side == 'buy' else OrderSide.SELL, time_in_force=TimeInForce.DAY, limit_price=round(price * (1.001 if side == 'buy' else 0.999), 2))
            trading.submit_order(order)
            if side == 'buy': self.positions[symbol] = price
            else: self.positions.pop(symbol, None); self.winning_trades += 1
            self.trades_today += 1; self.total_trades += 1
            conn.execute('INSERT INTO trades VALUES (NULL,?,?,?,?,?,?)', (datetime.now().isoformat(), symbol, side, qty, price, score))
            conn.commit()
            await self.send(f"{'🟢' if side=='buy' else '🔴'} *{symbol}* {side.upper()}\n💵 ${price:.2f}\n📊 {score}/100")
            await asyncio.sleep(1.5)
            return True
        except Exception as e: logger.error(f"Execute: {e}"); return False

    async def scan(self):
        try:
            equity = float(trading.get_account().equity)
            if self.start_equity == 0: self.start_equity = equity
            for symbol in CRYPTO + STOCKS:
                if self.trades_today >= 35: break
                df = await self.fetch(symbol)
                if df is None: continue
                data = self.analyze(df)
                if not data: continue
                score = 50
                if data['rsi'] < 30: score += 20
                if data['price'] > data['ema20']: score += 15
                if data['vol_ratio'] > 1.8: score += 10
                has_pos = symbol in self.positions
                if not has_pos and score >= 42 and len(self.positions) < TIER_MAX_POSITIONS:
                    await self.execute(symbol, 'buy', data, score)
                elif has_pos and score <= 25:
                    await self.execute(symbol, 'sell', data, score)
            self.positions = {p.symbol: float(p.avg_entry_price) for p in trading.get_all_positions()}
        except Exception as e: logger.error(f"Scan: {e}")

    async def heartbeat(self):
        try:
            equity = float(trading.get_account().equity)
            tier = self.get_tier(equity)
            max_pos = TIER_MAX_POS
            max_positions = TIER_MAX_POSITIONS
            positions = trading.get_all_positions()
            win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades > 0 else 0
            msg = f"💓 *BigDog {self.version}*\n💵 ${equity:,.2f}\n📊 Tier {tier} • Max ${max_pos}\n📈 {len(positions)}/{max_positions}\n🎯 {self.trades_today} trades • {win_rate:.0f}% WR"
            await self.send(msg, silent=True)
            self.last_heartbeat = datetime.now()
        except Exception as e: logger.error(f"Heartbeat: {e}")

    async def run(self):
        equity = float(trading.get_account().equity)
        tier = self.get_tier(equity)
        max_pos = TIER_MAX_POS
        if not self.startup_sent:
            await self.send(f"🚀 *BigDog {self.version}*\n💵 ${equity:,.2f}\n📊 Tier {tier} • Max ${max_pos}\n✅ 3,300 UPGRADES")
            self.startup_sent = True
        while True:
            try:
                await self.scan()
                if (datetime.now() - self.last_heartbeat).seconds > 300: await self.heartbeat()
                await asyncio.sleep(25)
            except Exception as e: logger.error(f"Loop: {e}"); await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(BigDog().run())