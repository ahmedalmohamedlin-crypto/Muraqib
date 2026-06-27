import os
import sys
import pytz
import requests
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from telegram.ext import CommandHandler
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import re

try:
    from SECRET import TELEGRAM_TOKEN, SALLA_TOKEN_URL
except ModuleNotFoundError:
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
    SALLA_TOKEN_URL = os.getenv("SALLA_TOKEN_URL")

# Ensure terminal prints emojis correctly
sys.stdout.reconfigure(encoding='utf-8')

ACCESS_TOKEN = None
LOCK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.telegram_bot.lock')


def acquire_bot_lock():
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, 'r', encoding='utf-8') as f:
                pid_text = f.read().strip()
            if pid_text:
                pid = int(pid_text)
                os.kill(pid, 0)
                print('⚠️ Telegram bot is already running from another process.')
                return None
        except (ProcessLookupError, PermissionError, ValueError, FileNotFoundError):
            try:
                os.remove(LOCK_FILE)
            except FileNotFoundError:
                pass
        except OSError:
            try:
                os.remove(LOCK_FILE)
            except FileNotFoundError:
                pass

    try:
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        os.write(fd, str(os.getpid()).encode('utf-8'))
        return fd
    except FileExistsError:
        print('⚠️ Telegram bot is already running from another process.')
        return None


def release_bot_lock(fd):
    if fd is None:
        return
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.remove(LOCK_FILE)
    except FileNotFoundError:
        pass

def normalize_phone(phone):
    # إزالة أي شيء غير رقم
    phone = re.sub(r"\D", "", phone)

    return phone

def sending_message_to_customer(orders):
    message = ""

    for order in orders:
        order_number = order.get("reference_id", "—")
        data1 = get_shipping_details(ACCESS_TOKEN, order_number)
        print(data1)
        amount = order.get("total", {}).get("amount", 0)

        # رقم الهاتف (بعض الطلبات فيها receiver وبعضها customer)
        phone = order.get("customer", {}).get("mobile", "غير متوفر")

        # المنتجات
        items = order.get("items", [])
        product_names = ", ".join([item.get("name", "") for item in items])

        # حالة الطلب (إلى أين الشحنة)
        status = order.get("status", {}).get("name", "غير معروف")
        # الوسوم     
        details = get_order_details(ACCESS_TOKEN, order['id'])
        tags = [tag.get("name", "لا يوجد وسوم") for tag in details.get("tags", [])]
        message += (
            f"🧾 رقم الطلب: #<code>{order_number}</code>\n"
            f"💰 السعر: {amount} SAR\n"
            f"📞 الهاتف: <code>{phone}</code>\n"
            f"📦 المنتجات: {product_names}\n"
            f"📍 حالة الشحنة: {status}\n"
            f"🏷️ الوسوم: {tags}\n"
            f"-----------------------------\n"
        )
    return message
# ==============================
# CONFIG
# ==============================

SALLA_ORDERS_URL = "https://api.salla.dev/admin/v2/orders?"
SALLA_SHIPPING_URL = "https://api.salla.dev/admin/v2"
# ==============================

# GET ACCESS TOKEN

# ==============================



def get_access_token():
    token_override = (
        os.getenv("SALLA_TOKEN")
        or os.getenv("SALLA_TOKEN_STORE_1")
        or os.getenv("SALLA_TOKEN_STORE_2")
        or os.getenv("SALLA_TOKEN_STORE_3")
        or os.getenv("SALLA_TOKEN_STORE_4")
    )
    if token_override:
        return token_override

    if not SALLA_TOKEN_URL:
        raise RuntimeError("Salla token is not configured. Set SALLA_TOKEN_URL or one of the SALLA_TOKEN_STORE_* variables.")

    response = requests.get(SALLA_TOKEN_URL)
    response.raise_for_status()

    return response.json().get("access_token")


# ==============================
# GET SHIPPING DETAILS BY SHIPPING ID
# ==============================
def get_shipping_details(token, shipping_id):
    url = f"{SALLA_SHIPPING_URL}/{shipping_id}/shipments"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    respone = requests.get(url, headers=headers)
    if respone.status_code == 200:
        return respone.json().get("data", {})

# ==============================

# GET TODAY ORDERS

# ==============================



def get_today_orders(token):

    riyadh = pytz.timezone("Asia/Riyadh")

    now = datetime.now(riyadh)

    today_start = now.replace(hour=0, minute=0, second=0).strftime("%Y-%m-%d %H:%M:%S")
    today_end = now.replace(hour=23, minute=59, second=59).strftime("%Y-%m-%d %H:%M:%S")

   

    headers = {

        "Authorization": f"Bearer {token}",

        "Content-Type": "application/json"

    }



    params = {

        "from_date": today_start,

        "to_date": today_end,

    }



    response = requests.get(SALLA_ORDERS_URL, headers=headers, params=params)

    data = response.json()
    cleaned_data = []

    for order in data.get("data", []):

        details = get_order_details(token, order['id'])

        tags = details.get("tags", [])

        # orders WITHOUT tags
        if not tags:
            cleaned_data.append(order)

    return cleaned_data
#==============================
# GET ORDERS BY STATUS 
#1: "بانتضار المراجعة"
#2: "قيد التنفيذ"
#3: "جاري التوصيل"
#==============================

def get_orders_by_status(token, status_id):
    now = datetime.now(pytz.timezone("Asia/Riyadh"))
    one_year_ago = (now - timedelta(days=365)).strftime("%Y-%m-%d")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    # Map your status_id to Salla status_id
    if status_id == 1:
        salla_status_id = 1283428545  # under_review
        from_date = one_year_ago
        to_date = (now - timedelta(days=2)).strftime("%Y-%m-%d")
    elif status_id == 2:
        salla_status_id = 1458516934  # completed
        from_date = (now - timedelta(days=30)).strftime("%Y-%m-%d")
        to_date = (now - timedelta(days=2)).strftime("%Y-%m-%d")
    elif status_id == 3:
        salla_status_id = 401284301  # delivering
        from_date = one_year_ago
        to_date = (now - timedelta(days=10)).strftime("%Y-%m-%d")
    else:
        salla_status_id = None
        from_date = None
        to_date = None

    all_orders = []
    page = 1
    per_page = 100

    while True:
        params = {
            "page": page,
            "per_page": per_page
        }
        if salla_status_id:
            params["status"] = salla_status_id
        if from_date:
            params["from_date"] = from_date
        if to_date:
            params["to_date"] = to_date

        response = requests.get(SALLA_ORDERS_URL, headers=headers, params=params)
        if response.status_code != 200:
            print(f"Error: {response.status_code} - {response.text}")
            break

        res_json = response.json()
        data = res_json.get("data", [])
        meta = res_json.get("meta", {}).get("pagination", {})

        all_orders.extend(data)

        # Check if we reached the last page
        if page >= meta.get("total_pages", 0):
            break

        page += 1

    return all_orders
# GET TODAY ORDERS MESSAGE

# ==============================

def get_today_orders_message(token):

    riyadh = pytz.timezone("Asia/Riyadh")

    now = datetime.now(riyadh)

    yesterday = now - timedelta(days=1)
    print(yesterday)
    yesterday_start = yesterday.replace(hour=0, minute=0, second=0).strftime("%Y-%m-%d %H:%M:%S")
    print(yesterday_start)
    yesterday_end = yesterday.replace(hour=23, minute=59, second=59).strftime("%Y-%m-%d %H:%M:%S")
    print(yesterday_end)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    params = {
        "from_date": yesterday_start,
        "to_date": yesterday_end,
    }

    response = requests.get(SALLA_ORDERS_URL, headers=headers, params=params)
    data = response.json()

    orders_without_tags = []

    for order in data.get("data", []):

        details = get_order_details(token, order['id'])
        tags = details.get("tags", [])

        if not tags:
            orders_without_tags.append(order)

    # -------------------------
    # MESSAGE 1 (phone + product)
    # -------------------------

    message1 = ""

    for order in orders_without_tags:

        phone = order.get("customer", {}).get("mobile", "")
        code = order.get("customer", {}).get("mobile_code", "")

        full_phone = f"{code}{phone}"

        items = order.get("items", [])
        product_names = " + ".join([item.get("name", "") for item in items])

        message1 += f"{full_phone}\n{product_names}\n\n"

    # -------------------------
    # MESSAGE 2 (statistics)
    # -------------------------

    whatsapp_total = 0
    website_total = 0

    snap = 0
    recommendation = 0

    for order in orders_without_tags:

        source = order.get("source", "")

        if source == "dashboard":
            whatsapp_total += 1
        else:
            website_total += 1

            # example logic (you can modify depending on your real tag logic)
            customer_city = order.get("customer", {}).get("city", "")

            if "سناب" in customer_city:
                snap += 1

            if "توصية" in customer_city:
                recommendation += 1

    total_sales = len(orders_without_tags)

    replied = 0
    not_replied = 0

    message2 = f"""
مبيعات متجر زمرد■

●اجمالي الواتس : {whatsapp_total}
- تيك توك :
- سناب :
- جوجل :
- توصية :
- انستا :
- يوتيوب :

●اجمالي الموقع : {website_total}
- تيك توك :
- سناب : {snap}
- جوجل :
- توصية : {recommendation}
- انستا :
- يوتيوب :

○اجمالي الرد : {replied}
○اجمالي لم يرد : {not_replied}
●اجمالي المبيعات الكلي:{total_sales}
"""

    return message1, message2
# ==============================

# GET ORDER BY NUMBER

# ==============================


def get_order_by_number(token, order_number):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    params = {

       "reference_id": order_number

    }

   

    response = requests.get(SALLA_ORDERS_URL, headers=headers, params=params)

    if response.status_code == 200:
        return response.json().get("data", {})
    else:
        return None

# ==============================

# GET ORDER BY PHONE NUMBER

# ==============================
def get_orders_by_phone(token, phone_number):
    headers = { 
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    # تنظيف الرقم قبل البحث
    clean_phone = normalize_phone(phone_number)

    params = {
        "keyword": clean_phone
    }

    response = requests.get(SALLA_ORDERS_URL, headers=headers, params=params)

    if response.status_code == 200:
        return response.json().get("data", [])
    else:
        return []
# ==============================
# GET ORDER DETAILS BY ORDER NUMBER
# ==============================
def get_order_details(token, order_id):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    url = f"https://api.salla.dev/admin/v2/orders/{order_id}"

    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        return response.json().get("data", {})
    return {}
# ==============================
# SHOW MENU
# ==============================

def get_store_keyboard():
    keyboard = [
        ["🏪 المتجر 1", "🏪 المتجر 2"],
        ["🏪 المتجر 3", "🏪 المتجر 4"],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_action_keyboard():
    keyboard = [
        ["📦 طلبات اليوم", "📊 رسالة المبيعات"],
        ["🔎 البحث عن طلب", "📱 البحث برقم الهاتف"],
        ["🔙 العودة للمخازن"],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["selected_store"] = None
    context.user_data["search_mode"] = None
    await update.message.reply_text(
        "اختر المتجر الذي تريده:",
        reply_markup=get_store_keyboard()
    )


# ==============================
# HANDLE USER CHOICE
# ==============================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    token = ACCESS_TOKEN
    if not token:
        await update.message.reply_text("❌ Failed to get Salla token.")
        return

    if text in {"🏪 المتجر 1", "🏪 المتجر 2", "🏪 المتجر 3", "🏪 المتجر 4"}:
        store_number = int(text.split()[-1])
        context.user_data["selected_store"] = store_number
        context.user_data["search_mode"] = None
        await update.message.reply_text(
            f"تم اختيار المتجر {store_number}. ماذا تريد أن تفعل؟",
            reply_markup=get_action_keyboard()
        )
        return

    if text == "🔙 العودة للمخازن":
        context.user_data["selected_store"] = None
        context.user_data["search_mode"] = None
        await update.message.reply_text(
            "اختر المتجر الذي تريده:",
            reply_markup=get_store_keyboard()
        )
        return

    selected_store = context.user_data.get("selected_store")
    if selected_store is None:
        await update.message.reply_text(
            "اختر متجر أولاً:",
            reply_markup=get_store_keyboard()
        )
        return

    token = (
        os.getenv("SALLA_TOKEN")
        or os.getenv(f"SALLA_TOKEN_STORE_{selected_store}")
        or ACCESS_TOKEN
    )
    if not token:
        await update.message.reply_text("❌ لا يوجد توكن لهذا المتجر.")
        return

    # 1️⃣ TODAY ORDERS
    if "طلبات اليوم" in text:
        context.user_data.pop("search_mode", None)
        await update.message.reply_text("⏳ جاري جلب الطلبات...")

        orders = get_today_orders(token)

        if not orders:
            await update.message.reply_text("📦 لا توجد طلبات اليوم.")
            return

        message = "📦 طلبات اليوم:\n\n"
        acc = 0

        for order in orders:
            if order['total']['amount'] > 0:
                amount = order['total']['amount']
                source = " واتساب" if order['source'] == 'dashboard' else " موقع"
                details= get_order_details(token, order['id'])
                tags = details['tags']
                tag_names = ", ".join([tag["name"] for tag in tags])
                phone = order.get("customer", {}).get("mobile", "غير متوفر")
                message += f"Order: #<code>{order['reference_id']}</code> -   {source}\n{phone}\n"
                acc += float(amount)

        message += f"\n💰 Total: {round(acc,0)} SAR"
        await update.message.reply_text(message,  parse_mode="HTML")

    # 2️⃣ SEARCH BY ORDER NUMBER
    elif "البحث عن طلب" in text:
        context.user_data["search_mode"] = "order_number"
        await update.message.reply_text("ادخل رقم الطلب فضلا:")

    # 3️⃣ SEARCH BY PHONE NUMBER
    elif "📱 البحث برقم الهاتف" in text:
        context.user_data["search_mode"] = "phone_number"
        await update.message.reply_text("ادخل رقم الهاتف فضلا:")

    # 4️⃣ TODAY SALES MESSAGE
    elif "رسالة المبيعات" in text:
        context.user_data.pop("search_mode", None)
        await update.message.reply_text("⏳ جاري جلب الطلبات...")
        
        message1, message2 = get_today_orders_message(token)

        if message1.strip() == "":
            message1 = "📦 لا توجد طلبات اليوم."

        await update.message.reply_text(message1, parse_mode="HTML")
        await update.message.reply_text(message2, parse_mode="HTML")
    # HANDLE SEARCH INPUT
    else:
        search_mode = context.user_data.get("search_mode")

        if search_mode == "order_number" and text.isdigit():
            orders = get_order_by_number(token, int(text))
           
            if orders:
                message = f"📦 معلومات الطلب #{text} :\n\n"
                message = sending_message_to_customer(orders)
                await update.message.reply_text(message ,  parse_mode="HTML")
            else:
                await update.message.reply_text("❌ لا يُوجد طلب بهذا الرقم.")

         #   context.user_data.pop("search_mode", None)

        elif search_mode == "phone_number" :
            orders = get_orders_by_phone(token, text)

            if not orders:
                await update.message.reply_text("❌ لا يُوجد طلبات متعلقة بهذا الرقم.")
            else:
                message = "📦 الطلبات الخاصة بهذا الرقم:\n\n"

                message = sending_message_to_customer(orders)

                await update.message.reply_text(message,  parse_mode="HTML")


         #   context.user_data.pop("search_mode", None)

        else:
            await update.message.reply_text("الرجاء إدخال رقم صالح أو اختيار خيار من القائمة.")


# ==============================
# MAIN
# ==============================

def run_telegram_bot():
    if not TELEGRAM_TOKEN:
        print("⚠️ Telegram bot disabled: missing TELEGRAM_BOT_TOKEN")
        return

    lock_fd = acquire_bot_lock()
    if lock_fd is None:
        return

    try:
        app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
        global ACCESS_TOKEN
        try:
            ACCESS_TOKEN = get_access_token()
        except Exception as e:
            ACCESS_TOKEN = None
            print(f"⚠️ Telegram bot started without an initial Salla token: {e}")
        app.add_handler(CommandHandler("start", start_command))
        app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
        print("🤖 Telegram bot is ready. Open Telegram and send /start")
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        print(f"⚠️ Telegram bot failed to start: {e}")
    finally:
        release_bot_lock(lock_fd)


if __name__ == "__main__":
    run_telegram_bot()




