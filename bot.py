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

# ذاكرة مؤقتة فائقة السرعة
user_data_store = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚡ أرسل الصور للتحويل إلى PDF، أو أرسل ملف PDF لاستخراج الصور فوراً.")

async def update_photo_ui(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    # انتظار قصير جداً (0.3 ثانية) لجمع الصور المرفوعة دفعة واحدة
    await asyncio.sleep(0.3)
    
    if user_id not in user_data_store:
        return

    data = user_data_store[user_id]
    photos_count = len(data["photos"])
    
    if photos_count == 0 or data.get("is_processing"):
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"⚡ تحويل ({photos_count}) صورة إلى PDF فوراً", callback_data="convert_to_pdf")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="cancel_action")]
    ])

    text_msg = f"📥 تم استلام ({photos_count}) صورة."

    try:
        if data.get("ctrl_msg_id"):
            await context.bot.edit_message_text(
                chat_id=data["chat_id"],
                message_id=data["ctrl_msg_id"],
                text=text_msg,
                reply_markup=keyboard
            )
        else:
            msg = await context.bot.send_message(
                chat_id=data["chat_id"],
                text=text_msg,
                reply_markup=keyboard
            )
            data["ctrl_msg_id"] = msg.message_id
    except Exception:
        pass

async def handle_photos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    if user_id not in user_data_store:
        user_data_store[user_id] = {
            "photos": [],
            "ctrl_msg_id": None,
            "chat_id": chat_id,
            "is_processing": False,
            "timer_task": None
        }

    data = user_data_store[user_id]
    if data["is_processing"]:
        return

    # حفظ أعلى دقة مباشرة
    data["photos"].append(update.message.photo[-1])

    if data.get("timer_task"):
        data["timer_task"].cancel()

    data["timer_task"] = asyncio.create_task(update_photo_ui(user_id, context))

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "convert_to_pdf":
        if user_id not in user_data_store or not user_data_store[user_id]["photos"]:
            await query.edit_message_text("❌ حدث خطأ، أرسل الصور مجدداً.")
            return

        data = user_data_store[user_id]
        data["is_processing"] = True
        photos_list = list(data["photos"])
        total = len(photos_list)

        await query.edit_message_text(f"⚡ جاري التحويل الفوري لـ {total} صورة...")

        try:
            # تنزيل الصور بالتوازي بأسرع سرعة
            async def download_bytes(p):
                f = await p.get_file()
                return bytes(await f.download_as_bytearray())

            photos_bytes = await asyncio.gather(*[download_bytes(p) for p in photos_list])

            # تحويل في الذاكرة بلمح البصر
            pdf_bytes = img2pdf.convert(photos_bytes)
            pdf_stream = io.BytesIO(pdf_bytes)
            pdf_stream.name = f"Images_{total}.pdf"

            await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=pdf_stream,
                caption=f"✅ تم تحويل {total} صورة بنجاح!"
            )

            user_data_store.pop(user_id, None)

        except Exception as e:
            user_data_store.pop(user_id, None)
            await context.bot.send_message(chat_id=query.message.chat_id, text=f"❌ خطأ: {str(e)}")

    elif query.data == "extract_pdf_photos":
        pdf_bytes = context.user_data.get("pdf_bytes")
        if not pdf_bytes:
            await query.edit_message_text("❌ انتهت الجلسة، أرسل الـ PDF مجدداً.")
            return

        await query.edit_message_text("⚡ جاري استخراج الصور...")

        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            all_images = [page.get_pixmap(dpi=90).tobytes("jpg") for page in doc]

            # إرسال ألبومات سريعة (10 صور بكل ألبوم)
            for i in range(0, len(all_images), 10):
                chunk = all_images[i:i + 10]
                media_group = [InputMediaPhoto(media=io.BytesIO(img)) for img in chunk]
                await context.bot.send_media_group(chat_id=query.message.chat_id, media=media_group)

            await query.delete_message()
            context.user_data.pop("pdf_bytes", None)

        except Exception as e:
            await context.bot.send_message(chat_id=query.message.chat_id, text=f"❌ خطأ أثناء الاستخراج: {str(e)}")

    elif query.data == "cancel_action":
        user_data_store.pop(user_id, None)
        context.user_data.pop("pdf_bytes", None)
        await query.edit_message_text("❌ تم الإلغاء.")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if doc.mime_type == 'application/pdf' or doc.file_name.lower().endswith('.pdf'):
        file = await context.bot.get_file(doc.file_id)
        context.user_data["pdf_bytes"] = bytes(await file.download_as_bytearray())

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🖼️ استخراج كافة الصور من الـ PDF", callback_data="extract_pdf_photos")],
            [InlineKeyboardButton("❌ إلغاء", callback_data="cancel_action")]
        ])

        await update.message.reply_text(
            f"📄 تم استلام ملف ({doc.file_name}):",
            reply_markup=keyboard
        )

def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photos))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.run_polling()

if __name__ == "__main__":
    main()
