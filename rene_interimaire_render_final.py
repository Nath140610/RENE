from __future__ import annotations

import asyncio
import json
import logging
import os
import random
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
DEPARTED_MEMBERS_FILE = BASE_DIR / "departed_members.json"
QUESTIONS_FILE = BASE_DIR / "questions.json"

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
STATUS_CHANNEL_ID = 1373577090213085204

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
        self.departed_members = load_json(DEPARTED_MEMBERS_FILE, {})
        self.questions = load_json(QUESTIONS_FILE, {})
        self.idle_avatar_applied = False
        self.last_task_until: datetime | None = None
        self.presence_lock = asyncio.Lock()
        self.stopping = False

    async def setup_hook(self) -> None:
        synced_commands = await self.tree.sync()
        logger.info("%s commandes synchronisées.", len(synced_commands))

        if not temporary_ban_checker.is_running():
            temporary_ban_checker.start()
        if not funny_presence_loop.is_running():
            funny_presence_loop.start()
        if not seniority_refresh_loop.is_running():
            seniority_refresh_loop.start()

    async def close(self) -> None:
        if temporary_ban_checker.is_running():
            temporary_ban_checker.cancel()
        if funny_presence_loop.is_running():
            funny_presence_loop.cancel()
        if seniority_refresh_loop.is_running():
            seniority_refresh_loop.cancel()
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
        config.setdefault("seniority_message_id", None)
        config.setdefault("allowed_domains", sorted(DEFAULT_ALLOWED_DOMAINS))
        return config


bot = ReneBot()



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


@bot.tree.interaction_check
async def global_command_check(interaction: discord.Interaction) -> bool:
    command_name = interaction.command.name if interaction.command else "commande"
    await set_task_presence(command_name)
    return True


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

def format_duration_since(date: datetime | None) -> str:
    if date is None:
        return "date inconnue"

    now = datetime.now(timezone.utc)
    delta = max(now - date, timedelta())
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
            duration = format_duration_since(joined)
        except (ValueError, TypeError):
            joined_ts = 0
            duration = "date inconnue"

        left_text = ""
        try:
            left = datetime.fromisoformat(str(record.get("left_at", "")))
            left_text = f" • parti <t:{int(left.timestamp())}:R>"
        except (ValueError, TypeError):
            pass

        departed_lines.append(
            f"{prefix} **{record.get('display_name', 'Ancien membre')}** — "
            f"avait rejoint <t:{joined_ts}:D> "
            f"(**{duration} depuis son arrivée**){left_text}"
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
        save_json(CONFIG_FILE, bot.config_data)
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
    return bool(
        role_id
        and any(role.id == int(role_id) for role in member.roles)
    ) or member.guild_permissions.manage_messages


async def register_question(message: discord.Message) -> None:
    if message.guild is None:
        return

    guild_questions = bot.questions.setdefault(str(message.guild.id), {})

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
        "confirmation_message_id": None,
    }

    guild_questions[str(message.id)] = question_data
    save_json(QUESTIONS_FILE, bot.questions)

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

    notification_message = await notify_channel.send(
        content=f"📨 {role_mention}, une nouvelle question attend une réponse.",
        embed=build_embed(
            "Question reçue",
            (
                f"👤 **Auteur :** {message.author.mention}\n"
                f"📍 **Question originale :** [ouvrir le message]({message.jump_url})\n\n"
                f"**Question :**\n{message.content}\n\n"
                "Pour répondre, utilisez la fonction **Répondre** sur :\n"
                "• le message original du membre ;\n"
                "• ou ce message de notification.\n\n"
                "René enverra automatiquement la réponse en MP."
            ),
            COLOR_INFO,
        ),
        allowed_mentions=discord.AllowedMentions(
            roles=True,
            users=False,
            everyone=False,
        ),
    )

    confirmation_message = await message.reply(
        embed=build_embed(
            "Question transmise",
            (
                "René a remis ta question aux modérateurs.\n"
                "Tu recevras leur réponse directement en message privé."
            ),
            COLOR_SUCCESS,
        ),
        mention_author=False,
    )

    question_data["staff_notification_message_id"] = notification_message.id
    question_data["confirmation_message_id"] = confirmation_message.id
    save_json(QUESTIONS_FILE, bot.questions)


def find_question_from_reference(
    guild_id: int,
    referenced_message_id: int,
) -> tuple[str | None, dict[str, Any] | None]:
    guild_questions = bot.questions.get(str(guild_id), {})

    # Réponse directe au message original.
    direct = guild_questions.get(str(referenced_message_id))
    if direct:
        return str(referenced_message_id), direct

    # Réponse au message de notification du staff ou au message de confirmation.
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

        await message.reply(
            (
                f"Désolé {message.author.mention}, "
                f"**{answered_name}** a déjà répondu à cette question."
            ),
            mention_author=False,
        )
        return True

    answer_text = message.content.strip()

    if not answer_text:
        await message.reply(
            "La réponse ne peut pas être vide.",
            mention_author=False,
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
                f"{answer_text}"
            ),
            color=COLOR_SUCCESS,
            image_path=IMAGE_READ,
        )

        delivery = "La réponse a bien été envoyée en MP."

    except discord.Forbidden:
        delivery = (
            "Je ne peux pas envoyer de MP à cette personne : "
            "ses messages privés sont probablement fermés."
        )

    except discord.NotFound:
        delivery = "Le compte de la personne n'a pas pu être retrouvé."

    except discord.HTTPException as error:
        logger.exception("Erreur pendant l'envoi de la réponse en MP.")
        delivery = f"Erreur Discord pendant l'envoi du MP : `{error}`"

    # La question est verrouillée dès qu'un modérateur a traité la réponse,
    # afin d'empêcher deux réponses simultanées.
    question["answered"] = True
    question["answered_by"] = message.author.id
    question["answer"] = answer_text[:1800]
    question["answered_at"] = datetime.now(timezone.utc).isoformat()
    question["delivery_result"] = delivery
    bot.questions[str(message.guild.id)][question_id] = question
    save_json(QUESTIONS_FILE, bot.questions)

    await message.reply(
        embed=build_embed(
            "Réponse enregistrée",
            f"✅ {delivery}",
            COLOR_SUCCESS,
        ),
        mention_author=False,
    )

    return True


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
    save_json(DEPARTED_MEMBERS_FILE, bot.departed_members)
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

    save_json(CONFIG_FILE, bot.config_data)

    if salon_anciennete is not None:
        await update_seniority_board(interaction.guild)

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
            f"{f'<@&{config.get('moderator_role_id')}>' if config.get('moderator_role_id') else 'non configuré'}"
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
    save_json(CONFIG_FILE, bot.config_data)

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
    save_json(CONFIG_FILE, bot.config_data)

    await finish_interaction(
        interaction,
        title="C'est noté !",
        description=(
            f"Les questions des membres seront maintenant prises en charge dans "
            f"{salon.mention}."
        ),
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
    if not DISCORD_TOKEN:
        raise RuntimeError(
            "La variable d'environnement DISCORD_TOKEN est absente. "
            "Ajoute-la dans Render > Environment."
        )

    web_thread = threading.Thread(
        target=run_web_server,
        name="rene-web-server",
        daemon=True,
    )
    web_thread.start()

    bot.run(DISCORD_TOKEN, log_handler=None)