import json
import time
import re
import asyncio
import urllib.request
import urllib.error

import discord
from discord.ext import commands


OLLAMA_URL = "http://localhost:11434/api/generate"
MODELS = {
    "primary": {"name": "qwen2.5:3b", "timeout": 45, "num_predict": 512},
    "fallback": {"name": "qwen2.5:1.5b", "timeout": 30, "num_predict": 256},
}
SYSTEM_PROMPT = (
    "You are a friendly, intelligent, and natural Discord community member. "
    "Your goal is to have conversations that feel human, engaging, and context-aware. "
    "Talk naturally like a real person, not like an AI assistant. "
    "Use casual language, show curiosity, ask follow-up questions when natural. "
    "Match the user's language automatically — if they write in Indonesian, answer in Indonesian. "
    "Never translate names, projects, brands, tokens, or technical terms. "
    "Keep responses concise unless detail is requested. "
    "Do not overuse emojis. "
    "IMPORTANT: You are a local AI model without internet access. "
    "You do NOT have current data about stock prices, IPO schedules, crypto prices, or real-time events. "
    "If asked about current prices, schedules, news, or time-sensitive data, "
    "clearly state that you don't have real-time access and cannot provide accurate current information. "
    "Never make up facts, company names, financial data, or crypto prices. "
    "When uncertain, say so — never invent facts. "
    "You are an experienced developer — help with Python, JS, TS, Discord bots, APIs, blockchain, debugging. "
    "Act like a helpful server member participating naturally in the community."
)
RATE_LIMIT_SECONDS = 3
MAX_CHARS = 1000

_user_cooldowns: dict[int, float] = {}

INJECTION_PATTERNS = re.compile(
    r"(abaikan.?perintah|ignore.?all|system.?prompt|override|"
    r"kamu.?sekarang|you.?are.?now|lupakan.?semua|reset.?conversation)",
    re.IGNORECASE,
)


def is_suspicious(text: str) -> bool:
    return bool(INJECTION_PATTERNS.search(text))


def ask_ollama(prompt: str, model_cfg: dict) -> str:
    payload = json.dumps({
        "model": model_cfg["name"],
        "system": SYSTEM_PROMPT,
        "prompt": prompt,
        "stream": False,
        "options": {
            "num_predict": model_cfg["num_predict"],
            "temperature": 0.9,
            "top_p": 0.9,
        }
    }).encode()

    req = urllib.request.Request(
        OLLAMA_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=model_cfg["timeout"]) as resp:
        data = json.loads(resp.read().decode())
        return data.get("response", "").strip()


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

        user_id = message.author.id
        now = time.time()
        last = _user_cooldowns.get(user_id, 0)
        if now - last < RATE_LIMIT_SECONDS:
            return
        _user_cooldowns[user_id] = now

        content = message.clean_content.strip()

        for prefix in (f"@{self.bot.user.display_name}", f"@{self.bot.user.name}", "<@", "<@!"):
            idx = content.find(prefix)
            if idx != -1:
                end = content.find(">", idx)
                if end != -1:
                    content = (content[:idx] + content[end + 1:]).strip()
                    break

        content = content.strip().lstrip(",").strip()

        if not content or len(content) > MAX_CHARS:
            return

        if is_suspicious(content):
            await message.reply("Maaf, pertanyaan gak sesuai. Coba tanya yang lain.", mention_author=True)
            return

        async with message.channel.typing():
            reply = None
            for tier in ("primary", "fallback"):
                cfg = MODELS[tier]
                try:
                    reply = await asyncio.wait_for(
                        self.bot.loop.run_in_executor(None, ask_ollama, content, cfg),
                        timeout=cfg["timeout"] + 5,
                    )
                    if reply:
                        break
                except Exception:
                    if tier == "fallback":
                        reply = "Maaf, otakku lagi lemot. Coba tanya lagi nanti."

        if not reply:
            reply = "Maaf, otakku lagi lemot. Coba tanya lagi nanti."

        if len(reply) > 2000:
            reply = reply[:1997] + "..."

        await message.reply(reply, mention_author=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AiChatCog(bot))
