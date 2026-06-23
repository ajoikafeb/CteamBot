import json
import asyncio
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta
from decimal import Decimal, getcontext

import aiohttp
import requests
import discord
from discord import app_commands
from discord.ext import commands, tasks

try:
    from wallet import get_user_wallets as vault_get_user_wallets
except Exception:
    vault_get_user_wallets = None

getcontext().prec = 50

CHAIN_ID = 16661
EVO_API = "https://api.evoevo.ai/v1"
BASE_API = EVO_API
WORLD_CUP_CAMPAIGN_PATH = "world-cup"
WORLD_CUP_PERIOD = "worldcup"
ZERO_G_API = "https://api.0g.exploreme.pro/api/v2"

REQUEST_TIMEOUT = 20
POINT_PER_MEMORY = 50
MAX_ADDRESS_PER_CHECK = 10
MAX_AGENT_DISPLAY = 12
WALLET_CACHE_TTL = 90
AGENT_DETAIL_CACHE_TTL = 180

LEADERBOARD_LIMIT = 20
LEADERBOARD_SCAN_LIMIT = 100
MAX_PORTFOLIO_ADDRESS = 10

AUTO_DELETE_SECONDS = 180
SNAPSHOT_HOUR_UTC = 0
LEADERBOARD_CACHE_TTL = 300
TX_CACHE_TTL = 90
SBT_CACHE_TTL = 300

DATA_DIR = Path("data")
PROGRESS_DIR = Path("data/progress_tracker")
PROGRESS_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR = PROGRESS_DIR / "api_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
PORTFOLIO_STATE_FILE = DATA_DIR / "portfolio_states.json"

_AGENT_LIST_CACHE = {}
_AGENT_DETAIL_CACHE = {}

PERIOD_LABELS = {
    "daily": "Daily",
    "weekly": "Weekly",
    "total": "All Time",
}
VALID_LIMITS = [20, 50, 100]
VALID_PERIODS = ["daily", "weekly", "total", WORLD_CUP_PERIOD]


# =============================================================================
# SHARED HELPERS
# =============================================================================

def mask_wallet(address):
    if not address:
        return "-"
    return address[:6] + "..." + address[-4:]


def mask_address(address):
    if not address:
        return "-"
    address = address.strip()
    return f"{address[:6]}...{address[-4:]}" if len(address) >= 12 else address


def normalize_address(address):
    return (address or "").strip().lower()


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


def parse_addresses(text):
    if not text:
        return []
    parts = str(text).replace("\n", ",").split(",")
    addresses = []
    for part in parts:
        addr = part.strip().lower()
        if addr:
            addresses.append(addr)
    return list(dict.fromkeys(addresses))


def to_int(value, default=0):
    try:
        if value is None:
            return default
        return int(float(value))
    except Exception:
        return default


def to_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def format_number(value):
    try:
        return f"{int(value):,}"
    except Exception:
        return str(value)


def format_percent(value):
    try:
        value = float(value)
        if value <= 1:
            value *= 100
        return f"{value:.2f}".rstrip("0").rstrip(".") + "%"
    except Exception:
        return "0%"


def format_return(value):
    try:
        if value is None:
            return "-"
        return f"{float(value):.2f}".rstrip("0").rstrip(".") + "%"
    except Exception:
        return "-"


def fmt_short(value):
    try:
        value = int(float(value))
    except Exception:
        return "0"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(value)


def fmt_full(value):
    try:
        return f"{int(float(value)):,}"
    except Exception:
        return "0"


def format_0g_balance(raw_balance):
    try:
        value = Decimal(str(raw_balance)) / Decimal(10 ** 18)
        return f"{value:.4f}".rstrip("0").rstrip(".")
    except Exception:
        return "0"


def pick_first(*values, default=None):
    for value in values:
        if value is not None:
            return value
    return default


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def now_ts():
    return int(datetime.now(timezone.utc).timestamp())


def date_key(dt=None):
    dt = dt or datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%d")


# =============================================================================
# HTTP HELPERS
# =============================================================================

def headers(origin="https://evoevo.ai", referer="https://evoevo.ai/"):
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
        "Origin": origin,
        "Referer": referer,
    }


def safe_get(url, params=None, origin="https://evoevo.ai", referer="https://evoevo.ai/"):
    try:
        res = requests.get(
            url,
            params=params,
            headers=headers(origin=origin, referer=referer),
            timeout=REQUEST_TIMEOUT,
        )
        if res.status_code != 200:
            return None, f"{res.status_code}: {res.text[:180]}"
        try:
            return res.json(), None
        except Exception:
            return None, "invalid json"
    except requests.exceptions.Timeout:
        return None, "timeout"
    except requests.exceptions.ConnectionError:
        return None, "connection error"
    except Exception as e:
        return None, str(e)


def parse_api_items(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ["items", "agents", "data", "results", "predictions"]:
            value = data.get(key)
            if isinstance(value, list):
                return value
        nested = data.get("data")
        if isinstance(nested, dict):
            for key in ["items", "agents", "results"]:
                value = nested.get(key)
                if isinstance(value, list):
                    return value
    return []


# =============================================================================
# JSON HELPERS
# =============================================================================

def load_json(path, default=None):
    if default is None:
        default = {} if path.suffix != ".json" or "rewards" not in str(path) else []
    try:
        path = Path(path)
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def cache_key_from(url, params=None):
    raw = url
    if params:
        ordered = "&".join(f"{k}={params[k]}" for k in sorted(params.keys()))
        raw = f"{url}?{ordered}"
    safe = "".join(c if c.isalnum() else "_" for c in raw)
    return safe[:180]


def cache_file_for(key):
    return CACHE_DIR / f"{key}.json"


def load_api_cache(key):
    data = load_json(cache_file_for(key), None)
    if not isinstance(data, dict) or "saved_at" not in data or "data" not in data:
        return None
    return data


def save_api_cache(key, data):
    save_json(
        cache_file_for(key),
        {
            "saved_at": now_ts(),
            "saved_at_iso": datetime.now(timezone.utc).isoformat(),
            "data": data,
        },
    )


# =============================================================================
# RESOLVE ADDRESSES
# =============================================================================

def resolve_addresses_for_user(user_id, address_text=None, allow_state_addresses=False):
    addresses = parse_addresses(address_text)
    if addresses:
        return addresses
    if vault_get_user_wallets:
        try:
            saved = vault_get_user_wallets(user_id)
            if saved:
                return list(dict.fromkeys(saved))
        except Exception:
            pass
    if allow_state_addresses:
        return get_state_addresses(user_id)
    return []


# =============================================================================
# CEKAGENT HELPERS
# =============================================================================

def parse_agent_basic(item):
    profile = item.get("agent_profile") or {}
    identity = (
        item.get("onchain_identity")
        or profile.get("onchain_identity")
        or {}
    )
    if not isinstance(identity, dict):
        identity = {}
    item_chain_id = identity.get("chain_id") or item.get("chain_id")
    if item_chain_id is not None:
        try:
            if int(item_chain_id) != int(CHAIN_ID):
                return None
        except Exception:
            return None
    if item.get("active") is False:
        return None
    status = str(item.get("status") or item.get("run_status") or "").lower()
    onchain_status = str(item.get("onchain_status") or "").lower()
    identity_status = str(identity.get("status") or "").lower()
    if status and status not in ["active", "ok", "running", "deployed"]:
        return None
    if onchain_status and onchain_status not in ["bound", "active", "ok"]:
        return None
    if identity_status and identity_status not in ["bound", "active", "ok"]:
        return None
    agent_id = pick_first(
        item.get("id"), item.get("agent_id"),
        profile.get("id"), profile.get("agent_id"),
    )
    if not agent_id:
        return None
    try:
        agent_id = int(agent_id)
    except Exception:
        return None
    token_id = pick_first(
        item.get("token_id"), identity.get("identity_agent_id"),
        identity.get("token_id"), default="-",
    )
    name = pick_first(
        item.get("display_name"), item.get("name"), item.get("title"),
        profile.get("display_name"), profile.get("name"), default="-",
    )
    wallet = pick_first(
        identity.get("agent_wallet_address"), identity.get("wallet_address"),
        item.get("wallet_address"), item.get("owner"),
        item.get("address"), default="-",
    )
    return {
        "agent_id": agent_id,
        "identity_agent_id": token_id,
        "token_id": token_id,
        "name": str(name or "-"),
        "wallet": wallet,
        "level": "-",
        "memories": to_int(item.get("memory_count") or item.get("memories")),
        "estimated_points": to_int(item.get("memory_count") or item.get("memories")) * POINT_PER_MEMORY,
        "predictions": 0, "settled": 0, "wins": 0, "losses": 0,
        "win_rate": 0, "streak": 0, "expected_return": None,
        "avatar_url": item.get("avatar_url"), "source": "list",
    }


def parse_agent_home(agent_id, data):
    agent = data.get("agent") or {}
    overview = data.get("overview") or {}
    prediction_counts = data.get("prediction_counts") or {}
    owner_overview = data.get("owner_overview") or {}
    identity = agent.get("onchain_identity") or {}
    name = pick_first(agent.get("name"), agent.get("display_name"), default="-")
    memories = pick_first(
        owner_overview.get("adopted_memory_count"), overview.get("memory_count"),
        overview.get("adoption_count"), agent.get("memory_count"), default=0,
    )
    predictions = pick_first(
        prediction_counts.get("all"), overview.get("total_predictions"),
        overview.get("priced_predictions"), agent.get("total_predictions"), default=0,
    )
    settled = pick_first(prediction_counts.get("settled"), overview.get("settled_predictions"), default=0)
    wins = pick_first(overview.get("settled_wins"), agent.get("total_wins"), default=0)
    losses = pick_first(overview.get("settled_losses"), default=0)
    win_rate = pick_first(overview.get("win_rate"), agent.get("win_rate"), default=0)
    streak = pick_first(overview.get("current_win_streak"), agent.get("current_win_streak"), default=0)
    expected_return = pick_first(overview.get("potential_return_pct"), default=None)
    memories = to_int(memories)
    return {
        "agent_id": to_int(agent.get("id") or agent_id),
        "identity_agent_id": str(identity.get("identity_agent_id") or "-"),
        "name": str(name or "-"),
        "wallet": agent.get("wallet_address") or identity.get("agent_wallet_address") or "-",
        "level": agent.get("level", "-"),
        "memories": memories,
        "estimated_points": memories * POINT_PER_MEMORY,
        "predictions": to_int(predictions), "settled": to_int(settled),
        "wins": to_int(wins), "losses": to_int(losses),
        "win_rate": to_float(win_rate), "streak": to_int(streak),
        "expected_return": expected_return,
        "avatar_url": agent.get("avatar_url"), "source": "home",
    }


def fetch_agent_home_uncached(agent_id):
    data, err = safe_get(f"{BASE_API}/agents/{agent_id}/home", params={"limit": 20, "chain_id": CHAIN_ID})
    if err:
        return None, err
    if not isinstance(data, dict):
        return None, "invalid home data"
    return parse_agent_home(agent_id, data), None


def fetch_agent_home(agent_id):
    cache_key = str(agent_id)
    now = time.time()
    cached = _AGENT_DETAIL_CACHE.get(cache_key)
    if cached:
        age = now - cached.get("time", 0)
        if age <= AGENT_DETAIL_CACHE_TTL:
            return cached.get("agent"), None, True
    agent, err = fetch_agent_home_uncached(agent_id)
    if not err and agent:
        _AGENT_DETAIL_CACHE[cache_key] = {"time": now, "agent": agent}
    if err and cached:
        return cached.get("agent"), None, True
    return agent, err, False


def get_agent_ids_by_wallet_uncached(address):
    data, err = safe_get(f"{BASE_API}/agents", params={"wallet_address": address, "chain_id": CHAIN_ID})
    if err:
        return [], err
    items = parse_api_items(data)
    agents = []
    for item in items:
        if not isinstance(item, dict):
            continue
        agent = parse_agent_basic(item)
        if agent:
            agents.append(agent)
    dedup = {}
    for agent in agents:
        dedup[agent["agent_id"]] = agent
    return list(dedup.values()), None


def get_agents_by_wallet(address):
    cache_key = f"{address.lower()}:{CHAIN_ID}"
    now = time.time()
    cached = _AGENT_LIST_CACHE.get(cache_key)
    if cached:
        age = now - cached.get("time", 0)
        if age <= WALLET_CACHE_TTL:
            return cached.get("agents", []), None, True
    basic_agents, err = get_agent_ids_by_wallet_uncached(address)
    if err:
        if cached:
            return cached.get("agents", []), None, True
        return [], err, False
    final_agents = []
    used_cache = False
    for basic in basic_agents:
        detail, detail_err, detail_cache = fetch_agent_home(basic["agent_id"])
        if detail:
            final_agents.append(detail)
            if detail_cache:
                used_cache = True
        else:
            final_agents.append(basic)
    final_agents.sort(key=lambda x: (x.get("memories", 0), x.get("estimated_points", 0), x.get("predictions", 0), x.get("win_rate", 0), x.get("streak", 0)), reverse=True)
    _AGENT_LIST_CACHE[cache_key] = {"time": now, "agents": final_agents}
    return final_agents, None, used_cache


def build_summary(agents):
    total_agent = len(agents)
    total_memory = sum(to_int(a.get("memories")) for a in agents)
    total_estimated_points = sum(to_int(a.get("estimated_points")) for a in agents)
    total_prediction = sum(to_int(a.get("predictions")) for a in agents)
    total_settled = sum(to_int(a.get("settled")) for a in agents)
    total_wins = sum(to_int(a.get("wins")) for a in agents)
    total_losses = sum(to_int(a.get("losses")) for a in agents)
    win_rates = [to_float(a.get("win_rate")) for a in agents if a.get("win_rate") is not None]
    avg_win_rate = sum(win_rates) / len(win_rates) if win_rates else 0
    best_agent = None
    if agents:
        best_agent = sorted(agents, key=lambda x: (to_int(x.get("memories")), to_int(x.get("estimated_points")), to_int(x.get("predictions")), to_float(x.get("win_rate")), to_int(x.get("streak"))), reverse=True)[0]
    return {
        "total_agent": total_agent, "total_memory": total_memory,
        "total_estimated_points": total_estimated_points,
        "total_prediction": total_prediction, "total_settled": total_settled,
        "total_wins": total_wins, "total_losses": total_losses,
        "avg_win_rate": avg_win_rate, "best_agent": best_agent,
    }


def make_agent_line(index, agent):
    name = agent.get("name") or "-"
    agent_id = agent.get("agent_id")
    identity_id = agent.get("identity_agent_id") or "-"
    level = agent.get("level")
    memories = format_number(agent.get("memories", 0))
    estimated_points = format_number(agent.get("estimated_points", 0))
    predictions = format_number(agent.get("predictions", 0))
    settled = format_number(agent.get("settled", 0))
    wins = format_number(agent.get("wins", 0))
    losses = format_number(agent.get("losses", 0))
    win_rate = format_percent(agent.get("win_rate", 0))
    streak = format_number(agent.get("streak", 0))
    expected_return = format_return(agent.get("expected_return"))
    return (
        f"**{index}. {name}**\n"
        f"API ID `#{agent_id}` • Identity `#{identity_id}`\n"
        f"Lv `{level}` • 🔥 `{streak}` • 📈 Return `{expected_return}`\n"
        f"📝 Mem `{memories}` • 💎 Est `{estimated_points}` pts\n"
        f"🎯 Pred `{predictions}` • Settled `{settled}` • ✅ `{wins}` / ❌ `{losses}`\n"
        f"🏆 WR `{win_rate}`"
    )


def build_agent_embed(address, agents, from_cache=False, err=None):
    embed = discord.Embed(
        title="🤖 EVOEVO AGENT SUMMARY",
        description=f"Wallet: `{mask_wallet(address)}`",
        color=0x8B5CF6,
    )
    if err:
        embed.add_field(name="❌ Error", value=f"`{err}`", inline=False)
        return embed
    if not agents:
        embed.add_field(name="📊 Summary", value="Tidak ada agent ditemukan.", inline=False)
        embed.set_footer(text="EvoEvo Agent Summary • Powered by Me")
        return embed
    summary = build_summary(agents)
    best = summary["best_agent"]
    best_text = "-"
    if best:
        best_text = (
            f"{best.get('name')} `#{best.get('agent_id')}`\n"
            f"📝 Highest Memory: `{format_number(best.get('memories', 0))}`\n"
            f"💎 Est. Points: `{format_number(best.get('estimated_points', 0))}`"
        )
    summary_text = (
        f"**Total Agent:** `{format_number(summary['total_agent'])}`\n"
        f"**Total Memory:** `{format_number(summary['total_memory'])}`\n"
        f"**Est. Total Points:** `{format_number(summary['total_estimated_points'])}`\n"
        f"**Total Prediction:** `{format_number(summary['total_prediction'])}`\n"
        f"**Settled:** `{format_number(summary['total_settled'])}`\n"
        f"**Wins/Losses:** `{format_number(summary['total_wins'])}` / `{format_number(summary['total_losses'])}`\n"
        f"**Avg Win Rate:** `{format_percent(summary['avg_win_rate'])}`\n\n"
        f"🏅 **Best Agent:** {best_text}"
    )
    embed.add_field(name="📊 Summary", value=summary_text, inline=False)
    display_agents = agents[:MAX_AGENT_DISPLAY]
    agent_lines = []
    for idx, agent in enumerate(display_agents, start=1):
        agent_lines.append(make_agent_line(idx, agent))
    if len(agents) > MAX_AGENT_DISPLAY:
        agent_lines.append(f"...dan `{len(agents) - MAX_AGENT_DISPLAY}` agent lainnya.")
    embed.add_field(name="📋 Agent List", value="\n\n".join(agent_lines)[:3900], inline=False)
    all_ids = ",".join(str(a["agent_id"]) for a in agents)
    if len(all_ids) <= 950:
        embed.add_field(name="📌 Copy Agent IDs", value=f"```txt\n{all_ids}\n```", inline=False)
    embed.add_field(name="ℹ️ Note", value=f"Est. Points dihitung dari `memory × {POINT_PER_MEMORY}`.\nIni estimasi reward Add Memory, bukan poin resmi leaderboard EvoEvo.", inline=False)
    cache_text = "cache" if from_cache else "fresh"
    embed.set_footer(text=f"EvoEvo Agent Summary • {cache_text} • wallet cache {WALLET_CACHE_TTL}s • detail cache {AGENT_DETAIL_CACHE_TTL}s • Powered by Me")
    return embed


def build_total_summary_embed(addresses, wallet_results):
    total_wallet = len(addresses)
    total_agent = 0
    total_memory = 0
    total_estimated_points = 0
    total_prediction = 0
    total_settled = 0
    total_wins = 0
    total_losses = 0
    best_global = None
    for result in wallet_results:
        agents = result.get("agents", [])
        total_agent += len(agents)
        total_memory += sum(to_int(a.get("memories")) for a in agents)
        total_estimated_points += sum(to_int(a.get("estimated_points")) for a in agents)
        total_prediction += sum(to_int(a.get("predictions")) for a in agents)
        total_settled += sum(to_int(a.get("settled")) for a in agents)
        total_wins += sum(to_int(a.get("wins")) for a in agents)
        total_losses += sum(to_int(a.get("losses")) for a in agents)
        for agent in agents:
            if best_global is None:
                best_global = agent
            else:
                current_score = (to_int(agent.get("memories")), to_int(agent.get("estimated_points")), to_int(agent.get("predictions")), to_float(agent.get("win_rate")))
                best_score = (to_int(best_global.get("memories")), to_int(best_global.get("estimated_points")), to_int(best_global.get("predictions")), to_float(best_global.get("win_rate")))
                if current_score > best_score:
                    best_global = agent
    embed = discord.Embed(title="🧾 AGENT SUMMARY", color=0x22C55E)
    value = (
        f"**Wallet:** `{format_number(total_wallet)}`\n"
        f"**Agent:** `{format_number(total_agent)}`\n"
        f"**Memory:** `{format_number(total_memory)}`\n"
        f"**Est. Points:** `{format_number(total_estimated_points)}`\n"
        f"**Prediction:** `{format_number(total_prediction)}`\n"
        f"**Settled:** `{format_number(total_settled)}`\n"
        f"**Wins/Losses:** `{format_number(total_wins)}` / `{format_number(total_losses)}`"
    )
    if best_global:
        value += (
            f"\n\n🏅 **Best Agent:** {best_global.get('name')} `#{best_global.get('agent_id')}`\n"
            f"📝 **Highest Memory:** `{format_number(best_global.get('memories', 0))}`\n"
            f"💎 **Est. Points:** `{format_number(best_global.get('estimated_points', 0))}`"
        )
    embed.add_field(name="📊 Total", value=value, inline=False)
    embed.add_field(name="ℹ️ Note", value=f"Est. Points = `memory × {POINT_PER_MEMORY}`.\nDigunakan sebagai estimasi Add Memory reward, bukan angka resmi leaderboard.", inline=False)
    embed.set_footer(text="EvoEvo Agent Summary • Multi-wallet summary")
    return embed


# =============================================================================
# PORTFOLIO HELPERS
# =============================================================================

def is_active_chain_agent(item):
    identity = item.get("onchain_identity") or {}
    if not isinstance(identity, dict):
        identity = {}
    item_chain_id = identity.get("chain_id") or item.get("chain_id")
    if item_chain_id is not None:
        try:
            if int(item_chain_id) != int(CHAIN_ID):
                return False
        except Exception:
            return False
    if item.get("active") is False:
        return False
    status = str(item.get("status") or item.get("run_status") or "").lower()
    onchain_status = str(item.get("onchain_status") or "").lower()
    identity_status = str(identity.get("status") or "").lower()
    if status and status not in ["active", "ok", "running", "deployed"]:
        return False
    if onchain_status and onchain_status not in ["bound", "active", "ok"]:
        return False
    if identity_status and identity_status not in ["bound", "active", "ok"]:
        return False
    return True


def get_agents(address):
    data, err = safe_get(f"{EVO_API}/agents", params={"wallet_address": address, "chain_id": CHAIN_ID})
    if err:
        return [], err
    items = parse_api_items(data)
    agents = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if not is_active_chain_agent(item):
            continue
        agent_id = item.get("id") or item.get("agent_id")
        if not agent_id:
            continue
        try:
            agent_id = int(agent_id)
        except Exception:
            continue
        if agent_id not in agents:
            agents.append(agent_id)
    return agents, None


def get_0g_account(address):
    data, err = safe_get(f"{ZERO_G_API}/accounts/{address}", origin="https://0g.exploreme.pro", referer="https://0g.exploreme.pro/")
    if err:
        return {"ok": False, "balance": "0", "tx_count": 0, "nonce": 0, "error": err}
    return {"ok": True, "balance": format_0g_balance(data.get("balance") or "0"), "tx_count": to_int(data.get("tx_count")), "nonce": to_int(data.get("nonce")), "error": None}


def get_sbt_status(address):
    data, err = safe_get(f"{EVO_API}/sbt/status", params={"wallet": address}, origin="https://event.evoevo.ai", referer="https://event.evoevo.ai/")
    result = {"orbit": False, "vector": False, "zenith": False, "error": None}
    if err:
        result["error"] = err
        return result
    if isinstance(data, dict):
        items = data.get("items") or data.get("data") or data.get("levels") or []
    elif isinstance(data, list):
        items = data
    else:
        items = []
    for item in items:
        if not isinstance(item, dict):
            continue
        level = to_int(item.get("level"))
        minted = bool(item.get("minted"))
        if level == 1 and minted:
            result["orbit"] = True
        elif level == 2 and minted:
            result["vector"] = True
        elif level == 3 and minted:
            result["zenith"] = True
    return result


def fetch_leaderboard(period="total", limit=None):
    data, err = safe_get(f"{EVO_API}/leaderboards/users/points", params={"limit": int(limit or LEADERBOARD_LIMIT), "period": period, "chain_id": CHAIN_ID})
    if err:
        return [], err
    if isinstance(data, dict):
        return data.get("items", []), None
    if isinstance(data, list):
        return data, None
    return [], None


def wallet_of(item):
    if not isinstance(item, dict):
        return ""
    return str(item.get("wallet_address") or item.get("wallet") or item.get("address") or item.get("user_address") or item.get("userAddress") or "").lower()


def rank_of(item):
    if not isinstance(item, dict):
        return None
    rank = item.get("rank") or item.get("rank_label") or item.get("position")
    try:
        return int(rank)
    except Exception:
        return None


def with_wallet_source(item, source):
    if not isinstance(item, dict):
        return item
    out = dict(item)
    out["_source"] = source
    return out


def extract_wallet_item(data, wallet):
    target = str(wallet or "").lower()
    if isinstance(data, dict):
        for key in ["me", "user", "wallet", "account", "profile", "result"]:
            value = data.get(key)
            if isinstance(value, dict) and wallet_of(value) in ["", target]:
                return value
        nested = data.get("data")
        if isinstance(nested, dict):
            for key in ["me", "user", "wallet", "account", "profile", "result"]:
                value = nested.get(key)
                if isinstance(value, dict) and wallet_of(value) in ["", target]:
                    return value
        items = parse_api_items(data)
    elif isinstance(data, list):
        items = data
    else:
        items = []
    for item in items:
        if isinstance(item, dict) and wallet_of(item) == target:
            return item
    return None


def fetch_wallet_leaderboard_item(wallet, period="total"):
    wallet = str(wallet or "").lower()
    if not wallet:
        return None, "empty wallet"
    period_candidates = ["total", "all_time", "all-time", "overall"] if period == "total" else [period]
    wallet_param_candidates = ["wallet", "wallet_address", "address"]
    best_item = None
    best_err = None
    for p in period_candidates:
        for wallet_param in wallet_param_candidates:
            data, err = safe_get(f"{EVO_API}/leaderboards/users/points", params={"limit": LEADERBOARD_LIMIT, "period": p, "chain_id": CHAIN_ID, wallet_param: wallet})
            if err:
                best_err = err
                continue
            item = extract_wallet_item(data, wallet)
            if not item:
                continue
            item = with_wallet_source(item, f"wallet_lookup:{p}:{wallet_param}")
            if best_item is None or points_of(item) > points_of(best_item):
                best_item = item
    return best_item, best_err


def prefer_fresher_rank_item(global_item, wallet_item):
    if not isinstance(wallet_item, dict):
        return global_item
    if not isinstance(global_item, dict):
        return wallet_item
    gp = points_of(global_item)
    wp = points_of(wallet_item)
    if wp and wp >= gp:
        return wallet_item
    if wp == gp and rank_of(wallet_item):
        return wallet_item
    return global_item


def find_prev_for_rank(items, current_item, fallback_prev=None):
    rank = rank_of(current_item)
    if not rank:
        return fallback_prev
    best_prev = None
    for item in items or []:
        if not isinstance(item, dict):
            continue
        item_rank = rank_of(item)
        if item_rank is None:
            continue
        if item_rank < rank:
            if best_prev is None or item_rank > rank_of(best_prev):
                best_prev = item
    return best_prev or fallback_prev


def fetch_worldcup_leaderboard(wallet=None):
    params = {"limit": LEADERBOARD_LIMIT, "chain_id": CHAIN_ID}
    if wallet:
        params["wallet"] = wallet.lower()
    data, err = safe_get(f"{EVO_API}/leaderboards/campaigns/{WORLD_CUP_CAMPAIGN_PATH}/points", params=params)
    if err:
        return [], None, err
    items = []
    me = None
    if isinstance(data, dict):
        items = data.get("items", []) or []
        me = data.get("me") if isinstance(data.get("me"), dict) else None
    elif isinstance(data, list):
        items = data
    return items, me, None


def points_of(item):
    if not item:
        return 0
    return to_int(item.get("total_points") or item.get("points") or item.get("user_points"))


def find_rank(items, address):
    address = address.lower()
    for idx, item in enumerate(items):
        wallet = wallet_of(item)
        if wallet == address:
            prev_item = items[idx - 1] if idx > 0 else None
            return with_wallet_source(item, "global_list"), prev_item
    return None, None


def top_threshold(items):
    if not items:
        return 0
    return points_of(items[-1])


def gap_to_target(items, current_item, target_rank):
    if not current_item:
        return None
    if len(items) < target_rank:
        return None
    target = items[target_rank - 1]
    gap = points_of(target) - points_of(current_item)
    if gap <= 0:
        return 0
    return gap + 1


def gap_to_prev(current_item, prev_item):
    if not current_item:
        return None
    if not prev_item:
        return 0
    gap = points_of(prev_item) - points_of(current_item)
    return max(gap + 1, 0)


def rank_text(item, items, period="total"):
    if item:
        rank = rank_of(item) or item.get("rank") or item.get("rank_label") or "-"
        pts = points_of(item)
        return f"#{rank} • {format_number(pts)} pts"
    threshold = top_threshold(items)
    if threshold > 0:
        return f"Outside Top {len(items)} • Top {len(items)} min {format_number(threshold)} pts"
    return "Not found"


def sbt_icon(value):
    return "✅" if value else "❌"


def agent_ids_text(agents):
    if not agents:
        return "-"
    text = ", ".join(str(x) for x in agents[:12])
    if len(agents) > 12:
        text += f" +{len(agents) - 12} more"
    return text


def build_wallet_embed_porto(addr, data):
    agents = data["agents"]
    account = data["account"]
    sbt = data["sbt"]
    ranks = data["ranks"]
    worldcup = data.get("worldcup", {})
    total_item = ranks["total"]["item"]
    total_items = ranks["total"]["items"]
    embed = discord.Embed(
        title="👤 EVOEVO PROFILE",
        description=f"Wallet: `{mask_wallet(addr)}`",
        color=0x8B5CF6,
    )
    total_points = points_of(total_item)
    total_source = str((total_item or {}).get("_source") or "")
    if total_item:
        total_points_text = format_number(total_points)
        if total_source.startswith("wallet_lookup"):
            total_points_text += " • synced"
    else:
        total_points_text = "Not in Top 100"
    embed.add_field(name="🤖 Agents", value=f"**Total Agent:** `{len(agents)}`\n**Agent IDs:** `{agent_ids_text(agents)}`", inline=False)
    embed.add_field(name="🏆 Points", value=f"**Total Points:** `{total_points_text}`", inline=False)
    wc_item = worldcup.get("item")
    wc_me = worldcup.get("me")
    wc_items = worldcup.get("items") or []
    wc_points = points_of(wc_item) if wc_item else to_int((wc_me or {}).get("total_points"))
    wc_rank = (wc_item or wc_me or {}).get("rank") or (wc_item or wc_me or {}).get("rank_label")
    if wc_rank:
        wc_text = f"#{wc_rank} • {format_number(wc_points)} pts"
    elif wc_points:
        wc_text = f"Outside Top {len(wc_items) or 100} • {format_number(wc_points)} pts"
    else:
        threshold = top_threshold(wc_items)
        wc_text = f"0 pts • Top {len(wc_items) or 100} min {format_number(threshold)} pts"
    embed.add_field(name="⚽ World Cup Campaign", value=f"`{wc_text}`", inline=False)
    daily_text = rank_text(ranks["daily"]["item"], ranks["daily"]["items"], "daily")
    weekly_text = rank_text(ranks["weekly"]["item"], ranks["weekly"]["items"], "weekly")
    total_text = rank_text(ranks["total"]["item"], ranks["total"]["items"], "total")
    rank_value = f"**Daily:** `{daily_text}`\n**Weekly:** `{weekly_text}`\n**All Time:** `{total_text}`"
    embed.add_field(name="📈 Rank", value=rank_value, inline=False)
    if total_item:
        prev_gap = gap_to_prev(total_item, ranks["total"]["prev"])
        top10_gap = gap_to_target(total_items, total_item, 10)
        top50_gap = gap_to_target(total_items, total_item, 50)
        top100_gap = gap_to_target(total_items, total_item, 100)
        gap_lines = []
        gap_lines.append(f"**Naik 1 Rank:** `{format_number(prev_gap)}`")
        gap_lines.append(f"**Top 10:** `{'Done' if top10_gap == 0 else format_number(top10_gap)}`")
        gap_lines.append(f"**Top 50:** `{'Done' if top50_gap == 0 else format_number(top50_gap)}`")
        gap_lines.append(f"**Top 100:** `{'Done' if top100_gap == 0 else format_number(top100_gap)}`")
        embed.add_field(name="🎯 Gap All Time", value="\n".join(gap_lines), inline=False)
    else:
        threshold = top_threshold(total_items)
        embed.add_field(name="🎯 Gap All Time", value=f"Rank outside Top `{len(total_items)}`.\nTop `{len(total_items)}` minimum: `{format_number(threshold)}` pts.", inline=False)
    embed.add_field(name="⛓️ 0G", value=f"**Balance:** `{account.get('balance')} 0G`\n**Total TX:** `{format_number(account.get('tx_count'))}`", inline=True)
    embed.add_field(name="🎖️ NeoSoul SBT", value=f"Orbit  {sbt_icon(sbt.get('orbit'))}\nVector {sbt_icon(sbt.get('vector'))}\nZenith {sbt_icon(sbt.get('zenith'))}", inline=True)
    warnings = []
    if data.get("agent_error"):
        warnings.append(f"Agent: {data['agent_error']}")
    if account.get("error"):
        warnings.append(f"0G: {account['error']}")
    if sbt.get("error"):
        warnings.append(f"SBT: {sbt['error']}")
    if warnings:
        embed.add_field(name="⚠️ Notes", value="\n".join(f"`{x}`" for x in warnings[:3]), inline=False)
    embed.set_footer(text="EvoEvo + 0G + NeoSoul • Rank endpoint only exposes Top 100")
    return embed


def load_portfolio_state_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not PORTFOLIO_STATE_FILE.exists():
        return {}
    try:
        data = json.loads(PORTFOLIO_STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_portfolio_state_db(data):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = PORTFOLIO_STATE_FILE.with_suffix(PORTFOLIO_STATE_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PORTFOLIO_STATE_FILE)


def get_state_addresses(user_id):
    db = load_portfolio_state_db()
    user_state = db.get(str(user_id), {})
    if not isinstance(user_state, dict):
        return []
    return [addr for addr, value in user_state.items() if is_valid_address(addr) and isinstance(value, dict)]


def get_rank_value(item):
    return rank_of(item)


def snapshot_from_wallet_data(addr, data):
    ranks = data.get("ranks", {})
    worldcup = data.get("worldcup", {})
    account = data.get("account", {})
    sbt = data.get("sbt", {})
    daily_item = ranks.get("daily", {}).get("item")
    weekly_item = ranks.get("weekly", {}).get("item")
    total_item = ranks.get("total", {}).get("item")
    wc_item = worldcup.get("item") or worldcup.get("me") or {}
    return {
        "address": addr,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "agents": len(data.get("agents", [])),
        "agent_ids": data.get("agents", [])[:25],
        "tx_count": to_int(account.get("tx_count")),
        "balance_0g": str(account.get("balance") or "0"),
        "sbt_orbit": bool(sbt.get("orbit")),
        "sbt_vector": bool(sbt.get("vector")),
        "sbt_zenith": bool(sbt.get("zenith")),
        "daily_rank": get_rank_value(daily_item),
        "daily_points": points_of(daily_item),
        "weekly_rank": get_rank_value(weekly_item),
        "weekly_points": points_of(weekly_item),
        "total_rank": get_rank_value(total_item),
        "total_points": points_of(total_item),
        "worldcup_rank": get_rank_value(wc_item),
        "worldcup_points": points_of(wc_item),
    }


def save_portfolio_snapshots(user_id, wallet_data):
    db = load_portfolio_state_db()
    key = str(user_id)
    user_state = db.get(key, {})
    if not isinstance(user_state, dict):
        user_state = {}
    saved = {}
    for addr, data in wallet_data.items():
        old_entry = user_state.get(addr, {}) if isinstance(user_state.get(addr), dict) else {}
        old_now = old_entry.get("now")
        snapshot = snapshot_from_wallet_data(addr, data)
        user_state[addr] = {"last": old_now, "now": snapshot}
        saved[addr] = {"last": old_now, "now": snapshot}
    db[key] = user_state
    save_portfolio_state_db(db)
    return saved


def get_portfolio_snapshots(user_id, addresses):
    db = load_portfolio_state_db()
    user_state = db.get(str(user_id), {})
    if not isinstance(user_state, dict):
        return {}
    out = {}
    for addr in addresses:
        entry = user_state.get(addr)
        if isinstance(entry, dict):
            out[addr] = entry
    return out


def fmt_rank(value):
    return f"#{value}" if value not in [None, "", 0] else "-"


def fmt_delta_number(old, new, suffix=""):
    old = to_int(old)
    new = to_int(new)
    delta = new - old
    sign = "+" if delta > 0 else ""
    return f"`{format_number(old)}` → `{format_number(new)}` ({sign}{format_number(delta)}{suffix})"


def fmt_delta_rank(old, new):
    if old in [None, ""] and new in [None, ""]:
        return "`-` → `-`"
    if old in [None, ""]:
        return f"`-` → `{fmt_rank(new)}`"
    if new in [None, ""]:
        return f"`{fmt_rank(old)}` → `-`"
    delta = int(old) - int(new)
    sign = "+" if delta > 0 else ""
    return f"`{fmt_rank(old)}` → `{fmt_rank(new)}` ({sign}{delta})"


def build_saved_state_embed(addresses, snapshots, title="📌 Last Portfolio Check"):
    embed = discord.Embed(title=title, color=0x38BDF8)
    if not snapshots:
        embed.description = "Belum ada portfolio state tersimpan. Jalankan `/portfolio state: now` dulu."
        return embed
    for addr in addresses[:10]:
        entry = snapshots.get(addr, {})
        snap = entry.get("now") or entry.get("last")
        if not snap:
            continue
        value = (
            f"Agents `{format_number(snap.get('agents', 0))}` • TX `{format_number(snap.get('tx_count', 0))}`\n"
            f"All Time `{fmt_rank(snap.get('total_rank'))}` • `{format_number(snap.get('total_points', 0))}` pts\n"
            f"World Cup `{fmt_rank(snap.get('worldcup_rank'))}` • `{format_number(snap.get('worldcup_points', 0))}` pts\n"
            f"Checked `{str(snap.get('checked_at', '-'))[:19].replace('T', ' ')}` UTC"
        )
        embed.add_field(name=f"`{addr}`", value=value, inline=False)
    embed.set_footer(text="State disimpan di data/portfolio_states.json")
    return embed


def build_compare_embeds(addresses, before_map, after_map):
    embeds = []
    summary = discord.Embed(title="📊 Portfolio Compare: Last Check → Now", color=0xF59E0B)
    if not before_map:
        summary.description = "Belum ada state sebelumnya. Hasil sekarang sudah disimpan sebagai baseline."
    else:
        total_old_tx = total_new_tx = 0
        total_old_agents = total_new_agents = 0
        total_old_wc = total_new_wc = 0
        total_old_points = total_new_points = 0
        for addr in addresses:
            old = before_map.get(addr) or {}
            new = after_map.get(addr) or {}
            total_old_tx += to_int(old.get("tx_count"))
            total_new_tx += to_int(new.get("tx_count"))
            total_old_agents += to_int(old.get("agents"))
            total_new_agents += to_int(new.get("agents"))
            total_old_wc += to_int(old.get("worldcup_points"))
            total_new_wc += to_int(new.get("worldcup_points"))
            total_old_points += to_int(old.get("total_points"))
            total_new_points += to_int(new.get("total_points"))
        summary.add_field(name="Wallets", value=f"`{len(addresses)}`", inline=True)
        summary.add_field(name="Agents", value=fmt_delta_number(total_old_agents, total_new_agents), inline=True)
        summary.add_field(name="0G TX", value=fmt_delta_number(total_old_tx, total_new_tx), inline=True)
        summary.add_field(name="All Time Points", value=fmt_delta_number(total_old_points, total_new_points), inline=False)
        summary.add_field(name="World Cup Points", value=fmt_delta_number(total_old_wc, total_new_wc), inline=False)
    summary.set_footer(text="Compare otomatis menyimpan hasil now sebagai last check terbaru.")
    embeds.append(summary)
    for addr in addresses[:9]:
        old = before_map.get(addr)
        new = after_map.get(addr)
        embed = discord.Embed(title=f"🔎 Compare {mask_wallet(addr)}", description=f"`{addr}`", color=0xF59E0B)
        if not old:
            embed.add_field(name="State", value="Belum ada last check untuk wallet ini. Now disimpan sebagai baseline.", inline=False)
        elif not new:
            embed.add_field(name="State", value="Data now tidak tersedia.", inline=False)
        else:
            embed.add_field(name="Agents", value=fmt_delta_number(old.get("agents"), new.get("agents")), inline=True)
            embed.add_field(name="0G TX", value=fmt_delta_number(old.get("tx_count"), new.get("tx_count")), inline=True)
            embed.add_field(name="All Time Rank", value=fmt_delta_rank(old.get("total_rank"), new.get("total_rank")), inline=False)
            embed.add_field(name="All Time Points", value=fmt_delta_number(old.get("total_points"), new.get("total_points")), inline=False)
            embed.add_field(name="World Cup Rank", value=fmt_delta_rank(old.get("worldcup_rank"), new.get("worldcup_rank")), inline=False)
            embed.add_field(name="World Cup Points", value=fmt_delta_number(old.get("worldcup_points"), new.get("worldcup_points")), inline=False)
        embeds.append(embed)
    return embeds


def ensure_rank_context(addr, period, ui_items, item, prev):
    if item:
        return item, prev, ui_items
    scan_items, err = fetch_leaderboard(period, limit=LEADERBOARD_SCAN_LIMIT)
    if err or not scan_items:
        return item, prev, ui_items
    scan_item, scan_prev = find_rank(scan_items, addr)
    if scan_item:
        return scan_item, scan_prev, scan_items
    return item, prev, scan_items


def collect_portfolio_data(addresses, daily_items, weekly_items, total_items, worldcup_items):
    wallet_data = {}
    embeds = []
    for addr in addresses:
        agents, agent_err = get_agents(addr)
        account = get_0g_account(addr)
        sbt = get_sbt_status(addr)
        daily_item, daily_prev = find_rank(daily_items, addr)
        weekly_item, weekly_prev = find_rank(weekly_items, addr)
        total_item, total_prev = find_rank(total_items, addr)
        daily_item, daily_prev, daily_ctx_items = ensure_rank_context(addr, "daily", daily_items, daily_item, daily_prev)
        weekly_item, weekly_prev, weekly_ctx_items = ensure_rank_context(addr, "weekly", weekly_items, weekly_item, weekly_prev)
        total_item, total_prev, total_ctx_items = ensure_rank_context(addr, "total", total_items, total_item, total_prev)
        if not (total_item and str((total_item or {}).get("_source") or "") == "global_list"):
            total_me, _ = fetch_wallet_leaderboard_item(addr, "total")
            total_item = prefer_fresher_rank_item(total_item, total_me)
            total_prev = find_prev_for_rank(total_ctx_items, total_item, total_prev)
        worldcup_item, worldcup_prev = find_rank(worldcup_items, addr)
        _, worldcup_me, _ = fetch_worldcup_leaderboard(addr)
        if isinstance(worldcup_me, dict):
            worldcup_me = with_wallet_source(worldcup_me, "worldcup_me")
            worldcup_item = prefer_fresher_rank_item(worldcup_item, worldcup_me)
            worldcup_prev = find_prev_for_rank(worldcup_items, worldcup_item, worldcup_prev)
        wallet_data[addr] = {
            "agents": agents, "agent_error": agent_err, "account": account, "sbt": sbt,
            "ranks": {"daily": {"item": daily_item, "prev": daily_prev, "items": daily_ctx_items},
                      "weekly": {"item": weekly_item, "prev": weekly_prev, "items": weekly_ctx_items},
                      "total": {"item": total_item, "prev": total_prev, "items": total_ctx_items}},
            "worldcup": {"item": worldcup_item, "prev": worldcup_prev, "items": worldcup_items, "me": worldcup_me},
        }
        embeds.append(build_wallet_embed_porto(addr, wallet_data[addr]))
    return wallet_data, embeds


def build_summary_embed(addresses, wallet_data):
    total_tx = 0
    total_agents = 0
    ranked_wallets = []
    worldcup_ranked_wallets = []
    for addr in addresses:
        data = wallet_data.get(addr, {})
        total_tx += to_int(data.get("account", {}).get("tx_count"))
        total_agents += len(data.get("agents", []))
        total_item = data.get("ranks", {}).get("total", {}).get("item")
        if total_item:
            try:
                ranked_wallets.append((int(rank_of(total_item)), addr, points_of(total_item)))
            except Exception:
                pass
        wc_item = data.get("worldcup", {}).get("item")
        if wc_item:
            try:
                worldcup_ranked_wallets.append((int(rank_of(wc_item)), addr, points_of(wc_item)))
            except Exception:
                pass
    embed = discord.Embed(title="🧾 Portfolio Summary", color=0x22C55E)
    embed.add_field(name="Wallets", value=f"`{len(addresses)}`", inline=True)
    embed.add_field(name="Agents", value=f"`{format_number(total_agents)}`", inline=True)
    embed.add_field(name="Total TX", value=f"`{format_number(total_tx)}`", inline=True)
    if ranked_wallets:
        ranked_wallets.sort(key=lambda x: x[0])
        best_rank, best_addr, best_points = ranked_wallets[0]
        embed.add_field(name="Best All Time Rank", value=f"`{mask_wallet(best_addr)}` • `#{best_rank}` • `{format_number(best_points)} pts`", inline=False)
    if worldcup_ranked_wallets:
        worldcup_ranked_wallets.sort(key=lambda x: x[0])
        best_rank, best_addr, best_points = worldcup_ranked_wallets[0]
        embed.add_field(name="Best World Cup Rank", value=f"`{mask_wallet(best_addr)}` • `#{best_rank}` • `{format_number(best_points)} pts`", inline=False)
    return embed


# =============================================================================
# PROGRESS HELPERS
# =============================================================================

def snapshot_file(period, key):
    return PROGRESS_DIR / f"{period}_{key}.json"


def snapshot_rank_map(snapshot):
    out = {}
    for item in snapshot:
        addr = normalize_address(item.get("address"))
        if not addr:
            continue
        try:
            out[addr] = int(item.get("rank"))
        except Exception:
            pass
    return out


def get_snapshot_for_days(period, days):
    target_date = datetime.now(timezone.utc) - timedelta(days=days)
    target_key = date_key(target_date)
    exact = snapshot_file(period, target_key)
    if exact.exists():
        return target_key, load_json(exact, [])
    files = sorted(PROGRESS_DIR.glob(f"{period}_*.json"))
    if not files:
        return None, []
    target_dt = datetime.strptime(target_key, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    best_file = None
    best_diff = None
    for f in files:
        try:
            k = f.stem.replace(f"{period}_", "")
            dt = datetime.strptime(k, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            diff = abs((dt - target_dt).total_seconds())
            if best_diff is None or diff < best_diff:
                best_diff = diff
                best_file = f
        except Exception:
            continue
    if not best_file:
        return None, []
    best_key = best_file.stem.replace(f"{period}_", "")
    return best_key, load_json(best_file, [])


def progress_text(old_rank, new_rank, has_previous):
    if not has_previous:
        return "--"
    if old_rank is None and new_rank is not None:
        return "NEW"
    if old_rank is None or new_rank is None:
        return "-"
    delta = old_rank - new_rank
    if delta > 0:
        return f"+{delta}"
    if delta < 0:
        return str(delta)
    return "0"


def progress_icon(progress):
    if progress == "NEW":
        return "✨"
    if progress == "--":
        return "➖"
    try:
        n = int(progress)
        if n > 0:
            return "📈"
        if n < 0:
            return "📉"
        return "➖"
    except Exception:
        return "➖"


def medal(rank):
    if rank == 1:
        return "🥇"
    if rank == 2:
        return "🥈"
    if rank == 3:
        return "🥉"
    return "🔹"


def parse_lb_items(data):
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    result = data.get("result")
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in ["items", "list", "users", "leaderboard", "data"]:
            if isinstance(result.get(key), list):
                return result[key]
    for key in ["items", "list", "users", "leaderboard", "data"]:
        if isinstance(data.get(key), list):
            return data[key]
    return []


def parse_lb_row(item, idx):
    address = item.get("wallet_address") or item.get("address") or item.get("wallet") or item.get("user_address") or item.get("account")
    points = item.get("total_points") or item.get("points") or item.get("point") or item.get("score") or 0
    rank = item.get("rank") or item.get("position") or idx
    if not address:
        return None
    try:
        rank = int(rank)
    except Exception:
        rank = idx
    try:
        points = int(float(points))
    except Exception:
        points = 0
    return {"rank": rank, "address": address, "points": points}


async def auto_delete_messages(messages, delay=AUTO_DELETE_SECONDS):
    await asyncio.sleep(delay)
    for msg in messages:
        try:
            await msg.delete()
        except Exception:
            pass


# =============================================================================
# COG: EvoEvoAgentCog (/cekagent)
# =============================================================================

class EvoCog(commands.Cog):
    evo = app_commands.Group(name="evo", description="Cek agent, portfolio, dan progress EvoEvo")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: aiohttp.ClientSession | None = None
        self._mem_cache = {}

    async def cog_load(self):
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=25),
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
                "Origin": "https://evoevo.ai",
                "Referer": "https://evoevo.ai/",
            },
        )

        if not self.daily_snapshot_task.is_running():
            self.daily_snapshot_task.start()

    async def cog_unload(self):
        if self.daily_snapshot_task.is_running():
            self.daily_snapshot_task.cancel()

        if self.session:
            await self.session.close()

    @evo.command(
        name="cekagent",
        description="Cek agent EvoEvo. Address opsional: kalau kosong pakai saved address dari /wallet add."
    )
    @app_commands.describe(
        address="Opsional. Pisahkan banyak address pakai koma. Kalau kosong pakai /wallet add saved address."
    )
    async def cekagent(self, interaction: discord.Interaction, address: str = None):
        await interaction.response.defer(ephemeral=True, thinking=True)

        addresses = resolve_addresses_for_user(interaction.user.id, address)

        if not addresses:
            await interaction.followup.send(
                "❌ Address kosong. Isi parameter `address` atau simpan dulu dengan `/wallet add`.",
                ephemeral=True,
            )
            return

        invalid = [addr for addr in addresses if not is_valid_address(addr)]

        if invalid:
            await interaction.followup.send(
                "❌ Ada address tidak valid:\n```txt\n"
                + "\n".join(invalid[:10])
                + "\n```",
                ephemeral=True,
            )
            return

        if len(addresses) > MAX_ADDRESS_PER_CHECK:
            await interaction.followup.send(
                f"❌ Maksimal `{MAX_ADDRESS_PER_CHECK}` address sekali cek biar tidak kena rate limit.",
                ephemeral=True,
            )
            return

        wallet_results = []
        embeds = []

        for addr in addresses:
            agents, err, from_cache = get_agents_by_wallet(addr)

            wallet_results.append({
                "address": addr,
                "agents": agents,
                "error": err,
                "from_cache": from_cache,
            })

            embeds.append(
                build_agent_embed(
                    address=addr,
                    agents=agents,
                    from_cache=from_cache,
                    err=err,
                )
            )

        if len(addresses) >= 2:
            embeds.insert(0, build_total_summary_embed(addresses, wallet_results))

        await interaction.followup.send(embeds=embeds[:10], ephemeral=True)

        rest = embeds[10:]
        while rest:
            await interaction.followup.send(embeds=rest[:10], ephemeral=True)
            rest = rest[10:]


    @evo.command(
        name="portfolio",
        description="Cek portfolio EvoEvo. Default ambil data terbaru; opsi bandingkan untuk compare last check."
    )
    @app_commands.describe(
        address="Opsional. Pisahkan banyak address pakai koma. Kalau kosong pakai /wallet add saved address.",
        bandingkan="Opsional. True untuk bandingkan cek terakhir vs data sekarang."
    )
    async def portfolio(
        self,
        interaction: discord.Interaction,
        address: str = None,
        bandingkan: bool = False,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)

        compare_mode = bool(bandingkan)

        addresses = resolve_addresses_for_user(
            interaction.user.id,
            address,
            allow_state_addresses=compare_mode,
        )

        if not addresses:
            await interaction.followup.send(
                "❌ Address kosong. Isi parameter `address` atau simpan dulu dengan `/wallet add`.",
                ephemeral=True,
            )
            return

        invalid = [addr for addr in addresses if not is_valid_address(addr)]

        if invalid:
            await interaction.followup.send(
                "❌ Ada address tidak valid:\n```txt\n"
                + "\n".join(invalid[:10])
                + "\n```",
                ephemeral=True,
            )
            return

        if len(addresses) > MAX_PORTFOLIO_ADDRESS:
            await interaction.followup.send(
                f"❌ Maksimal `{MAX_PORTFOLIO_ADDRESS}` address sekali cek biar Discord tidak kepanjangan.",
                ephemeral=True,
            )
            return

        daily_items, daily_err = fetch_leaderboard("daily")
        weekly_items, weekly_err = fetch_leaderboard("weekly")
        total_items, total_err = fetch_leaderboard("total")
        worldcup_items, _, worldcup_err = fetch_worldcup_leaderboard()

        if daily_err or weekly_err or total_err:
            await interaction.followup.send(
                "❌ Gagal ambil leaderboard:\n```txt\n"
                f"daily: {daily_err}\n"
                f"weekly: {weekly_err}\n"
                f"total: {total_err}\n"
                "```",
                ephemeral=True,
            )
            return

        wallet_data, embeds = collect_portfolio_data(addresses, daily_items, weekly_items, total_items, worldcup_items)

        old_snapshots = get_portfolio_snapshots(interaction.user.id, addresses)
        old_now_map = {
            addr: (old_snapshots.get(addr, {}) or {}).get("now")
            for addr in addresses
            if (old_snapshots.get(addr, {}) or {}).get("now")
        }

        saved = save_portfolio_snapshots(interaction.user.id, wallet_data)
        new_now_map = {
            addr: saved.get(addr, {}).get("now")
            for addr in addresses
            if saved.get(addr, {}).get("now")
        }

        if compare_mode:
            compare_embeds = build_compare_embeds(addresses, old_now_map, new_now_map)
            first_chunk = compare_embeds[:10]
            rest = compare_embeds[10:]
            await interaction.followup.send(embeds=first_chunk, ephemeral=True)
            while rest:
                await interaction.followup.send(embeds=rest[:10], ephemeral=True)
                rest = rest[10:]
            return

        if len(addresses) >= 2:
            embeds.insert(0, build_summary_embed(addresses, wallet_data))

        for embed in embeds:
            try:
                embed.set_footer(text="Portfolio terbaru • snapshot otomatis disimpan untuk opsi bandingkan")
            except Exception:
                pass

        first_chunk = embeds[:10]
        rest = embeds[10:]

        await interaction.followup.send(embeds=first_chunk, ephemeral=True)

        while rest:
            await interaction.followup.send(embeds=rest[:10], ephemeral=True)
            rest = rest[10:]


# =============================================================================
# COG: EvoProgress (/progress)
# =============================================================================

    async def fetch_json(
        self,
        url: str,
        params: dict | None = None,
        ttl: int = 0,
        cache_name: str | None = None,
        allow_stale_on_error: bool = True,
    ):
        params = params or {}
        key = cache_name or cache_key_from(url, params)

        if ttl > 0:
            cached = self._mem_cache.get(key)

            if cached:
                age = now_ts() - int(cached.get("saved_at", 0))
                if age <= ttl:
                    return cached.get("data")

            disk_cached = load_api_cache(key)

            if disk_cached:
                age = now_ts() - int(disk_cached.get("saved_at", 0))
                if age <= ttl:
                    self._mem_cache[key] = disk_cached
                    return disk_cached.get("data")

        try:
            async with self.session.get(url, params=params) as res:
                text = await res.text()

                if res.status != 200:
                    print("API ERROR:", res.status, url, params, text[:250])

                    if allow_stale_on_error and ttl > 0:
                        stale = self._mem_cache.get(key) or load_api_cache(key)
                        if stale:
                            print("CACHE FALLBACK:", key)
                            return stale.get("data")

                    return None

                data = json.loads(text)

                if ttl > 0:
                    payload = {
                        "saved_at": now_ts(),
                        "saved_at_iso": datetime.now(timezone.utc).isoformat(),
                        "data": data,
                    }
                    self._mem_cache[key] = payload
                    save_api_cache(key, data)

                return data

        except Exception as e:
            print("REQUEST ERROR:", repr(e), url, params)

            if allow_stale_on_error and ttl > 0:
                stale = self._mem_cache.get(key) or load_api_cache(key)
                if stale:
                    print("CACHE FALLBACK:", key)
                    return stale.get("data")

            return None

    async def fetch_leaderboard_async(self, period: str, limit: int, wallet: str | None = None):
        period = str(period or "total").lower()

        if period == WORLD_CUP_PERIOD:
            url = f"{EVO_API}/leaderboards/campaigns/{WORLD_CUP_CAMPAIGN_PATH}/points"
            params = {"limit": limit, "chain_id": CHAIN_ID}
            if wallet:
                params["wallet"] = normalize_address(wallet)
            cache_wallet = normalize_address(wallet) if wallet else "global"
            cache_name = f"leaderboard_{WORLD_CUP_PERIOD}_{limit}_{cache_wallet}"
        else:
            url = f"{EVO_API}/leaderboards/users/points"
            params = {
                "limit": limit,
                "period": period,
                "chain_id": CHAIN_ID,
            }
            cache_name = f"leaderboard_{period}_{limit}_{CHAIN_ID}"

        data = await self.fetch_json(
            url,
            params=params,
            ttl=LEADERBOARD_CACHE_TTL,
            cache_name=cache_name,
        )
        items = parse_lb_items(data)

        rows = []

        for idx, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue

            row = parse_lb_row(item, idx)

            if row:
                rows.append(row)

        rows.sort(key=lambda x: x["rank"])
        return rows[:limit]

    async def fetch_worldcup_me(self, wallet: str):
        data = await self.fetch_json(
            f"{EVO_API}/leaderboards/campaigns/{WORLD_CUP_CAMPAIGN_PATH}/points",
            params={"limit": 20, "wallet": normalize_address(wallet), "chain_id": CHAIN_ID},
            ttl=LEADERBOARD_CACHE_TTL,
            cache_name=f"leaderboard_{WORLD_CUP_PERIOD}_me_{normalize_address(wallet)}",
        )

        if not isinstance(data, dict) or not isinstance(data.get("me"), dict):
            return None

        me = data["me"]
        address = me.get("wallet_address") or wallet
        points = me.get("total_points") or me.get("points") or 0
        rank = me.get("rank")

        try:
            points = int(float(points))
        except Exception:
            points = 0

        try:
            rank = int(rank) if rank is not None else None
        except Exception:
            rank = None

        return {
            "rank": rank,
            "address": address,
            "points": points,
            "tx": await self.fetch_tx_count(address),
            "sbt": await self.fetch_sbt_status(address),
            "old_rank": None,
            "progress": "--",
        }

    async def fetch_tx_count(self, address: str):
        url = f"{ZERO_G_API}/accounts/{address}"
        data = await self.fetch_json(
            url,
            ttl=TX_CACHE_TTL,
            cache_name=f"tx_{normalize_address(address)}",
        )

        if not isinstance(data, dict):
            return 0

        tx = (
            data.get("tx_count")
            or data.get("transaction_count")
            or data.get("transactions_count")
            or data.get("transactions")
            or 0
        )

        try:
            return int(float(tx))
        except Exception:
            return 0

    async def fetch_sbt_status(self, address: str):
        url = f"{EVO_API}/sbt/status"
        data = await self.fetch_json(
            url,
            params={"wallet": address},
            ttl=SBT_CACHE_TTL,
            cache_name=f"sbt_{normalize_address(address)}",
        )

        if not isinstance(data, dict):
            return "-"

        levels = data.get("levels")

        if levels is None and isinstance(data.get("data"), dict):
            levels = data["data"].get("levels")

        if levels is None and isinstance(data.get("result"), dict):
            levels = data["result"].get("levels")

        if not isinstance(levels, list):
            return "-"

        level_map = {1: "O", 2: "V", 3: "Z"}
        owned = []

        for item in levels:
            if not isinstance(item, dict):
                continue

            level_num = item.get("level")

            try:
                level_num = int(level_num)
            except Exception:
                level_num = None

            minted = (
                item.get("minted") is True
                or item.get("failure_code") == "already_minted"
                or item.get("failureCode") == "already_minted"
            )

            if minted and level_num in level_map:
                owned.append(level_map[level_num])

        return "".join(owned) if owned else "-"

    async def enrich_row(self, row: dict, old_rank_map: dict, has_previous: bool):
        addr = row["address"]
        old_rank = old_rank_map.get(normalize_address(addr))

        tx_task = self.fetch_tx_count(addr)
        sbt_task = self.fetch_sbt_status(addr)

        tx, sbt = await asyncio.gather(tx_task, sbt_task)

        row["tx"] = tx
        row["sbt"] = sbt
        row["old_rank"] = old_rank
        row["progress"] = progress_text(old_rank, row["rank"], has_previous)

        return row

    async def enrich_many(self, rows: list, old_rank_map: dict, has_previous: bool):
        sem = asyncio.Semaphore(8)

        async def run(row):
            async with sem:
                return await self.enrich_row(row, old_rank_map, has_previous)

        return await asyncio.gather(*(run(row) for row in rows))

    async def save_snapshot(self, period: str, limit: int = 100):
        rows = await self.fetch_leaderboard_async(period, limit)

        if not rows:
            print(f"SNAPSHOT FAILED: {period}")
            return False

        snapshot = []

        for row in rows:
            snapshot.append({
                "rank": row["rank"],
                "address": row["address"],
                "points": row["points"],
                "time": datetime.now(timezone.utc).isoformat(),
            })

        save_json(snapshot_file(period, date_key()), snapshot)
        print(f"SNAPSHOT SAVED: {period} {len(snapshot)} rows")
        return True

    @tasks.loop(minutes=30)
    async def daily_snapshot_task(self):
        now = datetime.now(timezone.utc)

        if now.hour != SNAPSHOT_HOUR_UTC:
            return

        marker_file = PROGRESS_DIR / f"snapshot_marker_{date_key()}.json"

        marker = load_json(marker_file, {})
        done = marker.get("done", [])

        changed = False

        for period in VALID_PERIODS:
            if period in done:
                continue

            ok = await self.save_snapshot(period, 100)

            if ok:
                done.append(period)
                changed = True

            await asyncio.sleep(3)

        if changed:
            save_json(marker_file, {
                "date": date_key(),
                "done": done,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })

    @daily_snapshot_task.before_loop
    async def before_daily_snapshot(self):
        await self.bot.wait_until_ready()

    def build_progress_lines(self, rows: list):
        parts = []

        for row in rows:
            rank = row["rank"]
            addr = mask_address(row["address"])
            points = fmt_short(row["points"])
            tx = fmt_short(row.get("tx", 0))
            sbt = row.get("sbt") or "-"
            prog = row.get("progress", "--")
            icon = progress_icon(prog)

            parts.append(
                f"{medal(rank)} **#{rank}**  `{addr}`\n"
                f"🏆 `{points} pts` | 🔄 `{tx} tx` | 🎖️ `{sbt}`\n"
                f"{icon} `{prog}`"
            )

        return "\n\n".join(parts)

    def build_user_progress(self, row: dict | None, days: int):
        if not row:
            return "Wallet tidak ditemukan di Top 100 leaderboard."

        old_rank = row.get("old_rank")
        if row.get("rank") is None:
            return (
                f"`{mask_address(row.get('address'))}`\n\n"
                f"🏆 **Points:** `{fmt_full(row.get('points', 0))}`\n"
                f"🔄 **TX:** `{fmt_short(row.get('tx', 0))}`\n"
                f"🎖️ **SBT:** `{row.get('sbt') or '-'}`\n\n"
                "**Rank:** `Not Ranked / Outside visible leaderboard`"
            )

        old_text = f"#{old_rank}" if old_rank else "Not Ranked"
        prog = row.get("progress", "--")
        icon = progress_icon(prog)

        return (
            f"**#{row['rank']}**  `{mask_address(row['address'])}`\n\n"
            f"🏆 **Points:** `{fmt_full(row['points'])}`\n"
            f"🔄 **TX:** `{fmt_short(row.get('tx', 0))}`\n"
            f"🎖️ **SBT:** `{row.get('sbt') or '-'}`\n\n"
            f"**Rank:** `{old_text}` → `#{row['rank']}`\n"
            f"{icon} **Progress {days}D:** `{prog}`"
        )

    @evo.command(
        name="progress",
        description="Cek progress leaderboard EvoEvo."
    )
    @app_commands.describe(
        limit="Top leaderboard yang ditampilkan",
        period="Periode leaderboard",
        days="Range progress: 3, 5, 7, atau 14 hari",
        address="Opsional: wallet kamu"
    )
    @app_commands.choices(
        limit=[
            app_commands.Choice(name="Top 20", value=20),
            app_commands.Choice(name="Top 50", value=50),
            app_commands.Choice(name="Top 100", value=100),
        ],
        period=[
            app_commands.Choice(name="Daily", value="daily"),
            app_commands.Choice(name="Weekly", value="weekly"),
            app_commands.Choice(name="Total", value="total"),
            app_commands.Choice(name="World Cup", value="worldcup"),
        ],
        days=[
            app_commands.Choice(name="3 Days", value=3),
            app_commands.Choice(name="5 Days", value=5),
            app_commands.Choice(name="7 Days", value=7),
            app_commands.Choice(name="14 Days", value=14),
        ],
    )
    async def progress(
        self,
        interaction: discord.Interaction,
        limit: app_commands.Choice[int],
        period: app_commands.Choice[str],
        days: app_commands.Choice[int],
        address: str | None = None,
    ):
        await interaction.response.defer(thinking=True, ephemeral=True)

        sent_messages = []

        selected_limit = int(limit.value)
        selected_period = str(period.value)
        selected_days = int(days.value)

        if selected_period == WORLD_CUP_PERIOD and not address and vault_get_user_wallets:
            try:
                saved_wallets = vault_get_user_wallets(interaction.user.id)
                if saved_wallets:
                    address = saved_wallets[0]
            except Exception:
                pass

        rows = await self.fetch_leaderboard_async(selected_period, selected_limit, wallet=address if selected_period == WORLD_CUP_PERIOD else None)

        if not rows:
            msg = await interaction.followup.send(
                "Gagal mengambil leaderboard. Cek console bot untuk detail API ERROR.",
                ephemeral=True, wait=True,
            )
            sent_messages.append(msg)
            asyncio.create_task(auto_delete_messages(sent_messages))
            return

        compare_date, compare_snapshot = get_snapshot_for_days(selected_period, selected_days)
        has_previous = bool(compare_snapshot)
        old_rank_map = snapshot_rank_map(compare_snapshot)

        enriched = await self.enrich_many(rows, old_rank_map, has_previous)

        save_json(
            snapshot_file(selected_period, date_key()),
            [
                {
                    "rank": row["rank"], "address": row["address"],
                    "points": row["points"], "tx": row.get("tx", 0),
                    "sbt": row.get("sbt", "-"),
                    "time": datetime.now(timezone.utc).isoformat(),
                }
                for row in enriched
            ],
        )

        if compare_date:
            subtitle = f"{selected_period.upper()} • {selected_days}D • vs {compare_date}"
        else:
            subtitle = f"{selected_period.upper()} • {selected_days}D • no snapshot yet"

        subtitle += f" • cache {LEADERBOARD_CACHE_TTL // 60}m"

        chunk_size = 10
        embeds = []

        for start in range(0, len(enriched), chunk_size):
            chunk = enriched[start:start + chunk_size]

            title = f"📈 EVOEVO PROGRESS TOP {selected_limit}"
            if start > 0:
                title = f"📈 EVOEVO PROGRESS #{start + 1}-{start + len(chunk)}"

            embed = discord.Embed(
                title=title,
                description=f"**{subtitle}**\n\n{self.build_progress_lines(chunk)}",
                color=discord.Color.purple(),
            )
            embed.set_footer(
                text=f"Leaderboard cache {LEADERBOARD_CACHE_TTL // 60}m • TX cache {TX_CACHE_TTL}s • SBT cache {SBT_CACHE_TTL // 60}m"
            )

            embeds.append(embed)

        if address:
            user_row = None
            norm = normalize_address(address)

            for row in enriched:
                if normalize_address(row["address"]) == norm:
                    user_row = row
                    break

            if user_row is None:
                top100 = await self.fetch_leaderboard_async(selected_period, 100, wallet=address if selected_period == WORLD_CUP_PERIOD else None)

                for row in top100:
                    if normalize_address(row["address"]) == norm:
                        user_row = await self.enrich_row(row, old_rank_map, has_previous)
                        break

            if user_row is None and selected_period == WORLD_CUP_PERIOD and address:
                user_row = await self.fetch_worldcup_me(address)

            user_embed = discord.Embed(
                title="👤 YOUR PROGRESS",
                description=self.build_user_progress(user_row, selected_days),
                color=discord.Color.green(),
            )
            user_embed.set_footer(
                text=f"Cache aktif untuk • Leaderboard {LEADERBOARD_CACHE_TTL // 60}m"
            )
            embeds.append(user_embed)

        for embed in embeds:
            msg = await interaction.followup.send(
                embed=embed,
                ephemeral=True,
                wait=True,
            )
            sent_messages.append(msg)

        asyncio.create_task(auto_delete_messages(sent_messages))


# =============================================================================
# SETUP
# =============================================================================

async def setup(bot):
    await bot.add_cog(EvoCog(bot))
