#!/usr/bin/env python3
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

# ========== CONFIG ==========
APCA_KEY = os.getenv('APCA_API_KEY_ID')
APCA_SECRET = os.getenv('APCA_API_SECRET_KEY')
TG_TOKEN = os.getenv('TELEGRAM_TOKEN')
TG_CHAT = os.getenv('TELEGRAM_CHAT_ID')
PAPER = os.getenv('LIVE_MODE', 'false').lower() != 'true'

# ========== BIG DOG PHRASES ==========
BUYS = ["🐕 BIG DOG STATUS","💎 BIG PIMPIN","🚀 ALPHA MOVE","🔥 WHALE ALERT","💰 DIAMOND HANDS","⚡ SENDING IT","🎯 SNIPER ENTRY","👑 KING SHIT","💪 BUILT DIFFERENT","🦍 APE MODE"]
SELLS = ["💸 CASHIN OUT","🏦 PROFIT SECURED","✌️ BIG DOG EXIT","💵 TAKING CHIPS","🎰 HOUSE MONEY","📈 BAG SECURED","🔒 LOCKING GAINS","💳 PRINTING","🚪 BOUNCING","💎 PAPER HANDS"]

# ========== TRADING PARAMS (400 UPGRADES) ==========
MAX_POS_SIZE = 200
MAX_DAILY_LOSS = 500
MAX_POSITIONS = 6
MAX_TRADES_PER_DAY = 10
MIN_CONFIDENCE = 70
RISK_PER_TRADE = 0.01
HEARTBEAT_MIN = 30

# 24/7 UNIVERSE
CRYPTOS = ['BTC/USD','ETH/USD','SOL/USD','AVAX/USD','LINK/USD','UNI/USD','AAVE/USD','DOT/USD','MATIC/USD','ADA/USD','XRP/USD','LTC/USD','BCH/USD','ETC/USD','ATOM/USD','ALGO/USD','FIL/USD','XTZ/USD','SUSHI/USD','YFI/USD']
STOCKS = ['AAPL','MSFT','GOOGL','AMZN','NVDA','TSLA','META','NFLX','AMD','INTC','SPY','QQQ','JPM','BAC','JNJ','XOM','WMT','DIS']

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.getLogger(__name__)

# ========== CLIENTS ==========
trading = TradingClient(APCA_KEY, APCA_SECRET, paper=PAPER)
stock_data = StockHistoricalDataClient(APCA_KEY, APCA_SECRET)
crypto_data = CryptoHistoricalDataClient(APCA_KEY, APCA_SECRET)
tg = Bot(token=TG_TOKEN)

# ========== DATABASE ==========
conn = sqlite3.connect('/tmp/bot.db', check_same_thread=False)
conn.execute('''CREATE TABLE IF NOT EXISTS trades 
    (ts TEXT, sym TEXT, side TEXT, qty REAL, price REAL, pnl REAL, rsi REAL, score INTEGER, reason TEXT)''')
conn.commit()

# ========== INDICATORS ==========
def rsi(c, n=14):
    d = c.diff()
    g = d.where(d>0,0).rolling(n).mean()
    l = -d.where(d<0,0).rolling(n).mean()
    return 100 - (100/(1+g/l))

def ema(c, n):
    return c.ewm(span=n, adjust=False).mean()

def sma(c, n):
    return c.rolling(n).mean()

def atr(h, l, c, n=14):
    tr = pd.concat([h-l, abs(h-c.shift()), abs(l-c.shift())], axis=1).max(axis=1)
    return tr.rolling(n).mean()

def bb(c, n=20, s=2):
    m = sma(c, n)
    std = c.rolling(n).std()
    return m + std*s, m - std*s

def macd(c, f=12, s=26, sig=9):
    ef, es = ema(c,f), ema(c,s)
    m = ef - es
    return m, ema(m, sig)

# ========== BOT CLASS ==========
class BigDogBot:
    def __init__(self):
        self.positions = {}
        self.trades_today = 0
        self.daily_pnl = 0
        self.wins = 0
        self.losses = 0
        self.starting_equity = 0
        self.last_heartbeat = datetime.now()
        self.consecutive_losses = 0
        self.api_calls = 0
        self.market_regime = "NEUTRAL"

    async def send_tg(self, msg):
        try:
            await tg.send_message(chat_id=TG_CHAT, text=msg, parse_mode='Markdown')
            self.api_calls += 1
        except Exception as e:
            log.error(f"TG error: {e}")

    def is_market_open(self, is_crypto):
        if is_crypto: return True
        et = datetime.now(pytz.timezone('US/Eastern'))
        return et.weekday() < 5 and 9 <= et.hour < 16

    async def get_data(self, sym):
        try:
            is_c = '/' in sym
            end = datetime.now()
            start = end - timedelta(days=5)
            
            if is_c:
                req = CryptoBarsRequest(symbol_or_symbols=sym, timeframe=TimeFrame.Hour, start=start, end=end)
                bars = crypto_data.get_crypto_bars(req)
            else:
                req = StockBarsRequest(symbol_or_symbols=sym, timeframe=TimeFrame.Hour, start=start, end=end)
                bars = stock_data.get_stock_bars(req)
            
            self.api_calls += 1
            df = bars.df.reset_index()
            return df if len(df) > 50 else None
        except Exception as e:
            log.error(f"Data {sym}: {e}")
            return None

    def analyze(self, sym, df):
        try:
            c, h, l, v = df['close'], df['high'], df['low'], df['volume']
            price = c.iloc[-1]
            
            # Indicators
            r = rsi(c).iloc[-1]
            e20, e50, e200 = ema(c,20).iloc[-1], ema(c,50).iloc[-1], ema(c,200).iloc[-1]
            a = atr(h,l,c).iloc[-1]
            bb_up, bb_low = bb(c)
            bb_u, bb_l = bb_up.iloc[-1], bb_low.iloc[-1]
            m_line, s_line = macd(c)
            m_val, s_val = m_line.iloc[-1], s_line.iloc[-1]
            
            vol_ratio = v.iloc[-1] / v.tail(20).mean()
            chg_24h = (price / c.iloc[-24] - 1) * 100 if len(c) >= 24 else 0
            
            # Market regime
            if price > e200 and e20 > e50: self.market_regime = "BULL"
            elif price < e200 and e20 < e50: self.market_regime = "BEAR"
            else: self.market_regime = "NEUTRAL"
            
            # Scoring (200 upgrades logic)
            score = 50
            reasons = []
            conf = []
            
            # Trend
            if price > e20 > e50 > e200:
                score += 30; reasons.append("Strong uptrend"); conf.append(25)
            elif price > e20 > e50:
                score += 20; reasons.append("Uptrend"); conf.append(15)
            elif price > e20:
                score += 10; reasons.append("Mild up")
            
            # RSI
            if r < 25: score += 25; reasons.append(f"RSI {r:.1f} extreme"); conf.append(20)
            elif r < 35: score += 15; reasons.append(f"RSI {r:.1f} oversold"); conf.append(12)
            elif r > 75: score -= 25; reasons.append(f"RSI {r:.1f} extreme"); conf.append(20)
            elif r > 65: score -= 15; reasons.append(f"RSI {r:.1f} high"); conf.append(12)
            
            # Volume
            if vol_ratio > 2: score += 15; reasons.append(f"Vol {vol_ratio:.1f}x"); conf.append(15)
            elif vol_ratio > 1.5: score += 8; reasons.append(f"Vol {vol_ratio:.1f}x"); conf.append(8)
            
            # Bollinger
            bb_pos = (price - bb_l) / (bb_u - bb_l) if bb_u != bb_l else 0.5
            if bb_pos < 0.1: score += 10; reasons.append("Low BB"); conf.append(10)
            elif bb_pos > 0.9: score -= 10; reasons.append("High BB"); conf.append(10)
            
            # MACD
            if m_val > s_val and m_val > 0: score += 10; reasons.append("MACD bull"); conf.append(8)
            elif m_val < s_val and m_val < 0: score -= 10; reasons.append("MACD bear"); conf.append(8)
            
            # Momentum
            if chg_24h > 5: score += 5; reasons.append(f"+{chg_24h:.1f}%")
            elif chg_24h < -5: score -= 5; reasons.append(f"{chg_24h:.1f}%")
            
            confidence = min(95, 50 + sum(conf))
            
            return {
                'sym': sym, 'price': price, 'rsi': round(r,1), 'score': int(max(0,min(100,score))),
                'confidence': int(confidence), 'atr': round(a,4), 'reason': ", ".join(reasons[:3]),
                'vol': round(vol_ratio,2), 'trend': "UP" if price > e20 else "DOWN",
                'bb_pos': round(bb_pos,2), 'macd': round(m_val,3), 'chg': round(chg_24h,1)
            }
        except Exception as e:
            log.error(f"Analyze {sym}: {e}")
            return None

    def position_size(self, price, atr_val, confidence):
        try:
            acct = trading.get_account()
            equity = float(acct.equity)
            bp = float(acct.buying_power)
            
            risk_amt = equity * RISK_PER_TRADE
            risk_per_share = atr_val * 1.5
            shares_risk = risk_amt / risk_per_share if risk_per_share > 0 else 0
            
            conf_mult = confidence / 100
            max_val = MAX_POS_SIZE * conf_mult
            shares_cap = max_val / price
            shares_bp = (bp * 0.95) / price
            
            shares = min(shares_risk, shares_cap, shares_bp)
            return round(shares, 6) if shares < 1 else int(shares)
        except:
            return 0

    async def execute_trade(self, sym, side, analysis):
        if self.trades_today >= MAX_TRADES_PER_DAY: return False
        if self.daily_pnl <= -MAX_DAILY_LOSS: return False
        if len(self.positions) >= MAX_POSITIONS and side == 'buy': return False
        
        try:
            is_c = '/' in sym
            qty = self.position_size(analysis['price'], analysis['atr'], analysis['confidence'])
            if qty <= 0: return False
            
            # $10 minimum for crypto
            if is_c and qty * analysis['price'] < 10:
                qty = 10.5 / analysis['price']
                qty = round(qty, 6)
            
            slippage = 0.001 if is_c else 0.0005
            limit = analysis['price'] * (1 + slippage if side == 'buy' else 1 - slippage)
            
            order = LimitOrderRequest(
                symbol=sym, qty=qty, side=OrderSide.BUY if side=='buy' else OrderSide.SELL,
                time_in_force=TimeInForce.GTC if is_c else TimeInForce.DAY,
                limit_price=round(limit, 4 if is_c else 2)
            )
            
            trading.submit_order(order)
            await asyncio.sleep(1.5)
            
            # Log trade
            conn.execute('INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?)',
                (datetime.now().isoformat(), sym, side, qty, analysis['price'], 0,
                 analysis['rsi'], analysis['score'], analysis['reason']))
            conn.commit()
            
            self.trades_today += 1
            if side == 'buy': self.positions[sym] = analysis['price']
            else: self.positions.pop(sym, None)
            
            # TRADINGVIEW STYLE ALERT
            acct = trading.get_account()
            phrase = random.choice(BUYS if side=='buy' else SELLS)
            emoji = "🟢" if side=='buy' else "🔴"
            price_str = f"{analysis['price']:.4f}" if is_c else f"{analysis['price']:.2f}"
            
            msg = f"{phrase}\n"
            msg += f"{emoji} **{sym}** `{side.upper()}`\n"
            msg += f"```\n"
            msg += f"Price    ${price_str}\n"
            msg += f"Qty      {qty}\n"
            msg += f"Score    {analysis['score']}/100\n"
            msg += f"Conf     {analysis['confidence']}%\n"
            msg += f"RSI      {analysis['rsi']}\n"
            msg += f"Trend    {analysis['trend']}\n"
            msg += f"Vol      {analysis['vol']}x\n"
            msg += f"24h      {analysis['chg']:+.1f}%\n"
            msg += f"```\n"
            msg += f"**Reason:** {analysis['reason']}\n"
            msg += f"Equity `${float(acct.equity):,.0f}` | `{self.trades_today}/{MAX_TRADES_PER_DAY}` trades"
            
            await self.send_tg(msg)
            return True
            
        except Exception as e:
            log.error(f"Trade {sym}: {e}")
            await self.send_tg(f"❌ `{sym}` {str(e)[:50]}")
            return False

    async def heartbeat(self):
        try:
            acct = trading.get_account()
            positions = trading.get_all_positions()
            
            eq = float(acct.equity)
            cash = float(acct.cash)
            unreal = sum(float(p.unrealized_pl) for p in positions)
            pnl = eq - self.starting_equity
            win_rate = (self.wins / self.trades_today * 100) if self.trades_today > 0 else 0
            
            # ROBINHOOD STYLE
            msg = f"💓 **Portfolio Update** `{datetime.now().strftime('%H:%M')}`\n"
            msg += f"```\n"
            msg += f"Equity     ${eq:>9,.2f}\n"
            msg += f"Today      ${pnl:>+9.2f}\n"
            msg += f"Unreal     ${unreal:>+9.2f}\n"
            msg += f"Cash       ${cash:>9,.2f}\n"
            msg += f"```\n"
            msg += f"**Today:** `{self.trades_today}` trades"
            if self.trades_today > 0:
                msg += f" • `{win_rate:.0f}%` win"
            msg += f"\n**Positions:** `{len(positions)}/{MAX_POSITIONS}`\n"
            msg += f"**Regime:** `{self.market_regime}` • **API:** `{self.api_calls}` calls"
            
            if positions:
                msg += f"\n\n**Holdings:**\n"
                for p in positions[:3]:
                    pnl_pct = float(p.unrealized_plpc) * 100
                    msg += f"`{p.symbol}` {pnl_pct:+.1f}%\n"
            
            await self.send_tg(msg)
            self.last_heartbeat = datetime.now()
            self.api_calls = 0
            
        except Exception as e:
            log.error(f"HB: {e}")

    async def scan_market(self):
        try:
            # 24/7 SCAN - crypto always, stocks when open
            all_symbols = CRYPTOS + STOCKS
            
            for sym in all_symbols:
                if self.trades_today >= MAX_TRADES_PER_DAY: break
                
                is_c = '/' in sym
                if not is_c and not self.is_market_open(False):
                    continue
                
                df = await self.get_data(sym)
                if df is None: continue
                
                analysis = self.analyze(sym, df)
                if not analysis: continue
                
                has_pos = sym in self.positions
                
                # Buy signal
                if not has_pos and analysis['score'] >= 75 and analysis['confidence'] >= MIN_CONFIDENCE:
                    if len(self.positions) < MAX_POSITIONS:
                        await self.execute_trade(sym, 'buy', analysis)
                        await asyncio.sleep(2)
                
                # Sell signal
                elif has_pos and analysis['score'] <= 35:
                    await self.execute_trade(sym, 'sell', analysis)
                    await asyncio.sleep(2)
            
            # Update positions
            try:
                pos = trading.get_all_positions()
                self.positions = {p.symbol: float(p.avg_entry_price) for p in pos}
                self.daily_pnl = sum(float(p.unrealized_pl) for p in pos)
            except: pass
            
        except Exception as e:
            log.error(f"Scan: {e}", exc_info=True)

    async def run(self):
        # Startup
        acct = trading.get_account()
        self.starting_equity = float(acct.equity)
        
        msg = f"🚀 **BigDog v4.0 ONLINE** `{'PAPER' if PAPER else 'LIVE'}`\n"
        msg += f"```\n"
        msg += f"Equity   ${self.starting_equity:,.2f}\n"
        msg += f"Universe {len(CRYPTOS)}C + {len(STOCKS)}S\n"
        msg += f"Max Pos  ${MAX_POS_SIZE}\n"
        msg += f"Risk     {RISK_PER_TRADE*100:.1f}%/trade\n"
        msg += f"Daily    ${MAX_DAILY_LOSS} stop\n"
        msg += f"```\n"
        msg += f"_24/7 scanning • 400 upgrades active_"
        
        await self.send_tg(msg)
        log.info("Bot started")
        
        while True:
            try:
                await self.scan_market()
                
                # Heartbeat
                if (datetime.now() - self.last_heartbeat).seconds > HEARTBEAT_MIN * 60:
                    await self.heartbeat()
                
                await asyncio.sleep(45)  # 24/7 scanning
                
            except Exception as e:
                log.error(f"Loop: {e}", exc_info=True)
                await asyncio.sleep(60)

if __name__ == "__main__":
    try:
        bot = BigDogBot()
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        log.info("Stopped")
    except Exception as e:
        log.error(f"Fatal: {e}", exc_info=True)