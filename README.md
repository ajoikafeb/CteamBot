# CteamBot — Discord Multi-Purpose Bot

Discord bot serbaguna untuk manajemen wallet crypto, tracking gas fee, reward giveaway, pick winner, dan cek data on-chain.

## Fitur

### 🔐 Wallet Vault
Simpan alamat wallet crypto terenkripsi per user Discord.

| Command | Fungsi |
|---------|--------|
| `/wallet add <address>` | Simpan satu/banyak address |
| `/wallet view` | Lihat address tersimpan |
| `/wallet list` | Tampilkan semua address |
| `/wallet del <address>` | Hapus satu address |
| `/wallet clear` | Hapus semua address |

> Address disimpan terenkripsi (AES via `cryptography.fernet`).

### ⛽ Gas Tracker
Cek gas fee real-time + estimasi biaya transaksi dalam USD.

| Command | Fungsi |
|---------|--------|
| `/chain gas` | Gas fee + estimasi biaya transfer/contract dalam USD |
| `/chain calc` | Kalkulasi custom gas units |

**Chain didukung:** BNB Smart Chain, Ethereum, Base, Arbitrum, Optimism, Polygon, Avalanche C-Chain, Fantom, Cronos, zkSync Era, Linea, Scroll, Blast, Berachain, Sonic, Conflux eSpace, 0G.

> Price dari CoinGecko → Binance → OKX → CryptoCompare → DexScreener (fallback chain).

### 🎁 Reward System
Tracking reward/pembayaran giveaway per user.

| Command | Fungsi |
|---------|--------|
| `/reward add` | Daftarkan user reward baru |
| `/reward edit` | Edit data user |
| `/reward user` | Lihat data reward user |
| `/reward give` | Beri reward baru (status pending) |
| `/reward paid` | Tandai reward pending sebagai paid |
| `/reward unpaid` | Kembalikan ke pending |
| `/reward exportaddress` | Export CSV wallet address |
| `/reward exportdana` | Export CSV nomor Dana |
| `/reward exportbank` | Export CSV rekening bank |
| `/reward exportall` | Export semua data user |
| `/reward top` | Leaderboard total reward |

### 🎯 Giveaway Picker
Pilih pemenang giveaway dari pesan di channel berdasarkan keyword.

| Command | Fungsi |
|---------|--------|
| `/pick` | Pilih pemenang dengan berbagai opsi filter |

Fitur: filter tanggal, max entri per user, unique user, multi-win, channel scope.

### 👤 Evo / On-Chain
Cek data agent dan portfolio Evo.

| Command | Fungsi |
|---------|--------|
| `/evo cekagent` | Cek agent Evo (balance, on-chain, rank, poin) |
| `/evo portfolio` | Portfolio multi-agent per address |
| `/evo progress` | Progress tracker Evo |

### 📨 DM Management
Batasi siapa saja yang bisa menggunakan bot via DM.

| Command | Fungsi |
|---------|--------|
| `/dm allow <user>` | Izinkan user DM bot |
| `/dm deny <user>` | Blokir user DM bot |
| `/dm list` | Lihat daftar user yang diizinkan |

## Setup

```
pip install -r requirements.txt
```

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_TOKEN` | ✅ | Token bot Discord |
| `WALLET_SECRET` | ✅ | Kunci enkripsi wallet (min 32 karakter) |

## Requirements

- Python 3.10+
- discord.py
- web3
- cryptography
- aiohttp
- python-dotenv
- eth-abi
- requests
- rich

## Struktur

```
├── main.py                  # Entry point bot
├── cogs/
│   ├── wallet.py            # Wallet Vault
│   ├── gastracker.py        # Gas Tracker
│   ├── reward_vault.py      # Reward System
│   ├── evoevo.py            # Evo / On-Chain
│   └── wordpicker.py        # Giveaway Picker
├── data/                    # Data penyimpanan (JSON)
├── .gitignore
├── manifest.json
├── requirements.txt
└── README.md
```
