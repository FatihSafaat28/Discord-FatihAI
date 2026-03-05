# 🤖 FatihAI Discord Bot

FatihAI adalah asisten Discord bertenaga AI yang dirancang khusus untuk komunitas investor dan trader saham di Indonesia. Bot ini menggabungkan kecanggihan LLM (Large Language Models) dengan data pasar saham real-time dan pencarian web mutakhir.

## ✨ Fitur Utama

- **🚀 Multi-Model AI with Auto-Fallback**: Menggunakan model terbaik dari Groq (Llama 3.3 70B, Qwen3, dll.) dengan sistem otomatis beralih ke model cadangan jika kuota limit tercapai.
- **📈 Analisa Saham IDX Real-time**: Data langsung dari Bursa Efek Indonesia (IDX) via `yfinance`.
- **🔍 Multi-Search System**: Mencari berita dan informasi terkini menggunakan kombinasi Tavily, Serper (Google Search), dan DuckDuckGo.
- **💰 Personal Portfolio Tracker**: Catat harga beli saham Boss dan pantau Gain/Loss secara real-time. Tiap user memiliki catatan yang terpisah dan pribadi.
- **🔔 Smart Signal Scanner**: Memantau pergerakan teknikal (Volume Spike, RSI, MACD) setiap 5 menit. Jika ada saham di porto Boss yang terdeteksi, bot akan mengirimkan DM alert.
- **📡 Daily Closing Report**: Laporan performa portofolio harian dikirim otomatis ke DM Boss setiap market tutup (16:00 WIB).
- **🇮🇩 Timezone Aware**: Sistem dikunci menggunakan Waktu Indonesia Barat (WIB / Asia/Jakarta), menjamin akurasi scan market dimanapun bot di-deploy (Railway/Heroku/VPS).

## 🛠️ Instalasi

### 1. Persyaratan

- Python 3.10+
- Token Bot Discord ([Discord Developer Portal](https://discord.com/developers/applications))
- API Key Groq ([Groq Console](https://console.groq.com/))
- (Opsional) API Key Tavily & Serper untuk pencarian web yang lebih akurat.

### 2. Setup

Clone repository ini dan install dependensinya:

```bash
git clone https://github.com/FatihSafaat28/Discord-FatihAI.git
cd Discord-FatihAI
pip install -r requirements.txt
```

### 3. Konfigurasi

Buat file `.env` di direktori utama:

```env
DISCORD_TOKEN=your_discord_token
GROQ_API_KEY=your_groq_api_key
TAVILY_API_KEY=your_tavily_key
SERPER_API_KEY=your_serper_key
ALERT_CHANNEL_ID=your_channel_id_for_signals
WATCHLIST_CHANNEL_ID=your_channel_id_for_watchlist
```

### 4. Menjalankan Bot

```bash
python bot.py
```

## 🎮 Perintah Bot (Commands)

| Perintah                       | Deskripsi                                                       |
| ------------------------------ | --------------------------------------------------------------- |
| `!help`                        | Menampilkan seluruh daftar perintah & bantuan.                  |
| `!bro [tanya]`                 | Tanya FatihAI tentang topik apa saja (terintegrasi web search). |
| `!saham`                       | Lihat watchlist saham trending/aktif hari ini.                  |
| `!saham cari [KODE]`           | Analisa mendalam (teknikal/fundamental) saham tertentu.         |
| `!porto`                       | Lihat rangkuman Gain/Loss portofolio pribadi Boss.              |
| `!porto tambah [KODE] [HARGA]` | Masukkan saham ke daftar pantauan portofolio.                   |
| `!porto hapus [KODE]`          | Hapus saham dari daftar portofolio.                             |
| `!status`                      | Cek sisa kuota API AI dan Search, serta uptime bot.             |

## 🚀 Deployment

Bot ini dirancang untuk berjalan 24/7 di platform seperti **Railway.app** atau **VPS**. Sebuah `Procfile` sudah disediakan untuk deployment otomatis sebagai Worker.

---

_Dibuat dengan ❤️ untuk kemajuan Trader Indonesia oleh FatihAI Team._
