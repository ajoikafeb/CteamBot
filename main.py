import os
import json
import asyncio
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv


load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

ALLOWED_DM_FILE = Path("data") / "allowed_dm_users.json"


intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True


def load_allowed_dm():
    try:
        if not ALLOWED_DM_FILE.exists():
            return set()
        data = json.loads(ALLOWED_DM_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return set(int(x) for x in data)
        return set()
    except Exception:
        return set()


def save_allowed_dm(user_ids):
    ALLOWED_DM_FILE.parent.mkdir(parents=True, exist_ok=True)
    ALLOWED_DM_FILE.write_text(
        json.dumps([int(x) for x in user_ids], indent=2),
        encoding="utf-8",
    )


class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=intents
        )

    async def setup_hook(self):

        # Load semua cog
        for filename in os.listdir("./cogs"):
            if filename.endswith(".py"):
                await self.load_extension(
                    f"cogs.{filename[:-3]}"
                )
                print(f"Loaded cog: {filename}")

        # Register root-level groups
        self.tree.add_command(dm_group)

        # Sync slash command
        try:
            synced = await self.tree.sync()
            print(f"Synced {len(synced)} slash commands")
        except Exception as e:
            print(e)

    async def on_ready(self):
        print("=" * 50)
        print(f"Logged in as: {self.user}")
        print(f"Bot ID: {self.user.id}")
        print("=" * 50)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is not None:
            return True
        allowed = load_allowed_dm()
        if interaction.user.id not in allowed:
            await interaction.response.send_message(
                "\u26a0 Bot ini hanya bisa digunakan oleh user tertentu via DM.",
                ephemeral=True,
            )
            return False
        return True

    async def on_message(self, message):
        if message.author.bot:
            return

        if isinstance(message.channel, discord.DMChannel):
            allowed = load_allowed_dm()
            if message.author.id not in allowed:
                await message.channel.send(
                    "\u26a0 Bot ini hanya bisa digunakan oleh user tertentu via DM.",
                    delete_after=10,
                )
                return

        await self.process_commands(message)


bot = MyBot()

dm_group = app_commands.Group(name="dm", description="Kelola izin DM bot")


@dm_group.command(name="allow", description="Ijinkan user untuk DM bot.")
@app_commands.describe(user="User yang diizinkan")
async def dmallow(interaction: discord.Interaction, user: discord.Member):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("\u26a0 Hanya admin.", ephemeral=True)
        return

    allowed = load_allowed_dm()
    allowed.add(user.id)
    save_allowed_dm(allowed)

    embed = discord.Embed(
        title="\u2705 DM Allowed",
        description=f"{user.mention} sekarang bisa DM bot.",
        color=0x22C55E,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@dm_group.command(name="deny", description="Hapus izin DM user.")
@app_commands.describe(user="User yang diblokir")
async def dmdeny(interaction: discord.Interaction, user: discord.Member):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("\u26a0 Hanya admin.", ephemeral=True)
        return

    allowed = load_allowed_dm()
    allowed.discard(user.id)
    save_allowed_dm(allowed)

    embed = discord.Embed(
        title="\u274c DM Denied",
        description=f"{user.mention} tidak bisa DM bot lagi.",
        color=0xEF4444,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@dm_group.command(name="list", description="List user yang diizinkan DM bot.")
async def dmlist(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("\u26a0 Hanya admin.", ephemeral=True)
        return

    allowed = load_allowed_dm()
    if not allowed:
        await interaction.response.send_message("Belum ada user yang diizinkan DM.", ephemeral=True)
        return

    lines = []
    for uid in sorted(allowed):
        user = bot.get_user(uid)
        label = f"{user.mention} ({user.name})" if user else f"<@{uid}>"
        lines.append(f"\u2022 {label}")

    embed = discord.Embed(
        title="\U0001f4ec Allowed DM Users",
        description="\n".join(lines),
        color=0x8B5CF6,
    )
    embed.set_footer(text=f"Total: {len(allowed)} user")
    await interaction.response.send_message(embed=embed, ephemeral=True)


async def main():
    async with bot:
        await bot.start(TOKEN)


asyncio.run(main())