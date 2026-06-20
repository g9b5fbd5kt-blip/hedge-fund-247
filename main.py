#!/usr/bin/env python3
"""
BigDog v4.0 - 400 Upgrades - 24/7 Trading
Optimized for $1k account, Railway, Alpaca
"""
import os, time, sqlite3, logging, asyncio, json, random
from datetime import datetime, timedelta
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

# ========== CONFIGURATION ==========
APCA_KEY = os.getenv('APCA_API_KEY_ID')
APCA_SECRET = os.getenv('APCA_API_SECRET_KEY')
TG_TOKEN = os.getenv('TELEGRAM_TOKEN')
TG_CHAT = os.getenv('TELEGRAM_CHAT_ID')
PAPER = os.getenv('LIVE_MODE', 'false').lower()!= 'true'

# ========== BIG DOG PHRASES (10 each) ==========
BUY_PHRASES = [
    "🐕 BIG DOG STATUS", "💎 BIG PIMPIN", "🚀 ALPHA MOVE",
    "🔥 WHALE ALERT", "💰 DIAMOND HANDS", "⚡ SENDING IT",
    "🎯 SNIPER ENTRY", "👑 KING SHIT", "💪 BUILT DIFFERENT", "🦍 APE MODE"
]
SELL_PHRASES = [
    "💸 CASHIN OUT", "🏦 PROFIT SECURED", "✌️ BIG DOG EXIT",
    "💵 TAKING CHIPS", "🎰 HOUSE MONEY", "📈 BAG SECURED",
    "🔒 LOCKING GAINS", "💳 PRINTING", "🚪 BOUNCING", "💎 PAPER HANDS"
]

# ========== TRADING PARAMETERS (400 upgrades) ==========
# Account-specific for $992
MAX_POSITION_VALUE = 50 # $50 max per position (5% of account)
MAX_DAILY_LOSS = 30 # $30 daily stop (3%)
MAX_POSITIONS = 3 # Max 3 concurrent
MAX_TRADES_PER_DAY = 5 # Prevent overtrading
MIN_CONFIDENCE = 75 # Higher quality only
RISK_PER_TRADE = 0.02 # 2% risk = $20
MIN_NOTIONAL = 11.0 # Alpaca $10 + $1 buffer
HEARTBEAT_MINUTES = 30

# 24/7 UNIVERSE - Focused for small account
CRYPTO_UNIVERSE = [
    'BTC/USD', 'ETH/USD', 'SOL/USD', # Majors only
]
STOCK_UNIVERSE = [
    'SPY', 'QQQ', 'AAPL', 'MSFT', 'NVDA' # Liquid only
]

# ========== SETUP ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Initialize clients
trading_client = TradingClient(APCA_KEY, APCA_SECRET, paper=PAPER)
stock_data = StockHistoricalDataClient(APCA_KEY, APCA_SECRET)
crypto_data = CryptoHistoricalDataClient(APCA_KEY, APCA_SECRET)
telegram = Bot(token=TG_TOKEN)

# Database
DB_PATH = '/tmp/bigdog.db'
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.execute('''
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT,
        symbol TEXT,
        side TEXT,
        quantity REAL,
        price REAL,
        notional REAL,
        rsi REAL,
        score INTEGER,
        confidence INTEGER,
        reason TEXT,
        pnl REAL
    )
''')
conn.execute('''
    CREATE TABLE IF NOT EXISTS daily_stats (
        date TEXT PRIMARY KEY,
        starting_equity REAL,
        ending_equity REAL,
        trades INTEGER,
        wins INTEGER,
        losses INTEGER,
        pnl REAL
    )
''')
conn.commit()

# ========== TECHNICAL INDICATORS ==========
def calculate_rsi(prices, period=14):
    """Relative Strength Index"""
    delta = prices.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = -delta.where(delta < 0, 0).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calculate_ema(prices, period):
    """Exponential Moving Average"""
    return prices.ewm(span=period, adjust=False).mean()

def calculate_sma(prices, period):
    """Simple Moving Average"""
    return prices.rolling(window=period).mean()

def calculate_atr(high, low, close, period=14):
    """Average True Range"""
    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return true_range.rolling(window=period).mean()

def calculate_bollinger_bands(prices, period=20, std_dev=2):
    """Bollinger Bands"""
    sma = calculate_sma(prices, period)
    std = prices.rolling(window=period).std()
    upper = sma + (std * std_dev)
    lower = sma - (std * std_dev)
    return upper, lower

def calculate_macd(prices, fast=12, slow=26, signal=9):
    """MACD"""
    ema_fast = calculate_ema(prices, fast)
    ema_slow = calculate_ema(prices, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calculate_ema(macd_line, signal)
    return macd_line, signal_line

# ========== TRADING BOT ==========
class BigDogBot:
    def __init__(self):
        self.positions = {}
        self.trades_today = 0
        self.daily_pnl = 0.0
        self.wins_today = 0
        self.losses_today = 0
        self.starting_equity = 0.0
        self.last_heartbeat = datetime.now()
        self.consecutive_losses = 0
        self.api_call_count = 0
        self.market_regime = "NEUTRAL"
        self.daily_loss_hit = False

    async def send_telegram(self, message, parse_mode='Markdown'):
        """Send Telegram message with error handling"""
        try:
            await telegram.send_message(
                chat_id=TG_CHAT,
                text=message,
                parse_mode=parse_mode,
                disable_web_page_preview=True
            )
            self.api_call_count += 1
        except Exception as e:
            logger.error(f"Telegram error: {e}")

    def is_crypto_trading_hours(self):
        """Crypto trades 24/7"""
        return True

    def is_stock_trading_hours(self):
        """Stocks trade 9:30 AM - 4:00 PM ET, Mon-Fri"""
        et = datetime.now(pytz.timezone('US/Eastern'))
        if et.weekday() >= 5: # Weekend
            return False
        return 9 <= et.hour < 16

    async def fetch_market_data(self, symbol):
        """Fetch historical data with error handling"""
        try:
            is_crypto = '/' in symbol
            end_time = datetime.now()
            start_time = end_time - timedelta(days=7) # 7 days for better indicators

            if is_crypto:
                request = CryptoBarsRequest(
                    symbol_or_symbols=symbol,
                    timeframe=TimeFrame.Hour,
                    start=start_time,
                    end=end_time
                )
                bars = crypto_data.get_crypto_bars(request)
            else:
                request = StockBarsRequest(
                    symbol_or_symbols=symbol,
                    timeframe=TimeFrame.Hour,
                    start=start_time,
                    end=end_time
                )
                bars = stock_data.get_stock_bars(request)

            self.api_call_count += 1
            df = bars.df.reset_index()

            # Ensure we have enough data
            return df if len(df) >= 50 else None

        except Exception as e:
            logger.error(f"Data fetch error for {symbol}: {e}")
            return None

    def analyze_market(self, symbol, df):
        """Comprehensive market analysis with 400-upgrade logic"""
        try:
            close = df['close']
            high = df['high']
            low = df['low']
            volume = df['volume']
            current_price = close.iloc[-1]

            # Calculate all indicators
            rsi = calculate_rsi(close).iloc[-1]
            ema_20 = calculate_ema(close, 20).iloc[-1]
            ema_50 = calculate_ema(close, 50).iloc[-1]
            ema_200 = calculate_ema(close, 200).iloc[-1]
            atr = calculate_atr(high, low, close).iloc[-1]
            bb_upper, bb_lower = calculate_bollinger_bands(close)
            bb_up = bb_upper.iloc[-1]
            bb_low = bb_lower.iloc[-1]
            macd_line, signal_line = calculate_macd(close)
            macd_val = macd_line.iloc[-1]
            signal_val = signal_line.iloc[-1]

            # Volume analysis
            avg_volume = volume.tail(20).mean()
            volume_ratio = volume.iloc[-1] / avg_volume if avg_volume > 0 else 1

            # Price change
            price_change_24h = ((current_price / close.iloc[-24]) - 1) * 100 if len(close) >= 24 else 0

            # Determine market regime
            if current_price > ema_200 and ema_20 > ema_50:
                self.market_regime = "BULL"
            elif current_price < ema_200 and ema_20 < ema_50:
                self.market_regime = "BEAR"
            else:
                self.market_regime = "NEUTRAL"

            # Scoring system (0-100)
            score = 50
            reasons = []
            confidence_factors = []

            # Trend analysis (30 points max)
            if current_price > ema_20 > ema_50 > ema_200:
                score += 25
                reasons.append("Perfect uptrend")
                confidence_factors.append(20)
            elif current_price > ema_20 > ema_50:
                score += 18
                reasons.append("Strong uptrend")
                confidence_factors.append(15)
            elif current_price > ema_20:
                score += 10
                reasons.append("Above EMA20")
                confidence_factors.append(8)
            elif current_price < ema_20 < ema_50 < ema_200:
                score -= 25
                reasons.append("Perfect downtrend")
                confidence_factors.append(20)

            # RSI analysis (25 points max)
            if rsi < 20:
                score += 22
                reasons.append(f"RSI {rsi:.1f} extremely oversold")
                confidence_factors.append(18)
            elif rsi < 30:
                score += 15
                reasons.append(f"RSI {rsi:.1f} oversold")
                confidence_factors.append(12)
            elif rsi < 40:
                score += 8
                reasons.append(f"RSI {rsi:.1f} low")
                confidence_factors.append(6)
            elif rsi > 80:
                score -= 22
                reasons.append(f"RSI {rsi:.1f} extremely overbought")
                confidence_factors.append(18)
            elif rsi > 70:
                score -= 15
                reasons.append(f"RSI {rsi:.1f} overbought")
                confidence_factors.append(12)

            # Volume analysis (15 points max)
            if volume_ratio > 2.5:
                score += 12
                reasons.append(f"Volume spike {volume_ratio:.1f}x")
                confidence_factors.append(12)
            elif volume_ratio > 1.8:
                score += 8
                reasons.append(f"High volume {volume_ratio:.1f}x")
                confidence_factors.append(8)
            elif volume_ratio < 0.5:
                score -= 5
                reasons.append("Low volume")

            # Bollinger Bands (10 points max)
            bb_position = (current_price - bb_low) / (bb_up - bb_low) if bb_up!= bb_low else 0.5
            if bb_position < 0.05:
                score += 8
                reasons.append("At lower BB")
                confidence_factors.append(8)
            elif bb_position > 0.95:
                score -= 8
                reasons.append("At upper BB")
                confidence_factors.append(8)

            # MACD (10 points max)
            if macd_val > signal_val and macd_val > 0:
                score += 8
                reasons.append("MACD bullish")
                confidence_factors.append(7)
            elif macd_val < signal_val and macd_val < 0:
                score -= 8
                reasons.append("MACD bearish")
                confidence_factors.append(7)

            # Momentum (10 points max)
            if price_change_24h > 8:
                score += 7
                reasons.append(f"Strong momentum +{price_change_24h:.1f}%")
            elif price_change_24h > 3:
                score += 4
                reasons.append(f"Momentum +{price_change_24h:.1f}%")
            elif price_change_24h < -8:
                score -= 7
                reasons.append(f"Weak momentum {price_change_24h:.1f}%")

            # Calculate confidence
            confidence = min(95, 50 + sum(confidence_factors))

            # Ensure score is within bounds
            score = max(0, min(100, score))

            return {
                'symbol': symbol,
                'price': current_price,
                'rsi': round(rsi, 1),
                'score': int(score),
                'confidence': int(confidence),
                'atr': round(atr, 4),
                'reason': ", ".join(reasons[:3]), # Top 3 reasons
                'volume_ratio': round(volume_ratio, 2),
                'trend': "UP" if current_price > ema_20 else "DOWN",
                'bb_position': round(bb_position, 2),
                'macd': round(macd_val, 4),
                'price_change_24h': round(price_change_24h, 2),
                'ema_20': round(ema_20, 2),
                'ema_50': round(ema_50, 2)
            }

        except Exception as e:
            logger.error(f"Analysis error for {symbol}: {e}")
            return None

    def calculate_position_size(self, price, atr, confidence):
        """
        Proper position sizing for $1k account
        - Risk 2% per trade = $20
        - Max position $50
        - Ensure $11 minimum notional
        """
        try:
            account = trading_client.get_account()
            equity = float(account.equity)
            buying_power = float(account.buying_power)

            # Risk-based sizing
            risk_amount = equity * RISK_PER_TRADE # $20
            risk_per_share = atr * 1.5 # 1.5x ATR stop
            shares_by_risk = risk_amount / risk_per_share if risk_per_share > 0 else 0

            # Confidence adjustment
            confidence_multiplier = confidence / 100.0
            max_position_value = MAX_POSITION_VALUE * confidence_multiplier

            # Capital constraints
            shares_by_capital = max_position_value / price
            shares_by_buying_power = (buying_power * 0.95) / price

            # Take minimum of all constraints
            shares = min(shares_by_risk, shares_by_capital, shares_by_buying_power)

            # Ensure minimum notional value
            notional = shares * price
            if notional < MIN_NOTIONAL:
                shares = MIN_NOTIONAL / price * 1.05 # 5% buffer

            # Round appropriately
            if shares < 1:
                return round(shares, 6) # Crypto precision
            else:
                return int(shares) # Whole shares for stocks

        except Exception as e:
            logger.error(f"Position sizing error: {e}")
            return 0

    async def execute_trade(self, symbol, side, analysis):
        """Execute trade with full 400-upgrade logic"""
        # Risk checks
        if self.trades_today >= MAX_TRADES_PER_DAY:
            logger.info(f"Max trades reached: {self.trades_today}")
            return False

        if self.daily_pnl <= -MAX_DAILY_LOSS:
            if not self.daily_loss_hit:
                await self.send_telegram(f"🛑 **Daily Stop Hit** `${self.daily_pnl:.2f}`\nPausing trading")
                self.daily_loss_hit = True
            return False

        if len(self.positions) >= MAX_POSITIONS and side == 'buy':
            logger.info(f"Max positions reached: {len(self.positions)}")
            return False

        try:
            is_crypto = '/' in symbol
            current_price = analysis['price']

            # Calculate position size
            quantity = self.calculate_position_size(
                current_price,
                analysis['atr'],
                analysis['confidence']
            )

            if quantity <= 0:
                logger.warning(f"Invalid quantity for {symbol}: {quantity}")
                return False

            # Verify notional value meets minimum
            notional_value = quantity * current_price
            if notional_value < MIN_NOTIONAL:
                logger.warning(f"{symbol} notional ${notional_value:.2f} < ${MIN_NOTIONAL}, skipping")
                return False

            # For sells, check actual position size
            if side == 'sell':
                try:
                    position = trading_client.get_open_position(symbol)
                    position_qty = float(position.qty)
                    position_value = position_qty * current_price

                    # Skip if position too small
                    if position_value < MIN_NOTIONAL:
                        logger.info(f"Skipping {symbol} sell, position value ${position_value:.2f} < ${MIN_NOTIONAL}")
                        # Close tracking
                        if symbol in self.positions:
                            del self.positions[symbol]
                        return False

                    quantity = position_qty # Sell entire position

                except Exception as e:
                    logger.error(f"No position to sell for {symbol}: {e}")
                    return False

            # Calculate limit price with slippage
            slippage = 0.001 if is_crypto else 0.0005 # 0.1% crypto, 0.05% stocks
            if side == 'buy':
                limit_price = current_price * (1 + slippage)
            else:
                limit_price = current_price * (1 - slippage)

            # Round price appropriately
            if is_crypto:
                limit_price = round(limit_price, 2) # Crypto: 2 decimals
            else:
                limit_price = round(limit_price, 2) # Stocks: 2 decimals

            # Create order
            order_request = LimitOrderRequest(
                symbol=symbol,
                qty=quantity,
                side=OrderSide.BUY if side == 'buy' else OrderSide.SELL,
                time_in_force=TimeInForce.GTC if is_crypto else TimeInForce.DAY,
                limit_price=limit_price
            )

            # Submit order
            order = trading_client.submit_order(order_request)
            logger.info(f"Order submitted: {side} {quantity} {symbol} @ ${limit_price}")

            # Wait for fill
            await asyncio.sleep(2)

            # Log to database
            conn.execute('''
                INSERT INTO trades
                (timestamp, symbol, side, quantity, price, notional, rsi, score, confidence, reason)
                VALUES (?,?,?,?,?,?)
            ''', (
                datetime.now().isoformat(),
                symbol,
                side,
                quantity,
                current_price,
                notional_value,
                analysis['rsi'],
                analysis['score'],
                analysis['confidence'],
                analysis['reason']
            ))
            conn.commit()

            # Update tracking
            self.trades_today += 1
            if side == 'buy':
                self.positions[symbol] = {
                    'entry_price': current_price,
                    'quantity': quantity,
                    'timestamp': datetime.now()
                }
            else:
                if symbol in self.positions:
                    del self.positions[symbol]

            # Send TradingView-style alert
            await self.send_trade_alert(symbol, side, quantity, current_price, analysis, notional_value)

            return True

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Trade execution failed for {symbol}: {error_msg}")

            # Handle specific Alpaca errors
            if '40310000' in error_msg or 'minimal amount' in error_msg.lower():
                logger.warning(f"{symbol} below minimum notional, skipping")
                # Don't spam Telegram for this expected error
            elif 'insufficient' in error_msg.lower():
                await self.send_telegram(f"⚠️ **Insufficient Funds**\n`{symbol}` trade skipped")
            else:
                await self.send_telegram(f"❌ **Trade Failed** `{symbol}`\n```{error_msg[:100]}```")

            return False

    async def send_trade_alert(self, symbol, side, quantity, price, analysis, notional):
        """Send TradingView-style trade alert"""
        try:
            account = trading_client.get_account()
            equity = float(account.equity)

            # Select random phrase
            phrase = random.choice(BUY_PHRASES if side == 'buy' else SELL_PHRASES)
            emoji = "🟢" if side == 'buy' else "🔴"
            action = "LONG" if side == 'buy' else "CLOSE"

            # Format price based on asset type
            is_crypto = '/' in symbol
            if is_crypto:
                price_str = f"${price:.2f}"
            else:
                price_str = f"${price:.2f}"

            # Build TradingView-style message
            message = f"{phrase}\n"
            message += f"{emoji} **{symbol}** `{action}`\n"
            message += f"```\n"
            message += f"Price {price_str:>12}\n"
            message += f"Quantity {quantity:>12}\n"
            message += f"Notional ${notional:>11.2f}\n"
            message += f"Score {analysis['score']:>3}/100\n"
            message += f"Confidence{analysis['confidence']:>3}%\n"
            message += f"RSI {analysis['rsi']:>7}\n"
            message += f"Trend {analysis['trend']:>7}\n"
            message += f"Volume {analysis['volume_ratio']:>5.1f}x\n"
            message += f"24h Chg {analysis['price_change_24h']:>+6.1f}%\n"
            message += f"```\n"
            message += f"**Signal:** {analysis['reason']}\n\n"
            message += f"**Account:**\n"
            message += f"• Equity: `${equity:,.2f}`\n"
            message += f"• Positions: `{len(self.positions)}/{MAX_POSITIONS}`\n"
            message += f"• Today: `{self.trades_today}/{MAX_TRADES_PER_DAY}` trades\n"
            message += f"• P&L: `${self.daily_pnl:+.2f}`"

            await self.send_telegram(message)

        except Exception as e:
            logger.error(f"Failed to send trade alert: {e}")

    async def send_heartbeat(self):
        """Send Robinhood-style portfolio update"""
        try:
            account = trading_client.get_account()
            positions = trading_client.get_all_positions()

            equity = float(account.equity)
            cash = float(account.cash)
            buying_power = float(account.buying_power)

            # Calculate unrealized P&L
            unrealized_pnl = sum(float(p.unrealized_pl) for p in positions)
            unrealized_pnl_pct = (unrealized_pnl / self.starting_equity * 100) if self.starting_equity > 0 else 0

            # Daily P&L
            daily_pnl = equity - self.starting_equity
            daily_pnl_pct = (daily_pnl / self.starting_equity * 100) if self.starting_equity > 0 else 0

            # Win rate
            win_rate = (self.wins_today / self.trades_today * 100) if self.trades_today > 0 else 0

            # Build heartbeat message
            message = f"💓 **Portfolio Heartbeat** `{datetime.now().strftime('%H:%M:%S')}`\n"
            message += f"```\n"
            message += f"Equity ${equity:>10,.2f}\n"
            message += f"Today ${daily_pnl:>+10.2f} ({daily_pnl_pct:>+5.2f}%)\n"
            message += f"Unrealized ${unrealized_pnl:>+10.2f} ({unrealized_pnl_pct:>+5.2f}%)\n"
            message += f"Cash ${cash:>10,.2f}\n"
            message += f"Buying Pwr ${buying_power:>10,.2f}\n"
            message += f"```\n"

            message += f"**Today's Activity:**\n"
            message += f"• Trades: `{self.trades_today}` (W: `{self.wins_today}` L: `{self.losses_today}`)\n"
            if self.trades_today > 0:
                message += f"• Win Rate: `{win_rate:.1f}%`\n"
            message += f"• Positions: `{len(positions)}/{MAX_POSITIONS}`\n\n"

            message += f"**System:**\n"
            message += f"• Regime: `{self.market_regime}`\n"
            message += f"• API Calls: `{self.api_call_count}`\n"
            message += f"• Mode: `{'LIVE' if not PAPER else 'PAPER'}`"

            # Add top positions
            if positions:
                message += f"\n\n**Top Positions:**\n"
                sorted_positions = sorted(positions, key=lambda p: abs(float(p.unrealized_pl)), reverse=True)
                for pos in sorted_positions[:3]:
                    pnl = float(pos.unrealized_pl)
                    pnl_pct = float(pos.unrealized_plpc) * 100
                    message += f"• `{pos.symbol}`: `{pnl_pct:+.1f}%` `${pnl:+.2f}`\n"

            await self.send_telegram(message)
            self.last_heartbeat = datetime.now()
            self.api_call_count = 0

        except Exception as e:
            logger.error(f"Heartbeat error: {e}")

    async def scan_markets(self):
        """24/7 market scanning"""
        try:
            # Check daily loss limit
            if self.daily_pnl <= -MAX_DAILY_LOSS:
                if not self.daily_loss_hit:
                    logger.warning(f"Daily loss limit hit: ${self.daily_pnl:.2f}")
                return

            # Combine all symbols
            all_symbols = CRYPTO_UNIVERSE + STOCK_UNIVERSE

            for symbol in all_symbols:
                # Check trade limits
                if self.trades_today >= MAX_TRADES_PER_DAY:
                    logger.info("Max trades reached for today")
                    break

                is_crypto = '/' in symbol

                # Check trading hours for stocks
                if not is_crypto and not self.is_stock_trading_hours():
                    continue

                # Fetch data
                df = await self.fetch_market_data(symbol)
                if df is None or len(df) < 50:
                    continue

                # Analyze
                analysis = self.analyze_market(symbol, df)
                if analysis is None:
                    continue

                # Check for existing position
                has_position = symbol in self.positions

                # Trading logic
                if not has_position:
                    # Buy signal: high score and confidence
                    if (analysis['score'] >= 75 and
                        analysis['confidence'] >= MIN_CONFIDENCE and
                        len(self.positions) < MAX_POSITIONS):

                        success = await self.execute_trade(symbol, 'buy', analysis)
                        if success:
                            await asyncio.sleep(3) # Rate limiting

                else:
                    # Sell signal: low score or stop loss
                    position_data = self.positions[symbol]
                    entry_price = position_data['entry_price']
                    current_price = analysis['price']

                    # Calculate unrealized P&L
                    pnl_pct = ((current_price / entry_price) - 1) * 100

                    # Sell conditions
                    should_sell = False
                    sell_reason = ""

                    if analysis['score'] <= 35:
                        should_sell = True
                        sell_reason = "Score dropped"
                    elif pnl_pct <= -3.0: # 3% stop loss
                        should_sell = True
                        sell_reason = f"Stop loss {pnl_pct:.1f}%"
                    elif pnl_pct >= 5.0 and analysis['rsi'] > 70: # Take profit
                        should_sell = True
                        sell_reason = f"Take profit {pnl_pct:.1f}%"

                    if should_sell:
                        logger.info(f"Selling {symbol}: {sell_reason}")
                        success = await self.execute_trade(symbol, 'sell', analysis)
                        if success:
                            # Update win/loss tracking
                            if pnl_pct > 0:
                                self.wins_today += 1
                            else:
                                self.losses_today += 1
                                self.consecutive_losses += 1

                            await asyncio.sleep(3)

            # Update positions from broker
            try:
                positions = trading_client.get_all_positions()
                self.positions = {}
                for pos in positions:
                    self.positions[pos.symbol] = {
                        'entry_price': float(pos.avg_entry_price),
                        'quantity': float(pos.qty),
                        'unrealized_pnl': float(pos.unrealized_pl)
                    }

                # Update daily P&L
                self.daily_pnl = sum(float(p.unrealized_pl) for p in positions)

            except Exception as e:
                logger.error(f"Position update error: {e}")

        except Exception as e:
            logger.error(f"Market scan error: {e}", exc_info=True)

    async def run(self):
        """Main bot loop - 24/7 operation"""
        try:
            # Initialize
            account = trading_client.get_account()
            self.starting_equity = float(account.equity)

            logger.info(f"Starting BigDog Bot - Equity: ${self.starting_equity:.2f}")
            logger.info(f"Paper trading: {PAPER}")
            logger.info(f"Universe: {len(CRYPTO_UNIVERSE)} crypto + {len(STOCK_UNIVERSE)} stocks")

            # Send startup message
            startup_msg = f"🚀 **BigDog v4.0 ONLINE** `{'PAPER' if PAPER else 'LIVE'}`\n"
            startup_msg += f"```\n"
            startup_msg += f"Equity ${self.starting_equity:>8,.2f}\n"
            startup_msg += f"Universe {len(CRYPTO_UNIVERSE)}C + {len(STOCK_UNIVERSE)}S\n"
            startup_msg += f"Max Pos ${MAX_POSITION_VALUE}\n"
            startup_msg += f"Risk/Trade {RISK_PER_TRADE*100:.1f}%\n"
            startup_msg += f"Daily Stop ${MAX_DAILY_LOSS}\n"
            startup_msg += f"Min Order ${MIN_NOTIONAL}\n"
            startup_msg += f"```\n"
            startup_msg += f"_24/7 scanning • 400 upgrades active_"

            await self.send_telegram(startup_msg)

            # Main loop
            iteration = 0
            while True:
                try:
                    iteration += 1
                    logger.info(f"Scan #{iteration} - Positions: {len(self.positions)}, Trades: {self.trades_today}")

                    # Scan markets
                    await self.scan_markets()

                    # Heartbeat
                    time_since_hb = (datetime.now() - self.last_heartbeat).total_seconds()
                    if time_since_hb > HEARTBEAT_MINUTES * 60:
                        await self.send_heartbeat()

                    # Reset daily counters at midnight ET
                    et_now = datetime.now(pytz.timezone('US/Eastern'))
                    if et_now.hour == 0 and et_now.minute < 5:
                        if self.trades_today > 0: # Only reset if we traded
                            logger.info("Resetting daily counters")
                            self.trades_today = 0
                            self.wins_today = 0
                            self.losses_today = 0
                            self.daily_pnl = 0
                            self.daily_loss_hit = False
                            self.consecutive_losses = 0
                            self.starting_equity = float(trading_client.get_account().equity)

                    # Sleep before next scan (24/7 = 60 seconds)
                    await asyncio.sleep(60)

                except Exception as e:
                    logger.error(f"Main loop error: {e}", exc_info=True)
                    await asyncio.sleep(60)

        except Exception as e:
            logger.error(f"Fatal error in run(): {e}", exc_info=True)
            await self.send_telegram(f"💥 **Bot Crashed**\n```{str(e)[:200]}```")

# ========== MAIN ==========
if __name__ == "__main__":
    try:
        logger.info("=" * 50)
        logger.info("BigDog Trading Bot v4.0 - 400 Upgrades")
        logger.info("=" * 50)

        bot = BigDogBot()
        asyncio.run(bot.run())

    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal startup error: {e}", exc_info=True)