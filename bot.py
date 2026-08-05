import os
import asyncio
import io
import fitz  # PyMuPDF
import img2pdf
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8889805404:AAHNTvZ0i5xq0fYd2UqFRhFY6yTKZIILe_Q")

user_photos_store = {}
user_pdf_store = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚡ أرسل الصور للتحويل المباشر إلى PDF، أو أرسل ملف PDF لاستخراج كافة الصور منه.")

# ----------------- تجميع الصور والتحويل السريع -----------------
async def update_photo_ui(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    await asyncio.sleep(0.3)
    if user_id not in user_photos_store:
        return

    data = user_photos_store[user_id]
    count = len(data["bytes_list"])
    if count == 0 or data.get("is_processing"):
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"⚡ تحويل ({count}) صورة إلى PDF", callback_data="convert_to_pdf")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="cancel_action")]
    ])

    chat_id = data["chat_id"]

    if data.get("ctrl_msg_id"):
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=data["ctrl_msg_id"])
        except Exception:
            pass

    try:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=f"📥 تم استلام ({count}) صورة...",
            reply_markup=keyboard
        )
        data["ctrl_msg_id"] = msg.message_id
    except Exception:
        pass

async def handle_photos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    if user_id not in user_photos_store:
        user_photos_store[user_id] = {
            "bytes_list": [],
            "ctrl_msg_id": None,
            "chat_id": chat_id,
            "is_processing": False,
            "timer_task": None
        }

    data = user_photos_store[user_id]
    if data["is_processing"]:
        return

    photo = update.message.photo[-1]
    file_obj = await photo.get_file()
    photo_bytes = await file_obj.download_as_bytearray()
    
    data["bytes_list"].append(bytes(photo_bytes))

    if data.get("timer_task"):
        data["timer_task"].cancel()

    data["timer_task"] = asyncio.create_task(update_photo_ui(user_id, context))

# ----------------- استخراج الـ PDF -----------------
async def update_pdf_ui(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    await asyncio.sleep(0.3)
    if user_id not in user_pdf_store:
        return

    data = user_pdf_store[user_id]
    count = len(data["files"])
    if count == 0 or data.get("is_processing"):
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🖼️ استخراج كافة الصور من ({count}) ملف PDF", callback_data="extract_all_pdfs")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="cancel_action")]
    ])

    chat_id = data["chat_id"]

    if data.get("ctrl_msg_id"):
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=data["ctrl_msg_id"])
        except Exception:
            pass

    try:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=f"📄 تم استلام ({count}) ملف PDF...",
            reply_markup=keyboard
        )
        data["ctrl_msg_id"] = msg.message_id
    except Exception:
        pass

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if doc.mime_type == 'application/pdf' or doc.file_name.lower().endswith('.pdf'):
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id

        if user_id not in user_pdf_store:
            user_pdf_store[user_id] = {
                "files": [],
                "ctrl_msg_id": None,
                "chat_id": chat_id,
                "is_processing": False,
                "timer_task": None
            }

        data = user_pdf_store[user_id]
        if data["is_processing"]:
            return

        file_obj = await context.bot.get_file(doc.file_id)
        pdf_bytes = await file_obj.download_as_bytearray()
        data["files"].append(bytes(pdf_bytes))

        if data.get("timer_task"):
            data["timer_task"].cancel()

        data["timer_task"] = asyncio.create_task(update_pdf_ui(user_id, context))

# ----------------- تنفيذ الأوامر بأسرع وقت -----------------
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "convert_to_pdf":
        if user_id not in user_photos_store or not user_photos_store[user_id]["bytes_list"]:
            try:
                await query.delete_message()
            except Exception:
                pass
            return

        data = user_photos_store[user_id]
        data["is_processing"] = True
        photos_bytes = list(data["bytes_list"])
        total = len(photos_bytes)

        try:
            await query.delete_message()
        except Exception:
            pass

        try:
            pdf_bytes = img2pdf.convert(photos_bytes)
            pdf_stream = io.BytesIO(pdf_bytes)
            pdf_stream.name = f"Document_{total}.pdf"

            await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=pdf_stream,
                caption=f"✅ تم تحويل {total} صورة بنجاح!"
            )

            user_photos_store.pop(user_id, None)
        except Exception as e:
            user_photos_store.pop(user_id, None)

    elif query.data == "extract_all_pdfs":
        if user_id not in user_pdf_store or not user_pdf_store[user_id]["files"]:
            try:
                await query.delete_message()
            except Exception:
                pass
            return

        data = user_pdf_store[user_id]
        data["is_processing"] = True
        files_list = list(data["files"])

        try:
            await query.delete_message()
        except Exception:
            pass

        try:
            all_extracted_images = []
            for pdf_bytes in files_list:
                doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                for page in doc:
                    pix = page.get_pixmap(dpi=100)
                    all_extracted_images.append(pix.tobytes("jpg"))

            for i in range(0, len(all_extracted_images), 10):
                chunk = all_extracted_images[i:i + 10]
                media_group = [InputMediaPhoto(media=io.BytesIO(img)) for img in chunk]
                await context.bot.send_media_group(chat_id=query.message.chat_id, media=media_group)

            user_pdf_store.pop(user_id, None)

        except Exception as e:
            user_pdf_store.pop(user_id, None)

    elif query.data == "cancel_action":
        user_photos_store.pop(user_id, None)
        user_pdf_store.pop(user_id, None)
        try:
            await query.delete_message()
        except Exception:
            pass

def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photos))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.run_polling()

if __name__ == "__main__":
    main()
