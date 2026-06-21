#!/usr/bin/env python3
import os
import sys
import time

print("="*50)
print("BEAST MODE DEBUG STARTING")
print("="*50)
print(f"Python version: {sys.version}")
print(f"Time: {time.ctime()}")
print()

# Test 1: Environment variables
print("TEST 1: Checking environment variables...")
try:
    key = os.getenv('APCA_API_KEY_ID', 'NOT SET')
    secret = os.getenv('APCA_API_SECRET_k', 'NOT SET')
    token = os.getenv('TELEGRAM_TOKEN', 'NOT SET')
    chat = os.getenv('TELEGRAM_CHAT_ID', 'NOT SET')
    
    print(f"✓ APCA_API_KEY_ID: {key[:5]}..." if key != 'NOT SET' else "✗ APCA_API_KEY_ID: NOT SET")
    print(f"✓ APCA_API_SECRET_k: {secret[:5]}..." if secret != 'NOT SET' else "✗ APCA_API_SECRET_k: NOT SET")
    print(f"✓ TELEGRAM_TOKEN: {token[:5]}..." if token != 'NOT SET' else "✗ TELEGRAM_TOKEN: NOT SET")
    print(f"✓ TELEGRAM_CHAT_ID: {chat}" if chat != 'NOT SET' else "✗ TELEGRAM_CHAT_ID: NOT SET")
    print("TEST 1 PASSED")
except Exception as e:
    print(f"TEST 1 FAILED: {e}")
print()

# Test 2: Imports
print("TEST 2: Testing imports...")
try:
    import sqlite3
    print("✓ sqlite3")
except Exception as e:
    print(f"✗ sqlite3: {e}")

try:
    import pandas as pd
    print(f"✓ pandas {pd.__version__}")
except Exception as e:
    print(f"✗ pandas: {e}")

try:
    import numpy as np
    print(f"✓ numpy {np.__version__}")
except Exception as e:
    print(f"✗ numpy: {e}")

try:
    from alpaca.data.historical import CryptoHistoricalDataClient
    print("✓ alpaca-py")
except Exception as e:
    print(f"✗ alpaca-py: {e}")

try:
    import aiohttp
    print("✓ aiohttp")
except Exception as e:
    print(f"✗ aiohttp: {e}")

print("TEST 2 COMPLETE")
print()

# Test 3: Database
print("TEST 3: Testing database...")
try:
    import sqlite3
    conn = sqlite3.connect('test.db')
    conn.execute('CREATE TABLE IF NOT EXISTS test (id INTEGER)')
    conn.commit()
    conn.close()
    print("✓ Database works")
    print("TEST 3 PASSED")
except Exception as e:
    print(f"TEST 3 FAILED: {e}")
print()

# Test 4: Telegram
print("TEST 4: Testing Telegram...")
try:
    import asyncio
    import aiohttp
    
    async def test_telegram():
        token = os.getenv('TELEGRAM_TOKEN')
        chat = os.getenv('TELEGRAM_CHAT_ID')
        if not token or not chat:
            print("✗ Telegram credentials not set")
            return
        
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={
                'chat_id': chat,
                'text': '🧪 DEBUG TEST - Beast Mode is starting up...'
            }) as resp:
                if resp.status == 200:
                    print("✓ Telegram message sent")
                else:
                    print(f"✗ Telegram failed: {resp.status}")
    
    asyncio.run(test_telegram())
    print("TEST 4 COMPLETE")
except Exception as e:
    print(f"TEST 4 FAILED: {e}")
print()

print("="*50)
print("DEBUG COMPLETE - All tests finished")
print("="*50)
print()
print("If you see this, the container is working.")
print("Check Telegram for test message.")
print()
print("Keeping container alive for 60 seconds...")
time.sleep(60)
print("Exiting.")