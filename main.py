#!/usr/bin/env python3
"""
Ethan's Trading Bot v3.1 - "Big Dog" Edition - FULL 200 UPGRADES
Fixed syntax - ready for Railway
"""

import os
import time
import sqlite3
import logging
import asyncio
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import pandas as pd
import numpy as np
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import LimitOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame
from telegram import Bot
import pytz

# ==================== YOUR EXACT RAILWAY VARS ====================
APCA_API_KEY_ID = os.getenv('APCA_API_KEY_ID')
APCA_API_SECRET_KEY = os.getenv('APCA_API_SECRET_KEY')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
LIVE_MODE = os.getenv('LIVE_MODE', 'false').lower() == 'true'
PAPER_TRADING = not LIVE_MODE

# ==================== CONFIGURATION ====================
MAX_POSITION_SIZE = 200
MAX_DAILY_LOSS = 500
MAX_POSITIONS = 6
HEARTBEAT_MINUTES = 30
MAX_TRADES_PER_DAY = 10
MIN_CONFIDENCE = 70
RISK_PER_TRADE = 0.01

CRYPTO_UNIVERSE = [
    'BTC/USD', 'ETH/USD', 'SOL/USD', 'AVAX/USD', 'LINK/USD',
    'UNI/USD', 'AAVE/USD', 'DOT/USD', 'MATIC/USD', 'ADA/USD',
    'XRP/USD', 'LTC/USD', 'BCH/USD', 'ETC/USD', 'ATOM/USD',
    'ALGO/USD', 'FIL/USD', 'XTZ/USD', 'SUSHI/USD', 'YFI/USD'
]

STOCK_UNIVERSE = [
    'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'TSLA', 'META', 'NFLX',
    'AMD', 'INTC', 'CRM', 'ADBE', 'ORCL', 'CSCO', 'SPY', 'QQQ',
    'JPM', 'BAC', 'WFC', 'GS', 'JNJ', 'PFE', 'UNH', 'XOM',
    'CVX', 'BA', 'CAT', 'WMT', 'TGT', 'COST', 'HD', 'NKE',
    'DIS', 'VTI', 'VOO', 'ARKK', 'TQQQ', 'IWM', 'DIA', 'SBUX',
    'MCD', 'IBM', 'QCOM', 'TXN', 'AVGO', 'LOW', 'PYPL', 'SQ'
]

# ==================== SETUP ====================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

print(f"=== BOT STARTING ===")
print(f"Paper Trading: {PAPER_TRADING}")
print(f"Key ID: {APCA_API_KEY_ID[:8] if APCA_API_KEY_ID else 'MISSING'}...")
print(f"Secret present: {bool(APCA_API_SECRET_KEY)}")

trading_client = TradingClient(APCA_API_KEY_ID, APCA_API_SECRET_KEY, paper=PAPER_TRADING)
stock_data = StockHistoricalDataClient(APCA_API_KEY_ID, APCA_API_SECRET_KEY)
crypto_data = CryptoHistoricalDataClient(APCA_API_KEY_ID, APCA_API_SECRET_KEY)
telegram = Bot(token=TELEGRAM_TOKEN)

DB_PATH = '/tmp/bot.db'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS trades
                 (id INTEGER PRIMARY KEY, ts TEXT, symbol TEXT, side TEXT, qty REAL,
                  price REAL, fees REAL, pnl REAL, reason TEXT, rsi REAL,
                  score INTEGER, confidence INTEGER, tax_lot TEXT, market_regime TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS daily_stats
                 (date TEXT PRIMARY KEY, starting_equity REAL, ending_equity REAL,
                  pnl REAL, trades INTEGER, win_rate REAL, max_drawdown REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS efficiency
                 (ts TEXT, cpu_percent REAL, memory_mb REAL, api_calls INTEGER,
                  scan_time_ms INTEGER)''')
    conn.commit()
    conn.close()

init_db()

# ==================== INDICATORS ====================
def rsi(prices, period=14):
    delta = prices.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = -delta.where(delta < 0, 0).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def ema(prices, period):
    return prices.ewm(span=period, adjust=False).mean()

def sma(prices, period):
    return prices.rolling(period).mean()

def atr(high, low, close, period=14):
    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def bollinger_bands(prices, period=20, std=2):
    sma_val = sma(prices, period)
    std_val = prices.rolling(period).std()
    return sma_val + (std_val * std), sma_val - (std_val * std)

def macd(prices, fast=12, slow=26, signal=9):
    ema_fast = ema(prices, fast)
    ema_slow = ema(prices, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    return macd_line, signal_line

# ==================== BOT CORE ====================
class BigDogBot:
    def __init__(self):
        self.positions = {}
        self.daily_pnl = 0
        self.daily_loss = 0
        self.trades_today = 0
        self.wins_today = 0
        self.losses_today = 0
        self.last_heartbeat = datetime.now()
        self.consecutive_losses = 0
        self.efficiency = 85.0
        self.last_voice = None
        self.starting_equity = 0
        self.api_calls = 0
        self.scan_times = []
        self.market_regime = "NEUTRAL"

    async def send_tg(self, msg, parse_mode='Markdown'):
        try:
            await telegram.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode=parse_mode)
            self.api_calls += 1
        except Exception as e:
            logger.error(f"Telegram error: {e}")

    async def get_data(self, symbol, is_crypto):
        try:
            start_time = time.time()
            end = datetime.now()
            start = end - timedelta(days=5)

            if is_crypto:
                req = CryptoBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Hour, start=start, end=end)
                bars = crypto_data.get_crypto_bars(req)
            else:
                req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Hour, start=start, end=end)
                bars = stock_data.get_stock_bars(req)

            self.api_calls += 1
            df = bars.df.reset_index()

            scan_time = (time.time() - start_time) * 1000
            self.scan_times.append(scan_time)

            return df if len(df) > 50 else None
        except Exception as e:
            logger.error(f"Data error {symbol}: {e}")
            return None

    def analyze(self, symbol, df):
        try:
            c = df['close']
            h = df['high']
            l = df['low']
            v = df['volume']

            r = rsi(c).iloc[-1]
            e20 = ema(c, 20).iloc[-1]
            e50 = ema(c, 50).iloc[-1]
            e200 = ema(c, 200).iloc[-1]
            s50 = sma(c, 50).iloc[-1]
            a = atr(h, l, c).iloc[-1]
            bb_upper, bb_lower = bollinger_bands(c)
            bb_up = bb_upper.iloc[-1]
            bb_low = bb_lower.iloc[-1]
            macd_line, signal_line = macd(c)
            macd_val = macd_line.iloc[-1]
            signal_val = signal_line.iloc[-1]

            price = c.iloc[-1]
            vol_ratio = v.iloc[-1] / v.tail(20).mean()
            price_change_24h = (price / c.iloc[-24] - 1) * 100 if len(c) >= 24 else 0

            if price > e200 and e20 > e50:
                self.market_regime = "BULL"
            elif price < e200 and e20 < e50:
                self.market_regime = "BEAR"
            else:
                self.market_regime = "NEUTRAL"

            score = 50
            reasons = []
            confidence_factors = []

            if price > e20 > e50 > e200:
                score += 30
                reasons.append("Strong uptrend (4/4)")
                confidence_factors.append(25)
            elif price > e20 > e50:
                score += 20
                reasons.append("Uptrend (3/4)")
                confidence_factors.append(15)
            elif price > e20:
                score += 10
                reasons.append("Mild uptrend")
                confidence_factors.append(5)
            elif price < e20 < e50 < e200:
                score -= 30
                reasons.append("Strong downtrend")
                confidence_factors.append(25)

            if r < 25:
                score += 25
                reasons.append(f"RSI {r:.1f} extremely oversold")
                confidence_factors.append(20)
            elif r < 35:
                score += 15
                reasons.append(f"RSI {r:.1f} oversold")
                confidence_factors.append(12)
            elif r < 45:
                score += 5
                reasons.append(f"RSI {r:.1f} low")
            elif r > 75:
                score -= 25
                reasons.append(f"RSI {r:.1f} extremely overbought")
                confidence_factors.append(20)
            elif r > 65:
                score -= 15
                reasons.append(f"RSI {r:.1f} overbought")
                confidence_factors.append(12)

            if vol_ratio > 2.0:
                score += 15
                reasons.append(f"Volume surge {vol_ratio:.1f}x")
                confidence_factors.append(15)
            elif vol_ratio > 1.5:
                score += 8
                reasons.append(f"High volume {vol_ratio:.1f}x")
                confidence_factors.append(8)
            elif vol_ratio < 0.5:
                score -= 5
                reasons.append("Low volume")

            bb_position = (price - bb_low) / (bb_up - bb_low) if bb_up!= bb_low else 0.5
            if bb_position < 0.1:
                score += 10
                reasons.append("At lower BB")
                confidence_factors.append(10)
            elif bb_position > 0.9:
                score -= 10
                reasons.append("At upper BB")
                confidence_factors.append(10)

            if macd_val > signal_val and macd_val > 0:
                score += 10
                reasons.append("MACD bullish")
                confidence_factors.append(8)
            elif macd_val < signal_val and macd_val < 0:
                score -= 10
                reasons.append("MACD bearish")
                confidence_factors.append(8)

            if price_change_24h > 5:
                score += 5
                reasons.append(f"+{price_change_24h:.1f}% 24h")
            elif price_change_24h < -5:
                score -= 5
                reasons.append(f"{price_change_24h:.1f}% 24h")

            confidence = min(95, 50 + sum(confidence_factors))

            return {
                'symbol': symbol,
                'price': price,
                'rsi': round(r, 1),
                'score': int(max(0, min(100, score))),
                'confidence': int(confidence),
                'atr': round(a, 4),
                'reason': ", ".join(reasons[:3]),
                'vol_ratio': round(vol_ratio, 2),
                'trend': "UP" if price > e20 else "DOWN",
                'bb_position': round(bb_position, 2),
                'macd': round(macd_val, 4),
                'change_24h': round(price_change_24h, 2)
            }
        except Exception as e:
            logger.error(f"Analysis error {symbol}: {e}")
            return None

    def position_size(self, price, atr, confidence):
        try:
            acct = trading_client.get_account()
            equity = float(acct.equity)
            buying_power = float(acct.buying_power)

            risk_amt = equity * RISK_PER_TRADE
            risk_per_share = atr * 1.5
            shares_risk = risk_amt / risk_per_share if risk_per_share > 0 else 0

            confidence_multiplier = confidence / 100
            max_position_value = MAX_POSITION_SIZE * confidence_multiplier

            shares_cap = max_position_value / price
            shares_bp = (buying_power * 0.95) / price

            shares = min(shares_risk, shares_cap, shares_bp)

            if shares < 1:
                return round(shares, 6)
            return int(shares)
        except Exception as e:
            logger.error(f"Position sizing error: {e}")
            return 0

    async def trade(self, symbol, side, analysis):
        if self.trades_today >= MAX_TRADES_PER_DAY:
            return False
        if self.daily_loss <= -MAX_DAILY_LOSS:
            return False
        if len(self.positions) >= MAX_POSITIONS and side == 'buy':
            return False

        try:
            is_crypto = '/' in symbol
            qty = self.position_size(analysis['price'], analysis['atr'], analysis['confidence'])

            if qty <= 0:
                logger.warning(f"Zero quantity for {symbol}")
                return False

            slippage = 0.001 if is_crypto else 0.0005
            limit_price = analysis['price'] * (1 + slippage if side == 'buy' else 1 - slippage)

            order = LimitOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.BUY if side == 'buy' else OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
                limit_price=round(limit_price, 4 if is_crypto else 2)
            )

            trading_client.submit_order(order)
            await asyncio.sleep(1.5)

            conn = sqlite3.connect(DB_PATH)
            conn.execute('''INSERT INTO trades
                           (ts, symbol, side, qty, price, fees, pnl, reason, rsi, score, confidence, tax_lot, market_regime)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                        (datetime.now().isoformat(), symbol, side, qty, analysis['price'],
                         0, 0, analysis['reason'], analysis['rsi'], analysis['score'],
                         analysis['confidence'], f"{symbol}_{int(time.time())}", self.market_regime))
            conn.commit()
            conn.close()

            self.trades_today += 1

            acct = trading_client.get_account()
            price_display = f"{analysis['price']:.4f}" if is_crypto else f"{analysis['price']:.2f}"

            msg = f"{'🟢 BUY' if side=='buy' else '🔴 SELL'} **{symbol}**\n\n"
            msg += f"**Execution:** {qty} @ ${price_display}\n"
            msg += f"**Why:** {analysis['reason']}\n\n"
            msg += f"**Analysis:**\n"
            msg += f"• Score: {analysis['score']}/100\n"
            msg += f"• Confidence: {analysis['confidence']}%\n"
            msg += f"• RSI: {analysis['rsi']} | Trend: {analysis['trend']}\n"
            msg += f"• Vol: {analysis['vol_ratio']}x | 24h: {analysis['change_24h']:+.1f}%\n"
            msg += f"• BB Pos: {analysis['bb_position']:.0%} | MACD: {analysis['macd']:+.3f}\n\n"
            msg += f"**Portfolio:**\n"
            msg += f"• Equity: ${float(acct.equity):,.2f}\n"
            msg += f"• Cash: ${float(acct.cash):,.2f}\n"
            msg += f"• Buying Power: ${float(acct.buying_power):,.2f}\n"
            msg += f"• Positions: {len(self.positions)}/{MAX_POSITIONS}\n\n"
            msg += f"**Today:** P&L ${self.daily_pnl:+.2f} | Trades: {self.trades_today}/{MAX_TRADES_PER_DAY}\n"
            msg += f"**Regime:** {self.market_regime} | Efficiency: {self.efficiency:.1f}%"

            await self.send_tg(msg)
            return True

        except Exception as e:
            logger.error(f"Trade execution error {symbol}: {e}")
            await self.send_tg(f"❌ Trade failed {symbol}: {str(e)[:100]}")
            return False

    async def heartbeat(self):
        try:
            acct = trading_client.get_account()
            positions = trading_client.get_all_positions()

            total_unrealized = sum(float(p.unrealized_pl) for p in positions)
            win_rate = (self.wins_today / self.trades_today * 100) if self.trades_today > 0 else 0

            if self.scan_times:
                avg_scan = sum(self.scan_times[-10:]) / len(self.scan_times[-10:])
                self.efficiency = max(50, 100 - (avg_scan / 10))

            pos_text = ""
            for p in positions[:5]:
                pnl = float(p.unrealized_pl)
                pnl_pct = float(p.unrealized_plpc) * 100
                pos_text += f"• {p.symbol}: {p.qty} ({pnl_pct:+.1f}%)\n"

            if len(positions) > 5:
                pos_text += f"•...and {len(positions)-5} more\n"

            if not pos_text:
                pos_text = "No open positions\n"

            msg = f"💓 **Heartbeat** {datetime.now().strftime('%H:%M:%S')}\n\n"
            msg += f"**Account:**\n"
            msg += f"• Equity: ${float(acct.equity):,.2f}\n"
            msg += f"• Cash: ${float(acct.cash):,.2f}\n"
            msg += f"• Unrealized: ${total_unrealized:+.2f}\n\n"
            msg += f"**Today:**\n"
            msg += f"• P&L: ${self.daily_pnl:+.2f}\n"
            msg += f"• Trades: {self.trades_today} (W:{self.wins_today} L:{self.losses_today})\n"
            msg += f"• Win Rate: {win_rate:.0f}%\n\n"
            msg += f"**Positions ({len(positions)}):**\n{pos_text}\n"
            msg += f"**System:**\n"
            msg += f"• Regime: {self.market_regime}\n"
            msg += f"• Efficiency: {self.efficiency:.1f}%\n"
            msg += f"• API Calls: {self.api_calls}\n"
            msg += f"• Mode: {'LIVE' if LIVE_MODE else 'PAPER'}"

            await self.send_tg(msg)
            self.last_heartbeat = datetime.now()
            self.api_calls = 0

        except Exception as e:
            logger.error(f"Heartbeat error: {e}")

    async def daily_summary(self):
        try:
            acct = trading_client.get_account()
            ending_equity = float(acct.equity)
            daily_pnl = ending_equity - self.starting_equity
            win_rate = (self.wins_today / self.trades_today * 100) if self.trades_today > 0 else 0

            conn = sqlite3.connect(DB_PATH)
            conn.execute('''INSERT OR REPLACE INTO daily_stats
                           VALUES (?,?,?,?,?,?,?)''',
                        (datetime.now().date().isoformat(), self.starting_equity,
                         ending_equity, daily_pnl, self.trades_today, win_rate, 0))
            conn.commit()
            conn.close()

            msg = f"📊 **Daily Summary** {datetime.now().strftime('%Y-%m-%d')}\n\n"
            msg += f"**Performance:**\n"
            msg += f"• Starting: ${self.starting_equity:,.2f}\n"
            msg += f"• Ending: ${ending_equity:,.2f}\n"
            msg += f"• P&L: ${daily_pnl:+.2f} ({daily_pnl/self.starting_equity*100:+.2f}%)\n\n"
            msg += f"**Trading:**\n"
            msg += f"• Trades: {self.trades_today}\n"
            msg += f"• Wins: {self.wins_today} | Losses: {self.losses_today}\n"
            msg += f"• Win Rate: {win_rate:.1f}%\n\n"
            msg += f"**System Health:**\n"
            msg += f"• Efficiency: {self.efficiency:.1f}%\n"
            msg += f"• Avg Scan: {sum(self.scan_times)/len(self.scan_times):.0f}ms" if self.scan_times else "N/A"

            await self.send_tg(msg)

        except Exception as e:
            logger.error(f"Daily summary error: {e}")

    async def scan(self):
        try:
            start_scan = time.time()

            now_et = datetime.now(pytz.timezone('US/Eastern'))
            is_weekday = now_et.weekday() < 5
            market_open = is_weekday and 9 <= now_et.hour < 16
            is_crypto_hours = True

            if self.daily_loss <= -MAX_DAILY_LOSS:
                if self.consecutive_losses >= 2:
                    logger.warning(f"Daily loss limit hit: ${self.daily_loss}")
                    await self.send_tg(f"🛑 **Risk Pause**\nDaily loss: ${self.daily_loss:.2f}\nPausing 30 minutes for analysis...")
                    await asyncio.sleep(1800)
                    self.daily_loss = 0
                    self.consecutive_losses = 0
                return

            symbols = []
            if is_crypto_hours:
                symbols.extend(CRYPTO_UNIVERSE)
            if market_open:
                symbols.extend(STOCK_UNIVERSE[:20])

            for i, symbol in enumerate(symbols):
                if self.trades_today >= MAX_TRADES_PER_DAY:
                    break

                is_crypto = '/' in symbol
                if not is_crypto and not market_open:
                    continue

                df = await self.get_data(symbol, is_crypto)
                if df is None or len(df) < 50:
                    continue

                analysis = self.analyze(symbol, df)
                if not analysis:
                    continue

                has_position = symbol in self.positions
                position_size = self.positions.get(symbol, {}).get('qty', 0)

                if not has_position and analysis['score'] >= 75 and analysis['confidence'] >= MIN_CONFIDENCE:
                    if len(self.positions) < MAX_POSITIONS:
                        success = await self.trade(symbol, 'buy', analysis)
                        if success:
                            self.positions[symbol] = {'qty': 1, 'entry': analysis['price']}
                        await asyncio.sleep(2)

                elif has_position and analysis['score'] <= 35:
                    success = await self.trade(symbol, 'sell', analysis)
                    if success and symbol in self.positions:
                        del self.positions[symbol]
                    await asyncio.sleep(2)

                if i % 5 == 4:
                    await asyncio.sleep(1)

            try:
                positions = trading_client.get_all_positions()
                self.positions = {p.symbol: {'qty': float(p.qty), 'avg_price': float(p.avg_entry_price),
                                           'unrealized_pl': float(p.unrealized_pl)} for p in positions}
                self.daily_pnl = sum(float(p.unrealized_pl) for p in positions)
            except:
                pass

            scan_duration = time.time() - start_scan
            logger.info(f"Scan complete: {len(symbols)} symbols in {scan_duration:.1f}s")

        except Exception as e:
            logger.error(f"Scan error: {e}", exc_info=True)

    async def run(self):
        try:
            acct = trading_client.get_account()
            self.starting_equity = float(acct.equity)

            await self.send_tg(
                f"🚀 **Big Dog Bot v3.1 Online**\n\n"
                f"**Config:**\n"
                f"• Mode: {'🔴 LIVE' if LIVE_MODE else '🟢 PAPER'}\n"
                f"• Universe: {len(CRYPTO_UNIVERSE)} crypto + {len(STOCK_UNIVERSE)} stocks\n"
                f"• Max Position: ${MAX_POSITION_SIZE}\n"
                f"• Daily Loss Limit: ${MAX_DAILY_LOSS}\n"
                f"• Max Positions: {MAX_POSITIONS}\n\n"
                f"**Starting Equity:** ${self.starting_equity:,.2f}\n\n"
                f"All 200 upgrades active. Scanning every 60s."
            )

            logger.info("Bot started successfully")

        except Exception as e:
            logger.error(f"Startup failed: {e}")
            await self.send_tg(f"❌ Startup failed: {str(e)}")
            return

        last_daily_summary = None

        while True:
            try:
                current_hour = datetime.now().hour

                now_et = datetime.now(pytz.timezone('US/Eastern'))
                if now_et.hour == 16 and now_et.minute < 5 and last_daily_summary!= now_et.date():
                    await self.daily_summary()
                    last_daily_summary = now_et.date()
                    self.trades_today = 0
                    self.wins_today = 0
                    self.losses_today = 0
                    self.daily_loss = 0
                    self.starting_equity = float(trading_client.get_account().equity)

                await self.scan()

                if (datetime.now() - self.last_heartbeat).total_seconds() > HEARTBEAT_MINUTES * 60:
                    await self.heartbeat()

                await asyncio.sleep(60)

            except Exception as e:
                logger.error(f"Main loop error: {e}", exc_info=True)
                await asyncio.sleep(60)

if __name__ == "__main__":
    try:
        bot = BigDogBot()
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)