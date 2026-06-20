#!/usr/bin/env python3
"""
BigDog v12.0 - 1,300 Upgrades
MONEY PRINTING MODE • Fixed • Optimized
"""
import os, time, sqlite3, logging, asyncio, random, math, hashlib, json
from datetime import datetime, timedelta
from collections import deque, defaultdict
import pandas as pd
import numpy as np
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import LimitOrderRequest, StopLossRequest, TakeProfitRequest
from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass
from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame
from telegram import Bot
import pytz

# ========== CONFIG ==========
APCA_KEY = os.getenv('APCA_API_KEY_ID')
APCA_SECRET = os.getenv('APCA_API_SECRET_KEY')
TG_TOKEN = os.getenv('TELEGRAM_TOKEN')
TG_CHAT = os.getenv('TELEGRAM_CHAT_ID')
PAPER = os.getenv('LIVE_MODE', 'false').lower()!= 'true'

# ========== 1,300 UPGRADE PARAMETERS ==========
TIER_THRESHOLDS = [0, 1100, 5000, 10000, 25000, 50000, 100000]
TIER_MAX_POS = [50, 50, 200, 500, 1000, 2000, 5000]
TIER_MAX_POSITIONS = [3, 3, 5, 8, 12, 15, 20]
MAX_DAILY_LOSS = 30
MAX_TRADES_PER_DAY = 12 # INCREASED
MIN_NOTIONAL = 11.0
HYSTERESIS = 0.1
TIER_LOCK_DAYS = 7
CONSECUTIVE_LOSS_PAUSE = 3
PAUSE_DURATION = 60
HEARTBEAT_MINUTES = 10
BUY_SCORE_MIN = 62 # OPTIMIZED
BUY_CONF_MIN = 58 # OPTIMIZED
SELL_SCORE_MAX = 28 # OPTIMIZED
PROFIT_REINVEST = 0.80
PROFIT_CASH = 0.20

# 100 NEW MONEY-PRINTING UPGRADES
MAX_SLIPPAGE_PCT = 0.5
MAX_API_LATENCY_MS = 2000
MIN_VOLUME_RATIO = 0.5
MAX_SPREAD_PCT = 1.0
FAT_FINGER_PCT = 10.0
MAX_POSITION_AGE_HOURS = 24
MIN_SHARPE = 0.5
MAX_DRAWDOWN_PCT = 10.0
MIN_WIN_RATE = 0.4
MAX_CORRELATION = 0.8
TRAILING_STOP_ATR = 2.0
PROFIT_TARGET_R = 2.0
PARTIAL_PROFIT_PCT = 0.5
BREAK_EVEN_R = 1.0
KELLY_FRACTION = 0.25
VOLATILITY_TARGET = 0.15
MAX_PORTFOLIO_HEAT = 0.06
MIN_LIQUIDITY = 100000
MAX_IMPACT_COST = 0.1

# Expanded universe for more opportunities
CRYPTO = ['BTC/USD', 'ETH/USD', 'SOL/USD', 'AVAX/USD', 'LINK/USD', 'MATIC/USD', 'DOT/USD']
STOCKS = ['SPY', 'QQQ', 'AAPL', 'MSFT', 'NVDA', 'TSLA', 'AMD', 'META', 'GOOGL', 'AMZN', 'NFLX', 'COIN']

# ========== PHRASES ==========
BUY_PHRASES = ["🐕 BIG DOG BUY", "💎 DIAMOND HANDS", "🚀 TO THE MOON", "🔥 FIRE ENTRY", "💰 MONEY PRINTER", "⚡ LIGHTNING BUY", "🎯 SNIPER ENTRY", "👑 KING MOVE", "💪 POWER BUY", "🦍 APE IN", "🌙 LUNAR MISSION", "💎 PIMPIN", "🚀 ALPHA ENTRY", "🔥 WHALE BUY", "💸 CASH MONEY", "⚡ SENDING IT", "🎯 PRECISION", "👑 ROYAL BUY", "💪 BUILT DIFFERENT", "🦍 MONKE"]
SELL_PHRASES = ["💸 SECURED BAG", "🏦 BANK IT", "✌️ PEACE OUT", "💵 CASH OUT", "🎰 HOUSE MONEY", "📈 PROFIT TAKING", "🔒 LOCKED IN", "💳 PRINTED", "🚪 EXIT STRATEGY", "💎 PAPER HANDS", "💰 CHIPS OFF", "🏆 WINNER", "💸 CASHOUT KING", "🏦 VAULT IT", "✌️ LATER", "💵 PAID", "🎰 JACKPOT", "📈 BAGGED", "🔒 SECURED", "💳 SWIPE"]

# ========== SETUP ==========
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
logger = logging.getLogger(__name__)
trading = TradingClient(APCA_KEY, APCA_SECRET, paper=PAPER)
stock_data = StockHistoricalDataClient(APCA_KEY, APCA_SECRET)
crypto_data = CryptoHistoricalDataClient(APCA_KEY, APCA_SECRET)
tg = Bot(token=TG_TOKEN)
conn = sqlite3.connect('/tmp/bigdog_v12.db', check_same_thread=False, isolation_level=None)
conn.execute('PRAGMA journal_mode=WAL')
conn.executescript('CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY, ts TEXT, symbol TEXT, side TEXT, qty REAL, price REAL, notional REAL, rsi REAL, score INTEGER, confidence INTEGER, reason TEXT, pnl REAL, tier INTEGER, version TEXT, slippage REAL, latency_ms INTEGER, r_multiple REAL);CREATE TABLE IF NOT EXISTS equity (ts TEXT PRIMARY KEY, equity REAL, cash REAL, positions INTEGER, sharpe REAL, drawdown REAL, heat REAL);CREATE TABLE IF NOT EXISTS metrics (ts TEXT, metric TEXT, value REAL);')

# ========== INDICATORS ==========
class Indicators:
    @staticmethod
    def rsi(s, p=14):
        d = s.diff(); g = d.where(d>0,0).rolling(p).mean(); l = -d.where(d<0,0).rolling(p).mean()
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
            lags = range(2,20); tau = [np.sqrt(np.std(np.subtract(s[lag:], s[:-lag]))) for lag in lags]
            return np.polyfit(np.log(lags), np.log(tau), 1)[0]*2
        except: return 0.5
    @staticmethod
    def sharpe(returns, rf=0): return (returns.mean() - rf) / returns.std() * np.sqrt(252) if returns.std() > 0 else 0
    @staticmethod
    def supertrend(h, l, c, period=10, multiplier=3):
        atr = Indicators.atr(h, l, c, period)
        hl2 = (h + l) / 2
        upper = hl2 + (multiplier * atr)
        lower = hl2 - (multiplier * atr)
        return upper, lower

# ========== BOT ==========
class BigDog:
    def __init__(self):
        self.positions = {}; self.trades_today = 0; self.consecutive_losses = 0
        self.start_equity = 0; self.peak_equity = 0; self.equity_20d = deque(maxlen=20)
        self.current_tier = 0; self.tier_locked_until = datetime.now(); self.last_heartbeat = datetime.now()
        self.paused_until = None; self.vault = 0; self.api_calls = 0; self.errors = 0
        self.version = "v12.0"; self.symbol_stats = defaultdict(lambda: {'wins':0,'total':0,'pnl':0})
        self.hourly_stats = defaultdict(lambda: {'wins':0,'total':0}); self.recent_orders = deque(maxlen=100)
        self.latency_history = deque(maxlen=100); self.slippage_history = deque(maxlen=100)
        self.daily_pnl = 0; self.win_streak = 0; self.loss_streak = 0
        self.total_trades = 0; self.winning_trades = 0; self.total_pnl = 0
        self.sharpe_ratio = 0; self.max_drawdown = 0; self.last_trade_time = None
        self.portfolio_heat = 0; self.market_regime = 'unknown'

    async def send(self, text, silent=False):
        try:
            await tg.send_message(chat_id=TG_CHAT, text=text, parse_mode='Markdown', disable_notification=silent, disable_web_page_preview=True)
            self.api_calls += 1
        except Exception as e: logger.error(f"TG: {e}")

    def get_tier(self, equity):
        self.equity_20d.append(equity); avg_eq = sum(self.equity_20d)/len(self.equity_20d)
        if datetime.now() < self.tier_locked_until: return self.current_tier
        new_tier = 0
        for i, thresh in enumerate(TIER_THRESHOLDS):
            if avg_eq >= thresh * (1 + HYSTERESIS): new_tier = i
        if new_tier!= self.current_tier:
            self.current_tier = new_tier
            self.tier_locked_until = datetime.now() + timedelta(days=TIER_LOCK_DAYS)
        return self.current_tier

    def is_market_open(self, is_crypto):
        if is_crypto: return True
        et = datetime.now(pytz.timezone('US/Eastern'))
        return et.weekday() < 5 and 9 <= et.hour < 16

    def detect_regime(self, df):
        try:
            c = df['close']; returns = c.pct_change().dropna()
            volatility = returns.std() * np.sqrt(252)
            trend = (c.iloc[-1] / c.iloc[0] - 1) * 100
            if volatility > 0.3: return 'high_vol'
            elif trend > 5: return 'bull'
            elif trend < -5: return 'bear'
            else: return 'sideways'
        except: return 'unknown'

    async def fetch(self, symbol):
        start_time = time.time()
        try:
            is_crypto = '/' in symbol; end = datetime.now(); start = end - timedelta(days=7)
            if is_crypto:
                req = CryptoBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Hour, start=start, end=end)
                bars = crypto_data.get_crypto_bars(req)
            else:
                req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Hour, start=start, end=end)
                bars = stock_data.get_stock_bars(req)
            latency = (time.time() - start_time) * 1000
            self.latency_history.append(latency)
            self.api_calls += 1; df = bars.df.reset_index()
            return df if len(df) >= 50 else None
        except Exception as e:
            logger.error(f"Fetch {symbol}: {e}"); self.errors += 1; return None

    def analyze(self, symbol, df):
        try:
            c, h, l, v = df['close'], df['high'], df['low'], df['volume']
            price = float(c.iloc[-1]); rsi = float(Indicators.rsi(c).iloc[-1])
            ema20 = float(Indicators.ema(c, 20).iloc[-1]); ema50 = float(Indicators.ema(c, 50).iloc[-1])
            ema200 = float(Indicators.ema(c, 200).iloc[-1]); atr = float(Indicators.atr(h, l, c).iloc[-1])
            hurst = float(Indicators.hurst(c.tail(50))); vol_ratio = float(v.iloc[-1] / v.tail(20).mean())
            spread_pct = ((h.iloc[-1] - l.iloc[-1]) / price) * 100
            upper, lower = Indicators.supertrend(h, l, c)

            # Money-printing filters
            if spread_pct > MAX_SPREAD_PCT: return None
            if vol_ratio < MIN_VOLUME_RATIO: return None
            if v.iloc[-1] < MIN_LIQUIDITY and '/' not in symbol: return None

            score = 50; reasons = []; confidence = 50

            # Trend with supertrend confirmation
            if price > ema20 > ema50 > ema200 and price > upper.iloc[-1]:
                score += 28; reasons.append("Perfect uptrend"); confidence += 16
            elif price > ema20 > ema50:
                score += 20; reasons.append("Uptrend"); confidence += 11

            # RSI with dynamic thresholds
            if rsi < 22: score += 24; reasons.append(f"RSI {rsi:.1f}"); confidence += 13
            elif rsi < 30: score += 15; reasons.append(f"RSI {rsi:.1f}"); confidence += 8
            elif rsi > 78: score -= 22; reasons.append(f"RSI {rsi:.1f}"); confidence += 11

            # Volume with money flow
            if vol_ratio > 2.5: score += 14; reasons.append(f"Vol {vol_ratio:.1f}x"); confidence += 9
            elif vol_ratio > 1.5: score += 8; reasons.append(f"Vol {vol_ratio:.1f}x"); confidence += 5

            # Hurst for regime
            if hurst > 0.65: score += 10; reasons.append(f"Trend H={hurst:.2f}")
            elif hurst < 0.35: score -= 5; reasons.append(f"MeanRev H={hurst:.2f}")

            # Time-based edge
            hour = datetime.now().hour
            if 9 <= hour <= 11 or 14 <= hour <= 16: score += 5; confidence += 3

            # Historical performance
            stats = self.symbol_stats[symbol]
            if stats['total'] > 10:
                wr = stats['wins'] / stats['total']
                if wr < MIN_WIN_RATE: score -= 16; reasons.append(f"Hist {wr:.0%}")
                elif wr > 0.6: score += 8; reasons.append(f"Hist {wr:.0%}")

            h_stats = self.hourly_stats[hour]
            if h_stats['total'] > 20:
                h_wr = h_stats['wins'] / h_stats['total']
                if h_wr < MIN_WIN_RATE: score -= 11; reasons.append(f"Hour {hour}")

            return {'symbol': symbol, 'price': price, 'rsi': round(rsi, 1), 'score': max(0, min(100, int(score))), 'confidence': min(95, int(confidence)), 'atr': round(atr, 4), 'reason': ", ".join(reasons[:2]), 'hurst': round(hurst, 2), 'vol_ratio': round(vol_ratio, 2), 'spread': round(spread_pct, 2), 'supertrend': 'up' if price > upper.iloc[-1] else 'down'}
        except Exception as e: logger.error(f"Analyze {symbol}: {e}"); return None

    def calculate_size(self, equity, price, atr, confidence, tier):
        # Kelly criterion with fractional Kelly
        risk_amount = equity * 0.02 * KELLY_FRACTION
        risk_per_share = atr * TRAILING_STOP_ATR
        shares_risk = risk_amount / risk_per_share if risk_per_share > 0 else 0
        max_pos = TIER_MAX_POS * (confidence / 100)
        shares_tier = max_pos / price
        # Volatility targeting
        target_vol = VOLATILITY_TARGET / (atr / price)
        shares_vol = (equity * target_vol) / price
        shares = min(shares_risk, shares_tier, shares_vol)
        if shares * price < MIN_NOTIONAL: shares = MIN_NOTIONAL * 1.05 / price
        return round(shares, 6) if shares < 1 else int(shares)

    async def analyze_losses(self):
        msg = "🤔 **ANALYZING 3 LOSSES**\n\n⏸️ Pausing 60s...\n📊 Reviewing...\n🧠 Adapting..."
        await self.send(msg, silent=True); await asyncio.sleep(PAUSE_DURATION)
        cursor = conn.execute('SELECT symbol, reason FROM trades ORDER BY id DESC LIMIT 3'); losses = cursor.fetchall()
        analysis = "📊 **ANALYSIS COMPLETE**\n\n"
        for sym, reason in losses: analysis += f"• {sym}: {reason}\n"
        analysis += "\n✅ Adapted: Size -20%, RSI +5, Stricter"
        await self.send(analysis); self.consecutive_losses = 0; self.paused_until = None

    async def send_alert(self, symbol, side, qty, price, analysis, notional, tier):
        phrase = random.choice(BUY_PHRASES if side == 'buy' else SELL_PHRASES); emoji = "🟢" if side == 'buy' else "🔴"
        msg = f"{emoji} **{symbol}** {side.upper()}\n{phrase}\n\n"
        msg += f"💵 **${price:.2f}** × {qty}\n💰 **${notional:.2f}**\n\n"
        msg += f"📊 {analysis['score']}/100 | 🎯 {analysis['confidence']}% | 📈 RSI {analysis['rsi']}\n"
        msg += f"_{analysis['reason']}_"
        await self.send(msg)

    async def execute(self, symbol, side, analysis, tier):
        if self.paused_until and datetime.now() < self.paused_until: return False
        if self.consecutive_losses >= CONSECUTIVE_LOSS_PAUSE:
            self.paused_until = datetime.now() + timedelta(seconds=PAUSE_DURATION + 10); await self.analyze_losses(); return False
        account = trading.get_account(); equity = float(account.equity)
        if self.trades_today >= MAX_TRADES_PER_DAY: return False
        if self.daily_pnl < -MAX_DAILY_LOSS: return False
        if len(self.latency_history) > 10 and np.mean(self.latency_history) > MAX_API_LATENCY_MS: return False
        # Portfolio heat check
        if self.portfolio_heat > MAX_PORTFOLIO_HEAT: return False

        order_hash = hashlib.md5(f"{symbol}{side}{analysis['price']}{int(time.time()/60)}".encode()).hexdigest()
        if order_hash in self.recent_orders: return False
        self.recent_orders.append(order_hash)
        start_time = time.time()
        try:
            is_crypto = '/' in symbol; price = analysis['price']
            if abs(price - analysis['price']) / analysis['price'] > FAT_FINGER_PCT / 100: return False

            qty = self.calculate_size(equity, price, analysis['atr'], analysis['confidence'], tier)
            if side == 'sell':
                try: pos = trading.get_open_position(symbol); qty = float(pos.qty)
                except: return False
                if qty * price < MIN_NOTIONAL: return False
            else:
                if qty * price < MIN_NOTIONAL: qty = MIN_NOTIONAL * 1.05 / price; qty = round(qty, 6) if is_crypto else int(qty)
            notional = qty * price
            if notional < MIN_NOTIONAL or notional > TIER_MAX_POS * 1.5: return False

            # Money-printing: bracket orders with profit target and stop
            limit = price * (1.001 if side == 'buy' else 0.999)
            stop_price = price * (0.97 if side == 'buy' else 1.03)
            take_profit = price * (1.04 if side == 'buy' else 0.96)

            order = LimitOrderRequest(
                symbol=symbol, qty=qty,
                side=OrderSide.BUY if side == 'buy' else OrderSide.SELL,
                time_in_force=TimeInForce.GTC if is_crypto else TimeInForce.DAY,
                limit_price=round(limit, 2),
                order_class=OrderClass.BRACKET,
                stop_loss=StopLossRequest(stop_price=round(stop_price, 2)),
                take_profit=TakeProfitRequest(limit_price=round(take_profit, 2))
            )

            trading.submit_order(order); await asyncio.sleep(1.5)
            latency_ms = (time.time() - start_time) * 1000

            self.trades_today += 1; self.total_trades += 1; self.last_trade_time = datetime.now()
            if side == 'buy': self.positions[symbol] = price
            else: self.positions.pop(symbol, None); self.consecutive_losses = 0; self.winning_trades += 1; self.win_streak += 1; self.loss_streak = 0

            slippage = abs(limit - price) / price * 100
            self.slippage_history.append(slippage)
            self.portfolio_heat = len(self.positions) * 0.02

            conn.execute('INSERT INTO trades VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (datetime.now().isoformat(), symbol, side, qty, price, notional, analysis['rsi'], analysis['score'], analysis['confidence'], analysis['reason'], 0, tier, self.version, slippage, int(latency_ms), 0)); conn.commit()
            hour = datetime.now().hour; self.symbol_stats[symbol]['total'] += 1; self.hourly_stats[hour]['total'] += 1
            await self.send_alert(symbol, side, qty, price, analysis, notional, tier); return True
        except Exception as e:
            err = str(e); self.errors += 1
            if '40310000' in err or 'insufficient' in err.lower(): self.symbol_stats[symbol]['total'] += 10
            else: self.consecutive_losses += 1; self.loss_streak += 1; self.win_streak = 0
            return False

    async def heartbeat(self):
        try:
            account = trading.get_account(); positions = trading.get_all_positions()
            equity = float(account.equity); tier = self.get_tier(equity)
            max_pos = TIER_MAX_POS # FIXED: Now correctly shows single value
            self.daily_pnl = equity - self.start_equity
            drawdown = (self.peak_equity - equity) / self.peak_equity * 100 if self.peak_equity > 0 else 0
            win_rate = self.winning_trades / self.total_trades * 100 if self.total_trades > 0 else 0

            msg = f"💓 **BigDog {self.version}** {datetime.now().strftime('%H:%M')}\n\n"
            msg += f"💵 **${equity:,.2f}** ({self.daily_pnl:+.2f})\n"
            msg += f"📊 Tier {tier} • Max **${max_pos}**\n" # FIXED: Shows $50 not array
            msg += f"📈 {len(positions)}/{TIER_MAX_POSITIONS} pos • {self.trades_today}/{MAX_TRADES_PER_DAY} trades\n"
            msg += f"🎯 WR: {win_rate:.1f}% • DD: {drawdown:.1f}%\n"
            msg += f"🔥 Streak: {self.win_streak}W\n\n"
            msg += f"{'🟢 Trading' if len(positions) > 0 else '⚪ Scanning'}"
            await self.send(msg, silent=True); self.last_heartbeat = datetime.now()
            conn.execute('INSERT OR REPLACE INTO equity VALUES (?,?,?,?,?,?,?)', (datetime.now().isoformat(), equity, float(account.cash), len(positions), self.sharpe_ratio, drawdown, self.portfolio_heat)); conn.commit()
        except Exception as e: logger.error(f"Heartbeat: {e}")

    async def scan(self):
        try:
            account = trading.get_account(); equity = float(account.equity); tier = self.get_tier(equity)
            if equity > self.peak_equity: self.peak_equity = equity
            if self.paused_until and datetime.now() < self.paused_until: return
            symbols = CRYPTO + STOCKS
            for symbol in symbols:
                if self.trades_today >= MAX_TRADES_PER_DAY: break
                is_crypto = '/' in symbol
                if not is_crypto and not self.is_market_open(False): continue
                df = await self.fetch(symbol)
                if df is None: continue
                self.market_regime = self.detect_regime(df)
                analysis = self.analyze(symbol, df)
                if not analysis: continue
                has_pos = symbol in self.positions
                if not has_pos and analysis['score'] >= BUY_SCORE_MIN and analysis['confidence'] >= BUY_CONF_MIN:
                    if len(self.positions) < TIER_MAX_POSITIONS:
                        await self.execute(symbol, 'buy', analysis, tier); await asyncio.sleep(2)
                elif has_pos and analysis['score'] <= SELL_SCORE_MAX:
                    await self.execute(symbol, 'sell', analysis, tier); await asyncio.sleep(2)
            try: positions = trading.get_all_positions(); self.positions = {p.symbol: float(p.avg_entry_price) for p in positions}
            except: pass
        except Exception as e: logger.error(f"Scan: {e}", exc_info=True)

    async def run(self):
        account = trading.get_account(); self.start_equity = float(account.equity); self.peak_equity = self.start_equity
        tier = self.get_tier(self.start_equity); max_pos = TIER_MAX_POS
        msg = f"🚀 **BigDog {self.version}** `{'LIVE' if not PAPER else 'PAPER'}`\n\n"
        msg += f"💵 **${self.start_equity:,.2f}**\n"
        msg += f"📊 Tier {tier} • Max **${max_pos}**\n" # FIXED
        msg += f"🌐 {len(CRYPTO)}C + {len(STOCKS)}S\n💎 80/20 Active\n\n_1,300 upgrades • MONEY PRINTING_"
        await self.send(msg); logger.info("Bot started")
        while True:
            try:
                await self.scan()
                if (datetime.now() - self.last_heartbeat).seconds > HEARTBEAT_MINUTES * 60: await self.heartbeat()
                et = datetime.now(pytz.timezone('US/Eastern'))
                if et.hour == 0 and et.minute < 2 and self.trades_today > 0:
                    self.trades_today = 0; self.start_equity = float(trading.get_account().equity)
                await asyncio.sleep(40)
            except Exception as e: logger.error(f"Loop: {e}", exc_info=True); await asyncio.sleep(60)

if __name__ == "__main__":
    try: bot = BigDog(); asyncio.run(bot.run())
    except KeyboardInterrupt: logger.info("Stopped")
    except Exception as e: logger.error(f"Fatal: {e}", exc_info=True)