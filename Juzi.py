import asyncio
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message
import re
from pymongo import MongoClient
from config import API_HASH, API_ID, BOT_TOKEN, MONGO_URI, START_PIC, START_MSG, HELP_TXT, OWNER_ID

mongo_client = MongoClient(MONGO_URI)
db = mongo_client["auto_caption_bot"]
channels_collection = db["channel_captions"]
users_collection = db["users"]
text_settings_collection = db["text_settings"]
button_collection = db["custom_buttons"]

app = Client("auto_caption_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Pre-compiled regex patterns for better performance
QUALITY_PATTERNS = [
    (re.compile(r'\b(4K|2160p|UHD)\b', re.IGNORECASE), "4K"),
    (re.compile(r'\b(1080p|FHD)\b', re.IGNORECASE), "1080p"),
    (re.compile(r'\b(720p|HD)\b', re.IGNORECASE), "720p"),
    (re.compile(r'\b(480p|SD)\b', re.IGNORECASE), "480p"),
    (re.compile(r'\b(360p|LD)\b', re.IGNORECASE), "360p")
]

LANGUAGE_PATTERNS = [
    (re.compile(r'\b(English|ENG|en)\b', re.IGNORECASE), "English"),
    (re.compile(r'\b(Hindi|HIN|hi)\b', re.IGNORECASE), "Hindi"),
    (re.compile(r'\b(Tamil|TAM|ta)\b', re.IGNORECASE), "Tamil"),
    (re.compile(r'\b(Telugu|TEL|te)\b', re.IGNORECASE), "Telugu"),
    (re.compile(r'\b(Malayalam|MAL|ml)\b', re.IGNORECASE), "Malayalam"),
    (re.compile(r'\b(Kannada|KAN|kn)\b', re.IGNORECASE), "Kannada"),
    (re.compile(r'\b(Bengali|BEN|bn)\b', re.IGNORECASE), "Bengali"),
    (re.compile(r'\b(Marathi|MAR|mr)\b', re.IGNORECASE), "Marathi"),
    (re.compile(r'\b(Gujarati|GUJ|gu)\b', re.IGNORECASE), "Gujarati"),
    (re.compile(r'\b(Punjabi|PUN|pa)\b', re.IGNORECASE), "Punjabi")
]

EPISODE_PATTERNS = [
    re.compile(r'\b(?:EP|E)\s*-\s*(\d{1,3})\b', re.IGNORECASE),
    re.compile(r'\b(?:EP|E)\s*(\d{1,3})\b', re.IGNORECASE),
    re.compile(r'S(\d+)(?:E|EP)(\d+)', re.IGNORECASE),
    re.compile(r'S(\d+)\s*(?:E|EP|-\s*EP)\s*(\d+)', re.IGNORECASE),
    re.compile(r'(?:[([<{]?\s*(?:E|EP)\s*(\d+)\s*[)\]>}]?)', re.IGNORECASE),
    re.compile(r'(?:EP|E)?\s*[-]?\s*(\d{1,3})', re.IGNORECASE),
    re.compile(r'S(\d+)[^\d]*(\d+)', re.IGNORECASE),
    re.compile(r'(\d+)')
]

SEASON_PATTERNS = [
    re.compile(r'S(\d+)(?:E|EP)', re.IGNORECASE),
    re.compile(r'Season\s*(\d+)', re.IGNORECASE),
    re.compile(r'S(\d+)\s', re.IGNORECASE)
]

# Custom button pattern
BUTTON_PATTERN = re.compile(r'\[(.*?)\]\[buttonurl:(.*?)\]')

class FileInfoExtractor:
    @staticmethod
    def extract_episode(filename):
        for pattern in EPISODE_PATTERNS:
            match = pattern.search(filename)
            if match:
                return int(match.groups()[-1])
        return None

    @staticmethod
    def extract_season(filename):
        for pattern in SEASON_PATTERNS:
            match = pattern.search(filename)
            if match:
                return int(match.group(1))
        return None

    @staticmethod
    def extract_quality(filename):
        for pattern, quality in QUALITY_PATTERNS:
            if pattern.search(filename):
                return quality
        return "HD"

    @staticmethod
    def extract_language(filename):
        for pattern, language in LANGUAGE_PATTERNS:
            if pattern.search(filename):
                return language
        return "Multi"

    @staticmethod
    def format_file_size(size_bytes):
        if not size_bytes:
            return "0 B"
        
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.2f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.2f} TB"

    @staticmethod
    def extract_all_info(filename, file_size):
        return {
            'filename': filename,
            'episode': FileInfoExtractor.extract_episode(filename),
            'season': FileInfoExtractor.extract_season(filename),
            'quality': FileInfoExtractor.extract_quality(filename),
            'language': FileInfoExtractor.extract_language(filename),
            'filesize': FileInfoExtractor.format_file_size(file_size)
        }

class TextSettingsManager:
    @staticmethod
    async def add_remove_text(chat_id, text_to_remove, user_id, username):
        text_settings_collection.update_one(
            {"chat_id": chat_id},
            {"$addToSet": {"remove_texts": text_to_remove},
             "$set": {"user_id": user_id, "username": username}},
            upsert=True
        )

    @staticmethod
    async def add_replace_text(chat_id, old_text, new_text, user_id, username):
        text_settings_collection.update_one(
            {"chat_id": chat_id},
            {"$set": {f"replace_texts.{old_text}": new_text,
                     "user_id": user_id, "username": username}},
            upsert=True
        )

    @staticmethod
    async def get_text_settings(chat_id):
        return text_settings_collection.find_one({"chat_id": chat_id})

    @staticmethod
    async def remove_text_setting(chat_id, text_type, text_value, user_id):
        settings = text_settings_collection.find_one({"chat_id": chat_id})
        if not settings or settings.get("user_id") != user_id:
            return False

        if text_type == "remove":
            result = text_settings_collection.update_one(
                {"chat_id": chat_id},
                {"$pull": {"remove_texts": text_value}}
            )
        elif text_type == "replace":
            result = text_settings_collection.update_one(
                {"chat_id": chat_id},
                {"$unset": {f"replace_texts.{text_value}": ""}}
            )
        
        return result.modified_count > 0

    @staticmethod
    async def clear_all_settings(chat_id, user_id):
        settings = text_settings_collection.find_one({"chat_id": chat_id})
        if settings and settings.get("user_id") == user_id:
            result = text_settings_collection.delete_one({"chat_id": chat_id})
            return result.deleted_count > 0
        return False

    @staticmethod
    def apply_text_settings(caption, settings):
        if not settings:
            return caption

        # Apply remove text
        if 'remove_texts' in settings:
            for text_to_remove in settings['remove_texts']:
                caption = caption.replace(text_to_remove, '')

        # Apply replace text
        if 'replace_texts' in settings:
            for old_text, new_text in settings['replace_texts'].items():
                caption = caption.replace(old_text, new_text)

        # Clean up extra spaces and newlines
        caption = re.sub(r'\n\s*\n', '\n\n', caption)  # Remove extra blank lines
        caption = caption.strip()
        
        return caption

class ButtonManager:
    @staticmethod
    def parse_buttons(button_text):
        """Parse button format: [Text][buttonurl:https://example.com]"""
        buttons = []
        matches = BUTTON_PATTERN.findall(button_text)
        
        for text, url in matches:
            if url.startswith(('http://', 'https://', 't.me/')):
                buttons.append([InlineKeyboardButton(text.strip(), url=url.strip())])
        
        return InlineKeyboardMarkup(buttons) if buttons else None

    @staticmethod
    async def set_custom_button(chat_id, button_text, user_id, username):
        button_collection.update_one(
            {"chat_id": chat_id},
            {"$set": {
                "button_text": button_text,
                "user_id": user_id,
                "username": username,
                "parsed_buttons": ButtonManager.parse_buttons(button_text) is not None
            }},
            upsert=True
        )

    @staticmethod
    async def get_custom_button(chat_id):
        return button_collection.find_one({"chat_id": chat_id})

    @staticmethod
    async def remove_custom_button(chat_id, user_id):
        button_data = button_collection.find_one({"chat_id": chat_id})
        if button_data and button_data.get("user_id") == user_id:
            result = button_collection.delete_one({"chat_id": chat_id})
            return result.deleted_count > 0
        return False

    @staticmethod
    async def clear_all_buttons(chat_id, user_id):
        button_data = button_collection.find_one({"chat_id": chat_id})
        if button_data and button_data.get("user_id") == user_id:
            result = button_collection.delete_one({"chat_id": chat_id})
            return result.deleted_count > 0
        return False

class CaptionManager:
    @staticmethod
    async def set_caption(chat_id, caption_text, chat_title, user_id, username):
        channels_collection.update_one(
            {"chat_id": chat_id},
            {"$set": {
                "caption": caption_text, 
                "chat_title": chat_title,
                "user_id": user_id,
                "username": username
            }},
            upsert=True
        )

    @staticmethod
    async def remove_caption(chat_id, user_id):
        caption_data = channels_collection.find_one({"chat_id": chat_id})
        if caption_data and caption_data.get("user_id") == user_id:
            result = channels_collection.delete_one({"chat_id": chat_id})
            return result.deleted_count > 0
        return False

    @staticmethod
    async def get_caption(chat_id):
        return channels_collection.find_one({"chat_id": chat_id})

    @staticmethod
    def format_caption(caption_template, file_info):
        """Format caption with all placeholders"""
        caption = caption_template
        placeholders = {
            '{filename}': file_info['filename'],
            '{episode}': str(file_info['episode']) if file_info['episode'] else 'N/A',
            '{season}': str(file_info['season']) if file_info['season'] else 'N/A',
            '{quality}': file_info['quality'],
            '{language}': file_info['language'],
            '{filesize}': file_info['filesize']
        }
        
        for placeholder, value in placeholders.items():
            caption = caption.replace(placeholder, value)
        
        return caption

def get_user_info(message):
    """Safely get user information from message"""
    if message.from_user:
        return message.from_user.id, message.from_user.first_name
    elif message.sender_chat:
        return message.sender_chat.id, message.sender_chat.title
    else:
        return None, "Unknown"

# Command handlers
@app.on_message(filters.command("start"))
async def start_command(client, message):
    user_id, username = get_user_info(message)
    
    if user_id:
        # Track user in database
        users_collection.update_one(
            {"user_id": user_id},
            {"$set": {
                "username": username,
                "user_mention": f"@{username}" if message.from_user else username
            }},
            upsert=True
        )
    
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("𝖠𝖻𝗈𝗎𝗍", callback_data='help'),
         InlineKeyboardButton("𝖳𝖾𝗑𝗍 𝖲𝖾𝗍𝗍𝗂𝗇𝗀𝗌", callback_data='text_settings')],
        [InlineKeyboardButton("𝖢𝗎𝗌𝗍𝗈𝗆 𝖡𝗎𝗍𝗍𝗈𝗇", callback_data='custom_button')],
        [InlineKeyboardButton("𝖢𝗅𝗈𝗌𝖾", callback_data='close')],
        [InlineKeyboardButton("𝖣𝖾𝗏𝖾𝗅𝗈𝗉𝖾𝗋", url='https://t.me/Team_Wine')]
    ])
    
    await client.send_photo(
        chat_id=message.chat.id,
        photo=START_PIC,
        caption=START_MSG.format(first=username),
        reply_markup=buttons,
    )

@app.on_message(filters.command("help"))
async def help_command(client, message):
    help_text = (
        "🤖 **𝖢𝗈𝗆𝗆𝖺𝗇𝖽 𝖬𝖺𝗇𝗎𝖺𝗅 𝖡𝗒 𝖳𝖾𝖺𝗆 𝖶𝗂𝗇𝖾**\n\n"
        "**𝖠𝗏𝖺𝗂𝗅𝖺𝖻𝗅𝖾 𝖢𝗈𝗆𝗆𝖺𝗇𝖽𝗌:**\n"
        "• `/start` - Start the bot\n"
        "• `/help` - Show this help message\n"
        "• `/setcaption` - Set auto-caption for this chat\n"
        "• `/removecaption` - Remove auto-caption from this chat\n"
        "• `/showcaption` - Show current caption\n"
        "• `/mycaptions` - Show all your captions\n"
        "• `/textsettings` - Manage text editing settings\n"
        "• `/custombutton` - Set custom inline buttons\n"
        "• `/stats` - Show bot statistics\n"
        "\n**Placeholders:**\n"
        "• `{filename}` - Original filename\n"
        "• `{episode}` - Episode number\n"
        "• `{season}` - Season number\n"
        "• `{language}` - Language\n"
        "• `{quality}` - Video quality\n"
        "• `{filesize}` - File size\n"
        "\n**Text Editing Features:**\n"
        "• Remove specific words/lines from captions\n"
        "• Replace words/phrases in captions\n"
        "• Add custom inline buttons to messages\n"
        "Use `/textsettings` or `/custombutton` to configure these features."
    )
    await message.reply(help_text)

@app.on_message(filters.command("textsettings"))
async def text_settings_command(client, message):
    user_id, username = get_user_info(message)
    
    if not user_id:
        await message.reply("❌ Could not identify user. Please try again.")
        return

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🧹 Remove Text", callback_data='remove_text'),
         InlineKeyboardButton("♻️ Replace Text", callback_data='replace_text')],
        [InlineKeyboardButton("📋 View Settings", callback_data='view_text_settings'),
         InlineKeyboardButton("🗑️ Clear All", callback_data='clear_text_settings')],
        [InlineKeyboardButton("📖 Guide", callback_data='text_guide'),
         InlineKeyboardButton("🔙 Back", callback_data='help')]
    ])
    
    await message.reply(
        "🔤 **Text Settings Menu**\n\n"
        "Customize how captions are processed before being applied:\n\n"
        "• 🧹 **Remove Text**: Delete specific words/lines\n"
        "• ♻️ **Replace Text**: Change words/phrases\n"
        "• 📋 **View Settings**: See current configurations\n"
        "• 🗑️ **Clear All**: Remove all text settings\n"
        "• 📖 **Guide**: Learn how to use these features",
        reply_markup=buttons
    )

@app.on_message(filters.command("custombutton"))
async def custom_button_command(client, message):
    user_id, username = get_user_info(message)
    
    if not user_id:
        await message.reply("❌ Could not identify user. Please try again.")
        return

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Set Button", callback_data='set_button'),
         InlineKeyboardButton("👀 View Button", callback_data='view_button')],
        [InlineKeyboardButton("🗑️ Remove Button", callback_data='remove_button'),
         InlineKeyboardButton("📖 Button Guide", callback_data='button_guide')],
        [InlineKeyboardButton("🔙 Back", callback_data='help')]
    ])
    
    await message.reply(
        "🔘 **Custom Button Menu**\n\n"
        "Add custom inline buttons to your messages:\n\n"
        "• ➕ **Set Button**: Add custom buttons to messages\n"
        "• 👀 **View Button**: See current button configuration\n"
        "• 🗑️ **Remove Button**: Remove custom buttons\n"
        "• 📖 **Button Guide**: Learn button format\n\n"
        "**Format:**\n"
        "`[Button Text][buttonurl:https://example.com]`",
        reply_markup=buttons
    )

@app.on_message(filters.command("setbutton"))
async def set_button_command(client, message):
    user_id, username = get_user_info(message)
    
    if not user_id:
        await message.reply("❌ Could not identify user. Please try again.")
        return
    
    if len(message.command) < 2:
        await message.reply(
            "**Usage:** `/setbutton [Button Text][buttonurl:https://example.com]`\n\n"
            "**Examples:**\n"
            "• Single button:\n"
            "`/setbutton [Join Channel][buttonurl:https://t.me/YourChannel]`\n\n"
            "• Multiple buttons (one per line):\n"
            "`/setbutton [Join Channel][buttonurl:https://t.me/Channel1]\n"
            "[Download][buttonurl:https://t.me/Channel2]`\n\n"
            "**Note:** URLs must start with http://, https://, or t.me/"
        )
        return
    
    button_text = message.text.split(" ", 1)[1]
    
    # Validate button format
    parsed_buttons = ButtonManager.parse_buttons(button_text)
    if not parsed_buttons:
        await message.reply(
            "❌ Invalid button format!\n\n"
            "**Correct Format:**\n"
            "`[Button Text][buttonurl:https://example.com]`\n\n"
            "**Examples:**\n"
            "• `[Join Now][buttonurl:https://t.me/YourChannel]`\n"
            "• `[Download][buttonurl:https://example.com/file]`"
        )
        return
    
    await ButtonManager.set_custom_button(message.chat.id, button_text, user_id, username)
    await message.reply("✅ Custom button set successfully!")

@app.on_message(filters.command("showbutton"))
async def show_button_command(client, message):
    user_id, username = get_user_info(message)
    
    if not user_id:
        await message.reply("❌ Could not identify user. Please try again.")
        return
    
    button_data = await ButtonManager.get_custom_button(message.chat.id)
    if not button_data:
        await message.reply("❌ No custom button set for this chat.")
        return
    
    button_owner = button_data.get("username", "Unknown")
    button_text = button_data.get("button_text", "")
    
    # Create preview with actual buttons
    parsed_buttons = ButtonManager.parse_buttons(button_text)
    
    preview_text = (
        f"🔘 **Current Custom Button:**\n\n"
        f"`{button_text}`\n\n"
        f"👤 **Set by:** {button_owner}\n\n"
        f"**Preview:**"
    )
    
    await message.reply(preview_text, reply_markup=parsed_buttons)

@app.on_message(filters.command("removebutton"))
async def remove_button_command(client, message):
    user_id, username = get_user_info(message)
    
    if not user_id:
        await message.reply("❌ Could not identify user. Please try again.")
        return
    
    success = await ButtonManager.remove_custom_button(message.chat.id, user_id)
    if success:
        await message.reply("✅ Custom button removed successfully!")
    else:
        await message.reply("❌ No custom button found or you don't have permission to remove it!")

@app.on_message(filters.command("setcaption") & (filters.channel | filters.group | filters.private))
async def set_caption_command(client, message):
    user_id, username = get_user_info(message)
    
    if not user_id:
        await message.reply("❌ Could not identify user. Please try again.")
        return
    
    if len(message.command) < 2:
        help_text = (
            "**Usage:** `/setcaption Your caption text`\n\n"
            "**Available Placeholders:**\n"
            "• `{filename}` - Original filename\n"
            "• `{episode}` - Extracted episode number\n"
            "• `{season}` - Extracted season number\n"
            "• `{language}` - Detected language\n"
            "• `{quality}` - Video quality\n"
            "• `{filesize}` - Formatted file size\n\n"
            "**Example:**\n"
            "`/setcaption 🎬 {filename}\\n📺 Episode: {episode}\\n🎥 {quality} | {language} | {filesize}`"
        )
        await message.reply(help_text)
        return
    
    caption_text = message.text.split(" ", 1)[1]
    chat_title = message.chat.title if hasattr(message.chat, 'title') else "Private Chat"
    
    await CaptionManager.set_caption(
        message.chat.id, 
        caption_text, 
        chat_title,
        user_id,
        username
    )
    await message.reply("✅ Auto-caption set successfully!")

@app.on_message(filters.command("removecaption") & (filters.channel | filters.group | filters.private))
async def remove_caption_command(client, message):
    user_id, username = get_user_info(message)
    
    if not user_id:
        await message.reply("❌ Could not identify user. Please try again.")
        return
    
    success = await CaptionManager.remove_caption(message.chat.id, user_id)
    if success:
        await message.reply("✅ Auto-caption removed successfully!")
    else:
        await message.reply("❌ No caption found or you don't have permission to remove it!")

@app.on_message(filters.command("showcaption") & (filters.channel | filters.group | filters.private))
async def show_caption_command(client, message):
    caption_data = await CaptionManager.get_caption(message.chat.id)
    if caption_data:
        caption_owner = caption_data.get("username", "Unknown")
        await message.reply(
            f"**Caption for {caption_data['chat_title']}:**\n\n"
            f"`{caption_data['caption']}`\n\n"
            f"👤 **Set by:** {caption_owner}"
        )
    else:
        await message.reply("❌ No caption set for this chat!")

@app.on_message(filters.command("mycaptions"))
async def my_captions_command(client, message):
    user_id, username = get_user_info(message)
    
    if not user_id:
        await message.reply("❌ Could not identify user. Please try again.")
        return
    
    user_captions = channels_collection.find({"user_id": user_id})
    
    captions_list = "**📝 Your Auto-Captions:**\n\n"
    count = 0
    
    for caption in user_captions:
        count += 1
        chat_title = caption.get("chat_title", "Unknown Chat")
        caption_preview = caption["caption"][:50] + "..." if len(caption["caption"]) > 50 else caption["caption"]
        captions_list += f"**{count}. {chat_title}**\n`{caption_preview}`\n\n"
    
    if count == 0:
        captions_list = "❌ You haven't set any auto-captions yet!\nUse `/setcaption` to create one."
    
    await message.reply(captions_list)

# Text editing commands
@app.on_message(filters.command("removetext"))
async def remove_text_command(client, message):
    user_id, username = get_user_info(message)
    
    if not user_id:
        await message.reply("❌ Could not identify user. Please try again.")
        return
    
    if len(message.command) < 2:
        await message.reply(
            "**Usage:** `/removetext text_to_remove`\n\n"
            "**Example:**\n"
            "`/removetext Telegram` - Removes the word 'Telegram' from all captions\n"
            "`/removetext http://example.com` - Removes a specific URL"
        )
        return
    
    text_to_remove = message.text.split(" ", 1)[1]
    await TextSettingsManager.add_remove_text(message.chat.id, text_to_remove, user_id, username)
    await message.reply(f"✅ Text `{text_to_remove}` will be removed from all captions!")

@app.on_message(filters.command("replacetext"))
async def replace_text_command(client, message):
    user_id, username = get_user_info(message)
    
    if not user_id:
        await message.reply("❌ Could not identify user. Please try again.")
        return
    
    if len(message.command) < 3:
        await message.reply(
            "**Usage:** `/replacetext old_text new_text`\n\n"
            "**Example:**\n"
            "`/replacetext Telegram WhatsApp` - Replaces 'Telegram' with 'WhatsApp'\n"
            "`/replacetext HD 1080p` - Replaces 'HD' with '1080p'"
        )
        return
    
    parts = message.text.split(" ", 2)
    if len(parts) < 3:
        await message.reply("❌ Please provide both old text and new text.")
        return
    
    old_text = parts[1]
    new_text = parts[2]
    
    await TextSettingsManager.add_replace_text(message.chat.id, old_text, new_text, user_id, username)
    await message.reply(f"✅ Text `{old_text}` will be replaced with `{new_text}` in all captions!")

@app.on_message(filters.command("showtextsettings"))
async def show_text_settings_command(client, message):
    user_id, username = get_user_info(message)
    
    if not user_id:
        await message.reply("❌ Could not identify user. Please try again.")
        return
    
    settings = await TextSettingsManager.get_text_settings(message.chat.id)
    
    if not settings:
        await message.reply("❌ No text settings configured for this chat.")
        return
    
    settings_text = "🔤 **Current Text Settings:**\n\n"
    
    if 'remove_texts' in settings and settings['remove_texts']:
        settings_text += "🧹 **Texts to Remove:**\n"
        for text in settings['remove_texts']:
            settings_text += f"• `{text}`\n"
        settings_text += "\n"
    
    if 'replace_texts' in settings and settings['replace_texts']:
        settings_text += "♻️ **Text Replacements:**\n"
        for old_text, new_text in settings['replace_texts'].items():
            settings_text += f"• `{old_text}` → `{new_text}`\n"
    
    await message.reply(settings_text)

@app.on_message(filters.command("cleartextsettings"))
async def clear_text_settings_command(client, message):
    user_id, username = get_user_info(message)
    
    if not user_id:
        await message.reply("❌ Could not identify user. Please try again.")
        return
    
    success = await TextSettingsManager.clear_all_settings(message.chat.id, user_id)
    if success:
        await message.reply("✅ All text settings cleared successfully!")
    else:
        await message.reply("❌ No text settings found or you don't have permission to clear them!")

@app.on_message(filters.command("broadcast") & filters.user(OWNER_ID))
async def broadcast_command(client, message):
    if len(message.command) < 2:
        await message.reply("**Usage:** `/broadcast Your message here`")
        return

    broadcast_text = message.text.split(" ", 1)[1]
    users = users_collection.find({}, {"user_id": 1})
    
    count = 0
    for user in users:
        try:
            await client.send_message(user["user_id"], broadcast_text)
            count += 1
            await asyncio.sleep(0.1)  # Prevent flooding
        except:
            continue
    
    await message.reply(f"✅ Broadcast sent to {count} users.")

@app.on_message(filters.command("users") & filters.user(OWNER_ID))
async def users_command(client, message):
    user_count = users_collection.count_documents({})
    await message.reply(f"📊 **Total Users:** {user_count}")

@app.on_message(filters.command("stats"))
async def stats_command(client, message):
    total_users = users_collection.count_documents({})
    total_captions = channels_collection.count_documents({})
    total_text_settings = text_settings_collection.count_documents({})
    total_buttons = button_collection.count_documents({})
    
    stats_text = (
        "📊 **Bot Statistics**\n\n"
        f"👥 **Total Users:** {total_users}\n"
        f"📝 **Active Captions:** {total_captions}\n"
        f"🔤 **Text Settings:** {total_text_settings}\n"
        f"🔘 **Custom Buttons:** {total_buttons}\n"
        f"⚡ **Bot Status:** Online\n"
        f"🤖 **Version:** v0.1"
    )
    await message.reply(stats_text)

# Auto-caption handler with text settings and custom buttons
@app.on_message(filters.channel & (filters.document | filters.video | filters.audio))
async def auto_caption_handler(client, message):
    try:
        caption_data = await CaptionManager.get_caption(message.chat.id)
        if not caption_data:
            return
        
        # Extract file information
        file_name = (
            message.document.file_name if message.document else
            message.video.file_name if message.video else
            message.audio.file_name if message.audio else
            "Unknown"
        )
        
        file_size = (
            message.document.file_size if message.document else
            message.video.file_size if message.video else
            message.audio.file_size if message.audio else
            0
        )
        
        # Extract all file info
        file_info = FileInfoExtractor.extract_all_info(file_name, file_size)
        
        # Format caption
        formatted_caption = CaptionManager.format_caption(caption_data["caption"], file_info)
        
        # Apply text settings (remove/replace)
        text_settings = await TextSettingsManager.get_text_settings(message.chat.id)
        if text_settings:
            formatted_caption = TextSettingsManager.apply_text_settings(formatted_caption, text_settings)
        
        # Get custom buttons
        button_data = await ButtonManager.get_custom_button(message.chat.id)
        reply_markup = None
        if button_data:
            reply_markup = ButtonManager.parse_buttons(button_data.get("button_text", ""))
        
        # Apply caption to the message
        await client.edit_message_caption(
            chat_id=message.chat.id,
            message_id=message.id,
            caption=formatted_caption,
            reply_markup=reply_markup
        )
        
    except Exception as e:
        print(f"Auto-caption error: {e}")

# Callback query handler
@app.on_callback_query()
async def callback_handler(client: app, query: CallbackQuery):
    data = query.data
    
    if data == "help":
        await query.message.edit_text(
            text=HELP_TXT.format(first=query.from_user.first_name),
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton('Text Settings', callback_data='text_settings'),
                 InlineKeyboardButton('Custom Button', callback_data='custom_button')],
                [InlineKeyboardButton("Close", callback_data='close')]
            ])
        )
    
    elif data == "start":
        await query.message.edit_text(
            text=START_MSG.format(first=query.from_user.first_name),
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Help", callback_data='help'),
                 InlineKeyboardButton("Text Settings", callback_data='text_settings')],
                [InlineKeyboardButton("Custom Button", callback_data='custom_button')],
                [InlineKeyboardButton("Close", callback_data='close')],
                [InlineKeyboardButton("OWNER", url='https://t.me/Team_Wine')]
            ])
        )
    
    elif data == "text_settings":
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("🧹 Remove Text", callback_data='remove_text'),
             InlineKeyboardButton("♻️ Replace Text", callback_data='replace_text')],
            [InlineKeyboardButton("📋 View Settings", callback_data='view_text_settings'),
             InlineKeyboardButton("🗑️ Clear All", callback_data='clear_text_settings')],
            [InlineKeyboardButton("📖 Guide", callback_data='text_guide'),
             InlineKeyboardButton("🔙 Back", callback_data='help')]
        ])
        
        await query.message.edit_text(
            "🔤 **Text Settings Menu**\n\n"
            "Customize how captions are processed before being applied:\n\n"
            "• 🧹 **Remove Text**: Delete specific words/lines\n"
            "• ♻️ **Replace Text**: Change words/phrases\n"
            "• 📋 **View Settings**: See current configurations\n"
            "• 🗑️ **Clear All**: Remove all text settings\n"
            "• 📖 **Guide**: Learn how to use these features",
            reply_markup=buttons
        )
    
    elif data == "custom_button":
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Set Button", callback_data='set_button'),
             InlineKeyboardButton("👀 View Button", callback_data='view_button')],
            [InlineKeyboardButton("🗑️ Remove Button", callback_data='remove_button'),
             InlineKeyboardButton("📖 Button Guide", callback_data='button_guide')],
            [InlineKeyboardButton("🔙 Back", callback_data='help')]
        ])
        
        await query.message.edit_text(
            "🔘 **Custom Button Menu**\n\n"
            "Add custom inline buttons to your messages:\n\n"
            "• ➕ **Set Button**: Add custom buttons to messages\n"
            "• 👀 **View Button**: See current button configuration\n"
            "• 🗑️ **Remove Button**: Remove custom buttons\n"
            "• 📖 **Button Guide**: Learn button format\n\n"
            "**Format:**\n"
            "`[Button Text][buttonurl:https://example.com]`",
            reply_markup=buttons
        )
    
    elif data == "text_guide":
        guide_text = (
            "🔤 **Text Settings Guide**\n\n"
            "With these options, you can fully customize the message text.\n"
            "Here's what each button does:\n\n"
            "• 🧹 **Remove Text**: Delete any word or line from the original caption/text.\n"
            "   ➤ Example: Remove the word 'Telegram' from the message.\n\n"
            "• ♻️ **Replace Text**: Change specific words or phrases in the message.\n"
            "   ➤ Example: Replace 'Telegram' with 'WhatsApp'.\n\n"
            "**Usage Commands:**\n"
            "• `/removetext word` - Remove specific text\n"
            "• `/replacetext old new` - Replace text\n"
            "• `/showtextsettings` - View current settings\n"
            "• `/cleartextsettings` - Clear all settings\n\n"
            "Use these features to clean, edit or enhance captions, descriptions, or messages easily."
        )
        
        await query.message.edit_text(
            guide_text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data='text_settings')]
            ])
        )
    
    elif data == "button_guide":
        guide_text = (
            "🔘 **Custom Button Guide**\n\n"
            "You Can Set A Inline Button To Messages.\n\n"
            "**Format:**\n"
            "`[Button Text][buttonurl:https://example.com]`\n\n"
            "**Examples:**\n"
            "• Single button:\n"
            "`[Rkn Developer][buttonurl:https://t.me/RknDeveloper]`\n\n"
            "• Multiple buttons (one per line):\n"
            "`[Join Channel][buttonurl:https://t.me/Channel1]\n"
            "[Download][buttonurl:https://t.me/Channel2]\n"
            "[Support][buttonurl:https://t.me/Channel3]`\n\n"
            "**Note:**\n"
            "• URLs must start with http://, https://, or t.me/\n"
            "• Each button should be on a new line for multiple buttons\n"
            "• Buttons will appear in the order you specify"
        )
        
        await query.message.edit_text(
            guide_text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data='custom_button')]
            ])
        )
    
    elif data == "set_button":
        await query.message.edit_text(
            "➕ **Set Custom Button**\n\n"
            "**Format:**\n"
            "`[Button Text][buttonurl:https://example.com]`\n\n"
            "**Examples:**\n"
            "• Single button:\n"
            "`[Join Channel][buttonurl:https://t.me/YourChannel]`\n\n"
            "• Multiple buttons:\n"
            "`[Channel][buttonurl:https://t.me/Channel1]\n"
            "[Group][buttonurl:https://t.me/Group1]\n"
            "[Download][buttonurl:https://example.com]`\n\n"
            "**Usage:**\n"
            "You can use the command:\n"
            "`/setbutton [Text][buttonurl:URL]`\n\n"
            "Or send the button format directly after clicking 'Set Button'",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data='custom_button')]
            ])
        )
    
    elif data == "view_button":
        user_id = query.from_user.id
        button_data = await ButtonManager.get_custom_button(query.message.chat.id)
        
        if not button_data:
            await query.answer("No custom button set for this chat.", show_alert=True)
            return
        
        button_owner = button_data.get("username", "Unknown")
        button_text = button_data.get("button_text", "")
        
        # Create preview with actual buttons
        parsed_buttons = ButtonManager.parse_buttons(button_text)
        
        preview_text = (
            f"🔘 **Current Custom Button:**\n\n"
            f"`{button_text}`\n\n"
            f"👤 **Set by:** {button_owner}\n\n"
            f"**Preview:**"
        )
        
        await query.message.edit_text(
            preview_text,
            reply_markup=parsed_buttons or InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data='custom_button')]
            ])
        )
    
    elif data == "remove_button":
        user_id = query.from_user.id
        success = await ButtonManager.remove_custom_button(query.message.chat.id, user_id)
        
        if success:
            await query.message.edit_text(
                "✅ Custom button removed successfully!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data='custom_button')]
                ])
            )
        else:
            await query.answer("No custom button found or you don't have permission to remove it!", show_alert=True)
    
    elif data == "remove_text":
        await query.message.edit_text(
            "🧹 **Remove Text**\n\n"
            "**Usage:** Send the text you want to remove from captions.\n\n"
            "**Examples:**\n"
            "• `Telegram` - Removes the word 'Telegram'\n"
            "• `http://example.com` - Removes a specific URL\n"
            "• `Download now` - Removes the phrase 'Download now'\n\n"
            "You can also use the command:\n"
            "`/removetext text_to_remove`",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data='text_settings')]
            ])
        )
    
    elif data == "replace_text":
        await query.message.edit_text(
            "♻️ **Replace Text**\n\n"
            "**Usage:** Send the text replacement in format:\n"
            "`old_text new_text`\n\n"
            "**Examples:**\n"
            "• `Telegram WhatsApp` - Replaces 'Telegram' with 'WhatsApp'\n"
            "• `HD 1080p` - Replaces 'HD' with '1080p'\n"
            "• `movie film` - Replaces 'movie' with 'film'\n\n"
            "You can also use the command:\n"
            "`/replacetext old_text new_text`",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data='text_settings')]
            ])
        )
    
    elif data == "view_text_settings":
        user_id = query.from_user.id
        settings = await TextSettingsManager.get_text_settings(query.message.chat.id)
        
        if not settings:
            await query.answer("No text settings configured for this chat.", show_alert=True)
            return
        
        settings_text = "🔤 **Current Text Settings:**\n\n"
        
        if 'remove_texts' in settings and settings['remove_texts']:
            settings_text += "🧹 **Texts to Remove:**\n"
            for text in settings['remove_texts']:
                settings_text += f"• `{text}`\n"
            settings_text += "\n"
        
        if 'replace_texts' in settings and settings['replace_texts']:
            settings_text += "♻️ **Text Replacements:**\n"
            for old_text, new_text in settings['replace_texts'].items():
                settings_text += f"• `{old_text}` → `{new_text}`\n"
        
        await query.message.edit_text(
            settings_text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data='text_settings')]
            ])
        )
    
    elif data == "clear_text_settings":
        user_id = query.from_user.id
        success = await TextSettingsManager.clear_all_settings(query.message.chat.id, user_id)
        
        if success:
            await query.message.edit_text(
                "✅ All text settings cleared successfully!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data='text_settings')]
                ])
            )
        else:
            await query.answer("No text settings found or you don't have permission to clear them!", show_alert=True)
    
    elif data == "close":
        await query.message.delete()
        try:
            await query.message.reply_to_message.delete()
        except:
            pass

print("🤖 Auto Caption Bot with Custom Buttons Started Successfully!")
app.run()


