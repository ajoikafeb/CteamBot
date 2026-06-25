import json
import urllib.request
import urllib.error
from pathlib import Path

import discord
from discord.ext import commands


OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:3b"
SYSTEM_PROMPT = (
    "Kamu adalah asisten yang membantu di server Discord Cteam. "
    "Jawab pertanyaan dengan singkat, padat, dan ramah. "
    "Gunakan bahasa Indonesia. Maksimal 3 paragraf."
)
OLLAMA_TIMEOUT = 30


def ask_ollama(prompt: str) -> str:
    payload = json.dumps({
        "model": MODEL,
        "prompt": f"{SYSTEM_PROMPT}\n\nUser: {prompt}\nAsisten:",
        "stream": False,
        "options": {
            "num_predict": 512,
            "temperature": 0.7,
        }
    }).encode()

    req = urllib.request.Request(
        OLLAMA_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
            return data.get("response", "Maaf, aku gak bisa jawab sekarang.").strip()
    except urllib.error.HTTPError as e:
        return f"Ollama error (HTTP {e.code}). Cek `ollama serve` sudah jalan?"
    except urllib.error.URLError:
        return "Ollama gak bisa diakses. Pastikan `ollama serve` sudah jalan."
    except Exception as e:
        return f"Error: {e}"


class AiChatCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        if not self.bot.user.mentioned_in(message):
            return

        if message.mention_everyone:
            return

        content = message.clean_content.strip()

        for prefix in (f"@{self.bot.user.display_name}", f"@{self.bot.user.name}", "<@", "<@!"):
            idx = content.find(prefix)
            if idx != -1:
                content = content[:idx] + content[idx + len(content[idx:content.find(">", idx) + 1 if ">" in content[idx:] else len(content)]):]
                break

        content = content.strip().lstrip(",").strip()

        if not content:
            return

        async with message.channel.typing():
            reply = await self.bot.loop.run_in_executor(None, ask_ollama, content)

        if len(reply) > 2000:
            reply = reply[:1997] + "..."

        await message.reply(reply, mention_author=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AiChatCog(bot))
