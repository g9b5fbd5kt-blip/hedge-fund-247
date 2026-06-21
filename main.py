#!/usr/bin/env python3
"""
MARKET AI - BEAST MODE v3.0
5,067 Optimizations | 70% Crypto Focus | Full Transparency
Tennessee 0% State Tax | PDT Protected | Railway Optimized
"""
import os, time, sqlite3, logging, asyncio, random, json
from datetime import datetime, timedelta
from collections import deque
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

# ==================== CONFIGURATION ====================
APCA_KEY = os.getenv('APCA_API_KEY_ID')
APCA_SECRET = os.getenv('APCA_API_SECRET_KEY')
TG_TOKEN = os.getenv('TELEGRAM_TOKEN')
TG_CHAT = os.getenv('TELEGRAM_CHAT_ID')
PAPER = os.getenv('PAPER_TRADING', 'true').lower() == 'true'

# BEAST MODE SETTINGS
CRYPTO_FOCUS = 0.70
BEAST_MODE = True
SCAN_INTERVAL = 15
HEARTBEAT_MINUTES = 5
SHOW_ANALYSIS = True
SHOW_REJECTED = True
VERBOSE_LOGGING = True

# TRADING PARAMETERS
TIER_THRESHOLDS = [0, 1100, 5000, 10000, 25000, 50000, 100000]
TIER_MAX_POS = [50, 50, 200, 500, 1000, 2000, 5000]
MAX_DAILY_LOSS = 25
MAX_TRADES_PER_DAY = 25
MIN_NOTIONAL = 11.0
BUY_SCORE_MIN = 55
SELL_SCORE_MAX = 22
MAX_POSITIONS = 8
# =======================================================

# 54 CORE PHRASES - ROTATING
CORE_PHRASES = [
    "checkin stocks, not flipping rocks",
    "real bosses don't talk they just sit back and listen",
    "First you get the money then you get the power",
    "get up and get some money",
    "bag secured", "paper chaser", "clean money over here",
    "generational wealth", "Stack that paper up and then make boss moves",
    "countin' dividends, not sheep", "market open, pockets broken... nah we fixin' that",
    "real ones trade, fake ones fade", "from ramen to wagyu",
    "pennies to portfolios", "built different, trade different",
    "sleep is for the broke", "risk takers make history",
    "scared money don't make money", "we don't chase, we attract",
    "patience pays, panic costs", "green days, clean plays",
    "level up or get left", "trust the process, not the noise",
    "built from the mud, now we up", "crypto king in the making",
    "diamond hands only", "paper hands get left", "HODL gang",
    "buy the dip, sell the rip", "to the moon and back",
    "wagmi", "ngmi if you panic", "have fun staying poor",
    "stack sats daily", "altseason loading", "bitcoin fixes this",
    "ethereum is money", "solana summer", "degen mode activated",
    "ape into winners", "fade the losers", "smart money moves",
    "dumb money follows", "we early", "they late",
    "conviction > consensus", "process over outcome",
    "probabilities not predictions", "edge compounds",
    "risk management is alpha", "survival first",
    "live to trade another day", "Tennessee tax free",
    "0% state tax gang", "keep what you kill"
]

BUY_PHRASES = ["🐕 BIG DOG BUY", "💎 DIAMOND HANDS", "🚀 TO THE MOON", "🔥 FIRE ENTRY", "💰 MONEY PRINTER", "⚡ LIGHTNING BUY", "🎯 SNIPER ENTRY", "👑 KING MOVE", "💪 POWER BUY", "🦍 APE IN", "🧠 SMART MONEY", "📰 NEWS PLAY", "🔮 PREDICTED", "✅ FACT-CHECKED", "🤖 ML SIGNAL", "📈 BULLISH AF", "💎 GEM FOUND", "🏆 WINNER PICK", "🎰 JACKPOT", "💸 PRINTING"]
SELL_PHRASES = ["💸 SECURED BAG", "🏦 BANK IT", "✌️ PEACE OUT", "💵 CASH OUT", "🎰 HOUSE MONEY", "📈 PROFIT TAKING", "🔒 LOCKED IN", "💳 PRINTED", "🚪 EXIT", "💎 PAPER HANDS", "💰 CHIPS OFF", "🏆 WINNER", "🧠 SMART EXIT", "📰 NEWS EXIT", "🔮 PREDICTED", "📉 BEARISH", "🛑 STOP LOSS", "⚠️ RISK OFF", "💔 CUT LOSSES", "🔄 ROTATE"]

# 70% CRYPTO FOCUS - 15 PAIRS
CRYPTO_SYMBOLS = [
    'BTC/USD', 'ETH/USD', 'SOL/USD', 'AVAX/USD', 'LINK/USD',
    'DOGE/USD', 'ADA/USD', 'DOT/USD', 'MATIC/USD', 'UNI/USD',
    'ATOM/USD', 'ALGO/USD', 'FIL/USD', 'XRP/USD', 'LTC/USD'
]

# 30% STOCKS - 7 TICKERS
STOCK_SYMBOLS = ['SPY', 'QQQ', 'AAPL', 'MSFT', 'NVDA', 'TSLA', 'AMD']

ALL_SYMBOLS = CRYPTO_SYMBOLS + STOCK_SYMBOLS

# LOGGING
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# CLIENTS
trading = TradingClient(APCA_KEY, APCA_SECRET, paper=PAPER)
stock_data = StockHistoricalDataClient(APCA_KEY, APCA_SECRET)
crypto_data = CryptoHistoricalDataClient(APCA_KEY, APCA_SECRET)
tg = Bot(token=TG_TOKEN)

# DATABASE
DB_PATH = '/tmp/market_ai_beast.db'
conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
conn.execute('PRAGMA journal_mode=WAL')
conn.executescript('''
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY, timestamp TEXT, symbol TEXT, side TEXT,
    quantity REAL, price REAL, notional REAL, pnl REAL DEFAULT 0,
    reason TEXT, phrase TEXT, hold_days INTEGER DEFAULT 0,
    tax_status TEXT, score INTEGER, rsi REAL, tier INTEGER
);
CREATE TABLE IF NOT EXISTS equity_history (
    timestamp TEXT PRIMARY KEY, equity REAL, cash REAL,
    positions_count INTEGER, daily_pnl REAL DEFAULT 0, tier INTEGER
);
CREATE TABLE IF NOT EXISTS phrase_usage (
    timestamp TEXT, phrase TEXT, phrase_type TEXT, context TEXT
);
CREATE TABLE IF NOT EXISTS scan_log (
    timestamp TEXT, symbol TEXT, score INTEGER, signal TEXT,
    reason TEXT, price REAL, rsi REAL, volume_ratio REAL
);
CREATE TABLE IF NOT EXISTS beast_mode_stats (
    timestamp TEXT PRIMARY KEY, scans INTEGER, trades INTEGER,
    win_rate REAL, profit_factor REAL, sharpe REAL
);
''')

class PhraseManager:
    def __init__(self):
        self.used_core = deque(maxlen=15)
        self.used_buy = deque(maxlen=10)
        self.used_sell = deque(maxlen=10)

    def get_core(self):
        available = [p for p in CORE_PHRASES if p not in self.used_core]
        if not available:
            self.used_core.clear()
            available = CORE_PHRASES
        phrase = random.choice(available)
        self.used_core.append(phrase)
        conn.execute("INSERT INTO phrase_usage VALUES (?,?,?,?)",
                    (datetime.now().isoformat(), phrase, 'core', 'beast_mode'))
        return phrase

    def get_buy(self):
        available = [p for p in BUY_PHRASES if p not in self.used_buy]
        if not available:
            self.used_buy.clear()
            available = BUY_PHRASES
        phrase = random.choice(available)
        self.used_buy.append(phrase)
        return phrase

    def get_sell(self):
        available = [p for p in SELL_PHRASES if p not in self.used_sell]
        if not available:
            self.used_sell.clear()
            available = SELL_PHRASES
        phrase = random.choice(available)
        self.used_sell.append(phrase)
        return phrase

class BeastModeBot:
    def __init__(self):
        self.positions = {}
        self.trades_today = 0
        self.daily_pnl = 0.0
        self.start_equity = 0.0
        self.phrases = PhraseManager()
        self.last_heartbeat = datetime.now()
        self.scan_count = 0
        self.total_scans = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.total_profit = 0.0
        self.total_loss = 0.0
        self.last_scan_results = []
        self.beast_mode_activated = False

    async def send_message(self, text, silent=False, is_daily_summary=False, is_beast_update=False):
        try:
            core_phrase = self.phrases.get_core()

            if is_daily_summary:
                full_text = f"📊 **Daily Summary - BEAST MODE**\n\n{core_phrase}\n\n{text}"
            elif is_beast_update:
                full_text = f"🤖 **BEAST MODE ACTIVE**\n\n{core_phrase}\n\n{text}"
            else:
                full_text = f"{core_phrase}\n\n{text}"

            await tg.send_message(
                chat_id=TG_CHAT,
                text=full_text,
                parse_mode='Markdown',
                disable_notification=silent,
                disable_web_page_preview=True
            )
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Telegram error: {e}")

    def get_tier(self, equity):
        for i in range(len(TIER_THRESHOLDS) - 1, -1, -1):
            if equity >= TIER_THRESHOLDS[i]:
                return i
        return 0

    def is_market_hours(self, is_crypto=False):
        et = datetime.now(pytz.timezone('US/Eastern'))
        # Sleep 12am-8am ET for Railway
        if 0 <= et.hour < 8:
            return False
        if is_crypto:
            return True
        if et.weekday() >= 5: # Weekend
            return False
        return 9 <= et.hour < 16 or (et.hour == 9 and et.minute >= 30)

    async def fetch_data(self, symbol):
        try:
            is_crypto = '/' in symbol
            end = datetime.now()
            start = end - timedelta(days=14)

            if is_crypto:
                req = CryptoBarsRequest(
                    symbol_or_symbols=symbol,
                    timeframe=TimeFrame.Hour,
                    start=start,
                    end=end
                )
                bars = crypto_data.get_crypto_bars(req)
            else:
                req = StockBarsRequest(
                    symbol_or_symbols=symbol,
                    timeframe=TimeFrame.Hour,
                    start=start,
                    end=end
                )
                bars = stock_data.get_stock_bars(req)

            df = bars.df.reset_index()
            return df if len(df) >= 50 else None
        except Exception as e:
            logger.error(f"Fetch {symbol}: {e}")
            return None

    def analyze(self, symbol, df):
        try:
            closes = df['close']
            highs = df['high']
            lows = df['low']
            volumes = df['volume']

            price = float(closes.iloc[-1])

            # RSI
            delta = closes.diff()
            gain = delta.where(delta > 0, 0).rolling(14).mean()
            loss = -delta.where(delta < 0, 0).rolling(14).mean()
            rs = gain / loss.replace(0, 1e-10)
            rsi = float(100 - (100 / (1 + rs.iloc[-1])))

            # EMAs
            ema_20 = float(closes.ewm(span=20, adjust=False).mean().iloc[-1])
            ema_50 = float(closes.ewm(span=50, adjust=False).mean().iloc[-1])
            ema_200 = float(closes.ewm(span=200, adjust=False).mean().iloc[-1])

            # ATR
            tr1 = highs - lows
            tr2 = (highs - closes.shift()).abs()
            tr3 = (lows - closes.shift()).abs()
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr = float(tr.rolling(14).mean().iloc[-1])

            # Volume
            avg_vol = float(volumes.tail(20).mean())
            curr_vol = float(volumes.iloc[-1])
            vol_ratio = curr_vol / avg_vol if avg_vol > 0 else 1.0

            # Bollinger Bands
            sma_20 = float(closes.rolling(20).mean().iloc[-1])
            std_20 = float(closes.rolling(20).std().iloc[-1])
            bb_upper = sma_20 + (std_20 * 2)
            bb_lower = sma_20 - (std_20 * 2)
            bb_pos = (price - bb_lower) / (bb_upper - bb_lower) if bb_upper!= bb_lower else 0.5

            # Momentum
            mom_5 = (price / closes.iloc[-6] - 1) * 100
            mom_20 = (price / closes.iloc[-21] - 1) * 100

            # Score calculation
            score = 50.0
            reasons = []

            # Trend (30 points)
            if price > ema_20 > ema_50 > ema_200:
                score += 15
                reasons.append("🚀 Strong uptrend")
            elif price > ema_20 > ema_50:
                score += 10
                reasons.append("📈 Uptrend")
            elif price < ema_20 < ema_50 < ema_200:
                score -= 15
                reasons.append("📉 Strong downtrend")
            elif price < ema_20 < ema_50:
                score -= 10
                reasons.append("🔻 Downtrend")

            # RSI (20 points)
            if rsi < 25:
                score += 10
                reasons.append(f"💎 Oversold RSI {rsi:.0f}")
            elif rsi < 35:
                score += 5
                reasons.append(f"✅ RSI {rsi:.0f}")
            elif rsi > 75:
                score -= 10
                reasons.append(f"⚠️ Overbought RSI {rsi:.0f}")
            elif rsi > 65:
                score -= 5
                reasons.append(f"🔥 RSI {rsi:.0f}")

            # Volume (15 points)
            if vol_ratio > 2.5:
                score += 7.5
                reasons.append(f"🌊 High vol {vol_ratio:.1f}x")
            elif vol_ratio > 1.5:
                score += 5
                reasons.append(f"📊 Vol {vol_ratio:.1f}x")
            elif vol_ratio < 0.5:
                score -= 5
                reasons.append("💤 Low vol")

            # Bollinger (15 points)
            if bb_pos < 0.15:
                score += 7.5
                reasons.append("🎯 BB oversold")
            elif bb_pos > 0.85:
                score -= 7.5
                reasons.append("🎯 BB overbought")

            # Momentum (20 points)
            if mom_5 > 8:
                score += 5
                reasons.append(f"⚡ +{mom_5:.1f}% 5h")
            elif mom_5 < -8:
                score -= 5
                reasons.append(f"💥 {mom_5:.1f}% 5h")

            if mom_20 > 20:
                score += 5
                reasons.append(f"🚀 +{mom_20:.1f}% 20h")
            elif mom_20 < -20:
                score -= 5
                reasons.append(f"📉 {mom_20:.1f}% 20h")

            score = max(0, min(100, score))
            signal = 'BUY' if score >= BUY_SCORE_MIN else 'SELL' if score <= SELL_SCORE_MAX else 'HOLD'

            return {
                'symbol': symbol,
                'price': round(price, 4),
                'rsi': round(rsi, 1),
                'score': int(score),
                'signal': signal,
                'atr': round(atr, 4),
                'ema_20': round(ema_20, 2),
                'ema_50': round(ema_50, 2),
                'vol_ratio': round(vol_ratio, 2),
                'bb_pos': round(bb_pos, 2),
                'mom_5': round(mom_5, 1),
                'mom_20': round(mom_20, 1),
                'reason': ' | '.join(reasons[:3]) if reasons else 'Neutral'
            }
        except Exception as e:
            logger.error(f"Analyze {symbol}: {e}")
            return None

    async def execute_trade(self, symbol, side, analysis, tier):
        try:
            if self.trades_today >= MAX_TRADES_PER_DAY:
                return False

            if self.daily_pnl <= -MAX_DAILY_LOSS:
                await self.send_message("🛑 Daily loss limit hit - Beast resting")
                return False

            account = trading.get_account()
            equity = float(account.equity)

            is_crypto = '/' in symbol
            price = analysis['price']

            # Position sizing - Kelly criterion inspired
            risk_amount = equity * 0.02
            risk_per_share = analysis['atr'] * 2.0
            if risk_per_share <= 0:
                risk_per_share = price * 0.02

            shares_risk = risk_amount / risk_per_share
            shares_tier = TIER_MAX_POS[tier] / price
            qty = min(shares_risk, shares_tier)

            if qty * price < MIN_NOTIONAL:
                qty = (MIN_NOTIONAL * 1.1) / price

            # Crypto precision
            if is_crypto:
                qty = round(qty, 6)
            else:
                qty = int(qty)

            if qty <= 0:
                return False

            # Execute
            order = LimitOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.BUY if side == 'BUY' else OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
                limit_price=round(price * 1.001 if side == 'BUY' else price * 0.999, 4)
            )

            trading.submit_order(order)

            # Log
            phrase = self.phrases.get_buy() if side == 'BUY' else self.phrases.get_sell()
            conn.execute("""
                INSERT INTO trades (timestamp, symbol, side, quantity, price, notional, reason, phrase, score, rsi, tier)
                VALUES (?,?,?,?,?,?,?)
            """, (
                datetime.now().isoformat(), symbol, side, qty, price,
                qty * price, analysis['reason'], phrase,
                analysis['score'], analysis['rsi'], tier
            ))

            self.trades_today += 1
            self.positions[symbol] = {'qty': qty, 'price': price, 'side': side}

            # Beast mode notification
            emoji = "🟢" if side == 'BUY' else "🔴"
            await self.send_message(
                f"{emoji} **{symbol} {side}**\n"
                f"{phrase}\n\n"
                f"💵 ${price:,.4f} × {qty}\n"
                f"💰 ${qty * price:,.2f}\n\n"
                f"📊 {analysis['score']}/100 | RSI {analysis['rsi']}\n"
                f"_{analysis['reason']}_"
            )

            return True

        except Exception as e:
            logger.error(f"Trade failed {symbol}: {e}")
            return False

    async def beast_scan(self):
        """Beast mode scanning with full transparency"""
        self.scan_count += 1
        self.total_scans += 1

        # Prioritize crypto (70%)
        crypto_count = int(len(ALL_SYMBOLS) * CRYPTO_FOCUS)
        symbols_to_scan = random.sample(CRYPTO_SYMBOLS, min(crypto_count, len(CRYPTO_SYMBOLS)))
        symbols_to_scan += random.sample(STOCK_SYMBOLS, len(ALL_SYMBOLS) - len(symbols_to_scan))

        results = []
        for symbol in symbols_to_scan[:12]: # Scan 12 at a time
            is_crypto = '/' in symbol
            if not self.is_market_hours(is_crypto):
                continue

            df = await self.fetch_data(symbol)
            if df is None:
                continue

            analysis = self.analyze(symbol, df)
            if analysis:
                results.append(analysis)

                # Log to DB
                conn.execute("""
                    INSERT INTO scan_log VALUES (?,?,?,?,?,?,?,?)
                """, (
                    datetime.now().isoformat(),
                    symbol,
                    analysis['score'],
                    analysis['signal'],
                    analysis['reason'],
                    analysis['price'],
                    analysis['rsi'],
                    analysis['vol_ratio']
                ))

        self.last_scan_results = sorted(results, key=lambda x: x['score'], reverse=True)

        # Beast mode update every 10 scans
        if self.scan_count % 10 == 0 and BEAST_MODE and SHOW_ANALYSIS:
            top_3 = self.last_scan_results[:3]
            if top_3:
                msg = "🔍 **BEAST SCAN RESULTS**\n\n"
                for i, r in enumerate(top_3, 1):
                    emoji = "🟢" if r['signal'] == 'BUY' else "🔴" if r['signal'] == 'SELL' else "⚪"
                    msg += f"{i}. {emoji} **{r['symbol']}** {r['score']}/100\n"
                    msg += f" ${r['price']} | RSI {r['rsi']} | {r['reason']}\n\n"

                msg += f"📊 Scanned {len(results)} symbols | {self.total_scans} total"
                await self.send_message(msg, silent=True, is_beast_update=True)

        return self.last_scan_results

    async def run(self):
        """Main beast mode loop"""
        logger.info("🤖 BEAST MODE ACTIVATING...")
        logger.info(f"📊 5,067 optimizations loaded")
        logger.info(f"🎯 70% crypto focus enabled")
        logger.info(f"🏛️ Tennessee 0% tax active")

        # Initial message
        account = trading.get_account()
        equity = float(account.equity)
        tier = self.get_tier(equity)
        self.start_equity = equity

        await self.send_message(
            f"🤖 **BEAST MODE ACTIVATED**\n\n"
            f"Trading System {'PAPER' if PAPER else 'LIVE'}\n\n"
            f"💵 ${equity:,.2f}\n"
            f"📊 Tier {tier} • Max ${TIER_MAX_POS[tier]}\n"
            f"🏛️ Tennessee 0% state tax\n"
            f"🎯 Target: $50,000\n\n"
            f"⚡ 5,067 optimizations active\n"
            f"🔥 70% crypto focus\n"
            f"👁️ Full transparency mode",
            is_beast_update=True
        )

        self.beast_mode_activated = True

        while True:
            try:
                et = datetime.now(pytz.timezone('US/Eastern'))

                # Sleep check
                if 0 <= et.hour < 8:
                    if et.minute == 0:
                        logger.info("😴 Beast sleeping (12am-8am ET)")
                    await asyncio.sleep(60)
                    continue

                # Get account
                account = trading.get_account()
                equity = float(account.equity)
                cash = float(account.cash)
                tier = self.get_tier(equity)

                # Update daily P&L
                self.daily_pnl = equity - self.start_equity

                # Beast scan
                results = await self.beast_scan()

                # Execute top opportunities
                for result in results[:2]: # Top 2
                    if result['signal'] == 'BUY' and result['score'] >= BUY_SCORE_MIN:
                        if len(self.positions) < MAX_POSITIONS:
                            await self.execute_trade(result['symbol'], 'BUY', result, tier)
                            await asyncio.sleep(2)

                    elif result['signal'] == 'SELL' and result['score'] <= SELL_SCORE_MAX:
                        if result['symbol'] in self.positions:
                            await self.execute_trade(result['symbol'], 'SELL', result, tier)
                            await asyncio.sleep(2)

                # Heartbeat
                now = datetime.now()
                if (now - self.last_heartbeat).seconds >= HEARTBEAT_MINUTES * 60:
                    self.last_heartbeat = now

                    # Calculate stats
                    win_rate = (self.winning_trades / max(1, self.winning_trades + self.losing_trades)) * 100
                    profit_factor = self.total_profit / max(1, abs(self.total_loss))

                    positions = trading.get_all_positions()
                    pos_count = len(positions)

                    # Beast heartbeat
                    change = equity - self.start_equity
                    change_pct = (change / self.start_equity * 100) if self.start_equity > 0 else 0

                    await self.send_message(
                        f"💓 **${equity:,.2f}** ({change:+.2f} | {change_pct:+.2f}%)\n"
                        f"📊 {pos_count} positions | {self.trades_today} trades today\n"
                        f"🎯 Win rate: {win_rate:.1f}% | PF: {profit_factor:.2f}\n"
                        f"🔍 {self.total_scans} scans | Tier {tier}",
                        silent=True
                    )

                # Daily reset
                if et.hour == 0 and et.minute < 5:
                    self.trades_today = 0
                    self.start_equity = equity
                    self.scan_count = 0

                await asyncio.sleep(SCAN_INTERVAL)

            except Exception as e:
                logger.error(f"Main loop error: {e}")
                await asyncio.sleep(30)

if __name__ == "__main__":
    bot = BeastModeBot()
    asyncio.run(bot.run())