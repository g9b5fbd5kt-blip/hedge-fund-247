#!/usr/bin/env python3
"""
BEAST MODE v5.0 - ULTIMATE HUSTLE EDITION
Fixed with startup logging
"""
import os
import sys
import asyncio

# EARLY LOGGING - Print immediately
print("="*60, flush=True)
print("BEAST MODE v5.0 STARTING", flush=True)
print("="*60, flush=True)

# Test imports with error handling
print("[1/6] Importing core modules...", flush=True)
try:
    import sqlite3
    import json
    from datetime import datetime, timedelta
    from typing import Dict, List, Optional
    print("✓ Core modules imported", flush=True)
except Exception as e:
    print(f"✗ Core import failed: {e}", flush=True)
    sys.exit(1)

print("[2/6] Importing data science modules...", flush=True)
try:
    import numpy as np
    import pandas as pd
    print(f"✓ numpy {np.__version__}, pandas {pd.__version__}", flush=True)
except Exception as e:
    print(f"✗ Data science import failed: {e}", flush=True)
    print("Installing...", flush=True)
    os.system("pip install numpy pandas -q")
    import numpy as np
    import pandas as pd

print("[3/6] Importing aiohttp...", flush=True)
try:
    import aiohttp
    print("✓ aiohttp imported", flush=True)
except Exception as e:
    print(f"✗ aiohttp failed: {e}", flush=True)
    os.system("pip install aiohttp -q")
    import aiohttp

# Optional imports
print("[4/6] Checking Alpaca...", flush=True)
ALPACA = False
try:
    from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
    from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import LimitOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce
    ALPACA = True
    print("✓ Alpaca available", flush=True)
except Exception as e:
    print(f"⚠ Alpaca not available (simulation mode): {e}", flush=True)

print("[5/6] Setting up logging...", flush=True)
try:
    import structlog
    structlog.configure(processors=[structlog.processors.JSONRenderer()])
    log = structlog.get_logger()
    print("✓ structlog ready", flush=True)
except:
    import logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    log = logging.getLogger(__name__)
    print("✓ standard logging ready", flush=True)

print("[6/6] Loading configuration...", flush=True)

# ============================================================================
# CONFIG
# ============================================================================
class Config:
    APCA_KEY = os.getenv('APCA_API_KEY_ID', '')
    APCA_SECRET = os.getenv('APCA_API_SECRET_k', '')
    TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', '')
    TELEGRAM_CHAT = os.getenv('TELEGRAM_CHAT_ID', '')
    LIVE = os.getenv('LIVE_MODE', 'false').lower() == 'true'
    
    CAPITAL = 1005.42
    RISK = 0.005
    MAX_LOSS = 0.02
    SYMBOLS = ['BTC/USD', 'ETH/USD', 'SOL/USD', 'SPY', 'QQQ', 'TSLA', 'NVDA']
    TARGET_WEEKLY = 0.0125

config = Config()
print(f"✓ Config loaded - LIVE={config.LIVE}, CAPITAL=${config.CAPITAL}", flush=True)
print("="*60, flush=True)

# ============================================================================
# DATABASE
# ============================================================================
class DB:
    def __init__(self):
        try:
            self.conn = sqlite3.connect('beast.db', check_same_thread=False)
            c = self.conn.cursor()
            c.execute('''CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY, ts TEXT, symbol TEXT, side TEXT, qty REAL,
                entry REAL, exit REAL, pnl REAL, pnl_pct REAL, strategy TEXT)''')
            c.execute('''CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, val TEXT)''')
            self.conn.commit()
            print("✓ Database initialized", flush=True)
        except Exception as e:
            print(f"✗ Database failed: {e}", flush=True)
            raise
    
    def log_trade(self, t):
        try:
            c = self.conn.cursor()
            c.execute('INSERT INTO trades (ts,symbol,side,qty,entry,exit,pnl,pnl_pct,strategy) VALUES (?,?,?,?,?,?,?,?,?)',
                      (datetime.now().isoformat(), t['symbol'], t['side'], t['qty'], t['entry'],
                       t.get('exit'), t.get('pnl'), t.get('pnl_pct'), t.get('strategy')))
            self.conn.commit()
        except Exception as e:
            print(f"DB log error: {e}", flush=True)
    
    def get_stats(self, days=7):
        try:
            c = self.conn.cursor()
            c.execute(f'''SELECT COUNT(*), SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END),
                         SUM(pnl), AVG(pnl_pct) FROM trades 
                         WHERE ts > datetime('now', '-{days} days')''')
            r = c.fetchone()
            return {'total': r[0] or 0, 'wins': r[1] or 0, 'pnl': r[2] or 0, 
                    'avg': r[3] or 0, 'wr': (r[1]/r[0]*100) if r[0] else 0}
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
            print(f"DB set error: {e}", flush=True)

db = DB()

# ============================================================================
# TELEGRAM
# ============================================================================
class Telegram:
    def __init__(self):
        self.token = config.TELEGRAM_TOKEN
        self.chat = config.TELEGRAM_CHAT
        self.enabled = bool(self.token and self.chat)
        self.idx = db.get('phrase_idx', 0)
        
        self.phrases = {
            'morning': [
                "Good morning boss ☀️ checking stocks not flipping rocks",
                "Rise and grind 💪 hustle harder today",
                "Morning scan active - chase money not 🐕",
            ],
            'buy': [
                "CASHED IN 💰 {sym} long at ${price}",
                "Moving paper 💵 {sym} position opened",
                "The numbers don't lie graph 📈 {sym} entry",
                "Clean money this way 🧼 {sym}",
            ],
            'sell': [
                "CASHED OUT 💰 {sym} +${pnl}",
                "Money coming 💵 {sym} closed",
                "One dollar at a time! {sym} profit taken",
            ],
            'evening': [
                "Evening recap boss 🌙",
                "Market closed - counting paper 💵",
                "Day's hustle complete 💪",
            ],
            'general': [
                "System running: All systems nominal",
                "Platform active: Scanning 7 markets",
                "Engine running",
            ]
        }
        print(f"✓ Telegram initialized (enabled={self.enabled})", flush=True)
    
    def get_phrase(self, category):
        phrases = self.phrases.get(category, self.phrases['general'])
        phrase = phrases[self.idx % len(phrases)]
        self.idx += 1
        db.set('phrase_idx', self.idx)
        return phrase
    
    async def send(self, msg, category='general', **kwargs):
        if not self.enabled:
            print(f"[TELEGRAM {category}] {msg}", flush=True)
            return
        
        try:
            phrase = self.get_phrase(category)
            full_msg = f"{phrase}\n\n{msg.format(**kwargs)}"
            
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            async with aiohttp.ClientSession() as s:
                await s.post(url, json={'chat_id': self.chat, 'text': full_msg, 'parse_mode': 'Markdown'}, timeout=aiohttp.ClientTimeout(total=10))
            print(f"✓ Telegram sent: {category}", flush=True)
        except Exception as e:
            print(f"✗ Telegram failed: {e}", flush=True)

tg = Telegram()

# ============================================================================
# RISK MANAGER
# ============================================================================
class Risk:
    def __init__(self):
        self.daily_pnl = db.get('daily_pnl', 0.0)
        self.positions = db.get('positions', {})
        self.last_reset = db.get('last_reset', datetime.now().date().isoformat())
        print(f"✓ Risk manager ready (positions={len(self.positions)})", flush=True)
    
    def reset(self):
        today = datetime.now().date().isoformat()
        if today != self.last_reset:
            self.daily_pnl = 0
            self.last_reset = today
            db.set('daily_pnl', 0)
            db.set('last_reset', today)
    
    def can_trade(self, sym):
        self.reset()
        if self.daily_pnl <= -config.CAPITAL * config.MAX_LOSS:
            return False, "Daily loss"
        if len(self.positions) >= 3:
            return False, "Max positions"
        if sym in self.positions:
            return False, "Already in"
        return True, "OK"
    
    def size(self, price, stop):
        risk_amt = config.CAPITAL * config.RISK
        risk_per = abs(price - stop)
        if risk_per == 0:
            return 0
        qty = risk_amt / risk_per
        return int(qty) if price > 100 else round(qty, 6)

risk = Risk()

# ============================================================================
# SIGNALS
# ============================================================================
class Signals:
    def features(self, df):
        df = df.copy()
        delta = df['close'].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = -delta.where(delta < 0, 0).rolling(14).mean()
        df['rsi'] = 100 - (100 / (1 + gain / loss.replace(0, 0.001)))
        df['sma20'] = df['close'].rolling(20).mean()
        df['sma50'] = df['close'].rolling(50).mean()
        df['bb_mid'] = df['close'].rolling(20).mean()
        std = df['close'].rolling(20).std()
        df['bb_up'] = df['bb_mid'] + std * 2
        df['bb_low'] = df['bb_mid'] - std * 2
        df['macd'] = df['close'].ewm(12).mean() - df['close'].ewm(26).mean()
        df['macd_sig'] = df['macd'].ewm(9).mean()
        df['vol_sma'] = df['volume'].rolling(20).mean()
        df['vol_rat'] = df['volume'] / df['vol_sma'].replace(0, 1)
        return df.fillna(method='bfill').fillna(method='ffill')
    
    def generate(self, df):
        if len(df) < 50:
            return None
        df = self.features(df)
        l = df.iloc[-1]
        sigs = []
        
        if l['rsi'] < 22 and l['close'] < l['bb_low'] and l['vol_rat'] > 1.2:
            sigs.append({'t': 'mean_rev', 'd': 'long', 'c': 0.68, 'e': l['close'], 's': l['close'] * 0.9925, 'tgt': l['close'] * 1.009})
        if l['rsi'] > 78 and l['close'] > l['bb_up'] and l['vol_rat'] > 1.2:
            sigs.append({'t': 'mean_rev', 'd': 'short', 'c': 0.68, 'e': l['close'], 's': l['close'] * 1.0075, 'tgt': l['close'] * 0.991})
        if l['close'] > l['sma20'] > l['sma50'] and l['macd'] > l['macd_sig'] and l['vol_rat'] > 1.5:
            sigs.append({'t': 'momentum', 'd': 'long', 'c': 0.62, 'e': l['close'], 's': l['close'] * 0.9925, 'tgt': l['close'] * 1.011})
        
        return max(sigs, key=lambda x: x['c']) if sigs else None

signals = Signals()
print("✓ Signals ready", flush=True)

# ============================================================================
# MAIN ENGINE
# ============================================================================
class Beast:
    def __init__(self):
        self.cycle = 0
        self.trading = None
        self.crypto = None
        self.stocks = None
        
        if ALPACA and config.APCA_KEY:
            try:
                self.trading = TradingClient(config.APCA_KEY, config.APCA_SECRET, paper=not config.LIVE)
                self.crypto = CryptoHistoricalDataClient(config.APCA_KEY, config.APCA_SECRET)
                self.stocks = StockHistoricalDataClient(config.APCA_KEY, config.APCA_SECRET)
                print("✓ Alpaca clients initialized", flush=True)
            except Exception as e:
                print(f"⚠ Alpaca init failed: {e}", flush=True)
        else:
            print("⚠ Running in simulation mode", flush=True)
    
    async def get_data(self, sym):
        if not ALPACA or not self.crypto:
            return pd.DataFrame()
        try:
            if '/' in sym:
                req = CryptoBarsRequest(symbol_or_symbols=sym, timeframe=TimeFrame.Hour, limit=100)
                bars = self.crypto.get_crypto_bars(req)
            else:
                req = StockBarsRequest(symbol_or_symbols=sym, timeframe=TimeFrame.Hour, limit=100)
                bars = self.stocks.get_stock_bars(req)
            df = bars.df.reset_index()
            if not df.empty:
                return df.rename(columns={'timestamp': 'time'})
            return pd.DataFrame()
        except Exception as e:
            print(f"Data error {sym}: {e}", flush=True)
            return pd.DataFrame()
    
    async def run(self):
        print("\n" + "="*60, flush=True)
        print("BEAST MODE RUNNING", flush=True)
        print("="*60, flush=True)
        
        # Send startup message
        await tg.send(
            f"*Beast Mode v5.0 Active* 💪\n\n"
            f"Capital: ${config.CAPITAL:.2f}\n"
            f"Risk: ${config.CAPITAL * config.RISK:.2f}/trade\n"
            f"Mode: {'LIVE 🔴' if config.LIVE else 'PAPER 📝'}\n"
            f"Symbols: {len(config.SYMBOLS)}",
            'general'
        )
        
        while True:
            try:
                self.cycle += 1
                now = datetime.now().strftime('%H:%M:%S')
                print(f"\n[{now}] Cycle {self.cycle} - Scanning...", flush=True)
                
                for sym in config.SYMBOLS:
                    try:
                        df = await self.get_data(sym)
                        if not df.empty and len(df) >= 50:
                            sig = signals.generate(df)
                            if sig and sig['c'] >= 0.6:
                                ok, reason = risk.can_trade(sym)
                                if ok:
                                    qty = risk.size(sig['e'], sig['s'])
                                    if qty > 0:
                                        print(f"  → SIGNAL {sym} {sig['d']} @ ${sig['e']:.2f}", flush=True)
                                        await tg.send(
                                            f"*{sym}* {sig['d'].upper()}\nEntry: ${sig['e']:.2f}\nQty: {qty}",
                                            'buy', sym=sym, price=f"{sig['e']:.2f}", dir=sig['d'].upper()
                                        )
                        await asyncio.sleep(0.5)
                    except Exception as e:
                        print(f"  ✗ {sym}: {e}", flush=True)
                
                print(f"[{now}] Cycle complete. Sleeping 15min...", flush=True)
                await asyncio.sleep(900)
                
            except Exception as e:
                print(f"Loop error: {e}", flush=True)
                await asyncio.sleep(60)

# ============================================================================
# START
# ============================================================================
if __name__ == '__main__':
    print("\nStarting main loop...", flush=True)
    try:
        beast = Beast()
        asyncio.run(beast.run())
    except KeyboardInterrupt:
        print("\nShutdown requested", flush=True)
    except Exception as e:
        print(f"\nFATAL ERROR: {e}", flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)