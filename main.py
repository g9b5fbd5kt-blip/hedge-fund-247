#!/usr/bin/env python3
"""
BEAST MODE v5.1 - HARDENED EDITION
Fixes: env var typo, pandas deprecations, missing order submission,
       missing exit loop, position state sync, formatted Telegram messages,
       scheduled summaries, bracket orders, time-stop, daily reset.
"""
import os
import sys
import asyncio

print("="*60, flush=True)
print("BEAST MODE v5.1 HARDENED - STARTING", flush=True)
print("="*60, flush=True)

print("[1/6] Importing core modules...", flush=True)
try:
    import sqlite3
    import json
    from datetime import datetime, timedelta
    from typing import Dict, List, Optional, Tuple
    print("✓ Core modules imported", flush=True)
except Exception as e:
    print(f"✗ Core import failed: {e}", flush=True)
    sys.exit(1)

print("[2/6] Importing data science modules...", flush=True)
try:
    import numpy as np
    import pandas as pd
    # Suppress FutureWarning noise — we use ffill()/bfill() directly now
    import warnings
    warnings.filterwarnings("ignore", category=FutureWarning)
    print(f"✓ numpy {np.__version__}, pandas {pd.__version__}", flush=True)
except Exception as e:
    print(f"✗ Data science import failed: {e}", flush=True)
    os.system("pip install numpy pandas -q")
    import numpy as np
    import pandas as pd

print("[3/6] Importing aiohttp...", flush=True)
try:
    import aiohttp
    print("✓ aiohttp imported", flush=True)
except Exception as e:
    os.system("pip install aiohttp -q")
    import aiohttp

print("[4/6] Checking Alpaca...", flush=True)
ALPACA = False
try:
    from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
    from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import (
        MarketOrderRequest,
        LimitOrderRequest,
        TakeProfitRequest,
        StopLossRequest,
        GetOrdersRequest,
        ClosePositionRequest
    )
    from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass, QueryOrderStatus
    ALPACA = True
    print("✓ Alpaca available", flush=True)
except Exception as e:
    print(f"⚠ Alpaca not available (simulation mode): {e}", flush=True)

print("[5/6] Setting up logging...", flush=True)
import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("beast")
print("✓ Logging ready", flush=True)

print("[6/6] Loading configuration...", flush=True)

# ============================================================================
# CONFIG — FIX: env var name was APCA_API_SECRET_k (lowercase k = never matched)
# ============================================================================
class Config:
    # BUG FIX #1: was 'APCA_API_SECRET_k' — typo caused auth to always fail silently
    APCA_KEY    = os.getenv('APCA_API_KEY_ID', '')
    APCA_SECRET = os.getenv('APCA_API_SECRET_KEY', '')   # ← FIXED (was _k)
    TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', '')
    TELEGRAM_CHAT  = os.getenv('TELEGRAM_CHAT_ID', '')
    LIVE = os.getenv('LIVE_MODE', 'false').lower() == 'true'

    CAPITAL        = 1005.42   # synced from Alpaca on startup, this is fallback
    RISK           = 0.005     # 0.5% per trade
    MAX_LOSS_DAY   = 0.02      # 2% daily circuit breaker
    MAX_POSITIONS  = 3
    MAX_CRYPTO_POS = 2
    MAX_STOCK_POS  = 2
    SCAN_INTERVAL  = 900       # 15 minutes
    MAX_HOLD_HOURS = 24        # time-stop

    SYMBOLS = ['BTC/USD', 'ETH/USD', 'SOL/USD', 'SPY', 'QQQ', 'TSLA', 'NVDA']
    CRYPTO  = {'BTC/USD', 'ETH/USD', 'SOL/USD'}
    TARGET_WEEKLY = 0.0125

    # Per-asset stop/target multipliers
    SL_PCT  = {'crypto': 0.0075, 'stock': 0.015}
    TP_PCT  = {'crypto': 0.009,  'stock': 0.011}

config = Config()
print(f"✓ Config loaded — LIVE={config.LIVE}", flush=True)
print("="*60, flush=True)


# ============================================================================
# DATABASE — position state now persists open entries properly
# ============================================================================
class DB:
    def __init__(self):
        try:
            self.conn = sqlite3.connect('beast.db', check_same_thread=False)
            c = self.conn.cursor()
            # trades table — open positions have exit=NULL, pnl=NULL
            c.execute('''CREATE TABLE IF NOT EXISTS trades (
                id         INTEGER PRIMARY KEY,
                ts_open    TEXT,
                ts_close   TEXT,
                symbol     TEXT,
                side       TEXT,
                qty        REAL,
                entry      REAL,
                stop       REAL,
                target     REAL,
                exit       REAL,
                pnl        REAL,
                pnl_pct    REAL,
                strategy   TEXT,
                order_id   TEXT
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS state (
                key TEXT PRIMARY KEY,
                val TEXT
            )''')
            self.conn.commit()
            print("✓ Database initialized", flush=True)
        except Exception as e:
            print(f"✗ Database failed: {e}", flush=True)
            raise

    def open_trade(self, symbol, side, qty, entry, stop, target, strategy, order_id=''):
        """Log entry — returns row id."""
        c = self.conn.cursor()
        c.execute(
            '''INSERT INTO trades
               (ts_open, symbol, side, qty, entry, stop, target, strategy, order_id)
               VALUES (?,?,?,?,?,?,?,?,?)''',
            (datetime.utcnow().isoformat(), symbol, side, qty,
             entry, stop, target, strategy, order_id)
        )
        self.conn.commit()
        return c.lastrowid

    def close_trade(self, trade_id, exit_price, pnl, pnl_pct):
        """Mark a trade closed."""
        c = self.conn.cursor()
        c.execute(
            '''UPDATE trades
               SET ts_close=?, exit=?, pnl=?, pnl_pct=?
               WHERE id=?''',
            (datetime.utcnow().isoformat(), exit_price, pnl, pnl_pct, trade_id)
        )
        self.conn.commit()

    def get_open_trades(self) -> List[dict]:
        """All trades with no close timestamp."""
        c = self.conn.cursor()
        c.execute(
            'SELECT id,ts_open,symbol,side,qty,entry,stop,target,strategy,order_id '
            'FROM trades WHERE ts_close IS NULL'
        )
        rows = c.fetchall()
        keys = ['id','ts_open','symbol','side','qty','entry','stop','target','strategy','order_id']
        return [dict(zip(keys, r)) for r in rows]

    def get_stats(self, days=7) -> dict:
        c = self.conn.cursor()
        try:
            c.execute(f'''
                SELECT COUNT(*),
                       SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END),
                       SUM(pnl),
                       AVG(pnl_pct)
                FROM trades
                WHERE ts_close IS NOT NULL          -- BUG FIX #7: only closed trades
                  AND ts_open > datetime('now', '-{days} days')
            ''')
            r = c.fetchone()
            total = r[0] or 0
            wins  = r[1] or 0
            return {
                'total': total,
                'wins':  wins,
                'pnl':   round(r[2] or 0, 2),
                'avg':   round(r[3] or 0, 4),
                'wr':    round((wins / total * 100) if total else 0, 1)
            }
        except Exception as e:
            log.error(f"Stats error: {e}")
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
            c.execute('INSERT OR REPLACE INTO state (key,val) VALUES (?,?)',
                      (k, json.dumps(v)))
            self.conn.commit()
        except Exception as e:
            log.error(f"DB set error: {e}")

db = DB()


# ============================================================================
# TELEGRAM — BUG FIX #6: kwargs now applied to phrase AND body correctly
# ============================================================================
class Telegram:
    def __init__(self):
        self.token   = config.TELEGRAM_TOKEN
        self.chat    = config.TELEGRAM_CHAT
        self.enabled = bool(self.token and self.chat)
        self.idx     = db.get('phrase_idx', 0)

        self.phrases = {
            'morning': [
                "☀️ Good morning boss — checking stocks not flipping rocks",
                "Rise and grind 💪 hustle harder today",
                "Morning scan active — chase money not 🐕",
            ],
            'buy': [
                "CASHED IN 💰",
                "Moving paper 💵",
                "Numbers don't lie 📈",
                "Clean money this way 🧼",
            ],
            'sell': [
                "CASHED OUT 💰",
                "Money coming 💵",
                "One dollar at a time! 💵",
            ],
            'loss': [
                "Took the L, part of the game 📉",
                "Stop hit — protect the bag first 🛑",
                "Cut the loss, live to fight 💪",
            ],
            'evening': [
                "🌙 Evening recap boss",
                "Market closed — counting paper 💵",
                "Day's hustle complete 💪",
            ],
            'weekly': [
                "📊 Weekly bag secured",
                "Seven days down 💰",
                "Weekly hustle report 🔥",
            ],
            'general': [
                "System running ✅",
                "Platform active — scanning 7 markets 📡",
                "Engine running 🔥",
            ]
        }
        print(f"✓ Telegram initialized (enabled={self.enabled})", flush=True)

    def _next_phrase(self, category: str) -> str:
        phrases = self.phrases.get(category, self.phrases['general'])
        phrase  = phrases[self.idx % len(phrases)]
        self.idx += 1
        db.set('phrase_idx', self.idx)
        return phrase

    async def send(self, body: str, category: str = 'general') -> None:
        """
        BUG FIX #6: phrase is decoration, body is the pre-formatted message.
        Caller formats body before passing in — no .format() magic here.
        """
        phrase   = self._next_phrase(category)
        full_msg = f"{phrase}\n\n{body}"

        if not self.enabled:
            print(f"[TG:{category}] {full_msg}", flush=True)
            return

        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            async with aiohttp.ClientSession() as s:
                resp = await s.post(
                    url,
                    json={
                        'chat_id':    self.chat,
                        'text':       full_msg,
                        'parse_mode': 'Markdown'
                    },
                    timeout=aiohttp.ClientTimeout(total=10)
                )
                if resp.status != 200:
                    text = await resp.text()
                    log.warning(f"TG HTTP {resp.status}: {text[:100]}")
        except Exception as e:
            log.error(f"Telegram send failed: {e}")

tg = Telegram()


# ============================================================================
# RISK MANAGER — FIX #4: position count now driven from DB, not in-memory dict
# ============================================================================
class Risk:
    def __init__(self):
        self.daily_pnl  = db.get('daily_pnl', 0.0)
        self.last_reset = db.get('last_reset', datetime.utcnow().date().isoformat())

    def _reset_if_new_day(self):
        today = datetime.utcnow().date().isoformat()
        if today != self.last_reset:
            self.daily_pnl  = 0.0
            self.last_reset = today
            db.set('daily_pnl', 0.0)
            db.set('last_reset', today)
            log.info("Daily PnL reset ✓")

    def add_pnl(self, amount: float):
        self.daily_pnl += amount
        db.set('daily_pnl', self.daily_pnl)

    def can_trade(self, sym: str) -> Tuple[bool, str]:
        self._reset_if_new_day()

        # Daily loss circuit breaker
        if self.daily_pnl <= -(config.CAPITAL * config.MAX_LOSS_DAY):
            return False, f"Daily loss limit hit (${self.daily_pnl:.2f})"

        # Position count from DB (survives restarts) — BUG FIX #4
        open_trades = db.get_open_trades()
        symbols_open = {t['symbol'] for t in open_trades}

        if sym in symbols_open:
            return False, "Already in position"

        total_open  = len(open_trades)
        crypto_open = sum(1 for t in open_trades if t['symbol'] in config.CRYPTO)
        stock_open  = total_open - crypto_open

        if total_open >= config.MAX_POSITIONS:
            return False, f"Max {config.MAX_POSITIONS} positions"

        is_crypto = sym in config.CRYPTO
        if is_crypto and crypto_open >= config.MAX_CRYPTO_POS:
            return False, "Max crypto positions"
        if not is_crypto and stock_open >= config.MAX_STOCK_POS:
            return False, "Max stock positions"

        return True, "OK"

    def size(self, price: float, stop: float) -> float:
        risk_amt  = config.CAPITAL * config.RISK
        risk_per  = abs(price - stop)
        if risk_per == 0:
            return 0.0
        qty = risk_amt / risk_per
        # Fractional for crypto, whole shares for stocks
        return round(qty, 6) if price < 1000 else round(qty, 4)

risk = Risk()


# ============================================================================
# SIGNALS — FIX #2: replaced deprecated fillna(method=) with ffill()/bfill()
# ============================================================================
class Signals:
    def features(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        c  = df['close']

        # RSI-14
        delta = c.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        df['rsi'] = 100 - (100 / (1 + gain / loss.replace(0, 1e-9)))

        # SMAs
        df['sma20'] = c.rolling(20).mean()
        df['sma50'] = c.rolling(50).mean()

        # Bollinger Bands
        bb_std       = c.rolling(20).std()
        df['bb_mid'] = df['sma20']
        df['bb_up']  = df['sma20'] + bb_std * 2
        df['bb_low'] = df['sma20'] - bb_std * 2

        # MACD
        df['macd']     = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
        df['macd_sig'] = df['macd'].ewm(span=9, adjust=False).mean()

        # Volume ratio
        vol_sma       = df['volume'].rolling(20).mean()
        df['vol_rat'] = df['volume'] / vol_sma.replace(0, 1)

        # ATR for dynamic stop distance
        hi_lo   = df['high'] - df['low']
        hi_pc   = (df['high'] - c.shift()).abs()
        lo_pc   = (df['low']  - c.shift()).abs()
        df['atr'] = pd.concat([hi_lo, hi_pc, lo_pc], axis=1).max(axis=1).rolling(14).mean()

        # BUG FIX #2: was fillna(method='bfill'/'ffill') — deprecated in pandas 2.1+
        return df.bfill().ffill()

    def generate(self, df: pd.DataFrame) -> Optional[dict]:
        if len(df) < 55:   # need 50 bars + warmup
            return None
        df = self.features(df)
        l  = df.iloc[-1]   # latest bar
        sigs = []

        price = float(l['close'])
        atr   = float(l['atr']) if l['atr'] > 0 else price * 0.005

        # --- Signal 1: Mean Reversion Long ---
        if (l['rsi'] < 25
                and l['close'] < l['bb_low']
                and l['vol_rat'] > 1.2):
            stop   = price - atr * 1.5
            target = price + atr * 2.0
            sigs.append({'t': 'mean_rev', 'd': 'long', 'c': 0.68,
                         'e': price, 's': stop, 'tgt': target})

        # --- Signal 2: Mean Reversion Short ---
        if (l['rsi'] > 75
                and l['close'] > l['bb_up']
                and l['vol_rat'] > 1.2):
            stop   = price + atr * 1.5
            target = price - atr * 2.0
            sigs.append({'t': 'mean_rev', 'd': 'short', 'c': 0.65,
                         'e': price, 's': stop, 'tgt': target})

        # --- Signal 3: Momentum Long ---
        if (l['close'] > l['sma20'] > l['sma50']
                and l['macd'] > l['macd_sig']
                and l['vol_rat'] > 1.5):
            stop   = price - atr * 2.0
            target = price + atr * 2.5
            sigs.append({'t': 'momentum', 'd': 'long', 'c': 0.62,
                         'e': price, 's': stop, 'tgt': target})

        # --- Signal 4: Pullback Long ---
        # Price pulled back to SMA20 in an uptrend, RSI mid-range
        if (l['close'] > l['sma50']
                and abs(l['close'] - l['sma20']) / l['sma20'] < 0.005
                and 42 < l['rsi'] < 58
                and l['macd'] > 0):
            stop   = l['sma50'] * 0.998
            target = price + atr * 2.0
            sigs.append({'t': 'pullback', 'd': 'long', 'c': 0.60,
                         'e': price, 's': stop, 'tgt': target})

        if not sigs:
            return None
        # Return highest-confidence signal
        return max(sigs, key=lambda x: x['c'])

signals = Signals()
print("✓ Signals ready", flush=True)


# ============================================================================
# MAIN ENGINE — FIX #3 #5: actual order submission + exit monitoring loop
# ============================================================================
class Beast:
    def __init__(self):
        self.cycle       = 0
        self.trading     = None
        self.crypto_data = None
        self.stock_data  = None
        self._init_alpaca()

    def _init_alpaca(self):
        if not ALPACA or not config.APCA_KEY:
            log.warning("Running in SIMULATION mode — no Alpaca keys")
            return
        try:
            self.trading = TradingClient(
                config.APCA_KEY,
                config.APCA_SECRET,
                paper=not config.LIVE
            )
            self.crypto_data = CryptoHistoricalDataClient(config.APCA_KEY, config.APCA_SECRET)
            self.stock_data  = StockHistoricalDataClient(config.APCA_KEY, config.APCA_SECRET)

            # Sync real account balance
            acct = self.trading.get_account()
            config.CAPITAL = float(acct.portfolio_value)
            log.info(f"✓ Alpaca connected | Portfolio: ${config.CAPITAL:,.2f}")
        except Exception as e:
            log.error(f"Alpaca init failed: {e}")

    # ------------------------------------------------------------------
    # DATA FETCH
    # ------------------------------------------------------------------
    async def get_bars(self, sym: str) -> pd.DataFrame:
        """Fetch 100 hourly bars. Returns empty DF on failure."""
        if not self.trading:
            return pd.DataFrame()
        try:
            if sym in config.CRYPTO:
                req  = CryptoBarsRequest(symbol_or_symbols=sym,
                                         timeframe=TimeFrame.Hour, limit=100)
                bars = await asyncio.to_thread(self.crypto_data.get_crypto_bars, req)
            else:
                req  = StockBarsRequest(symbol_or_symbols=sym,
                                        timeframe=TimeFrame.Hour, limit=100)
                bars = await asyncio.to_thread(self.stock_data.get_stock_bars, req)

            df = bars.df.reset_index()
            if df.empty:
                return pd.DataFrame()
            df = df.rename(columns={'timestamp': 'time'})
            # Normalise column names (alpaca-py returns symbol-indexed multi-level sometimes)
            df.columns = [c[-1] if isinstance(c, tuple) else c for c in df.columns]
            return df
        except Exception as e:
            log.warning(f"Data fetch {sym}: {e}")
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # ORDER SUBMISSION — FIX #5
    # ------------------------------------------------------------------
    async def submit_entry(self, sym: str, sig: dict, qty: float) -> Optional[str]:
        """
        Submit bracket order: market entry + attached SL + TP.
        Returns order_id string or None.
        """
        if not self.trading:
            # Simulation — fake order ID
            fake_id = f"SIM-{sym}-{int(datetime.utcnow().timestamp())}"
            log.info(f"[SIM] Entry {sym} {sig['d']} qty={qty} @ ~{sig['e']:.4f} | "
                     f"SL={sig['s']:.4f} TP={sig['tgt']:.4f}")
            return fake_id

        try:
            side = OrderSide.BUY if sig['d'] == 'long' else OrderSide.SELL

            # Bracket order: entry + SL + TP in one atomic request
            order_req = MarketOrderRequest(
                symbol         = sym,
                qty            = qty,
                side           = side,
                time_in_force  = TimeInForce.GTC,
                order_class    = OrderClass.BRACKET,
                take_profit    = TakeProfitRequest(limit_price=round(sig['tgt'], 2)),
                stop_loss      = StopLossRequest(stop_price=round(sig['s'], 2))
            )
            order = await asyncio.to_thread(self.trading.submit_order, order_req)
            log.info(f"Order submitted: {order.id} | {sym} {sig['d']} {qty}")
            return str(order.id)
        except Exception as e:
            log.error(f"Order submit failed {sym}: {e}")
            return None

    async def close_position(self, sym: str, reason: str) -> Optional[float]:
        """Close position at market. Returns fill price or None."""
        if not self.trading:
            log.info(f"[SIM] Close {sym} reason={reason}")
            return None
        try:
            req = ClosePositionRequest(percentage='100')
            await asyncio.to_thread(self.trading.close_position, sym, req)
            log.info(f"Position closed: {sym} | {reason}")
            return None   # live fill price comes via stream; we'll re-fetch
        except Exception as e:
            log.error(f"Close failed {sym}: {e}")
            return None

    # ------------------------------------------------------------------
    # GET CURRENT PRICE
    # ------------------------------------------------------------------
    async def get_price(self, sym: str) -> float:
        df = await self.get_bars(sym)
        if df.empty:
            return 0.0
        return float(df['close'].iloc[-1])

    # ------------------------------------------------------------------
    # EXIT MONITOR — FIX #3: check SL/TP/time-stop every cycle
    # ------------------------------------------------------------------
    async def monitor_exits(self):
        open_trades = db.get_open_trades()
        if not open_trades:
            return

        for trade in open_trades:
            sym       = trade['symbol']
            entry     = trade['entry']
            stop      = trade['stop']
            target    = trade['target']
            trade_id  = trade['id']
            ts_open   = datetime.fromisoformat(trade['ts_open'])
            side      = trade['side']

            current_price = await self.get_price(sym)
            if current_price == 0:
                continue

            age_hours = (datetime.utcnow() - ts_open).total_seconds() / 3600
            hit_stop  = (current_price <= stop)  if side == 'long' else (current_price >= stop)
            hit_tp    = (current_price >= target) if side == 'long' else (current_price <= target)
            hit_time  = age_hours >= config.MAX_HOLD_HOURS

            reason = None
            if hit_stop:  reason = "STOP_LOSS"
            elif hit_tp:  reason = "TAKE_PROFIT"
            elif hit_time: reason = "TIME_STOP"

            if reason:
                await self.close_position(sym, reason)
                # PnL calculation
                if side == 'long':
                    pnl_pct = (current_price - entry) / entry
                else:
                    pnl_pct = (entry - current_price) / entry
                pnl = pnl_pct * entry * trade['qty']

                db.close_trade(trade_id, current_price, round(pnl, 4), round(pnl_pct, 6))
                risk.add_pnl(pnl)

                cat  = 'sell' if pnl >= 0 else 'loss'
                sign = '+' if pnl >= 0 else ''
                body = (
                    f"*{sym}* closed\n"
                    f"Reason: {reason}\n"
                    f"Entry: ${entry:.4f} → Exit: ${current_price:.4f}\n"
                    f"PnL: {sign}${pnl:.2f} ({sign}{pnl_pct*100:.2f}%)\n"
                    f"Daily PnL: ${risk.daily_pnl:.2f}"
                )
                await tg.send(body, cat)
                log.info(f"CLOSED {sym} | {reason} | PnL={sign}${pnl:.2f}")

    # ------------------------------------------------------------------
    # ENTRY SCAN
    # ------------------------------------------------------------------
    async def scan_entries(self):
        for sym in config.SYMBOLS:
            try:
                ok, reason = risk.can_trade(sym)
                if not ok:
                    log.debug(f"Skip {sym}: {reason}")
                    continue

                df = await self.get_bars(sym)
                if df.empty or len(df) < 55:
                    log.debug(f"Insufficient data {sym}: {len(df)} bars")
                    continue

                sig = signals.generate(df)
                if not sig or sig['c'] < 0.60:
                    continue

                qty = risk.size(sig['e'], sig['s'])
                if qty <= 0:
                    log.warning(f"Zero qty calculated for {sym}")
                    continue

                log.info(f"SIGNAL {sym} {sig['d'].upper()} | conf={sig['c']} "
                         f"entry={sig['e']:.4f} sl={sig['s']:.4f} tp={sig['tgt']:.4f}")

                order_id = await self.submit_entry(sym, sig, qty)
                if order_id:
                    trade_id = db.open_trade(
                        symbol   = sym,
                        side     = sig['d'],
                        qty      = qty,
                        entry    = sig['e'],
                        stop     = sig['s'],
                        target   = sig['tgt'],
                        strategy = sig['t'],
                        order_id = order_id
                    )
                    is_pct = sig['tgt'] / sig['e'] - 1
                    body = (
                        f"*{sym}* {sig['d'].upper()} entered\n"
                        f"Strategy: {sig['t']} | Conf: {sig['c']*100:.0f}%\n"
                        f"Entry: ${sig['e']:.4f}\n"
                        f"Stop:  ${sig['s']:.4f} (-{abs(sig['e']-sig['s'])/sig['e']*100:.2f}%)\n"
                        f"Target: ${sig['tgt']:.4f} (+{is_pct*100:.2f}%)\n"
                        f"Qty: {qty} | Risk: ${config.CAPITAL*config.RISK:.2f}"
                    )
                    await tg.send(body, 'buy')

                await asyncio.sleep(0.5)   # rate limit buffer
            except Exception as e:
                log.error(f"Scan error {sym}: {e}")

    # ------------------------------------------------------------------
    # SCHEDULED REPORTS — FIX #8
    # ------------------------------------------------------------------
    async def morning_report(self):
        stats = db.get_stats(days=1)
        body  = (
            f"*Morning Report* — {datetime.now().strftime('%a %b %d')}\n"
            f"Capital: ${config.CAPITAL:,.2f}\n"
            f"Yesterday: {stats['total']} trades | "
            f"WR: {stats['wr']}% | PnL: ${stats['pnl']:.2f}\n"
            f"Daily loss limit: ${config.CAPITAL * config.MAX_LOSS_DAY:.2f}\n"
            f"Scanning: {', '.join(config.SYMBOLS)}"
        )
        await tg.send(body, 'morning')

    async def evening_report(self):
        stats = db.get_stats(days=1)
        open_t = db.get_open_trades()
        body   = (
            f"*Evening Recap* — {datetime.now().strftime('%a %b %d')}\n"
            f"Trades today: {stats['total']} | WR: {stats['wr']}%\n"
            f"Day PnL: ${stats['pnl']:.2f}\n"
            f"Open positions: {len(open_t)}\n"
            f"Capital: ${config.CAPITAL:,.2f}"
        )
        await tg.send(body, 'evening')

    async def weekly_report(self):
        stats = db.get_stats(days=7)
        body  = (
            f"*Weekly Bag Report* 💰\n"
            f"7-day trades: {stats['total']} | WR: {stats['wr']}%\n"
            f"Week PnL: ${stats['pnl']:.2f}\n"
            f"Avg trade: {stats['avg']*100:.3f}%\n"
            f"Capital: ${config.CAPITAL:,.2f}\n"
            f"Target hit: {'✅' if stats['pnl'] > config.CAPITAL * config.TARGET_WEEKLY else '🔄'}"
        )
        await tg.send(body, 'weekly')

    # ------------------------------------------------------------------
    # SCHEDULER — FIX #8
    # ------------------------------------------------------------------
    async def schedule_loop(self):
        """Background task for timed reports. Runs independently of scan loop."""
        morning_sent  = None
        evening_sent  = None
        weekly_sent   = None

        while True:
            try:
                now = datetime.now()
                today = now.date()

                # 9:00 AM local — morning report
                if now.hour == 9 and now.minute < 15 and morning_sent != today:
                    await self.morning_report()
                    morning_sent = today

                # 4:05 PM local (market close + 5 min) — evening report
                if now.hour == 16 and now.minute < 20 and evening_sent != today:
                    await self.evening_report()
                    evening_sent = today

                # Friday 5 PM — weekly report
                if now.weekday() == 4 and now.hour == 17 and now.minute < 20 and weekly_sent != today:
                    await self.weekly_report()
                    weekly_sent = today

                await asyncio.sleep(60)   # check every minute
            except Exception as e:
                log.error(f"Scheduler error: {e}")
                await asyncio.sleep(60)

    # ------------------------------------------------------------------
    # MAIN LOOP
    # ------------------------------------------------------------------
    async def run(self):
        log.info("BEAST MODE v5.1 RUNNING")

        # Send startup
        open_t = db.get_open_trades()
        body   = (
            f"*Beast Mode v5.1 Active* 💪\n"
            f"Capital: ${config.CAPITAL:,.2f}\n"
            f"Risk/trade: ${config.CAPITAL * config.RISK:.2f}\n"
            f"Mode: {'🔴 LIVE' if config.LIVE else '📝 PAPER'}\n"
            f"Symbols: {len(config.SYMBOLS)}\n"
            f"Open positions restored: {len(open_t)}"
        )
        await tg.send(body, 'general')

        # Start scheduler in background
        asyncio.create_task(self.schedule_loop())

        while True:
            try:
                self.cycle += 1
                log.info(f"─── Cycle {self.cycle} | "
                         f"{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} ───")

                # 1) Check/exit open positions first
                await self.monitor_exits()

                # 2) Scan for new entries
                await self.scan_entries()

                # 3) Refresh account balance
                if self.trading:
                    try:
                        acct = await asyncio.to_thread(self.trading.get_account)
                        config.CAPITAL = float(acct.portfolio_value)
                    except Exception as e:
                        log.warning(f"Balance refresh failed: {e}")

                log.info(f"Cycle {self.cycle} complete | "
                         f"Capital: ${config.CAPITAL:,.2f} | "
                         f"Sleeping {config.SCAN_INTERVAL}s")
                await asyncio.sleep(config.SCAN_INTERVAL)

            except Exception as e:
                log.error(f"Main loop error: {e}")
                await asyncio.sleep(60)


# ============================================================================
# ENTRY POINT
# ============================================================================
if __name__ == '__main__':
    print("\nStarting Beast Mode v5.1...", flush=True)
    try:
        beast = Beast()
        asyncio.run(beast.run())
    except KeyboardInterrupt:
        print("\nShutdown requested", flush=True)
    except Exception as e:
        print(f"\nFATAL: {e}", flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)
