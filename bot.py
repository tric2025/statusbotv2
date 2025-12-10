import json
import pathlib
import os

import discord
from discord import app_commands
from discord.ext import commands, tasks
from googletrans import Translator  # <-- ADDED

# ================== CONFIG ==================

TOKEN = os.getenv("TOKEN")  # Make sure TOKEN is set in your environment

# Path to config file (must exist in same folder or will be created)
CONFIG_PATH = pathlib.Path("config.json")


def load_config():
    """Load config.json or create default structure."""
    if not CONFIG_PATH.exists():
        return {"guilds": {}}
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("Error reading config.json, using empty config:", e)
        return {"guilds": {}}


def save_config(cfg):
    """Save config.json."""
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


config = load_config()

# ================== DISCORD SETUP ==================

intents = discord.Intents.default()
intents.guilds = True
intents.members = True      # needed for guild.get_member()
intents.presences = True    # needed for status (online / idle / dnd / offline)
intents.message_content = True  # <-- NEEDED for translation commands/on_message

bot = commands.Bot(command_prefix="!", intents=intents)  # prefix used for translator cmds

# ============ TRANSLATION BOT SETUP ============

translator = Translator()

# Store user language preferences in memory: {user_id: "en", ...}
user_languages: dict[int, str] = {}

# Store per-channel auto-translate settings: {channel_id: ["en", "it", "ar"]}
auto_channel_langs: dict[int, list[str]] = {}

SUPPORTED_LANGS = {
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "ru": "Russian",
    "ja": "Japanese",
    "ko": "Korean",
    "zh-cn": "Chinese (Simplified)",
    "zh-tw": "Chinese (Traditional)",
    "ar": "Arabic",
}

# ================== CONFIG HELPERS ==================

def get_guild_config(guild_id: int) -> dict:
    """
    Return config dict for one guild, creating it if missing.
    Structure:
    {
      "tracked_user_ids": [int, ...],
      "panel": {"channel_id": int, "message_id": int}  # optional
    }
    """
    gid = str(guild_id)
    if "guilds" not in config:
        config["guilds"] = {}
    if gid not in config["guilds"]:
        config["guilds"][gid] = {
            "tracked_user_ids": [],
            # "panel": {...} added later
        }
        save_config(config)
    return config["guilds"][gid]


def status_to_emoji_text(status: discord.Status | None) -> tuple[str, str]:
    """Convert discord.Status into (emoji, human text)."""
    if status is None:
        return "⚫", "Offline"

    if status is discord.Status.online:
        return "🟢", "Online"
    if status is discord.Status.idle:
        return "🌙", "Idle"
    if status is discord.Status.dnd:
        return "⛔", "Do Not Disturb"

    # includes offline / invisible
    return "⚫", "Offline"


def build_status_embed(guild: discord.Guild, guild_cfg: dict) -> discord.Embed:
    """Build the status embed for one guild."""
    tracked_ids = guild_cfg.get("tracked_user_ids", [])
    if not tracked_ids:
        description = "No tracked users yet. Use `/adduser` to add support members."
    else:
        lines: list[str] = []
        for uid in tracked_ids:
            member = guild.get_member(uid)
            if member is None:
                lines.append(f"❓ <@{uid}> – Not found in this server")
                continue

            emoji, text = status_to_emoji_text(member.status)
            lines.append(f"{emoji} {member.mention} – **{text}**")

        description = "\n".join(lines) if lines else "No valid tracked members found."

    embed = discord.Embed(
        title="Zexr Status – Support Team",
        description=description,
        colour=discord.Colour.blurple(),
    )
    embed.set_footer(text="Presence updates every 60 seconds.")
    embed.timestamp = discord.utils.utcnow()
    return embed


# ================== EVENTS ==================

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")

    # Sync slash commands globally
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands globally.")
    except Exception as e:
        print("Error syncing commands:", e)

    # Set bot activity text
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="who is ready for support",
        )
    )

    # Start background auto-update loop
    if not update_panels.is_running():
        update_panels.start()


# ================== TRANSLATOR PREFIX COMMANDS ==================

@bot.command(name="setlang")
async def set_language(ctx: commands.Context, lang_code: str):
    """
    Set your personal target language, e.g.:
    !setlang es
    !setlang en
    !setlang ar
    """
    lang_code = lang_code.lower()

    if lang_code not in SUPPORTED_LANGS:
        supported = ", ".join(f"{k} ({v})" for k, v in SUPPORTED_LANGS.items())
        await ctx.send(
            f"❌ Unknown language code `{lang_code}`.\n"
            f"Supported examples: {supported}"
        )
        return

    user_languages[ctx.author.id] = lang_code
    await ctx.send(
        f"✅ Your target language has been set to **{SUPPORTED_LANGS[lang_code]}** (`{lang_code}`)."
    )


@bot.command(name="mylang")
async def my_language(ctx: commands.Context):
    """
    Check your personal target language.
    """
    lang_code = user_languages.get(ctx.author.id)
    if not lang_code:
        await ctx.send("🛈 You have not set a language yet. Use `!setlang <code>`.")
    else:
        lang_name = SUPPORTED_LANGS.get(lang_code, lang_code)
        await ctx.send(f"🌍 Your current target language is **{lang_name}** (`{lang_code}`).")


@bot.command(name="translate", aliases=["tr"])
async def translate_text(ctx: commands.Context, *, text: str = None):
    """
    Translate text to the user's selected language.
    Usage:
    !translate Hello, how are you?
    !tr Bonjour tout le monde
    """
    if text is None:
        await ctx.send("❌ Please provide text to translate.\nExample: `!translate Hello world`")
        return

    target_lang = user_languages.get(ctx.author.id)
    if not target_lang:
        await ctx.send("🛈 Please set your language first with `!setlang <code>`.")
        return

    try:
        result = translator.translate(text, dest=target_lang)
        source_lang = result.src
        translated = result.text

        await ctx.send(
            f"**Original ({source_lang})**: {text}\n"
            f"**Translated ({target_lang})**: {translated}"
        )

    except Exception as e:
        await ctx.send(f"⚠️ Error while translating: `{e}`")


@bot.command(name="langs")
async def list_langs(ctx: commands.Context):
    """
    Show some example language codes.
    """
    lines = [f"`{code}` → {name}" for code, name in SUPPORTED_LANGS.items()]
    message = "🌐 Example language codes you can use with `!setlang` or channel settings:\n" + "\n".join(lines)
    await ctx.send(message)


# ================== CHANNEL AUTO-TRANSLATE (PREFIX COMMANDS) ==================

@bot.command(name="setchannellangs")
async def set_channel_langs(
    ctx: commands.Context,
    *lang_codes: str
):
    """
    Set auto-translate languages for the current channel.
    Usage:
    !setchannellangs en it ar
    """
    channel = ctx.channel

    if not lang_codes:
        await ctx.send(
            "❌ Please provide at least one language code.\n"
            "Example: `!setchannellangs en it ar`"
        )
        return

    lang_codes = [code.lower() for code in lang_codes]

    # Validate language codes
    invalid = [code for code in lang_codes if code not in SUPPORTED_LANGS]
    if invalid:
        invalid_str = ", ".join(invalid)
        supported = ", ".join(SUPPORTED_LANGS.keys())
        await ctx.send(
            f"❌ Invalid language code(s): {invalid_str}\n"
            f"Supported examples: {supported}"
        )
        return

    auto_channel_langs[channel.id] = list(lang_codes)
    pretty = ", ".join(f"{code} ({SUPPORTED_LANGS[code]})" for code in lang_codes)
    await ctx.send(
        f"✅ Auto-translate enabled in {channel.mention} for languages: {pretty}"
    )


@bot.command(name="channellangs")
async def channel_langs(ctx: commands.Context):
    """
    Show which auto-translate languages are set for this channel.
    Usage:
    !channellangs
    """
    channel = ctx.channel
    langs = auto_channel_langs.get(channel.id)
    if not langs:
        await ctx.send(f"🛈 No auto-translate languages set for {channel.mention}.")
        return

    pretty = ", ".join(f"{code} ({SUPPORTED_LANGS.get(code, code)})" for code in langs)
    await ctx.send(f"🌍 {channel.mention} auto-translates to: {pretty}")


@bot.command(name="clearchannellangs")
async def clear_channel_langs(ctx: commands.Context):
    """
    Disable auto-translate for the current channel.
    Usage:
    !clearchannellangs
    """
    channel = ctx.channel

    if channel.id in auto_channel_langs:
        del auto_channel_langs[channel.id]
        await ctx.send(f"✅ Auto-translate disabled for {channel.mention}.")
    else:
        await ctx.send(f"🛈 No auto-translate settings found for {channel.mention}.")


# ================== SLASH COMMANDS (YOUR STATUS SYSTEM) ==================

@bot.tree.command(
    name="adduser",
    description="Add a support member to the Zexr Status tracking list.",
)
@app_commands.describe(user="The user to track for support status.")
async def adduser(interaction: discord.Interaction, user: discord.User):
    """Add user to tracked list for this guild."""
    if not interaction.guild:
        await interaction.response.send_message(
            "This command can only be used in a server.",
            ephemeral=True,
        )
        return

    # Require Manage Server permission
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message(
            "You need the **Manage Server** permission to use this command.",
            ephemeral=True,
        )
        return

    guild_cfg = get_guild_config(interaction.guild.id)
    tracked = guild_cfg.get("tracked_user_ids", [])

    if user.id in tracked:
        await interaction.response.send_message(
            f"ℹ️ {user.mention} is already in the tracking list.",
            ephemeral=True,
        )
        return

    tracked.append(user.id)
    guild_cfg["tracked_user_ids"] = tracked
    save_config(config)

    await interaction.response.send_message(
        f"✅ Added {user.mention} to the Zexr Status tracking list.",
        ephemeral=True,
    )


@bot.tree.command(
    name="removeuser",
    description="Remove a support member from the Zexr Status tracking list.",
)
@app_commands.describe(user="The user to remove from tracking.")
async def removeuser(interaction: discord.Interaction, user: discord.User):
    """Remove user from tracked list for this guild."""
    if not interaction.guild:
        await interaction.response.send_message(
            "This command can only be used in a server.",
            ephemeral=True,
        )
        return

    # Require Manage Server permission
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message(
            "You need the **Manage Server** permission to use this command.",
            ephemeral=True,
        )
        return

    guild_cfg = get_guild_config(interaction.guild.id)
    tracked = guild_cfg.get("tracked_user_ids", [])

    if user.id not in tracked:
        await interaction.response.send_message(
            f"ℹ️ {user.mention} is not currently being tracked.",
            ephemeral=True,
        )
        return

    tracked.remove(user.id)
    guild_cfg["tracked_user_ids"] = tracked
    save_config(config)

    await interaction.response.send_message(
        f"🗑️ Removed {user.mention} from the tracking list.",
        ephemeral=True,
    )


@bot.tree.command(
    name="statuspanel",
    description="Post the Zexr Status support panel to a chosen channel.",
)
@app_commands.describe(channel="The channel where the status panel will be posted.")
async def statuspanel(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
):
    """Create / move the status panel message for this guild."""
    if not interaction.guild:
        await interaction.response.send_message(
            "This command can only be used in a server.",
            ephemeral=True,
        )
        return

    # Require Manage Server permission
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message(
            "You need the **Manage Server** permission to use this command.",
            ephemeral=True,
        )
        return

    # Ensure bot can actually send messages there
    permissions = channel.permissions_for(interaction.guild.me)
    if not permissions.send_messages:
        await interaction.response.send_message(
            f"I don't have permission to send messages in {channel.mention}.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)

    guild_cfg = get_guild_config(interaction.guild.id)

    # Build and send initial panel
    embed = build_status_embed(interaction.guild, guild_cfg)
    message = await channel.send(embed=embed)

    # Store where the panel is so the loop can edit it
    guild_cfg["panel"] = {
        "channel_id": channel.id,
        "message_id": message.id,
    }
    save_config(config)

    await interaction.followup.send(
        f"✅ Status panel created in {channel.mention}.\n"
        f"It will auto-update every **60 seconds**.",
        ephemeral=True,
    )


# ================== BACKGROUND TASK (STATUS PANELS) ==================

@tasks.loop(seconds=60)
async def update_panels():
    """Update all configured panels every 60 seconds."""
    for gid_str, guild_cfg in config.get("guilds", {}).items():
        panel_info = guild_cfg.get("panel")
        if not panel_info:
            continue

        guild_id = int(gid_str)
        guild = bot.get_guild(guild_id)
        if guild is None:
            continue

        channel_id = panel_info.get("channel_id")
        message_id = panel_info.get("message_id")
        if not channel_id or not message_id:
            continue

        channel = guild.get_channel(channel_id)
        if channel is None:
            continue

        try:
            message = await channel.fetch_message(message_id)
        except Exception:
            # Panel message missing (deleted?) – try to recreate once
            try:
                embed = build_status_embed(guild, guild_cfg)
                new_msg = await channel.send(embed=embed)
                guild_cfg["panel"]["message_id"] = new_msg.id
                save_config(config)
            except Exception:
                continue
        else:
            try:
                embed = build_status_embed(guild, guild_cfg)
                await message.edit(embed=embed)
            except Exception:
                continue


@update_panels.before_loop
async def before_update_panels():
    """Wait until the bot is ready before starting the loop."""
    await bot.wait_until_ready()


# ================== AUTO-TRANSLATE ON_MESSAGE ==================

@bot.event
async def on_message(message: discord.Message):
    # Ignore messages from the bot itself
    if message.author.bot:
        return

    # Let prefix commands work
    await bot.process_commands(message)

    channel = message.channel

    # If the channel is configured for auto-translate, translate every message
    if channel.id in auto_channel_langs:
        target_langs = auto_channel_langs[channel.id]
        text = message.content

        # Don't try to translate empty messages or only attachments
        if not text.strip():
            return

        try:
            # Detect original language once
            detection = translator.detect(text)
            src_lang = detection.lang

            translations = []
            for lang in target_langs:
                # Skip if same language as source
                if lang == src_lang:
                    continue

                result = translator.translate(text, src=src_lang, dest=lang)
                translations.append(
                    f"**{SUPPORTED_LANGS.get(lang, lang)} (`{lang}`)**: {result.text}"
                )

            if translations:
                # Reply in the same channel, referencing the original message
                response = (
                    f"💬 Auto-translation of message from "
                    f"**{SUPPORTED_LANGS.get(src_lang, src_lang)} (`{src_lang}`)**:\n"
                    + "\n".join(translations)
                )
                await channel.send(response, reference=message)

        except Exception as e:
            print(f"Error in auto-translate: {e}")


# ================== RUN BOT ==================

if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("❌ TOKEN environment variable is not set.")
    bot.run(TOKEN)
