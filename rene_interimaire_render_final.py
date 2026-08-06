from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from flask import Flask, jsonify

import discord
from discord import app_commands
from discord.ext import commands, tasks

# ============================================================
# RENÉ L'INTÉRIMAIRE — VOIDLOOP'S STUDIO
# ============================================================
#
# Images à placer dans le même dossier :
#   CRY.png
#   READ.png
#   NEW.png
#   C'EST_NOTÉ.png
#   REFLECHIS.png
#   DELIVER.png
#   INSPECT.png
#   HAMMER.png
#   SLEEP.png
#   IDLE.png
#
# Installation :
#   pip install -U discord.py Flask
#
# Active dans le portail développeur Discord :
#   - SERVER MEMBERS INTENT
#   - MESSAGE CONTENT INTENT
#
# Le bannissement Roblox est uniquement simulé.
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

CONFIG_FILE = BASE_DIR / "config.json"
WARNINGS_FILE = BASE_DIR / "warnings.json"
ROBLOX_LINKS_FILE = BASE_DIR / "roblox_links.json"
TEMP_BANS_FILE = BASE_DIR / "temporary_bans.json"
CASES_FILE = BASE_DIR / "moderation_cases.json"

IMAGE_CRY = BASE_DIR / "CRY.png"
IMAGE_READ = BASE_DIR / "READ.png"
IMAGE_NEW = BASE_DIR / "NEW.png"
IMAGE_NOTED = BASE_DIR / "C'EST_NOTÉ.png"
IMAGE_THINKING = BASE_DIR / "REFLECHIS.png"
IMAGE_DELIVER = BASE_DIR / "DELIVER.png"
IMAGE_INSPECT = BASE_DIR / "INSPECT.png"
IMAGE_HAMMER = BASE_DIR / "HAMMER.png"
IMAGE_SLEEP = BASE_DIR / "SLEEP.png"
IMAGE_IDLE = BASE_DIR / "IDLE.png"

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

MAX_WARNINGS = 10
DISCORD_BAN_DURATION = timedelta(days=1)
FAKE_ROBLOX_BAN_DAYS = 10
DM_DELAY_SECONDS = 0.8

WELCOME_MESSAGE = (
    "Bienvenue {mention}, amuse-toi bien dans **VoidLoop's Studio** !"
)

WARNING_MESSAGE = (
    "Attention {mention}, ça fait **{count} fois sur {maximum}** "
    "que je te reprends.\n\n"
    "À la 10e fois, je te bannis **10 jours du jeu Roblox** "
    "et **1 jour de Discord** !"
)

BAD_WORDS = {
    "fdp",
    "ntm",
    "pute",
    "putain",
    "salope",
    "connard",
    "connasse",
    "encule",
    "enculee",
    "enculer",
    "batard",
    "merde",
    "ta gueule",
    "nique",
    "niquer",
}

# Domaines autorisés par défaut.
# Tout autre domaine sera considéré comme une publicité.
DEFAULT_ALLOWED_DOMAINS = {
    "youtube.com",
    "youtu.be",
    "github.com",
    "roblox.com",
    "create.roblox.com",
    "devforum.roblox.com",
    "discord.com",
    "discordapp.com",
    "cdn.discordapp.com",
    "media.discordapp.net",
    "tenor.com",
    "giphy.com",
    "tiktok.com",
    "x.com",
    "twitter.com",
    "instagram.com",
    "twitch.tv",
}

URL_PATTERN = re.compile(
    r"(?i)\b("
    r"(?:https?://|www\.)[^\s<>()]+"
    r"|(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s<>()]*)?"
    r")"
)

DISCORD_INVITE_PATTERN = re.compile(
    r"(?i)(?:https?://)?(?:www\.)?"
    r"(?:discord\.gg|discord(?:app)?\.com/invite)/[A-Za-z0-9-]+"
)

COLOR_PRIMARY = discord.Color.from_rgb(105, 73, 255)
COLOR_SUCCESS = discord.Color.from_rgb(58, 190, 120)
COLOR_WARNING = discord.Color.from_rgb(245, 166, 35)
COLOR_DANGER = discord.Color.from_rgb(225, 70, 85)
COLOR_INFO = discord.Color.from_rgb(65, 145, 255)
COLOR_SLEEP = discord.Color.from_rgb(90, 95, 110)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("rene")


# ============================================================
# JSON
# ============================================================

def save_json(path: Path, data: dict[str, Any]) -> None:
    temporary_path = path.with_suffix(path.suffix + ".tmp")

    with temporary_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)

    temporary_path.replace(path)


def load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        save_json(path, default)
        return default.copy()

    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)

        if isinstance(data, dict):
            return data

    except (OSError, json.JSONDecodeError):
        logger.exception("Impossible de lire %s.", path.name)

    return default.copy()


# ============================================================
# TEXTE, LIENS ET FILTRE
# ============================================================

def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(
        character
        for character in text
        if not unicodedata.combining(character)
    )
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def contains_bad_word(text: str) -> bool:
    cleaned_text = normalize_text(text)

    for bad_word in BAD_WORDS:
        cleaned_word = normalize_text(bad_word)

        if " " in cleaned_word:
            if f" {cleaned_word} " in f" {cleaned_text} ":
                return True
        elif re.search(rf"\b{re.escape(cleaned_word)}\b", cleaned_text):
            return True

    return False


def extract_urls(text: str) -> list[str]:
    return [match.rstrip(".,;!?)]}") for match in URL_PATTERN.findall(text)]


def normalize_domain(url: str) -> str:
    candidate = url if "://" in url else f"https://{url}"
    parsed = urlparse(candidate)
    domain = (parsed.hostname or "").lower()

    if domain.startswith("www."):
        domain = domain[4:]

    return domain


def domain_is_allowed(domain: str, allowed_domains: set[str]) -> bool:
    return any(
        domain == allowed or domain.endswith(f".{allowed}")
        for allowed in allowed_domains
    )


def classify_links(
    text: str,
    allowed_domains: set[str],
) -> tuple[bool, str]:
    urls = extract_urls(text)

    if not urls:
        return True, "Aucun lien détecté."

    if DISCORD_INVITE_PATTERN.search(text):
        return False, "Invitation Discord détectée."

    blocked_domains: list[str] = []

    for url in urls:
        domain = normalize_domain(url)

        if not domain or not domain_is_allowed(domain, allowed_domains):
            blocked_domains.append(domain or url)

    if blocked_domains:
        return False, (
            "Domaine non autorisé : "
            + ", ".join(f"`{domain}`" for domain in blocked_domains[:4])
        )

    return True, "Le lien appartient à un domaine autorisé."


# ============================================================
# INTERFACES
# ============================================================

def build_embed(
    title: str,
    description: str,
    color: discord.Color,
    *,
    footer: str = "René L'Intérimaire • VoidLoop's Studio",
) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text=footer)
    return embed


def image_attachment(
    image_path: Path,
    attachment_name: str = "rene_status.png",
) -> discord.File | None:
    if not image_path.is_file():
        logger.warning("Image introuvable : %s", image_path.name)
        return None

    return discord.File(image_path, filename=attachment_name)


async def send_embed_with_thumbnail(
    destination: discord.abc.Messageable,
    *,
    title: str,
    description: str,
    color: discord.Color,
    image_path: Path,
    allowed_mentions: discord.AllowedMentions | None = None,
) -> discord.Message:
    embed = build_embed(title, description, color)
    file = image_attachment(image_path)

    arguments: dict[str, Any] = {
        "embed": embed,
        "allowed_mentions": (
            allowed_mentions
            if allowed_mentions is not None
            else discord.AllowedMentions.none()
        ),
    }

    if file is not None:
        embed.set_thumbnail(url="attachment://rene_status.png")
        arguments["file"] = file

    return await destination.send(**arguments)


async def edit_embed_with_thumbnail(
    message: discord.Message,
    *,
    title: str,
    description: str,
    color: discord.Color,
    image_path: Path,
) -> None:
    embed = build_embed(title, description, color)
    file = image_attachment(image_path)
    attachments: list[discord.File] = []

    if file is not None:
        embed.set_thumbnail(url="attachment://rene_status.png")
        attachments.append(file)

    try:
        await message.edit(embed=embed, attachments=attachments)
    except discord.HTTPException:
        pass


async def begin_interaction_thinking(
    interaction: discord.Interaction,
    *,
    ephemeral: bool = True,
) -> None:
    embed = build_embed(
        "René réfléchit…",
        "Patiente un instant, René vérifie ses dossiers avant de répondre.",
        COLOR_INFO,
    )
    file = image_attachment(IMAGE_THINKING)

    arguments: dict[str, Any] = {
        "embed": embed,
        "ephemeral": ephemeral,
    }

    if file is not None:
        embed.set_thumbnail(url="attachment://rene_status.png")
        arguments["file"] = file

    await interaction.response.send_message(**arguments)


async def finish_interaction(
    interaction: discord.Interaction,
    *,
    title: str,
    description: str,
    color: discord.Color = COLOR_SUCCESS,
    image_path: Path = IMAGE_NOTED,
) -> None:
    embed = build_embed(title, description, color)
    file = image_attachment(image_path)
    attachments: list[discord.File] = []

    if file is not None:
        embed.set_thumbnail(url="attachment://rene_status.png")
        attachments.append(file)

    await interaction.edit_original_response(
        embed=embed,
        attachments=attachments,
    )


def warning_progress_description(stage: int) -> str:
    stages = {
        15: (
            "📂 **Ouverture du dossier…**\n"
            "`██░░░░░░░░` **15 %**\n\n"
            "René récupère les informations du message."
        ),
        53: (
            "📋 **Vérification du dossier…**\n"
            "`█████░░░░░` **53 %**\n\n"
            "René vérifie l'historique et le motif."
        ),
        100: (
            "✍️ **Signature du dossier…**\n"
            "`██████████` **100 %**\n\n"
            "Le dossier est prêt à être archivé."
        ),
    }
    return stages.get(stage, stages[15])


async def animate_warning_file(
    channel: discord.abc.Messageable,
) -> discord.Message:
    message = await send_embed_with_thumbnail(
        channel,
        title="Traitement du dossier",
        description=warning_progress_description(15),
        color=COLOR_INFO,
        image_path=IMAGE_THINKING,
    )

    await asyncio.sleep(0.7)
    await edit_embed_with_thumbnail(
        message,
        title="Traitement du dossier",
        description=warning_progress_description(53),
        color=COLOR_WARNING,
        image_path=IMAGE_THINKING,
    )

    await asyncio.sleep(0.8)
    await edit_embed_with_thumbnail(
        message,
        title="Traitement du dossier",
        description=warning_progress_description(100),
        color=COLOR_SUCCESS,
        image_path=IMAGE_NOTED,
    )

    await asyncio.sleep(0.5)
    return message


# ============================================================
# PETIT SERVEUR WEB POUR RENDER + UPTIMEROBOT
# ============================================================

web_app = Flask(__name__)


@web_app.get("/")
def web_home():
    return jsonify(
        {
            "bot": "René L'Intérimaire",
            "studio": "VoidLoop Studio",
            "status": "online" if bot_is_ready() else "starting",
        }
    ), 200


@web_app.get("/health")
def web_health():
    return jsonify(
        {
            "ok": True,
            "discord_ready": bot_is_ready(),
            "message": (
                "René est en service !"
                if bot_is_ready()
                else "René démarre..."
            ),
        }
    ), 200


def bot_is_ready() -> bool:
    try:
        return bot.is_ready() and not bot.is_closed()
    except NameError:
        return False


def run_web_server() -> None:
    port = int(os.environ.get("PORT", "10000"))

    web_app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False,
    )


# ============================================================
# BOT
# ============================================================

class ReneBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True

        super().__init__(
            command_prefix="!",
            intents=intents,
            allowed_mentions=discord.AllowedMentions(
                users=True,
                roles=False,
                everyone=False,
                replied_user=False,
            ),
        )

        self.config_data = load_json(CONFIG_FILE, {})
        self.warning_data = load_json(WARNINGS_FILE, {})
        self.roblox_links = load_json(ROBLOX_LINKS_FILE, {})
        self.temporary_bans = load_json(TEMP_BANS_FILE, {})
        self.moderation_cases = load_json(CASES_FILE, {})
        self.idle_avatar_applied = False
        self.stopping = False

    async def setup_hook(self) -> None:
        synced_commands = await self.tree.sync()
        logger.info("%s commandes synchronisées.", len(synced_commands))

        if not temporary_ban_checker.is_running():
            temporary_ban_checker.start()

    async def close(self) -> None:
        if temporary_ban_checker.is_running():
            temporary_ban_checker.cancel()
        await super().close()

    def get_guild_config(self, guild_id: int) -> dict[str, Any]:
        guild_key = str(guild_id)

        if guild_key not in self.config_data:
            self.config_data[guild_key] = {
                "welcome_channel_id": None,
                "announcement_channel_id": None,
                "staff_records_channel_id": None,
                "allowed_domains": sorted(DEFAULT_ALLOWED_DOMAINS),
            }
            save_json(CONFIG_FILE, self.config_data)

        config = self.config_data[guild_key]
        config.setdefault("welcome_channel_id", None)
        config.setdefault("announcement_channel_id", None)
        config.setdefault("staff_records_channel_id", None)
        config.setdefault("allowed_domains", sorted(DEFAULT_ALLOWED_DOMAINS))
        return config


bot = ReneBot()


# ============================================================
# PRÉSENCE ET AVATAR
# ============================================================

async def set_bot_avatar(image_path: Path) -> bool:
    if bot.user is None or not image_path.is_file():
        return False

    try:
        await bot.user.edit(avatar=image_path.read_bytes())
        return True
    except discord.HTTPException as error:
        logger.warning("Impossible de changer l'avatar : %s", error)
        return False


@bot.event
async def on_ready() -> None:
    if bot.user is None:
        return

    logger.info("Connecté en tant que %s (%s).", bot.user, bot.user.id)

    await bot.change_presence(
        status=discord.Status.online,
        activity=discord.Game(name="classer les dossiers"),
    )

    if not bot.idle_avatar_applied:
        bot.idle_avatar_applied = True
        await set_bot_avatar(IMAGE_IDLE)


# ============================================================
# BIENVENUE
# ============================================================

@bot.event
async def on_member_join(member: discord.Member) -> None:
    config = bot.get_guild_config(member.guild.id)
    channel_id = config.get("welcome_channel_id")

    if not channel_id:
        return

    channel = member.guild.get_channel(int(channel_id))

    if isinstance(channel, discord.TextChannel):
        await send_embed_with_thumbnail(
            channel,
            title="Bienvenue à bord !",
            description=WELCOME_MESSAGE.format(mention=member.mention),
            color=COLOR_PRIMARY,
            image_path=IMAGE_NEW,
            allowed_mentions=discord.AllowedMentions(users=True),
        )


# ============================================================
# AVERTISSEMENTS ET DOSSIERS STAFF
# ============================================================

def get_warning_count(guild_id: int, user_id: int) -> int:
    guild_warnings = bot.warning_data.setdefault(str(guild_id), {})
    return int(guild_warnings.get(str(user_id), 0))


def set_warning_count(guild_id: int, user_id: int, count: int) -> None:
    guild_warnings = bot.warning_data.setdefault(str(guild_id), {})
    guild_warnings[str(user_id)] = count
    save_json(WARNINGS_FILE, bot.warning_data)


def create_case(
    guild_id: int,
    user_id: int,
    moderator_id: int | None,
    *,
    case_type: str,
    reason: str,
    warning_count: int,
    channel_id: int,
    deleted_content: str,
) -> dict[str, Any]:
    guild_key = str(guild_id)
    cases = bot.moderation_cases.setdefault(guild_key, [])

    case = {
        "id": len(cases) + 1,
        "user_id": user_id,
        "moderator_id": moderator_id,
        "type": case_type,
        "reason": reason,
        "warning_count": warning_count,
        "channel_id": channel_id,
        "deleted_content": deleted_content[:800],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    cases.append(case)
    save_json(CASES_FILE, bot.moderation_cases)
    return case


async def send_case_to_staff(
    guild: discord.Guild,
    case: dict[str, Any],
) -> None:
    config = bot.get_guild_config(guild.id)
    channel_id = config.get("staff_records_channel_id")

    if not channel_id:
        return

    channel = guild.get_channel(int(channel_id))

    if not isinstance(channel, discord.TextChannel):
        return

    user = guild.get_member(int(case["user_id"]))
    user_text = user.mention if user else f"<@{case['user_id']}>"

    embed = build_embed(
        f"Dossier #{case['id']} — {case['type']}",
        (
            f"👤 **Membre :** {user_text}\n"
            f"📍 **Salon :** <#{case['channel_id']}>\n"
            f"⚠️ **Avertissements :** {case['warning_count']}/{MAX_WARNINGS}\n"
            f"📝 **Motif :** {case['reason']}\n\n"
            f"**Message supprimé :**\n"
            f"```{case['deleted_content'] or 'Aucun contenu texte'}```"
        ),
        COLOR_WARNING,
    )
    embed.set_footer(
        text=(
            f"Dossier #{case['id']} • Accessible au staff • "
            "René L'Intérimaire"
        )
    )

    file = image_attachment(IMAGE_READ, "dossier_staff.png")
    arguments: dict[str, Any] = {"embed": embed}

    if file is not None:
        embed.set_thumbnail(url="attachment://dossier_staff.png")
        arguments["file"] = file

    try:
        await channel.send(**arguments)
    except discord.HTTPException:
        logger.exception("Impossible d'envoyer le dossier au staff.")


async def add_warning(
    member: discord.Member,
    channel: discord.TextChannel | discord.Thread,
    *,
    reason: str,
    deleted_content: str,
    public_image: Path = IMAGE_CRY,
) -> int:
    progress_message = await animate_warning_file(channel)

    warning_count = min(
        get_warning_count(member.guild.id, member.id) + 1,
        MAX_WARNINGS,
    )
    set_warning_count(member.guild.id, member.id, warning_count)

    case = create_case(
        member.guild.id,
        member.id,
        bot.user.id if bot.user else None,
        case_type="Avertissement",
        reason=reason,
        warning_count=warning_count,
        channel_id=channel.id,
        deleted_content=deleted_content,
    )
    await send_case_to_staff(member.guild, case)

    await edit_embed_with_thumbnail(
        progress_message,
        title="René intervient",
        description=(
            WARNING_MESSAGE.format(
                mention=member.mention,
                count=warning_count,
                maximum=MAX_WARNINGS,
            )
            + f"\n\n📂 **Dossier #{case['id']} archivé pour le staff.**"
        ),
        color=COLOR_WARNING,
        image_path=public_image,
    )

    return warning_count


# ============================================================
# BAN TEMPORAIRE
# ============================================================

def register_temporary_ban(
    guild_id: int,
    user_id: int,
    unban_at: datetime,
) -> None:
    guild_bans = bot.temporary_bans.setdefault(str(guild_id), {})
    guild_bans[str(user_id)] = unban_at.isoformat()
    save_json(TEMP_BANS_FILE, bot.temporary_bans)


def remove_temporary_ban(guild_id: int, user_id: int) -> None:
    guild_key = str(guild_id)
    guild_bans = bot.temporary_bans.get(guild_key, {})
    guild_bans.pop(str(user_id), None)

    if not guild_bans:
        bot.temporary_bans.pop(guild_key, None)

    save_json(TEMP_BANS_FILE, bot.temporary_bans)


@tasks.loop(minutes=1)
async def temporary_ban_checker() -> None:
    now = datetime.now(timezone.utc)

    for guild_key, guild_bans in list(bot.temporary_bans.items()):
        guild = bot.get_guild(int(guild_key))

        if guild is None:
            continue

        for user_key, date_text in list(guild_bans.items()):
            try:
                unban_at = datetime.fromisoformat(date_text)
            except ValueError:
                remove_temporary_ban(int(guild_key), int(user_key))
                continue

            if unban_at.tzinfo is None:
                unban_at = unban_at.replace(tzinfo=timezone.utc)

            if now < unban_at:
                continue

            try:
                await guild.unban(
                    discord.Object(id=int(user_key)),
                    reason="Fin du bannissement automatique de René",
                )
                remove_temporary_ban(int(guild_key), int(user_key))
            except discord.NotFound:
                remove_temporary_ban(int(guild_key), int(user_key))
            except (discord.Forbidden, discord.HTTPException):
                logger.exception("Impossible de débannir %s.", user_key)


@temporary_ban_checker.before_loop
async def before_temporary_ban_checker() -> None:
    await bot.wait_until_ready()


async def punish_member(
    member: discord.Member,
    channel: discord.TextChannel | discord.Thread,
) -> None:
    linked_name = bot.roblox_links.get(
        str(member.guild.id),
        {},
    ).get(str(member.id), "compte Roblox non lié")

    try:
        unban_at = datetime.now(timezone.utc) + DISCORD_BAN_DURATION

        await member.ban(
            reason="10 avertissements automatiques de René",
            delete_message_seconds=0,
        )

        register_temporary_ban(member.guild.id, member.id, unban_at)
        discord_result = "Bannissement Discord appliqué pendant **1 jour**."

    except discord.Forbidden:
        discord_result = "René n'a pas la permission de bannir ce membre."
    except discord.HTTPException as error:
        discord_result = f"Erreur Discord : `{error}`"

    await send_embed_with_thumbnail(
        channel,
        title="Sanction maximale",
        description=(
            f"{member.mention} a atteint **10 avertissements**.\n\n"
            f"🔨 **Discord :** {discord_result}\n"
            f"🎮 **Roblox :** `{linked_name}` est affiché comme banni "
            f"pendant **{FAKE_ROBLOX_BAN_DAYS} jours**.\n"
            "*Aucun véritable bannissement Roblox n'est effectué.*"
        ),
        color=COLOR_DANGER,
        image_path=IMAGE_HAMMER,
        allowed_mentions=discord.AllowedMentions(users=True),
    )

    set_warning_count(member.guild.id, member.id, 0)


# ============================================================
# ANNONCES EN MP
# ============================================================

async def distribute_announcement(message: discord.Message) -> None:
    if (
        message.guild is None
        or not isinstance(message.channel, discord.TextChannel)
    ):
        return

    config = bot.get_guild_config(message.guild.id)
    announcement_channel_id = config.get("announcement_channel_id")

    if (
        not announcement_channel_id
        or message.channel.id != int(announcement_channel_id)
        or not message.mention_everyone
    ):
        return

    tour_message = await send_embed_with_thumbnail(
        message.channel,
        title="René fait sa tournée…",
        description=(
            "📬 **Je fais ma tournée, patiente un instant… (0 %)**\n\n"
            "`░░░░░░░░░░`"
        ),
        color=COLOR_INFO,
        image_path=IMAGE_DELIVER,
    )

    announcement_text = re.sub(
        r"@everyone|@here",
        "",
        message.content,
        flags=re.IGNORECASE,
    ).strip() or "*Annonce sans texte*"

    if message.attachments:
        announcement_text += "\n\n📎 **Pièces jointes :**\n" + "\n".join(
            attachment.url for attachment in message.attachments
        )

    members = [member for member in message.guild.members if not member.bot]
    total = len(members)
    sent = 0
    failed = 0

    for index, member in enumerate(members, start=1):
        try:
            await send_embed_with_thumbnail(
                member,
                title="Message de VoidLoop Studio",
                description=(
                    "🏴‍☠️ **Hé ho matelot, j'ai reçu un message pour toi "
                    "de la part de VoidLoop Studio :**\n\n"
                    f"{announcement_text}"
                ),
                color=COLOR_PRIMARY,
                image_path=IMAGE_READ,
            )
            sent += 1
        except (discord.Forbidden, discord.HTTPException):
            failed += 1

        percent = round(index / total * 100) if total else 100
        blocks = percent // 10

        if index == total or index == 1 or index % 3 == 0:
            await edit_embed_with_thumbnail(
                tour_message,
                title="René fait sa tournée…",
                description=(
                    f"📬 **Je fais ma tournée, patiente un instant… "
                    f"({percent} %)**\n\n"
                    f"`{'█' * blocks}{'░' * (10 - blocks)}`\n\n"
                    f"**{index}/{total} membres traités**"
                ),
                color=COLOR_INFO,
                image_path=IMAGE_DELIVER,
            )

        await asyncio.sleep(DM_DELAY_SECONDS)

    await edit_embed_with_thumbnail(
        tour_message,
        title="C'est noté !",
        description=(
            "René a terminé sa tournée.\n\n"
            f"✅ **{sent} MP envoyés**\n"
            f"❌ **{failed} échecs**"
        ),
        color=COLOR_SUCCESS,
        image_path=IMAGE_NOTED,
    )


# ============================================================
# ANTI-PUB
# ============================================================

async def inspect_links(message: discord.Message) -> str:
    """Retourne : none, allowed ou blocked."""

    urls = extract_urls(message.content)

    if not urls or message.guild is None:
        return "none"

    config = bot.get_guild_config(message.guild.id)
    announcement_channel_id = config.get("announcement_channel_id")

    # Tous les liens sont autorisés dans le salon d'annonces.
    if (
        announcement_channel_id
        and message.channel.id == int(announcement_channel_id)
    ):
        return "allowed"

    # Le staff peut publier des liens sans contrôle.
    if isinstance(message.author, discord.Member):
        if (
            message.author.guild_permissions.manage_messages
            or message.author.guild_permissions.administrator
        ):
            return "allowed"

    inspection_message = await send_embed_with_thumbnail(
        message.channel,
        title="René réfléchit…",
        description=(
            "Un colis contenant un lien vient d'arriver.\n"
            "René vérifie d'abord le bordereau."
        ),
        color=COLOR_INFO,
        image_path=IMAGE_THINKING,
    )

    await asyncio.sleep(0.7)

    await edit_embed_with_thumbnail(
        inspection_message,
        title="René transporte le colis…",
        description=(
            "📦 Le colis est en route vers le bureau de contrôle.\n\n"
            "`████░░░░░░` **40 %**"
        ),
        color=COLOR_INFO,
        image_path=IMAGE_DELIVER,
    )

    await asyncio.sleep(0.8)

    await edit_embed_with_thumbnail(
        inspection_message,
        title="René inspecte le colis…",
        description=(
            "🔎 Analyse du lien, du domaine et de sa destination.\n\n"
            "`████████░░` **80 %**"
        ),
        color=COLOR_WARNING,
        image_path=IMAGE_INSPECT,
    )

    await asyncio.sleep(1)

    allowed_domains = {
        str(domain).lower()
        for domain in config.get("allowed_domains", DEFAULT_ALLOWED_DOMAINS)
    }
    allowed, reason = classify_links(message.content, allowed_domains)

    if allowed:
        await edit_embed_with_thumbnail(
            inspection_message,
            title="Lien autorisé",
            description=(
                "✅ **Le colis est conforme.**\n\n"
                f"{reason}\n"
                "Le message reste visible."
            ),
            color=COLOR_SUCCESS,
            image_path=IMAGE_NOTED,
        )
        return "allowed"

    original_content = message.content

    try:
        await message.delete()
    except discord.HTTPException:
        logger.warning("Impossible de supprimer la publicité.")

    if not isinstance(message.author, discord.Member):
        return "blocked"

    warning_count = await add_warning(
        message.author,
        message.channel,
        reason=f"Publicité ou lien non autorisé — {reason}",
        deleted_content=original_content,
        public_image=IMAGE_HAMMER,
    )

    await edit_embed_with_thumbnail(
        inspection_message,
        title="Publicité détectée",
        description=(
            "❌ **Le colis a été refusé et le message supprimé.**\n\n"
            f"{reason}\n"
            f"{message.author.mention} possède maintenant "
            f"**{warning_count}/{MAX_WARNINGS} avertissements**."
        ),
        color=COLOR_DANGER,
        image_path=IMAGE_HAMMER,
    )

    if warning_count >= MAX_WARNINGS:
        await punish_member(message.author, message.channel)

    return "blocked"


# ============================================================
# MESSAGES
# ============================================================

@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot or message.guild is None or bot.stopping:
        return

    await distribute_announcement(message)

    # Les liens sont inspectés avant le filtre de grossièretés.
    link_result = await inspect_links(message)

    # Une publicité supprimée a déjà généré son avertissement.
    if link_result == "blocked":
        return

    if not contains_bad_word(message.content):
        return

    original_content = message.content

    try:
        await message.delete()
    except discord.HTTPException:
        logger.warning("Impossible de supprimer le message grossier.")

    if not isinstance(message.author, discord.Member):
        return

    warning_count = await add_warning(
        message.author,
        message.channel,
        reason="Langage grossier ou insultant",
        deleted_content=original_content,
        public_image=IMAGE_CRY,
    )

    if warning_count >= MAX_WARNINGS:
        await punish_member(message.author, message.channel)


# ============================================================
# /CONFIG
# ============================================================

@bot.tree.command(
    name="config",
    description="Configurer René L'Intérimaire.",
)
@app_commands.describe(
    salon_bienvenue="Salon des messages de bienvenue",
    salon_annonces="Salon où les @everyone sont envoyés en MP",
    salon_dossiers="Salon privé contenant les dossiers de modération",
)
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def config_command(
    interaction: discord.Interaction,
    salon_bienvenue: discord.TextChannel | None = None,
    salon_annonces: discord.TextChannel | None = None,
    salon_dossiers: discord.TextChannel | None = None,
) -> None:
    assert interaction.guild is not None

    await begin_interaction_thinking(interaction)

    config = bot.get_guild_config(interaction.guild.id)

    if salon_bienvenue is not None:
        config["welcome_channel_id"] = salon_bienvenue.id

    if salon_annonces is not None:
        config["announcement_channel_id"] = salon_annonces.id

    if salon_dossiers is not None:
        config["staff_records_channel_id"] = salon_dossiers.id

    save_json(CONFIG_FILE, bot.config_data)
    await asyncio.sleep(0.7)

    welcome_id = config.get("welcome_channel_id")
    announcements_id = config.get("announcement_channel_id")
    records_id = config.get("staff_records_channel_id")

    await finish_interaction(
        interaction,
        title="C'est noté !",
        description=(
            "La configuration de René est enregistrée.\n\n"
            f"👋 **Bienvenue :** "
            f"{f'<#{welcome_id}>' if welcome_id else 'non configuré'}\n"
            f"📢 **Annonces :** "
            f"{f'<#{announcements_id}>' if announcements_id else 'non configuré'}\n"
            f"📂 **Dossiers du staff :** "
            f"{f'<#{records_id}>' if records_id else 'non configuré'}\n\n"
            "Les liens sont toujours autorisés dans le salon d'annonces."
        ),
    )


# ============================================================
# /DOSSIER
# ============================================================

@bot.tree.command(
    name="dossier",
    description="Consulter le dossier de modération d'un membre.",
)
@app_commands.describe(membre="Membre dont le dossier doit être consulté")
@app_commands.default_permissions(manage_messages=True)
@app_commands.guild_only()
async def dossier_command(
    interaction: discord.Interaction,
    membre: discord.Member,
) -> None:
    assert interaction.guild is not None

    await begin_interaction_thinking(interaction)

    cases = [
        case
        for case in bot.moderation_cases.get(str(interaction.guild.id), [])
        if int(case.get("user_id", 0)) == membre.id
    ]

    if not cases:
        await finish_interaction(
            interaction,
            title="Dossier vide",
            description=(
                f"{membre.mention} ne possède aucun dossier de modération."
            ),
        )
        return

    recent_cases = cases[-5:]
    lines: list[str] = []

    for case in reversed(recent_cases):
        created_at = case.get("created_at", "")
        try:
            timestamp = int(datetime.fromisoformat(created_at).timestamp())
            date_text = f"<t:{timestamp}:f>"
        except (ValueError, TypeError):
            date_text = "date inconnue"

        lines.append(
            f"**Dossier #{case.get('id')}** — {case.get('type')}\n"
            f"Motif : {case.get('reason')}\n"
            f"Date : {date_text}\n"
            f"Warns après action : {case.get('warning_count')}/{MAX_WARNINGS}"
        )

    await finish_interaction(
        interaction,
        title=f"Dossier de {membre.display_name}",
        description=(
            f"📂 **{len(cases)} action(s) enregistrée(s)**\n\n"
            + "\n\n".join(lines)
        ),
        color=COLOR_WARNING,
        image_path=IMAGE_READ,
    )


# ============================================================
# /LIEROBLOX
# ============================================================

@bot.tree.command(
    name="lieroblox",
    description="Lier ton pseudo Roblox à ton compte Discord.",
)
@app_commands.describe(pseudo="Ton pseudo Roblox exact")
@app_commands.guild_only()
async def link_roblox_command(
    interaction: discord.Interaction,
    pseudo: str,
) -> None:
    assert interaction.guild is not None

    await begin_interaction_thinking(interaction)
    pseudo = pseudo.strip()

    if not re.fullmatch(r"[A-Za-z0-9_]{3,20}", pseudo):
        await finish_interaction(
            interaction,
            title="Pseudo invalide",
            description=(
                "Le pseudo doit contenir entre 3 et 20 caractères, "
                "avec uniquement des lettres, chiffres ou `_`."
            ),
            color=COLOR_DANGER,
            image_path=IMAGE_CRY,
        )
        return

    links = bot.roblox_links.setdefault(str(interaction.guild.id), {})
    links[str(interaction.user.id)] = pseudo
    save_json(ROBLOX_LINKS_FILE, bot.roblox_links)

    await finish_interaction(
        interaction,
        title="C'est noté !",
        description=(
            f"Ton compte est lié au pseudo Roblox **{pseudo}**.\n"
            "Ce lien sert seulement aux sanctions Roblox simulées."
        ),
    )


# ============================================================
# /WARNINGS ET /RESETWARNINGS
# ============================================================

@bot.tree.command(
    name="warnings",
    description="Voir les avertissements d'un membre.",
)
@app_commands.describe(membre="Membre à vérifier")
@app_commands.default_permissions(manage_messages=True)
@app_commands.guild_only()
async def warnings_command(
    interaction: discord.Interaction,
    membre: discord.Member,
) -> None:
    assert interaction.guild is not None

    await begin_interaction_thinking(interaction)
    count = get_warning_count(interaction.guild.id, membre.id)

    await finish_interaction(
        interaction,
        title="C'est noté !",
        description=(
            f"{membre.mention} possède **{count}/{MAX_WARNINGS} avertissements**."
        ),
        color=COLOR_WARNING if count else COLOR_SUCCESS,
    )


@bot.tree.command(
    name="resetwarnings",
    description="Remettre les avertissements d'un membre à zéro.",
)
@app_commands.describe(membre="Membre à réinitialiser")
@app_commands.default_permissions(manage_messages=True)
@app_commands.guild_only()
async def reset_warnings_command(
    interaction: discord.Interaction,
    membre: discord.Member,
) -> None:
    assert interaction.guild is not None

    await begin_interaction_thinking(interaction)
    set_warning_count(interaction.guild.id, membre.id, 0)

    await finish_interaction(
        interaction,
        title="C'est noté !",
        description=(
            f"Les avertissements de {membre.mention} ont été remis à zéro."
        ),
    )


# ============================================================
# /STOP
# ============================================================

@bot.tree.command(
    name="stop",
    description="Arrêter René proprement.",
)
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def stop_command(interaction: discord.Interaction) -> None:
    await begin_interaction_thinking(interaction, ephemeral=False)
    bot.stopping = True
    await asyncio.sleep(0.8)

    await finish_interaction(
        interaction,
        title="C'est noté !",
        description=(
            "René range ses dossiers, termine son café et va dormir.\n"
            "Le bot va maintenant s'arrêter."
        ),
        color=COLOR_SLEEP,
        image_path=IMAGE_SLEEP,
    )

    try:
        await bot.change_presence(
            status=discord.Status.idle,
            activity=discord.Game(name="dormir après son intérim"),
        )
    except discord.HTTPException:
        pass

    await set_bot_avatar(IMAGE_SLEEP)
    await asyncio.sleep(2)
    await bot.close()


# ============================================================
# ERREURS
# ============================================================

@bot.tree.error
async def slash_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
) -> None:
    logger.error("Erreur de commande : %s", error)

    if isinstance(error, app_commands.MissingPermissions):
        title = "Permission refusée"
        description = "Tu n'as pas la permission d'utiliser cette commande."
    else:
        title = "René a fait une erreur"
        description = "Quelque chose s'est mal passé pendant la commande."

    embed = build_embed(title, description, COLOR_DANGER)
    file = image_attachment(IMAGE_CRY)

    arguments: dict[str, Any] = {
        "embed": embed,
        "ephemeral": True,
    }

    if file is not None:
        embed.set_thumbnail(url="attachment://rene_status.png")
        arguments["file"] = file

    try:
        if interaction.response.is_done():
            await interaction.followup.send(**arguments)
        else:
            await interaction.response.send_message(**arguments)
    except discord.HTTPException:
        pass


# ============================================================
# LANCEMENT
# ============================================================

if __name__ == "__main__":
    import traceback

    print("[DÉMARRAGE] Lancement de René...", flush=True)
    print(
        f"[TOKEN] Variable présente : {bool(DISCORD_TOKEN)}",
        flush=True,
    )

    if not DISCORD_TOKEN:
        raise RuntimeError(
            "La variable DISCORD_TOKEN est absente dans Render."
        )

    try:
        print("[WEB] Démarrage du serveur Flask...", flush=True)

        web_thread = threading.Thread(
            target=run_web_server,
            name="rene-web-server",
            daemon=True,
        )
        web_thread.start()

        print("[DISCORD] Connexion de René à Discord...", flush=True)

        bot.run(
            DISCORD_TOKEN.strip(),
            log_handler=None,
        )

    except Exception:
        print("[ERREUR FATALE] René n'a pas pu démarrer :", flush=True)
        traceback.print_exc()
        raise