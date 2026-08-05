import os
import asyncio
import io
import fitz  # PyMuPDF
import img2pdf
from PIL import Image
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

user_photos = {}
executor = ThreadPoolExecutor(max_workers=100)

def process_id_cards_fast(photos_bytes_list):
    a4_width, a4_height = 2480, 3508
    cards_per_page = 4
    pages_bytes = []

    # فتح الصور مباشرة وتحويلها إلى RGB بأسرع طريقة
    images = [Image.open(io.BytesIO(b)).convert('RGB') for b in photos_bytes_list]

    card_pairs = []
    for i in range(0, len(images), 2):
        front = images[i]
        back = images[i+1] if (i + 1) < len(images) else None
        card_pairs.append((front, back))

    for p in range(0, len(card_pairs), cards_per_page):
        page_cards = card_pairs[p:p + cards_per_page]
        canvas = Image.new('RGB', (a4_width, a4_height), (255, 255, 255))
        
        target_card_w = 1000
        y_offset = 120

        for front, back in page_cards:
            f_aspect = front.height / front.width
            f_w = target_card_w
            f_h = int(f_w * f_aspect)
            front_resized = front.resize((f_w, f_h), Image.Resampling.BILINEAR)

            x_front = 160
            canvas.paste(front_resized, (x_front, y_offset))

            if back:
                b_aspect = back.height / back.width
                b_w = target_card_w
                b_h = int(b_w * b_aspect)
                back_resized = back.resize((b_w, b_h), Image.Resampling.BILINEAR)
                
                x_back = x_front + f_w + 160
                canvas.paste(back_resized, (x_back, y_offset))
                max_h = max(f_h, b_h)
            else:
                max_h = f_h

            y_offset += max_h + 160

        img_byte_arr = io.BytesIO()
        canvas.save(img_byte_arr, format='JPEG', quality=85)
        pages_bytes.append(img_byte_arr.getvalue())

    return img2pdf.convert(pages_bytes)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🚀 أهلاً بك في البوت السريع جداً!\n\n"
        "• أرسل أي عدد من الصور واختر [📄 تحويل إلى PDF] أو [💳 تسطير 4 بطاقات بصفحة A4].\n"
        "• أرسل ملف PDF لاستخراج جميع الصور منه بسرعة."
    )
    await update.message.reply_text(msg)

async def fetch_photo_bytes(photo_obj):
    file = await photo_obj.get_file()
    return bytes(await file.download_as_bytearray())

async def handle_photos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    if user_id not in user_photos:
        user_photos[user_id] = {
            "photos": [],
            "ctrl_msg_id": None,
            "is_processing": False
        }

    if user_photos[user_id]["is_processing"]:
        return

    photo = update.message.photo[-1]
    user_photos[user_id]["photos"].append(photo)
    count = len(user_photos[user_id]["photos"])

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📄 تحويل إلى PDF ({count} صورة)", callback_data="convert_pdf_fast")],
        [InlineKeyboardButton(f"💳 تسطير 4 بطاقات في صفحة ({count} صورة)", callback_data="convert_id_card")],
        [InlineKeyboardButton("❌ مسح وإلغاء", callback_data="cancel_all")]
    ])

    text_msg = f"📥 تم استلام ({count}) صورة حتى الآن...\nاختر العملية المطلوبة:"

    ctrl_id = user_photos[user_id]["ctrl_msg_id"]
    if ctrl_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=ctrl_id,
                text=text_msg,
                reply_markup=keyboard
            )
            return
        except Exception:
            pass

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=text_msg,
        reply_markup=keyboard
    )
    user_photos[user_id]["ctrl_msg_id"] = msg.message_id

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data in ["convert_pdf_fast", "convert_id_card"]:
        if user_id not in user_photos or not user_photos[user_id]["photos"]:
            await query.edit_message_text("❌ لم يتم العثور على صور!")
            return

        if user_photos[user_id]["is_processing"]:
            return

        user_photos[user_id]["is_processing"] = True
        photo_objects = list(user_photos[user_id]["photos"])
        total_count = len(photo_objects)

        mode_text = "تسطير 4 بطاقات بصفحة" if query.data == "convert_id_card" else "تحويل سريع إلى PDF"
        await query.edit_message_text(f"⚡ جاري معالجة {total_count} صورة بنظام [{mode_text}]...")

        try:
            download_tasks = [fetch_photo_bytes(p) for p in photo_objects]
            photos_bytes_list = await asyncio.gather(*download_tasks)

            loop = asyncio.get_event_loop()

            if query.data == "convert_id_card":
                pdf_bytes = await loop.run_in_executor(
                    executor, 
                    lambda: process_id_cards_fast(photos_bytes_list)
                )
            else:
                pdf_bytes = await loop.run_in_executor(
                    executor, 
                    lambda: img2pdf.convert(photos_bytes_list)
                )

            pdf_stream = io.BytesIO(pdf_bytes)
            pdf_stream.name = f"output_{user_id}.pdf"

            await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=pdf_stream,
                caption=f"⚡ تم تنفيذ التحويل بنجاح وسرعة!"
            )

            user_photos.pop(user_id, None)

        except Exception as e:
            if user_id in user_photos:
                user_photos[user_id]["is_processing"] = False
            await context.bot.send_message(chat_id=query.message.chat_id, text=f"❌ حدث خطأ أثناء المعالجة: {str(e)}")

    elif query.data == "extract_pdf_photos":
        doc_data = context.user_data.get("pdf_data")
        if not doc_data:
            await query.edit_message_text("❌ انتهت جلسة الملف، يرجى إعادة إرساله.")
            return

        await query.edit_message_text("⚡ جاري استخراج الصور...")

        try:
            def extract_pdf_ultra_fast():
                pdf_doc = fitz.open(stream=doc_data, filetype="pdf")
                total_pages = len(pdf_doc)
                all_batches = []
                
                batch_size = 10
                for i in range(0, total_pages, batch_size):
                    batch_images = []
                    for page_index in range(i, min(i + batch_size, total_pages)):
                        page = pdf_doc[page_index]
                        pix = page.get_pixmap(dpi=90)
                        batch_images.append(pix.tobytes("jpg"))
                    all_batches.append(batch_images)
                    
                return all_batches

            loop = asyncio.get_event_loop()
            batches = await loop.run_in_executor(executor, extract_pdf_ultra_fast)

            for batch in batches:
                media_group = [
                    InputMediaPhoto(media=io.BytesIO(img_bytes))
                    for img_bytes in batch
                ]
                await context.bot.send_media_group(
                    chat_id=query.message.chat_id,
                    media=media_group
                )

            await query.delete_message()
            context.user_data.pop("pdf_data", None)

        except Exception as e:
            await context.bot.send_message(chat_id=query.message.chat_id, text=f"❌ حدث خطأ أثناء استخراج الصور: {str(e)}")

    elif query.data == "cancel_all":
        user_photos.pop(user_id, None)
        context.user_data.pop("pdf_data", None)
        await query.edit_message_text("❌ تم المسح وتفريغ الذاكرة.")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    
    if doc.mime_type == 'application/pdf' or doc.file_name.lower().endswith('.pdf'):
        file = await context.bot.get_file(doc.file_id)
        pdf_data = await file.download_as_bytearray()
        
        context.user_data["pdf_data"] = pdf_data

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🖼️ استخراج الصور من الـ PDF", callback_data="extract_pdf_photos")],
            [InlineKeyboardButton("❌ إلغاء", callback_data="cancel_all")]
        ])

        await update.message.reply_text(
            f"📄 تم استلام ملف الـ PDF ({doc.file_name})...\nاضغط على الزر أدناه لبدء استخراج الصور:",
            reply_markup=keyboard
        )

def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photos))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(CallbackQueryHandler(button_callback))

    print("🚀 البوت السريع يعمل بكفاءة وبدون أي أخطاء...")
    app.run_polling()

if __name__ == "__main__":
    main()
