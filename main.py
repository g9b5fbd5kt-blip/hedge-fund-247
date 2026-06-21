#!/usr/bin/env python3
"""
BEAST MODE v5.0 - WORKING VERSION
With debug logging
"""
import os
import sys
import time
import asyncio

print("="*60, flush=True)
print("BEAST MODE STARTING...", flush=True)
print(f"Time: {time.ctime()}", flush=True)
print(f"Python: {sys.version.split()[0]}", flush=True)
print("="*60, flush=True)

# Test environment
print("\n[1/5] Checking environment...", flush=True)
try:
    APCA_KEY = os.getenv('APCA_API_KEY_ID', '')
    APCA_SECRET = os.getenv('APCA_API_SECRET_k', '')
    TG_TOKEN = os.getenv('TELEGRAM_TOKEN', '')
    TG_CHAT = os.getenv('TELEGRAM_CHAT_ID', '')
    LIVE = os.getenv('LIVE_MODE', 'false').lower() == 'true'

    print(f"✓ APCA_KEY: {APCA_KEY[:8]}..." if APCA_KEY else "✗ No APCA_KEY", flush=True)
    print(f"✓ TG_TOKEN: {TG_TOKEN[:8]}..." if TG_TOKEN else "✗ No TG_TOKEN", flush=True)
    print(f"✓ TG_CHAT: {TG_CHAT}" if TG_CHAT else "✗ No TG_CHAT", flush=True)
    print(f"✓ LIVE_MODE: {LIVE}", flush=True)
except Exception as e:
    print(f"✗ Environment error: {e}", flush=True)
    sys.exit(1)

# Test imports
print("\n[2/5] Testing imports...", flush=True)
try:
    import sqlite3
    print("✓ sqlite3", flush=True)
except Exception as e:
    print(f"✗ sqlite3: {e}", flush=True)

try:
    import pandas as pd
    print(f"✓ pandas {pd.__version__}", flush=True)
except Exception as e:
    print(f"✗ pandas: {e}", flush=True)
    print("Installing pandas...", flush=True)
    os.system("pip install pandas")
    import pandas as pd

try:
    import numpy as np
    print(f"✓ numpy {np.__version__}", flush=True)
except Exception as e:
    print(f"✗ numpy: {e}", flush=True)
    os.system("pip install numpy")
    import numpy as np

try:
    import aiohttp
    print("✓ aiohttp", flush=True)
except Exception as e:
    print(f"✗ aiohttp: {e}", flush=True)
    os.system("pip install aiohttp")
    import aiohttp

ALPACA_AVAILABLE = False
try:
    from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
    from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.trading.client import TradingClient
    ALPACA_AVAILABLE = True
    print("✓ alpaca-py", flush=True)
except Exception as e:
    print(f"⚠ alpaca-py not available: {e}", flush=True)
    print(" Running in simulation mode", flush=True)

print("\n[3/5] Initializing database...", flush=True)
try:
    conn = sqlite3.connect('beast.db', check_same_thread=False)
    conn.execute('''CREATE TABLE IF NOT EXISTS trades
                   (id INTEGER PRIMARY KEY, ts TEXT, symbol TEXT, side TEXT,
                    qty REAL, entry REAL, pnl REAL)''')
    conn.commit()
    print("✓ Database ready", flush=True)
except Exception as e:
    print(f"✗ Database error: {e}", flush=True)

print("\n[4/5] Testing Telegram...", flush=True)
async def send_telegram(msg):
    if not TG_TOKEN or not TG_CHAT:
        print("⚠ Telegram not configured", flush=True)
        return False
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={
                'chat_id': TG_CHAT,
                'text': msg,
                'parse_mode': 'Markdown'
            }) as resp:
                if resp.status == 200:
                    print("✓ Telegram message sent", flush=True)
                    return True
                else:
                    print(f"✗ Telegram failed: {resp.status}", flush=True)
                    return False
    except Exception as e:
        print(f"✗ Telegram error: {e}", flush=True)
        return False

# Send startup message
asyncio.run(send_telegram(
    "🚀 *Beast Mode v5.0 Started*\n\n"
    "Good morning boss ☀️ checking stocks not flipping rocks\n\n"
    f"Capital: $1005.42\n"
    f"Mode: {'LIVE' if LIVE else 'PAPER'}\n"
    f"Time: {time.strftime('%H:%M:%S')}"
))

print("\n[5/5] Starting main loop...", flush=True)
print("="*60, flush=True)
print("BOT IS RUNNING - Check Telegram", flush=True)
print("="*60, flush=True)

# Main trading loop (simplified but working)
async def main_loop():
    cycle = 0
    symbols = ['BTC/USD', 'ETH/USD', 'SOL/USD', 'SPY', 'QQQ', 'TSLA', 'NVDA']

    # Initialize Alpaca if available
    trading_client = None
    if ALPACA_AVAILABLE and APCA_KEY:
        try:
            trading_client = TradingClient(APCA_KEY, APCA_SECRET, paper=not LIVE)
            print("✓ Alpaca connected", flush=True)
        except Exception as e:
            print(f"⚠ Alpaca connection failed: {e}", flush=True)

    while True:
        try:
            cycle += 1
            now = time.strftime('%H:%M:%S')
            print(f"\n[{now}] Cycle {cycle} - Scanning {len(symbols)} symbols...", flush=True)

            # Simulate scanning (replace with real logic later)
            for symbol in symbols[:2]: # Just test with 2 symbols
                print(f" Scanning {symbol}...", flush=True)
                await asyncio.sleep(0.5)

            # Send heartbeat every 10 cycles (every ~2.5 hours)
            if cycle % 10 == 0:
                await send_telegram(
                    f"💓 *Heartbeat*\n\n"
                    f"Cycle: {cycle}\n"
                    f"Status: Running\n"
                    f"Time: {now}\n"
                    f"The hustle is what I know 💪"
                )

            print(f"[{now}] Cycle {cycle} complete. Sleeping 15 min...", flush=True)
            await asyncio.sleep(900) # 15 minutes

        except Exception as e:
            print(f"Error in main loop: {e}", flush=True)
            await asyncio.sleep(60)

# Run forever
try:
    asyncio.run(main_loop())
except KeyboardInterrupt:
    print("\nShutting down...", flush=True)
except Exception as e:
    print(f"\nFatal error: {e}", flush=True)
    import traceback
    traceback.print_exc()