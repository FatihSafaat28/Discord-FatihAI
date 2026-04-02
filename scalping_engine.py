import asyncio
import json
import time
import websockets
import requests
from datetime import datetime
import os
from dotenv import load_dotenv

load_dotenv()

class FinnhubWebsocketManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(FinnhubWebsocketManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized: return
        self.api_key = os.getenv("FINNHUB_API_KEY")
        print(f"DEBUG: [Finnhub] WS Manager Initializing. Key Found: {self.api_key is not None}")
        self.uri = f"wss://ws.finnhub.io?token={self.api_key}"
        self.subscribers = {}  # { symbol: [callbacks] }
        self.ws = None
        self._initialized = True
        self.is_running = False

    async def start(self):
        """Start the background websocket connection task."""
        if self.is_running: return
        print(f"DEBUG: [Finnhub] Starting WS Manager Loop...")
        self.is_running = True
        asyncio.create_task(self._run())

    async def _run(self):
        """Internal run loop for websocket."""
        print(f"DEBUG: [Finnhub] WS Thread started. URI: {self.uri[:20]}...")
        while self.is_running:
            try:
                async with websockets.connect(self.uri) as ws:
                    self.ws = ws
                    print(f"✅ [Finnhub] Connected to websocket.")
                    
                    # Re-subscribe to all active symbols on reconnect
                    for symbol in self.subscribers.keys():
                        print(f"DEBUG: [Finnhub] Re-subscribing to {symbol}")
                        await self.ws.send(json.dumps({"type": "subscribe", "symbol": symbol}))

                    async for message in ws:
                        data = json.loads(message)
                        if data.get("type") == "trade":
                            trades = data.get("data", [])
                            for trade in trades:
                                symbol = trade.get("s")
                                price = trade.get("p")
                                if symbol in self.subscribers:
                                    # print(f"DEBUG: [Finnhub] Price update: {symbol} -> {price}")
                                    for callback in self.subscribers[symbol]:
                                        asyncio.create_task(callback(price))
                        elif data.get("type") == "error":
                            print(f"❌ [Finnhub] WS Error: {data.get('msg')}")
                            if "API key" in data.get('msg', ''):
                                print("   💡 Check your FINNHUB_API_KEY in .env!")

            except Exception as e:
                print(f"⚠️ [Finnhub] WS Connection lost: {e}. Reconnecting in 5s...")
                await asyncio.sleep(5)

    async def subscribe(self, symbol, callback):
        """Subscribe to a symbol with a callback."""
        if symbol not in self.subscribers:
            self.subscribers[symbol] = []
            print(f"DEBUG: [Finnhub] Registering new subscriber for {symbol}")
            if self.ws:
                try:
                    is_closed = getattr(self.ws, 'closed', False)
                    if not is_closed:
                        print(f"DEBUG: [Finnhub] Sending WS subscribe for {symbol}")
                        await self.ws.send(json.dumps({"type": "subscribe", "symbol": symbol}))
                    else:
                        print(f"DEBUG: [Finnhub] WS is closed, skipping immediate subscribe for {symbol}")
                except Exception as e:
                    print(f"⚠️ [Finnhub] Failed to send subscribe for {symbol}: {e}")
            else:
                print(f"DEBUG: [Finnhub] WS not connected yet, {symbol} will subscribe on connect.")
        
        self.subscribers[symbol].append(callback)
        print(f"🔔 [Finnhub] Subscribed to {symbol}")

    async def unsubscribe(self, symbol, callback):
        """Unsubscribe from a symbol."""
        if symbol in self.subscribers:
            if callback in self.subscribers[symbol]:
                self.subscribers[symbol].remove(callback)
            
            if not self.subscribers[symbol]:
                del self.subscribers[symbol]
                if self.ws:
                    try:
                        is_closed = getattr(self.ws, 'closed', False)
                        if not is_closed:
                            await self.ws.send(json.dumps({"type": "unsubscribe", "symbol": symbol}))
                    except Exception as e:
                        print(f"⚠️ [Finnhub] Failed to send unsubscribe for {symbol}: {e}")
                print(f"🔕 [Finnhub] Unsubscribed from {symbol}")

class ScalpingSession:
    def __init__(self, user, ticker, discord_client, manager, groq_client, gemini_manager):
        self.user = user
        self.ticker = ticker
        self.client = discord_client
        self.manager = manager
        self.groq_client = groq_client
        self.gemini_manager = gemini_manager
        self.balance = 100_000_000  # IDR
        self.start_time = time.time()
        self.duration = 30 * 60  # 30 minutes
        self.price_buffer = []  # Last 5 mins of prices
        self.current_price = None
        self.is_active = True
        
        # Trade State
        self.position = None  # { "buy_price": float, "tp": float, "sl": float, "amount": float, "why": str }
        self.last_analysis_time = 0
        self.analysis_interval = 5 * 60  # 5 minutes

    async def start(self):
        """Start the session."""
        await self.manager.subscribe(self.ticker, self.on_price_update)
        
        # Start periodic background loop
        asyncio.create_task(self._session_loop())
        print(f"🚀 [Session] Started for {self.user.name} on {self.ticker}")

        # Wait a moment for price data to arrive before first analysis
        await asyncio.sleep(2) 
        
        # Initial analysis for channel response
        self.last_analysis_time = time.time()
        return await self.run_ai_analysis()

    async def stop(self):
        """Stop the session."""
        self.is_active = False
        await self.manager.unsubscribe(self.ticker, self.on_price_update)
        print(f"🛑 [Session] Stopped for {self.user.name}")

    async def on_price_update(self, price):
        """Callback for price ticks."""
        if not self.is_active: return
        
        self.current_price = price
        self.price_buffer.append({"price": price, "time": time.time()})
        
        # Keep only last 10 mins of buffer
        cutoff = time.time() - 600
        self.price_buffer = [p for p in self.price_buffer if p["time"] > cutoff]

        # Check TP/SL if in position
        if self.position:
            await self._check_signals()

    async def _session_loop(self):
        """Background loop for timing and analysis."""
        while self.is_active:
            now = time.time()
            
            # Check if 30 mins limit reached
            if now - self.start_time >= self.duration:
                await self._notify_end()
                await self.stop()
                break

            # Run AI Analysis every 5 mins
            if now - self.last_analysis_time >= self.analysis_interval:
                await self.run_ai_analysis()
                self.last_analysis_time = now

            await asyncio.sleep(1)

    async def run_ai_analysis(self, is_re_analyze=False):
        """Call AI to get trade recommendations."""
        print(f"DEBUG: run_ai_analysis called. Current price: {self.current_price}")
        if not self.current_price: 
            return "⏳ Belum ada data harga masuk, Boss. Mohon tunggu sebentar sampai data bursa masuk..."
        
        status_msg = "Sedang melakukan analisis ulang..." if is_re_analyze else "Sedang melakukan analisis pasar untuk 5 menit ke depan..."
        try:
            # We only send status to DM if it's a re-analysis or periodic
            if is_re_analyze:
                await self.user.send(f"🔍 **[{self.ticker}]** {status_msg}")
        except Exception as e:
            print(f"DEBUG: Failed to send status to user: {e}")

        # Prepare price data history for prompt
        recent_prices = [p["price"] for p in self.price_buffer[-100:]]
        price_str = ", ".join([str(p) for p in recent_prices]) if recent_prices else str(self.current_price)

        prompt = f"""
Sistem Scalping Demo IDR.
Ticker: {self.ticker}
Harga Saat Ini: {self.current_price}
History Harga (terakhir): [{price_str}]
Balance User: Rp {self.balance:,.0f} IDR

Tugas: Berikan rekomendasi Buy, Take Profit (TP), dan Stop Loss (SL) untuk 5 menit ke depan.
Tentukan juga 'Amount' (jumlah uang IDR) yang harus digunakan untuk trade ini (Smart Risk Management).
Berikan penjelasan singkat 'Mengapa' dalam Bahasa Indonesia yang ramah.

Format output JSON:
{{
  "recommendation": "BUY" atau "WAIT",
  "buy_price": {self.current_price},
  "tp": harga_tp,
  "sl": harga_sl,
  "amount": jumlah_idr,
  "why": "penjelasan singkat"
}}
        """

        try:
            print(f"DEBUG: Calling AI for {self.ticker}...")
            ai_data = await self._call_ai(prompt)
            print(f"DEBUG: AI response: {ai_data}")
            msg = ""
            if ai_data and ai_data.get("recommendation") == "BUY":
                # Execute simulated buy
                self.position = {
                    "buy_price": ai_data.get("buy_price", self.current_price),
                    "tp": ai_data.get("tp"),
                    "sl": ai_data.get("sl"),
                    "amount": ai_data.get("amount", 10_000_000),
                    "why": ai_data.get("why", "Analisis teknis mendukung.")
                }
                
                # Update balance (simulated lock)
                self.balance -= self.position["amount"]
                
                msg = f"🟢 **BUY SIGNAL!** \n"
                msg += f"Ticker: `{self.ticker}`\n"
                msg += f"Harga Beli: `Rp {self.position['buy_price']:,.2f}`\n"
                msg += f"Take Profit: `Rp {self.position['tp']:,.2f}`\n"
                msg += f"Stop Loss: `Rp {self.position['sl']:,.2f}`\n"
                msg += f"Modal Trade: `Rp {self.position['amount']:,.0f}`\n"
                msg += f"Sisa Saldo: `Rp {self.balance:,.0f}`\n\n"
                msg += f"💡 **Analogi AI**: {self.position['why']}"
            else:
                msg = f"🟡 **WAIT**: AI menyarankan untuk menunggu momen yang tepat. \n💡 {ai_data.get('why', 'Pasar sedang konsolidasi atau data belum cukup.') if ai_data else 'AI tidak memberikan respon valid.'}"
            
            # Always send to DM for any analysis (re-analysis or periodic)
            await self.user.send(msg)
            return msg
        
        except Exception as e:
            print(f"❌ AI Error in run_ai_analysis: {e}")
            err_msg = f"⚠️ Maaf Boss, terjadi error saat analisis AI: {e}"
            try: await self.user.send(err_msg)
            except: pass
            return err_msg

    async def _call_ai(self, prompt):
        """Helper to call Gemini (including auto-fallbacks) with JSON response."""
        if hasattr(self, 'gemini_manager') and self.gemini_manager:
            try:
                print(f"DEBUG: [Scalping AI] Requesting analysis from Gemini Manager...")
                # GeminiManager.generate_analysis internally handles fallback across all its models
                res_tuple = await asyncio.to_thread(self.gemini_manager.generate_analysis, prompt)
                res_content, label = res_tuple
                
                if res_content:
                    print(f"DEBUG: [Scalping AI] Success using {label}")
                    # Clean up common AI markdown artifacts
                    res_content = res_content.replace("```json", "").replace("```", "").strip()
                    return json.loads(res_content)
            except Exception as e:
                print(f"  ❌ [Scalping AI] All Gemini models failed or JSON error: {e}")

        print("❌ [Scalping AI] Final failure: No AI response could be generated.")
        return None

    async def _check_signals(self):
        """Real-time monitoring of TP/SL."""
        # Capture current state to avoid race conditions from fast price updates
        pos = self.position
        price = self.current_price
        
        if not pos or not price: 
            return
        
        # Profit hit
        if price >= pos["tp"]:
            self.position = None # Nullify immediately before awaiting
            profit = pos["amount"] * (price / pos["buy_price"])
            self.balance += profit
            
            msg = f"✅ **TAKE PROFIT HIT!** \n"
            msg += f"Ticker: `{self.ticker}`\n"
            msg += f"Harga Jual: `Rp {price:,.2f}`\n"
            msg += f"Hasil: `+Rp {profit - pos['amount']:,.0f}` ✨\n"
            msg += f"Saldo Baru: `Rp {self.balance:,.0f}`"
            try: await self.user.send(msg)
            except: pass
            return

        # Loss hit
        elif price <= pos["sl"]:
            self.position = None # Nullify immediately before awaiting
            loss_rem = pos["amount"] * (price / pos["buy_price"])
            self.balance += loss_rem
            
            msg = f"🔴 **STOP LOSS HIT!** \n"
            msg += f"Ticker: `{self.ticker}`\n"
            msg += f"Harga Jual (Minus): `Rp {price:,.2f}`\n"
            msg += f"Hasil: `-Rp {pos['amount'] - loss_rem:,.0f}` 💀\n"
            msg += f"Saldo Baru: `Rp {self.balance:,.0f}`"
            try: await self.user.send(msg)
            except: pass
            
            # Handle rugi: Immediate re-analysis
            await self.run_ai_analysis(is_re_analyze=True)
            return

    async def _notify_end(self):
        """Notify user that 30 mins session is over."""
        msg = f"⏳ **Sesi Scalping 30 Menit Selesai!**\n"
        msg += f"Ticker: `{self.ticker}`\n"
        msg += f"Saldo Akhir: `Rp {self.balance:,.0f}`\n\n"
        msg += f"Apa Boss mau lanjut atau quit?\n"
        msg += f"- Ketik `!scalping {self.ticker}` untuk mulai sesi baru.\n"
        msg += f"- Atau ketik kode saham lain."
        await self.user.send(msg)

# Global Manager Instance
scalping_ws_manager = FinnhubWebsocketManager()
active_scalping_sessions = {} # { user_id: ScalpingSession }
