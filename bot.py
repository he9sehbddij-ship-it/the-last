import os
import asyncio
import io
import fitz  # PyMuPDF
import img2pdf
from concurrent.futures import ThreadPoolExecutor
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

# القاموس لحفظ بيانات الصور لكل مستخدم
user_data_store = {}
executor = ThreadPoolExecutor(max_workers=50)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚡ أهلاً بك!\n\n"
        "• أرسل الصور دفعة واحدة وسيظهر لك زر التحويل فوراً.\n"
        "• أرسل ملف PDF لاستخراج جميع الصور منه بضغطة زر."
    )

async def update_photo_ui(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    """وظيفة لتحديث الزر بذكاء بعد اكتمال وصول مجموعة الصور لعدم التعليق"""
    await asyncio.sleep(0.6)  # انتظار بسيط لضمان وصول جميع الصور المرسلة دفعة واحدة
    
    if user_id not in user_data_store:
        return

    data = user_data_store[user_id]
    photos_count = len(data["photos"])
    
    if photos_count == 0 or data.get("is_processing"):
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📄 تحويل ({photos_count}) صورة إلى PDF", callback_data="convert_to_pdf")],
        [InlineKeyboardButton("❌ مسح وإلغاء", callback_data="cancel_action")]
    ])

    text_msg = f"📥 تم استلام ({photos_count}) صورة.\nاضغط للتحويل الفوري:"

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
        pass  # تجنب أخطاء التليجرام عند التحديث السريع

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

    # أخذ أعلى دقة للصورة المرسلة
    photo = update.message.photo[-1]
    data["photos"].append(photo)

    # إلغاء المؤقت القديم وإنشاء مؤقت جديد لإعطاء مهلة لتجميع كافة الصور
    if data.get("timer_task"):
        data["timer_task"].cancel()

    data["timer_task"] = asyncio.create_task(update_photo_ui(user_id, context))

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "convert_to_pdf":
        if user_id not in user_data_store or not user_data_store[user_id]["photos"]:
            await query.edit_message_text("❌ حدث تنشيط للذاكرة، يرجى إرسال الصور من جديد.")
            return

        data = user_data_store[user_id]
        if data.get("is_processing"):
            return

        data["is_processing"] = True
        photos_list = list(data["photos"])
        total = len(photos_list)

        await query.edit_message_text(f"⚡ جاري تحويل {total} صورة إلى PDF بأقصى سرعة...")

        try:
            # تنزيل جميع الصور بالتوازي لسرعة فائقة
            async def download_bytes(p):
                f = await p.get_file()
                return bytes(await f.download_as_bytearray())

            download_tasks = [download_bytes(p) for p in photos_list]
            photos_bytes = await asyncio.gather(*download_tasks)

            # التحويل إلى PDF بدون استهلاك معالج السيرفر
            loop = asyncio.get_event_loop()
            pdf_bytes = await loop.run_in_executor(
                executor, lambda: img2pdf.convert(photos_bytes)
            )

            pdf_stream = io.BytesIO(pdf_bytes)
            pdf_stream.name = f"Photos_{total}.pdf"

            await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=pdf_stream,
                caption=f"⚡ تم تحويل {total} صورة بنجاح!"
            )

            # تفريغ الذاكرة تماماً بعد النجاح
            user_data_store.pop(user_id, None)

        except Exception as e:
            if user_id in user_data_store:
                user_data_store[user_id]["is_processing"] = False
            await context.bot.send_message(chat_id=query.message.chat_id, text=f"❌ حدث خطأ: {str(e)}")

    elif query.data == "extract_pdf_photos":
        pdf_data = context.user_data.get("pdf_bytes")
        if not pdf_data:
            await query.edit_message_text("❌ انتهت الجلسة، أرسل ملف الـ PDF مرة أخرى.")
            return

        await query.edit_message_text("⚡ جاري استخراج الصور وإرسالها فوراً...")

        try:
            def extract_all():
                doc = fitz.open(stream=pdf_data, filetype="pdf")
                images = []
                for page in doc:
                    pix = page.get_pixmap(dpi=100)
                    images.append(pix.tobytes("jpg"))
                return images

            loop = asyncio.get_event_loop()
            all_images = await loop.run_in_executor(executor, extract_all)

            # تقسيم الصور لمجموعات (كل ألبوم 10 صور) لسرعة الإرسال
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
        await query.edit_message_text("❌ تم الإلغاء وتفريغ الذاكرة.")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    
    if doc.mime_type == 'application/pdf' or doc.file_name.lower().endswith('.pdf'):
        file = await context.bot.get_file(doc.file_id)
        pdf_bytes = await file.download_as_bytearray()
        
        context.user_data["pdf_bytes"] = bytes(pdf_bytes)

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🖼️ استخراج كافة الصور من الـ PDF", callback_data="extract_pdf_photos")],
            [InlineKeyboardButton("❌ إلغاء", callback_data="cancel_action")]
        ])

        await update.message.reply_text(
            f"📄 تم استلام ملف الـ PDF ({doc.file_name})\nاضغط أدناه لاستخراج الصور فوراً:",
            reply_markup=keyboard
        )

def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photos))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(CallbackQueryHandler(button_callback))

    print("🚀 البوت السريع والمستقر يعمل بدون تعليق...")
    app.run_polling()

if __name__ == "__main__":
    main()
