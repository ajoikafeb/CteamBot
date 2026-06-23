import json
import csv
import io
from pathlib import Path
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands


DATA_DIR = Path("data")
USERS_FILE = DATA_DIR / "reward_users.json"
REWARDS_FILE = DATA_DIR / "rewards.json"
COUNTER_FILE = DATA_DIR / "reward_counter.json"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_json(path, default=None):
    if default is None:
        default = {}
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


def load_users():
    return load_json(USERS_FILE, {})


def save_users(data):
    save_json(USERS_FILE, data)


def load_rewards():
    return load_json(REWARDS_FILE, [])


def save_rewards(data):
    save_json(REWARDS_FILE, data)


def get_counter():
    data = load_json(COUNTER_FILE, {"next_id": 1})
    return data.get("next_id", 1)


def bump_counter():
    data = load_json(COUNTER_FILE, {"next_id": 1})
    data["next_id"] = data.get("next_id", 1) + 1
    save_json(COUNTER_FILE, data)
    return data["next_id"] - 1


def get_user_by_alias(alias):
    users = load_users()
    alias_l = alias.strip().lower()
    for uid, u in users.items():
        if u.get("alias", "").strip().lower() == alias_l:
            return uid, u
    return None, None


def get_user_by_discord(user_id):
    users = load_users()
    key = str(user_id)
    u = users.get(key)
    if u:
        return key, u
    return None, None


def get_user_by_wallet(wallet):
    users = load_users()
    wallet_l = wallet.strip().lower()
    for uid, u in users.items():
        if u.get("wallet", "").strip().lower() == wallet_l:
            return uid, u
    return None, None


def get_user_rewards(user_id):
    rewards = load_rewards()
    return [r for r in rewards if r["user_id"] == user_id]


def user_total_reward(user_id):
    rewards = get_user_rewards(user_id)
    return sum(r.get("amount", 0) for r in rewards if r["status"] == "paid")


def user_reward_count(user_id):
    return len(get_user_rewards(user_id))


def user_pending_count(user_id):
    return sum(1 for r in get_user_rewards(user_id) if r["status"] == "pending")


def user_paid_count(user_id):
    return sum(1 for r in get_user_rewards(user_id) if r["status"] == "paid")


def build_user_embed(user, data):
    uid = str(user.id) if hasattr(user, "id") else str(user)
    total = user_total_reward(int(uid))
    count = user_reward_count(int(uid))
    pending = user_pending_count(int(uid))
    paid = user_paid_count(int(uid))

    embed = discord.Embed(
        title="\U0001f381 Reward User",
        color=0x8B5CF6,
    )

    mention = user.mention if hasattr(user, "mention") else f"<@{uid}>"
    embed.description = f"Data reward untuk {mention}"

    embed.add_field(name="Alias", value=f"`{data.get('alias', '-')}`", inline=True)
    embed.add_field(name="Wallet", value=f"`{data.get('wallet', '-')}`", inline=True)
    embed.add_field(name="Dana", value=f"`{data.get('dana', '-')}`", inline=True)
    embed.add_field(name="Bank", value=f"`{data.get('bank_name', '-')}`", inline=True)
    embed.add_field(name="Rekening", value=f"`{data.get('bank_account', '-')}`", inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)

    embed.add_field(name="Total Reward", value=f"Rp{total:,.0f}", inline=True)
    embed.add_field(name="Reward Count", value=f"`{count}`", inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)
    embed.add_field(name="Pending", value=f"`{pending}`", inline=True)
    embed.add_field(name="Paid", value=f"`{paid}`", inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)

    return embed


def build_paid_embed(alias, amount, currency, method, mention_str):
    embed = discord.Embed(
        title="\u2705 REWARD PAID",
        description=f"{mention_str}\n\n**Amount:**\n{currency}{amount:,.0f}\n\n**Method:**\n{method}\n\n**Status:**\nPAID\n\nSilakan cek pembayaran Anda.\n\nJika dana sudah diterima,\nsilakan tutup ticket ini.\n\nTerima kasih.",
        color=0x22C55E,
    )
    return embed


def build_top_leaderboard(rows, limit=10):
    sorted_rows = sorted(rows, key=lambda x: x[1], reverse=True)[:limit]
    medals = ["\U0001f947", "\U0001f948", "\U0001f949"]
    lines = []
    for idx, (alias, total) in enumerate(sorted_rows):
        medal = medals[idx] if idx < 3 else f"`{idx+1}.`"
        lines.append(f"{medal} **{alias}**\nRp{total:,.0f}")
    return "\n\n".join(lines)


def detect_ticket_number(channel_name):
    parts = channel_name.split("-")
    for p in parts:
        if p.isdigit():
            return p
    return None


class RewardVaultCog(commands.Cog):
    reward = app_commands.Group(name="reward", description="Kelola reward giveaway")

    def __init__(self, bot):
        self.bot = bot

    @reward.command(
        name="add",
        description="Tambah user reward baru."
    )
    @app_commands.describe(
        user="User Discord",
        alias="Alias unik untuk user",
        wallet="Wallet address (opsional)",
        dana="Nomor Dana (opsional)",
        bank="Nama Bank (opsional)",
        rekening="Nomor Rekening (opsional)"
    )
    async def rewardadd(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        alias: str,
        wallet: str = None,
        dana: str = None,
        bank: str = None,
        rekening: str = None,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        uid = str(user.id)

        users = load_users()

        existing_alias, _ = get_user_by_alias(alias)
        if existing_alias is not None:
            await interaction.followup.send(
                f"\u26a0 Alias `{alias}` sudah digunakan oleh user lain.",
                ephemeral=True,
            )
            return

        if wallet:
            existing_wallet, _ = get_user_by_wallet(wallet)
            if existing_wallet is not None:
                await interaction.followup.send(
                    "\u26a0 Wallet sudah digunakan user lain.",
                    ephemeral=True,
                )
                return

        users[uid] = {
            "discord_id": user.id,
            "discord_name": user.name,
            "alias": alias.strip(),
            "wallet": (wallet or "").strip(),
            "dana": (dana or "").strip(),
            "bank_name": (bank or "").strip(),
            "bank_account": (rekening or "").strip(),
        }

        save_users(users)

        embed = discord.Embed(
            title="\u2705 User Reward Ditambahkan",
            description=f"User {user.mention} berhasil didaftarkan sebagai `{alias}`.",
            color=0x22C55E,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @reward.command(
        name="edit",
        description="Edit data user reward."
    )
    @app_commands.describe(
        user="User Discord",
        alias="Alias baru (opsional)",
        wallet="Wallet baru (opsional)",
        dana="Nomor Dana baru (opsional)",
        bank="Nama Bank baru (opsional)",
        rekening="Nomor Rekening baru (opsional)"
    )
    async def rewardedit(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        alias: str = None,
        wallet: str = None,
        dana: str = None,
        bank: str = None,
        rekening: str = None,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        uid = str(user.id)
        users = load_users()

        if uid not in users:
            await interaction.followup.send(
                f"\u26a0 User {user.mention} belum terdaftar. Gunakan `/reward add` dulu.",
                ephemeral=True,
            )
            return

        if alias:
            existing_alias, _ = get_user_by_alias(alias)
            if existing_alias is not None and existing_alias != uid:
                await interaction.followup.send(
                    f"\u26a0 Alias `{alias}` sudah digunakan user lain.",
                    ephemeral=True,
                )
                return
            users[uid]["alias"] = alias.strip()

        if wallet:
            existing_wallet, _ = get_user_by_wallet(wallet)
            if existing_wallet is not None and existing_wallet != uid:
                await interaction.followup.send(
                    "\u26a0 Wallet sudah digunakan user lain.",
                    ephemeral=True,
                )
                return
            users[uid]["wallet"] = wallet.strip()

        if dana is not None:
            users[uid]["dana"] = dana.strip()
        if bank is not None:
            users[uid]["bank_name"] = bank.strip()
        if rekening is not None:
            users[uid]["bank_account"] = rekening.strip()

        save_users(users)

        embed = discord.Embed(
            title="\u2705 User Reward Diupdate",
            description=f"Data user {user.mention} berhasil diupdate.",
            color=0x22C55E,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @reward.command(
        name="user",
        description="Lihat data reward user."
    )
    @app_commands.describe(
        user="User Discord"
    )
    async def rewarduser(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        uid = str(user.id)
        users = load_users()

        if uid not in users:
            await interaction.followup.send(
                f"\u26a0 User {user.mention} belum terdaftar.",
                ephemeral=True,
            )
            return

        embed = build_user_embed(user, users[uid])
        await interaction.followup.send(embed=embed, ephemeral=True)

    @reward.command(
        name="give",
        description="Berikan reward baru ke user (status: pending)."
    )
    @app_commands.describe(
        user="User Discord",
        amount="Jumlah reward",
        currency="Mata uang (IDR, USDT, dll)",
        method="Metode pembayaran (CRYPTO/DANA/BANK/OTHER)",
        event="Nama event"
    )
    async def rewardgive(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        amount: int,
        currency: str,
        method: str,
        event: str,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        uid = str(user.id)
        users = load_users()

        if uid not in users:
            await interaction.followup.send(
                f"\u26a0 User {user.mention} belum terdaftar. Gunakan `/reward add` dulu.",
                ephemeral=True,
            )
            return

        method_upper = method.upper()
        valid_methods = ["CRYPTO", "DANA", "BANK", "OTHER"]
        if method_upper not in valid_methods:
            await interaction.followup.send(
                f"\u26a0 Metode tidak valid. Pilih: {', '.join(valid_methods)}",
                ephemeral=True,
            )
            return

        reward_id = bump_counter()
        alias = users[uid].get("alias", user.name)

        rewards = load_rewards()
        rewards.append({
            "reward_id": reward_id,
            "user_id": user.id,
            "alias": alias,
            "event": event.strip(),
            "amount": amount,
            "currency": currency.strip().upper(),
            "method": method_upper,
            "status": "pending",
            "created_at": now_iso(),
            "paid_at": None,
        })
        save_rewards(rewards)

        embed = discord.Embed(
            title="\u2705 Reward Ditambahkan",
            description=f"Reward **{currency.upper()} {amount:,.0f}** untuk {user.mention}",
            color=0x22C55E,
        )
        embed.add_field(name="Event", value=f"`{event.strip()}`", inline=True)
        embed.add_field(name="Method", value=f"`{method_upper}`", inline=True)
        embed.add_field(name="Status", value="`pending`", inline=True)
        embed.add_field(name="Reward ID", value=f"`{reward_id}`", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @reward.command(
        name="paid",
        description="Tandai reward pending terbaru sebagai paid."
    )
    @app_commands.describe(
        user="User Discord"
    )
    async def paid(self, interaction: discord.Interaction, user: discord.Member):
        await interaction.response.defer(ephemeral=True, thinking=True)
        uid = user.id
        users = load_users()
        key = str(uid)

        if key not in users:
            await interaction.followup.send(
                f"\u26a0 User {user.mention} belum terdaftar.",
                ephemeral=True,
            )
            return

        rewards = load_rewards()
        pending = [r for r in rewards if r["user_id"] == uid and r["status"] == "pending"]

        if not pending:
            await interaction.followup.send(
                f"\u26a0 Tidak ada reward pending untuk {user.mention}.",
                ephemeral=True,
            )
            return

        pending.sort(key=lambda r: r.get("reward_id", 0), reverse=True)
        target = pending[0]

        for r in rewards:
            if r.get("reward_id") == target["reward_id"]:
                r["status"] = "paid"
                r["paid_at"] = now_iso()
                break

        save_rewards(rewards)

        alias = users[key].get("alias", user.name)

        channel = interaction.channel
        if channel and isinstance(channel, discord.TextChannel):
            base_name = channel.name
            ticket_num = detect_ticket_number(base_name)
            if ticket_num:
                new_name = f"paid-{alias}"
                try:
                    await channel.edit(name=new_name)
                except Exception:
                    pass

        mention_str = user.mention
        embed = build_paid_embed(alias, target["amount"], target["currency"], target["method"], mention_str)
        await interaction.followup.send(embed=embed, ephemeral=True)

        try:
            await interaction.channel.send(embed=embed)
        except Exception:
            pass

    @reward.command(
        name="unpaid",
        description="Kembalikan status reward paid terbaru menjadi pending."
    )
    @app_commands.describe(
        user="User Discord"
    )
    async def unpaid(self, interaction: discord.Interaction, user: discord.Member):
        await interaction.response.defer(ephemeral=True, thinking=True)
        uid = user.id
        users = load_users()
        key = str(uid)

        if key not in users:
            await interaction.followup.send(
                f"\u26a0 User {user.mention} belum terdaftar.",
                ephemeral=True,
            )
            return

        rewards = load_rewards()
        paid_list = [r for r in rewards if r["user_id"] == uid and r["status"] == "paid"]

        if not paid_list:
            await interaction.followup.send(
                f"\u26a0 Tidak ada reward paid untuk {user.mention}.",
                ephemeral=True,
            )
            return

        paid_list.sort(key=lambda r: r.get("paid_at") or "", reverse=True)
        target = paid_list[0]

        for r in rewards:
            if r.get("reward_id") == target["reward_id"]:
                r["status"] = "pending"
                r["paid_at"] = None
                break

        save_rewards(rewards)

        channel = interaction.channel
        if channel and isinstance(channel, discord.TextChannel):
            base_name = channel.name
            ticket_num = detect_ticket_number(base_name)
            if ticket_num:
                new_name = f"ticket-{ticket_num}"
                try:
                    await channel.edit(name=new_name)
                except Exception:
                    pass
            else:
                try:
                    await channel.edit(name="ticket-reopened")
                except Exception:
                    pass

        embed = discord.Embed(
            title="\U0001f504 Reward Dibuka Kembali",
            description=f"Reward **{target['currency']} {target['amount']:,.0f}** untuk {user.mention} dikembalikan ke **pending**.",
            color=0xF59E0B,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

        try:
            await interaction.channel.send(embed=embed)
        except Exception:
            pass

    @reward.command(
        name="exportaddress",
        description="Export CSV wallet address untuk airdrop."
    )
    async def rewardexportaddress(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        users = load_users()
        rows = []

        for uid, u in users.items():
            wallet = u.get("wallet", "").strip()
            if not wallet:
                continue
            alias = u.get("alias", u.get("discord_name", ""))
            rows.append({
                "name": alias,
                "address": wallet,
                "amount": "1",
            })

        if not rows:
            await interaction.followup.send("\u26a0 Tidak ada user dengan wallet address.", ephemeral=True)
            return

        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=["name", "address", "amount"])
        writer.writeheader()
        writer.writerows(rows)
        csv_content = output.getvalue()
        output.close()

        await interaction.followup.send(
            file=discord.File(
                io.BytesIO(csv_content.encode()),
                filename="reward_export_address.csv"
            ),
            ephemeral=True,
        )

    @reward.command(
        name="exportdana",
        description="Export CSV nomor Dana untuk pembayaran."
    )
    async def rewardexportdana(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        users = load_users()
        rewards = load_rewards()
        rows = []

        pending_dana = [r for r in rewards if r["method"] == "DANA" and r["status"] == "pending"]

        for r in pending_dana:
            u = users.get(str(r["user_id"]))
            if not u:
                continue
            dana = u.get("dana", "").strip()
            if not dana:
                continue
            alias = u.get("alias", u.get("discord_name", ""))
            rows.append({
                "name": alias,
                "dana": dana,
                "amount": str(r["amount"]),
                "currency": r["currency"],
                "event": r["event"],
            })

        if not rows:
            await interaction.followup.send("\u26a0 Tidak ada pending payment via DANA.", ephemeral=True)
            return

        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=["name", "dana", "amount", "currency", "event"])
        writer.writeheader()
        writer.writerows(rows)
        csv_content = output.getvalue()
        output.close()

        await interaction.followup.send(
            file=discord.File(
                io.BytesIO(csv_content.encode()),
                filename="reward_export_dana.csv"
            ),
            ephemeral=True,
        )

    @reward.command(
        name="exportbank",
        description="Export CSV rekening bank untuk pembayaran."
    )
    async def rewardexportbank(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        users = load_users()
        rewards = load_rewards()
        rows = []

        pending_bank = [r for r in rewards if r["method"] == "BANK" and r["status"] == "pending"]

        for r in pending_bank:
            u = users.get(str(r["user_id"]))
            if not u:
                continue
            bank_name = u.get("bank_name", "").strip()
            bank_acc = u.get("bank_account", "").strip()
            if not bank_name or not bank_acc:
                continue
            alias = u.get("alias", u.get("discord_name", ""))
            rows.append({
                "name": alias,
                "bank": bank_name,
                "account": bank_acc,
                "amount": str(r["amount"]),
                "currency": r["currency"],
                "event": r["event"],
            })

        if not rows:
            await interaction.followup.send("\u26a0 Tidak ada pending payment via BANK.", ephemeral=True)
            return

        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=["name", "bank", "account", "amount", "currency", "event"])
        writer.writeheader()
        writer.writerows(rows)
        csv_content = output.getvalue()
        output.close()

        await interaction.followup.send(
            file=discord.File(
                io.BytesIO(csv_content.encode()),
                filename="reward_export_bank.csv"
            ),
            ephemeral=True,
        )

    @reward.command(
        name="exportall",
        description="Export semua data user reward ke CSV."
    )
    async def rewardexportall(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        users = load_users()

        if not users:
            await interaction.followup.send("\u26a0 Belum ada user terdaftar.", ephemeral=True)
            return

        rows = []
        for uid, u in users.items():
            uid_int = int(uid)
            total = user_total_reward(uid_int)
            count = user_reward_count(uid_int)
            rows.append({
                "alias": u.get("alias", ""),
                "discord_name": u.get("discord_name", ""),
                "wallet": u.get("wallet", ""),
                "dana": u.get("dana", ""),
                "bank": u.get("bank_name", ""),
                "account": u.get("bank_account", ""),
                "total_reward": str(total),
                "reward_count": str(count),
            })

        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=["alias", "discord_name", "wallet", "dana", "bank", "account", "total_reward", "reward_count"])
        writer.writeheader()
        writer.writerows(rows)
        csv_content = output.getvalue()
        output.close()

        await interaction.followup.send(
            file=discord.File(
                io.BytesIO(csv_content.encode()),
                filename="reward_export_all.csv"
            ),
            ephemeral=True,
        )

    @reward.command(
        name="top",
        description="Leaderboard total reward terkumpul."
    )
    async def rewardtop(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        users = load_users()

        if not users:
            await interaction.followup.send("\u26a0 Belum ada user terdaftar.", ephemeral=True)
            return

        leaderboard = []
        for uid, u in users.items():
            uid_int = int(uid)
            total = user_total_reward(uid_int)
            if total > 0:
                alias = u.get("alias", u.get("discord_name", "Unknown"))
                leaderboard.append((alias, total))

        if not leaderboard:
            await interaction.followup.send("\u26a0 Belum ada reward terbayar.", ephemeral=True)
            return

        text = build_top_leaderboard(leaderboard)

        embed = discord.Embed(
            title="\U0001f3c6 Reward Leaderboard",
            description=text,
            color=0xF59E0B,
        )
        embed.set_footer(text="Total reward yang sudah dibayarkan")

        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(RewardVaultCog(bot))
