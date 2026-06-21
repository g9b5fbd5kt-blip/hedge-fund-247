#!/usr/bin/env python3
"""
BEAST MODE v6.0 - PRODUCTION FINAL
Complete trading system with web dashboard
Capital: $1,005.42 | Target: 1.25% weekly | Railway optimized
All bugs fixed, all features integrated
"""
import os
import sys
import asyncio
import json
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

print("="*60, flush=True)
print("BEAST MODE v6.0 PRODUCTION - STARTING", flush=True)
print("="*60, flush=True)

print("[1/6] Importing core modules...", flush=True)
try:
    import numpy as np
    import pandas as pd
    import warnings
    warnings.filterwarnings("ignore", category=FutureWarning)
    print(f"✓ numpy {np.__version__}, pandas {pd.__version__}", flush=True)
except Exception as e:
    print(f"Installing dependencies...", flush=True)
    os.system("pip install numpy pandas -q")
    import numpy as np
    import pandas as pd

print("[2/6] Importing aiohttp...", flush=True)
try:
    import aiohttp
    from aiohttp import web
    import aiohttp_cors
    print("✓ aiohttp imported", flush=True)
except:
    os.system("pip install aiohttp aiohttp-cors -q")
    import aiohttp
    from aiohttp import web
    import aiohttp_cors

print("[3/6] Checking Alpaca...", flush=True)
ALPACA = False
try:
    from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
    from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import MarketOrderRequest, TakeProfitRequest, StopLossRequest
    from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass
    ALPACA = True
    print("✓ Alpaca available", flush=True)
except Exception as e:
    print(f"⚠ Alpaca not available: {e}", flush=True)

print("[4/6] Setting up logging...", flush=True)
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("beast")
print("✓ Logging ready", flush=True)

print("[5/6] Loading configuration...", flush=True)
class Config:
    APCA_KEY = os.getenv('APCA_API_KEY_ID', '')
    APCA_SECRET = os.getenv('APCA_API_SECRET_KEY', '')
    TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', '')
    TELEGRAM_CHAT = os.getenv('TELEGRAM_CHAT_ID', '')
    LIVE = os.getenv('LIVE_MODE', 'false').lower() == 'true'
    CAPITAL = 1005.42
    RISK = 0.005
    MAX_LOSS_DAY = 0.02
    MAX_POSITIONS = 3
    SCAN_INTERVAL = 900
    MAX_HOLD_HOURS = 24
    SYMBOLS = ['BTC/USD', 'ETH/USD', 'SOL/USD', 'SPY', 'QQQ', 'TSLA', 'NVDA']
    CRYPTO = {'BTC/USD', 'ETH/USD', 'SOL/USD'}

config = Config()
print(f"✓ Config loaded — LIVE={config.LIVE}", flush=True)

print("[6/6] Initializing database...", flush=True)
class DB:
    def __init__(self):
        self.conn = sqlite3.connect('beast.db', check_same_thread=False)
        c = self.conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY, ts_open TEXT, ts_close TEXT, symbol TEXT, side TEXT,
            qty REAL, entry REAL, stop REAL, target REAL, exit REAL, pnl REAL,
            pnl_pct REAL, strategy TEXT, order_id TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, val TEXT)''')
        self.conn.commit()
        print("✓ Database ready", flush=True)
    
    def open_trade(self, symbol, side, qty, entry, stop, target, strategy, order_id=''):
        c = self.conn.cursor()
        c.execute('INSERT INTO trades (ts_open,symbol,side,qty,entry,stop,target,strategy,order_id) VALUES (?,?,?,?,?,?,?,?,?)',
                  (datetime.utcnow().isoformat(), symbol, side, qty, entry, stop, target, strategy, order_id))
        self.conn.commit()
        return c.lastrowid
    
    def close_trade(self, trade_id, exit_price, pnl, pnl_pct):
        c = self.conn.cursor()
        c.execute('UPDATE trades SET ts_close=?, exit=?, pnl=?, pnl_pct=? WHERE id=?',
                  (datetime.utcnow().isoformat(), exit_price, pnl, pnl_pct, trade_id))
        self.conn.commit()
    
    def get_open_trades(self):
        c = self.conn.cursor()
        c.execute('SELECT id,ts_open,symbol,side,qty,entry,stop,target,strategy,order_id FROM trades WHERE ts_close IS NULL')
        return [dict(zip(['id','ts_open','symbol','side','qty','entry','stop','target','strategy','order_id'], r)) for r in c.fetchall()]
    
    def get_closed_trades(self, limit=50):
        c = self.conn.cursor()
        c.execute('SELECT * FROM trades WHERE ts_close IS NOT NULL ORDER BY ts_close DESC LIMIT ?', (limit,))
        cols = [d[0] for d in c.description]
        return [dict(zip(cols, r)) for r in c.fetchall()]
    
    def get_stats(self, days=7):
        c = self.conn.cursor()
        try:
            c.execute(f'''SELECT COUNT(*), SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END), SUM(pnl), AVG(pnl_pct)
                         FROM trades WHERE ts_close IS NOT NULL AND ts_open > datetime('now', '-{days} days')''')
            r = c.fetchone()
            total = r[0] or 0
            wins = r[1] or 0
            return {'total': total, 'wins': wins, 'pnl': round(r[2] or 0, 2), 'avg': round(r[3] or 0, 4), 'wr': round((wins/total*100) if total else 0, 1)}
        except:
            return {'total': 0, 'wins': 0, 'pnl': 0, 'avg': 0, 'wr': 0}
    
    def get(self, k, d=None):
        try:
            c = self.conn.cursor()
            c.execute('SELECT val FROM state WHERE key=?', (k,))
            r = c.fetchone()
            return json.loads(r[0]) if r else d
        except:
            return d
    
    def set(self, k, v):
        try:
            c = self.conn.cursor()
            c.execute('INSERT OR REPLACE INTO state (key,val) VALUES (?,?)', (k, json.dumps(v)))
            self.conn.commit()
        except Exception as e:
            log.error(f"DB error: {e}")

db = DB()

class Telegram:
    def __init__(self):
        self.token = config.TELEGRAM_TOKEN
        self.chat = config.TELEGRAM_CHAT
        self.enabled = bool(self.token and self.chat)
        self.idx = db.get('phrase_idx', 0)
        self.phrases = {
            'morning': ["☀️ Good morning boss — checking stocks not flipping rocks", "Rise and grind 💪 hustle harder today"],
            'buy': ["CASHED IN 💰", "Moving paper 💵", "Numbers don't lie 📈", "Clean money this way 🧼"],
            'sell': ["CASHED OUT 💰", "Money coming 💵", "One dollar at a time! 💵"],
            'loss': ["Took the L, part of the game 📉", "Stop hit — protect the bag 🛑"],
            'evening': ["🌙 Evening recap boss", "Market closed — counting paper 💵"],
            'general': ["System running ✅", "Platform active 📡"]
        }
        print(f"✓ Telegram ready (enabled={self.enabled})", flush=True)
    
    def _phrase(self, cat):
        p = self.phrases.get(cat, self.phrases['general'])
        phrase = p[self.idx % len(p)]
        self.idx += 1
        db.set('phrase_idx', self.idx)
        return phrase
    
    async def send(self, body, category='general'):
        msg = f"{self._phrase(category)}\n\n{body}"
        if not self.enabled:
            print(f"[TG] {msg}", flush=True)
            return
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            async with aiohttp.ClientSession() as s:
                await s.post(url, json={'chat_id': self.chat, 'text': msg, 'parse_mode': 'Markdown'}, timeout=aiohttp.ClientTimeout(total=10))
        except Exception as e:
            log.error(f"TG failed: {e}")

tg = Telegram()

class Risk:
    def __init__(self):
        self.daily_pnl = db.get('daily_pnl', 0.0)
        self.last_reset = db.get('last_reset', datetime.utcnow().date().isoformat())
    
    def reset(self):
        today = datetime.utcnow().date().isoformat()
        if today != self.last_reset:
            self.daily_pnl = 0.0
            self.last_reset = today
            db.set('daily_pnl', 0.0)
            db.set('last_reset', today)
    
    def can_trade(self, sym):
        self.reset()
        if self.daily_pnl <= -(config.CAPITAL * config.MAX_LOSS_DAY):
            return False, "Daily loss limit"
        open_trades = db.get_open_trades()
        if len(open_trades) >= config.MAX_POSITIONS:
            return False, "Max positions"
        if sym in {t['symbol'] for t in open_trades}:
            return False, "Already in"
        return True, "OK"
    
    def size(self, price, stop):
        risk_amt = config.CAPITAL * config.RISK
        risk_per = abs(price - stop)
        if risk_per == 0:
            return 0
        qty = risk_amt / risk_per
        return round(qty, 6) if price < 1000 else round(qty, 4)
    
    def add_pnl(self, amount):
        self.daily_pnl += amount
        db.set('daily_pnl', self.daily_pnl)

risk = Risk()

class Signals:
    def features(self, df):
        df = df.copy()
        c = df['close']
        delta = c.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        df['rsi'] = 100 - (100 / (1 + gain / loss.replace(0, 1e-9)))
        df['sma20'] = c.rolling(20).mean()
        df['sma50'] = c.rolling(50).mean()
        std = c.rolling(20).std()
        df['bb_up'] = df['sma20'] + std * 2
        df['bb_low'] = df['sma20'] - std * 2
        df['macd'] = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
        df['macd_sig'] = df['macd'].ewm(span=9, adjust=False).mean()
        df['vol_rat'] = df['volume'] / df['volume'].rolling(20).mean().replace(0, 1)
        hi_lo = df['high'] - df['low']
        df['atr'] = pd.concat([hi_lo, (df['high'] - c.shift()).abs(), (df['low'] - c.shift()).abs()], axis=1).max(axis=1).rolling(14).mean()
        return df.bfill().ffill()
    
    def generate(self, df):
        if len(df) < 55:
            return None
        df = self.features(df)
        l = df.iloc[-1]
        price = float(l['close'])
        atr = float(l['atr']) if l['atr'] > 0 else price * 0.005
        sigs = []
        if l['rsi'] < 25 and l['close'] < l['bb_low'] and l['vol_rat'] > 1.2:
            sigs.append({'t': 'mean_rev', 'd': 'long', 'c': 0.68, 'e': price, 's': price - atr*1.5, 'tgt': price + atr*2})
        if l['rsi'] > 75 and l['close'] > l['bb_up'] and l['vol_rat'] > 1.2:
            sigs.append({'t': 'mean_rev', 'd': 'short', 'c': 0.65, 'e': price, 's': price + atr*1.5, 'tgt': price - atr*2})
        if l['close'] > l['sma20'] > l['sma50'] and l['macd'] > l['macd_sig'] and l['vol_rat'] > 1.5:
            sigs.append({'t': 'momentum', 'd': 'long', 'c': 0.62, 'e': price, 's': price - atr*2, 'tgt': price + atr*2.5})
        return max(sigs, key=lambda x: x['c']) if sigs else None

signals = Signals()

class Beast:
    def __init__(self):
        self.cycle = 0
        self.trading = None
        self.crypto_data = None
        self.stock_data = None
        if ALPACA and config.APCA_KEY:
            try:
                self.trading = TradingClient(config.APCA_KEY, config.APCA_SECRET, paper=not config.LIVE)
                self.crypto_data = CryptoHistoricalDataClient(config.APCA_KEY, config.APCA_SECRET)
                self.stock_data = StockHistoricalDataClient(config.APCA_KEY, config.APCA_SECRET)
                acct = self.trading.get_account()
                config.CAPITAL = float(acct.portfolio_value)
                print(f"✓ Alpaca connected | ${config.CAPITAL:,.2f}", flush=True)
            except Exception as e:
                print(f"⚠ Alpaca: {e}", flush=True)
    
    async def get_bars(self, sym):
        if not self.trading:
            return pd.DataFrame()
        try:
            if sym in config.CRYPTO:
                req = CryptoBarsRequest(symbol_or_symbols=sym, timeframe=TimeFrame.Hour, limit=100)
                bars = await asyncio.to_thread(self.crypto_data.get_crypto_bars, req)
            else:
                req = StockBarsRequest(symbol_or_symbols=sym, timeframe=TimeFrame.Hour, limit=100)
                bars = await asyncio.to_thread(self.stock_data.get_stock_bars, req)
            df = bars.df.reset_index()
            return df.rename(columns={'timestamp': 'time'}) if not df.empty else pd.DataFrame()
        except Exception as e:
            log.warning(f"Data {sym}: {e}")
            return pd.DataFrame()
    
    async def submit_order(self, sym, sig, qty):
        if not self.trading:
            return f"SIM-{int(datetime.utcnow().timestamp())}"
        try:
            side = OrderSide.BUY if sig['d'] == 'long' else OrderSide.SELL
            order = MarketOrderRequest(symbol=sym.replace('/', ''), qty=qty, side=side, time_in_force=TimeInForce.GTC,
                                       order_class=OrderClass.BRACKET,
                                       take_profit=TakeProfitRequest(limit_price=round(sig['tgt'], 2)),
                                       stop_loss=StopLossRequest(stop_price=round(sig['s'], 2)))
            result = await asyncio.to_thread(self.trading.submit_order, order)
            return str(result.id)
        except Exception as e:
            log.error(f"Order failed: {e}")
            return None
    
    async def get_price(self, sym):
        df = await self.get_bars(sym)
        return float(df['close'].iloc[-1]) if not df.empty else 0
    
    async def monitor_exits(self):
        for trade in db.get_open_trades():
            price = await self.get_price(trade['symbol'])
            if price == 0:
                continue
            age = (datetime.utcnow() - datetime.fromisoformat(trade['ts_open'])).total_seconds() / 3600
            hit_stop = (price <= trade['stop']) if trade['side'] == 'long' else (price >= trade['stop'])
            hit_tp = (price >= trade['target']) if trade['side'] == 'long' else (price <= trade['target'])
            hit_time = age >= config.MAX_HOLD_HOURS
            if hit_stop or hit_tp or hit_time:
                pnl_pct = ((price - trade['entry']) / trade['entry']) if trade['side'] == 'long' else ((trade['entry'] - price) / trade['entry'])
                pnl = pnl_pct * trade['entry'] * trade['qty']
                db.close_trade(trade['id'], price, pnl, pnl_pct)
                risk.add_pnl(pnl)
                reason = "STOP" if hit_stop else "TARGET" if hit_tp else "TIME"
                await tg.send(f"*{trade['symbol']}* closed\nReason: {reason}\nPnL: ${pnl:+.2f} ({pnl_pct*100:+.2f}%)", 'sell' if pnl >= 0 else 'loss')
    
    async def scan(self):
        for sym in config.SYMBOLS:
            ok, reason = risk.can_trade(sym)
            if not ok:
                continue
            df = await self.get_bars(sym)
            if len(df) < 55:
                continue
            sig = signals.generate(df)
            if sig and sig['c'] >= 0.6:
                qty = risk.size(sig['e'], sig['s'])
                if qty > 0:
                    order_id = await self.submit_order(sym, sig, qty)
                    if order_id:
                        db.open_trade(sym, sig['d'], qty, sig['e'], sig['s'], sig['tgt'], sig['t'], order_id)
                        await tg.send(f"*{sym}* {sig['d'].upper()}\nEntry: ${sig['e']:.2f}\nStop: ${sig['s']:.2f}\nTarget: ${sig['tgt']:.2f}", 'buy')
            await asyncio.sleep(0.5)
    
    async def run(self):
        print("\n" + "="*60, flush=True)
        print("BEAST MODE v6.0 RUNNING", flush=True)
        print("="*60, flush=True)
        await tg.send(f"*Beast Mode v6.0 Active*\nCapital: ${config.CAPITAL:.2f}\nMode: {'LIVE' if config.LIVE else 'PAPER'}", 'general')
        
        # Start web dashboard
        app = web.Application()
        async def status(request):
            return web.json_response({
                'capital': config.CAPITAL,
                'pnl': risk.daily_pnl,
                'positions': len(db.get_open_trades()),
                'trades': db.get_stats(7),
                'open': db.get_open_trades(),
                'closed': db.get_closed_trades(20)
            })
        app.router.add_get('/api/status', status)
        app.router.add_static('/', '.', show_index=True)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, '0.0.0.0', int(os.getenv('PORT', 8080))).start()
        print(f"✓ Dashboard: http://0.0.0.0:{os.getenv('PORT', 8080)}", flush=True)
        
        while True:
            try:
                self.cycle += 1
                log.info(f"Cycle {self.cycle}")
                await self.monitor_exits()
                await self.scan()
                await asyncio.sleep(config.SCAN_INTERVAL)
            except Exception as e:
                log.error(f"Loop: {e}")
                await asyncio.sleep(60)

if __name__ == '__main__':
    try:
        asyncio.run(Beast().run())
    except KeyboardInterrupt:
        print("\nShutdown", flush=True)