# 🤖 FatihAI Bot - Full Recap & Features

FatihAI adalah asisten pintar untuk trader dan investor saham di Bursa Efek Indonesia (IDX). Menggunakan model AI canggih (Llama 3.3 70B & others) dan sistem Multi-Search (Tavily, Serper, DuckDuckGo).

---

## 💬 1. Chat & AI Capabilities
### `!bro [pertanyaan]`
Tanya apa saja kepada FatihAI. Bot akan mencari data terbaru di internet sebelum menjawab.
> **Boss:** !bro Gimana kabar IHSG hari ini?
> **FatihAI:** IHSG hari ini terpantau menguat 0.5% ke level 7,300, Boss. Sentimen positif datang dari rilis data inflasi yang terkendali...
> -# 🤖 *Llama 3.3 70B ⭐ | Tavily*

### `!status`
Cek kesehatan bot, sisa kuota API, dan uptime.
> **Contoh Output:**
> 📊 **Status FatihAI** `LIVE 🌐`
> **Llama 3.3 70B ⭐** ← 🟢 **AKTIF**
> ```
> 📅 RPD: 45 / 1000 used
> ⏱️ TPM: 1200 / 5000 used
> ```
> ⏱️ Uptime: **12j 30m 15d**

---

## 📈 2. Stock Analysis Features
### `!saham`
Melihat daftar saham yang sedang trending atau paling aktif hari ini.
> **Contoh Output:**
> 📊 **Watchlist Saham IDX**
> 🟢 **BBCA** — Rp 10,500 (+1.25%) | Vol: 45.2M
> 🔴 **GOTO** — Rp 52 (-3.70%) | Vol: 1.2B

### `!saham cari [KODE]`
Analisa mendalam "3-Lensa" (Fundamental, Teknikal, Narasi).
> **Contoh Output:**
> 🔍 **ANALISA 3-LENSA — BBCA (Bank Central Asia)**
> ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
> 💎 **Fundamental**: P/E 24x, PBV 4.8x. Profit margin sangat sehat...
> 📊 **Teknikal**: RSI Netral (55). Berada di atas MA-50...
> 📰 **Narasi**: Berita akuisisi baru di sektor digital...

---

## 💰 3. Portfolio Tracker
Kelola porto Boss langsung di Discord.
- `!porto` : List performa semua saham di porto.
- `!porto tambah [KODE] [HARGA]` : Catat saham baru.
- `!porto hapus [KODE]` : Hapus dari record.

---

## 🔔 4. Automated Alerts (Background)
Bot memantau market secara otomatis setiap menit.
### Signal Scanner
Memberikan alert jika ada "Volume Spike", "RSI Oversold", atau "MACD Cross".
> 🚨 **ALERT — BBRI (Bank Rakyat Indonesia)**
> 📊 Volume: 3.5x rata-rata 🚀
> 📋 SIGNAL SCORE: █████ 5/5
> 💡 ANALISA AI: Saham ini terdeteksi akumulasi besar...

---

## 📰 5. Unified News System (Setiap 30 Menit)
Sistem berita otomatis terintegrasi di channel #news-trading.
*   **Morning Briefing (08:30 WIB)**: Rangkuman berita pagi & IHSG Outlook.
*   **Market Pulse (09:00 - 16:00 WIB)**: Update tren saham (Bullish/Bearish) setiap 30 menit.
*   **Evening Recap (16:30 WIB)**: Ringkasan hasil penutupan market hari ini.

---
💡 **Tips:** Ketik `!help` untuk melihat semua daftar perintah lengkap kapan saja!
