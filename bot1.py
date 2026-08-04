import os
import asyncio
import io
import cv2
import numpy as np
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

# قراءة التوكن من متغيرات بيئة GitHub Secrets
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8889805404:AAHNTvZ0i5xg0fYd2UgFRhFY6yTKZIILe_Q")

user_photos = {}
executor = ThreadPoolExecutor(max_workers=100)

def auto_crop_and_fix(image_bytes):
    try:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if img is None:
            return Image.open(io.BytesIO(image_bytes)).convert('RGB')

        orig = img.copy()
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edged = cv2.Canny(blurred, 50, 200)

        contours, _ = cv2.findContours(edged.copy(), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        contours = sorted(contours, key=cv2.contourArea, reverse=True)[:5]

        card_contour = None
        for c in contours:
            peri = cv2.arcLength(c, True)
            approx = cv2.approxPolyDP(c, 0.02 * peri, True)
            if len(approx) == 4:
                card_contour = approx
                break

        if card_contour is not None:
            pts = card_contour.reshape(4, 2)
            rect = np.zeros((4, 2), dtype="float32")

            s = pts.sum(axis=1)
            rect[0] = pts[np.argmin(s)]
            rect[2] = pts[np.argmax(s)]

            diff = np.diff(pts, axis=1)
            rect[1] = pts[np.argmin(diff)]
            rect[3] = pts[np.argmax(diff)]

            (tl, tr, br, bl) = rect
            widthA = np.sqrt(((br[0] - bl[0]) ** 2) + ((br[1] - bl[1]) ** 2))
            widthB = np.sqrt(((tr[0] - tl[0]) ** 2) + ((tr[1] - tl[1]) ** 2))
            maxWidth = max(int(widthA), int(widthB))

            heightA = np.sqrt(((tr[0] - br[0]) ** 2) + ((tr[1] - br[1]) ** 2))
            heightB = np.sqrt(((tl[0] - bl[0]) ** 2) + ((tl[1] - bl[1]) ** 2))
            maxHeight = max(int(heightA), int(heightB))

            dst = np.array([
                [0, 0],
                [maxWidth - 1, 0],
                [maxWidth - 1, maxHeight - 1],
                [0, maxHeight - 1]], dtype="float32")

            M = cv2.getPerspectiveTransform(rect, dst)
            warped = cv2.warpPerspective(orig, M, (maxWidth, maxHeight))
            
            if warped.shape[0] > warped.shape[1]:
                warped = cv2.rotate(warped, cv2.ROTATE_90_CLOCKWISE)

            color_converted = cv2.cvtColor(warped, cv2.COLOR_BGR2RGB)
            return Image.fromarray(color_converted)

        pil_img = Image.open(io.BytesIO(image_bytes)).convert('RGB')
        if pil_img.height > pil_img.width:
            pil_img = pil_img.rotate(-90, expand=True)
        return pil_img
    except Exception:
        return Image.open(io.BytesIO(image_bytes)).convert('RGB')

def process_id_cards_a4(photos_bytes_list):
    processed_images = [auto_crop_and_fix(b) for b in photos_bytes_list]
    
    a4_width, a4_height = 2480, 3508
    cards_per_page = 4
    pages_bytes = []

    card_pairs = []
    for i in range(0, len(processed_images), 2):
        front = processed_images[i]
        back = processed_images[i+1] if (i + 1) < len(processed_images) else None
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
            front_resized = front.resize((f_w, f_h), Image.Resampling.LANCZOS)

            x_front = 160
            canvas.paste(front_resized, (x_front, y_offset))

            if back:
                b_aspect = back.height / back.width
                b_w = target_card_w
                b_h = int(b_w * b_aspect)
                back_resized = back.resize((b_w, b_h), Image.Resampling.LANCZOS)
                
                x_back = x_front + f_w + 160
                canvas.paste(back_resized, (x_back, y_offset))
                max_h = max(f_h, b_h)
            else:
                max_h = f_h

            y_offset += max_h + 160

        img_byte_arr = io.BytesIO()
        canvas.save(img_byte_arr, format='JPEG', quality=95)
        pages_bytes.append(img_byte_arr.getvalue())

    return img2pdf.convert(pages_bytes)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🚀 أهلاً بك في البوت الذكي!\n\n"
        "• أرسل صورة أو صوراً واضغط [📄 تحويل إلى PDF] أو [💳 تسطير بطاقات A4] لجمع 4 بطاقات بصفحة واحدة.\n"
        "• أرسل ملف PDF للتعامل معه مباشرة واستخراج الصور منه."
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
        [InlineKeyboardButton(f"💳 تسطير بطاقات A4 ({count} صورة)", callback_data="convert_id_card")],
        [InlineKeyboardButton("❌ مسح وإلغاء", callback_data="cancel_all")]
    ])

    text_msg = f"📥 تم استلام ({count}) صورة حتى الآن...\nاختر طريقة التحويل المطلوبة:"

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

        mode_text = "قص وتسطير 4 بطاقات بصفحة A4" if query.data == "convert_id_card" else "تحويل إلى PDF"
        await query.edit_message_text(f"🚀 جاري معالجة {total_count} صورة بنظام [{mode_text}]...")

        try:
            download_tasks = [fetch_photo_bytes(p) for p in photo_objects]
            photos_bytes_list = await asyncio.gather(*download_tasks)

            loop = asyncio.get_event_loop()

            if query.data == "convert_id_card":
                pdf_bytes = await loop.run_in_executor(
                    executor, 
                    lambda: process_id_cards_a4(photos_bytes_list)
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
                caption=f"⚡ تم تنفيذ التحويل بنجاح!"
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

        await query.edit_message_text("⚡ جاري استخراج جميع الصور من ملف الـ PDF...")

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
                    
                return all_batches, total_pages

            loop = asyncio.get_event_loop()
            batches, total_pages = await loop.run_in_executor(executor, extract_pdf_ultra_fast)

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

    print("🚀 البوت جاهز ويعمل بكافة الخيارات المطلوبة على GitHub Actions...")
    app.run_polling()

if __name__ == "__main__":
    main()