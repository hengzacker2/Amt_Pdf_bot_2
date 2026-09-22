import os
import uuid
import shutil
import unicodedata
import logging
import asyncio
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
import uvicorn


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ============================================================
# LOAD ENVIRONMENT VARIABLES
# ============================================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

# Render automatically provides PORT for Web Services.
PORT = int(os.getenv("PORT", "10000"))

# Render public URL.
# This is only required when running in webhook mode.
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")

# Optional manual mode:
#
# BOT_MODE=polling
#     -> force local polling
#
# BOT_MODE=webhook
#     -> force webhook
#
# If BOT_MODE is not provided, the bot automatically decides:
#     RENDER_EXTERNAL_URL exists -> webhook
#     otherwise -> polling
BOT_MODE = os.getenv("BOT_MODE", "").lower().strip()


# ============================================================
# DETERMINE RUNNING MODE
# ============================================================

if BOT_MODE == "polling":
    USE_WEBHOOK = False

elif BOT_MODE == "webhook":
    USE_WEBHOOK = True

else:
    # Automatic mode
    USE_WEBHOOK = bool(RENDER_EXTERNAL_URL)


# ============================================================
# WEBHOOK SECRET
# ============================================================

WEBHOOK_SECRET = os.getenv(
    "WEBHOOK_SECRET",
    uuid.uuid4().hex,
)


# ============================================================
# VALIDATE ENVIRONMENT
# ============================================================

if not BOT_TOKEN:
    raise ValueError(
        "BOT_TOKEN is not set. "
        "Please add BOT_TOKEN to your .env file."
    )


if USE_WEBHOOK and not RENDER_EXTERNAL_URL:
    raise ValueError(
        "Webhook mode is enabled but RENDER_EXTERNAL_URL is not set."
    )


# ============================================================
# RENDER URL
# ============================================================

if RENDER_EXTERNAL_URL:
    RENDER_EXTERNAL_URL = RENDER_EXTERNAL_URL.rstrip("/")


# ============================================================
# WEBHOOK URL
# ============================================================

if USE_WEBHOOK:
    WEBHOOK_URL = (
        f"{RENDER_EXTERNAL_URL}"
        f"/telegram-webhook/"
        f"{WEBHOOK_SECRET}"
    )
else:
    WEBHOOK_URL = None


# ============================================================
# TEMPORARY STORAGE
# ============================================================

BASE_DIR = (
    Path(__file__)
    .resolve()
    .parent
    .parent
)

TEMP_DIR = BASE_DIR / "temp"

TEMP_DIR.mkdir(
    exist_ok=True
)


# ============================================================
# USER DATA
# ============================================================

user_images = {}

user_states = {}

bot_messages = {}


# ============================================================
# TRACK BOT MESSAGE
# ============================================================

def track_bot_message(
    user_id,
    message,
):
    if not message:
        return

    if user_id not in bot_messages:
        bot_messages[user_id] = []

    bot_messages[user_id].append(
        message.message_id
    )


# ============================================================
# /START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user_id = update.effective_user.id

    # Start fresh session
    user_images[user_id] = []

    # Clear current state
    user_states.pop(
        user_id,
        None,
    )

    # Clear filename
    context.user_data.pop(
        "pdf_filename",
        None,
    )

    # Start fresh message tracking
    bot_messages[user_id] = []

    message = await update.message.reply_text(

        "👋 សួស្តី! សូមស្វាគមន៍មកកាន់ "
        "Image to PDF Bot 📄\n\n"

        "Bot នេះអាចជួយអ្នកបម្លែងរូបភាពទៅជា PDF បាន។\n\n"

        "📷 សូមផ្ញើរូបភាពដែលអ្នកចង់បញ្ចូលក្នុង PDF។\n\n"

        "អ្នកអាចផ្ញើរូបភាពបានច្រើនតាមដែលអ្នកចង់បាន។"
    )

    track_bot_message(
        user_id,
        message,
    )


# ============================================================
# IMAGE RECEIVER
# ============================================================

async def receive_image(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user_id = update.effective_user.id

    if user_id not in user_images:
        user_images[user_id] = []

    if user_id not in bot_messages:
        bot_messages[user_id] = []

    # Highest quality Telegram photo
    photo = update.message.photo[-1]

    user_images[user_id].append(
        photo.file_id
    )

    image_number = len(
        user_images[user_id]
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "➕ បន្ថែមរូបភាព",
                callback_data="add_more",
            ),

            InlineKeyboardButton(
                "✅ រួចរាល់",
                callback_data="done",
            ),
        ],

        [
            InlineKeyboardButton(
                "❌ បោះបង់",
                callback_data="cancel",
            )
        ],
    ]

    reply_markup = InlineKeyboardMarkup(
        keyboard
    )

    message = await update.message.reply_text(

        f"✅ បានទទួលរូបភាពទី "
        f"{image_number} រួចរាល់!\n\n"

        f"📸 ចំនួនរូបភាពបច្ចុប្បន្ន៖ "
        f"**{image_number}**\n\n"

        "សូមផ្ញើរូបភាពបន្ថែម "
        "ឬចុច **រួចរាល់**។",

        reply_markup=reply_markup,

        parse_mode="Markdown",
    )

    track_bot_message(
        user_id,
        message,
    )


# ============================================================
# IMAGE SENT AS FILE
# ============================================================

async def receive_image_file(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user_id = update.effective_user.id

    if user_id not in user_images:
        user_images[user_id] = []

    if user_id not in bot_messages:
        bot_messages[user_id] = []

    document = update.message.document

    original_filename = (
        document.file_name or ""
    )

    extension = Path(
        original_filename
    ).suffix.lower()

    allowed_extensions = {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
    }

    # ========================================================
    # CHECK EXTENSION
    # ========================================================

    if extension not in allowed_extensions:

        message = await update.message.reply_text(

            "⚠️ ឯកសារនេះមិនមែនជារូបភាពដែល "
            "អាចប្រើបានទេ។\n\n"

            "📷 សូមផ្ញើរូបភាពប្រភេទ៖\n"
            "• JPG\n"
            "• JPEG\n"
            "• PNG\n"
            "• WEBP"
        )

        track_bot_message(
            user_id,
            message,
        )

        return


    # ========================================================
    # CHECK MIME TYPE
    # ========================================================

    mime_type = (
        document.mime_type or ""
    )

    allowed_mime_types = {
        "image/jpeg",
        "image/png",
        "image/webp",
    }

    if (
        mime_type
        and mime_type not in allowed_mime_types
    ):

        message = await update.message.reply_text(

            "⚠️ ប្រភេទឯកសាររូបភាពនេះ "
            "មិនត្រូវបានគាំទ្រទេ។\n\n"

            "សូមផ្ញើ JPG, PNG ឬ WEBP។"
        )

        track_bot_message(
            user_id,
            message,
        )

        return


    # ========================================================
    # SAVE FILE ID
    # ========================================================

    user_images[user_id].append(
        document.file_id
    )

    image_number = len(
        user_images[user_id]
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "➕ បន្ថែមរូបភាព",
                callback_data="add_more",
            ),

            InlineKeyboardButton(
                "✅ រួចរាល់",
                callback_data="done",
            ),
        ],

        [
            InlineKeyboardButton(
                "❌ បោះបង់",
                callback_data="cancel",
            )
        ],
    ]

    reply_markup = InlineKeyboardMarkup(
        keyboard
    )

    message = await update.message.reply_text(

        f"✅ បានទទួលរូបភាពទី "
        f"{image_number} រួចរាល់!\n\n"

        f"📸 ចំនួនរូបភាពបច្ចុប្បន្ន៖ "
        f"**{image_number}**\n\n"

        "សូមផ្ញើរូបភាពបន្ថែម "
        "ឬចុច **រួចរាល់**។",

        reply_markup=reply_markup,

        parse_mode="Markdown",
    )

    track_bot_message(
        user_id,
        message,
    )


# ============================================================
# RECEIVE PDF FILENAME
# ============================================================

async def receive_filename(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user_id = update.effective_user.id

    if user_states.get(
        user_id
    ) != "waiting_for_filename":

        message = await update.message.reply_text(

            "📷 សូមផ្ញើរូបភាពមកខ្ញុំ "
            "ឬចុច **✅ រួចរាល់** "
            "នៅពេលអ្នកបញ្ចប់ការជ្រើសរើសរូបភាព។",

            parse_mode="Markdown",
        )

        track_bot_message(
            user_id,
            message,
        )

        return


    # ========================================================
    # CHECK IMAGES
    # ========================================================

    if (
        user_id not in user_images
        or not user_images[user_id]
    ):

        message = await update.message.reply_text(

            "⚠️ អ្នកមិនទាន់មានរូបភាពទេ។\n\n"

            "សូមផ្ញើរូបភាពមុននឹងបង្កើត PDF។ 📷"
        )

        track_bot_message(
            user_id,
            message,
        )

        return


    # ========================================================
    # GET FILENAME
    # ========================================================

    filename = update.message.text.strip()

    if not filename:

        message = await update.message.reply_text(

            "⚠️ សូមបញ្ចូលឈ្មោះ PDF។\n\n"

            "ឧទាហរណ៍៖\n"
            "ឯកសារសាលា"
        )

        track_bot_message(
            user_id,
            message,
        )

        return


    # ========================================================
    # NORMALIZE KHMER UNICODE
    # ========================================================

    filename = unicodedata.normalize(
        "NFC",
        filename,
    )


    # ========================================================
    # REMOVE .PDF
    # ========================================================

    if filename.lower().endswith(".pdf"):
        filename = filename[:-4]


    # ========================================================
    # REMOVE UNSAFE CHARACTERS
    # ========================================================

    invalid_characters = (
        '<>:"/\\|?*'
    )

    filename = "".join(

        character

        for character in filename

        if character not in invalid_characters

    )


    # ========================================================
    # CLEAN
    # ========================================================

    filename = filename.strip()

    if not filename:

        message = await update.message.reply_text(

            "⚠️ ឈ្មោះ PDF មិនត្រឹមត្រូវទេ។\n\n"

            "សូមសាកល្បងឈ្មោះផ្សេង។"
        )

        track_bot_message(
            user_id,
            message,
        )

        return


    # ========================================================
    # LIMIT FILENAME LENGTH
    # ========================================================

    filename = filename[:150]

    filename = filename + ".pdf"


    # Save filename
    context.user_data[
        "pdf_filename"
    ] = filename

    image_count = len(
        user_images[user_id]
    )

    keyboard = [

        [
            InlineKeyboardButton(
                "✏️ ប្តូរឈ្មោះ",
                callback_data="rename",
            )
        ],

        [
            InlineKeyboardButton(
                "📄 បង្កើត PDF",
                callback_data="generate_pdf",
            )
        ],

        [
            InlineKeyboardButton(
                "❌ បោះបង់",
                callback_data="cancel",
            )
        ],
    ]

    reply_markup = InlineKeyboardMarkup(
        keyboard
    )

    message = await update.message.reply_text(

        f"📄 **ត្រៀមបង្កើត PDF**\n\n"

        f"📸 ចំនួនរូបភាព៖ "
        f"**{image_count}**\n"

        f"📑 ចំនួនទំព័រ៖ "
        f"**{image_count}**\n\n"

        f"🏷️ ឈ្មោះឯកសារ៖\n"
        f"`{filename}`\n\n"

        "សូមពិនិត្យព័ត៌មានខាងលើ "
        "មុនបង្កើត PDF។",

        reply_markup=reply_markup,

        parse_mode="Markdown",
    )

    track_bot_message(
        user_id,
        message,
    )

    user_states.pop(
        user_id,
        None,
    )


# ============================================================
# DOWNLOAD TELEGRAM IMAGES
# ============================================================

async def download_user_images(
    context,
    file_ids,
    session_dir,
):

    downloaded_files = []

    for index, file_id in enumerate(
        file_ids,
        start=1,
    ):

        telegram_file = (
            await context.bot.get_file(
                file_id
            )
        )

        image_path = (
            session_dir
            / f"image_{index}"
        )

        await telegram_file.download_to_drive(
            custom_path=str(
                image_path
            )
        )

        # Validate image
        try:

            with Image.open(
                image_path
            ) as image:

                image.verify()

        except Exception:

            raise ValueError(
                f"File {index} is not a valid image."
            )

        downloaded_files.append(
            image_path
        )

    return downloaded_files


# ============================================================
# CREATE PDF
# ============================================================

def create_pdf(
    image_paths,
    pdf_path,
):

    images = []

    try:

        for image_path in image_paths:

            image = Image.open(
                image_path
            )

            # =================================================
            # RGB
            # =================================================

            if image.mode != "RGB":

                background = Image.new(
                    "RGB",
                    image.size,
                    "white",
                )

                if image.mode == "RGBA":

                    background.paste(

                        image,

                        mask=image.getchannel(
                            "A"
                        ),

                    )

                else:

                    background.paste(
                        image
                    )

                image.close()

                image = background

            else:

                image = image.copy()

            images.append(
                image
            )


        if not images:

            raise ValueError(
                "No images to convert."
            )


        first_image = images[0]

        remaining_images = images[1:]


        first_image.save(

            str(pdf_path),

            "PDF",

            resolution=100.0,

            save_all=True,

            append_images=remaining_images,

        )

    finally:

        for image in images:

            try:

                image.close()

            except Exception:

                pass


# ============================================================
# GENERATE PDF
# ============================================================

async def generate_pdf(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    user_id = query.from_user.id

    file_ids = user_images.get(
        user_id,
        [],
    )

    if not file_ids:

        message = await query.message.reply_text(

            "⚠️ អ្នកមិនមានរូបភាពសម្រាប់ "
            "បង្កើត PDF ទេ។"
        )

        track_bot_message(
            user_id,
            message,
        )

        return


    filename = context.user_data.get(
        "pdf_filename"
    )

    if not filename:

        message = await query.message.reply_text(

            "⚠️ មិនទាន់មានឈ្មោះ PDF ទេ។\n\n"

            "សូមបញ្ចូលឈ្មោះ PDF មុន។"
        )

        track_bot_message(
            user_id,
            message,
        )

        user_states[user_id] = (
            "waiting_for_filename"
        )

        return


    # ========================================================
    # UNIQUE SESSION DIRECTORY
    # ========================================================

    session_id = uuid.uuid4().hex

    session_dir = (
        TEMP_DIR
        / str(user_id)
        / session_id
    )

    session_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    pdf_path = (
        session_dir
        / filename
    )


    try:

        message = await query.message.reply_text(

            "⏳ **កំពុងទាញយករូបភាព...**\n\n"

            f"📸 ចំនួនរូបភាព៖ "
            f"**{len(file_ids)}**",

            parse_mode="Markdown",
        )

        track_bot_message(
            user_id,
            message,
        )


        # ====================================================
        # DOWNLOAD
        # ====================================================

        downloaded_files = (
            await download_user_images(

                context,

                file_ids,

                session_dir,

            )
        )


        message = await query.message.reply_text(

            "📄 **កំពុងបង្កើត PDF...**\n\n"

            "សូមរង់ចាំបន្តិច។",

            parse_mode="Markdown",
        )

        track_bot_message(
            user_id,
            message,
        )


        # ====================================================
        # CREATE PDF
        # ====================================================

        create_pdf(

            downloaded_files,

            pdf_path,

        )


        if not pdf_path.exists():

            raise FileNotFoundError(
                "PDF file was not created."
            )


        # ====================================================
        # SEND PDF
        # ====================================================

        with open(
            pdf_path,
            "rb",
        ) as pdf_file:

            pdf_message = (
                await query.message.reply_document(

                    document=pdf_file,

                    filename=filename,

                    caption=(

                        "✅ **បង្កើត PDF បានជោគជ័យ!**\n\n"

                        f"📄 ឈ្មោះ៖ `{filename}`\n"

                        f"📸 ចំនួនរូបភាព៖ "
                        f"{len(file_ids)}\n"

                        f"📑 ចំនួនទំព័រ៖ "
                        f"{len(file_ids)}"

                    ),

                    parse_mode="Markdown",

                )
            )


        track_bot_message(
            user_id,
            pdf_message,
        )


        # ====================================================
        # SUCCESS BUTTONS
        # ====================================================

        keyboard = [

            [
                InlineKeyboardButton(
                    "📄 បង្កើត PDF ម្តងទៀត",
                    callback_data="create_again",
                )
            ],

            [
                InlineKeyboardButton(
                    "🗑️ សម្អាតការសន្ទនា",
                    callback_data="clear_chat",
                )
            ],

        ]

        reply_markup = InlineKeyboardMarkup(
            keyboard
        )


        message = await query.message.reply_text(

            "🎉 **រួចរាល់!**\n\n"

            "តើអ្នកចង់ធ្វើអ្វីបន្ទាប់?",

            reply_markup=reply_markup,

            parse_mode="Markdown",
        )

        track_bot_message(
            user_id,
            message,
        )


        # ====================================================
        # CLEAR SESSION
        # ====================================================

        user_images.pop(
            user_id,
            None,
        )

        user_states.pop(
            user_id,
            None,
        )

        context.user_data.pop(
            "pdf_filename",
            None,
        )

    finally:

        try:

            if session_dir.exists():

                shutil.rmtree(
                    session_dir
                )

        except Exception as cleanup_error:

            logger.error(
                "Cleanup error: %s",
                cleanup_error,
            )


# ============================================================
# CREATE AGAIN
# ============================================================

async def create_again(
    query,
    user_id,
    context,
):

    user_images[user_id] = []

    user_states.pop(
        user_id,
        None,
    )

    context.user_data.pop(
        "pdf_filename",
        None,
    )

    if user_id not in bot_messages:
        bot_messages[user_id] = []


    message = await query.message.reply_text(

        "📄 **បង្កើត PDF ថ្មី**\n\n"

        "សូមចាប់ផ្តើមផ្ញើរូបភាពថ្មីមកខ្ញុំ។ 📷\n\n"

        "អ្នកអាចផ្ញើរូបភាពបានច្រើនតាមដែលអ្នកចង់បាន។",

        parse_mode="Markdown",
    )

    track_bot_message(
        user_id,
        message,
    )


# ============================================================
# CLEAR CHAT
# ============================================================

async def clear_chat(
    query,
    user_id,
    context,
):

    message_ids = bot_messages.get(
        user_id,
        [],
    )

    for message_id in message_ids:

        try:

            await context.bot.delete_message(

                chat_id=query.message.chat_id,

                message_id=message_id,

            )

        except Exception as error:

            logger.warning(
                "Could not delete message %s: %s",
                message_id,
                error,
            )


    bot_messages[user_id] = []

    user_images.pop(
        user_id,
        None,
    )

    user_states.pop(
        user_id,
        None,
    )

    context.user_data.pop(
        "pdf_filename",
        None,
    )


# ============================================================
# BUTTON HANDLER
# ============================================================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id


    # ========================================================
    # RENAME
    # ========================================================

    if query.data == "rename":

        user_states[user_id] = (
            "waiting_for_filename"
        )

        message = await query.message.reply_text(

            "✏️ **ប្តូរឈ្មោះ PDF**\n\n"

            "សូមបញ្ចូលឈ្មោះ PDF ថ្មី។\n\n"

            "ឧទាហរណ៍៖\n"
            "`ឯកសារសាលា_2026`\n\n"

            "💡 មិនចាំបាច់បញ្ចូល `.pdf` ទេ។",

            parse_mode="Markdown",
        )

        track_bot_message(
            user_id,
            message,
        )

        return


    # ========================================================
    # ADD MORE
    # ========================================================

    if query.data == "add_more":

        image_count = len(
            user_images.get(
                user_id,
                [],
            )
        )

        message = await query.message.reply_text(

            f"📸 បច្ចុប្បន្នអ្នកមានរូបភាពចំនួន "
            f"**{image_count}**។\n\n"

            "សូមផ្ញើរូបភាពបន្ថែមមកខ្ញុំ។ 📷",

            parse_mode="Markdown",
        )

        track_bot_message(
            user_id,
            message,
        )

        return


    # ========================================================
    # DONE
    # ========================================================

    if query.data == "done":

        image_count = len(
            user_images.get(
                user_id,
                [],
            )
        )

        if image_count == 0:

            message = await query.message.reply_text(

                "⚠️ អ្នកមិនទាន់បានផ្ញើរូបភាពទេ។\n\n"

                "សូមផ្ញើរូបភាពយ៉ាងហោចណាស់ "
                "1 រូប។ 📷"
            )

            track_bot_message(
                user_id,
                message,
            )

            return


        user_states[user_id] = (
            "waiting_for_filename"
        )

        message = await query.message.reply_text(

            f"📄 **ត្រៀមបង្កើត PDF**\n\n"

            f"📸 ចំនួនរូបភាព៖ "
            f"**{image_count}**\n"

            f"📑 ចំនួនទំព័រ៖ "
            f"**{image_count}**\n\n"

            "✏️ សូមបញ្ចូលឈ្មោះ PDF "
            "ដែលអ្នកចង់បាន។\n\n"

            "ឧទាហរណ៍៖\n"
            "`ឯកសារសាលា`\n\n"

            "💡 មិនចាំបាច់បញ្ចូល `.pdf` ទេ។",

            parse_mode="Markdown",
        )

        track_bot_message(
            user_id,
            message,
        )

        return


    # ========================================================
    # GENERATE
    # ========================================================

    if query.data == "generate_pdf":

        await generate_pdf(
            update,
            context,
        )

        return


    # ========================================================
    # CREATE AGAIN
    # ========================================================

    if query.data == "create_again":

        await create_again(
            query,
            user_id,
            context,
        )

        return


    # ========================================================
    # CLEAR CHAT
    # ========================================================

    if query.data == "clear_chat":

        await clear_chat(
            query,
            user_id,
            context,
        )

        return


    # ========================================================
    # CANCEL
    # ========================================================

    if query.data == "cancel":

        user_images.pop(
            user_id,
            None,
        )

        user_states.pop(
            user_id,
            None,
        )

        context.user_data.pop(
            "pdf_filename",
            None,
        )

        message = await query.message.reply_text(

            "❌ បានបោះបង់ការបង្កើត PDF។\n\n"

            "អ្នកអាចចាប់ផ្តើមម្តងទៀត "
            "ដោយផ្ញើរូបភាពថ្មី។ 📷"
        )

        track_bot_message(
            user_id,
            message,
        )

        return


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(
    update,
    context,
):

    logger.exception(
        "Telegram error: %s",
        context.error,
    )


# ============================================================
# CREATE TELEGRAM APPLICATION
# ============================================================

application = (
    Application
    .builder()
    .token(BOT_TOKEN)
    .build()
)


# ============================================================
# REGISTER HANDLERS
# ============================================================

application.add_handler(
    CommandHandler(
        "start",
        start,
    )
)


application.add_handler(
    MessageHandler(
        filters.PHOTO,
        receive_image,
    )
)


application.add_handler(
    MessageHandler(
        filters.Document.ALL,
        receive_image_file,
    )
)


application.add_handler(
    MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        receive_filename,
    )
)


application.add_handler(
    CallbackQueryHandler(
        button_handler,
    )
)


application.add_error_handler(
    error_handler
)


# ============================================================
# HEALTH CHECK
# ============================================================

async def health(
    request: Request,
):

    return PlainTextResponse(
        "Image2PDF Bot is running."
    )


# ============================================================
# TELEGRAM WEBHOOK
# ============================================================

async def telegram_webhook(
    request: Request,
):

    if request.method != "POST":

        return JSONResponse(
            {
                "error": "POST required"
            },
            status_code=405,
        )


    try:

        data = await request.json()

        update = Update.de_json(
            data,
            application.bot,
        )

        await application.process_update(
            update
        )

        return JSONResponse(
            {
                "ok": True
            }
        )


    except Exception as error:

        logger.exception(
            "Webhook processing error"
        )

        return JSONResponse(
            {
                "ok": False,
                "error": str(error),
            },
            status_code=500,
        )


# ============================================================
# STARLETTE WEB APP
# ============================================================

routes = [

    Route(
        "/health",
        health,
        methods=["GET"],
    ),

]


# Only expose the Telegram webhook route
# when webhook mode is being used.

if USE_WEBHOOK:

    routes.append(

        Route(
            f"/telegram-webhook/{WEBHOOK_SECRET}",
            telegram_webhook,
            methods=["POST"],
        )

    )


web_app = Starlette(
    routes=routes
)


# ============================================================
# LOCAL POLLING MODE
# ============================================================

# ============================================================
# LOCAL POLLING MODE
# ============================================================

def run_polling():

    logger.info(
        "🤖 Image2PDF Bot starting in POLLING mode..."
    )

    logger.info(
        "📍 Local development mode enabled."
    )

    logger.info(
        "🌐 RENDER_EXTERNAL_URL is not required."
    )

    # --------------------------------------------------------
    # Run Telegram polling
    # --------------------------------------------------------

    application.run_polling(
        drop_pending_updates=True
    )


# ============================================================
# RENDER WEBHOOK MODE
# ============================================================

async def run_webhook():

    logger.info(
        "🤖 Image2PDF Bot starting in WEBHOOK mode..."
    )

    logger.info(
        "🌐 Render URL: %s",
        RENDER_EXTERNAL_URL,
    )

    logger.info(
        "🔗 Webhook URL: %s",
        WEBHOOK_URL,
    )

    # --------------------------------------------------------
    # Initialize Telegram application
    # --------------------------------------------------------

    await application.initialize()

    await application.start()

    # --------------------------------------------------------
    # Set Telegram webhook
    # --------------------------------------------------------

    await application.bot.set_webhook(

        url=WEBHOOK_URL,

        drop_pending_updates=True,

        allowed_updates=Update.ALL_TYPES,

    )

    logger.info(
        "✅ Telegram webhook configured."
    )

    # --------------------------------------------------------
    # Start Uvicorn
    # --------------------------------------------------------

    config = uvicorn.Config(

        web_app,

        host="0.0.0.0",

        port=PORT,

        log_level="info",

    )

    server = uvicorn.Server(
        config
    )

    try:

        await server.serve()

    finally:

        logger.info(
            "🛑 Shutting down bot..."
        )

        await application.stop()

        await application.shutdown()


# ============================================================
# PROGRAM ENTRY POINT
# ============================================================

if __name__ == "__main__":

    if USE_WEBHOOK:

        logger.info(
            "============================================"
        )

        logger.info(
            "Running in RENDER WEBHOOK mode"
        )

        logger.info(
            "============================================"
        )

        asyncio.run(
            run_webhook()
        )

    else:

        logger.info(
            "============================================"
        )

        logger.info(
            "Running in LOCAL POLLING mode"
        )

        logger.info(
            "============================================"
        )

        run_polling()