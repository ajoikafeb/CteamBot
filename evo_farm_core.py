
# evo_farm_core.py
# v1.4 STAT POINTS OG PATCH (2026-06-17 03:03:20 UTC)
# - /farm stat fetches complete points and native OG balance.

# v1.2 SESSION SERIALIZE FIX (2026-06-17 02:45:28 UTC)
# - Fix: Object of type Session is not JSON serializable saat /farm add.
# - Semua key runtime seperti _session dibuang sebelum encrypt/save.
# Discord EvoEvo Farm Core
# Based on EvoEvo Autofarm v2.1.10 logic:
# - chain_id=16661
# - agent auto-resolve by wallet_address + chain_id
# - add-memory endpoint
# - raw selector 0xa29adb25 registry-first calldata layout

import asyncio
import json
import os
import random
import re
import time
import uuid
import threading
from pathlib import Path
from datetime import datetime, timezone

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from web3 import Web3

try:
    from eth_abi import encode as eth_abi_encode
except Exception:  # eth-abi v2 fallback
    try:
        from eth_abi import encode_abi as eth_abi_encode
    except Exception:
        eth_abi_encode = None

try:
    from cryptography.fernet import Fernet
except Exception:
    Fernet = None


APP_VERSION = "discord-simple-v1.0"
RPC_URL = "https://evmrpc.0g.ai"
CHAIN_ID = 16661
EVO_API = "https://api.evoevo.ai"
RAW_INTAKE_SELECTOR = "0xa29adb25"
DEFAULT_REGISTRY = "0x8004Ae533a0301CbD7508373b663756D26DfB028"

DATA_DIR = Path("data") / "discord_farm"
USERS_DIR = DATA_DIR / "users"
FEED_TABS = ["recommended", "weekly_best", "monthly_best", "all_time_best"]

REQUEST_TIMEOUT = 15
RETRY_TOTAL = 3
POINT_PER_TX = 50

DEFAULT_SETTINGS = {
    "cooldown_min": 20,
    "cooldown_max": 70,
    "refresh_min": 20,
    "refresh_max": 70,
    "tx_per_agent": 2,
    "feed_limit": 120,
    "platform_limit": 100,
    "gas_level": "standard",
}

_SAVE_LOCKS = {}
_SAVE_LOCKS_GUARD = threading.Lock()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def now_ts():
    return int(time.time())


def mask_wallet(address: str) -> str:
    if not address:
        return "-"
    return str(address)[:6] + "..." + str(address)[-4:]


def mask_agent(agent_id) -> str:
    s = str(agent_id)
    if len(s) <= 4:
        return "***"
    return s[:2] + "***" + s[-2:]


def short_hash(tx_hash: str) -> str:
    if not tx_hash:
        return "-"
    return str(tx_hash)[:10] + "..." + str(tx_hash)[-6:]


def safe_name(value: str) -> str:
    value = str(value or "wallet").strip()
    value = re.sub(r"[^a-zA-Z0-9_\-\.]+", "_", value)
    return value[:48] or "wallet"


def user_dir(user_id: int) -> Path:
    p = USERS_DIR / str(int(user_id))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _lock_for(path: Path):
    try:
        key = str(Path(path).resolve()).lower()
    except Exception:
        key = str(path).lower()
    with _SAVE_LOCKS_GUARD:
        if key not in _SAVE_LOCKS:
            _SAVE_LOCKS[key] = threading.RLock()
        return _SAVE_LOCKS[key]


def load_json(path: Path, default=None):
    if default is None:
        default = {}
    try:
        path = Path(path)
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock_for(path):
        last_err = None
        for attempt in range(5):
            tmp = path.with_name(f"{path.name}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp")
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                    f.flush()
                    try:
                        os.fsync(f.fileno())
                    except Exception:
                        pass
                tmp.replace(path)
                return
            except Exception as e:
                last_err = e
                try:
                    if tmp.exists():
                        tmp.unlink()
                except Exception:
                    pass
                time.sleep(0.05 * (attempt + 1))
        raise RuntimeError(f"save_json failed for {path}: {last_err}")


def normalize_bearer(token: str) -> str:
    token = str(token or "").strip()
    if token.lower().startswith("bearer "):
        token = token.split(" ", 1)[1].strip()
    return token


def normalize_private_key(private_key: str):
    raw = str(private_key or "")
    raw = (
        raw.replace("\ufeff", "")
        .replace("\u200b", "")
        .replace("\u200c", "")
        .replace("\u200d", "")
        .strip()
    )
    matches = re.findall(r"(?:0x)?[0-9a-fA-F]{64}", raw)
    candidate = matches[0] if matches else raw
    candidate = candidate.strip().strip('"').strip("'").strip().rstrip(",;")
    if "=" in candidate:
        candidate = candidate.split("=", 1)[-1].strip()
    candidate = "".join(candidate.split())
    if candidate.lower().startswith("0x"):
        candidate = candidate[2:]
    if len(candidate) != 64:
        return None
    if not re.fullmatch(r"[0-9a-fA-F]{64}", candidate):
        return None
    if int(candidate, 16) == 0:
        return None
    return "0x" + candidate.lower()


def derive_wallet_address(private_key: str):
    pk = normalize_private_key(private_key)
    if not pk:
        return None
    try:
        return Web3().eth.account.from_key(pk).address
    except Exception:
        return None


def create_session():
    session = requests.Session()
    retry = Retry(
        total=RETRY_TOTAL,
        connect=RETRY_TOTAL,
        read=RETRY_TOTAL,
        backoff_factor=0.8,
        status_forcelist=[429, 500, 502, 503, 504, 520, 521, 522, 523, 524],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=30, pool_maxsize=30)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def headers(wallet: dict):
    return {
        "Authorization": f"Bearer {normalize_bearer(wallet.get('bearer'))}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Origin": "https://evoevo.ai",
        "Referer": "https://evoevo.ai/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/149.0.0.0 Safari/537.36",
    }


def safe_request(method, url, wallet=None, **kwargs):
    try:
        timeout = kwargs.pop("timeout", REQUEST_TIMEOUT)
        hdrs = kwargs.pop("headers", None)
        if hdrs is None and wallet is not None:
            hdrs = headers(wallet)
        if str(method).upper() == "POST":
            return requests.request(method, url, headers=hdrs, timeout=max(timeout, 25), **kwargs), None
        session = wallet.get("_session") if isinstance(wallet, dict) else None
        if session is None:
            session = create_session()
        return session.request(method, url, headers=hdrs, timeout=timeout, **kwargs), None
    except requests.exceptions.Timeout as e:
        return None, f"timeout: {str(e)[:160]}"
    except requests.exceptions.ConnectionError as e:
        return None, f"connection error: {str(e)[:180]}"
    except Exception as e:
        return None, str(e)[:180]


def parse_api_items(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ["items", "data", "results", "agents", "predictions"]:
            value = data.get(key)
            if isinstance(value, list):
                return value
        nested = data.get("data")
        if isinstance(nested, dict):
            for key in ["items", "results", "agents", "predictions"]:
                value = nested.get(key)
                if isinstance(value, list):
                    return value
    return []


def clean_jsonable(obj):
    """Buang runtime object sebelum data diencrypt/disimpan ke JSON.

    requests.Session, Web3 object, asyncio task, dll tidak boleh masuk farm_data.enc.
    Semua key yang diawali "_" dianggap runtime-only.
    """
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if str(key).startswith("_"):
                continue
            if key in ["session", "http_session"]:
                continue
            out[key] = clean_jsonable(value)
        return out
    if isinstance(obj, list):
        return [clean_jsonable(x) for x in obj]
    if isinstance(obj, tuple):
        return [clean_jsonable(x) for x in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    # Fallback aman: jangan simpan object Python mentah.
    return str(obj)


def prepare_wallet_for_save(wallet: dict):
    item = clean_jsonable(wallet or {})
    item["bearer"] = normalize_bearer(item.get("bearer"))
    pk = normalize_private_key(item.get("private_key"))
    if pk:
        item["private_key"] = pk
    try:
        item["agent_ids"] = [int(x) for x in (item.get("agent_ids") or []) if str(x).strip()]
    except Exception:
        item["agent_ids"] = []
    item.setdefault("settings", dict(DEFAULT_SETTINGS))
    item["updated_at"] = now_iso()
    return item


class FarmStore:
    """Encrypted per-user farm data store."""

    def __init__(self, root: Path = DATA_DIR):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        USERS_DIR.mkdir(parents=True, exist_ok=True)
        if Fernet is None:
            raise RuntimeError("cryptography belum terinstall. Jalankan: pip install cryptography")
        self.fernet = Fernet(self._load_key())

    def _load_key(self) -> bytes:
        env = os.getenv("FARM_ENC_KEY", "").strip()
        if env:
            return env.encode()
        key_path = self.root / "server.key"
        if key_path.exists():
            return key_path.read_bytes().strip()
        key = Fernet.generate_key()
        key_path.write_bytes(key)
        return key

    def _file(self, user_id: int) -> Path:
        return user_dir(user_id) / "farm_data.enc"

    def load(self, user_id: int) -> dict:
        path = self._file(user_id)
        if not path.exists():
            return {"user_id": int(user_id), "wallets": [], "created_at": now_iso(), "updated_at": now_iso()}
        raw = path.read_bytes()
        if not raw:
            return {"user_id": int(user_id), "wallets": [], "created_at": now_iso(), "updated_at": now_iso()}
        data = json.loads(self.fernet.decrypt(raw).decode("utf-8"))
        if not isinstance(data, dict):
            data = {}
        data.setdefault("user_id", int(user_id))
        data.setdefault("wallets", [])
        return data

    def save(self, user_id: int, data: dict):
        data = clean_jsonable(dict(data or {}))
        data["user_id"] = int(user_id)
        data["updated_at"] = now_iso()

        # Pastikan wallet bersih dari object runtime seperti requests.Session.
        wallets = []
        for wallet in data.get("wallets", []) or []:
            if isinstance(wallet, dict):
                wallets.append(prepare_wallet_for_save(wallet))
        data["wallets"] = wallets

        raw = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        encrypted = self.fernet.encrypt(raw)
        path = self._file(user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock_for(path):
            tmp = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
            tmp.write_bytes(encrypted)
            tmp.replace(path)

    def list_wallets(self, user_id: int):
        return self.load(user_id).get("wallets", [])

    def upsert_wallet(self, user_id: int, wallet: dict):
        data = self.load(user_id)
        wallets = data.get("wallets", [])
        wallet = prepare_wallet_for_save(wallet)
        name_l = wallet["name"].lower()
        replaced = False
        for i, old in enumerate(wallets):
            if str(old.get("name", "")).lower() == name_l:
                wallets[i] = wallet
                replaced = True
                break
        if not replaced:
            wallets.append(wallet)
        data["wallets"] = wallets
        self.save(user_id, data)

    def update_wallets(self, user_id: int, wallets: list):
        data = self.load(user_id)
        data["wallets"] = [prepare_wallet_for_save(w) for w in (wallets or []) if isinstance(w, dict)]
        self.save(user_id, data)

    def get_wallets_by_names(self, user_id: int, names: str = None):
        wallets = self.list_wallets(user_id)
        if not names or str(names).strip().lower() in ["all", "*"]:
            return wallets
        wanted = {x.strip().lower() for x in str(names).split(",") if x.strip()}
        return [w for w in wallets if str(w.get("name", "")).lower() in wanted]


def number_or_none(value):
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except Exception:
        return None


def extract_first_number(data, keys):
    """Ambil angka dari dict nested dengan beberapa kemungkinan key."""
    if isinstance(data, dict):
        for key in keys:
            if key in data:
                num = number_or_none(data.get(key))
                if num is not None:
                    return num
        for value in data.values():
            found = extract_first_number(value, keys)
            if found is not None:
                return found
    elif isinstance(data, list):
        for item in data:
            found = extract_first_number(item, keys)
            if found is not None:
                return found
    return None


def points_from_item(item):
    if not isinstance(item, dict):
        return None
    total = extract_first_number(item, ["total_points", "points", "total", "score"])
    if total is not None:
        return total
    user = extract_first_number(item, ["user_points"])
    agent = extract_first_number(item, ["agent_points"])
    if user is not None or agent is not None:
        return int(user or 0) + int(agent or 0)
    return None


def rank_from_item(item):
    if not isinstance(item, dict):
        return None
    return number_or_none(item.get("rank") or item.get("rank_label") or item.get("position"))


def wallet_of_item(item):
    if not isinstance(item, dict):
        return ""
    return str(
        item.get("wallet_address")
        or item.get("wallet")
        or item.get("address")
        or item.get("user_address")
        or item.get("userAddress")
        or ""
    ).lower()



class EvoCore:
    def __init__(self):
        self.w3 = Web3(Web3.HTTPProvider(RPC_URL))

    def ensure_w3(self):
        if not self.w3.is_connected():
            self.w3 = Web3(Web3.HTTPProvider(RPC_URL))
        if not self.w3.is_connected():
            raise RuntimeError("Gagal connect ke RPC 0G")
        return self.w3

    def resolve_agents(self, wallet: dict):
        address = wallet.get("wallet_address") or derive_wallet_address(wallet.get("private_key"))
        if not address:
            return [], "private key invalid / cannot derive address"
        wallet["_session"] = wallet.get("_session") or create_session()
        res, err = safe_request(
            "GET",
            f"{EVO_API}/v1/agents",
            wallet=wallet,
            params={"wallet_address": address, "chain_id": CHAIN_ID},
            timeout=10,
        )
        if err or res is None:
            return [], err or "no response"
        if res.status_code == 401:
            return [], "401 bearer invalid/expired"
        if res.status_code != 200:
            return [], f"{res.status_code}: {res.text[:120]}"
        try:
            data = res.json()
        except Exception:
            return [], "invalid json"
        out = []
        for item in parse_api_items(data):
            if not isinstance(item, dict):
                continue
            identity = item.get("onchain_identity") or {}
            if isinstance(identity, dict) and identity.get("chain_id") is not None:
                try:
                    if int(identity.get("chain_id")) != CHAIN_ID:
                        continue
                except Exception:
                    continue
            api_id = item.get("id") or item.get("agent_id")
            if not api_id:
                continue
            try:
                api_id = int(api_id)
            except Exception:
                continue
            status = str(item.get("status") or item.get("run_status") or "").lower()
            active_flag = item.get("active")
            onchain_status = str(item.get("onchain_status") or "").lower()
            identity_status = str(identity.get("status") or "").lower() if isinstance(identity, dict) else ""
            if active_flag is False:
                continue
            if status and status not in ["active", "ok", "running", "deployed"]:
                continue
            if onchain_status and onchain_status not in ["bound", "active", "ok"]:
                continue
            if identity_status and identity_status not in ["bound", "active", "ok"]:
                continue
            if api_id not in out:
                out.append(api_id)
        return out, None

    def build_wallet(self, name: str, bearer: str, private_key: str):
        pk = normalize_private_key(private_key)
        if not pk:
            return None, "private key invalid"
        address = derive_wallet_address(pk)
        if not address:
            return None, "cannot derive wallet address"
        wallet = {
            "name": safe_name(name),
            "bearer": normalize_bearer(bearer),
            "private_key": pk,
            "wallet_address": address,
            "agent_ids": [],
            "settings": dict(DEFAULT_SETTINGS),
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        agents, err = self.resolve_agents(wallet)
        if err:
            return None, f"resolve agent gagal: {err}"
        wallet["agent_ids"] = agents
        wallet.pop("_session", None)
        return prepare_wallet_for_save(wallet), None

    def fetch_platform_feed(self, wallet: dict, agent_id: int, tab="recommended", limit=None):
        limit = int(limit or wallet.get("settings", {}).get("platform_limit", DEFAULT_SETTINGS["platform_limit"]))
        res, err = safe_request(
            "GET",
            f"{EVO_API}/v1/platform/feeding",
            wallet=wallet,
            params={
                "tab": tab,
                "limit": limit,
                "chain_id": CHAIN_ID,
                "agent_id": int(agent_id),
                "include_intaken": "false",
            },
        )
        if err or res is None:
            return [], err or "no response"
        if res.status_code != 200:
            return [], f"{res.status_code}: {res.text[:120]}"
        try:
            data = res.json()
        except Exception:
            return [], "invalid json"

        out = []
        for item in parse_api_items(data):
            if not isinstance(item, dict):
                continue
            reasoning = item.get("reasoning") or {}
            opinion_id = (
                reasoning.get("opinion_id")
                or item.get("opinion_id")
                or item.get("source_opinion_id")
                or item.get("id")
            )
            if not opinion_id:
                continue
            selected_has_intaken = reasoning.get("selected_agent_has_intaken")
            if selected_has_intaken is True:
                continue
            title = (
                item.get("title")
                or item.get("question")
                or reasoning.get("title")
                or reasoning.get("question")
                or ""
            )
            out.append({
                "opinion_id": int(opinion_id),
                "title": str(title)[:180],
                "tab": tab,
                "source": "platform",
                "agent_id": int(agent_id),
                "quality": int(item.get("quality") or reasoning.get("quality") or 0),
                "raw_id": item.get("id"),
            })
        return out, None

    def fetch_agent_predictions(self, wallet: dict, source_agent_id: int, limit=80):
        res, err = safe_request(
            "GET",
            f"{EVO_API}/v1/agents/{int(source_agent_id)}/predictions",
            wallet=wallet,
            params={"limit": int(limit), "chain_id": CHAIN_ID},
        )
        if err or res is None or res.status_code != 200:
            return []
        try:
            data = res.json()
        except Exception:
            return []
        out = []
        for item in parse_api_items(data):
            if not isinstance(item, dict):
                continue
            opinion_id = item.get("opinion_id") or item.get("source_opinion_id")
            prediction_id = item.get("prediction_id") or item.get("predictionId") or item.get("id")
            if not opinion_id:
                continue
            out.append({
                "opinion_id": int(opinion_id),
                "prediction_id": prediction_id,
                "title": str(item.get("title") or item.get("question") or "")[:180],
                "tab": "agent_predictions",
                "source": "source_agent",
                "agent_id": int(source_agent_id),
                "raw_id": item.get("id"),
            })
        return out

    def screen_feed_for_wallet(self, wallet: dict):
        feed_limit = int(wallet.get("settings", {}).get("feed_limit", DEFAULT_SETTINGS["feed_limit"]))
        seen = set()
        rows = []
        counters = {"platform": 0, "source_agent": 0, "total": 0, "errors": []}

        for agent_id in wallet.get("agent_ids", []):
            if len(rows) >= feed_limit:
                break
            for tab in FEED_TABS:
                ops, err = self.fetch_platform_feed(wallet, agent_id, tab=tab)
                if err:
                    counters["errors"].append(f"{mask_agent(agent_id)} {tab}: {err}")
                    continue
                for op in ops:
                    oid = op["opinion_id"]
                    if oid in seen:
                        continue
                    seen.add(oid)
                    op["wallet_name"] = wallet["name"]
                    rows.append(op)
                    counters["platform"] += 1
                    if len(rows) >= feed_limit:
                        break
                if len(rows) >= feed_limit:
                    break

            # Extra small source-agent scan from own agent predictions.
            for op in self.fetch_agent_predictions(wallet, agent_id, limit=80):
                if len(rows) >= feed_limit:
                    break
                oid = op["opinion_id"]
                if oid in seen:
                    continue
                seen.add(oid)
                op["wallet_name"] = wallet["name"]
                rows.append(op)
                counters["source_agent"] += 1

        rows.sort(key=lambda x: (x.get("quality", 0), x.get("opinion_id", 0)), reverse=True)
        counters["total"] = len(rows)
        return rows[:feed_limit], counters

    def fetch_me_points(self, wallet: dict):
        """Fetch authenticated point summary.

        Endpoint ini biasanya mengikuti bearer user:
        GET /v1/me/points
        Parser dibuat fleksibel karena schema API bisa berubah.
        """
        res, err = safe_request("GET", f"{EVO_API}/v1/me/points", wallet=wallet, timeout=10)
        if err or res is None:
            return {"ok": False, "error": err or "no response"}
        if res.status_code == 401:
            return {"ok": False, "error": "401 bearer invalid/expired"}
        if res.status_code != 200:
            return {"ok": False, "error": f"{res.status_code}: {res.text[:120]}"}
        try:
            data = res.json()
        except Exception:
            return {"ok": False, "error": "invalid json"}

        total = extract_first_number(data, ["total_points", "points", "total", "score"])
        user_points = extract_first_number(data, ["user_points", "userPoints"])
        agent_points = extract_first_number(data, ["agent_points", "agentPoints"])
        worldcup_points = extract_first_number(data, ["worldcup_points", "world_cup_points", "campaign_points"])

        return {
            "ok": True,
            "total_points": total,
            "user_points": user_points,
            "agent_points": agent_points,
            "worldcup_points": worldcup_points,
            "raw": data,
        }

    def fetch_leaderboard_item(self, wallet: dict, period="total", limit=100):
        address = str(wallet.get("wallet_address") or "").lower()
        if not address:
            return None, "wallet address empty"

        res, err = safe_request(
            "GET",
            f"{EVO_API}/v1/leaderboards/users/points",
            wallet=wallet,
            params={"limit": int(limit), "period": period, "chain_id": CHAIN_ID},
            timeout=10,
        )
        if err or res is None:
            return None, err or "no response"
        if res.status_code != 200:
            return None, f"{res.status_code}: {res.text[:120]}"
        try:
            data = res.json()
        except Exception:
            return None, "invalid json"

        for item in parse_api_items(data):
            if wallet_of_item(item) == address:
                return item, None

        return None, "not in fetched leaderboard"

    def fetch_points_summary(self, wallet: dict):
        """Fetch point lengkap untuk /farm stat.

        Menggabungkan:
        - /v1/me/points untuk poin real dari bearer
        - leaderboard daily/weekly/total untuk rank + points jika masuk limit
        """
        summary = {
            "me": self.fetch_me_points(wallet),
            "leaderboard": {},
            "errors": [],
        }

        for period in ["daily", "weekly", "total"]:
            item, err = self.fetch_leaderboard_item(wallet, period=period, limit=100)
            if item:
                summary["leaderboard"][period] = {
                    "rank": rank_from_item(item),
                    "points": points_from_item(item),
                    "user_points": extract_first_number(item, ["user_points", "userPoints"]),
                    "agent_points": extract_first_number(item, ["agent_points", "agentPoints"]),
                }
            else:
                summary["leaderboard"][period] = None
                if err and "not in fetched" not in str(err):
                    summary["errors"].append(f"{period}: {err}")

        return summary

    def fetch_og_balance(self, wallet: dict):
        """Fetch native OG balance dari RPC."""
        try:
            w3 = self.ensure_w3()
            address = Web3.to_checksum_address(wallet.get("wallet_address"))
            wei = int(w3.eth.get_balance(address))
            og = wei / 10**18
            return {"ok": True, "wei": wei, "og": og}
        except Exception as e:
            return {"ok": False, "error": str(e)[:160], "wei": None, "og": None}

    def fetch_wallet_live_stats(self, wallet: dict):
        """Live stats untuk /farm stat: points + OG."""
        return {
            "points": self.fetch_points_summary(wallet),
            "og": self.fetch_og_balance(wallet),
        }

    def request_memory_data(self, wallet: dict, agent_id: int, opinion_id: int):
        res, err = safe_request(
            "POST",
            f"{EVO_API}/v1/agents/{int(agent_id)}/memories/from-opinion",
            wallet=wallet,
            json={"opinion_id": int(opinion_id)},
        )
        if err or res is None:
            return None, err or "no response"
        if res.status_code == 401:
            return None, "401 bearer invalid/expired"
        if res.status_code == 403:
            return None, "403 agent not owned"
        if res.status_code not in [200, 201, 202]:
            return None, f"{res.status_code}: {res.text[:100]}"
        try:
            data = res.json()
        except Exception:
            return None, "invalid json response"
        if data.get("status") != "pending_chain":
            return None, "status bukan pending_chain"
        sig = data.get("reasoning_intake_with_sig") or {}
        required = [
            "contract_address", "identity_registry_address", "token_id",
            "source_opinion_id", "reasoning_hash", "opinion_hash",
            "new_memory_root", "nonce", "deadline", "signature",
        ]
        missing = [k for k in required if sig.get(k) in [None, ""]]
        if missing:
            return None, "signature missing " + ",".join(missing)
        return data, None

    @staticmethod
    def _hex_bytes(value, size=None):
        s = str(value or "")
        if s.startswith("0x"):
            s = s[2:]
        b = bytes.fromhex(s)
        if size and len(b) != size:
            raise ValueError(f"expected {size} bytes, got {len(b)}")
        return b

    def build_raw_calldata(self, memory_data: dict):
        if eth_abi_encode is None:
            raise RuntimeError("eth_abi belum terinstall. Jalankan: pip install eth-abi")
        sig = memory_data["reasoning_intake_with_sig"]
        types = ["address", "uint256", "uint256", "bytes32", "bytes32", "bytes32", "uint256", "uint256", "bytes"]
        values = [
            Web3.to_checksum_address(sig.get("identity_registry_address") or DEFAULT_REGISTRY),
            int(sig["token_id"]),
            int(sig["source_opinion_id"]),
            self._hex_bytes(sig["reasoning_hash"], 32),
            self._hex_bytes(sig["opinion_hash"], 32),
            self._hex_bytes(sig["new_memory_root"], 32),
            int(sig["nonce"]),
            int(sig["deadline"]),
            self._hex_bytes(sig["signature"]),
        ]
        return RAW_INTAKE_SELECTOR + eth_abi_encode(types, values).hex()

    def send_tx(self, wallet: dict, memory_data: dict):
        w3 = self.ensure_w3()
        account = w3.eth.account.from_key(wallet["private_key"])
        wallet_address = account.address
        sig = memory_data["reasoning_intake_with_sig"]
        calldata = self.build_raw_calldata(memory_data)
        gas_price = int(w3.eth.gas_price)
        tx = {
            "from": wallet_address,
            "to": Web3.to_checksum_address(sig["contract_address"]),
            "nonce": w3.eth.get_transaction_count(wallet_address),
            "chainId": CHAIN_ID,
            "gasPrice": gas_price,
            "gas": 600000,
            "value": 0,
            "data": calldata,
        }
        est = w3.eth.estimate_gas(tx)
        tx["gas"] = int(est * 1.20)
        signed = w3.eth.account.sign_transaction(tx, wallet["private_key"])
        raw_tx = getattr(signed, "rawTransaction", None) or getattr(signed, "raw_transaction")
        tx_hash = w3.eth.send_raw_transaction(raw_tx)
        tx_hex = tx_hash.hex()
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=90)
        ok = int(receipt.status) == 1
        return {
            "ok": ok,
            "tx_hash": tx_hex,
            "gas_used": int(receipt.gasUsed),
            "status": int(receipt.status),
        }



def discord_error_file(user_id: int) -> Path:
    return user_dir(user_id) / "errors.json"


def log_user_error(user_id: int, where: str, error: str, extra: dict = None):
    try:
        path = discord_error_file(user_id)
        rows = load_json(path, [])
        if not isinstance(rows, list):
            rows = []
        rows.append({
            "time": now_iso(),
            "where": str(where),
            "error": str(error)[:1000],
            "extra": extra or {},
        })
        save_json(path, rows[-300:])
    except Exception:
        pass


def feed_file(user_id: int) -> Path:
    return user_dir(user_id) / "feed_data.json"


def used_file(user_id: int) -> Path:
    return user_dir(user_id) / "used_opinions.json"


def history_file(user_id: int) -> Path:
    return user_dir(user_id) / "tx_history.json"


def load_used(user_id: int):
    data = load_json(used_file(user_id), {})
    return data if isinstance(data, dict) else {}


def mark_used(user_id: int, opinion_id: int, row: dict = None):
    data = load_used(user_id)
    data[str(int(opinion_id))] = {
        "time": now_iso(),
        "row": row or {},
    }
    save_json(used_file(user_id), data)


def add_history(user_id: int, row: dict):
    rows = load_json(history_file(user_id), [])
    if not isinstance(rows, list):
        rows = []
    rows.append(row)
    save_json(history_file(user_id), rows[-2000:])
