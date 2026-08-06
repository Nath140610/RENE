from __future__ import annotations

import asyncio
import base64
import gzip
import hashlib
import io
import json
import logging
import os
import random
import re
import threading
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken
from flask import Flask, jsonify
from waitress import serve
import imageio_ffmpeg

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
#   ATTENTE.mp3
#
# Installation :
#   pip install -r requirements.txt
#
# Active dans le portail développeur Discord :
#   - SERVER MEMBERS INTENT
#   - MESSAGE CONTENT INTENT
#
# Aucun réglage OAuth2 RPC vocal n'est nécessaire.
#
# Le bannissement Roblox est uniquement simulé.
# ============================================================

BASE_DIR = Path(__file__).resolve().parent


def asset_path(*filenames: str) -> Path:
    """Trouve un asset même si l'extension PNG est écrite en majuscules."""
    for filename in filenames:
        exact = BASE_DIR / filename
        if exact.exists():
            return exact

    try:
        files_by_name = {
            path.name.casefold(): path
            for path in BASE_DIR.iterdir()
            if path.is_file()
        }
        for filename in filenames:
            found = files_by_name.get(filename.casefold())
            if found is not None:
                return found
    except OSError:
        pass

    return BASE_DIR / filenames[0]


CONFIG_FILE = BASE_DIR / "config.json"
WARNINGS_FILE = BASE_DIR / "warnings.json"
ROBLOX_LINKS_FILE = BASE_DIR / "roblox_links.json"
TEMP_BANS_FILE = BASE_DIR / "temporary_bans.json"
CASES_FILE = BASE_DIR / "moderation_cases.json"
DEPARTED_MEMBERS_FILE = BASE_DIR / "departed_members.json"
QUESTIONS_FILE = BASE_DIR / "questions.json"

IMAGE_CRY = asset_path("CRY.png")
IMAGE_READ = asset_path("READ.png")
IMAGE_NEW = asset_path("NEW.png")
IMAGE_NOTED = asset_path("C'EST_NOTÉ.png", "C'EST_NOTÉ.PNG", "CEST_NOTE.png")
IMAGE_THINKING = asset_path("REFLECHIS.png")
IMAGE_DELIVER = asset_path("DELIVER.png")
IMAGE_INSPECT = asset_path("INSPECT.png")
IMAGE_HAMMER = asset_path("HAMMER.png")
IMAGE_SLEEP = asset_path("SLEEP.png")
IMAGE_IDLE = asset_path("IDLE.png")
AUDIO_WAITING = asset_path("ATTENTE.mp3", "ATTENTE.MP3")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

MAX_WARNINGS = 10
DISCORD_BAN_DURATION = timedelta(days=1)
FAKE_ROBLOX_BAN_DAYS = 10
DM_DELAY_SECONDS = 0.8
STATUS_CHANNEL_ID = 1373577090213085204
STATE_BACKUP_MARKER = "RENE_STATE_BACKUP_V3"
STATE_BACKUP_FILENAME = "rene_state_backup.bin"
LEGACY_CONFIG_BACKUP_MARKER = "RENE_CONFIG_BACKUP_V1"
LEGACY_CONFIG_BACKUP_FILENAME = "rene_config_backup.json"
VOICE_CONNECT_TIMEOUT_SECONDS = 20.0
VOICE_CONNECT_ATTEMPTS = 4
VOICE_RETRY_DELAY_SECONDS = 3.0
REMOTE_BACKUP_DELAY_SECONDS = 2.5

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
    "tg",
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
    r"(?i)(?<!@)\b("
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
    content: str | None = None,
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

    if content is not None:
        arguments["content"] = content

    if file is not None:
        embed.set_thumbnail(url="attachment://rene_status.png")
        arguments["file"] = file

    return await destination.send(**arguments)


async def reply_embed_with_thumbnail(
    message: discord.Message,
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
        "mention_author": False,
        "allowed_mentions": (
            allowed_mentions
            if allowed_mentions is not None
            else discord.AllowedMentions.none()
        ),
    }

    if file is not None:
        embed.set_thumbnail(url="attachment://rene_status.png")
        arguments["file"] = file

    return await message.reply(**arguments)


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


def current_web_status() -> str:
    try:
        if bot.stopping:
            return "sleeping"
        if bot_is_ready():
            return "online"
    except NameError:
        pass
    return "starting"


@web_app.get("/")
def web_home():
    status = current_web_status()
    return jsonify(
        {
            "bot": "René L'Intérimaire",
            "studio": "VoidLoop Studio",
            "status": status,
        }
    ), 200


@web_app.get("/health")
def web_health():
    status = current_web_status()
    return jsonify(
        {
            "ok": True,
            "status": status,
            "discord_ready": bot_is_ready(),
            "message": (
                "René est en service !"
                if status == "online"
                else "René dort volontairement."
                if status == "sleeping"
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
    serve(
        web_app,
        host="0.0.0.0",
        port=port,
        threads=2,
        clear_untrusted_proxy_headers=True,
    )


# ============================================================
# BOT
# ============================================================

class ReneCommandTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        command_name = (
            interaction.command.name
            if interaction.command is not None
            else "commande"
        )
        await set_task_presence(command_name)
        return True


class ReneBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.message_content = True
        intents.voice_states = True

        super().__init__(
            command_prefix="!",
            intents=intents,
            tree_cls=ReneCommandTree,
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
        self.departed_members = load_json(DEPARTED_MEMBERS_FILE, {})
        self.questions = load_json(QUESTIONS_FILE, {})
        self.idle_avatar_applied = False
        self.last_task_until: datetime | None = None
        self.presence_lock = asyncio.Lock()
        self.waiting_voice_tasks: dict[int, asyncio.Task[None]] = {}
        self.waiting_voice_locks: dict[int, asyncio.Lock] = {}
        self.voice_connect_locks: dict[int, asyncio.Lock] = {}
        self.question_locks: dict[tuple[int, str], asyncio.Lock] = {}
        self.remote_state_loaded = False
        self.remote_state_message_id: int | None = None
        self.state_backup_task: asyncio.Task[None] | None = None
        self.state_backup_dirty = False
        self.stopping = False

    async def setup_hook(self) -> None:
        synced_commands = await self.tree.sync()
        logger.info("%s commandes synchronisées.", len(synced_commands))

    async def close(self) -> None:
        if self.state_backup_task and not self.state_backup_task.done():
            self.state_backup_task.cancel()
            await asyncio.gather(self.state_backup_task, return_exceptions=True)

        if self.remote_state_loaded and self.user is not None:
            try:
                await asyncio.wait_for(
                    persist_state_to_discord(),
                    timeout=12.0,
                )
            except (asyncio.TimeoutError, discord.HTTPException):
                logger.exception("La sauvegarde finale de René a échoué.")

        if temporary_ban_checker.is_running():
            temporary_ban_checker.cancel()
        if funny_presence_loop.is_running():
            funny_presence_loop.cancel()
        if seniority_refresh_loop.is_running():
            seniority_refresh_loop.cancel()

        for task in list(self.waiting_voice_tasks.values()):
            task.cancel()
        if self.waiting_voice_tasks:
            await asyncio.gather(
                *self.waiting_voice_tasks.values(),
                return_exceptions=True,
            )
        self.waiting_voice_tasks.clear()

        for guild in list(self.guilds):
            await disconnect_waiting_voice(guild)

        await super().close()

    def get_guild_config(self, guild_id: int) -> dict[str, Any]:
        guild_key = str(guild_id)

        if guild_key not in self.config_data:
            self.config_data[guild_key] = {
                "welcome_channel_id": None,
                "announcement_channel_id": None,
                "staff_records_channel_id": None,
                "seniority_channel_id": None,
                "questions_channel_id": None,
                "moderator_role_id": None,
                "waiting_voice_channel_id": None,
                "seniority_message_id": None,
                "allowed_domains": sorted(DEFAULT_ALLOWED_DOMAINS),
            }
            save_json(CONFIG_FILE, self.config_data)

        config = self.config_data[guild_key]
        config.setdefault("welcome_channel_id", None)
        config.setdefault("announcement_channel_id", None)
        config.setdefault("staff_records_channel_id", None)
        config.setdefault("seniority_channel_id", None)
        config.setdefault("questions_channel_id", None)
        config.setdefault("moderator_role_id", None)
        config.setdefault("waiting_voice_channel_id", None)
        config.setdefault("seniority_message_id", None)
        config.setdefault("allowed_domains", sorted(DEFAULT_ALLOWED_DOMAINS))
        return config


bot = ReneBot()




# ============================================================
# SAUVEGARDE PERSISTANTE ET CHIFFRÉE DANS DISCORD
# ============================================================

def write_all_local_state() -> None:
    save_json(CONFIG_FILE, bot.config_data)
    save_json(WARNINGS_FILE, bot.warning_data)
    save_json(ROBLOX_LINKS_FILE, bot.roblox_links)
    save_json(TEMP_BANS_FILE, bot.temporary_bans)
    save_json(CASES_FILE, bot.moderation_cases)
    save_json(DEPARTED_MEMBERS_FILE, bot.departed_members)
    save_json(QUESTIONS_FILE, bot.questions)


def build_state_snapshot() -> dict[str, Any]:
    return {
        "version": 3,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "config": bot.config_data,
        "warnings": bot.warning_data,
        "roblox_links": bot.roblox_links,
        "temporary_bans": bot.temporary_bans,
        "moderation_cases": bot.moderation_cases,
        "departed_members": bot.departed_members,
        "questions": bot.questions,
    }


def apply_state_snapshot(snapshot: dict[str, Any]) -> None:
    mapping = {
        "config": "config_data",
        "warnings": "warning_data",
        "roblox_links": "roblox_links",
        "temporary_bans": "temporary_bans",
        "moderation_cases": "moderation_cases",
        "departed_members": "departed_members",
        "questions": "questions",
    }

    for key, attribute in mapping.items():
        value = snapshot.get(key)
        if isinstance(value, dict):
            setattr(bot, attribute, value)

    write_all_local_state()


def build_state_cipher(secret: str) -> Fernet:
    digest = hashlib.sha256(secret.strip().encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def get_primary_state_cipher() -> Fernet:
    # STATE_SECRET est recommandé. Sinon, le token Discord est utilisé.
    secret = os.getenv("STATE_SECRET") or DISCORD_TOKEN or "rene-local-fallback"
    return build_state_cipher(secret)


def get_state_decryption_ciphers() -> list[Fernet]:
    # Permet de définir STATE_SECRET sans perdre une ancienne sauvegarde
    # qui avait été chiffrée avec le token Discord.
    secrets: list[str] = []
    for candidate in (
        os.getenv("STATE_SECRET"),
        DISCORD_TOKEN,
        "rene-local-fallback",
    ):
        if candidate and candidate.strip() not in secrets:
            secrets.append(candidate.strip())
    return [build_state_cipher(secret) for secret in secrets]


def encode_state_snapshot(snapshot: dict[str, Any]) -> bytes:
    raw = json.dumps(
        snapshot,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    compressed = gzip.compress(raw, compresslevel=6)
    return get_primary_state_cipher().encrypt(compressed)


def decode_state_snapshot(payload: bytes) -> dict[str, Any]:
    last_error: InvalidToken | None = None
    for cipher in get_state_decryption_ciphers():
        try:
            decrypted = cipher.decrypt(payload)
            raw = gzip.decompress(decrypted)
            decoded = json.loads(raw.decode("utf-8"))
            if not isinstance(decoded, dict):
                raise ValueError("La sauvegarde distante est invalide.")
            return decoded
        except InvalidToken as error:
            last_error = error

    raise last_error or InvalidToken


async def get_persistence_channel() -> discord.TextChannel | None:
    channel = bot.get_channel(STATUS_CHANNEL_ID)

    if channel is None:
        try:
            channel = await bot.fetch_channel(STATUS_CHANNEL_ID)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    return channel if isinstance(channel, discord.TextChannel) else None


async def iter_pinned_messages(
    channel: discord.TextChannel,
) -> list[discord.Message]:
    try:
        return [message async for message in channel.pins()]
    except (discord.Forbidden, discord.HTTPException):
        return []


async def find_backup_message(
    channel: discord.TextChannel,
    marker: str,
) -> discord.Message | None:
    if marker == STATE_BACKUP_MARKER and bot.remote_state_message_id:
        try:
            return await channel.fetch_message(bot.remote_state_message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            bot.remote_state_message_id = None

    for message in await iter_pinned_messages(channel):
        if (
            bot.user is not None
            and message.author.id == bot.user.id
            and marker in message.content
        ):
            if marker == STATE_BACKUP_MARKER:
                bot.remote_state_message_id = message.id
            return message

    try:
        async for message in channel.history(limit=150):
            if (
                bot.user is not None
                and message.author.id == bot.user.id
                and marker in message.content
            ):
                if marker == STATE_BACKUP_MARKER:
                    bot.remote_state_message_id = message.id
                return message
    except (discord.Forbidden, discord.HTTPException):
        pass

    return None


async def load_legacy_config_backup(
    channel: discord.TextChannel,
) -> bool:
    message = await find_backup_message(channel, LEGACY_CONFIG_BACKUP_MARKER)
    if message is None:
        return False

    attachment = next(
        (
            item
            for item in message.attachments
            if item.filename == LEGACY_CONFIG_BACKUP_FILENAME
        ),
        None,
    )
    if attachment is None:
        return False

    try:
        data = json.loads((await attachment.read()).decode("utf-8"))
        if not isinstance(data, dict):
            return False
        bot.config_data = data
        write_all_local_state()
        logger.info("Ancienne sauvegarde /config migrée avec succès.")
        return True
    except (UnicodeDecodeError, json.JSONDecodeError, discord.HTTPException):
        logger.exception("Impossible de migrer l'ancienne sauvegarde /config.")
        return False


async def load_state_from_discord() -> bool:
    if bot.user is None:
        return False

    channel = await get_persistence_channel()
    if channel is None:
        logger.warning(
            "Impossible d'accéder au salon de sauvegarde %s.",
            STATUS_CHANNEL_ID,
        )
        return False

    message = await find_backup_message(channel, STATE_BACKUP_MARKER)
    if message is None:
        return await load_legacy_config_backup(channel)

    attachment = next(
        (
            item
            for item in message.attachments
            if item.filename == STATE_BACKUP_FILENAME
        ),
        None,
    )
    if attachment is None:
        logger.warning("Le fichier de sauvegarde de René est absent.")
        return False

    try:
        snapshot = decode_state_snapshot(await attachment.read())
        apply_state_snapshot(snapshot)
        bot.remote_state_message_id = message.id
        logger.info(
            "État persistant rechargé depuis Discord (version %s).",
            snapshot.get("version", "?"),
        )
        return True
    except InvalidToken:
        logger.error(
            "La sauvegarde existe mais sa clé ne correspond plus. "
            "Vérifie la variable STATE_SECRET. René conserve la sauvegarde "
            "existante et ne l'écrase pas."
        )
        # True signifie ici : une sauvegarde existe, mais elle est illisible.
        # Cela empêche on_ready de l'écraser avec un état vide.
        return True
    except (
        OSError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        discord.HTTPException,
    ):
        logger.exception("Impossible de recharger l'état distant de René.")
        return False


async def persist_state_to_discord() -> bool:
    if bot.user is None:
        return False

    channel = await get_persistence_channel()
    if channel is None:
        logger.warning(
            "Sauvegarde distante impossible : salon %s inaccessible.",
            STATUS_CHANNEL_ID,
        )
        return False

    write_all_local_state()
    payload = encode_state_snapshot(build_state_snapshot())
    backup_file = discord.File(
        io.BytesIO(payload),
        filename=STATE_BACKUP_FILENAME,
    )

    content = (
        f"`{STATE_BACKUP_MARKER}`\n"
        "🔐 **Sauvegarde interne chiffrée de René**\n"
        "Elle conserve la configuration, les avertissements et les dossiers "
        "après les redémarrages de Render. Merci de ne pas la supprimer."
    )

    message = await find_backup_message(channel, STATE_BACKUP_MARKER)

    try:
        if message is None:
            message = await channel.send(
                content=content,
                file=backup_file,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            bot.remote_state_message_id = message.id
            try:
                await message.pin(
                    reason="Sauvegarde persistante chiffrée de René"
                )
            except (discord.Forbidden, discord.HTTPException):
                logger.warning(
                    "Sauvegarde créée, mais René n'a pas pu l'épingler."
                )
        else:
            try:
                await message.edit(
                    content=content,
                    attachments=[backup_file],
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                # Recréation propre si Discord refuse le remplacement du fichier.
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass
                message = await channel.send(
                    content=content,
                    file=discord.File(
                        io.BytesIO(payload),
                        filename=STATE_BACKUP_FILENAME,
                    ),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                bot.remote_state_message_id = message.id
                try:
                    await message.pin(
                        reason="Sauvegarde persistante chiffrée de René"
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass

        logger.info("État de René sauvegardé durablement dans Discord.")
        return True
    except discord.HTTPException:
        logger.exception("Impossible de sauvegarder l'état de René dans Discord.")
        return False


def schedule_state_backup(delay: float = REMOTE_BACKUP_DELAY_SECONDS) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    bot.state_backup_dirty = True
    if bot.state_backup_task and not bot.state_backup_task.done():
        return

    async def worker() -> None:
        try:
            while bot.state_backup_dirty and not bot.stopping:
                bot.state_backup_dirty = False
                await asyncio.sleep(delay)
                await persist_state_to_discord()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Erreur pendant la sauvegarde différée de René.")

    bot.state_backup_task = loop.create_task(
        worker(),
        name="rene-state-backup",
    )


def save_runtime_json(path: Path, data: dict[str, Any]) -> None:
    save_json(path, data)
    schedule_state_backup()


async def save_state_immediately() -> None:
    write_all_local_state()
    await persist_state_to_discord()


# ============================================================
# ACTIVITÉ DE RENÉ
# ============================================================

FUNNY_ACTIVITIES = [
    "chercher son café",
    "classer un dossier vide",
    "faire semblant de travailler",
    "attendre la fin de son intérim",
    "réparer l'imprimante",
    "compter ses 0 € de salaire",
    "chercher le bouton « tout réparer »",
    "surveiller les cartons suspects",
    "demander une pause au patron",
    "trier les tickets par couleur",
]


async def set_task_presence(task_name: str) -> None:
    async with bot.presence_lock:
        bot.last_task_until = datetime.now(timezone.utc) + timedelta(minutes=5)
        try:
            await bot.change_presence(
                status=discord.Status.online,
                activity=discord.Game(name=f"/{task_name}"),
            )
        except discord.HTTPException:
            pass


@tasks.loop(minutes=3)
async def funny_presence_loop() -> None:
    if bot.stopping or bot.user is None:
        return

    now = datetime.now(timezone.utc)
    if bot.last_task_until and now < bot.last_task_until:
        return

    try:
        await bot.change_presence(
            status=discord.Status.online,
            activity=discord.Game(name=random.choice(FUNNY_ACTIVITIES)),
        )
    except discord.HTTPException:
        pass


@funny_presence_loop.before_loop
async def before_funny_presence_loop() -> None:
    await bot.wait_until_ready()


# ============================================================
# CLASSEMENT D'ANCIENNETÉ
# ============================================================

def format_duration_between(
    start: datetime | None,
    end: datetime | None = None,
) -> str:
    if start is None:
        return "date inconnue"

    end = end or datetime.now(timezone.utc)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    delta = max(end - start, timedelta())
    days = delta.days
    years, remainder = divmod(days, 365)
    months, days_left = divmod(remainder, 30)

    parts: list[str] = []
    if years:
        parts.append(f"{years} an{'s' if years > 1 else ''}")
    if months:
        parts.append(f"{months} mois")
    if not years and days_left:
        parts.append(f"{days_left} jour{'s' if days_left > 1 else ''}")
    return ", ".join(parts) or "aujourd'hui"


def format_duration_since(date: datetime | None) -> str:
    return format_duration_between(date)


def oldest_current_members(guild: discord.Guild) -> list[discord.Member]:
    members = [
        member for member in guild.members
        if not member.bot and member.joined_at is not None
    ]
    return sorted(members, key=lambda member: member.joined_at)[:10]


def oldest_departed_members(guild: discord.Guild) -> list[dict[str, Any]]:
    records = bot.departed_members.get(str(guild.id), [])
    current_ids = {member.id for member in guild.members}

    usable = [
        record for record in records
        if int(record.get("user_id", 0)) not in current_ids
        and record.get("joined_at")
    ]

    def joined_key(record: dict[str, Any]) -> datetime:
        try:
            return datetime.fromisoformat(str(record["joined_at"]))
        except (ValueError, TypeError):
            return datetime.max.replace(tzinfo=timezone.utc)

    return sorted(usable, key=joined_key)[:10]


async def update_seniority_board(guild: discord.Guild) -> None:
    config = bot.get_guild_config(guild.id)
    channel_id = config.get("seniority_channel_id")

    if not channel_id:
        return

    channel = guild.get_channel(int(channel_id))
    if not isinstance(channel, discord.TextChannel):
        return

    current_lines: list[str] = []
    medals = ["🥇", "🥈", "🥉"]

    for index, member in enumerate(oldest_current_members(guild), start=1):
        prefix = medals[index - 1] if index <= 3 else f"`#{index}`"
        joined = member.joined_at
        timestamp = int(joined.timestamp()) if joined else 0
        current_lines.append(
            f"{prefix} **{member.display_name}** — "
            f"depuis <t:{timestamp}:D> "
            f"(**{format_duration_since(joined)}**)"
        )

    departed_lines: list[str] = []
    for index, record in enumerate(oldest_departed_members(guild), start=1):
        prefix = medals[index - 1] if index <= 3 else f"`#{index}`"
        try:
            joined = datetime.fromisoformat(str(record["joined_at"]))
            joined_ts = int(joined.timestamp())
        except (ValueError, TypeError):
            joined = None
            joined_ts = 0

        left_text = ""
        left: datetime | None = None
        try:
            left = datetime.fromisoformat(str(record.get("left_at", "")))
            left_text = f" • parti <t:{int(left.timestamp())}:R>"
        except (ValueError, TypeError):
            pass

        duration = format_duration_between(joined, left)

        departed_lines.append(
            f"{prefix} **{record.get('display_name', 'Ancien membre')}** — "
            f"avait rejoint <t:{joined_ts}:D> "
            f"(**présent pendant {duration}**){left_text}"
        )

    embed = build_embed(
        "🏆 Classement d'ancienneté",
        (
            "### Membres toujours présents\n"
            + ("\n".join(current_lines) if current_lines else "*Aucun membre classé.*")
            + "\n\n### Anciens membres ayant quitté le serveur\n"
            + ("\n".join(departed_lines) if departed_lines else
               "*Aucun départ enregistré pour le moment.*")
            + "\n\n-# Le classement des anciens membres commence à partir de "
              "l'installation de cette version de René."
        ),
        COLOR_PRIMARY,
    )

    message_id = config.get("seniority_message_id")
    message: discord.Message | None = None

    if message_id:
        try:
            message = await channel.fetch_message(int(message_id))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            message = None

    if message is None:
        message = await channel.send(embed=embed)
        config["seniority_message_id"] = message.id
        await save_state_immediately()
    else:
        await message.edit(embed=embed)


@tasks.loop(hours=6)
async def seniority_refresh_loop() -> None:
    for guild in bot.guilds:
        try:
            await update_seniority_board(guild)
        except Exception:
            logger.exception("Impossible d'actualiser le classement d'ancienneté.")


@seniority_refresh_loop.before_loop
async def before_seniority_refresh_loop() -> None:
    await bot.wait_until_ready()


# ============================================================
# QUESTIONS AUX MODÉRATEURS
# ============================================================

def member_has_moderator_role(member: discord.Member) -> bool:
    config = bot.get_guild_config(member.guild.id)
    role_id = config.get("moderator_role_id")
    has_role = bool(
        role_id
        and any(role.id == int(role_id) for role in member.roles)
    )
    return (
        has_role
        or member.guild_permissions.manage_messages
        or member.guild_permissions.administrator
    )


async def register_question(message: discord.Message) -> None:
    if message.guild is None:
        return

    confirmation_message = await reply_embed_with_thumbnail(
        message,
        title="René réfléchit…",
        description="Je transmets ta question au bon bureau.",
        color=COLOR_INFO,
        image_path=IMAGE_THINKING,
    )

    guild_questions = bot.questions.setdefault(str(message.guild.id), {})

    # Limite la taille de la sauvegarde : supprime d'abord les plus anciennes
    # questions déjà traitées au-delà de 1 000 entrées par serveur.
    if len(guild_questions) >= 1000:
        answered_items = sorted(
            (
                (question_id, data)
                for question_id, data in guild_questions.items()
                if data.get("answered")
            ),
            key=lambda item: str(item[1].get("created_at", "")),
        )
        for question_id, _ in answered_items[: max(1, len(guild_questions) - 999)]:
            guild_questions.pop(question_id, None)

    question_data: dict[str, Any] = {
        "question_message_id": message.id,
        "author_id": message.author.id,
        "channel_id": message.channel.id,
        "content": message.content[:1800],
        "answered": False,
        "answered_by": None,
        "answer": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "staff_notification_message_id": None,
        "confirmation_message_id": confirmation_message.id,
    }
    guild_questions[str(message.id)] = question_data
    save_runtime_json(QUESTIONS_FILE, bot.questions)

    config = bot.get_guild_config(message.guild.id)
    role_id = config.get("moderator_role_id")
    staff_channel_id = config.get("staff_records_channel_id")
    notify_channel = (
        message.guild.get_channel(int(staff_channel_id))
        if staff_channel_id
        else message.channel
    )
    if not isinstance(notify_channel, discord.TextChannel):
        notify_channel = message.channel

    role_mention = f"<@&{role_id}>" if role_id else "**Modérateurs**"
    notification_message = await send_embed_with_thumbnail(
        notify_channel,
        content=f"📨 {role_mention}, une nouvelle question attend une réponse.",
        title="Question reçue",
        description=(
            f"👤 **Auteur :** {message.author.mention}\n"
            f"📍 **Question originale :** [ouvrir le message]({message.jump_url})\n\n"
            f"**Question :**\n{message.content[:1800]}\n\n"
            "Répondez avec la fonction **Répondre** sur le message original "
            "ou sur cette notification. René enverra la réponse en MP."
        ),
        color=COLOR_INFO,
        image_path=IMAGE_READ,
        allowed_mentions=discord.AllowedMentions(
            roles=True,
            users=False,
            everyone=False,
        ),
    )

    question_data["staff_notification_message_id"] = notification_message.id
    save_runtime_json(QUESTIONS_FILE, bot.questions)

    await edit_embed_with_thumbnail(
        confirmation_message,
        title="Question transmise",
        description=(
            "C'est noté ! René a remis ta question aux modérateurs.\n"
            "Tu recevras leur réponse directement en message privé."
        ),
        color=COLOR_SUCCESS,
        image_path=IMAGE_NOTED,
    )


def find_question_from_reference(
    guild_id: int,
    referenced_message_id: int,
) -> tuple[str | None, dict[str, Any] | None]:
    guild_questions = bot.questions.get(str(guild_id), {})
    direct = guild_questions.get(str(referenced_message_id))
    if direct:
        return str(referenced_message_id), direct

    for question_id, question in guild_questions.items():
        related_ids = {
            int(question.get("question_message_id", 0) or 0),
            int(question.get("staff_notification_message_id", 0) or 0),
            int(question.get("confirmation_message_id", 0) or 0),
        }
        if referenced_message_id in related_ids:
            return question_id, question

    return None, None


async def process_moderator_answer(message: discord.Message) -> bool:
    if (
        message.guild is None
        or not isinstance(message.author, discord.Member)
        or not message.reference
        or not message.reference.message_id
        or not member_has_moderator_role(message.author)
    ):
        return False

    question_id, question = find_question_from_reference(
        message.guild.id,
        message.reference.message_id,
    )
    if question is None or question_id is None:
        return False

    lock_key = (message.guild.id, question_id)
    lock = bot.question_locks.setdefault(lock_key, asyncio.Lock())

    async with lock:
        question = bot.questions.get(str(message.guild.id), {}).get(question_id)
        if question is None:
            return False

        if question.get("answered"):
            answered_by_id = question.get("answered_by")
            answered_by = (
                message.guild.get_member(int(answered_by_id))
                if answered_by_id
                else None
            )
            answered_name = (
                answered_by.display_name
                if answered_by
                else "un autre modérateur"
            )
            await reply_embed_with_thumbnail(
                message,
                title="Question déjà traitée",
                description=(
                    f"Désolé {message.author.mention}, "
                    f"**{answered_name}** a déjà répondu à cette question."
                ),
                color=COLOR_WARNING,
                image_path=IMAGE_NOTED,
                allowed_mentions=discord.AllowedMentions(users=True),
            )
            return True

        answer_text = message.content.strip()
        if not answer_text:
            await reply_embed_with_thumbnail(
                message,
                title="Réponse vide",
                description="La réponse ne peut pas être vide.",
                color=COLOR_DANGER,
                image_path=IMAGE_CRY,
            )
            return True

        try:
            user = bot.get_user(int(question["author_id"]))
            if user is None:
                user = await bot.fetch_user(int(question["author_id"]))

            await send_embed_with_thumbnail(
                user,
                title="Réponse à ta question",
                description=(
                    f"**Ta question :**\n{question.get('content', '')}\n\n"
                    f"**Réponse de {message.author.display_name} :**\n"
                    f"{answer_text[:1800]}"
                ),
                color=COLOR_SUCCESS,
                image_path=IMAGE_READ,
            )
            delivery = "La réponse a bien été envoyée en MP."
        except discord.Forbidden:
            delivery = (
                "Impossible d'envoyer le MP : les messages privés du membre "
                "sont probablement fermés."
            )
        except discord.NotFound:
            delivery = "Le compte du membre n'a pas pu être retrouvé."
        except discord.HTTPException as error:
            logger.exception("Erreur pendant l'envoi de la réponse en MP.")
            delivery = f"Erreur Discord pendant l'envoi du MP : `{error}`"

        question["answered"] = True
        question["answered_by"] = message.author.id
        question["answer"] = answer_text[:1800]
        question["answered_at"] = datetime.now(timezone.utc).isoformat()
        question["delivery_result"] = delivery
        bot.questions[str(message.guild.id)][question_id] = question
        save_runtime_json(QUESTIONS_FILE, bot.questions)

        await reply_embed_with_thumbnail(
            message,
            title="Réponse enregistrée",
            description=f"✅ {delivery}",
            color=COLOR_SUCCESS,
            image_path=IMAGE_NOTED,
        )
        return True


# ============================================================
# VOCAL D'ATTENTE URGENTE
# ============================================================

def get_configured_moderators(guild: discord.Guild) -> list[discord.Member]:
    config = bot.get_guild_config(guild.id)
    role_id = config.get("moderator_role_id")
    moderators: list[discord.Member] = []
    seen: set[int] = set()

    if role_id:
        role = guild.get_role(int(role_id))
        if role is not None:
            for member in role.members:
                if not member.bot and member.id not in seen:
                    moderators.append(member)
                    seen.add(member.id)

    if not moderators:
        for member in guild.members:
            if (
                not member.bot
                and member.id not in seen
                and (
                    member.guild_permissions.manage_messages
                    or member.guild_permissions.administrator
                )
            ):
                moderators.append(member)
                seen.add(member.id)

    return moderators


def waiting_members(channel: discord.VoiceChannel) -> list[discord.Member]:
    return [member for member in channel.members if not member.bot]


def get_ffmpeg_executable() -> str:
    custom = os.getenv("FFMPEG_EXECUTABLE")
    return custom or imageio_ffmpeg.get_ffmpeg_exe()


async def notify_moderators_waiting(
    guild: discord.Guild,
    channel: discord.VoiceChannel,
    members: list[discord.Member],
    cycle_number: int,
) -> tuple[int, int]:
    moderators = get_configured_moderators(guild)
    sent = 0
    failed = 0
    waiting_list = "\n".join(
        f"• **{member.display_name}** (`{member.id}`)"
        for member in members
    )

    if not moderators:
        logger.warning(
            "VOCAL : aucun modérateur n'est disponible sur le serveur %s.",
            guild.id,
        )

    for moderator in moderators:
        try:
            await send_embed_with_thumbnail(
                moderator,
                title="🚨 URGENT — Une personne attend",
                description=(
                    f"Une ou plusieurs personnes attendent dans "
                    f"**{channel.name}** sur **{guild.name}**.\n\n"
                    f"{waiting_list}\n\n"
                    f"🔁 **Rappel sonore n°{cycle_number}**\n"
                    "Rejoins rapidement le vocal pour les prendre en charge."
                ),
                color=COLOR_DANGER,
                image_path=IMAGE_DELIVER,
            )
            sent += 1
        except (discord.Forbidden, discord.HTTPException):
            failed += 1

    logger.info(
        "VOCAL : alerte cycle=%s envoyés=%s échecs=%s.",
        cycle_number,
        sent,
        failed,
    )
    return sent, failed


async def cleanup_voice_client(guild: discord.Guild) -> None:
    voice_client = guild.voice_client
    if voice_client is None:
        return

    logger.info("VOCAL : nettoyage de l'ancienne connexion vocale.")
    try:
        if voice_client.is_playing() or voice_client.is_paused():
            voice_client.stop()
    except Exception:
        pass

    try:
        await voice_client.disconnect(force=True)
    except Exception:
        pass

    try:
        voice_client.cleanup()
    except Exception:
        pass

    await asyncio.sleep(1.0)


async def disconnect_waiting_voice(guild: discord.Guild) -> None:
    await cleanup_voice_client(guild)


async def connect_voice_safely(
    guild: discord.Guild,
    channel: discord.VoiceChannel,
) -> discord.VoiceClient | None:
    lock = bot.voice_connect_locks.setdefault(guild.id, asyncio.Lock())

    async with lock:
        me = guild.me
        if me is None:
            logger.error("VOCAL : membre du bot introuvable dans le serveur.")
            return None

        permissions = channel.permissions_for(me)
        if not permissions.view_channel or not permissions.connect or not permissions.speak:
            logger.error(
                "VOCAL : permissions insuffisantes dans %s "
                "(voir=%s connecter=%s parler=%s).",
                channel.name,
                permissions.view_channel,
                permissions.connect,
                permissions.speak,
            )
            return None

        existing = guild.voice_client
        if existing is not None and existing.is_connected():
            if existing.channel and existing.channel.id != channel.id:
                try:
                    await existing.move_to(channel)
                except (discord.ClientException, discord.HTTPException):
                    await cleanup_voice_client(guild)
                else:
                    return existing
            else:
                return existing

        if existing is not None:
            await cleanup_voice_client(guild)

        for attempt in range(1, VOICE_CONNECT_ATTEMPTS + 1):
            if not waiting_members(channel) or bot.stopping:
                return None

            logger.info(
                "VOCAL : tentative de connexion %s/%s à %s.",
                attempt,
                VOICE_CONNECT_ATTEMPTS,
                channel.name,
            )

            try:
                voice_client = await channel.connect(
                    timeout=VOICE_CONNECT_TIMEOUT_SECONDS,
                    reconnect=False,
                    self_deaf=True,
                    self_mute=False,
                )
                await asyncio.sleep(0.5)
                if voice_client.is_connected():
                    logger.info("VOCAL : connexion vocale établie.")
                    return voice_client
                await cleanup_voice_client(guild)
            except (
                asyncio.TimeoutError,
                discord.ClientException,
                discord.ConnectionClosed,
                discord.HTTPException,
            ) as error:
                logger.warning(
                    "VOCAL : échec de connexion %s/%s : %s",
                    attempt,
                    VOICE_CONNECT_ATTEMPTS,
                    error,
                )
                await cleanup_voice_client(guild)

            if attempt < VOICE_CONNECT_ATTEMPTS:
                await asyncio.sleep(VOICE_RETRY_DELAY_SECONDS * attempt)

        logger.error(
            "VOCAL : impossible de rejoindre %s après %s tentatives.",
            channel.name,
            VOICE_CONNECT_ATTEMPTS,
        )
        return None


async def waiting_voice_loop(guild: discord.Guild) -> None:
    cycle_number = 0
    current_task = asyncio.current_task()

    try:
        while not bot.stopping:
            config = bot.get_guild_config(guild.id)
            channel_id = config.get("waiting_voice_channel_id")
            if not channel_id:
                break

            channel = guild.get_channel(int(channel_id))
            if not isinstance(channel, discord.VoiceChannel):
                logger.error("VOCAL : le salon configuré est introuvable.")
                break

            await asyncio.sleep(0.5)
            members = waiting_members(channel)
            if not members:
                break

            if not AUDIO_WAITING.is_file():
                logger.error("VOCAL : ATTENTE.mp3 est introuvable.")
                for moderator in get_configured_moderators(guild):
                    try:
                        await send_embed_with_thumbnail(
                            moderator,
                            title="Son d'attente introuvable",
                            description=(
                                "René ne trouve pas `ATTENTE.mp3` dans son dossier."
                            ),
                            color=COLOR_DANGER,
                            image_path=IMAGE_CRY,
                        )
                    except (discord.Forbidden, discord.HTTPException):
                        pass
                break

            voice_client = await connect_voice_safely(guild, channel)
            if voice_client is None:
                if waiting_members(channel):
                    await asyncio.sleep(VOICE_RETRY_DELAY_SECONDS)
                    continue
                break

            if voice_client.is_playing() or voice_client.is_paused():
                voice_client.stop()
                await asyncio.sleep(0.3)

            members = waiting_members(channel)
            if not members:
                break

            cycle_number += 1
            logger.info(
                "VOCAL : lancement du cycle audio n°%s dans %s.",
                cycle_number,
                channel.name,
            )

            # Le MP est renvoyé à chaque nouvelle lecture, comme demandé.
            await notify_moderators_waiting(
                guild,
                channel,
                members,
                cycle_number,
            )

            playback_finished = asyncio.Event()
            event_loop = asyncio.get_running_loop()

            def after_playback(error: Exception | None) -> None:
                if error is not None:
                    logger.error("VOCAL : erreur de lecture : %s", error)
                event_loop.call_soon_threadsafe(playback_finished.set)

            try:
                ffmpeg = get_ffmpeg_executable()
                logger.info(
                    "VOCAL : lecture de %s avec %s.",
                    AUDIO_WAITING.name,
                    ffmpeg,
                )
                source = discord.FFmpegOpusAudio(
                    str(AUDIO_WAITING),
                    executable=ffmpeg,
                    before_options="-nostdin -hide_banner -loglevel error",
                    options="-vn",
                )
                voice_client.play(source, after=after_playback)

                while not playback_finished.is_set():
                    if not waiting_members(channel):
                        if voice_client.is_playing() or voice_client.is_paused():
                            voice_client.stop()
                        break

                    if not voice_client.is_connected():
                        logger.warning("VOCAL : connexion perdue pendant la lecture.")
                        break

                    try:
                        await asyncio.wait_for(
                            playback_finished.wait(),
                            timeout=1.0,
                        )
                    except asyncio.TimeoutError:
                        pass

            except (discord.ClientException, OSError, RuntimeError) as error:
                logger.exception("VOCAL : impossible de lire ATTENTE.mp3 : %s", error)
                await cleanup_voice_client(guild)
                await asyncio.sleep(VOICE_RETRY_DELAY_SECONDS)

    except asyncio.CancelledError:
        raise
    finally:
        await disconnect_waiting_voice(guild)
        if bot.waiting_voice_tasks.get(guild.id) is current_task:
            bot.waiting_voice_tasks.pop(guild.id, None)


async def ensure_waiting_voice_task(guild: discord.Guild) -> None:
    logger.info(
        "VOCAL : vérification de la tâche d'attente pour le serveur %s.",
        guild.id,
    )
    lock = bot.waiting_voice_locks.setdefault(guild.id, asyncio.Lock())

    async with lock:
        config = bot.get_guild_config(guild.id)
        channel_id = config.get("waiting_voice_channel_id")
        existing = bot.waiting_voice_tasks.get(guild.id)

        if not channel_id:
            if existing and not existing.done():
                existing.cancel()
                await asyncio.gather(existing, return_exceptions=True)
            bot.waiting_voice_tasks.pop(guild.id, None)
            await disconnect_waiting_voice(guild)
            return

        channel = guild.get_channel(int(channel_id))
        if not isinstance(channel, discord.VoiceChannel):
            logger.error("VOCAL : le salon configuré n'est pas un vocal valide.")
            return

        await asyncio.sleep(1.0)
        members = waiting_members(channel)

        if not members:
            logger.info("VOCAL : aucun humain présent.")
            if existing and not existing.done():
                existing.cancel()
                await asyncio.gather(existing, return_exceptions=True)
            bot.waiting_voice_tasks.pop(guild.id, None)
            await disconnect_waiting_voice(guild)
            return

        if existing is not None and not existing.done():
            logger.info("VOCAL : la boucle d'attente est déjà active.")
            return

        logger.info(
            "VOCAL : création de la boucle pour %s humain(s).",
            len(members),
        )
        bot.waiting_voice_tasks[guild.id] = asyncio.create_task(
            waiting_voice_loop(guild),
            name=f"waiting-voice-{guild.id}",
        )


@bot.event
async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
) -> None:
    if member.bot or bot.stopping:
        return

    before_id = before.channel.id if before.channel else None
    after_id = after.channel.id if after.channel else None
    config = bot.get_guild_config(member.guild.id)
    channel_id = config.get("waiting_voice_channel_id")

    logger.info(
        "VOCAL : membre=%s avant=%s après=%s configuré=%s",
        member,
        before_id,
        after_id,
        channel_id,
    )

    if not channel_id:
        return

    watched_id = int(channel_id)
    if before_id != watched_id and after_id != watched_id:
        return

    await asyncio.sleep(1.0)
    await ensure_waiting_voice_task(member.guild)


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

    if not bot.remote_state_loaded:
        bot.remote_state_loaded = True
        loaded = await load_state_from_discord()
        if not loaded or bot.remote_state_message_id is None:
            await persist_state_to_discord()

    # Les boucles démarrent seulement après le chargement de l'état persistant.
    if not temporary_ban_checker.is_running():
        temporary_ban_checker.start()
    if not funny_presence_loop.is_running():
        funny_presence_loop.start()
    if not seniority_refresh_loop.is_running():
        seniority_refresh_loop.start()

    await bot.change_presence(
        status=discord.Status.online,
        activity=discord.Game(name=random.choice(FUNNY_ACTIVITIES)),
    )

    if not bot.idle_avatar_applied:
        bot.idle_avatar_applied = True
        await set_bot_avatar(IMAGE_IDLE)

    status_channel = bot.get_channel(STATUS_CHANNEL_ID)

    if status_channel is None:
        try:
            status_channel = await bot.fetch_channel(STATUS_CHANNEL_ID)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            status_channel = None

    if isinstance(status_channel, discord.TextChannel):
        try:
            await send_embed_with_thumbnail(
                status_channel,
                title="René est reconnecté !",
                description=(
                    "🟢 **René L'Intérimaire est de nouveau en service.**\n\n"
                    "La connexion avec Discord est rétablie et René reprend "
                    "ses dossiers là où il les avait laissés."
                ),
                color=COLOR_SUCCESS,
                image_path=IMAGE_NEW,
            )
        except discord.HTTPException:
            logger.exception(
                "Impossible d'envoyer le message de reconnexion dans le salon status."
            )


    # Si Render ou Discord a redémarré pendant qu'une personne attendait,
    # René reprend automatiquement la boucle sonore.
    for guild in bot.guilds:
        await ensure_waiting_voice_task(guild)


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

    await update_seniority_board(member.guild)


@bot.event
async def on_member_remove(member: discord.Member) -> None:
    if member.bot:
        return

    guild_key = str(member.guild.id)
    records = bot.departed_members.setdefault(guild_key, [])

    records.append(
        {
            "user_id": member.id,
            "display_name": member.display_name,
            "joined_at": (
                member.joined_at.isoformat()
                if member.joined_at else None
            ),
            "left_at": datetime.now(timezone.utc).isoformat(),
        }
    )

    # Garde au maximum les 500 derniers départs enregistrés.
    bot.departed_members[guild_key] = records[-500:]
    save_runtime_json(DEPARTED_MEMBERS_FILE, bot.departed_members)
    await update_seniority_board(member.guild)


# ============================================================
# AVERTISSEMENTS ET DOSSIERS STAFF
# ============================================================

def get_warning_count(guild_id: int, user_id: int) -> int:
    guild_warnings = bot.warning_data.setdefault(str(guild_id), {})
    return int(guild_warnings.get(str(user_id), 0))


def set_warning_count(guild_id: int, user_id: int, count: int) -> None:
    guild_warnings = bot.warning_data.setdefault(str(guild_id), {})
    guild_warnings[str(user_id)] = count
    save_runtime_json(WARNINGS_FILE, bot.warning_data)


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

    next_case_id = max(
        (int(existing.get("id", 0)) for existing in cases),
        default=0,
    ) + 1

    case = {
        "id": next_case_id,
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
    # Les 2 000 dossiers les plus récents par serveur sont conservés.
    bot.moderation_cases[guild_key] = cases[-2000:]
    save_runtime_json(CASES_FILE, bot.moderation_cases)
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
    safe_deleted_content = str(
        case.get("deleted_content") or "Aucun contenu texte"
    ).replace("```", "[code]")

    embed = build_embed(
        f"Dossier #{case['id']} — {case['type']}",
        (
            f"👤 **Membre :** {user_text}\n"
            f"📍 **Salon :** <#{case['channel_id']}>\n"
            f"⚠️ **Avertissements :** {case['warning_count']}/{MAX_WARNINGS}\n"
            f"📝 **Motif :** {case['reason']}\n\n"
            f"**Message supprimé :**\n"
            f"```{safe_deleted_content}```"
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
    save_runtime_json(TEMP_BANS_FILE, bot.temporary_bans)


def remove_temporary_ban(guild_id: int, user_id: int) -> None:
    guild_key = str(guild_id)
    guild_bans = bot.temporary_bans.get(guild_key, {})
    guild_bans.pop(str(user_id), None)

    if not guild_bans:
        bot.temporary_bans.pop(guild_key, None)

    save_runtime_json(TEMP_BANS_FILE, bot.temporary_bans)


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

    ban_case = create_case(
        member.guild.id,
        member.id,
        bot.user.id if bot.user else None,
        case_type="Bannissement temporaire",
        reason="10 avertissements atteints",
        warning_count=MAX_WARNINGS,
        channel_id=channel.id,
        deleted_content=(
            f"Discord : {discord_result} | "
            f"Roblox simulé : {linked_name}, {FAKE_ROBLOX_BAN_DAYS} jours"
        ),
    )
    await send_case_to_staff(member.guild, ban_case)

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
        if member_has_moderator_role(message.author):
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

    if await process_moderator_answer(message):
        return

    config = bot.get_guild_config(message.guild.id)
    questions_channel_id = config.get("questions_channel_id")

    if (
        questions_channel_id
        and message.channel.id == int(questions_channel_id)
        and isinstance(message.author, discord.Member)
        and not member_has_moderator_role(message.author)
    ):
        await register_question(message)
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
    salon_anciennete="Salon du classement d'ancienneté",
    salon_questions="Salon où les membres posent leurs questions",
    role_moderateur="Rôle autorisé à répondre aux questions",
    vocal_attente="Vocal où René joue ATTENTE.mp3 en boucle",
)
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def config_command(
    interaction: discord.Interaction,
    salon_bienvenue: discord.TextChannel | None = None,
    salon_annonces: discord.TextChannel | None = None,
    salon_dossiers: discord.TextChannel | None = None,
    salon_anciennete: discord.TextChannel | None = None,
    salon_questions: discord.TextChannel | None = None,
    role_moderateur: discord.Role | None = None,
    vocal_attente: discord.VoiceChannel | None = None,
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
    if salon_anciennete is not None:
        config["seniority_channel_id"] = salon_anciennete.id
        config["seniority_message_id"] = None
    if salon_questions is not None:
        config["questions_channel_id"] = salon_questions.id
    if role_moderateur is not None:
        config["moderator_role_id"] = role_moderateur.id
    if vocal_attente is not None:
        config["waiting_voice_channel_id"] = vocal_attente.id

    await save_state_immediately()

    if salon_anciennete is not None:
        await update_seniority_board(interaction.guild)
    if vocal_attente is not None:
        await ensure_waiting_voice_task(interaction.guild)

    await asyncio.sleep(0.6)

    await finish_interaction(
        interaction,
        title="C'est noté !",
        description=(
            "La configuration de René est enregistrée.\n\n"
            f"👋 **Bienvenue :** "
            f"{f'<#{config.get('welcome_channel_id')}>' if config.get('welcome_channel_id') else 'non configuré'}\n"
            f"📢 **Annonces :** "
            f"{f'<#{config.get('announcement_channel_id')}>' if config.get('announcement_channel_id') else 'non configuré'}\n"
            f"📂 **Dossiers :** "
            f"{f'<#{config.get('staff_records_channel_id')}>' if config.get('staff_records_channel_id') else 'non configuré'}\n"
            f"🏆 **Ancienneté :** "
            f"{f'<#{config.get('seniority_channel_id')}>' if config.get('seniority_channel_id') else 'non configuré'}\n"
            f"❓ **Questions :** "
            f"{f'<#{config.get('questions_channel_id')}>' if config.get('questions_channel_id') else 'non configuré'}\n"
            f"🛡️ **Rôle modérateur :** "
            f"{f'<@&{config.get('moderator_role_id')}>' if config.get('moderator_role_id') else 'non configuré'}\n"
            f"🔊 **Vocal d'attente :** "
            f"{f'<#{config.get('waiting_voice_channel_id')}>' if config.get('waiting_voice_channel_id') else 'non configuré'}"
        ),
    )


@bot.tree.command(
    name="moddefinir",
    description="Définir le rôle des modérateurs de René.",
)
@app_commands.describe(role="Rôle autorisé à répondre aux questions")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def mod_define_command(
    interaction: discord.Interaction,
    role: discord.Role,
) -> None:
    assert interaction.guild is not None

    await begin_interaction_thinking(interaction)
    config = bot.get_guild_config(interaction.guild.id)
    config["moderator_role_id"] = role.id
    await save_state_immediately()

    await finish_interaction(
        interaction,
        title="C'est noté !",
        description=f"Le rôle {role.mention} est maintenant reconnu comme modérateur.",
    )


@bot.tree.command(
    name="salonquestions",
    description="Définir le salon dans lequel les membres posent leurs questions.",
)
@app_commands.describe(salon="Salon réservé aux questions")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def questions_channel_command(
    interaction: discord.Interaction,
    salon: discord.TextChannel,
) -> None:
    assert interaction.guild is not None

    await begin_interaction_thinking(interaction)
    config = bot.get_guild_config(interaction.guild.id)
    config["questions_channel_id"] = salon.id
    await save_state_immediately()

    await finish_interaction(
        interaction,
        title="C'est noté !",
        description=(
            f"Les questions des membres seront maintenant prises en charge dans "
            f"{salon.mention}."
        ),
    )


@bot.tree.command(
    name="vocalattente",
    description="Définir le vocal d'attente urgente.",
)
@app_commands.describe(salon="Vocal dans lequel ATTENTE.mp3 sera joué en boucle")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def waiting_voice_command(
    interaction: discord.Interaction,
    salon: discord.VoiceChannel,
) -> None:
    assert interaction.guild is not None

    await begin_interaction_thinking(interaction)
    config = bot.get_guild_config(interaction.guild.id)
    config["waiting_voice_channel_id"] = salon.id
    await save_state_immediately()
    await ensure_waiting_voice_task(interaction.guild)

    await finish_interaction(
        interaction,
        title="C'est noté !",
        description=(
            f"Le vocal d'attente est maintenant {salon.mention}.\n\n"
            "Quand un membre y entre, René rejoint le vocal, joue "
            "`ATTENTE.mp3` en boucle et prévient les modérateurs en MP "
            "à chaque redémarrage du son."
        ),
    )


@bot.tree.command(
    name="diagnosticvocal",
    description="Vérifier toute la configuration du vocal d'attente.",
)
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def diagnostic_voice_command(interaction: discord.Interaction) -> None:
    assert interaction.guild is not None

    await begin_interaction_thinking(interaction)
    config = bot.get_guild_config(interaction.guild.id)
    channel_id = config.get("waiting_voice_channel_id")
    channel = (
        interaction.guild.get_channel(int(channel_id))
        if channel_id
        else None
    )

    try:
        discord_version = importlib_metadata.version("discord.py")
    except importlib_metadata.PackageNotFoundError:
        discord_version = "introuvable"
    try:
        davey_version = importlib_metadata.version("davey")
    except importlib_metadata.PackageNotFoundError:
        davey_version = "NON INSTALLÉ"

    audio_ok = AUDIO_WAITING.is_file()
    ffmpeg_ok = False
    ffmpeg_text = "introuvable"
    try:
        ffmpeg_text = get_ffmpeg_executable()
        ffmpeg_ok = Path(ffmpeg_text).is_file()
    except Exception as error:
        ffmpeg_text = str(error)

    if isinstance(channel, discord.VoiceChannel) and interaction.guild.me:
        perms = channel.permissions_for(interaction.guild.me)
        permission_text = (
            f"Voir : {'✅' if perms.view_channel else '❌'} • "
            f"Connexion : {'✅' if perms.connect else '❌'} • "
            f"Parler : {'✅' if perms.speak else '❌'}"
        )
        humans = waiting_members(channel)
        channel_text = f"{channel.mention} (`{channel.id}`)"
    else:
        permission_text = "Salon non configuré ou introuvable."
        humans = []
        channel_text = "non configuré"

    voice_client = interaction.guild.voice_client
    voice_text = (
        f"connecté dans {voice_client.channel.mention}"
        if voice_client and voice_client.is_connected() and voice_client.channel
        else "déconnecté"
    )
    task = bot.waiting_voice_tasks.get(interaction.guild.id)
    task_text = "active" if task and not task.done() else "inactive"

    await finish_interaction(
        interaction,
        title="Diagnostic vocal terminé",
        description=(
            f"🎙️ **Salon :** {channel_text}\n"
            f"🔐 **Permissions :** {permission_text}\n"
            f"👥 **Humains présents :** {len(humans)}\n"
            f"🎵 **ATTENTE.mp3 :** {'✅' if audio_ok else '❌'}\n"
            f"🧰 **FFmpeg :** {'✅' if ffmpeg_ok else '⚠️'} `{ffmpeg_text}`\n"
            f"📦 **discord.py :** `{discord_version}`\n"
            f"🔒 **DAVE/davey :** `{davey_version}`\n"
            f"🔊 **VoiceClient :** {voice_text}\n"
            f"🔁 **Boucle :** {task_text}"
        ),
        color=COLOR_SUCCESS if audio_ok and davey_version != "NON INSTALLÉ" else COLOR_WARNING,
        image_path=IMAGE_INSPECT,
    )


@bot.tree.command(
    name="anciennete",
    description="Actualiser manuellement le classement d'ancienneté.",
)
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def seniority_command(interaction: discord.Interaction) -> None:
    assert interaction.guild is not None

    await begin_interaction_thinking(interaction)
    await update_seniority_board(interaction.guild)
    await finish_interaction(
        interaction,
        title="C'est noté !",
        description="Le classement d'ancienneté a été actualisé.",
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
    save_runtime_json(ROBLOX_LINKS_FILE, bot.roblox_links)

    await finish_interaction(
        interaction,
        title="C'est noté !",
        description=(
            f"Ton compte est lié au pseudo Roblox **{pseudo}**.\n"
            "Ce lien sert seulement aux sanctions Roblox simulées."
        ),
    )


# ============================================================
# /WARN MANUEL
# ============================================================

@bot.tree.command(
    name="warn",
    description="Donner manuellement un avertissement à un membre.",
)
@app_commands.describe(
    membre="Membre à avertir",
    raison="Motif de l'avertissement",
)
@app_commands.default_permissions(manage_messages=True)
@app_commands.guild_only()
async def manual_warn_command(
    interaction: discord.Interaction,
    membre: discord.Member,
    raison: str,
) -> None:
    assert interaction.guild is not None

    await begin_interaction_thinking(interaction)
    if membre.bot:
        await finish_interaction(
            interaction,
            title="Action impossible",
            description="René ne peut pas avertir un bot.",
            color=COLOR_DANGER,
            image_path=IMAGE_CRY,
        )
        return

    channel = interaction.channel
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        await finish_interaction(
            interaction,
            title="Salon incompatible",
            description="Utilise cette commande dans un salon textuel.",
            color=COLOR_DANGER,
            image_path=IMAGE_CRY,
        )
        return

    count = await add_warning(
        membre,
        channel,
        reason=f"Avertissement manuel par {interaction.user}: {raison[:500]}",
        deleted_content="Aucun message supprimé — sanction manuelle.",
        public_image=IMAGE_CRY,
    )
    if count >= MAX_WARNINGS:
        await punish_member(membre, channel)

    await finish_interaction(
        interaction,
        title="C'est noté !",
        description=(
            f"{membre.mention} possède maintenant **{count}/{MAX_WARNINGS} "
            f"avertissements**.\nMotif : {raison[:500]}"
        ),
        color=COLOR_WARNING,
        image_path=IMAGE_NOTED,
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
    await persist_state_to_discord()
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
    logger.error("Erreur de commande slash : %s", error)

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
    if not DISCORD_TOKEN:
        raise RuntimeError(
            "La variable d'environnement DISCORD_TOKEN est absente. "
            "Ajoute-la dans Render > Environment."
        )

    logger.info("Démarrage du serveur web Render...")
    web_thread = threading.Thread(
        target=run_web_server,
        name="rene-web-server",
        daemon=True,
    )
    web_thread.start()

    logger.info("Connexion de René à Discord...")
    try:
        bot.run(DISCORD_TOKEN.strip(), log_handler=None)
    except Exception:
        logger.exception("René s'est arrêté à cause d'une erreur fatale.")
        raise

    # Sur Render, quitter le processus relancerait automatiquement le bot.
    # Après /stop, le serveur web reste donc actif mais René demeure hors ligne.
    if bot.stopping:
        logger.info(
            "René dort volontairement. Redémarre le service Render pour le réveiller."
        )
        while True:
            time.sleep(3600)