import os
import json
import base64
import hashlib
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
from cryptography.fernet import Fernet
from dotenv import load_dotenv


DATA_DIR = Path("data")
DATA_FILE = DATA_DIR / "user_wallets.json"
MAX_WALLETS_PER_USER = 50

load_dotenv()

SECRET = os.getenv("WALLET_SECRET", "CHANGE_ME_PLEASE_CHANGE_THIS_KEY")

FERNET_KEY = base64.urlsafe_b64encode(
    hashlib.sha256(SECRET.encode()).digest()
)

CIPHER = Fernet(FERNET_KEY)


def ensure_data_file():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not DATA_FILE.exists():
        DATA_FILE.write_text("{}", encoding="utf-8")


def load_wallet_db():
    ensure_data_file()

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            return data

        return {}

    except Exception:
        return {}


def save_wallet_db(data):
    ensure_data_file()

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def encrypt_wallet(address: str) -> str:
    return CIPHER.encrypt(address.encode()).decode()


def decrypt_wallet(value: str) -> str:
    try:
        return CIPHER.decrypt(value.encode()).decode()
    except Exception:
        return value


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
    return address.strip().lower()


def mask_wallet(address):
    if not address:
        return "-"
    return address[:6] + "..." + address[-4:]


def get_user_wallets(user_id):
    db = load_wallet_db()
    key = str(user_id)

    item = db.get(key)

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

        decrypted = decrypt_wallet(wallet)

        if not is_valid_address(decrypted):
            continue

        addr = normalize_address(decrypted)

        if addr not in clean:
            clean.append(addr)

    return clean


def set_user_wallets(user_id, wallets):
    db = load_wallet_db()
    key = str(user_id)

    clean_plain = []

    for wallet in wallets:
        if not isinstance(wallet, str):
            continue

        if not is_valid_address(wallet):
            continue

        addr = normalize_address(wallet)

        if addr not in clean_plain:
            clean_plain.append(addr)

    encrypted_wallets = [encrypt_wallet(addr) for addr in clean_plain]

    db[key] = {
        "wallets": encrypted_wallets
    }

    save_wallet_db(db)

    return clean_plain


def parse_addresses(text):
    parts = text.replace("\n", ",").split(",")
    addresses = []

    for part in parts:
        addr = part.strip().lower()

        if addr:
            addresses.append(addr)

    return list(dict.fromkeys(addresses))


def chunk_lines(lines, max_chars=950):
    chunks = []
    current = []

    for line in lines:
        candidate = "\n".join(current + [line])
        if len(candidate) > max_chars and current:
            chunks.append("\n".join(current))
            current = [line]
        else:
            current.append(line)

    if current:
        chunks.append("\n".join(current))

    return chunks


def build_wallet_embed(user, wallets):
    embed = discord.Embed(
        title="📦 Wallet Vault",
        description=f"Wallet tersimpan untuk {user.mention}",
        color=0x8B5CF6,
    )

    if not wallets:
        embed.add_field(
            name="Wallet",
            value="Belum ada wallet tersimpan.",
            inline=False,
        )
        embed.set_footer(text="Gunakan /wallet add untuk menambahkan address.")
        return embed

    embed.add_field(
        name="Total Wallet",
        value=f"`{len(wallets)}` / `{MAX_WALLETS_PER_USER}`",
        inline=False,
    )

    # Full list satu per satu. Format inline-code memudahkan user copy 1 address.
    one_by_one_lines = [
        f"**{idx}.** `{wallet}`"
        for idx, wallet in enumerate(wallets, start=1)
    ]

    for idx, chunk in enumerate(chunk_lines(one_by_one_lines, max_chars=980)[:10], start=1):
        embed.add_field(
            name=f"📌 Copy One by One {idx}" if len(wallets) > 12 else "📌 Copy One by One",
            value=chunk,
            inline=False,
        )

    # Copy all tetap disediakan dalam code block.
    compact_lines = wallets
    for idx, chunk in enumerate(chunk_lines(compact_lines, max_chars=950)[:5], start=1):
        embed.add_field(
            name=f"📋 Copy All Address Part {idx}" if len(wallets) > 20 else "📋 Copy All Address",
            value=f"```txt\n{chunk}\n```",
            inline=False,
        )

    embed.set_footer(text="Address tersimpan terenkripsi. /portfolio dan /cekagent bisa pakai saved address kalau address dikosongkan.")
    return embed


class WalletVaultCog(commands.Cog):
    wallet = app_commands.Group(name="wallet", description="Kelola wallet address")

    def __init__(self, bot):
        self.bot = bot

    @wallet.command(
        name="add",
        description="Simpan wallet address ke akun Discord kamu."
    )
    @app_commands.describe(
        address="Bisa satu atau banyak address, pisahkan pakai koma."
    )
    async def addwallet(self, interaction: discord.Interaction, address: str):
        await interaction.response.defer(ephemeral=True, thinking=True)

        addresses = parse_addresses(address)

        if not addresses:
            await interaction.followup.send("❌ Address kosong.", ephemeral=True)
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

        current_wallets = get_user_wallets(interaction.user.id)

        added = []
        skipped = []

        for addr in addresses:
            addr = normalize_address(addr)

            if addr in current_wallets:
                skipped.append(addr)
                continue

            if len(current_wallets) >= MAX_WALLETS_PER_USER:
                break

            current_wallets.append(addr)
            added.append(addr)

        set_user_wallets(interaction.user.id, current_wallets)

        embed = discord.Embed(
            title="✅ Wallet Saved",
            color=0x22C55E,
        )

        if added:
            embed.add_field(
                name=f"Added `{len(added)}`",
                value="```txt\n" + "\n".join(added) + "\n```",
                inline=False,
            )

        if skipped:
            embed.add_field(
                name=f"Skipped duplicate `{len(skipped)}`",
                value="```txt\n" + "\n".join(skipped[:15]) + "\n```",
                inline=False,
            )

        embed.add_field(
            name="Total Wallet",
            value=f"`{len(current_wallets)}` / `{MAX_WALLETS_PER_USER}`",
            inline=False,
        )

        embed.set_footer(text="Wallet disimpan terenkripsi. Gunakan /wallet view untuk melihat.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @wallet.command(
        name="view",
        description="Lihat wallet address yang kamu simpan."
    )
    async def viewaddress(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        wallets = get_user_wallets(interaction.user.id)
        embed = build_wallet_embed(interaction.user, wallets)

        await interaction.followup.send(embed=embed, ephemeral=True)

    @wallet.command(
        name="list",
        description="Tampilkan list address full agar bisa dicopy satu-satu."
    )
    async def listaddress(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        wallets = get_user_wallets(interaction.user.id)
        embed = build_wallet_embed(interaction.user, wallets)

        await interaction.followup.send(embed=embed, ephemeral=True)

    @wallet.command(
        name="del",
        description="Hapus satu wallet address dari akun Discord kamu."
    )
    @app_commands.describe(
        address="Wallet address yang mau dihapus."
    )
    async def delwallet(self, interaction: discord.Interaction, address: str):
        await interaction.response.defer(ephemeral=True, thinking=True)

        if not is_valid_address(address):
            await interaction.followup.send("❌ Address tidak valid.", ephemeral=True)
            return

        addr = normalize_address(address)
        wallets = get_user_wallets(interaction.user.id)

        if addr not in wallets:
            await interaction.followup.send(
                f"❌ Address `{mask_wallet(addr)}` tidak ada di wallet kamu.",
                ephemeral=True,
            )
            return

        wallets.remove(addr)
        set_user_wallets(interaction.user.id, wallets)

        await interaction.followup.send(
            f"✅ Wallet `{mask_wallet(addr)}` berhasil dihapus.\nTotal tersisa: `{len(wallets)}`",
            ephemeral=True,
        )

    @wallet.command(
        name="clear",
        description="Hapus semua wallet address yang kamu simpan."
    )
    async def clearwallet(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        wallets = get_user_wallets(interaction.user.id)

        if not wallets:
            await interaction.followup.send("Wallet kamu masih kosong.", ephemeral=True)
            return

        set_user_wallets(interaction.user.id, [])

        await interaction.followup.send(
            f"✅ Semua wallet berhasil dihapus. Total terhapus: `{len(wallets)}`",
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(WalletVaultCog(bot))