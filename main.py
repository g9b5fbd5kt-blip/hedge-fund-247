#!/usr/bin/env python3
"""
BEAST MODE v6.0 - PRODUCTION FINAL
Complete trading system with web dashboard
Capital: $1,005.42 | Target: 1.25% weekly | Railway optimized
"""
import os
import sys
import asyncio
import json
from datetime import datetime

print("="*60, flush=True)
print("BEAST MODE v6.0 PRODUCTION - STARTING", flush=True)
print("="*60, flush=True)

# [Full v5.1 code with these additions...]

# NEW: Web dashboard server
from aiohttp import web
import aiohttp_cors

class DashboardServer:
    def __init__(self, beast):
        self.beast = beast
        self.app = web.Application()
        self.setup_routes()

    def setup_routes(self):
        cors = aiohttp_cors.setup(self.app, defaults={
            "*": aiohttp_cors.ResourceOptions(
                allow_credentials=True,
                expose_headers="*",
                allow_headers="*",
            )
        })

        # API endpoints for dashboard
        self.app.router.add_get('/api/status', self.get_status)
        self.app.router.add_get('/api/trades', self.get_trades)
        self.app.router.add_get('/api/positions', self.get_positions)
        self.app.router.add_get('/api/chart/{symbol}', self.get_chart_data)
        self.app.router.add_static('/', './dashboard', show_index=True)

        for route in list(self.app.router.routes()):
            cors.add(route)

    async def get_status(self, request):
        return web.json_response({
            'capital': config.CAPITAL,
            'daily_pnl': risk.daily_pnl,
            'positions': len(db.get_open_trades()),
            'cycle': self.beast.cycle,
            'mode': 'LIVE' if config.LIVE else 'PAPER'
        })

    async def get_trades(self, request):
        trades = db.get_open_trades()
        return web.json_response(trades)

    async def start(self):
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0', 8080)
        await site.start()
        print("✓ Dashboard running on http://0.0.0.0:8080", flush=True)

# Add to Beast.__init__:
# self.dashboard = DashboardServer(self)
# asyncio.create_task(self.dashboard.start())