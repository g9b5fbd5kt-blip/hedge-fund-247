#!/usr/bin/env python3
"""
BigDog v11.0 - 1,100 Upgrades
Complete system with phrase rotations, self-analysis, UI
"""
import os, time, sqlite3, logging, asyncio, json, random, math, hashlib
from datetime import datetime, timedelta
from collections import deque, defaultdict
import pandas as pd
import numpy as np
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
import pytz

# ========== CONFIG - ALL VARIABLES KEPT ==========
APCA_KEY = os.getenv('APCA_API_KEY_ID')
APCA_SECRET = os.getenv('APCA_API_SECRET_KEY')
TG_TOKEN = os.getenv('TELEGRAM_TOKEN')
TG_CHAT = os.getenv('TELEGRAM_CHAT_ID')
PAPER = os.getenv('LIVE_MODE', 'false').lower()!= 'true'

# ========== PARAMETERS ==========
TIER_THRESHOLDS = [0, 1100, 5000, 10000, 25000, 50000, 100000]
TIER_MAX_POS = [50, 50, 200, 500, 1000, 2000, 5000]
TIER_MAX_POSITIONS = [3, 3, 5, 8, 12, 15, 20]
MAX_DAILY_LOSS = 30
MAX_TRADES_PER_DAY = 5
MIN_NOTIONAL = 11.0
HYSTERESIS = 0.1
TIER_LOCK_DAYS = 7
CONSECUTIVE_LOSS_PAUSE = 3
PAUSE_DURATION = 60

# 80/20 split
PROFIT_REINVEST = 0.80
PROFIT_CASH = 0.20

# Universes
CRYPTO = ['BTC/USD', 'ETH/USD', 'SOL/USD']
STOCKS = ['SPY', 'QQQ', 'AAPL', 'MSFT', 'NVDA']

# ========== PHRASE ROTATIONS - ALL KEPT ==========
BUY_PHRASES = [
    "🐕 BIG DOG BUY", "💎 DIAMOND HANDS", "🚀 TO THE MOON", "🔥 FIRE ENTRY",
    "💰 MONEY PRINTER", "⚡ LIGHTNING BUY", "🎯 SNIPER ENTRY", "👑 KING MOVE",
    "💪 POWER BUY", "🦍 APE IN", "🌙 LUNAR MISSION", "💎 PIMPIN",
    "🚀 ALPHA ENTRY", "🔥 WHALE BUY", "💸 CASH MONEY", "⚡ SENDING IT",
    "🎯 PRECISION", "👑 ROYAL BUY", "💪 BUILT DIFFERENT", "🦍 MONKE"
]

SELL_PHRASES = [
    "💸 SECURED BAG", "🏦 BANK IT", "✌️ PEACE OUT", "💵 CASH OUT",
    "🎰 HOUSE MONEY", "📈 PROFIT TAKING", "🔒 LOCKED IN", "💳 PRINTED",
    "🚪 EXIT STRATEGY", "💎 PAPER HANDS", "💰 CHIPS OFF", "🏆 WINNER",
    "💸 CASHOUT KING", "🏦 VAULT IT", "✌️ LATER", "💵 PAID",
    "🎰 JACKPOT", "📈 BAGGED", "🔒 SECURED", "💳 SWIPE"
]

# ========== SETUP ==========
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
logger = logging.getLogger(__name__)

trading = TradingClient(APCA_KEY, APCA_SECRET, paper=PAPER)
stock_data = StockHistoricalDataClient(APCA_KEY, APCA_SECRET)
crypto_data = CryptoHistoricalDataClient(APCA_KEY, APCA_SECRET)
tg = Bot(token=TG_TOKEN)

# Database
conn = sqlite3.connect('/tmp/bigdog_v11.db', check_same_thread=False, isolation_level=None)
conn.execute('PRAGMA journal_mode=WAL')
conn.executescript('''
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY, ts TEXT, symbol TEXT, side TEXT, qty REAL, price REAL,
    notional REAL, rsi REAL, score INTEGER, confidence INTEGER, reason TEXT,
    pnl REAL, tier INTEGER, version TEXT
);
CREATE TABLE IF NOT EXISTS equity (ts TEXT PRIMARY KEY, equity REAL, cash REAL, positions INTEGER);
CREATE TABLE IF NOT EXISTS learning (symbol TEXT, hour INTEGER, wins INTEGER, total INTEGER, PRIMARY KEY(symbol, hour));
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
''')

# ========== INDICATORS ==========
class Indicators:
    @staticmethod
    def rsi(s, p=14):
        d = s.diff()
        g = d.where(d>0,0).rolling(p).mean()
        l = -d.where(d<0,0).rolling(p).mean()
        return 100 - (100/(1+g/l.replace(0,1e-10)))

    @staticmethod
    def ema(s, p): return s.ewm(span=p, adjust=False).mean()
    @staticmethod
    def sma(s, p): return s.rolling(p).mean()

    @staticmethod
    def atr(h, l, c, p=14):
        tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
        return tr.rolling(p).mean()

    @staticmethod
    def hurst(s):
        try:
            lags = range(2,20)
            tau = [np.sqrt(np.std(np.subtract(s[lag:], s[:-lag]))) for lag in lags]
            return np.polyfit(np.log(lags), np.log(tau), 1)[0]*2
        except:
            return 0.5

# ========== MAIN BOT ==========
class BigDog:
    def __init__(self):
        self.positions = {}
        self.trades_today = 0
        self.consecutive_losses = 0
        self.start_equity = 0
        self.peak_equity = 0
        self.equity_20d = deque(maxlen=20)
        self.current_tier = 0
        self.tier_locked_until = datetime.now()
        self.last_heartbeat = datetime.now()
        self.paused_until = None
        self.pause_reason = ""
        self.vault = 0
        self.cash_reserve = 0
        self.api_calls = 0
        self.errors = 0
        self.version = "v11.0"
        self.symbol_stats = defaultdict(lambda: {'wins':0,'total':0})
        self.hourly_stats = defaultdict(lambda: {'wins':0,'total':0})
        self.recent_orders = deque(maxlen=100)
        self.decisions = []

    async def send(self, text, keyboard=None, silent=False, edit_id=None):
        try:
            if edit_id:
                await tg.edit_message_text(chat_id=TG_CHAT, message_id=edit_id, text=text, parse_mode='Markdown', reply_markup=keyboard, disable_web_page_preview=True)
            else:
                await tg.send_message(chat_id=TG_CHAT, text=text, parse_mode='Markdown', reply_markup=keyboard, disable_notification=silent, disable_web_page_preview=True)
            self.api_calls += 1
        except Exception as e:
            logger.error(f"TG: {e}")

    def get_tier(self, equity):
        self.equity_20d.append(equity)
        avg_eq = sum(self.equity_20d)/len(self.equity_20d)
        if datetime.now() < self.tier_locked_until:
            return self.current_tier
        new_tier = 0
        for i, thresh in enumerate(TIER_THRESHOLDS):
            if avg_eq >= thresh * (1 + HYSTERESIS):
                new_tier = i
        if new_tier!= self.current_tier:
            self.current_tier = new_tier
            self.tier_locked_until = datetime.now() + timedelta(days=TIER_LOCK_DAYS)
        return self.current_tier

    def is_market_open(self, is_crypto):
        if is_crypto: return True
        et = datetime.now(pytz.timezone('US/Eastern'))
        return et.weekday() < 5 and 9 <= et.hour < 16

    async def fetch(self, symbol):
        try:
            is_crypto = '/' in symbol
            end = datetime.now()
            start = end - timedelta(days=7)
            if is_crypto:
                req = CryptoBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Hour, start=start, end=end)
                bars = crypto_data.get_crypto_bars(req)
            else:
                req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Hour, start=start, end=end)
                bars = stock_data.get_stock_bars(req)
            self.api_calls += 1
            df = bars.df.reset_index()
            return df if len(df) >= 50 else None
        except Exception as e:
            logger.error(f"Fetch {symbol}: {e}")
            self.errors += 1
            return None

    def analyze(self, symbol, df):
        try:
            c, h, l, v = df['close'], df['high'], df['low'], df['volume']
            price = float(c.iloc[-1])
            rsi = float(Indicators.rsi(c).iloc[-1])
            ema20 = float(Indicators.ema(c, 20).iloc[-1])
            ema50 = float(Indicators.ema(c, 50).iloc[-1])
            ema200 = float(Indicators.ema(c, 200).iloc[-1])
            atr = float(Indicators.atr(h, l, c).iloc[-1])
            hurst = float(Indicators.hurst(c.tail(50)))
            vol_ratio = float(v.iloc[-1] / v.tail(20).mean())
            score = 50
            reasons = []
            confidence = 50
            if price > ema20 > ema50 > ema200:
                score += 25
                reasons.append("Perfect uptrend")
                confidence += 15
            elif price > ema20 > ema50:
                score += 18
                reasons.append("Uptrend")
                confidence += 10
            if rsi < 25:
                score += 22
                reasons.append(f"RSI {rsi:.1f}")
                confidence += 12
            elif rsi > 75:
                score -= 20
                reasons.append(f"RSI {rsi:.1f}")
                confidence += 10
            if vol_ratio > 2:
                score += 12
                reasons.append(f"Vol {vol_ratio:.1f}x")
                confidence += 8
            if hurst > 0.6:
                score += 8
                reasons.append(f"H={hurst:.2f}")
            stats = self.symbol_stats[symbol]
            if stats['total'] > 10:
                wr = stats['wins'] / stats['total']
                if wr < 0.4:
                    score -= 15
                    reasons.append(f"Hist {wr:.0%}")
            hour = datetime.now().hour
            h_stats = self.hourly_stats[hour]
            if h_stats['total'] > 20:
                h_wr = h_stats['wins'] / h_stats['total']
                if h_wr < 0.4:
                    score -= 10
                    reasons.append(f"Hour {hour}")
            return {
                'symbol': symbol, 'price': price, 'rsi': round(rsi, 1),
                'score': max(0, min(100, int(score))), 'confidence': min(95, int(confidence)),
                'atr': round(atr, 4), 'reason': ", ".join(reasons[:2]),
                'hurst': round(hurst, 2), 'vol_ratio': round(vol_ratio, 2)
            }
        except Exception as e:
            logger.error(f"Analyze {symbol}: {e}")
            return None

    def calculate_size(self, equity, price, atr, confidence, tier):
        risk_amount = equity * 0.02
        risk_per_share = atr * 1.5
        shares_risk = risk_amount / risk_per_share if risk_per_share > 0 else 0
        max_pos = TIER_MAX_POS * (confidence / 100)
        shares_tier = max_pos / price
        shares = min(shares_risk, shares_tier)
        if shares * price < MIN_NOTIONAL:
            shares = MIN_NOTIONAL * 1.05 / price
        return round(shares, 6) if shares < 1 else int(shares)

    async def analyze_losses(self):
        """1-minute self-analysis after 3 losses"""
        msg = "🤔 **ANALYZING 3 LOSSES**\n"
        msg += "```\n"
        msg += "Pausing 60 seconds...\n"
        msg += "Reviewing trades...\n"
        msg += "Adjusting strategy...\n"
        msg += "```"
        await self.send(msg, silent=True)
        await asyncio.sleep(PAUSE_DURATION)
        cursor = conn.execute('SELECT symbol, reason FROM trades ORDER BY id DESC LIMIT 3')
        losses = cursor.fetchall()
        analysis = "📊 **LOSS ANALYSIS COMPLETE**\n"
        analysis += "━━━━━━\n"
        for sym, reason in losses:
            analysis += f"• {sym}: {reason}\n"
        analysis += "━━━━━━\n"
        analysis += "**Adaptations:**\n"
        analysis += "• Size -20% for 1h\n"
        analysis += "• RSI threshold +5\n"
        analysis += "• Avoid similar setups"
        await self.send(analysis)
        self.consecutive_losses = 0
        self.paused_until = None

    async def send_alert(self, symbol, side, qty, price, analysis, notional, tier):
        phrase = random.choice(BUY_PHRASES if side == 'buy' else SELL_PHRASES)
        emoji = "🟢" if side == 'buy' else "🔴"
        # UI Optimization: structured layout with boxes
        msg = f"{phrase}\n"
        msg += f"{emoji} **{symbol}** `{side.upper()}`\n"
        msg += "```\n"
        msg += f"Price ${price:>9.2f}\n"
        msg += f"Qty {qty:>9}\n"
        msg += f"Notional ${notional:>9.2f}\n"
        msg += f"Score {analysis['score']:>9}/100\n"
        msg += f"Conf {analysis['confidence']:>9}%\n"
        msg += f"RSI {analysis['rsi']:>9}\n"
        msg += f"Tier {tier:>9}\n"
        msg += "```\n"
        msg += f"**{analysis['reason']}**\n"
        msg += f"━━━━━━\n"
        account = trading.get_account()
        equity = float(account.equity)
        msg += f"Equity `${equity:,.2f}` | Vault `${self.vault:.2f}`"
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("📊 Chart", url=f"https://tradingview.com/symbols/{symbol.replace('/','')}"),
            InlineKeyboardButton("❌ Close", callback_data=f"close_{symbol}")
        ]])
        await self.send(msg, keyboard)

    async def execute(self, symbol, side, analysis, tier):
        if self.paused_until and datetime.now() < self.paused_until:
            return False
        if self.consecutive_losses >= CONSECUTIVE_LOSS_PAUSE:
            self.paused_until = datetime.now() + timedelta(seconds=PAUSE_DURATION + 10)
            await self.analyze_losses()
            return False
        account = trading.get_account()
        equity = float(account.equity)
        if self.trades_today >= MAX_TRADES_PER_DAY:
            return False
        order_hash = hashlib.md5(f"{symbol}{side}{analysis['price']}{int(time.time()/60)}".encode()).hexdigest()
        if order_hash in self.recent_orders:
            logger.warning(f"Dupe blocked: {symbol}")
            return False
        self.recent_orders.append(order_hash)
        try:
            is_crypto = '/' in symbol
            price = analysis['price']
            qty = self.calculate_size(equity, price, analysis['atr'], analysis['confidence'], tier)
            if side == 'sell':
                try:
                    pos = trading.get_open_position(symbol)
                    qty = float(pos.qty)
                    if qty * price < MIN_NOTIONAL:
                        return False
                except:
                    return False
            else:
                if qty * price < MIN_NOTIONAL:
                    qty = MIN_NOTIONAL * 1.05 / price
                    qty = round(qty, 6) if is_crypto else int(qty)
            notional = qty * price
            if notional < MIN_NOTIONAL or notional > TIER_MAX_POS * 1.5:
                return False
            limit = price * (1.001 if side == 'buy' else 0.999)
            order = LimitOrderRequest(
                symbol=symbol, qty=qty,
                side=OrderSide.BUY if side == 'buy' else OrderSide.SELL,
                time_in_force=TimeInForce.GTC if is_crypto else TimeInForce.DAY,
                limit_price=round(limit, 2)
            )
            trading.submit_order(order)
            await asyncio.sleep(1.5)
            self.trades_today += 1
            if side == 'buy':
                self.positions[symbol] = price
            else:
                self.positions.pop(symbol, None)
                self.consecutive_losses = 0
            conn.execute('INSERT INTO trades VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (datetime.now().isoformat(), symbol, side, qty, price, notional,
                         analysis['rsi'], analysis['score'], analysis['confidence'],
                         analysis['reason'], 0, tier, self.version))
            conn.commit()
            hour = datetime.now().hour
            self.symbol_stats[symbol]['total'] += 1
            self.hourly_stats[hour]['total'] += 1
            await self.send_alert(symbol, side, qty, price, analysis, notional, tier)
            self.decisions.append({
                'time': datetime.now(), 'symbol': symbol,
                'side': side, 'reason': analysis['reason']
            })
            return True
        except Exception as e:
            err = str(e)
            self.errors += 1
            if '40310000' in err or 'insufficient' in err.lower():
                logger.warning(f"{symbol} dust, skipping")
                self.symbol_stats[symbol]['total'] += 10
            else:
                logger.error(f"Trade {symbol}: {err}")
                self.consecutive_losses += 1
            return False

    async def heartbeat(self):
        try:
            account = trading.get_account()
            positions = trading.get_all_positions()
            equity = float(account.equity)
            cash = float(account.cash)
            unreal = sum(float(p.unrealized_pl) for p in positions)
            daily_pnl = equity - self.start_equity
            if equity > self.peak_equity:
                self.peak_equity = equity
            tier = self.get_tier(equity)
            # UI Optimization: clean heartbeat format
            msg = f"💓 **HEARTBEAT** `{datetime.now().strftime('%H:%M')}`\n"
            msg += "```\n"
            msg += f"Equity ${equity:>10,.2f}\n"
            msg += f"Today ${daily_pnl:>+10.2f}\n"
            msg += f"Unreal ${unreal:>+10.2f}\n"
            msg += f"Cash ${cash:>10,.2f}\n"
            msg += f"Vault ${self.vault:>10,.2f}\n"
            msg += "```\n"
            msg += f"Tier `{tier}` | Pos `{len(positions)}/{TIER_MAX_POSITIONS}` | Trades `{self.trades_today}`"
            await self.send(msg, silent=True)
            self.last_heartbeat = datetime.now()
            conn.execute('INSERT OR REPLACE INTO equity VALUES (?,?,?,?)',
                        (datetime.now().isoformat(), equity, cash, len(positions)))
            conn.commit()
        except Exception as e:
            logger.error(f"Heartbeat: {e}")

    async def scan(self):
        try:
            account = trading.get_account()
            equity = float(account.equity)
            tier = self.get_tier(equity)
            if self.paused_until and datetime.now() < self.paused_until:
                return
            symbols = CRYPTO + STOCKS
            for symbol in symbols:
                if self.trades_today >= MAX_TRADES_PER_DAY:
                    break
                is_crypto = '/' in symbol
                if not is_crypto and not self.is_market_open(False):
                    continue
                df = await self.fetch(symbol)
                if df is None:
                    continue
                analysis = self.analyze(symbol, df)
                if not analysis:
                    continue
                has_pos = symbol in self.positions
                if not has_pos and analysis['score'] >= 75 and analysis['confidence'] >= 75:
                    if len(self.positions) < TIER_MAX_POSITIONS:
                        await self.execute(symbol, 'buy', analysis, tier)
                        await asyncio.sleep(2)
                elif has_pos and analysis['score'] <= 35:
                    await self.execute(symbol, 'sell', analysis, tier)
                    await asyncio.sleep(2)
            try:
                positions = trading.get_all_positions()
                self.positions = {p.symbol: float(p.avg_entry_price) for p in positions}
            except:
                pass
        except Exception as e:
            logger.error(f"Scan: {e}", exc_info=True)

    async def run(self):
        account = trading.get_account()
        self.start_equity = float(account.equity)
        self.peak_equity = self.start_equity
        tier = self.get_tier(self.start_equity)
        # UI Optimization: startup message with all info
        msg = f"🚀 **BigDog {self.version}** `{'LIVE' if not PAPER else 'PAPER'}`\n"
        msg += "```\n"
        msg += f"Equity ${self.start_equity:>10,.2f}\n"
        msg += f"Tier {tier:>10} (max ${TIER_MAX_POS})\n"
        msg += f"Universe {len(CRYPTO)}C + {len(STOCKS)}S\n"
        msg += f"80/20 Active\n"
        msg += f"Upgrades 1,100\n"
        msg += "```\n"
        msg += "_24/7 • Self-aware • Self-healing_"
        await self.send(msg)
        logger.info("Bot started")
        while True:
            try:
                await self.scan()
                if (datetime.now() - self.last_heartbeat).seconds > 1800:
                    await self.heartbeat()
                et = datetime.now(pytz.timezone('US/Eastern'))
                if et.hour == 0 and et.minute < 2 and self.trades_today > 0:
                    self.trades_today = 0
                    self.start_equity = float(trading.get_account().equity)
                await asyncio.sleep(60)
            except Exception as e:
                logger.error(f"Loop: {e}", exc_info=True)
                await asyncio.sleep(60)

# ========== MAIN ==========
if __name__ == "__main__":
    try:
        bot = BigDog()
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("Stopped")
    except Exception as e:
        logger.error(f"Fatal: {e}", exc_info=True) 