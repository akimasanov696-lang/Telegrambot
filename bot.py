import sqlite3
import logging
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ConversationHandler

logging.basicConfig(level=logging.ERROR)

TOKEN = "8628421331:AAEAIOxXY5yDo28JzNVABeF_NLTw3ft9iac"
ADMIN_IDS = [6165273503, 5910455056, 6524224796]
CHANNEL_ID = "@Grib_Gifts"
BOT_USERNAME = "@grib_stars_bot"

# ID приватного чата и топиков для логов
LOG_CHAT_ID = -1004368720192
WITHDRAW_THREAD_ID = 6   # тема "Выводы"
JOIN_THREAD_ID = 5       # тема "Логи заходов"
ACTIVATION_THREAD_ID = 2 # тема "Активация чеков"

WAITING_FOR_AVATAR = 1

class Database:
    def __init__(self, db_file="users.db"):
        self.db_file = db_file
        self._init_db()

    def _get_connection(self):
        conn = sqlite3.connect(self.db_file, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                stars INTEGER DEFAULT 0,
                invited_by INTEGER,
                reg_date TEXT,
                last_daily TEXT,
                level TEXT DEFAULT '🌱 Новичок'
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER,
                referred_id INTEGER,
                date TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS gift_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                gift_name TEXT,
                gift_emoji TEXT,
                stars_cost INTEGER,
                status TEXT DEFAULT 'pending',
                request_date TEXT,
                completed_date TEXT,
                output_message_id INTEGER
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bot_config (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        try:
            cursor.execute("ALTER TABLE gift_requests ADD COLUMN output_message_id INTEGER")
        except sqlite3.OperationalError:
            pass
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_stars ON users(stars)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_invited ON users(invited_by)")
        conn.commit()
        conn.close()
        print("✅ База данных готова")

    def get_user(self, user_id):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        conn.close()
        return dict(result) if result else None

    def create_user(self, user_id, username="", first_name=""):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO users (user_id, username, first_name, reg_date)
            VALUES (?, ?, ?, ?)
        """, (user_id, username, first_name, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return self.get_user(user_id)

    def get_or_create_user(self, user_id, username="", first_name=""):
        user = self.get_user(user_id)
        if not user:
            user = self.create_user(user_id, username, first_name)
        else:
            if username or first_name:
                conn = self._get_connection()
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE users SET username = ?, first_name = ? WHERE user_id = ?
                """, (username, first_name, user_id))
                conn.commit()
                conn.close()
                user = self.get_user(user_id)
        return user

    def add_stars(self, user_id, amount):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET stars = stars + ? WHERE user_id = ?", (amount, user_id))
        conn.commit()
        conn.close()
        self._update_level(user_id)

    def _update_level(self, user_id):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT stars FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        if result:
            stars = result[0]
            if stars >= 200:
                level = "🏆 Легенда"
            elif stars >= 100:
                level = "👑 VIP"
            elif stars >= 50:
                level = "⭐ Продвинутый"
            elif stars >= 20:
                level = "🌟 Друг"
            else:
                level = "🌱 Новичок"
            cursor.execute("UPDATE users SET level = ? WHERE user_id = ?", (level, user_id))
            conn.commit()
        conn.close()

    def process_referral(self, referrer_id, referred_id):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM referrals WHERE referrer_id = ? AND referred_id = ?", (referrer_id, referred_id))
        if cursor.fetchone():
            conn.close()
            return False
        cursor.execute("""
            INSERT INTO referrals (referrer_id, referred_id, date)
            VALUES (?, ?, ?)
        """, (referrer_id, referred_id, datetime.now().isoformat()))
        self.add_stars(referrer_id, 2)
        conn.commit()
        conn.close()
        return True

    def get_referrals_count(self, user_id):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", (user_id,))
        count = cursor.fetchone()[0]
        conn.close()
        return count

    def get_total_users(self):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        count = cursor.fetchone()[0]
        conn.close()
        return count

    def get_referrals_activated(self, user_id):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) FROM referrals 
            WHERE referrer_id = ? AND referred_id IN (SELECT user_id FROM users)
        """, (user_id,))
        count = cursor.fetchone()[0]
        conn.close()
        return count

    def create_gift_request(self, user_id, gift_name, gift_emoji, stars_cost):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO gift_requests (user_id, gift_name, gift_emoji, stars_cost, request_date)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, gift_name, gift_emoji, stars_cost, datetime.now().isoformat()))
        request_id = cursor.lastrowid
        conn.commit()
        conn.close()
        self.add_stars(user_id, -stars_cost)
        return request_id

    def set_gift_output_message_id(self, request_id, message_id):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE gift_requests SET output_message_id = ? WHERE id = ?", (message_id, request_id))
        conn.commit()
        conn.close()

    def get_pending_requests(self):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM gift_requests WHERE status = 'pending' ORDER BY request_date ASC")
        result = cursor.fetchall()
        conn.close()
        return [dict(row) for row in result]

    def get_all_gift_requests(self):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM gift_requests ORDER BY request_date DESC")
        result = cursor.fetchall()
        conn.close()
        return [dict(row) for row in result]

    def complete_request(self, request_id):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE gift_requests SET status = 'completed', completed_date = ?
            WHERE id = ?
        """, (datetime.now().isoformat(), request_id))
        conn.commit()
        conn.close()

    def reject_request(self, request_id):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM gift_requests WHERE id = ?", (request_id,))
        req = cursor.fetchone()
        if not req or req['status'] != 'pending':
            conn.close()
            return None
        self.add_stars(req['user_id'], req['stars_cost'])
        cursor.execute("UPDATE gift_requests SET status = 'rejected', completed_date = ? WHERE id = ?",
                       (datetime.now().isoformat(), request_id))
        conn.commit()
        conn.close()
        return dict(req)

    def get_user_gift_requests(self, user_id):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM gift_requests WHERE user_id = ? ORDER BY request_date DESC", (user_id,))
        result = cursor.fetchall()
        conn.close()
        return [dict(row) for row in result]

    def get_config(self, key, default=None):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM bot_config WHERE key = ?", (key,))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else default

    def set_config(self, key, value):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO bot_config (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
        conn.close()

db = Database()

def get_referral_link(user_id):
    return f"https://t.me/grib_stars_bot?start=ref_{user_id}"

async def check_subscription(user_id, context):
    try:
        member = await context.bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ["member", "administrator", "creator"]
    except:
        return False

async def edit_or_reply(query, text, reply_markup=None):
    try:
        if query.message.photo or query.message.caption:
            await query.edit_message_caption(caption=text, reply_markup=reply_markup, parse_mode='HTML')
        else:
            await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode='HTML')
    except Exception as e:
        if "Message is not modified" in str(e):
            pass
        else:
            await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode='HTML')

async def show_main_menu(update, context):
    user_id = update.effective_user.id
    avatar_file_id = db.get_config("avatar_file_id")
    
    keyboard = [
        [InlineKeyboardButton("👤 Профиль", callback_data="profile")],
        [
            InlineKeyboardButton("⭐ Заработать звёзды", callback_data="earn_stars"),
            InlineKeyboardButton("🎁 Вывести подарки", callback_data="gift_shop")
        ],
        [
            InlineKeyboardButton("📦 Выведено", callback_data="withdrawn"),
            InlineKeyboardButton("📄 Чеки", callback_data="checks")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    caption = "<b>🍄 Добро пожаловать в @Grib_Stars_Bot!\nВыберите действие:</b>"
    
    try:
        if avatar_file_id:
            await update.message.reply_photo(photo=avatar_file_id, caption=caption, reply_markup=reply_markup, parse_mode='HTML')
        else:
            await update.message.reply_text(caption, reply_markup=reply_markup, parse_mode='HTML')
    except:
        await update.message.reply_text(caption, reply_markup=reply_markup, parse_mode='HTML')

async def start(update: Update, context):
    user = update.effective_user
    user_id = user.id
    username = user.username or ""
    first_name = user.first_name or ""

    is_subscribed = await check_subscription(user_id, context)
    
    if not is_subscribed:
        keyboard = [
            [InlineKeyboardButton("📢 Подписаться на канал", url="https://t.me/Grib_Gifts")],
            [InlineKeyboardButton("✅ Я подписался", callback_data="check_sub")]
        ]
        await update.message.reply_text(
            f"<b>🍄 Привет, {first_name}!\n\n"
            f"Добро пожаловать в @Grib_Stars_Bot!\n\n"
            f"Чтобы начать, подпишись на наш канал:\n"
            f"👉 {CHANNEL_ID}\n\n"
            f"После подписки нажми кнопку ниже 👇</b>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )
        return

    user_data = db.get_or_create_user(user_id, username, first_name)
    referrer_id = None
    if context.args:
        try:
            ref_id = int(context.args[0].replace("ref_", ""))
            if ref_id != user_id and db.get_user(ref_id):
                if db.process_referral(ref_id, user_id):
                    referrer_id = ref_id
                    try:
                        await context.bot.send_message(
                            chat_id=ref_id,
                            text=f"<b>🍄 По твоей ссылке присоединился новый друг!\n⭐ +2 звёзды!</b>",
                            parse_mode='HTML'
                        )
                    except:
                        pass
        except:
            pass

    # Лог захода
    user_link = f"@{username}" if username else f"<a href='tg://user?id={user_id}'>{first_name}</a>"
    log_text = f"🆕 Новый пользователь: {user_link} (ID: <code>{user_id}</code>)"
    if referrer_id:
        ref_user = db.get_user(referrer_id)
        if ref_user:
            ref_link = f"@{ref_user['username']}" if ref_user['username'] else f"ID {referrer_id}"
            log_text += f"\n👥 Пригласил: {ref_link}"
    try:
        await context.bot.send_message(
            chat_id=LOG_CHAT_ID,
            message_thread_id=JOIN_THREAD_ID,
            text=log_text,
            parse_mode='HTML'
        )
    except:
        pass

    await show_main_menu(update, context)

async def check_subscription_callback(update: Update, context):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    is_subscribed = await check_subscription(user_id, context)
    
    if is_subscribed:
        user = query.from_user
        db.get_or_create_user(user_id, user.username or "", user.first_name or "")
        
        keyboard = [
            [InlineKeyboardButton("👤 Профиль", callback_data="profile")],
            [
                InlineKeyboardButton("⭐ Заработать звёзды", callback_data="earn_stars"),
                InlineKeyboardButton("🎁 Вывести подарки", callback_data="gift_shop")
            ],
            [
                InlineKeyboardButton("📦 Выведено", callback_data="withdrawn"),
                InlineKeyboardButton("📄 Чеки", callback_data="checks")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        caption = "<b>🍄 Добро пожаловать в @Grib_Stars_Bot!\nВыберите действие:</b>"
        await edit_or_reply(query, caption, reply_markup)
    else:
        await query.edit_message_text(
            f"<b>❌ Ты ещё не подписался на канал!\n\n"
            f"Перейди по ссылке и подпишись:\n"
            f"👉 {CHANNEL_ID}</b>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 Подписаться", url="https://t.me/Grib_Gifts")],
                [InlineKeyboardButton("✅ Проверить", callback_data="check_sub")]
            ]),
            parse_mode='HTML'
        )

async def button_handler(update: Update, context):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    user_data = db.get_user(user_id)
    
    # --- Админские кнопки ---
    if query.data == "admin_panel_back":
        if user_id not in ADMIN_IDS:
            await query.answer("⛔ Нет доступа")
            return
        keyboard = [
            [InlineKeyboardButton("📦 Выводы", callback_data="admin_withdrawals")],
            [InlineKeyboardButton("📄 Чеки", callback_data="admin_checks")],
            [InlineKeyboardButton("📨 Рассылка", callback_data="admin_broadcast")],
            [InlineKeyboardButton("🚫 Блок", callback_data="admin_block")],
            [InlineKeyboardButton("📋 Тестовые логи", callback_data="admin_logs")],
            [InlineKeyboardButton("❌ Закрыть", callback_data="close_panel")]
        ]
        await edit_or_reply(query, "<b>🛠️ Админ панель</b>", InlineKeyboardMarkup(keyboard))
        return

    if query.data == "close_panel":
        if user_id not in ADMIN_IDS:
            await query.answer("⛔ Нет доступа")
            return
        try:
            await query.message.delete()
        except:
            await query.edit_message_text("<b>Панель закрыта.</b>", parse_mode='HTML')
        return

    if query.data == "admin_withdrawals":
        if user_id not in ADMIN_IDS:
            await query.answer("⛔ Нет доступа")
            return
        requests = db.get_pending_requests()
        if not requests:
            text = "<b>📭 Нет новых заявок.</b>"
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="admin_panel_back")]]
            await edit_or_reply(query, text, InlineKeyboardMarkup(keyboard))
            return

        text = "<b>📦 ВЫВОДЫ (ожидают):</b>\n\n"
        keyboard = []
        for req in requests:
            text += (
                f"🆔 <b>Заявка #{req['id']}</b>\n"
                f"👤 Пользователь: {req['user_id']}\n"
                f"{req['gift_emoji']} Подарок: {req['gift_name']}\n"
                f"⭐ Цена: {req['stars_cost']}\n"
                f"📅 {req['request_date'][:16]}\n\n"
            )
            keyboard.append([
                InlineKeyboardButton("✅ Принять", callback_data=f"accept_{req['id']}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{req['id']}")
            ])
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="admin_panel_back")])
        await edit_or_reply(query, text, InlineKeyboardMarkup(keyboard))
        return

    if query.data.startswith("accept_") or query.data.startswith("reject_"):
        if user_id not in ADMIN_IDS:
            await query.answer("⛔ Нет доступа")
            return
        action, req_id_str = query.data.split("_")
        request_id = int(req_id_str)
        if action == "accept":
            conn = db._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM gift_requests WHERE id = ? AND status = 'pending'", (request_id,))
            req = cursor.fetchone()
            conn.close()
            if not req:
                await query.answer("Заявка уже не актуальна")
                return
            db.complete_request(request_id)
            gift = dict(req)
            try:
                await context.bot.send_message(
                    chat_id=gift['user_id'],
                    text=f"<b>🎉 Поздравляем! Ваша заявка на {gift['gift_emoji']} {gift['gift_name']} принята!\n</b>"
                         f"<b>Ожидайте начисления подарка от администратора.</b>",
                    parse_mode='HTML'
                )
            except:
                pass
            pending_count = len(db.get_pending_requests())
            req_user = db.get_user(gift['user_id'])
            user_link = f"@{req_user['username']}" if req_user and req_user['username'] else f"<a href='tg://user?id={gift['user_id']}'>{req_user['first_name'] if req_user else 'Пользователь'}</a>"
            if gift['output_message_id']:
                check_link = f"https://t.me/c/{str(LOG_CHAT_ID)[4:]}/{gift['output_message_id']}"
            else:
                check_link = "чек (нет ссылки)"
            text_log = (
                f"🎁 Пользователь {user_link} (ID: <code>{gift['user_id']}</code>) "
                f"активировал <a href='{check_link}'>чек</a> и получил {gift['stars_cost']} ⭐\n"
                f"Осталось активаций: {pending_count}"
            )
            try:
                await context.bot.send_message(
                    chat_id=LOG_CHAT_ID,
                    message_thread_id=ACTIVATION_THREAD_ID,
                    text=text_log,
                    parse_mode='HTML'
                )
            except:
                pass
            await query.answer("Заявка принята")
        else:  # reject
            req = db.reject_request(request_id)
            if not req:
                await query.answer("Заявка уже не актуальна")
                return
            try:
                await context.bot.send_message(
                    chat_id=req['user_id'],
                    text=f"<b>❌ Ваша заявка на {req['gift_emoji']} {req['gift_name']} отклонена.</b>\n"
                         f"<b>Звёзды возвращены на баланс.</b>",
                    parse_mode='HTML'
                )
            except:
                pass
            await query.answer("Заявка отклонена")
        # Обновляем список
        await button_handler(update, context)
        return

    if query.data == "admin_checks":
        if user_id not in ADMIN_IDS:
            await query.answer("⛔ Нет доступа")
            return
        all_reqs = db.get_all_gift_requests()
        if not all_reqs:
            text = "<b>📄 Заявок пока нет.</b>"
        else:
            text = "<b>📄 Все заявки:</b>\n\n"
            for req in all_reqs:
                status_map = {'completed': '✅ Выполнен', 'rejected': '❌ Отклонён', 'pending': '🟢 Ожидает'}
                status = status_map.get(req['status'], req['status'])
                text += f"<b>🆔 {req['id']} {req['gift_emoji']} {req['gift_name']} ({req['stars_cost']}⭐) — {status}</b>\n"
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="admin_panel_back")]]
        await edit_or_reply(query, text, InlineKeyboardMarkup(keyboard))
        return

    if query.data in ["admin_broadcast", "admin_block", "admin_logs"]:
        if user_id not in ADMIN_IDS:
            await query.answer("⛔ Нет доступа")
            return
        messages = {
            "admin_broadcast": "📨 Рассылка пока в разработке",
            "admin_block": "🚫 Блокировка пока в разработке",
            "admin_logs": "📋 Логи пока в разработке"
        }
        await query.answer(messages[query.data])
        return

    # --- Обычные кнопки ---
    if query.data == "profile":
        referrals = db.get_referrals_count(user_id)
        activated = db.get_referrals_activated(user_id)
        name = user_data['first_name'] or "Не указано"
        username = query.from_user.username or "Не указан"
        text = (
            f"<b>👤 Профиль\n\n"
            f"💬 Имя: {name}\n"
            f"👤 Username: @{username}\n"
            f"🆔 ID: {user_id}\n\n"
            f"🔗 Твоя реф ссылка:\n"
            f"{get_referral_link(user_id)}\n\n"
            f"👥 Всего друзей: {referrals}\n"
            f"✅ Активировали бота: {activated}\n"
            f"💰 Баланс: ⭐ {user_data['stars']}</b>"
        )
        keyboard = [
            [InlineKeyboardButton("📤 Поделиться ссылкой", switch_inline_query=f"🍄 GRIB STARS — зарабатывай звёзды! {get_referral_link(user_id)}")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]
        ]
        await edit_or_reply(query, text, InlineKeyboardMarkup(keyboard))
        return

    elif query.data == "earn_stars":
        text = (
            f"<b>⭐ Заработать звёзды\n\n"
            f"📤 Приглашай друзей по своей ссылке\n"
            f"  → Ты получаешь 2 ⭐ за каждого друга</b>"
        )
        keyboard = [
            [InlineKeyboardButton("📤 Поделиться ссылкой", switch_inline_query=f"🍄 GRIB STARS — зарабатывай звёзды! {get_referral_link(user_id)}")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]
        ]
        await edit_or_reply(query, text, InlineKeyboardMarkup(keyboard))
        return

    elif query.data == "withdrawn":
        orders = db.get_user_gift_requests(user_id)
        completed = [o for o in orders if o['status'] == 'completed']
        if not completed:
            text = "<b>📦 У тебя пока нет выполненных выводов.\n\nПродолжай зарабатывать звёзды! ⭐</b>"
        else:
            text = "<b>📦 Твои выводы:\n\n</b>"
            for order in completed:
                text += f"<b>{order['gift_emoji']} {order['gift_name']} — {order['stars_cost']} ⭐\n</b>"
                text += "<b>  ✅ Выполнен: {}</b>\n\n".format(order['completed_date'][:10])
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]]
        await edit_or_reply(query, text, InlineKeyboardMarkup(keyboard))
        return

    elif query.data == "checks":
        orders = db.get_user_gift_requests(user_id)
        pending = [o for o in orders if o['status'] == 'pending']
        if not pending:
            text = "<b>📄 У тебя нет активных заявок.\n\nХочешь получить подарок? Перейди в магазин! 🎁</b>"
        else:
            text = "<b>📄 Активные заявки:\n\n</b>"
            for order in pending:
                text += f"<b>{order['gift_emoji']} {order['gift_name']} — {order['stars_cost']} ⭐\n</b>"
                text += "<b>  🟢 Статус: Ожидает\n</b>"
                text += "<b>  📅 От: {}</b>\n\n".format(order['request_date'][:10])
        keyboard = [
            [InlineKeyboardButton("🎁 Магазин", callback_data="gift_shop")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]
        ]
        await edit_or_reply(query, text, InlineKeyboardMarkup(keyboard))
        return

    elif query.data == "gift_shop":
        gifts = [
            {"emoji": "💝", "name": "Сердце", "price": 15},
            {"emoji": "🧸", "name": "Мишка", "price": 15},
            {"emoji": "🌹", "name": "Роза", "price": 25},
            {"emoji": "🎁", "name": "Подарок", "price": 25},
            {"emoji": "🍾", "name": "Шампанское", "price": 50},
            {"emoji": "💐", "name": "Цветы", "price": 50},
            {"emoji": "🚀", "name": "Ракета", "price": 50},
            {"emoji": "🎂", "name": "Торт", "price": 100},
            {"emoji": "💍", "name": "Кольцо", "price": 100},
            {"emoji": "💎", "name": "Бриллиант", "price": 100},
            {"emoji": "🏆", "name": "Кубок", "price": 200}
        ]
        text = (
            f"<b>💰 Баланс: {user_data['stars']} ⭐\n\n"
            f"📌 Для вывода требуется минимум 15 ⭐\n\n"
            f"🎁 Выберите подарок:</b>"
        )
        keyboard = []
        row = []
        for gift in gifts:
            btn_text = f"{gift['emoji']} {gift['name']} ({gift['price']} ⭐)"
            callback_data = f"buy_{gift['name']}_{gift['price']}_{gift['emoji']}"
            row.append(InlineKeyboardButton(btn_text, callback_data=callback_data))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")])
        await edit_or_reply(query, text, InlineKeyboardMarkup(keyboard))
        return

    elif query.data == "back_to_menu":
        keyboard = [
            [InlineKeyboardButton("👤 Профиль", callback_data="profile")],
            [
                InlineKeyboardButton("⭐ Заработать звёзды", callback_data="earn_stars"),
                InlineKeyboardButton("🎁 Вывести подарки", callback_data="gift_shop")
            ],
            [
                InlineKeyboardButton("📦 Выведено", callback_data="withdrawn"),
                InlineKeyboardButton("📄 Чеки", callback_data="checks")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        caption = "<b>🍄 Добро пожаловать в @Grib_Stars_Bot!\nВыберите действие:</b>"
        await edit_or_reply(query, caption, reply_markup)
        return

    elif query.data.startswith("buy_"):
        data = query.data.split("_")
        if len(data) >= 4:
            gift_name = data[1]
            gift_price = int(data[2])
            gift_emoji = data[3]
            
            if user_data['stars'] < gift_price:
                text = (
                    f"<b>❌ Недостаточно звёзд!\n\n"
                    f"Нужно: {gift_price} ⭐\n"
                    f"У тебя: {user_data['stars']} ⭐\n\n"
                    f"Пригласи друзей, чтобы заработать больше!</b>"
                )
                keyboard = [[InlineKeyboardButton("🔙 Магазин", callback_data="gift_shop")]]
                await edit_or_reply(query, text, InlineKeyboardMarkup(keyboard))
                return
            
            request_id = db.create_gift_request(user_id, gift_name, gift_emoji, gift_price)
            user_data = db.get_user(user_id)
            
            try:
                msg = await context.bot.send_message(
                    chat_id=LOG_CHAT_ID,
                    message_thread_id=WITHDRAW_THREAD_ID,
                    text=f"📦 Новая заявка #{request_id}\n"
                         f"👤 Пользователь: @{query.from_user.username or query.from_user.first_name} ({user_id})\n"
                         f"{gift_emoji} Подарок: {gift_name}\n"
                         f"⭐ Цена: {gift_price}",
                    parse_mode='HTML'
                )
                db.set_gift_output_message_id(request_id, msg.message_id)
            except:
                pass
            
            text = (
                f"<b>✅ Заявка оформлена!\n\n"
                f"{gift_emoji} {gift_name}\n"
                f"⭐ Потрачено: {gift_price} ⭐\n"
                f"⭐ Осталось: {user_data['stars']}\n\n"
                f"⏳ Ожидай, админ свяжется с тобой!</b>"
            )
            keyboard = [[InlineKeyboardButton("🔙 В меню", callback_data="back_to_menu")]]
            await edit_or_reply(query, text, InlineKeyboardMarkup(keyboard))
            return

# Админская команда /panel
async def panel_command(update: Update, context):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("<b>⛔ У тебя нет доступа!</b>", parse_mode='HTML')
        return
    keyboard = [
        [InlineKeyboardButton("📦 Выводы", callback_data="admin_withdrawals")],
        [InlineKeyboardButton("📄 Чеки", callback_data="admin_checks")],
        [InlineKeyboardButton("📨 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton("🚫 Блок", callback_data="admin_block")],
        [InlineKeyboardButton("📋 Тестовые логи", callback_data="admin_logs")],
        [InlineKeyboardButton("❌ Закрыть", callback_data="close_panel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("<b>🛠️ Админ панель</b>", reply_markup=reply_markup, parse_mode='HTML')

# Команда /givestars
async def givestars(update: Update, context):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("<b>⛔ У тебя нет доступа!</b>", parse_mode='HTML')
        return
    if not context.args:
        await update.message.reply_text("<b>❌ Укажи количество звёзд.\nПример: /givestars 1000</b>", parse_mode='HTML')
        return
    try:
        amount = int(context.args[0])
    except:
        await update.message.reply_text("<b>❌ Неверное число!</b>", parse_mode='HTML')
        return
    db.add_stars(user_id, amount)
    await update.message.reply_text(f"<b>✅ Выдано {amount} ⭐!</b>", parse_mode='HTML')

async def setavatar_start(update: Update, context):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("<b>⛔ У тебя нет доступа!</b>", parse_mode='HTML')
        return ConversationHandler.END
    await update.message.reply_text("<b>📸 Отправь мне фото, которое будет аватаркой бота.</b>", parse_mode='HTML')
    return WAITING_FOR_AVATAR

async def setavatar_photo(update: Update, context):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("<b>⛔ У тебя нет доступа!</b>", parse_mode='HTML')
        return ConversationHandler.END
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        db.set_config("avatar_file_id", file_id)
        await update.message.reply_text("<b>✅ Аватарка успешно сохранена!</b>", parse_mode='HTML')
    else:
        await update.message.reply_text("<b>❌ Пожалуйста, отправь фото.</b>", parse_mode='HTML')
        return WAITING_FOR_AVATAR
    return ConversationHandler.END

async def setavatar_cancel(update: Update, context):
    await update.message.reply_text("<b>❌ Операция отменена.</b>", parse_mode='HTML')
    return ConversationHandler.END

async def delavatar(update: Update, context):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("<b>⛔ У тебя нет доступа!</b>", parse_mode='HTML')
        return
    db.set_config("avatar_file_id", None)
    await update.message.reply_text("<b>🗑️ Аватарка удалена.</b>", parse_mode='HTML')

async def complete_gift(update: Update, context):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("<b>⛔ У тебя нет доступа!</b>", parse_mode='HTML')
        return
    if not context.args:
        await update.message.reply_text("<b>❌ Укажи ID заявки.\nПример: /complete 1</b>", parse_mode='HTML')
        return
    try:
        request_id = int(context.args[0])
    except:
        await update.message.reply_text("<b>❌ Неверный ID!</b>", parse_mode='HTML')
        return
    conn = db._get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM gift_requests WHERE id = ? AND status = 'pending'", (request_id,))
    req = cursor.fetchone()
    conn.close()
    if not req:
        await update.message.reply_text("<b>❌ Заявка не найдена или уже выполнена!</b>", parse_mode='HTML')
        return
    db.complete_request(request_id)
    pending_count = len(db.get_pending_requests())
    gift = dict(req)
    req_user = db.get_user(gift['user_id'])
    user_link = f"@{req_user['username']}" if req_user and req_user['username'] else f"<a href='tg://user?id={gift['user_id']}'>{req_user['first_name'] if req_user else 'Пользователь'}</a>"
    if gift['output_message_id']:
        check_link = f"https://t.me/c/{str(LOG_CHAT_ID)[4:]}/{gift['output_message_id']}"
    else:
        check_link = "чек (нет ссылки)"
    text = (
        f"🎁 Пользователь {user_link} (ID: <code>{gift['user_id']}</code>) "
        f"активировал <a href='{check_link}'>чек</a> и получил {gift['stars_cost']} ⭐\n"
        f"Осталось активаций: {pending_count}"
    )
    try:
        await context.bot.send_message(
            chat_id=LOG_CHAT_ID,
            message_thread_id=ACTIVATION_THREAD_ID,
            text=text,
            parse_mode='HTML'
        )
    except:
        pass
    try:
        await context.bot.send_message(
            chat_id=gift['user_id'],
            text=f"<b>🎉 Поздравляем! Ваша заявка на {gift['gift_emoji']} {gift['gift_name']} принята!\n</b>"
                 f"<b>Ожидайте начисления подарка от администратора.</b>",
            parse_mode='HTML'
        )
    except:
        pass
    await update.message.reply_text(f"<b>✅ Заявка #{request_id} выполнена!</b>", parse_mode='HTML')

async def echo(update: Update, context):
    if update.effective_chat.id == LOG_CHAT_ID:
        return
    await update.message.reply_text("<b>🍄 Используй /start для входа в GRIB STARS</b>", parse_mode='HTML')

def main():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("complete", complete_gift))
    app.add_handler(CommandHandler("delavatar", delavatar))
    app.add_handler(CommandHandler("panel", panel_command))
    app.add_handler(CommandHandler("givestars", givestars))
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("setavatar", setavatar_start)],
        states={WAITING_FOR_AVATAR: [MessageHandler(filters.PHOTO, setavatar_photo)]},
        fallbacks=[CommandHandler("cancel", setavatar_cancel)],
    )
    app.add_handler(conv_handler)
    
    app.add_handler(CallbackQueryHandler(check_subscription_callback, pattern="check_sub"))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
    
    print("=" * 50)
    print("🍄 GRIB STARS БОТ ЗАПУЩЕН!")
    print("=" * 50)
    print(f"🤖 Бот: {BOT_USERNAME}")
    print(f"📢 Канал: {CHANNEL_ID}")
    print(f"📊 База данных: users.db")
    print("=" * 50)
    print("🎁 11 подарков в магазине")
    print("⭐ Реферальная система (2⭐ за друга)")
    print("📦 Вывод звёзд")
    print("📸 Команда /setavatar (для админа)")
    print("💰 Команда /givestars (для админа)")
    print(f"👥 Админы: {', '.join(str(x) for x in ADMIN_IDS)}")
    print("=" * 50)
    
    app.run_polling()

if __name__ == "__main__":
    main()
