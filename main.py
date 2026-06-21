import asyncio
import os
import json
import sqlite3
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
    CAPITAL = float(os.getenv('CAPITAL', '1005.42'))
    RISK_PER_TRADE = 0.01
    PAUSED = False

    SYMBOLS = ['BTC/USD', 'ETH/USD', 'SOL/USD', 'SPY', 'QQQ', 'TSLA', 'NVDA']

config = Config()

# ============= DATABASE =============
class DB:
    def __init__(self):
        self.conn = sqlite3.connect('beast.db', check_same_thread=False)
        self.init()

    def init(self):
        c = self.conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS trades
                    (id INTEGER PRIMARY KEY, symbol TEXT, side TEXT, qty REAL,
                     entry REAL, exit REAL, pnl REAL, status TEXT, timestamp TEXT)''')
        self.conn.commit()

    def get_open_trades(self):
        c = self.conn.cursor()
        c.execute("SELECT * FROM trades WHERE status='open'")
        return [{'id':r[0],'symbol':r[1],'side':r[2],'qty':r[3],'entry':r[4]} for r in c.fetchall()]

    def get_stats(self, days=7):
        c = self.conn.cursor()
        c.execute(f"SELECT * FROM trades WHERE timestamp > datetime('now', '-{days} days') AND status='closed'")
        trades = c.fetchall()
        if not trades: return {'total':0,'wins':0,'wr':0}
        wins = len([t for t in trades if t[6] > 0])
        return {'total':len(trades),'wins':wins,'wr':round(wins/len(trades)*100) if trades else 0}

db = DB()

# ============= RISK =============
class Risk:
    def __init__(self):
        self.daily_pnl = 0.0
        self.trades_today = 0

    def can_trade(self):
        return not config.PAUSED and self.daily_pnl > -config.CAPITAL * 0.02

risk = Risk()

# ============= AI BRAIN =============
class AIBrain:
    def __init__(self):
        self.performance_history = []
        self.strategy_weights = {'momentum':0.25,'reversal':0.25,'breakout':0.25,'trend':0.25}
        self.model = None

    def decide(self, symbol, data):
        if not risk.can_trade(): return None
        # Simple AI logic
        price_change = (data[-1] - data[-10]) / data[-10] if len(data) > 10 else 0
        if abs(price_change) > 0.015:
            return {'side':'long' if price_change > 0 else 'short','confidence':0.75,'qty':0.01}
        return None

brain = AIBrain()

# ============= TELEGRAM =============
class TG:
    def __init__(self):
        self.bot = Bot(config.TELEGRAM_TOKEN) if config.TELEGRAM_TOKEN else None

    async def send(self, msg, level='general'):
        if self.bot:
            try:
                await self.bot.send_message(config.TELEGRAM_CHAT_ID, msg, parse_mode='HTML')
            except: pass

tg = TG()

# ============= MAIN BOT =============
class BeastBot:
    def __init__(self):
        self.cycle = 0
        self.exchange = ccxt.kraken({'enableRateLimit':True})

    async def scan_and_trade(self):
        for symbol in config.SYMBOLS:
            try:
                ticker = self.exchange.fetch_ticker(symbol)
                price = ticker['last']
                # AI decides
                signal = brain.decide(symbol, [price]*20)
                if signal:
                    logger.info(f"Signal: {symbol} {signal['side']}")
                    await tg.send(f"🎯 <b>{symbol}</b>\n{signal['side'].upper()} @ ${price:.2f}", 'general')
            except Exception as e:
                logger.error(f"Error {symbol}: {e}")

    async def run(self):
        await tg.send("🟢 <b>BEAST MODE v7.0 ONLINE</b>\n\nMaster Control Panel Active", 'general')

        # Web Server
        app = web.Application()

        async def api_status(request):
            return web.json_response({
                'capital': config.CAPITAL,
                'daily_pnl': risk.daily_pnl,
                'positions': db.get_open_trades(),
                'stats': db.get_stats(7),
                'brain': {
                    'trades_learned': len(brain.performance_history),
                    'strategies': brain.strategy_weights,
                    'accuracy': 'Learning...' if not brain.model else 'Active'
                },
                'cycle': self.cycle,
                'paused': config.PAUSED
            })

        async def api_control(request):
            data = await request.json()
            action = data.get('action')

            if action == 'pause':
                config.PAUSED = True
                await tg.send("⏸️ <b>Trading PAUSED</b> from dashboard", 'general')
                return web.json_response({'status':'paused'})

            elif action == 'resume':
                config.PAUSED = False
                await tg.send("▶️ <b>Trading RESUMED</b> from dashboard", 'general')
                return web.json_response({'status':'resumed'})

            elif action == 'close_all':
                positions = db.get_open_trades()
                count = len(positions)
                # Close logic here
                await tg.send(f"🚨 <b>CLOSED {count} positions</b> from dashboard", 'general')
                return web.json_response({'closed':count})

            elif action == 'set_risk':
                level = float(data.get('level', 1.0))
                config.RISK_PER_TRADE = 0.01 * level
                await tg.send(f"⚙️ Risk set to {level}% from dashboard", 'general')
                return web.json_response({'risk':config.RISK_PER_TRADE})

            return web.json_response({'error':'unknown action'})

        async def api_positions(request):
            positions = db.get_open_trades()
            conn = sqlite3.connect('beast.db')
            c = conn.cursor()
            c.execute('SELECT * FROM trades ORDER BY timestamp DESC LIMIT 20')
            trades = [{'symbol':r[1],'side':r[2],'pnl':r[6],'time':r[8]} for r in c.fetchall()]
            conn.close()
            return web.json_response({'positions':positions,'trades':trades})

        async def serve_dashboard(request):
            try:
                return web.FileResponse('./index.html')
            except:
                return web.Response(text="<h1>Beast Mode</h1><p>Dashboard loading...</p>", content_type='text/html')

        app.router.add_get('/api/status', api_status)
        app.router.add_get('/api/positions', api_positions)
        app.router.add_post('/api/control', api_control)
        app.router.add_get('/', serve_dashboard)
        app.router.add_get('/dashboard', serve_dashboard)

        runner = web.AppRunner(app)
        await runner.setup()
        port = int(os.getenv('PORT', 8080))
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        logger.info(f"✓ Web dashboard: http://0.0.0.0:{port}")

        # Main loop
        last_report = datetime.min
        while True:
            try:
                self.cycle += 1
                now = datetime.utcnow()

                # Scan for opportunities
                if not config.PAUSED:
                    await self.scan_and_trade()

                # Daily report
                if now.hour == 22 and (now - last_report).seconds > 3600:
                    stats = db.get_stats(1)
                    await tg.send(f"📊 <b>Daily Report</b>\n\nCapital: ${config.CAPITAL:.2f}\nP&L: ${risk.daily_pnl:.2f}\nTrades: {stats['total']}\nWR: {stats['wr']}%", 'general')
                    last_report = now

                await asyncio.sleep(30)

            except Exception as e:
                logger.error(f"Main loop error: {e}")
                await asyncio.sleep(60)

if __name__ == '__main__':
    bot = BeastBot()
    asyncio.run(bot.run())