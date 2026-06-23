import random
from datetime import datetime, time
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands


JAKARTA_TZ = ZoneInfo("Asia/Jakarta")


def parse_date_id(date_text: str, end: bool = False) -> datetime:
    try:
        d = datetime.strptime(date_text.strip(), "%Y-%m-%d").date()
    except ValueError:
        raise app_commands.AppCommandError("Format tanggal harus YYYY-MM-DD. Contoh: 2026-05-25")

    t = time.max if end else time.min
    return datetime.combine(d, t, tzinfo=JAKARTA_TZ)


class DateRangeModal(discord.ui.Modal):
    start_date = discord.ui.TextInput(
        label="Start Date",
        placeholder="YYYY-MM-DD, contoh: 2026-05-25",
        required=True,
        min_length=10,
        max_length=10,
    )
    end_date = discord.ui.TextInput(
        label="End Date",
        placeholder="YYYY-MM-DD atau ketik 'now'",
        required=True,
        min_length=3,
        max_length=10,
    )

    def __init__(
        self,
        keyword: str,
        channel: discord.TextChannel,
        unique_user: bool,
        allow_bot: bool,
        max_per_user: int = 0,
        winner_count: int = 1,
        multi_win: bool = False,
        max_multi_win: int = 0,
    ):
        super().__init__(title="Atur Range Giveaway")
        self.keyword = keyword
        self.channel = channel
        self.unique_user = unique_user
        self.allow_bot = allow_bot
        self.max_per_user = max_per_user
        self.winner_count = winner_count
        self.multi_win = multi_win
        self.max_multi_win = max_multi_win

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)

        start_val = self.start_date.value.strip()
        end_val = self.end_date.value.strip()

        try:
            after_dt = parse_date_id(start_val, end=False)
        except app_commands.AppCommandError as e:
            return await interaction.followup.send(str(e))

        if end_val.lower() == "now":
            before_dt = datetime.now(tz=JAKARTA_TZ)
            end_val = before_dt.strftime("%Y-%m-%d %H:%M")
        else:
            try:
                before_dt = parse_date_id(end_val, end=True)
            except app_commands.AppCommandError as e:
                return await interaction.followup.send(str(e))

        if after_dt > before_dt:
            return await interaction.followup.send("Tanggal mulai tidak boleh lebih besar dari tanggal akhir.")

        keyword_clean = self.keyword.strip()
        if not keyword_clean:
            return await interaction.followup.send("Keyword tidak boleh kosong.")

        user_entries: dict[int, list[discord.Message]] = {}

        try:
            async for msg in self.channel.history(
                limit=None,
                after=after_dt,
                before=before_dt,
                oldest_first=True,
            ):
                if msg.author.bot and not self.allow_bot:
                    continue

                if keyword_clean not in msg.content:
                    continue

                user_entries.setdefault(msg.author.id, []).append(msg)
        except discord.Forbidden:
            return await interaction.followup.send(
                "Aku tidak punya izin membaca riwayat pesan di channel itu. Aktifkan permission `Read Message History`."
            )
        except discord.HTTPException:
            return await interaction.followup.send("Gagal mengambil riwayat pesan. Coba ulangi lagi nanti.")

        if not user_entries:
            return await interaction.followup.send(
                f"Tidak ada pesan valid dengan keyword `{self.keyword}` pada range `{start_val}` sampai `{end_val}`."
            )

        pool: list[discord.Message] = []
        for uid, msgs in user_entries.items():
            capped = msgs
            if self.unique_user:
                capped = msgs[:1]
            elif self.max_per_user > 0:
                capped = msgs[:self.max_per_user]
            pool.extend(capped)

        if not pool:
            return await interaction.followup.send("Tidak ada entri valid setelah filter.")

        total_participants = len(user_entries)
        total_entries = len(pool)

        k = min(self.winner_count, len(pool))
        random.shuffle(pool)

        if self.multi_win:
            winners = []
            win_counts: dict[int, int] = {}
            for msg in pool:
                uid = msg.author.id
                if self.max_multi_win > 0 and win_counts.get(uid, 0) >= self.max_multi_win:
                    continue
                winners.append(msg)
                win_counts[uid] = win_counts.get(uid, 0) + 1
                if len(winners) == k:
                    break
        else:
            winners = []
            picked_users: set[int] = set()
            for msg in pool:
                if msg.author.id in picked_users:
                    continue
                winners.append(msg)
                picked_users.add(msg.author.id)
                if len(winners) == k:
                    break

        embed = discord.Embed(
            title="Giveaway Winner",
            description=f"**{len(winners)}** pemenang berhasil dipilih dari pesan yang sesuai keyword.",
            color=discord.Color.gold(),
            timestamp=datetime.now(tz=JAKARTA_TZ),
        )

        winners_text = "\n".join(
            f"{i+1}. {w.author.mention}" for i, w in enumerate(winners)
        )
        embed.add_field(name="Winner" if len(winners) == 1 else "Winners", value=winners_text, inline=False)
        embed.add_field(name="Keyword", value=f"`{self.keyword}`", inline=True)
        embed.add_field(name="Total Peserta", value=f"`{total_participants}`", inline=True)
        embed.add_field(name="Total Entri", value=f"`{total_entries}`", inline=True)
        embed.add_field(name="Range", value=f"`{start_val}` sampai `{end_val}`", inline=False)

        if self.max_per_user > 0 and not self.unique_user:
            embed.add_field(name="Max Entri/User", value=f"`{self.max_per_user}`", inline=True)
        if self.multi_win:
            label = "Multi Win"
            val = "Ya"
            if self.max_multi_win > 0:
                val += f" (max {self.max_multi_win}x/user)"
            embed.add_field(name=label, value=val, inline=True)

        embed.set_footer(
            text=f"Picked by {interaction.user.display_name} • {datetime.now(tz=JAKARTA_TZ).strftime('%-m/%-d/%y, %-I:%M %p')}"
        )

        await interaction.followup.send(embed=embed)


class PickCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="pick",
        description="Pilih pemenang giveaway dari pesan yang sesuai keyword."
    )
    @app_commands.describe(
        keyword="Keyword yang harus ada di pesan peserta. Contoh: vidya",
        channel="Channel tempat pesan giveaway dikirim. Kosongkan untuk channel ini.",
        unique_user="Hitung 1 pesan saja per user agar tidak spam.",
        allow_bot="Izinkan pesan dari bot ikut dihitung.",
        max_per_user="Batasi jumlah entri per user (0 = tidak dibatasi).",
        winner_count="Jumlah pemenang yang dipilih (default: 1).",
        multi_win="Izinkan 1 user menang lebih dari sekali.",
        max_multi_win="Batasi max kemenangan per user (0 = tak terbatas).",
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    async def pick(
        self,
        interaction: discord.Interaction,
        keyword: str,
        channel: discord.TextChannel | None = None,
        unique_user: bool = True,
        allow_bot: bool = False,
        max_per_user: app_commands.Range[int, 0, 100] = 0,
        winner_count: app_commands.Range[int, 1, 50] = 1,
        multi_win: bool = False,
        max_multi_win: app_commands.Range[int, 0, 50] = 0,
    ):
        target_channel = channel or interaction.channel
        if not isinstance(target_channel, discord.TextChannel):
            return await interaction.response.send_message(
                "Command ini hanya bisa dipakai di text channel.", ephemeral=True
            )

        modal = DateRangeModal(
            keyword=keyword,
            channel=target_channel,
            unique_user=unique_user,
            allow_bot=allow_bot,
            max_per_user=max_per_user,
            winner_count=winner_count,
            multi_win=multi_win,
            max_multi_win=max_multi_win,
        )
        await interaction.response.send_modal(modal)

    @pick.error
    async def pick_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            msg = "Kamu butuh permission `Manage Messages` untuk memakai command ini."
        else:
            msg = f"Terjadi error: `{error}`"

        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(PickCog(bot))
