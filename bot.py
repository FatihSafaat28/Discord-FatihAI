import discord
import time
import os
import json
import asyncio
import re
import random
import requests as http_requests
import yfinance as yf
from datetime import datetime
import pytz

JAKARTA_TZ = pytz.timezone('Asia/Jakarta')
from groq import Groq
from ddgs import DDGS
from dotenv import load_dotenv
from discord.ext import tasks

# ==========================================
# 1. KUNCI RAHASIA (dari .env)
# ==========================================
load_dotenv()
GROQ_API_KEY = os.getenv('GROQ_API_KEY')
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
TAVILY_API_KEY = os.getenv('TAVILY_API_KEY')
SERPER_API_KEY = os.getenv('SERPER_API_KEY')
WATCHLIST_CHANNEL_ID = os.getenv('WATCHLIST_CHANNEL_ID')
ALERT_CHANNEL_ID = os.getenv('ALERT_CHANNEL_ID')
NEWS_CHANNEL_ID = os.getenv('NEWS_CHANNEL_ID')

# ==========================================
# 2. PERSIAPAN AI (GROQ) + AUTO MODEL FALLBACK
# ==========================================
groq_client = Groq(api_key=GROQ_API_KEY)

# Daftar model — urutan = prioritas fallback (dari terbaik ke paling hemat)
# Auto-switch jika sisa RPD atau TPM < threshold
MODEL_CONFIGS = [
    {"name": "llama-3.3-70b-versatile",                    "label": "Llama 3.3 70B ⭐"},
    {"name": "llama-3.1-8b-instant",                       "label": "Llama 3.1 8B �"},
    {"name": "meta-llama/llama-4-scout-17b-16e-instruct",  "label": "Llama 4 Scout 🚀"},
    {"name": "qwen/qwen3-32b",                             "label": "Qwen3 32B 💎"},
    {"name": "openai/gpt-oss-120b",                        "label": "GPT OSS 120B 🧠"},
    {"name": "openai/gpt-oss-20b",                         "label": "GPT OSS 20B ⚡"},
]

# Threshold: switch model jika sisa kuota < 20%
FALLBACK_THRESHOLD = 0.2

# Pengelompokan Model untuk ROUTING
# !bro akan menggunakan model selain Llama untuk menghemat biaya
GENERAL_MODELS = [
    "meta-llama/llama-4-scout-17b-16e-instruct", 
    "qwen/qwen3-32b", 
    "openai/gpt-oss-120b", 
    "openai/gpt-oss-20b"
]

class ModelManager:
    """
    Manager yang menggunakan data REAL dari Groq response headers.
    Headers yang digunakan (dari docs.groq.com):
    - x-ratelimit-limit-requests     → RPD limit
    - x-ratelimit-remaining-requests → RPD remaining
    - x-ratelimit-limit-tokens       → TPM limit
    - x-ratelimit-remaining-tokens   → TPM remaining
    """
    def __init__(self, models):
        self.models = models
        self.data = {}
        for m in models:
            self.data[m["name"]] = {
                "rpd_limit": None,       # x-ratelimit-limit-requests
                "rpd_remaining": None,   # x-ratelimit-remaining-requests
                "tpm_limit": None,       # x-ratelimit-limit-tokens
                "tpm_remaining": None,   # x-ratelimit-remaining-tokens
                "last_tokens": 0,        # Token terakhir yang digunakan
            }

    def update_from_headers(self, model_name, raw_headers):
        """Update data dari Groq response headers."""
        headers = {k.lower(): v for k, v in raw_headers.items()}
        d = self.data[model_name]

        val = headers.get("x-ratelimit-limit-requests")
        if val is not None: d["rpd_limit"] = int(val)

        val = headers.get("x-ratelimit-remaining-requests")
        if val is not None: d["rpd_remaining"] = int(val)

        val = headers.get("x-ratelimit-limit-tokens")
        if val is not None: d["tpm_limit"] = int(val)

        val = headers.get("x-ratelimit-remaining-tokens")
        if val is not None: d["tpm_remaining"] = int(val)

    def is_near_limit(self, model_name):
        """
        Cek apakah model mendekati limit.
        True jika sisa RPD atau TPM < 20% dari limit.
        Jika belum ada data (belum pernah request), anggap belum limit.
        """
        d = self.data[model_name]

        # Belum ada data → belum tahu, anggap aman
        if d["rpd_limit"] is None:
            return False

        # Cek RPD: sisa request harian
        if d["rpd_remaining"] is not None and d["rpd_limit"] > 0:
            rpd_ratio = d["rpd_remaining"] / d["rpd_limit"]
            if rpd_ratio < FALLBACK_THRESHOLD:
                return True

        # Cek TPM: sisa token per menit
        if d["tpm_remaining"] is not None and d["tpm_limit"] > 0:
            tpm_ratio = d["tpm_remaining"] / d["tpm_limit"]
            if tpm_ratio < FALLBACK_THRESHOLD:
                return True

        return False

    def get_best_model(self):
        """
        Pilih model terbaik yang masih tersedia.
        Prioritas: model utama (70B) > fallback (8B).
        """
        for model in self.models:
            if not self.is_near_limit(model["name"]):
                return model
        # Semua mendekati limit → pakai fallback
        return self.models[-1]

# Inisialisasi Model Manager
model_manager = ModelManager(MODEL_CONFIGS)

# ==========================================
# PERSONA FATIH AI
# ==========================================
FATIH_AI_PERSONA = """
Kamu adalah FatihAI, asisten AI laki-laki yang ramah dan sopan. Panggil pengguna "Boss".

Aturan:
1. Jawab dalam Bahasa Indonesia yang baik. Gunakan emoji secukupnya 😊
2. Jawab RINGKAS & PADAT (maks ~1500 karakter). Jika topik luas, beri ringkasan lalu tanya "Mau saya jelaskan lebih lanjut, Boss?"
3. Format Discord Markdown: **tebal** untuk hal penting, `- ` untuk list (1 item per baris), baris kosong antar bagian.
4. Jika ada KONTEKS PENCARIAN WEB, WAJIB gunakan data tersebut sebagai sumber utama. JANGAN buat jawaban sendiri jika data pencarian sudah tersedia.
5. Jika data pencarian berisi jadwal/tanggal/waktu/harga, WAJIB sajikan dalam format terstruktur (list atau tabel). Tampilkan data spesifik: tanggal, waktu, nama, lokasi, dll. JANGAN berikan jawaban umum atau generik.
6. JANGAN tambahkan disclaimer seperti "jadwal bisa berubah", "saya tidak yakin", "mungkin berbeda". Cukup sajikan data apa adanya dari hasil pencarian.
7. Jika ditanya siapa kamu, perkenalkan diri sebagai FatihAI.
8. Jujur jika tidak tahu jawabannya.
"""

# ==========================================
# 3. RATE LIMITER (PEMBATAS REQUEST)
# ==========================================
MAX_REQUESTS_PER_MINUTE = 5
COOLDOWN_SECONDS = 60

# Dictionary untuk menyimpan waktu request per user
# Format: { user_id: [timestamp1, timestamp2, ...] }
user_request_timestamps = {}

def check_rate_limit(user_id):
    """
    Cek apakah user sudah melebihi batas request.
    Return (is_allowed, remaining_seconds)
    """
    now = time.time()

    if user_id not in user_request_timestamps:
        user_request_timestamps[user_id] = []

    # Bersihkan timestamp yang sudah lebih dari 60 detik
    user_request_timestamps[user_id] = [
        ts for ts in user_request_timestamps[user_id]
        if now - ts < COOLDOWN_SECONDS
    ]

    # Cek apakah sudah mencapai batas
    if len(user_request_timestamps[user_id]) >= MAX_REQUESTS_PER_MINUTE:
        oldest_timestamp = user_request_timestamps[user_id][0]
        remaining = int(COOLDOWN_SECONDS - (now - oldest_timestamp)) + 1
        return False, remaining

    user_request_timestamps[user_id].append(now)
    return True, 0

# ==========================================
# MEMORY (INGATAN PERCAKAPAN)
# ==========================================
MEMORY_DURATION_SECONDS = 60  # 1 menit

# Dictionary untuk menyimpan riwayat chat per user
# Format: { user_id: { "last_time": timestamp, "history": [{"role": ..., "content": ...}, ...] } }
user_chat_memory = {}

def get_chat_history(user_id):
    """
    Ambil riwayat chat user. Jika sudah lebih dari 5 menit sejak
    pesan terakhir, reset memori dan mulai percakapan baru.
    """
    now = time.time()

    if user_id in user_chat_memory:
        last_time = user_chat_memory[user_id]["last_time"]
        if now - last_time > MEMORY_DURATION_SECONDS:
            user_chat_memory[user_id] = {"last_time": now, "history": []}
    else:
        user_chat_memory[user_id] = {"last_time": now, "history": []}

    return user_chat_memory[user_id]["history"]

def add_to_memory(user_id, user_message, ai_response):
    """Simpan pesan user dan balasan AI ke dalam memori."""
    now = time.time()

    if user_id not in user_chat_memory:
        user_chat_memory[user_id] = {"last_time": now, "history": []}

    memory = user_chat_memory[user_id]
    memory["last_time"] = now

    memory["history"].append({"role": "user", "content": user_message})
    memory["history"].append({"role": "assistant", "content": ai_response})

    # Batasi memori (maks 6 pesan = 3 percakapan)
    if len(memory["history"]) > 6:
        memory["history"] = memory["history"][-6:]

# ==========================================
# PORTFOLIO SYSTEM (Local Memory)
# ==========================================
# Format: { user_id: { "TICKER": buy_price, ... } }
user_portfolios = {}

# ==========================================
# MULTI-SEARCH SYSTEM (Tavily → Serper → DuckDuckGo)
# ==========================================
class SearchManager:
    """
    Multi-search dengan auto-fallback.
    Urutan: Tavily → Serper → DuckDuckGo (unlimited)
    """
    def __init__(self):
        self.providers = []
        self.last_provider = None

        # 1. Tavily (1,000 credits/month)
        if TAVILY_API_KEY:
            self.providers.append({
                "name": "Tavily 🔍",
                "credits": "1,000/bulan",
                "remaining": None,
                "active": True,
                "search_fn": self._search_tavily,
            })

        # 2. Serper (2,500 credits)
        if SERPER_API_KEY:
            self.providers.append({
                "name": "Serper 🌐",
                "credits": "2,500 total",
                "remaining": None,
                "active": True,
                "search_fn": self._search_serper,
            })

        # 3. DuckDuckGo (unlimited, always last)
        self.providers.append({
            "name": "DuckDuckGo 🦆",
            "credits": "Unlimited",
            "remaining": "∞",
            "active": True,
            "search_fn": self._search_duckduckgo,
        })

    def search(self, query, max_results=5):
        """
        Coba setiap provider berurutan.
        Return: (search_text, provider_name) atau (None, None)
        """
        for provider in self.providers:
            if not provider["active"]:
                continue
            try:
                result = provider["search_fn"](query, max_results)
                if result:
                    self.last_provider = provider["name"]
                    print(f"  ✅ [{provider['name']}] Berhasil | Sisa: {provider['remaining']}")
                    return result, provider["name"]
                else:
                    print(f"  ⚠️ [{provider['name']}] Tidak ada hasil, coba provider berikutnya...")
            except Exception as e:
                print(f"  ❌ [{provider['name']}] Error: {e}, coba provider berikutnya...")
                # Jika error (termasuk kredit habis), nonaktifkan sementara
                if "credit" in str(e).lower() or "limit" in str(e).lower() or "402" in str(e) or "429" in str(e):
                    provider["active"] = False
                    provider["remaining"] = 0
                    print(f"  🚫 [{provider['name']}] Kredit habis! Dinonaktifkan.")

        return None, None

    def _search_tavily(self, query, max_results=5):
        """Cari via Tavily API."""
        resp = http_requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": TAVILY_API_KEY,
                "query": query,
                "max_results": max_results,
                "search_depth": "basic",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        results = data.get("results", [])
        if not results:
            return None

        # Cek sisa kredit Tavily via /usage endpoint
        try:
            usage_resp = http_requests.get(
                "https://api.tavily.com/usage",
                headers={"Authorization": f"Bearer {TAVILY_API_KEY}"},
                timeout=5,
            )
            if usage_resp.status_code == 200:
                usage_data = usage_resp.json()
                account = usage_data.get("account", {})
                plan_limit = account.get("plan_limit")
                plan_usage = account.get("plan_usage", 0)
                if plan_limit is not None:
                    remaining = plan_limit - plan_usage
                    self._update_remaining("Tavily 🔍", remaining)
        except Exception:
            pass

        search_text = ""
        for i, r in enumerate(results, 1):
            title = r.get("title", "")
            content = r.get("content", "")
            url = r.get("url", "")
            search_text += f"{i}. **{title}**\n   {content}\n   Sumber: {url}\n\n"

        return search_text

    def _search_serper(self, query, max_results=5):
        """Cari via Serper (Google SERP) API."""
        resp = http_requests.post(
            "https://google.serper.dev/search",
            headers={
                "X-API-KEY": SERPER_API_KEY,
                "Content-Type": "application/json",
            },
            json={"q": query, "num": max_results},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        # Update credits dari response headers
        credits_remaining = resp.headers.get("X-Credits-Remaining") or resp.headers.get("x-credits-remaining")
        if credits_remaining is not None:
            self._update_remaining("Serper 🌐", int(credits_remaining))

        results = data.get("organic", [])
        if not results:
            return None

        search_text = ""
        for i, r in enumerate(results[:max_results], 1):
            title = r.get("title", "")
            snippet = r.get("snippet", "")
            link = r.get("link", "")
            search_text += f"{i}. **{title}**\n   {snippet}\n   Sumber: {link}\n\n"

        return search_text

    def _search_duckduckgo(self, query, max_results=5):
        """Cari via DuckDuckGo (fallback, unlimited)."""
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))

        if not results:
            return None

        search_text = ""
        for i, r in enumerate(results, 1):
            search_text += f"{i}. **{r['title']}**\n"
            search_text += f"   {r['body']}\n"
            search_text += f"   Sumber: {r['href']}\n\n"

        return search_text

    def _update_remaining(self, provider_name, remaining):
        """Update sisa credits untuk provider."""
        for p in self.providers:
            if p["name"] == provider_name:
                p["remaining"] = remaining
                if isinstance(remaining, (int, float)) and remaining <= 0:
                    p["active"] = False
                break

    def get_status(self):
        """Dapatkan status semua search providers."""
        lines = []
        for p in self.providers:
            status_icon = "🟢" if p["active"] else "🔴"
            remaining = p['remaining'] if p['remaining'] is not None else '-'
            lines.append(f"{status_icon} **{p['name']}** | Sisa: `{remaining}` | Limit: {p['credits']}")
        return "\n".join(lines)

# Inisialisasi Search Manager
search_manager = SearchManager()

# ==========================================
# SAHAM SYSTEM (Watchlist + Analisa + Signal Alert)
# ==========================================
SCAN_INTERVAL_MINUTES = 5
WATCHLIST_CACHE_MINUTES = 30

# 20 Saham CORE (selalu di-scan)
IDX_CORE_STOCKS = [
    "BBCA", "BBRI", "BMRI", "BBNI",  # Perbankan
    "TLKM", "ASII", "UNVR", "ICBP",  # Blue chip
    "GOTO", "BREN", "AMMN", "ADRO",  # Trending
    "PANI", "CPIN", "MDKA", "INDF",  # LQ45
    "SMGR", "KLBF", "EXCL", "ANTM",  # Industri
]

# Pool LQ45 + saham populer untuk padding dinamis
IDX_LQ45_POOL = [
    "ACES", "AKRA", "AMRT", "ARTO", "BBTN",
    "BFIN", "BRPT", "BUKA", "CTRA", "EMTK",
    "ESSA", "GGRM", "HRUM", "INKP", "INTP",
    "ITMG", "JPFA", "JSMR", "MAPI", "MBMA",
    "MEDC", "MIKA", "MNCN", "PGEO", "PGAS",
    "PTBA", "PTPP", "SCMA", "SIDO", "SRTG",
    "TBIG", "TINS", "TKIM", "TPIA", "UNTR",
    "WIKA", "WMUU", "WSKT",
]

def format_rupiah(value):
    """Format angka ke Rupiah yang mudah dibaca."""
    if value is None: return "N/A"
    if value >= 1e12: return f"Rp {value/1e12:.1f}T"
    if value >= 1e9: return f"Rp {value/1e9:.1f}B"
    if value >= 1e6: return f"Rp {value/1e6:.1f}M"
    return f"Rp {value:,.0f}"

def format_volume(value):
    """Format volume ke angka yang mudah dibaca."""
    if value is None: return "N/A"
    if value >= 1e9: return f"{value/1e9:.1f}B"
    if value >= 1e6: return f"{value/1e6:.1f}M"
    if value >= 1e3: return f"{value/1e3:.1f}K"
    return str(int(value))

def calculate_rsi(closes, period=14):
    """Hitung RSI-14."""
    if len(closes) < period + 1: return None
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    recent = deltas[-period:]
    gains = [d if d > 0 else 0 for d in recent]
    losses = [-d if d < 0 else 0 for d in recent]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0: return 100.0
    return round(100 - (100 / (1 + avg_gain / avg_loss)), 1)

def calculate_macd(closes, fast=12, slow=26, sig=9):
    """Hitung MACD. Return (macd, signal, prev_macd, prev_signal)."""
    if len(closes) < slow + sig: return None, None, None, None
    def ema(values, p):
        m = 2 / (p + 1)
        r = [values[0]]
        for v in values[1:]: r.append((v - r[-1]) * m + r[-1])
        return r
    ef, es = ema(closes, fast), ema(closes, slow)
    ml = [f - s for f, s in zip(ef, es)]
    sl = ema(ml, sig)
    return ml[-1], sl[-1], ml[-2], sl[-2]

class SahamManager:
    """Manager untuk semua fitur saham."""

    def __init__(self, search_mgr, groq, model_mgr):
        self.search_manager = search_mgr
        self.groq_client = groq
        self.model_manager = model_mgr
        self.watchlist_cache = None
        self.watchlist_cache_time = 0
        self.alerted_stocks = {}
        self.prev_prices = {}
        self.detail_cache = {}  # { ticker: { 'time': timestamp, 'result': (...) } }

    def _fetch_stock_data(self, ticker_code):
        """Fetch data lengkap untuk satu saham."""
        try:
            t = yf.Ticker(f"{ticker_code}.JK")
            info = t.info
            if not info:
                return None
            # Coba currentPrice, fallback ke regularMarketPrice
            cur_price = info.get('currentPrice') or info.get('regularMarketPrice')
            if not cur_price:
                return None
            hist = t.history(period="1mo")
            closes = list(hist['Close'].values) if len(hist) > 0 else []
            rsi = calculate_rsi(closes) if closes else None
            macd_vals = calculate_macd(closes) if closes else (None, None, None, None)
            return {
                "ticker": ticker_code,
                "name": info.get("shortName", ticker_code),
                "sector": info.get("sector", "N/A"),
                "current_price": cur_price,
                "prev_close": info.get("previousClose"),
                "open": info.get("open"),
                "day_high": info.get("dayHigh"),
                "day_low": info.get("dayLow"),
                "volume": info.get("volume"),
                "avg_volume": info.get("averageVolume"),
                "market_cap": info.get("marketCap"),
                "pe_ratio": info.get("trailingPE"),
                "forward_pe": info.get("forwardPE"),
                "dividend_yield": info.get("dividendYield"),
                "roe": info.get("returnOnEquity"),
                "profit_margin": info.get("profitMargins"),
                "revenue_growth": info.get("revenueGrowth"),
                "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
                "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
                "ma50": info.get("fiftyDayAverage"),
                "ma200": info.get("twoHundredDayAverage"),
                "recommendation": info.get("recommendationKey"),
                "target_price": info.get("targetMeanPrice"),
                "analyst_count": info.get("numberOfAnalystOpinions"),
                "rsi": rsi,
                "macd": macd_vals[0], "macd_signal": macd_vals[1],
                "prev_macd": macd_vals[2], "prev_signal": macd_vals[3],
            }
        except Exception as e:
            print(f"  ❌ Error fetch {ticker_code}: {e}")
            return None

    def _calculate_signals(self, data):
        """Hitung signal score. Pure math, 0 token."""
        score = 0
        signals = []
        price = data.get("current_price")
        prev = data.get("prev_close")

        # 1. Volume Spike
        vol, avg_vol = data.get("volume"), data.get("avg_volume")
        if vol and avg_vol and avg_vol > 0:
            r = vol / avg_vol
            if r > 2.0:
                score += 1
                signals.append(f"✅ Volume Spike    → {r:.1f}x rata-rata {'⬆️' * min(int(r), 3)}")
            else:
                signals.append(f"❌ Volume          → {r:.1f}x (trigger: >2x)")

        # 2. Big Mover
        if price and prev and prev > 0:
            pct = (price - prev) / prev * 100
            if abs(pct) > 4:
                score += 1
                signals.append(f"✅ Big Mover       → {pct:+.2f}% {'🚀' if pct > 0 else '💥'}")
            else:
                signals.append(f"❌ Perubahan       → {pct:+.2f}% (trigger: >±4%)")

        # 3. RSI
        rsi = data.get("rsi")
        if rsi is not None:
            if rsi < 30:
                score += 1
                signals.append(f"✅ RSI Oversold    → {rsi} (trigger: <30)")
            elif rsi > 70:
                score += 1
                signals.append(f"✅ RSI Overbought  → {rsi} ⚠️ (trigger: >70)")
            else:
                signals.append(f"❌ RSI             → {rsi} (zona netral)")

        # 4. MACD Cross
        m, ms, pm, ps = data.get("macd"), data.get("macd_signal"), data.get("prev_macd"), data.get("prev_signal")
        if all(v is not None for v in [m, ms, pm, ps]):
            if pm <= ps and m > ms:
                score += 1
                signals.append(f"✅ MACD Cross      → Bullish crossover ↗️")
            elif pm >= ps and m < ms:
                score += 1
                signals.append(f"✅ MACD Cross      → Bearish crossover ↘️")
            else:
                signals.append(f"❌ MACD            → Belum crossover")

        # 5. MA-50 Cross
        ma50 = data.get("ma50")
        prev_price = self.prev_prices.get(data["ticker"])
        if price and ma50 and prev_price:
            if prev_price < ma50 and price > ma50:
                score += 1
                signals.append(f"✅ MA-50 Cross Up  → Harga tembus MA-50 ↗️")
            elif prev_price > ma50 and price < ma50:
                score += 1
                signals.append(f"✅ MA-50 Cross Dn  → Harga jatuh di bawah MA-50 ↘️")
            else:
                ab = "di atas" if price > ma50 else "di bawah"
                signals.append(f"❌ MA-50           → Harga {ab} MA-50")

        # 6. Near 52w High
        h52 = data.get("fifty_two_week_high")
        if price and h52 and h52 > 0:
            r = price / h52
            if r >= 0.95:
                score += 1
                signals.append(f"✅ Near 52w High   → {r*100:.0f}% dari Rp {h52:,.0f} 🔥")
            else:
                signals.append(f"❌ 52w High        → {r*100:.0f}% dari Rp {h52:,.0f}")

        # 7. Near 52w Low
        l52 = data.get("fifty_two_week_low")
        if price and l52 and l52 > 0:
            r = price / l52
            if r <= 1.05:
                score += 1
                signals.append(f"✅ Near 52w Low    → {r*100:.0f}% dari Rp {l52:,.0f} ⚠️")

        return score, signals

    def _get_alert_level(self, score):
        if score >= 4: return "🚨 EXTREME ALERT", "🚨"
        if score >= 3: return "🔥 STRONG ALERT", "🔥"
        if score >= 2: return "🔔 ALERT", "🔔"
        return None, None

    def _score_bar(self, score, mx=5):
        return "█" * min(score, mx) + "░" * (mx - min(score, mx))

    def _ai_analysis(self, prompt_text, max_tokens=500):
        """Generate AI analysis via Groq. Retry dengan model fallback jika 429."""
        for model in MODEL_CONFIGS:
            try:
                raw = self.groq_client.chat.completions.with_raw_response.create(
                    model=model["name"],
                    messages=[
                        {"role": "system", "content": "Kamu analis saham profesional Indonesia. Jawab SINGKAT dalam bullet points bahasa Indonesia. Setiap poin mulai dengan emoji."},
                        {"role": "user", "content": prompt_text}
                    ],
                    temperature=0.7, max_tokens=max_tokens
                )
                resp = raw.parse()
                self.model_manager.update_from_headers(model["name"], raw.headers)
                ai_text = resp.choices[0].message.content
                # Strip Qwen3 <think> tags
                ai_text = re.sub(r'<think>.*?</think>', '', ai_text, flags=re.DOTALL).strip()
                print(f"  ✅ [Saham AI] {model['label']} berhasil")
                return ai_text, model["label"]
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "rate" in err_str.lower() or "limit" in err_str.lower():
                    print(f"  ⚠️ {model['label']} rate limited, coba model berikutnya...")
                    continue
                return f"- ⚠️ Analisa AI error: {e}", "N/A"
        return "- ⚠️ Semua model AI sedang limit, coba lagi nanti.", "N/A"

    def format_alert_message(self, data, score, signals, ai_analysis, process_time):
        """Format pesan alert untuk Discord."""
        alert_level, emoji = self._get_alert_level(score)
        price = data.get('current_price') or 0
        prev = data.get('prev_close') or 0
        change_pct = ((price - prev) / prev * 100) if prev else 0
        ce = "🟢" if change_pct >= 0 else "🔴"
        now = datetime.now(JAKARTA_TZ)
        ts = now.strftime("%d %b %Y, %H:%M:%S") + " WIB"

        msg = f"{'=' * 30}\n"
        msg += f"{alert_level} — {data['ticker']} ({data['name']})\n"
        msg += f"⏰ Terdeteksi: {ts} (diproses {process_time:.0f} detik)\n"
        msg += f"📌 Signal terjadi dalam {SCAN_INTERVAL_MINUTES} menit terakhir\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        msg += f"💰 Rp {prev:,.0f} → Rp {price:,.0f} | {ce} {change_pct:+.2f}%\n"
        msg += f"📊 Volume: {format_volume(data['volume'])} (avg: {format_volume(data['avg_volume'])})\n\n"
        msg += f"📋 SIGNAL SCORE: {self._score_bar(score)} {score}/5\n\n"
        for s in signals: msg += f"{s}\n"
        msg += f"\n💡 ANALISA AI:\n{ai_analysis}\n\n"
        msg += f"🔗 Lihat Chart:\n"
        msg += f"TradingView → <https://tradingview.com/chart/?symbol=IDX:{data['ticker']}>\n"
        msg += f"Stockbit    → <https://stockbit.com/symbol/{data['ticker']}>\n\n"
        msg += f"⚠️ Ini bukan rekomendasi beli/jual.\n"
        msg += f"{'=' * 30}\n"
        msg += f"-# 🤖 Signal Scanner | Skor {score}/8 {emoji}"
        return msg

    def get_watchlist(self):
        """Feature 1: Dynamic AI watchlist."""
        now = time.time()
        if self.watchlist_cache and (now - self.watchlist_cache_time) < WATCHLIST_CACHE_MINUTES * 60:
            return self.watchlist_cache, True

        # Web search trending stocks
        query = "saham IDX paling aktif hari ini top gainers losers trending"
        search_results, _ = self.search_manager.search(query)

        # AI extract tickers
        tickers = IDX_CORE_STOCKS[:]
        if search_results:
            try:
                ai_text, _ = self._ai_analysis(
                    f"Dari teks berikut, extract kode saham IDX yang disebutkan. HANYA return JSON array berisi kode saham (4 huruf kapital). Contoh: [\"BBCA\",\"GOTO\"]. Jika tidak ada, return [].\n\n{search_results}",
                    max_tokens=200
                )
                start, end = ai_text.find('['), ai_text.rfind(']') + 1
                if start >= 0 and end > start:
                    extracted = json.loads(ai_text[start:end])
                    if extracted:
                        seen = set()
                        merged = []
                        for t in extracted + IDX_CORE_STOCKS:
                            tu = t.upper().strip()
                            if tu not in seen and len(tu) == 4:
                                seen.add(tu)
                                merged.append(tu)
                        tickers = merged[:15]
            except Exception as e:
                print(f"  ⚠️ AI extract gagal: {e}")

        # Fetch data
        watchlist = []
        for tc in tickers[:15]:
            try:
                t = yf.Ticker(f"{tc}.JK")
                info = t.info
                if not info or 'currentPrice' not in info: continue
                p = info.get('currentPrice', 0)
                pc = info.get('previousClose', 0)
                watchlist.append({
                    "ticker": tc,
                    "name": info.get("shortName", tc),
                    "price": p,
                    "change_pct": ((p - pc) / pc * 100) if pc else 0,
                    "volume": info.get('volume', 0),
                })
            except: continue

        watchlist.sort(key=lambda x: abs(x['change_pct']), reverse=True)
        self.watchlist_cache = watchlist
        self.watchlist_cache_time = now
        return watchlist, False

    def format_watchlist_message(self, data, from_cache):
        """Format watchlist untuk Discord."""
        now = datetime.now(JAKARTA_TZ)
        msg = f"{'=' * 30}\n"
        msg += f"📊 **Watchlist Saham IDX** — {now.strftime('%d %b %Y, %H:%M')} WIB\n"
        msg += f"🔍 AI Research {'(cache)' if from_cache else '(fresh)'}\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        for item in data[:12]:
            e = "🟢" if item['change_pct'] >= 0 else "🔴"
            msg += f"{e} **{item['ticker']}** — {item['name']}\n"
            msg += f"   Rp {item['price']:,.0f} ({item['change_pct']:+.2f}%) | Vol: {format_volume(item['volume'])}\n\n"
        if from_cache:
            rem = int(WATCHLIST_CACHE_MINUTES * 60 - (time.time() - self.watchlist_cache_time))
            msg += f"⏰ Update berikutnya: {rem // 60}m {rem % 60}d\n"
        msg += f"💡 Ketik `!saham cari [KODE]` untuk detail\n"
        msg += f"{'=' * 30}\n"
        msg += f"-# 🤖 FatihAI Watchlist 📊"
        return msg

    def get_detail(self, ticker_code):
        """Feature 2: Detailed stock analysis (Unified 3-Lens)."""
        ticker_code = ticker_code.upper().strip()
        now_ts = time.time()
        
        # 1. Cek Cache (1 jam TTL)
        if ticker_code in self.detail_cache:
            cached = self.detail_cache[ticker_code]
            if now_ts - cached['time'] < 3600:
                print(f"  ⚡ Using cached detail for {ticker_code}")
                return cached['result']

        data = self._fetch_stock_data(ticker_code)
        if not data: return None
        score, signals = self._calculate_signals(data)

        # 2. Web search news (Filtered source for quality)
        sq = f"site:cnbcindonesia.com {ticker_code} {data['name']} analisa berita terbaru {datetime.now(JAKARTA_TZ).strftime('%B %Y')}"
        search_results, search_provider = self.search_manager.search(sq, max_results=5)

        # 3. AI analysis (Strict 3-Lens Prompt)
        prompt = f"""Bertindaklah sebagai Senior Equity Analyst. Analisa saham {ticker_code} ({data['name']}) dengan format 3-Lensa yang ketat.

DATA PASAR:
- Harga: Rp {(data.get('current_price') or 0):,.0f} | Prev: Rp {(data.get('prev_close') or 0):,.0f}
- P/E: {data.get('pe_ratio','N/A')}x | PBV: {data.get('pbv_ratio','N/A')}x | ROE: {f"{data.get('roe',0)*100:.1f}%" if data.get('roe') else 'N/A'}
- RSI: {data.get('rsi','N/A')} | Volume: {format_volume(data.get('volume'))}
- Konsensus Anais: {data.get('recommendation','N/A')} (Target: Rp {(data.get('target_price') or 0):,.0f})

BERITA TERBARU:
{search_results[:2000] if search_results else 'Tidak ada berita signifikan.'}

STRUKTUR LAPORAN (WAJIB):
1. 🏦 **LENSA FUNDAMENTAL**: Evaluasi kesehatan keuangan, valuasi (murah/mahal), dan efisiensi (ROE).
2. 📈 **LENSA TEKNIKAL**: Baca tren harga, indikator RSI, dan apakah sedang di zona beli/jual.
3. 🗣️ **LENSA NARASI/SENTIMEN**: Berikan SKOR SENTIMEN (1-10) berdasarkan berita terbaru. Apakah kabarnya organik atau sekadar hype?
4. 🏁 **VERDICT**: Berikan kesimpulan tegas (STRONG BUY / BUY / HOLD / AVOID) dan 1 kalimat alasan utamanya.

Gunakan gaya bahasa profesional, lugas, dan berikan poin-poin penting saja."""

        ai_text, model_label = self._ai_analysis(prompt, max_tokens=1000)
        result = (data, score, signals, ai_text, search_provider, model_label)
        
        # Simpan ke cache
        self.detail_cache[ticker_code] = {'time': now_ts, 'result': result}
        return result

    def format_detail_message(self, data, score, signals, ai_text, search_provider, model_label):
        """Format detail analysis untuk Discord."""
        price = data.get('current_price') or 0
        prev = data.get('prev_close') or 0
        cpct = ((price - prev) / prev * 100) if prev else 0
        ce = "🟢" if cpct >= 0 else "🔴"
        h52, l52 = data.get('fifty_two_week_high', 0), data.get('fifty_two_week_low', 0)
        pos_pct = ((price - l52) / (h52 - l52) * 100) if h52 and l52 and h52 > l52 else 0
        pb = "█" * int(pos_pct / 10) + "░" * (10 - int(pos_pct / 10))

        msg = f"📊 Analisa Saham: **{data['ticker']}** — {data['name']}\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        msg += f"💰 **HARGA & PERGERAKAN**\n```\n"
        msg += f"Harga saat ini : Rp {price:,.0f}\n"
        msg += f"Buka           : Rp {(data.get('open') or 0):,.0f}\n"
        msg += f"Tertinggi      : Rp {(data.get('day_high') or 0):,.0f}\n"
        msg += f"Terendah       : Rp {(data.get('day_low') or 0):,.0f}\n"
        msg += f"Perubahan      : {ce} {cpct:+.2f}%\n"
        msg += f"Volume         : {format_volume(data['volume'])} (avg: {format_volume(data['avg_volume'])})\n```\n\n"

        msg += f"📈 **RANGE & TREND**\n```\n"
        msg += f"52 Minggu      : Rp {l52:,.0f} — Rp {h52:,.0f}\n"
        msg += f"Posisi         : [{pb}] {pos_pct:.0f}%\n"
        ma50 = data.get('ma50')
        if ma50: msg += f"MA-50          : Rp {ma50:,.0f} {'✅' if price > ma50 else '⚠️'}\n"
        ma200 = data.get('ma200')
        if ma200: msg += f"MA-200         : Rp {ma200:,.0f} {'✅' if price > ma200 else '⚠️'}\n"
        msg += f"RSI-14         : {data.get('rsi', 'N/A')}\n```\n\n"

        msg += f"📊 **FUNDAMENTAL**\n```\n"
        msg += f"Sektor         : {data['sector']}\n"
        msg += f"Market Cap     : {format_rupiah(data['market_cap'])}\n"
        pe = data.get('pe_ratio')
        msg += f"P/E Ratio      : {f'{pe:.2f}x' if pe else 'N/A'}\n"
        dv = data.get('dividend_yield')
        msg += f"Dividend Yield : {f'{dv:.2f}%' if dv else 'N/A'}\n"
        roe = data.get('roe')
        msg += f"ROE            : {f'{roe*100:.2f}%' if roe else 'N/A'}\n"
        pm = data.get('profit_margin')
        msg += f"Profit Margin  : {f'{pm*100:.2f}%' if pm else 'N/A'}\n```\n\n"

        rec = data.get('recommendation')
        target = data.get('target_price')
        cnt = data.get('analyst_count', 0)
        if rec and cnt:
            re = {"strong_buy":"⭐","buy":"🟢","hold":"🟡","sell":"🔴","strong_sell":"🔴🔴"}.get(rec,"❓")
            up = ((target - price) / price * 100) if target else 0
            msg += f"🎯 **KONSENSUS ANALIS** ({cnt} analis)\n```\n"
            msg += f"Rating         : {re} {rec.upper().replace('_',' ')}\n"
            msg += f"Target Harga   : Rp {(target or 0):,.0f}\n"
            msg += f"Upside         : {up:+.1f}%\n```\n\n"

        msg += f"📋 **SIGNAL SCORE**: {self._score_bar(score)} {score}/5\n\n"
        for s in signals: msg += f"{s}\n"
        msg += f"\n💡 **ANALISA AI:**\n{ai_text}\n\n"
        msg += f"🔗 **Lihat Chart:**\n"
        msg += f"TradingView → <https://tradingview.com/chart/?symbol=IDX:{data['ticker']}>\n"
        msg += f"Stockbit    → <https://stockbit.com/symbol/{data['ticker']}>\n\n"
        msg += f"⚠️ Ini bukan rekomendasi beli/jual.\n"
        si = f" | {search_provider}" if search_provider else ""
        msg += f"-# 🤖 *{model_label}*{si} | yfinance 📊"
        return msg

    def _build_scan_pool(self, max_size=50, user_tickers=None):
        """Build dynamic pool: user porto (priority) + 20 core + trending."""
        pool = list(user_tickers) if user_tickers else []
        seen = set(pool)

        # Tambah core stocks
        for tc in IDX_CORE_STOCKS:
            if tc not in seen:
                pool.append(tc)
                seen.add(tc)

        # Tambah dari watchlist cache (saham trending)
        if self.watchlist_cache:
            for item in self.watchlist_cache:
                tc = item.get('ticker', '')
                if tc and tc not in seen:
                    pool.append(tc)
                    seen.add(tc)

        # Padding dari LQ45 pool (random)
        remaining = max_size - len(pool)
        if remaining > 0:
            available = [s for s in IDX_LQ45_POOL if s not in seen]
            random.shuffle(available)
            pool.extend(available[:remaining])

        return pool[:max_size]

    def scan_signals(self, user_tickers=None):
        """Feature 3: Scan dinamis saham (porto user + core + trending), return list alert."""
        scan_pool = self._build_scan_pool(50, user_tickers)
        alerts = []
        for tc in scan_pool:
            data = self._fetch_stock_data(tc)
            if not data: continue
            score, signals = self._calculate_signals(data)
            self.prev_prices[tc] = data.get('current_price')
            if score >= 2:
                last = self.alerted_stocks.get(tc, 0)
                if time.time() - last < 3600:
                    print(f"  ⏳ {tc} cooldown, skip")
                    continue
                alerts.append({"data": data, "score": score, "signals": signals})
                self.alerted_stocks[tc] = time.time()
        return alerts


# Inisialisasi Saham Manager
saham_manager = SahamManager(search_manager, groq_client, model_manager)

# ==========================================
# DISCORD MESSAGE SPLITTER (MAKS 2000 KARAKTER)
# ==========================================
DISCORD_MAX_LENGTH = 2000

def split_message(text, max_length=DISCORD_MAX_LENGTH):
    """Pecah pesan panjang menjadi beberapa bagian."""
    if len(text) <= max_length:
        return [text]

    chunks = []
    while len(text) > 0:
        if len(text) <= max_length:
            chunks.append(text)
            break

        split_pos = text.rfind('\n', 0, max_length)
        if split_pos == -1:
            split_pos = text.rfind(' ', 0, max_length)
        if split_pos == -1:
            split_pos = max_length

        chunks.append(text[:split_pos])
        text = text[split_pos:].lstrip('\n')

    return chunks

# ==========================================
# 4. PERSIAPAN BOT DISCORD
# ==========================================
intents = discord.Intents.default()
intents.message_content = True
discord_client = discord.Client(intents=intents)
bot_start_time = time.time()

# ==========================================
# BACKGROUND TASKS (Signal Scanner + Watchlist Auto-Post)
# ==========================================
@tasks.loop(minutes=SCAN_INTERVAL_MINUTES)
async def signal_scanner():
    """Background: scan signals setiap 10 menit."""
    now = datetime.now(JAKARTA_TZ)
    if now.weekday() >= 5: return  # Skip weekend
    h, m = now.hour, now.minute
    if not ((h > 9 or (h == 9 and m >= 0)) and (h < 15 or (h == 15 and m <= 30))):
        return  # Di luar jam market

    if not ALERT_CHANNEL_ID: return
    scan_start = time.time()
    
    # Kumpulkan semua ticker unik dari portofolio semua user
    user_tickers = set()
    for portfolio in user_portfolios.values():
        user_tickers.update(portfolio.keys())
    
    alerts = await asyncio.to_thread(saham_manager.scan_signals, list(user_tickers))
    scan_pool_size = len(saham_manager._build_scan_pool(50, list(user_tickers)))
    print(f"\n🔍 [Signal Scanner] Scanning {scan_pool_size} saham...")
    scan_time = time.time() - scan_start
    print(f"  ⏱️ Scan selesai dalam {scan_time:.1f}s | {len(alerts)} alert")

    if not alerts: return
    channel = discord_client.get_channel(int(ALERT_CHANNEL_ID))
    if not channel:
        print(f"  ❌ Channel alert tidak ditemukan")
        return

    for alert in alerts:
        ai_start = time.time()
        data, score, signals = alert['data'], alert['score'], alert['signals']
        alert_level, _ = saham_manager._get_alert_level(score)
        prompt = f"""Analisa SINGKAT alert saham dalam 4-6 bullet points.
Data: {data['ticker']} ({data['name']}) Rp {data['current_price']:,.0f} ({((data['current_price']-data['prev_close'])/data['prev_close']*100) if data['prev_close'] else 0:+.2f}%)
Volume: {format_volume(data['volume'])} (avg: {format_volume(data['avg_volume'])})
RSI: {data.get('rsi','N/A')} | 52w High: {data.get('fifty_two_week_high',0)} | Level: {alert_level} (Skor {score})
Signal: {', '.join([s for s in signals if s.startswith('✅')])}"""
        ai_text, _ = saham_manager._ai_analysis(prompt)
        process_time = time.time() - scan_start
        msg = saham_manager.format_alert_message(data, score, signals, ai_text, process_time)
        chunks = split_message(msg)
        for chunk in chunks:
            await channel.send(chunk)
        
        # --- INTEGRASI PORTOFOLIO (DM ALERT) ---
        ticker = data['ticker']
        for user_id, portfolio in user_portfolios.items():
            if ticker in portfolio:
                try:
                    user = await discord_client.fetch_user(user_id)
                    if user:
                        dm_msg = f"❗ **PORTFOLIO ALERT!** ❗\n"
                        dm_msg += f"Saham **{ticker}** di portofolio Boss terdeteksi signal!\n\n"
                        dm_msg += msg # Menggunakan pesan alert yang sama
                        dm_chunks = split_message(dm_msg)
                        for dm_chunk in dm_chunks:
                            await user.send(dm_chunk)
                        print(f"  📩 DM Alert terkirim ke {user.name} untuk {ticker}")
                except Exception as e:
                    print(f"  ❌ Gagal kirim DM alert ke {user_id}: {e}")
        # ----------------------------------------
        
        print(f"  🔔 Alert: {data['ticker']} (skor {score})")

@signal_scanner.before_loop
async def before_signal_scanner():
    await discord_client.wait_until_ready()

@tasks.loop(minutes=WATCHLIST_CACHE_MINUTES)
async def watchlist_auto_post():
    """Background: post watchlist otomatis setiap 30 menit."""
    now = datetime.now(JAKARTA_TZ)
    if now.weekday() >= 5 or now.hour < 9 or now.hour >= 16: return
    if not WATCHLIST_CHANNEL_ID: return

    channel = discord_client.get_channel(int(WATCHLIST_CHANNEL_ID))
    if not channel: return

    print(f"\n📊 [Watchlist] Updating...")
    data, from_cache = await asyncio.to_thread(saham_manager.get_watchlist)
    if not data: return
    msg = saham_manager.format_watchlist_message(data, from_cache)
    chunks = split_message(msg)
    for chunk in chunks:
        await channel.send(chunk)
    print(f"  ✅ Watchlist posted ({len(data)} saham)")

@watchlist_auto_post.before_loop
async def before_watchlist():
    await discord_client.wait_until_ready()

@tasks.loop(minutes=30)
async def daily_portfolio_report():
    """Background: kirim laporan harian portofolio ke DM user saat market tutup."""
    now = datetime.now(JAKARTA_TZ)
    # Jalankan hanya di hari kerja, pada jam 16:00 - 16:30 WIB
    if now.weekday() >= 5 or now.hour != 16: return

    print(f"\n📩 [Daily Report] Mengirim laporan harian ke {len(user_portfolios)} user...")
    
    for user_id, portfolio in user_portfolios.items():
        if not portfolio: continue
        
        try:
            user = await discord_client.fetch_user(user_id)
            if not user: continue

            msg = f"🔔 **Laporan Penutupan Market Boss {user.name}!**\n"
            msg += f"📅 {now.strftime('%d %b %Y')}\n"
            msg += f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            
            total_current = 0
            total_buy = 0
            
            for ticker, buy_price in portfolio.items():
                try:
                    t = yf.Ticker(f"{ticker}.JK")
                    cur_price = t.info.get('currentPrice') or t.info.get('regularMarketPrice')
                    if cur_price:
                        diff = cur_price - buy_price
                        pct = (diff / buy_price) * 100
                        e = "🟢" if pct >= 0 else "🔴"
                        msg += f"{e} **{ticker}**: Rp {cur_price:,.0f} ({pct:+.2f}%)\n"
                        total_current += cur_price
                        total_buy += buy_price
                except: continue
            
            if total_buy > 0:
                total_pct = ((total_current - total_buy) / total_buy) * 100
                msg += f"\n📊 **Total Performa: {total_pct:+.2f}%**\n"
            
            msg += f"\n-# 🤖 *FatihAI Daily Portfolio Report*"
            await user.send(msg)
            print(f"  ✅ Report terkirim ke {user.name}")
        except Exception as e:
            print(f"  ❌ Gagal kirim report ke user {user_id}: {e}")

@daily_portfolio_report.before_loop
async def before_daily_report():
    await discord_client.wait_until_ready()

@tasks.loop(minutes=30)
async def morning_market_briefing():
    """Background: kirim ringkasan berita market setiap pagi pukul 08:30 WIB."""
    now = datetime.now(JAKARTA_TZ)
    # Jalankan hanya di hari kerja, pada jam 08:30 - 09:00 WIB
    if now.weekday() >= 5 or now.hour != 8 or now.minute < 30: return

    if not NEWS_CHANNEL_ID: return
    channel = discord_client.get_channel(int(NEWS_CHANNEL_ID))
    if not channel: return

    print(f"\n📰 [Morning Briefing] Menyiapkan berita pagi...")
    
    async with channel.typing():
        # 1. Cari berita market global & IHSG
        sq = f"sentimen market global IHSG hari ini {now.strftime('%d %B %Y')}"
        search_text, provider = search_manager.search(sq, max_results=8)
        
        # 2. AI Summarization
        prompt = f"""Bertindaklah sebagai News Anchor Keuangan. Buat ringkasan "MORNING BRIEFING" untuk trader Indonesia.
Berita Hari Ini:
{search_text[:3000] if search_text else 'Belum ada berita signifikan.'}

Gunakan format:
📌 **MORNING BRIEFING - {now.strftime('%d %b %Y')}** ☕
━━━━━━━━━━━━━━━━━━━━━
1. **Global Sentiment**: Bagaimana kondisi bursa AS/Asia semalam?
2. **IHSG Outlook**: Prediksi pergerakan hari ini.
3. **Saham Pantauan**: Saham yang berpotensi ramai (base on news).
4. **Kalender Ekonomi**: Agenda hari ini jika ada.

Gaya bahasa: Semangat, lugas, dan informatif."""
        
        # Pakai model terbaik (70B) untuk ringkasan berita
        ai_msg, model_label = saham_manager._ai_analysis(prompt, max_tokens=1000)
        
        header = f"📰 **{now.strftime('%d %B %Y')} - Market Preparation**\n"
        chunks = split_message(header + ai_msg)
        for chunk in chunks:
            await channel.send(chunk)
            
    print(f"  ✅ Morning Briefing terkirim ke channel {NEWS_CHANNEL_ID}")

@morning_market_briefing.before_loop
async def before_morning_briefing():
    await discord_client.wait_until_ready()

@tasks.loop(minutes=1)
async def market_session_alert():
    """Background: kirim pengumuman pembukaan & penutupan market."""
    now = datetime.now(JAKARTA_TZ)
    if now.weekday() >= 5: return
    
    # Opening: 09:00 WIB
    if now.hour == 9 and now.minute == 0:
        msg = "🔔 **MARKET IS OPEN!** 🔔\nSelamat bertarung Boss! Pantau terus signal di channel ini. 📈"
        for cid in [ALERT_CHANNEL_ID, WATCHLIST_CHANNEL_ID]:
            if not cid: continue
            channel = discord_client.get_channel(int(cid))
            if channel: await channel.send(msg)
            
    # Closing: 16:00 WIB
    if now.hour == 16 and now.minute == 0:
        msg = "🏁 **MARKET IS CLOSED!** 🏁\nSesi perdagangan hari ini selesai. Istirahat yang cukup, tunggu laporan harian di DM Boss! ☕"
        for cid in [ALERT_CHANNEL_ID, WATCHLIST_CHANNEL_ID]:
            if not cid: continue
            channel = discord_client.get_channel(int(cid))
            if channel: await channel.send(msg)

@market_session_alert.before_loop
async def before_session_alert():
    await discord_client.wait_until_ready()

@tasks.loop(minutes=30)
async def evening_market_recap():
    """Background: kirim ringkasan penutupan market setiap sore pukul 16:30 WIB."""
    now = datetime.now(JAKARTA_TZ)
    # Jalankan hanya di hari kerja, pada jam 16:30 - 17:00 WIB
    if now.weekday() >= 5 or now.hour != 16 or now.minute < 30: return

    if not NEWS_CHANNEL_ID: return
    channel = discord_client.get_channel(int(NEWS_CHANNEL_ID))
    if not channel: return

    print(f"\n📰 [Evening Recap] Menyiapkan berita penutupan...")
    
    async with channel.typing():
        # 1. Cari berita penutupan IHSG & sentimen hari ini
        sq = f"penutupan IHSG statistik bursa berita hari ini {now.strftime('%d %B %Y')}"
        search_text, provider = search_manager.search(sq, max_results=8)
        
        # 2. AI Summarization
        prompt = f"""Bertindaklah sebagai Senior Market Analyst. Buat ringkasan "EVENING RECAP" untuk penutupan bursa hari ini.
Berita Penutupan:
{search_text[:3000] if search_text else 'Belum ada data penutupan signifikan.'}

Gunakan format:
🏁 **EVENING RECAP - {now.strftime('%d %b %Y')}** 📈
━━━━━━━━━━━━━━━━━━━━━
1. **Market Review**: Bagaimana penutupan IHSG hari ini? (Naik/Turun/Level).
2. **Key Movers**: Saham atau sektor apa yang menggerakkan bursa hari ini?
3. **Daily Narrative**: Sentimen apa yang mendominasi pasar hari ini?
4. **Conclusion**: Insight singkat untuk persiapan besok.

Gaya bahasa: Profesional, tajam, dan edukatif."""
        
        ai_msg, model_label = saham_manager._ai_analysis(prompt, max_tokens=1000)
        
        header = f"📰 **{now.strftime('%d %B %Y')} - Market Recap**\n"
        chunks = split_message(header + ai_msg)
        for chunk in chunks:
            await channel.send(chunk)
            
    print(f"  ✅ Evening Recap terkirim ke channel {NEWS_CHANNEL_ID}")

@evening_market_recap.before_loop
async def before_evening_recap():
    await discord_client.wait_until_ready()

@discord_client.event
async def on_ready():
    global bot_start_time
    bot_start_time = time.time()

    # Start background tasks
    if not signal_scanner.is_running():
        signal_scanner.start()
    if not watchlist_auto_post.is_running():
        watchlist_auto_post.start()
    if not daily_portfolio_report.is_running():
        daily_portfolio_report.start()
    if not morning_market_briefing.is_running():
        morning_market_briefing.start()
    if not evening_market_recap.is_running():
        evening_market_recap.start()
    if not market_session_alert.is_running():
        market_session_alert.start()

    print(f'Yeay! Bot {discord_client.user} sudah online dan siap digunakan!')
    print(f'Models ({len(MODEL_CONFIGS)}):')
    for i, m in enumerate(MODEL_CONFIGS):
        role = "⭐ Utama" if i == 0 else f"Fallback #{i}"
        print(f'  {i+1}. {m["label"]} [{role}]')
    print(f'Auto-Fallback: Aktif (switch jika sisa < {int(FALLBACK_THRESHOLD*100)}%)')
    print(f'Tracking: LIVE dari Groq response headers 🌐')
    print(f'Search Providers ({len(search_manager.providers)}):')
    for p in search_manager.providers:
        print(f'  • {p["name"]} | Limit: {p["credits"]}')
    print(f'Saham System: Signal Scanner (tiap {SCAN_INTERVAL_MINUTES}m) + Watchlist (tiap {WATCHLIST_CACHE_MINUTES}m)')
    print(f'Alert Channel: {ALERT_CHANNEL_ID} | Watchlist Channel: {WATCHLIST_CHANNEL_ID}')
    active = model_manager.get_best_model()
    print(f'🤖 Model Aktif: {active["label"]} ({active["name"]})')

@discord_client.event
async def on_message(message):
    if message.author == discord_client.user:
        return

    # ==========================================
    # Command: !help — Daftar Perintah
    # ==========================================
    if message.content.strip() == '!help':
        help_msg = f"🤖 **Daftar Perintah FatihAI**\n"
        help_msg += f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        help_msg += f"💬 **CHAT & AI**\n"
        help_msg += f"• `!bro [tanya]` : Tanya apa saja ke FatihAI (hemat kuota)\n"
        help_msg += f"• `!status` : Cek kesehatan & kuota model AI\n\n"
        help_msg += f"📈 **SAHAM & PASAR**\n"
        help_msg += f"• `!saham` : Lihat watchlist trending hari ini\n"
        help_msg += f"• `!saham cari [KODE]` : Analisa 3-Lensa mendalam (Fundamental, Teknikal, Narasi)\n\n"
        help_msg += f"💰 **PORTO SAYA**\n"
        help_msg += f"• `!porto` : Cek performa semua saham di porto Boss\n"
        help_msg += f"• `!porto tambah [KODE] [HARGA]` : Simpan saham ke porto\n"
        help_msg += f"• `!porto hapus [KODE]` : Hapus saham dari porto Boss\n\n"
        help_msg += f"-# 💡 *Tips: Cek channel #news-trading untuk Morning Briefing (08:30) & Evening Recap (16:30)!*"
        await message.reply(help_msg)
        return

    # ==========================================
    # Command: !porto — Kelola Portofolio
    # ==========================================
    if message.content.startswith('!porto'):
        user_id = message.author.id
        if user_id not in user_portfolios:
            user_portfolios[user_id] = {}

        cmd_parts = message.content.strip().split()
        
        # 1. !porto (List Summary)
        if len(cmd_parts) == 1:
            portfolio = user_portfolios[user_id]
            if not portfolio:
                await message.reply("📋 Portofolio Boss masih kosong. Tambahkan saham pakai `!porto tambah [KODE] [HARGA]`")
                return
            
            async with message.channel.typing():
                msg = f"📋 **Portofolio Boss {message.author.name}**\n"
                msg += f"━━━━━━━━━━━━━━━━━━━━━\n\n"
                total_current = 0
                total_buy = 0
                
                for ticker, buy_price in portfolio.items():
                    try:
                        t = yf.Ticker(f"{ticker}.JK")
                        cur_price = t.info.get('currentPrice') or t.info.get('regularMarketPrice')
                        if cur_price:
                            diff = cur_price - buy_price
                            pct = (diff / buy_price) * 100
                            e = "🟢" if pct >= 0 else "🔴"
                            msg += f"{e} **{ticker}**\n"
                            msg += f"   Avg: Rp {buy_price:,.0f} → Now: Rp {cur_price:,.0f} (**{pct:+.2f}%**)\n\n"
                            total_current += cur_price
                            total_buy += buy_price
                        else:
                            msg += f"⚪ **{ticker}**\n   Avg: Rp {buy_price:,.0f} (Data tidak tersedia)\n\n"
                    except:
                        msg += f"⚠️ **{ticker}** (Gagal fetch data)\n\n"
                
                if total_buy > 0:
                    total_pct = ((total_current - total_buy) / total_buy) * 100
                    indicator = "🚀 CUAN BANGET" if total_pct > 5 else "✅ UNTUNG" if total_pct > 0 else "🔻 BONCOS" if total_pct < -5 else "⚠️ MERAH"
                    msg += f"📊 **ESTIMASI TOTAL G/L: {total_pct:+.2f}%** — *{indicator}*\n"
                
                msg += f"\n-# 🤖 *FatihAI Portfolio Tracker*"
                await message.reply(msg)
            return

        # 2. !porto tambah [KODE] [HARGA]
        if cmd_parts[1] == 'tambah':
            if len(cmd_parts) < 4:
                await message.reply("💡 Cara pakai: `!porto tambah BBCA 10500`")
                return
            ticker = cmd_parts[2].upper()
            try:
                # Ambil sisa pesan sebagai harga (antisipasi spasi: Rp 10.000)
                raw_price = "".join(cmd_parts[3:]).lower().replace('rp', '').replace(' ', '')
                
                # Heuristic: Di saham IDX, titik hampir selalu ribuan (10.000)
                # Jika ada koma, titik fix ribuan (1.234,50)
                if '.' in raw_price and ',' in raw_price:
                    raw_price = raw_price.replace('.', '').replace(',', '.')
                elif '.' in raw_price:
                    # 10.000 -> 10000. Di IDX tidak ada harga desimal seperti 10.5
                    raw_price = raw_price.replace('.', '')
                elif ',' in raw_price:
                    raw_price = raw_price.replace(',', '.')
                
                price = float(raw_price)
                user_portfolios[user_id][ticker] = price
                await message.reply(f"✅ Berhasil mencatat **{ticker}** di harga **Rp {price:,.0f}** ke porto Boss.")
            except ValueError:
                await message.reply("❌ Harga harus berupa angka, Boss! (Contoh: `10500` atau `10.500`) ")
            return

        # 3. !porto hapus [KODE]
        if cmd_parts[1] == 'hapus':
            if len(cmd_parts) < 3:
                await message.reply("💡 Cara pakai: `!porto hapus BBCA`")
                return
            ticker = cmd_parts[2].upper()
            if ticker in user_portfolios[user_id]:
                del user_portfolios[user_id][ticker]
                await message.reply(f"🗑️ **{ticker}** sudah dihapus dari portofolio Boss.")
            else:
                await message.reply(f"❌ Saham **{ticker}** tidak ada di porto Boss.")
            return

    # ==========================================
    # Command: !saham — Watchlist & Analisa Saham
    # ==========================================
    if message.content.strip() == '!saham':
        async with message.channel.typing():
            try:
                data, from_cache = await asyncio.to_thread(saham_manager.get_watchlist)
                if not data:
                    await message.reply("❌ Gagal mengambil data saham. Coba lagi nanti.")
                    return
                msg = saham_manager.format_watchlist_message(data, from_cache)
                chunks = split_message(msg)
                await message.reply(chunks[0])
                for chunk in chunks[1:]:
                    await message.channel.send(chunk)
            except Exception as e:
                await message.reply(f"❌ Error: {e}")
        return

    if message.content.startswith('!saham cari '):
        query = message.content.replace('!saham cari ', '').strip()
        if not query:
            await message.reply("💡 Cara pakai: `!saham cari BBCA`")
            return

        async with message.channel.typing():
            try:
                result = await asyncio.to_thread(saham_manager.get_detail, query)
                if not result:
                    await message.reply(
                        f"❌ Saham **\"{query.upper()}\"** tidak ditemukan di IDX.\n\n"
                        f"💡 Tips:\n"
                        f"- Pastikan kode saham benar (contoh: BBCA, GOTO, BREN)\n"
                        f"- Kode saham IDX biasanya 4 huruf\n"
                        f"- Coba: `!saham cari BBCA`"
                    )
                    return
                data, score, signals, ai_text, search_provider, model_label = result
                msg = saham_manager.format_detail_message(data, score, signals, ai_text, search_provider, model_label)
                chunks = split_message(msg)
                await message.reply(chunks[0])
                for chunk in chunks[1:]:
                    await message.channel.send(chunk)
            except Exception as e:
                await message.reply(f"❌ Error: {e}")
        return

    # ==========================================
    # Command: !status — Cek status bot
    # ==========================================
    if message.content.strip() == '!status':
        selected = model_manager.get_best_model()
        uptime_seconds = int(time.time() - bot_start_time)
        hours, remainder = divmod(uptime_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        # Hitung jumlah user yang punya memori aktif
        active_users = sum(
            1 for uid in user_chat_memory
            if time.time() - user_chat_memory[uid]["last_time"] < MEMORY_DURATION_SECONDS
        )

        status_msg = f"📊 **Status FatihAI** `LIVE 🌐`\n"
        status_msg += f"━━━━━━━━━━━━━━━━━━━━━\n\n"

        for model in MODEL_CONFIGS:
            d = model_manager.data[model["name"]]
            active = " ← 🟢 **AKTIF**" if model["name"] == selected["name"] else ""
            near = " ⚠️" if model_manager.is_near_limit(model["name"]) else ""

            status_msg += f"**{model['label']}**{active}{near}\n"
            status_msg += f"```\n"

            if d["rpd_limit"] is not None:
                rpd_used = d["rpd_limit"] - d["rpd_remaining"]
                rpd_pct = rpd_used / d["rpd_limit"] * 100
                bar_filled = int(rpd_pct / 10)
                progress = "█" * bar_filled + "░" * (10 - bar_filled)
                status_msg += f"� RPD: {rpd_used} / {d['rpd_limit']} used\n"
                status_msg += f"   [{progress}] {rpd_pct:.1f}%\n"
                status_msg += f"   Sisa: {d['rpd_remaining']} request\n"
            else:
                status_msg += f"📅 RPD: - (kirim !bro dulu)\n"

            if d["tpm_limit"] is not None:
                tpm_used = d["tpm_limit"] - d["tpm_remaining"]
                tpm_pct = tpm_used / d["tpm_limit"] * 100
                bar_filled = int(tpm_pct / 10)
                progress = "█" * bar_filled + "░" * (10 - bar_filled)
                status_msg += f"⏱️ TPM: {tpm_used} / {d['tpm_limit']} used\n"
                status_msg += f"   [{progress}] {tpm_pct:.1f}%\n"
                status_msg += f"   Sisa: {d['tpm_remaining']} token\n"
            else:
                status_msg += f"⏱️ TPM: - (kirim !bro dulu)\n"

            status_msg += f"```\n"

        status_msg += f"⏱️ Uptime: **{hours}j {minutes}m {seconds}d**\n"
        status_msg += f"🧠 Memori aktif: **{active_users}** user\n"
        status_msg += f"🔄 Auto-fallback: sisa < **{int(FALLBACK_THRESHOLD*100)}%** → switch model\n\n"

        # Bagian Search Providers
        status_msg += f"🔍 **Search Providers**\n"
        status_msg += search_manager.get_status()

        await message.reply(status_msg)
        return

    # ==========================================
    # Command: !bro — Tanya FatihAI (General Chat)
    # ==========================================
    if message.content.startswith('!bro'):
        allowed, retry_after = check_rate_limit(message.author.id)
        if not allowed:
            await message.reply(f"⏳ Wait Boss! Tunggu **{retry_after} detik** lagi.")
            return

        pertanyaan = message.content.replace('!bro ', '').strip()
        if not pertanyaan: return
        
        async with message.channel.typing():
            try:
                # Routing: !bro uses GENERAL_MODELS
                active_pool = [m for m in MODEL_CONFIGS if m["name"] in GENERAL_MODELS]
                if not active_pool: active_pool = MODEL_CONFIGS

                # Optimasi search query (tambah tanggal)
                now = datetime.now(JAKARTA_TZ)
                date_str = now.strftime("%B %d %Y")
                search_query = f"{pertanyaan} {date_str}"

                # Cari via multi-search (Tavily → Serper → DuckDuckGo)
                print(f"[Search] Query: '{search_query}'")
                search_results, search_tool = search_manager.search(search_query)

                # Bangun system prompt dengan tanggal dan konteks pencarian
                date_info = now.strftime("Hari ini adalah %A, %d %B %Y. Waktu sekarang: %H:%M WIB.")
                system_prompt = FATIH_AI_PERSONA + f"\n📅 {date_info}\n"
                if search_results:
                    system_prompt += f"\n--- KONTEKS PENCARIAN WEB via {search_tool} ---\n{search_results[:3000]}\n--- AKHIR DATA PENCARIAN ---\n"

                chat_history = get_chat_history(message.author.id)
                messages = [{"role": "system", "content": system_prompt}] + chat_history[-6:] + [{"role": "user", "content": pertanyaan}]

                ai_reply = None
                model_label = None
                total_tokens = 0
                for model in active_pool:
                    try:
                        raw_response = groq_client.chat.completions.with_raw_response.create(
                            model=model["name"],
                            messages=messages,
                            temperature=0.7,
                            max_tokens=1000
                        )
                        response = raw_response.parse()
                        ai_reply = response.choices[0].message.content
                        ai_reply = re.sub(r'<think>.*?</think>', '', ai_reply, flags=re.DOTALL).strip()
                        
                        total_tokens = response.usage.total_tokens if response.usage else 0  # noqa
                        model_manager.update_from_headers(model["name"], raw_response.headers)
                        model_label = model["label"]
                        d = model_manager.data[model["name"]]
                        print(f"[{model_label}] Tokens: {total_tokens} | User: {message.author} | RPD sisa: {d['rpd_remaining']}/{d['rpd_limit']} | TPM sisa: {d['tpm_remaining']}/{d['tpm_limit']}")
                        break
                    except Exception as e:
                        if "429" in str(e): continue
                        raise e

                if ai_reply:
                    add_to_memory(message.author.id, pertanyaan, ai_reply)
                    chunks = split_message(ai_reply)
                    
                    search_info = f" | {search_tool}" if search_tool else ""
                    model_footer = f"\n\n-# 🤖 *{model_label}*{search_info}"
                    chunks[-1] += model_footer
                    
                    await message.reply(chunks[0])
                    for i in range(1, len(chunks)): await message.channel.send(chunks[i])
                else:
                    await message.reply("❌ Semua model cadangan sedang sibuk, Boss.")

            except Exception as e:
                await message.reply(f"❌ AI-nya lagi pusing, Boss: {e}")
        return


# ==========================================
# 5. NYALAKAN BOT
# ==========================================
discord_client.run(DISCORD_TOKEN)