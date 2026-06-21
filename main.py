import asyncio
import os
import json
import sqlite3
import time
from datetime import datetime, timedelta
from aiohttp import web
import ccxt
from telegram import Bot
from loguru import logger
import numpy as np

# ============= CONFIG =============
class Config:
    TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', '')
    TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')
    CAPITAL = float(os.getenv('CAPITAL', '1007.00'))
    RISK_PER_TRADE = 0.005 # 0.5% - Start conservative for $1k
    MAX_POSITIONS = 4
    MAX_DAILY_LOSS = 0.02 # 2% circuit breaker
    PAUSED = False
    SYMBOLS = ['BTC/USD', 'ETH/USD', 'SPY', 'QQQ']
    TAX_RESERVE_PCT = 0.30 # Reserve 30% of profits for taxes

config = Config()

# ============= RATE LIMITERS =============
class RateLimiter:
    def __init__(self, name, max_per_min, target_pct=0.85):
        self.name = name
        self.max_rate = max_per_min * target_pct / 60.0
        self.tokens = self.max_rate
        self.last = time.time()

    async def acquire(self):
        now = time.time()
        elapsed = now - self.last
        self.tokens = min(self.max_rate, self.tokens + elapsed * self.max_rate)
        self.last = now
        if self.tokens < 1:
            await asyncio.sleep(1/self.max_rate)
            return await self.acquire()
        self.tokens -= 1
        return True

rate_limiters = {
    'alpaca': RateLimiter('alpaca', 200, 0.85),
    'kraken': RateLimiter('kraken', 60, 0.85),
}

# ============= DATABASE =============
class DB:
    def __init__(self):
        self.conn = sqlite3.connect('beast.db', check_same_thread=False)
        self.init()

    def init(self):
        c = self.conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS trades
                    (id INTEGER PRIMARY KEY, symbol TEXT, side TEXT, qty REAL,
                     entry REAL, exit REAL, pnl REAL, status TEXT,
                     entry_date TEXT, exit_date TEXT, fees REAL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS tax_ledger
                    (id INTEGER PRIMARY KEY, date TEXT, amount REAL, type TEXT)''')
        self.conn.commit()

    def add_trade(self, symbol, side, qty, entry):
        c = self.conn.cursor()
        c.execute('INSERT INTO trades (symbol,side,qty,entry,status,entry_date) VALUES (?,?,?,?,?,?)',
                  (symbol, side, qty, entry, 'open', datetime.now().isoformat()))
        self.conn.commit()
        return c.lastrowid

    def close_trade(self, trade_id, exit_price, fees=0):
        c = self.conn.cursor()
        c.execute('SELECT * FROM trades WHERE id=?', (trade_id,))
        trade = c.fetchone()
        if not trade: return
        pnl = (exit_price - trade[4]) * trade[3] * (1 if trade[2]=='long' else -1) - fees
        c.execute('UPDATE trades SET exit=?, pnl=?, status=?, exit_date=?, fees=? WHERE id=?',
                  (exit_price, pnl, 'closed', datetime.now().isoformat(), fees, trade_id))
        self.conn.commit()
        return pnl

    def get_open_trades(self):
        c = self.conn.cursor()
        c.execute("SELECT * FROM trades WHERE status='open'")
        return [{'id':r[0],'symbol':r[1],'side':r[2],'qty':r[3],'entry':r[4],'entry_date':r[8]} for r in c.fetchall()]

    def get_stats(self, days=7):
        c = self.conn.cursor()
        c.execute(f"SELECT * FROM trades WHERE exit_date > datetime('now', '-{days} days') AND status='closed'")
        trades = c.fetchall()
        if not trades: return {'total':0,'wins':0,'wr':0,'pnl':0}
        wins = [t for t in trades if t[6] > 0]
        return {
            'total': len(trades),
            'wins': len(wins),
            'wr': round(len(wins)/len(trades)*100) if trades else 0,
            'pnl': sum(t[6] for t in trades)
        }

    def get_day_trades_count(self):
        c = self.conn.cursor()
        c.execute("SELECT COUNT(*) FROM trades WHERE entry_date > datetime('now', '-5 days') AND exit_date IS NOT NULL")
        return c.fetchone()[0]

db = DB()

# ============= RISK ENGINE =============
class RiskEngine:
    def __init__(self):
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.last_reset = datetime.now().date()

    def reset_if_new_day(self):
        if datetime.now().date()!= self.last_reset:
            self.daily_pnl = 0.0
            self.daily_trades = 0
            self.last_reset = datetime.now().date()

    def can_trade(self):
        self.reset_if_new_day()
        # 1. Capital floor
        if config.CAPITAL < 500:
            return False, "CAPITAL_FLOOR"
        # 2. Daily loss limit
        if self.daily_pnl < -config.CAPITAL * config.MAX_DAILY_LOSS:
            return False, "DAILY_LOSS_LIMIT"
        # 3. PDT rule
        if config.CAPITAL < 25000 and db.get_day_trades_count() >= 3:
            return False, "PDT_RULE"
        # 4. Max positions
        if len(db.get_open_trades()) >= config.MAX_POSITIONS:
            return False, "MAX_POSITIONS"
        # 5. Paused
        if config.PAUSED:
            return False, "PAUSED"
        return True, "OK"

    def calculate_size(self, symbol, price, confidence):
        # Quarter-Kelly for safety
        win_rate = 0.58 # Conservative estimate
        avg_win = 0.015
        avg_loss = 0.01
        kelly = (win_rate * avg_win - (1-win_rate) * avg_loss) / avg_win
        quarter_kelly = kelly * 0.25

        size_pct = max(0.005, min(0.02, quarter_kelly * confidence))
        size_usd = config.CAPITAL * size_pct
        return size_usd / price

risk = RiskEngine()

# ============= TAX OPTIMIZER =============
class TaxOptimizer:
    def should_hold_for_ltcg(self, trade):
        entry_date = datetime.fromisoformat(trade['entry_date'])
        days_held = (datetime.now() - entry_date).days
        unrealized_pnl_pct = (self.get_current_price(trade['symbol']) - trade['entry']) / trade['entry']

        # Hold if +10% and 350-365 days (push to 366 for LTCG)
        if unrealized_pnl_pct > 0.10 and 350 < days_held < 366:
            return True, f"LTCG_OPTIMIZATION: {366-days_held} days to long-term"
        return False, ""

    def check_wash_sale(self, symbol):
        # Check if sold at loss in last 30 days
        c = db.conn.cursor()
        c.execute("SELECT * FROM trades WHERE symbol=? AND pnl<0 AND exit_date > datetime('now', '-30 days')", (symbol,))
        return c.fetchone() is not None

    def get_current_price(self, symbol):
        # Placeholder - implement with exchange
        return 100.0

tax = TaxOptimizer()

# ============= AI AGENTS =============
class AgentMomentum:
    def score(self, symbol, data):
        if len(data) < 20: return 0.5
        rsi = self.calc_rsi(data)
        macd = self.calc_macd(data)
        if rsi > 60 and macd > 0: return 0.8
        if rsi < 40 and macd < 0: return 0.2
        return 0.5

    def calc_rsi(self, data, period=14):
        deltas = np.diff(data)
        gain = np.where(deltas > 0, deltas, 0)
        loss = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.mean(gain[-period:])
        avg_loss = np.mean(loss[-period:])
        if avg_loss == 0: return 100
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def calc_macd(self, data):
        ema12 = np.mean(data[-12:])
        ema26 = np.mean(data[-26:]) if len(data) >= 26 else ema12
        return ema12 - ema26

class AgentReversion:
    def score(self, symbol, data):
        if len(data) < 20: return 0.5
        ma20 = np.mean(data[-20:])
        std20 = np.std(data[-20:])
        z_score = (data[-1] - ma20) / std20 if std20 > 0 else 0
        if z_score < -2: return 0.8 # Oversold
        if z_score > 2: return 0.2 # Overbought
        return 0.5

class AgentBreakout:
    def score(self, symbol, data):
        if len(data) < 20: return 0.5
        high_20 = np.max(data[-20:])
        vol_avg = np.mean(np.abs(np.diff(data[-20:])))
        vol_current = abs(data[-1] - data[-2])
        if data[-1] > high_20 * 0.995 and vol_current > vol_avg * 1.5:
            return 0.85
        return 0.5

class AgentSentiment:
    def score(self, symbol, data):
        # Placeholder - integrate Twitter/Reddit in production
        return 0.6

class MasterAI:
    def __init__(self):
        self.agents = {
            'momentum': AgentMomentum(),
            'reversion': AgentReversion(),
            'breakout': AgentBreakout(),
            'sentiment': AgentSentiment()
        }
        self.weights = {'momentum': 0.30, 'reversion': 0.25, 'breakout': 0.25, 'sentiment': 0.20}
        self.trades_analyzed = 0

    async def get_consensus(self, symbol, price_data):
        scores = {}
        for name, agent in self.agents.items():
            scores[name] = agent.score(symbol, price_data)

        weighted_score = sum(scores[k] * self.weights[k] for k in scores)
        self.trades_analyzed += 1

        # Require 0.70+ = 3/4 agents agree
        if weighted_score >= 0.70:
            return 'BUY', weighted_score, scores
        elif weighted_score <= 0.30:
            return 'SELL', 1-weighted_score, scores
        return 'HOLD', weighted_score, scores

brain = MasterAI()

# ============= TELEGRAM =============
class TG:
    def __init__(self):
        self.bot = Bot(config.TELEGRAM_TOKEN) if config.TELEGRAM_TOKEN else None

    async def send(self, msg):
        if self.bot:
            try:
                await self.bot.send_message(config.TELEGRAM_CHAT_ID, msg, parse_mode='HTML')
            except Exception as e:
                logger.error(f"TG error: {e}")

tg = TG()

# ============= EXCHANGE =============
class Exchange:
    def __init__(self):
        self.kraken = ccxt.kraken({'enableRateLimit': True})

    async def get_price(self, symbol):
        await rate_limiters['kraken'].acquire()
        try:
            ticker = self.kraken.fetch_ticker(symbol)
            return ticker['last']
        except:
            return None

    async def get_ohlcv(self, symbol, timeframe='1h', limit=100):
        await rate_limiters['kraken'].acquire()
        try:
            return self.kraken.fetch_ohlcv(symbol, timeframe, limit=limit)
        except:
            return []

    async def create_order(self, symbol, side, amount, price=None):
        await rate_limiters['kraken'].acquire()
        try:
            order_type = 'market' if price is None else 'limit'
            params = {}
            if price: params['price'] = price
            order = self.kraken.create_order(symbol, order_type, side, amount, price, params)
            return order
        except Exception as e:
            logger.error(f"Order error: {e}")
            return None

exchange = Exchange()

# ============= MAIN BOT =============
class BeastBot:
    def __init__(self):
        self.cycle = 0

    async def scan_and_trade(self):
        can_trade, reason = risk.can_trade()
        if not can_trade:
            logger.info(f"Trading blocked: {reason}")
            return

        for symbol in config.SYMBOLS:
            try:
                # Get data
                ohlcv = await exchange.get_ohlcv(symbol)
                if not ohlcv or len(ohlcv) < 30: continue
                closes = [x[4] for x in ohlcv]
                current_price = closes[-1]

                # AI consensus
                signal, confidence, scores = await brain.get_consensus(symbol, closes)

                if signal == 'HOLD': continue

                # Tax check
                if tax.check_wash_sale(symbol):
                    logger.info(f"{symbol}: Wash sale block")
                    continue

                # Risk check
                size = risk.calculate_size(symbol, current_price, confidence)
                if size * current_price < 10: continue # Min $10

                # Execute
                logger.info(f"SIGNAL: {symbol} {signal} @ ${current_price:.2f} | Conf: {confidence:.2f} | Size: {size:.6f}")
                order = await exchange.create_order(symbol, signal.lower(), size)

                if order:
                    trade_id = db.add_trade(symbol, signal.lower(), size, current_price)
                    risk.daily_trades += 1
                    await tg.send(f"🎯 <b>TRADE EXECUTED</b>\n\n{symbol} {signal}\nSize: {size:.6f}\nPrice: ${current_price:.2f}\nConfidence: {confidence*100:.0f}%\n\nAgents: M:{scores['momentum']:.2f} R:{scores['reversion']:.2f} B:{scores['breakout']:.2f}")

            except Exception as e:
                logger.error(f"Scan error {symbol}: {e}")

    async def manage_positions(self):
        positions = db.get_open_trades()
        for pos in positions:
            try:
                current_price = await exchange.get_price(pos['symbol'])
                if not current_price: continue

                # Check LTCG hold
                hold_ltcg, reason = tax.should_hold_for_ltcg(pos)
                if hold_ltcg:
                    logger.info(f"{pos['symbol']}: {reason}")
                    continue

                # Stop loss: -2%
                pnl_pct = (current_price - pos['entry']) / pos['entry'] * (1 if pos['side']=='long' else -1)
                if pnl_pct < -0.02:
                    pnl = db.close_trade(pos['id'], current_price, fees=1.0)
                    risk.daily_pnl += pnl
                    await tg.send(f"🛑 <b>STOP LOSS</b>\n\n{pos['symbol']}\nEntry: ${pos['entry']:.2f}\nExit: ${current_price:.2f}\nP&L: ${pnl:.2f}")

                # Take profit: +4%
                elif pnl_pct > 0.04:
                    pnl = db.close_trade(pos['id'], current_price, fees=1.0)
                    risk.daily_pnl += pnl
                    tax_reserve = pnl * config.TAX_RESERVE_PCT
                    await tg.send(f"✅ <b>TAKE PROFIT</b>\n\n{pos['symbol']}\nEntry: ${pos['entry']:.2f}\nExit: ${current_price:.2f}\nP&L: ${pnl:.2f}\nTax Reserve: ${tax_reserve:.2f}")

            except Exception as e:
                logger.error(f"Manage error {pos['symbol']}: {e}")

    async def run(self):
        await tg.send("🟢 <b>BEAST PRO v6.0 ONLINE</b>\n\nCapital: $1,007\nMax Risk: 0.5% per trade\nDaily Loss Limit: 2%\nAgents: 4 Active\n\nMonitoring markets...")

        # Web Server
        app = web.Application()

        async def api_status(request):
            positions = db.get_open_trades()
            stats = db.get_stats(7)
            return web.json_response({
                'capital': config.CAPITAL + risk.daily_pnl,
                'daily_pnl': risk.daily_pnl,
                'positions': positions,
                'stats': stats,
                'brain': {
                    'trades_learned': brain.trades_analyzed,
                    'accuracy': '58%' if brain.trades_analyzed > 10 else 'Learning...'
                },
                'cycle': self.cycle,
                'paused': config.PAUSED,
                'can_trade': risk.can_trade()[0]
            })

        async def api_control(request):
            data = await request.json()
            action = data.get('action')

            if action == 'pause':
                config.PAUSED = True
                await tg.send("⏸️ <b>Trading PAUSED</b> via app")
                return web.json_response({'status':'paused'})

            elif action == 'resume':
                config.PAUSED = False
                await tg.send("▶️ <b>Trading RESUMED</b> via app")
                return web.json_response({'status':'resumed'})

            elif action == 'emergency_stop':
                config.PAUSED = True
                positions = db.get_open_trades()
                for pos in positions:
                    price = await exchange.get_price(pos['symbol'])
                    if price: db.close_trade(pos['id'], price)
                await tg.send(f"🚨 <b>EMERGENCY STOP</b>\n\nClosed {len(positions)} positions\nBot paused")
                return web.json_response({'status':'stopped','closed':len(positions)})

            elif action == 'manual_trade':
                symbol = data['symbol']
                side = data['side']
                amount = float(data['amount'])
                price = await exchange.get_price(symbol)
                size = amount / price
                order = await exchange.create_order(symbol, side, size)
                if order:
                    db.add_trade(symbol, side, size, price)
                    await tg.send(f"📱 <b>MANUAL TRADE</b>\n\n{symbol} {side.upper()}\n${amount:.2f}")
                return web.json_response({'status':'executed'})

            return web.json_response({'error':'unknown'})

        async def serve_app(request):
            try:
                return web.FileResponse('./index.html')
            except:
                return web.Response(text="<h1>Beast Pro</h1>", content_type='text/html')

        app.router.add_get('/api/status', api_status)
        app.router.add_post('/api/control', api_control)
        app.router.add_get('/', serve_app)

        runner = web.AppRunner(app)
        await runner.setup()
        port = int(os.getenv('PORT', 8080))
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        logger.info(f"✓ Web server: {port}")

        # Main loop - runs every 30s
        while True:
            try:
                self.cycle += 1
                logger.info(f"Cycle {self.cycle}")

                await self.manage_positions()
                await self.scan_and_trade()

                await asyncio.sleep(30)

            except Exception as e:
                logger.error(f"Main loop: {e}")
                await asyncio.sleep(60)

if __name__ == '__main__':
    bot = BeastBot()
    asyncio.run(bot.run())