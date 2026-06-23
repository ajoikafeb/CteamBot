import os
import sys
import json
import time
import base64
import hashlib
from pathlib import Path
from datetime import datetime, timezone

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands


# ==========================================================
# GAS TRACKER + GAS CALCULATOR COG v3
#
# Commands:
# /gas chain:
# /gascalc chain: wallet: address: balance:
#
# Features:
# - Compact ephemeral output
# - Wallet Vault integration + direct encrypted JSON fallback
# - Price fallback: CoinGecko -> Binance -> OKX -> CryptoCompare -> DexScreener -> stale cache
# - Gas + price + balance cache
# ==========================================================

CACHE_TTL_GAS = 8
CACHE_TTL_BALANCE = 8
CACHE_TTL_PRICE = 20
HTTP_TIMEOUT = 12

GAS_UNITS = {
    "transfer": 21_000,
    "contract": 150_000,
    "heavy": 300_000,
    "very_heavy": 600_000,
}

DATA_DIR = Path("data")
WALLET_DATA_FILE = DATA_DIR / "user_wallets.json"

CHAIN_CONFIG = {
    "bnb": {
        "label": "BNB SMART CHAIN",
        "name": "BNB Smart Chain",
        "symbol": "BNB",
        "rpc": ["https://bsc-dataseed.binance.org", "https://bsc-rpc.publicnode.com"],
        "coingecko_id": "binancecoin",
        "binance_symbols": ["BNBUSDT"],
        "okx_inst": ["BNB-USDT"],
        "cryptocompare": "BNB",
        "dex_tokens": ["0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"],
        "thresholds": (1, 3, 8),
    },
    "ethereum": {
        "label": "ETHEREUM",
        "name": "Ethereum",
        "symbol": "ETH",
        "rpc": ["https://ethereum-rpc.publicnode.com", "https://rpc.ankr.com/eth"],
        "coingecko_id": "ethereum",
        "binance_symbols": ["ETHUSDT"],
        "okx_inst": ["ETH-USDT"],
        "cryptocompare": "ETH",
        "dex_tokens": ["0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"],
        "thresholds": (8, 20, 45),
    },
    "base": {
        "label": "BASE",
        "name": "Base",
        "symbol": "ETH",
        "rpc": ["https://base-rpc.publicnode.com", "https://mainnet.base.org"],
        "coingecko_id": "ethereum",
        "binance_symbols": ["ETHUSDT"],
        "okx_inst": ["ETH-USDT"],
        "cryptocompare": "ETH",
        "dex_tokens": ["0x4200000000000000000000000000000000000006"],
        "thresholds": (0.02, 0.10, 0.50),
    },
    "arbitrum": {
        "label": "ARBITRUM",
        "name": "Arbitrum One",
        "symbol": "ETH",
        "rpc": ["https://arbitrum-one-rpc.publicnode.com", "https://arb1.arbitrum.io/rpc"],
        "coingecko_id": "ethereum",
        "binance_symbols": ["ETHUSDT"],
        "okx_inst": ["ETH-USDT"],
        "cryptocompare": "ETH",
        "dex_tokens": ["0x82af49447d8a07e3bd95bd0d56f35241523fbab1"],
        "thresholds": (0.02, 0.10, 0.50),
    },
    "optimism": {
        "label": "OPTIMISM",
        "name": "Optimism",
        "symbol": "ETH",
        "rpc": ["https://optimism-rpc.publicnode.com", "https://mainnet.optimism.io"],
        "coingecko_id": "ethereum",
        "binance_symbols": ["ETHUSDT"],
        "okx_inst": ["ETH-USDT"],
        "cryptocompare": "ETH",
        "dex_tokens": ["0x4200000000000000000000000000000000000006"],
        "thresholds": (0.02, 0.10, 0.50),
    },
    "polygon": {
        "label": "POLYGON",
        "name": "Polygon",
        "symbol": "POL",
        "rpc": ["https://polygon-bor-rpc.publicnode.com", "https://polygon-rpc.com"],
        "coingecko_id": "polygon-ecosystem-token",
        "binance_symbols": ["POLUSDT", "MATICUSDT"],
        "okx_inst": ["POL-USDT", "MATIC-USDT"],
        "cryptocompare": "POL",
        "dex_tokens": ["0x0000000000000000000000000000000000001010", "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270"],
        "thresholds": (30, 80, 150),
    },
    "avalanche": {
        "label": "AVALANCHE",
        "name": "Avalanche C-Chain",
        "symbol": "AVAX",
        "rpc": ["https://avalanche-c-chain-rpc.publicnode.com", "https://api.avax.network/ext/bc/C/rpc"],
        "coingecko_id": "avalanche-2",
        "binance_symbols": ["AVAXUSDT"],
        "okx_inst": ["AVAX-USDT"],
        "cryptocompare": "AVAX",
        "dex_tokens": ["0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7"],
        "thresholds": (25, 60, 120),
    },
    "linea": {
        "label": "LINEA",
        "name": "Linea",
        "symbol": "ETH",
        "rpc": ["https://linea-rpc.publicnode.com", "https://rpc.linea.build"],
        "coingecko_id": "ethereum",
        "binance_symbols": ["ETHUSDT"],
        "okx_inst": ["ETH-USDT"],
        "cryptocompare": "ETH",
        "dex_tokens": ["0xe5D7C2a44FfDDf6b295A15c148167daaAf5Cf34f"],
        "thresholds": (0.02, 0.10, 0.50),
    },
    "scroll": {
        "label": "SCROLL",
        "name": "Scroll",
        "symbol": "ETH",
        "rpc": ["https://scroll-rpc.publicnode.com", "https://rpc.scroll.io"],
        "coingecko_id": "ethereum",
        "binance_symbols": ["ETHUSDT"],
        "okx_inst": ["ETH-USDT"],
        "cryptocompare": "ETH",
        "dex_tokens": ["0x5300000000000000000000000000000000000004"],
        "thresholds": (0.02, 0.10, 0.50),
    },
    "zksync": {
        "label": "ZKSYNC ERA",
        "name": "zkSync Era",
        "symbol": "ETH",
        "rpc": ["https://zksync-era-rpc.publicnode.com", "https://mainnet.era.zksync.io"],
        "coingecko_id": "ethereum",
        "binance_symbols": ["ETHUSDT"],
        "okx_inst": ["ETH-USDT"],
        "cryptocompare": "ETH",
        "dex_tokens": ["0x000000000000000000000000000000000000800A"],
        "thresholds": (0.02, 0.10, 0.50),
    },
    "opbnb": {
        "label": "OPBNB",
        "name": "opBNB",
        "symbol": "BNB",
        "rpc": ["https://opbnb-rpc.publicnode.com", "https://opbnb-mainnet-rpc.bnbchain.org"],
        "coingecko_id": "binancecoin",
        "binance_symbols": ["BNBUSDT"],
        "okx_inst": ["BNB-USDT"],
        "cryptocompare": "BNB",
        "dex_tokens": ["0x4200000000000000000000000000000000000006"],
        "thresholds": (0.01, 0.05, 0.20),
    },
    "mantle": {
        "label": "MANTLE",
        "name": "Mantle",
        "symbol": "MNT",
        "rpc": ["https://mantle-rpc.publicnode.com", "https://rpc.mantle.xyz"],
        "coingecko_id": "mantle",
        "binance_symbols": ["MNTUSDT"],
        "okx_inst": ["MNT-USDT"],
        "cryptocompare": "MNT",
        "dex_tokens": ["0xDeadDeAddeAddEAddeadDEaDDEAdDeaDDeAD0000"],
        "thresholds": (0.02, 0.10, 0.50),
    },
    "blast": {
        "label": "BLAST",
        "name": "Blast",
        "symbol": "ETH",
        "rpc": ["https://blast-rpc.publicnode.com", "https://rpc.blast.io"],
        "coingecko_id": "ethereum",
        "binance_symbols": ["ETHUSDT"],
        "okx_inst": ["ETH-USDT"],
        "cryptocompare": "ETH",
        "dex_tokens": ["0x4300000000000000000000000000000000000004"],
        "thresholds": (0.02, 0.10, 0.50),
    },
    "sei": {
        "label": "SEI EVM",
        "name": "Sei EVM",
        "symbol": "SEI",
        "rpc": ["https://evm-rpc.sei-apis.com"],
        "coingecko_id": "sei-network",
        "binance_symbols": ["SEIUSDT"],
        "okx_inst": ["SEI-USDT"],
        "cryptocompare": "SEI",
        "dex_tokens": [],
        "thresholds": (1, 5, 15),
    },
    "berachain": {
        "label": "BERACHAIN",
        "name": "Berachain",
        "symbol": "BERA",
        "rpc": ["https://rpc.berachain.com"],
        "coingecko_id": "berachain-bera",
        "binance_symbols": ["BERAUSDT"],
        "okx_inst": ["BERA-USDT"],
        "cryptocompare": "BERA",
        "dex_tokens": [],
        "thresholds": (1, 5, 15),
    },
    "sonic": {
        "label": "SONIC",
        "name": "Sonic",
        "symbol": "S",
        "rpc": ["https://rpc.soniclabs.com"],
        "coingecko_id": "sonic-3",
        "binance_symbols": ["SUSDT", "SONICUSDT"],
        "okx_inst": ["S-USDT", "SONIC-USDT"],
        "cryptocompare": "S",
        "dex_tokens": [],
        "thresholds": (1, 5, 15),
    },
    "conflux": {
        "label": "CONFLUX ESPACE",
        "name": "Conflux eSpace",
        "symbol": "CFX",
        "rpc": ["https://evm.confluxrpc.com"],
        "coingecko_id": "conflux-token",
        "binance_symbols": ["CFXUSDT"],
        "okx_inst": ["CFX-USDT"],
        "cryptocompare": "CFX",
        "dex_tokens": [],
        "thresholds": (1, 5, 15),
    },
    "0g": {
        "label": "0G NETWORK",
        "name": "0G",
        "symbol": "0G",
        "rpc": ["https://evmrpc.0g.ai"],
        "coingecko_id": None,
        "binance_symbols": ["0GUSDT"],
        "okx_inst": ["0G-USDT"],
        "cryptocompare": "0G",
        "dex_tokens": [],
        "thresholds": (0.000001, 0.00001, 0.0001),
    },
}

CHAIN_CHOICES = [
    app_commands.Choice(name="BNB", value="bnb"),
    app_commands.Choice(name="Ethereum", value="ethereum"),
    app_commands.Choice(name="Base", value="base"),
    app_commands.Choice(name="Arbitrum", value="arbitrum"),
    app_commands.Choice(name="Optimism", value="optimism"),
    app_commands.Choice(name="Polygon", value="polygon"),
    app_commands.Choice(name="Avalanche", value="avalanche"),
    app_commands.Choice(name="Linea", value="linea"),
    app_commands.Choice(name="Scroll", value="scroll"),
    app_commands.Choice(name="zkSync Era", value="zksync"),
    app_commands.Choice(name="opBNB", value="opbnb"),
    app_commands.Choice(name="Mantle", value="mantle"),
    app_commands.Choice(name="Blast", value="blast"),
    app_commands.Choice(name="Sei EVM", value="sei"),
    app_commands.Choice(name="Berachain", value="berachain"),
    app_commands.Choice(name="Sonic", value="sonic"),
    app_commands.Choice(name="Conflux eSpace", value="conflux"),
    app_commands.Choice(name="0G", value="0g"),
]

GAS_CACHE = {}
PRICE_CACHE = {}
BALANCE_CACHE = {}


def now_utc_text():
    return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")


def is_valid_address(address):
    if not isinstance(address, str):
        return False

    address = address.strip()

    if not address.startswith("0x"):
        return False

    if len(address) != 42:
        return False

    try:
        int(address[2:], 16)
        return True
    except Exception:
        return False


def normalize_address(address):
    return str(address or "").strip().lower()


def mask_wallet(address):
    if not address:
        return "-"
    return address[:6] + "..." + address[-4:]


def format_token(value, symbol):
    value = float(value)
    if value >= 1:
        return f"{value:,.6f} {symbol}"
    if value >= 0.000001:
        return f"{value:.8f} {symbol}"
    return f"{value:.12f} {symbol}"


def format_usd_number(value):
    if value is None:
        return "USD unavailable"

    value = float(value)

    if value >= 1:
        return f"${value:,.4f}"
    if value >= 0.0001:
        return f"${value:.6f}"
    return f"${value:.10f}"


def format_price(value):
    return format_usd_number(value)


def gas_status(gwei, cfg):
    low, mid, high = cfg.get("thresholds", (1, 5, 15))
    gwei = float(gwei)

    if gwei <= float(low):
        return "🟢 Very Low"
    if gwei <= float(mid):
        return "🟡 Low"
    if gwei <= float(high):
        return "🟠 Medium"
    return "🔴 High"


def gas_cost_native(gwei, gas_units):
    return float(gwei) * 1e-9 * int(gas_units)


def decrypt_wallet_direct(value):
    try:
        from cryptography.fernet import Fernet
        from dotenv import load_dotenv

        load_dotenv()
        secret = os.getenv("WALLET_SECRET", "CHANGE_ME_PLEASE_CHANGE_THIS_KEY")
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
        cipher = Fernet(key)

        return cipher.decrypt(value.encode()).decode()
    except Exception:
        return value


def get_wallets_from_json(user_id):
    try:
        if not WALLET_DATA_FILE.exists():
            return []

        with open(WALLET_DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        item = data.get(str(user_id))

        if isinstance(item, dict):
            wallets = item.get("wallets", [])
        elif isinstance(item, list):
            wallets = item
        else:
            wallets = []

        clean = []

        for wallet in wallets:
            if not isinstance(wallet, str):
                continue

            decrypted = decrypt_wallet_direct(wallet)
            if not is_valid_address(decrypted):
                continue

            addr = normalize_address(decrypted)
            if addr not in clean:
                clean.append(addr)

        return clean

    except Exception:
        return []


def get_saved_wallets(user_id):
    # 1) Try loaded modules first. This fixes different cog filenames.
    for module_name, module in list(sys.modules.items()):
        if "wallet" not in module_name.lower():
            continue

        fn = getattr(module, "get_user_wallets", None)
        if callable(fn):
            try:
                wallets = fn(user_id)
                if isinstance(wallets, list):
                    return [normalize_address(x) for x in wallets if is_valid_address(x)]
            except Exception:
                pass

    # 2) Try common import paths.
    for module_name in [
        "cogs.wallet_vault",
        "wallet_vault",
        "cogs.wallet_manager",
        "wallet_manager",
        "cogs.wallets",
        "wallets",
    ]:
        try:
            module = __import__(module_name, fromlist=["get_user_wallets"])
            fn = getattr(module, "get_user_wallets", None)
            if callable(fn):
                wallets = fn(user_id)
                if isinstance(wallets, list):
                    return [normalize_address(x) for x in wallets if is_valid_address(x)]
        except Exception:
            pass

    # 3) Direct JSON fallback. Works with your current Wallet Vault structure.
    return get_wallets_from_json(user_id)


async def rpc_call(session, rpc_url, method, params):
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }

    async with session.post(rpc_url, json=payload, timeout=HTTP_TIMEOUT) as res:
        data = await res.json(content_type=None)

    if "error" in data:
        raise RuntimeError(str(data["error"])[:180])

    return data.get("result")


async def rpc_gas_price(session, rpc_urls):
    last_error = None

    for rpc_url in rpc_urls:
        try:
            gas_hex = await rpc_call(session, rpc_url, "eth_gasPrice", [])

            if not gas_hex:
                raise RuntimeError("empty gas price")

            wei = int(gas_hex, 16)
            gwei = wei / 1e9

            return wei, gwei
        except Exception as e:
            last_error = e
            continue

    raise RuntimeError(f"Semua RPC gas gagal: {last_error}")


async def rpc_fee_history(session, rpc_urls, blocks=4):
    last_error = None
    for rpc_url in rpc_urls:
        try:
            return await rpc_call(session, rpc_url, "eth_feeHistory", [blocks, "latest", [10, 25, 50]])
        except Exception as e:
            last_error = e
            continue
    raise RuntimeError(f"fee history gagal: {last_error}")


async def get_gas_tiers(session, chain_key):
    cfg = CHAIN_CONFIG[chain_key]
    now = time.time()
    cached = GAS_CACHE.get(chain_key)
    if cached and now - cached["time"] < CACHE_TTL_GAS:
        return cached["tiers"]

    try:
        fee_data = await rpc_fee_history(session, cfg["rpc"])
        rewards = fee_data.get("reward", [])
        base_fees = fee_data.get("baseFeePerGas", [])

        if rewards and base_fees and len(rewards) > 0:
            p10 = sum(float(r[0]) for r in rewards) / len(rewards)
            p25 = sum(float(r[1]) for r in rewards) / len(rewards)
            p50 = sum(float(r[2]) for r in rewards) / len(rewards)
            base = float(base_fees[-1])

            tiers = {
                "low": (base + p10) / 1e9,
                "medium": (base + p25) / 1e9,
                "high": (base + p50) / 1e9,
                "base_fee_gwei": base / 1e9,
                "mode": "eip1559",
            }
        else:
            raise RuntimeError("no fee data")
    except Exception:
        _, gwei = await rpc_gas_price(session, cfg["rpc"])
        tiers = {
            "low": gwei,
            "medium": gwei,
            "high": gwei,
            "base_fee_gwei": gwei,
            "mode": "legacy",
        }

    GAS_CACHE[chain_key] = {"time": now, "tiers": tiers}
    return tiers


async def rpc_balance(session, rpc_urls, address):
    last_error = None

    for rpc_url in rpc_urls:
        try:
            balance_hex = await rpc_call(
                session,
                rpc_url,
                "eth_getBalance",
                [address, "latest"],
            )

            if balance_hex is None:
                raise RuntimeError("empty balance")

            wei = int(balance_hex, 16)
            native = wei / 1e18

            return native
        except Exception as e:
            last_error = e
            continue

    raise RuntimeError(f"Semua RPC balance gagal: {last_error}")


async def get_balance(session, chain_key, address):
    address = normalize_address(address)

    if not is_valid_address(address):
        raise RuntimeError("Address tidak valid")

    cache_key = f"{chain_key}:{address}"
    now = time.time()
    cached = BALANCE_CACHE.get(cache_key)

    if cached and now - cached["time"] < CACHE_TTL_BALANCE:
        return cached["balance"]

    cfg = CHAIN_CONFIG[chain_key]
    balance = await rpc_balance(session, cfg["rpc"], address)

    BALANCE_CACHE[cache_key] = {
        "time": now,
        "balance": balance,
    }

    return balance


async def price_from_coingecko(session, coin_id):
    if not coin_id:
        return None

    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {
        "ids": coin_id,
        "vs_currencies": "usd",
    }

    async with session.get(url, params=params, timeout=HTTP_TIMEOUT) as res:
        if res.status != 200:
            return None
        data = await res.json(content_type=None)

    price = data.get(coin_id, {}).get("usd")
    return float(price) if price is not None else None


async def price_from_binance(session, symbols):
    for symbol in symbols or []:
        try:
            url = "https://api.binance.com/api/v3/ticker/price"
            params = {"symbol": symbol}

            async with session.get(url, params=params, timeout=HTTP_TIMEOUT) as res:
                if res.status != 200:
                    continue
                data = await res.json(content_type=None)

            price = data.get("price")
            if price is not None:
                return float(price)
        except Exception:
            continue

    return None


async def price_from_okx(session, inst_ids):
    for inst_id in inst_ids or []:
        try:
            url = "https://www.okx.com/api/v5/market/ticker"
            params = {"instId": inst_id}

            async with session.get(url, params=params, timeout=HTTP_TIMEOUT) as res:
                if res.status != 200:
                    continue
                data = await res.json(content_type=None)

            items = data.get("data") or []
            if not items:
                continue

            price = items[0].get("last")
            if price is not None:
                return float(price)
        except Exception:
            continue

    return None


async def price_from_cryptocompare(session, symbol):
    if not symbol:
        return None

    try:
        url = "https://min-api.cryptocompare.com/data/price"
        params = {
            "fsym": symbol,
            "tsyms": "USD",
        }

        async with session.get(url, params=params, timeout=HTTP_TIMEOUT) as res:
            if res.status != 200:
                return None
            data = await res.json(content_type=None)

        price = data.get("USD")
        return float(price) if price is not None else None
    except Exception:
        return None


async def price_from_dexscreener(session, token_addresses):
    for token in token_addresses or []:
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{token}"

            async with session.get(url, timeout=HTTP_TIMEOUT) as res:
                if res.status != 200:
                    continue
                data = await res.json(content_type=None)

            pairs = data.get("pairs") or []
            best = None

            for pair in pairs:
                price_usd = pair.get("priceUsd")
                liquidity = ((pair.get("liquidity") or {}).get("usd") or 0)

                if price_usd is None:
                    continue

                try:
                    price_value = float(price_usd)
                    liquidity_value = float(liquidity)
                except Exception:
                    continue

                if best is None or liquidity_value > best[0]:
                    best = (liquidity_value, price_value)

            if best is not None:
                return best[1]
        except Exception:
            continue

    return None


async def get_price(session, chain_key):
    cfg = CHAIN_CONFIG[chain_key]
    symbol = cfg["symbol"]
    cache_key = f"price:{symbol}:{chain_key}"
    now = time.time()

    cached = PRICE_CACHE.get(cache_key)
    if cached and now - cached["time"] < CACHE_TTL_PRICE:
        return cached["price"], cached.get("source", "cache")

    sources = [
        ("CoinGecko", price_from_coingecko(session, cfg.get("coingecko_id"))),
        ("Binance", price_from_binance(session, cfg.get("binance_symbols"))),
        ("OKX", price_from_okx(session, cfg.get("okx_inst"))),
        ("CryptoCompare", price_from_cryptocompare(session, cfg.get("cryptocompare"))),
        ("DexScreener", price_from_dexscreener(session, cfg.get("dex_tokens"))),
    ]

    for source_name, coro in sources:
        try:
            price = await coro
            if price is not None and price > 0:
                PRICE_CACHE[cache_key] = {
                    "time": now,
                    "price": float(price),
                    "source": source_name,
                }
                return float(price), source_name
        except Exception:
            continue

    # Stale cache fallback.
    if cached and cached.get("price"):
        return float(cached["price"]), "Cache"

    return None, "Unavailable"


def build_gas_text(chain_key, tiers, price, price_source):
    cfg = CHAIN_CONFIG[chain_key]
    symbol = cfg["symbol"]
    status = gas_status(tiers["medium"], cfg)
    mode = tiers.get("mode", "legacy")

    lines = []
    lines.append(f"⛽ **{cfg['label']}**")
    lines.append("")
    lines.append("━━━━━━━━━━━━")
    lines.append("")

    if mode == "eip1559":
        base = tiers["base_fee_gwei"]
        low = tiers["low"]
        med = tiers["medium"]
        high = tiers["high"]
        lines.append(f"⚡ **Gas** • L:{low:.2f} M:{med:.2f} H:{high:.2f} Gwei")
        lines.append(f"   Base `{base:.4f}` | {status}")
    else:
        lines.append(f"⚡ **Gas** • `{tiers['medium']:.8f}` Gwei {status}")

    lines.append("")
    lines.append("💸 **Estimated (Medium)**")
    lines.append("")

    med_gwei = tiers["medium"]

    items = [
        ("Transfer", GAS_UNITS["transfer"]),
        ("Contract", GAS_UNITS["contract"]),
        ("Heavy", GAS_UNITS["heavy"]),
        ("Very Heavy", GAS_UNITS["very_heavy"]),
    ]

    for name, gas_units in items:
        native = gas_cost_native(med_gwei, gas_units)
        usd = native * price if price is not None else None
        lines.append(f"**{name:<11}** • `{format_token(native, symbol)}` • {format_usd_number(usd)}")

    lines.append("")
    lines.append("━━━━━━━━━━━━")
    lines.append("")
    lines.append(f"💰 **Price** • 1 {symbol} = {format_price(price)}")
    lines.append(f"🧭 **Source** • {price_source}")
    lines.append(f"🕒 **Updated** • {now_utc_text()}")

    return "\n".join(lines)


def build_calc_text(chain_key, tiers, price, price_source, balance, source_label=None, address=None):
    cfg = CHAIN_CONFIG[chain_key]
    symbol = cfg["symbol"]
    status = gas_status(tiers["medium"], cfg)
    mode = tiers.get("mode", "legacy")

    balance = float(balance)
    balance_usd = balance * price if price is not None else None

    lines = []
    lines.append("⛽ **GAS CALCULATOR**")
    lines.append("")
    lines.append("━━━━━━━━━━━━")
    lines.append("")
    lines.append(f"🌐 **{cfg['name']}**")

    if address:
        lines.append(f"💳 **Wallet** • `{mask_wallet(address)}`")

    if source_label:
        lines.append(f"📌 **Source** • {source_label}")

    lines.append(f"💰 **Balance** • `{format_token(balance, symbol)}` ({format_usd_number(balance_usd)})")
    lines.append("")
    lines.append("━━━━━━━━━━━━")
    lines.append("")
    lines.append("📊 **Estimated Max TX (Medium)**")

    med_gwei = tiers["medium"]
    low_gwei = tiers["low"]
    high_gwei = tiers["high"]

    items = [
        ("Transfer", GAS_UNITS["transfer"]),
        ("Contract", GAS_UNITS["contract"]),
        ("Heavy", GAS_UNITS["heavy"]),
        ("Very Heavy", GAS_UNITS["very_heavy"]),
    ]

    for name, gas_units in items:
        native_low = gas_cost_native(low_gwei, gas_units) if low_gwei > 0 else 999
        native_med = gas_cost_native(med_gwei, gas_units)
        native_high = gas_cost_native(high_gwei, gas_units) if high_gwei > 0 else 999
        count_med = int(balance // native_med) if native_med > 0 else 0
        count_low = int(balance // native_low) if native_low > 0 else 0
        count_high = int(balance // native_high) if native_high > 0 else 0
        lines.append(f"**{name:<11}** ≈ `{count_low:,}`-`{count_high:,}` TX (med `{count_med:,}`)")

    lines.append("")
    lines.append("━━━━━━━━━━━━")
    lines.append("")
    if mode == "eip1559":
        lines.append(f"⚡ **Gas** • L:{low_gwei:.2f} M:{med_gwei:.2f} H:{high_gwei:.2f} Gwei {status}")
    else:
        lines.append(f"⚡ **Gas** • `{med_gwei:.8f}` Gwei {status}")
    lines.append(f"💰 **Price** • {format_price(price)}")
    lines.append(f"🧭 **Price Source** • {price_source}")
    lines.append(f"🕒 **Updated** • {now_utc_text()}")

    return "\n".join(lines)


def parse_wallet_selector(wallet_text):
    if wallet_text is None:
        return None

    text = str(wallet_text).strip()

    if not text:
        return None

    if is_valid_address(text):
        return normalize_address(text)

    lowered = text.lower().replace("#", "").replace("wallet", "").strip()

    try:
        return int(lowered)
    except Exception:
        return None


async def wallet_autocomplete(interaction: discord.Interaction, current: str):
    wallets = get_saved_wallets(interaction.user.id)
    choices = []
    current_l = (current or "").lower().strip()

    for idx, address in enumerate(wallets[:25], start=1):
        label = f"{idx} • {mask_wallet(address)}"
        value = str(idx)

        if current_l and current_l not in label.lower() and current_l not in value:
            continue

        choices.append(app_commands.Choice(name=label, value=value))

    return choices[:25]


class GasCog(commands.Cog):
    chain = app_commands.Group(name="chain", description="Cek gas & kalkulasi biaya transaksi")

    def __init__(self, bot):
        self.bot = bot

    @chain.command(
        name="gas",
        description="Cek gas fee realtime dan estimasi biaya transfer/contract dalam USD."
    )
    @app_commands.choices(chain=CHAIN_CHOICES)
    async def gas(self, interaction: discord.Interaction, chain: app_commands.Choice[str]):
        await interaction.response.defer(ephemeral=True, thinking=True)
        chain_key = chain.value

        try:
            async with aiohttp.ClientSession() as session:
                tiers = await get_gas_tiers(session, chain_key)
                price, price_source = await get_price(session, chain_key)

            text = build_gas_text(chain_key, tiers, price, price_source)

            embed = discord.Embed(
                description=text,
                color=0xF59E0B,
            )
            embed.set_footer(text="Private gas tracker • EIP-1559 fee tiers + multi-source USD price")

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            await interaction.followup.send(
                f"❌ Gagal cek gas `{chain_key}`: `{str(e)[:180]}`",
                ephemeral=True,
            )

    @chain.command(
        name="calc",
        description="Hitung saldo cukup untuk berapa transfer/contract TX."
    )
    @app_commands.choices(chain=CHAIN_CHOICES)
    @app_commands.autocomplete(wallet=wallet_autocomplete)
    @app_commands.describe(
        chain="Pilih chain.",
        wallet="Nomor wallet dari /wallet view. Contoh: 1",
        address="Wallet address manual. Jika diisi, bot ambil balance via RPC.",
        balance="Saldo native token manual. Contoh: 0.084"
    )
    async def gascalc(
        self,
        interaction: discord.Interaction,
        chain: app_commands.Choice[str],
        wallet: str = None,
        address: str = None,
        balance: float = None,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        chain_key = chain.value
        selected_address = None
        source_label = None
        final_balance = None

        saved_wallets = get_saved_wallets(interaction.user.id)

        try:
            wallet_selector = parse_wallet_selector(wallet)

            # Priority:
            # 1. manual balance can override balance source, but wallet/address can still label it
            # 2. wallet selector
            # 3. address
            # 4. auto single wallet
            # 5. balance only
            if isinstance(wallet_selector, int):
                if wallet_selector < 1 or wallet_selector > len(saved_wallets):
                    await interaction.followup.send(
                        f"❌ Wallet nomor `{wallet_selector}` tidak ditemukan. Kamu punya `{len(saved_wallets)}` wallet tersimpan.",
                        ephemeral=True,
                    )
                    return

                selected_address = saved_wallets[wallet_selector - 1]
                source_label = f"Vault #{wallet_selector}"

            elif isinstance(wallet_selector, str) and is_valid_address(wallet_selector):
                selected_address = normalize_address(wallet_selector)
                source_label = "Wallet Field Address"

            elif address:
                if not is_valid_address(address):
                    await interaction.followup.send("❌ Address tidak valid.", ephemeral=True)
                    return

                selected_address = normalize_address(address)
                source_label = "Manual Address"

            elif balance is None and len(saved_wallets) == 1:
                selected_address = saved_wallets[0]
                source_label = "Vault #1"

            elif balance is None:
                if saved_wallets:
                    await interaction.followup.send(
                        "❌ Isi salah satu: `wallet`, `address`, atau `balance`.\n"
                        f"Kamu punya `{len(saved_wallets)}` wallet. Pilih `wallet: 1` sampai `wallet: {len(saved_wallets)}`.",
                        ephemeral=True,
                    )
                else:
                    await interaction.followup.send(
                        "❌ Isi salah satu: `wallet`, `address`, atau `balance`.",
                        ephemeral=True,
                    )
                return

            if balance is not None:
                if balance <= 0:
                    await interaction.followup.send("❌ Balance harus lebih dari 0.", ephemeral=True)
                    return

                final_balance = float(balance)
                if source_label:
                    source_label = f"{source_label} + Manual Balance"
                else:
                    source_label = "Manual Balance"

            async with aiohttp.ClientSession() as session:
                tiers = await get_gas_tiers(session, chain_key)
                price, price_source = await get_price(session, chain_key)

                if final_balance is None:
                    if not selected_address:
                        await interaction.followup.send(
                            "❌ Tidak ada wallet/address untuk dibaca balance-nya.",
                            ephemeral=True,
                        )
                        return

                    final_balance = await get_balance(session, chain_key, selected_address)

            text = build_calc_text(
                chain_key=chain_key,
                tiers=tiers,
                price=price,
                price_source=price_source,
                balance=final_balance,
                source_label=source_label,
                address=selected_address,
            )

            embed = discord.Embed(
                description=text,
                color=0x22C55E,
            )
            embed.set_footer(text="Private gas calculator • Estimasi kasar, gas aktual bisa berbeda")

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            await interaction.followup.send(
                f"❌ Gagal hitung gas `{chain_key}`: `{str(e)[:180]}`",
                ephemeral=True,
            )


async def setup(bot):
    await bot.add_cog(GasCog(bot))
