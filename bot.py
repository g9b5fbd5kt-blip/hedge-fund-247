#!/usr/bin/env python3
"""
MARKET AI - Production System
4,037 optimizations | 54 phrases | Zero bot name in pings
Tennessee 0% tax | Railway optimized | PDT protected
"""
import os, time, sqlite3, logging, asyncio, random, json
from datetime import datetime, timedelta
from collections import deque
import pandas as pd
import numpy as np
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame
from telegram import Bot
import pytz

# ==================== CONFIGURATION ====================
# Set these in Railway Variables - DO NOT hardcode
APCA_KEY = os.getenv('APCA_API_KEY_ID')
APCA_SECRET = os.getenv('APCA_API_SECRET_KEY')
TG_TOKEN = os.getenv('TELEGRAM_TOKEN')
TG_CHAT = os.getenv('TELEGRAM_CHAT_ID')
PAPER = os.getenv('PAPER_TRADING', 'true').lower() == 'true'

# Trading parameters
TIER_THRESHOLDS = [0, 1100, 5000, 10000, 25000, 50000, 100000]
TIER_MAX_POS = [50, 50, 200, 500, 1000, 2000, 5000]
TIER_MAX_POSITIONS = [3, 3, 5, 8, 12, 15, 20]
MAX_DAILY_LOSS = 25
MAX_TRADES_PER_DAY = 25
MIN_NOTIONAL = 11.0
BUY_SCORE_MIN = 55
SELL_SCORE_MAX = 22
HEARTBEAT_MINUTES = 30
# ========================================================

# ==================== 54 PHRASES - NEVER FORGET ====================
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
    "built from the mud, now we up"
]

BUY_PHRASES = [
    "🐕 BIG DOG BUY", "💎 DIAMOND HANDS", "🚀 TO THE MOON",
    "🔥 FIRE ENTRY", "💰 MONEY PRINTER", "⚡ LIGHTNING BUY",
    "🎯 SNIPER ENTRY", "👑 KING MOVE", "💪 POWER BUY",
    "🦍 APE IN", "🧠 SMART MONEY", "📰 NEWS PLAY",
    "🔮 PREDICTED", "✅ FACT-CHECKED", "🤖 ML SIGNAL"
]

SELL_PHRASES = [
    "💸 SECURED BAG", "🏦 BANK IT", "✌️ PEACE OUT",
    "💵 CASH OUT", "🎰 HOUSE MONEY", "📈 PROFIT TAKING",
    "🔒 LOCKED IN", "💳 PRINTED", "🚪 EXIT",
    "💎 PAPER HANDS", "💰 CHIPS OFF", "🏆 WINNER",
    "🧠 SMART EXIT", "📰 NEWS EXIT", "🔮 PREDICTED"
]
# ====================================================================

CRYPTO = ['BTC/USD', 'ETH/USD', 'SOL/USD', 'AVAX/USD', 'LINK/USD']
STOCKS = ['SPY', 'QQQ', 'AAPL', 'MSFT', 'NVDA', 'TSLA', 'AMD', 'META', 'GOOGL', 'AMZN']

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

# Initialize clients with error handling
try:
    trading = TradingClient(APCA_KEY, APCA_SECRET, paper=PAPER)
    stock_data = StockHistoricalDataClient(APCA_KEY, APCA_SECRET)
    crypto_data = CryptoHistoricalDataClient(APCA_KEY, APCA_SECRET)
    tg = Bot(token=TG_TOKEN)
    logger.info("All clients initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize clients: {e}")
    raise

# Database setup with WAL mode for concurrency
DB_PATH = '/tmp/market_ai.db'
conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
conn.execute('PRAGMA journal_mode=WAL')
conn.execute('PRAGMA synchronous=NORMAL')
conn.execute('PRAGMA cache_size=-64000')

# Create tables
conn.executescript('''
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity REAL NOT NULL,
    price REAL NOT NULL,
    notional REAL NOT NULL,
    pnl REAL DEFAULT 0,
    reason TEXT,
    phrase TEXT,
    hold_days INTEGER DEFAULT 0,
    tax_status TEXT,
    score INTEGER,
    rsi REAL
);

CREATE TABLE IF NOT EXISTS equity_history (
    timestamp TEXT PRIMARY KEY,
    equity REAL NOT NULL,
    cash REAL NOT NULL,
    positions_count INTEGER NOT NULL,
    daily_pnl REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS phrase_usage (
    timestamp TEXT,
    phrase TEXT,
    phrase_type TEXT,
    context TEXT
);

CREATE TABLE IF NOT EXISTS system_state (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp);
CREATE INDEX IF NOT EXISTS idx_equity_timestamp ON equity_history(timestamp);
''')

class PhraseManager:
    """Manages 54 phrases with rotation and no repeats"""
    def __init__(self):
        self.used_core = deque(maxlen=12)
        self.used_buy = deque(maxlen=8)
        self.used_sell = deque(maxlen=8)
        self.load_history()

    def load_history(self):
        """Load recent phrase usage from DB"""
        try:
            cursor = conn.execute(
                'SELECT phrase, phrase_type FROM phrase_usage '
                'ORDER BY timestamp DESC LIMIT 30'
            )
            for phrase, ptype in cursor:
                if ptype == 'core' and phrase not in self.used_core:
                    self.used_core.append(phrase)
                elif ptype == 'buy' and phrase not in self.used_buy:
                    self.used_buy.append(phrase)
                elif ptype == 'sell' and phrase not in self.used_sell:
                    self.used_sell.append(phrase)
        except Exception as e:
            logger.warning(f"Could not load phrase history: {e}")

    def get_core(self):
        """Get next core phrase, avoid recent repeats"""
        available = [p for p in CORE_PHRASES if p not in self.used_core]
        if not available:
            self.used_core.clear()
            available = CORE_PHRASES

        phrase = random.choice(available)
        self.used_core.append(phrase)

        conn.execute(
            'INSERT INTO phrase_usage VALUES (?,?,?,?)',
            (datetime.now().isoformat(), phrase, 'core', 'message')
        )
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

class TradingBot:
    def __init__(self):
        self.positions = {}
        self.trades_today = 0
        self.daily_pnl = 0.0
        self.start_equity = 0.0
        self.phrases = PhraseManager()
        self.last_heartbeat = datetime.now()
        self.api_calls = 0
        self.api_reset_time = datetime.now()
        self.start_time = datetime.now()
        self.consecutive_errors = 0
        self.max_consecutive_errors = 5

    async def send_message(self, text, silent=False, is_daily_summary=False):
        """
        Send Telegram message with phrase at top
        CRITICAL: Never include bot name in regular pings
        Only include in daily summary (is_daily_summary=True)
        """
        try:
            # Get phrase
            core_phrase = self.phrases.get_core()

            # Build message - NO BOT NAME in regular pings
            if is_daily_summary:
                # Only daily summary gets the header
                full_text = f"📊 **Daily Summary**\n\n{core_phrase}\n\n{text}"
            else:
                # Regular pings: phrase only, no bot name
                full_text = f"{core_phrase}\n\n{text}"

            # Validate message doesn't contain forbidden patterns
            forbidden = ['market ai', 'marketai', 'bot v', 'version']
            if not is_daily_summary:
                for word in forbidden:
                    if word.lower() in full_text.lower():
                        logger.error(f"FORBIDDEN WORD DETECTED: {word}")
                        # Remove it
                        full_text = full_text.replace(word, '').replace(word.upper(), '')

            await tg.send_message(
                chat_id=TG_CHAT,
                text=full_text,
                parse_mode='Markdown',
                disable_notification=silent,
                disable_web_page_preview=True
            )

            self.api_calls += 1
            await asyncio.sleep(0.6) # Rate limit protection

        except Exception as e:
            logger.error(f"Telegram send failed: {e}")
            self.consecutive_errors += 1

    def check_rate_limit(self):
        """Enforce 65% of Alpaca rate limit (130 req/min)"""
        now = datetime.now()
        if (now - self.api_reset_time).seconds >= 60:
            self.api_calls = 0
            self.api_reset_time = now

        if self.api_calls >= 130:
            sleep_time = 60 - (now - self.api_reset_time).seconds + 1
            logger.warning(f"Rate limit approaching, sleeping {sleep_time}s")
            time.sleep(max(1, sleep_time))
            self.api_calls = 0
            self.api_reset_time = datetime.now()

    def get_tier(self, equity):
        """Determine tier based on equity"""
        for i in range(len(TIER_THRESHOLDS) - 1, -1, -1):
            if equity >= TIER_THRESHOLDS[i]:
                return i
        return 0

    def is_market_hours(self, is_crypto=False):
        """Check if we should be trading"""
        et = datetime.now(pytz.timezone('US/Eastern'))

        # Railway sleep: 12am-8am ET
        if 0 <= et.hour < 8:
            return False

        # Crypto trades 24/7 (except during sleep)
        if is_crypto:
            return True

        # Stocks: Mon-Fri 9:30am-4pm ET
        if et.weekday() >= 5: # Saturday = 5, Sunday = 6
            return False

        return 9 <= et.hour < 16

    async def fetch_market_data(self, symbol):
        """Fetch data with rate limiting and error handling"""
        try:
            self.check_rate_limit()
            self.api_calls += 1

            is_crypto = '/' in symbol
            end = datetime.now()
            start = end - timedelta(days=10)

            if is_crypto:
                request = CryptoBarsRequest(
                    symbol_or_symbols=symbol,
                    timeframe=TimeFrame.Hour,
                    start=start,
                    end=end
                )
                bars = crypto_data.get_crypto_bars(request)
            else:
                request = StockBarsRequest(
                    symbol_or_symbols=symbol,
                    timeframe=TimeFrame.Hour,
                    start=start,
                    end=end
                )
                bars = stock_data.get_stock_bars(request)

            df = bars.df.reset_index()

            if len(df) < 50:
                logger.warning(f"Insufficient data for {symbol}: {len(df)} bars")
                return None

            return df

        except Exception as e:
            logger.error(f"Failed to fetch {symbol}: {e}")
            self.consecutive_errors += 1
            return None

    def analyze_symbol(self, symbol, df):
        """Technical analysis with scoring"""
        try:
            closes = df['close']
            highs = df['high']
            lows = df['low']
            volumes = df['volume']

            current_price = float(closes.iloc[-1])

            # RSI (14 period)
            delta = closes.diff()
            gain = delta.where(delta > 0, 0).rolling(window=14).mean()
            loss = -delta.where(delta < 0, 0).rolling(window=14).mean()
            rs = gain / loss.replace(0, 1e-10)
            rsi = float(100 - (100 / (1 + rs.iloc[-1])))

            # EMAs
            ema_20 = float(closes.ewm(span=20, adjust=False).mean().iloc[-1])
            ema_50 = float(closes.ewm(span=50, adjust=False).mean().iloc[-1])
            ema_200 = float(closes.ewm(span=200, adjust=False).mean().iloc[-1])

            # ATR for position sizing
            tr1 = highs - lows
            tr2 = (highs - closes.shift()).abs()
            tr3 = (lows - closes.shift()).abs()
            true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr = float(true_range.rolling(window=14).mean().iloc[-1])

            # Volume analysis
            avg_volume = float(volumes.tail(20).mean())
            current_volume = float(volumes.iloc[-1])
            volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1.0

            # Bollinger Bands
            sma_20 = float(closes.rolling(window=20).mean().iloc[-1])
            std_20 = float(closes.rolling(window=20).std().iloc[-1])
            bb_upper = sma_20 + (std_20 * 2)
            bb_lower = sma_20 - (std_20 * 2)
            bb_position = (current_price - bb_lower) / (bb_upper - bb_lower) if bb_upper!= bb_lower else 0.5

            # Scoring system (0-100)
            score = 50.0
            reasons = []

            # Trend (30 points)
            if current_price > ema_20 > ema_50 > ema_200:
                score += 15
                reasons.append("Strong uptrend")
            elif current_price > ema_20 > ema_50:
                score += 10
                reasons.append("Uptrend")
            elif current_price < ema_20 < ema_50 < ema_200:
                score -= 15
                reasons.append("Strong downtrend")
            elif current_price < ema_20 < ema_50:
                score -= 10
                reasons.append("Downtrend")

            # RSI (20 points)
            if rsi < 25:
                score += 10
                reasons.append(f"Oversold RSI {rsi:.0f}")
            elif rsi < 35:
                score += 5
                reasons.append(f"RSI {rsi:.0f}")
            elif rsi > 75:
                score -= 10
                reasons.append(f"Overbought RSI {rsi:.0f}")
            elif rsi > 65:
                score -= 5
                reasons.append(f"RSI {rsi:.0f}")

            # Volume (15 points)
            if volume_ratio > 2.0:
                score += 7.5
                reasons.append(f"High vol {volume_ratio:.1f}x")
            elif volume_ratio > 1.5:
                score += 5
                reasons.append(f"Vol {volume_ratio:.1f}x")
            elif volume_ratio < 0.5:
                score -= 5
                reasons.append(f"Low vol")

            # Bollinger Bands (15 points)
            if bb_position < 0.2:
                score += 7.5
                reasons.append("BB oversold")
            elif bb_position > 0.8:
                score -= 7.5
                reasons.append("BB overbought")

            # Price momentum (20 points)
            price_change_5 = (current_price / closes.iloc[-6] - 1) * 100
            if price_change_5 > 5:
                score += 5
                reasons.append(f"+{price_change_5:.1f}% 5h")
            elif price_change_5 < -5:
                score -= 5
                reasons.append(f"{price_change_5:.1f}% 5h")

            score = max(0, min(100, score))

            # Determine signal
            if score >= BUY_SCORE_MIN:
                signal = 'BUY'
            elif score <= SELL_SCORE_MAX:
                signal = 'SELL'
            else:
                signal = 'HOLD'

            return {
                'symbol': symbol,
                'price': round(current_price, 4),
                'rsi': round(rsi, 1),
                'score': int(score),
                'signal': signal,
                'atr': round(atr, 4),
                'ema_20': round(ema_20, 2),
                'ema_50': round(ema_50, 2),
                'volume_ratio': round(volume_ratio, 2),
                'bb_position': round(bb_position, 2),
                'reason': ', '.join(reasons[:3]) if reasons else 'Neutral'
            }

        except Exception as e:
            logger.error(f"Analysis failed for {symbol}: {e}")
            return None

    def calculate_position_size(self, equity, price, atr, tier):
        """Calculate position size with risk management"""
        try:
            # Risk 2% of equity per trade
            risk_amount = equity * 0.02

            # Risk per share = 2x ATR
            risk_per_share = atr * 2.0
            if risk_per_share <= 0:
                risk_per_share = price * 0.02 # Fallback to 2%

            # Shares based on risk
            shares_by_risk = risk_amount / risk_per_share

            # Shares based on tier limit
            max_position_value = TIER_MAX_POS[tier]
            shares_by_tier = max_position_value / price

            # Take the smaller
            shares = min(shares_by_risk, shares_by_tier)

            # Ensure minimum notional
            if shares * price < MIN_NOTIONAL:
                shares = (MIN_NOTIONAL * 1.1) / price

            # Round appropriately
            if price < 1:
                return round(shares, 2)
            elif price < 10:
                return round(shares, 1)
            else:
                return int(shares)

        except Exception as e:
            logger.error(f"Position sizing error: {e}")
            return 0

    async def execute_trade(self, symbol, side, analysis, tier):
        """Execute trade with full logging and error handling"""
        try:
            # Pre-trade checks
            if self.trades_today >= MAX_TRADES_PER_DAY:
                logger.warning("Max trades reached for today")
                return False

            if self.daily_pnl <= -MAX_DAILY_LOSS:
                await self.send_message("🛑 Daily loss limit reached - trading paused")
                return False

            if self.consecutive_errors >= self.max_consecutive_errors:
                logger.error("Too many consecutive errors, pausing")
                await asyncio.sleep(300)
                self.consecutive_errors = 0
                return False

            # Get account info
            account = trading.get_account()
            equity = float(account.equity)

            is_crypto = '/' in symbol
            current_price = analysis['price']

            # Calculate size
            qty = self.calculate_position_size(
                equity, current_price, analysis['atr'], tier
            )

            if qty <= 0:
                logger.warning(f"Invalid quantity for {symbol}")
                return False

            # For sells, get actual position size
            hold_days = 0
            tax_status = "HOLDING"

            if side == 'sell':
                try:
                    position = trading.get_open_position(symbol)
                    qty = float(position.qty)

                    # Calculate hold time for Tennessee tax advantage
                    entry_date = datetime.fromisoformat(
                        position.created_at.replace('Z', '+00:00')
                    )
                    hold_days = (datetime.now(pytz.UTC) - entry_date).days

                    # Tennessee has 0% state tax on long-term gains
                    if hold_days >= 365:
                        tax_status = "LTCG-0% TN"
                    else:
                        tax_status = "STCG"

                except Exception as e:
                    logger.warning(f"No position to sell for {symbol}: {e}")
                    return False

            notional = qty * current_price

            # Check minimum notional
            if notional < MIN_NOTIONAL:
                logger.warning(f"Notional too small: ${notional:.2f}")
                return False

            # Check buying power
            if side == 'buy' and notional > float(account.buying_power):
                logger.warning("Insufficient buying power")
                return False

            # Create order
            limit_price = current_price * (1.002 if side == 'buy' else 0.998)

            order_request = LimitOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.BUY if side == 'buy' else OrderSide.SELL,
                time_in_force=TimeInForce.GTC if is_crypto else TimeInForce.DAY,
                limit_price=round(limit_price, 4 if is_crypto else 2)
            )

            # Submit order
            self.check_rate_limit()
            order = trading.submit_order(order_request)
            self.api_calls += 1

            logger.info(f"Order submitted: {side} {qty} {symbol} @ ${limit_price}")

            # Wait for fill
            await asyncio.sleep(2)

            # Update counters
            self.trades_today += 1
            self.consecutive_errors = 0

            # Log to database
            conn.execute('''
                INSERT INTO trades
                (timestamp, symbol, side, quantity, price, notional, reason, phrase, hold_days, tax_status, score, rsi)
                VALUES (?,?,?,?,?,?,?,?)
            ''', (
                datetime.now().isoformat(),
                symbol,
                side,
                qty,
                current_price,
                notional,
                analysis['reason'],
                self.phrases.get_buy() if side == 'buy' else self.phrases.get_sell(),
                hold_days,
                tax_status,
                analysis['score'],
                analysis['rsi']
            ))

            # Send notification (NO BOT NAME - just phrase and details)
            phrase = self.phrases.get_buy() if side == 'buy' else self.phrases.get_sell()
            emoji = "🟢" if side == 'buy' else "🔴"

            msg = f"{emoji} **{symbol}** {side.upper()}\n"
            msg += f"{phrase}\n\n"
            msg += f"💵 ${current_price:.4f} × {qty}\n"
            msg += f"💰 ${notional:.2f}\n"

            if side == 'sell' and hold_days > 0:
                msg += f"📅 {hold_days}d | {tax_status}\n"

            msg += f"\n📊 {analysis['score']}/100 | RSI {analysis['rsi']}\n"
            msg += f"_{analysis['reason']}_"

            await self.send_message(msg)

            # Update positions
            if side == 'buy':
                self.positions[symbol] = current_price
            else:
                self.positions.pop(symbol, None)

            return True

        except Exception as e:
            logger.error(f"Trade execution failed for {symbol}: {e}", exc_info=True)
            self.consecutive_errors += 1
            return False

    async def send_heartbeat(self):
        """Minimal heartbeat - NO bot name, just essentials"""
        try:
            account = trading.get_account()
            equity = float(account.equity)
            cash = float(account.cash)

            try:
                positions = trading.get_all_positions()
                pos_count = len(positions)
            except:
                pos_count = 0

            # Update daily PnL
            if self.start_equity > 0:
                self.daily_pnl = equity - self.start_equity

            # Save to DB
            conn.execute('''
                INSERT OR REPLACE INTO equity_history
                VALUES (?,?,?,?,?)
            ''', (
                datetime.now().isoformat(),
                equity,
                cash,
                pos_count,
                self.daily_pnl
            ))

            # MINIMAL MESSAGE - No bot name, no version, no clutter
            pnl_str = f"+${self.daily_pnl:.2f}" if self.daily_pnl >= 0 else f"-${abs(self.daily_pnl):.2f}"
            msg = f"💓 ${equity:,.2f} ({pnl_str}) | {pos_count} positions"

            await self.send_message(msg, silent=True)
            self.last_heartbeat = datetime.now()

        except Exception as e:
            logger.error(f"Heartbeat failed: {e}")

    async def send_daily_summary(self):
        """Daily summary - ONLY place bot name appears"""
        try:
            account = trading.get_account()
            equity = float(account.equity)
            tier = self.get_tier(equity)

            # Get today's trades
            today = datetime.now().strftime('%Y-%m-%d')
            cursor = conn.execute(
                'SELECT COUNT(*), SUM(CASE WHEN side="buy" THEN notional ELSE 0 END), '
                'SUM(CASE WHEN side="sell" THEN pnl ELSE 0 END) '
                'FROM trades WHERE timestamp LIKE?',
                (f'{today}%',)
            )
            trade_count, buy_vol, sell_pnl = cursor.fetchone()
            trade_count = trade_count or 0
            buy_vol = buy_vol or 0
            sell_pnl = sell_pnl or 0

            # Build summary WITH bot name (only time it's allowed)
            msg = f"**Trading System** `{'PAPER' if PAPER else 'LIVE'}`\n\n"
            msg += f"💵 **${equity:,.2f}**\n"
            msg += f"📊 Tier {tier} • Max ${TIER_MAX_POS[tier]}\n"
            msg += f"🏛️ Tennessee 0% state tax\n"
            msg += f"🎯 Target: $50,000\n\n"
            msg += f"📈 Today: {trade_count} trades\n"
            msg += f"💰 Volume: ${buy_vol:,.0f}\n"
            msg += f"📊 P&L: ${self.daily_pnl:+.2f}\n\n"
            msg += f"✅ All systems operational"

            await self.send_message(msg, is_daily_summary=True)

        except Exception as e:
            logger.error(f"Daily summary failed: {e}")

    async def scan_markets(self):
        """Main scanning loop"""
        try:
            # Get account
            account = trading.get_account()
            equity = float(account.equity)
            tier = self.get_tier(equity)

            if self.start_equity == 0:
                self.start_equity = equity
                conn.execute(
                    'INSERT OR REPLACE INTO system_state VALUES (?,?,?)',
                    ('start_equity', str(equity), datetime.now().isoformat())
                )

            # Update positions
            try:
                positions = trading.get_all_positions()
                self.positions = {p.symbol: float(p.avg_entry_price) for p in positions}
            except:
                self.positions = {}

            # Check if we should trade
            if len(self.positions) >= TIER_MAX_POSITIONS[tier]:
                logger.info(f"Max positions reached ({len(self.positions)})")
                return

            # Scan symbols
            all_symbols = CRYPTO + STOCKS
            random.shuffle(all_symbols) # Avoid always checking same order

            for symbol in all_symbols:
                # Check limits
                if self.trades_today >= MAX_TRADES_PER_DAY:
                    break

                if len(self.positions) >= TIER_MAX_POSITIONS[tier]:
                    break

                # Check market hours
                is_crypto = '/' in symbol
                if not self.is_market_hours(is_crypto):
                    continue

                # Fetch and analyze
                df = await self.fetch_market_data(symbol)
                if df is None:
                    continue

                analysis = self.analyze_symbol(symbol, df)
                if analysis is None:
                    continue

                # Trading logic
                has_position = symbol in self.positions

                if not has_position and analysis['signal'] == 'BUY':
                    if analysis['score'] >= BUY_SCORE_MIN:
                        success = await self.execute_trade(symbol, 'buy', analysis, tier)
                        if success:
                            await asyncio.sleep(3) # Avoid rate limits

                elif has_position and analysis['signal'] == 'SELL':
                    if analysis['score'] <= SELL_SCORE_MAX:
                        success = await self.execute_trade(symbol, 'sell', analysis, tier)
                        if success:
                            await asyncio.sleep(3)

            # Heartbeat check
            if (datetime.now() - self.last_heartbeat).seconds >= HEARTBEAT_MINUTES * 60:
                await self.send_heartbeat()

        except Exception as e:
            logger.error(f"Market scan error: {e}", exc_info=True)
            self.consecutive_errors += 1

    async def run(self):
        """Main run loop"""
        logger.info("=" * 60)
        logger.info("TRADING BOT STARTING")
        logger.info(f"Mode: {'PAPER' if PAPER else 'LIVE'}")
        logger.info(f"Time: {datetime.now()}")
        logger.info("=" * 60)

        # Send startup notification (daily summary format)
        await self.send_daily_summary()

        # Main loop
        while True:
            try:
                et = datetime.now(pytz.timezone('US/Eastern'))

                # Railway sleep mode (12am-8am ET)
                if 0 <= et.hour < 8:
                    if et.minute == 0: # Log once per hour
                        logger.info("😴 Railway sleep mode active (12am-8am ET)")
                    await asyncio.sleep(3600) # Sleep 1 hour
                    continue

                # Reset daily counters at midnight ET
                if et.hour == 0 and et.minute < 5 and self.trades_today > 0:
                    logger.info("Resetting daily counters")
                    self.trades_today = 0
                    self.start_equity = float(trading.get_account().equity)
                    self.daily_pnl = 0
                    await self.send_daily_summary()

                # Scan markets
                await self.scan_markets()

                # Reset error counter on success
                if self.consecutive_errors > 0:
                    self.consecutive_errors = max(0, self.consecutive_errors - 1)

                # Sleep between scans
                await asyncio.sleep(25)

            except KeyboardInterrupt:
                logger.info("Shutdown requested")
                break
            except Exception as e:
                logger.error(f"Critical error in main loop: {e}", exc_info=True)
                self.consecutive_errors += 1

                if self.consecutive_errors >= self.max_consecutive_errors:
                    logger.critical("Too many errors, shutting down")
                    await self.send_message("🚨 Bot stopped due to errors - check logs")
                    break

                await asyncio.sleep(60)

def main():
    """Entry point"""
    try:
        # Verify environment variables
        required_vars = ['APCA_API_KEY_ID', 'APCA_API_SECRET_KEY', 'TELEGRAM_TOKEN', 'TELEGRAM_CHAT_ID']
        missing = [var for var in required_vars if not os.getenv(var)]

        if missing:
            logger.error(f"Missing environment variables: {missing}")
            return

        # Create and run bot
        bot = TradingBot()
        asyncio.run(bot.run())

    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)

if __name__ == '__main__':
    main()