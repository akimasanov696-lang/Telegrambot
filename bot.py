import sqlite3
import logging
import random
import os
import string
import html
import time
from datetime import datetime
from functools import wraps
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ConversationHandler,
)

# ---------- Настройки ----------
logging.basicConfig(level=logging.INFO)

TOKEN = "8628421331:AAGSIMWNK-iomg-bY2vopi6_o2eHm8ern5g"  # Замените на свой
ADMIN_IDS = [6165273503, 5910455056, 6524224796]
CHANNEL_ID = "@Grib_Gifts"
BOT_USERNAME = "@grib_stars_bot"
LOG_CHAT_ID = -1004368720192
WITHDRAW_THREAD_ID = 6
JOIN_THREAD_ID = 5
ACTIVATION_THREAD_ID = 2

WAITING_FOR_AVATAR = 1
CAPTCHA = 2
CHECK_ACTIVATIONS, CHECK_STARS, CHECK_PASSWORD, WAITING_CHECK_PASSWORD = range(10, 14)

captcha_storage = {}

# ---------- Декоратор повторных попыток для БД ----------
def db_retry(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        for attempt in range(3):
            try:
                return func(*args, **kwargs)
            except sqlite3.OperationalError:
                if attempt == 2:
                    raise
                time.sleep(0.1)
    return wrapper

# ---------- База данных ----------
class Database:
    def __init__(self, db_file="users.db"):
        self.db_file = db_file
        self._init_db()

    def _get_connection(self):
        conn = sqlite3.connect(self.db_file, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                stars REAL DEFAULT 0,
                invited_by INTEGER,
                reg_date TEXT,
                last_daily TEXT,
                level TEXT DEFAULT '🌱 Новичок'
            );
            CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER,
                referred_id INTEGER,
                date TEXT,
                UNIQUE(referrer_id, referred_id)
            );
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
            );
            CREATE TABLE IF NOT EXISTS bot_config (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS texts (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_admin_id INTEGER,
                code TEXT UNIQUE,
                password TEXT DEFAULT '',
                max_activations INTEGER,
                current_activations INTEGER DEFAULT 0,
                stars_per_activation REAL,
                is_active INTEGER DEFAULT 1,
                created_date TEXT,
                output_message_id INTEGER
            );
        """)
        try:
            cursor.execute("ALTER TABLE checks ADD COLUMN output_message_id INTEGER")
        except sqlite3.OperationalError:
            pass
        conn.commit()
        conn.close()

    # ---------- Тексты ----------
    def get_text(self, key, default=""):
        conn = self._get_connection()
        row = conn.execute("SELECT value FROM texts WHERE key = ?", (key,)).fetchone()
        conn.close()
        return row[0] if row else default

    def set_text(self, key, value):
        conn = self._get_connection()
        conn.execute("INSERT OR REPLACE INTO texts (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
        conn.close()

    # ---------- Пользователи ----------
    def get_user(self, user_id):
        conn = self._get_connection()
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def create_user(self, user_id, username="", first_name=""):
        conn = self._get_connection()
        conn.execute("INSERT OR IGNORE INTO users (user_id, username, first_name, reg_date) VALUES (?, ?, ?, ?)",
                     (user_id, username, first_name, datetime.now().isoformat()))
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
                conn.execute("UPDATE users SET username = ?, first_name = ? WHERE user_id = ?",
                             (username, first_name, user_id))
                conn.commit()
                conn.close()
                user = self.get_user(user_id)
        return user

    @db_retry
    def add_stars(self, user_id, amount):
        conn = self._get_connection()
        conn.execute("UPDATE users SET stars = stars + ? WHERE user_id = ?", (amount, user_id))
        conn.commit()
        conn.close()
        self._update_level(user_id)

    def _update_level(self, user_id):
        conn = self._get_connection()
        row = conn.execute("SELECT stars FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if row:
            stars = row[0]
            if stars >= 200:   level = "🏆 Легенда"
            elif stars >= 100: level = "👑 VIP"
            elif stars >= 50:  level = "⭐ Продвинутый"
            elif stars >= 20:  level = "🌟 Друг"
            else:              level = "🌱 Новичок"
            conn.execute("UPDATE users SET level = ? WHERE user_id = ?", (level, user_id))
            conn.commit()
        conn.close()

    # ---------- Рефералы ----------
    def process_referral(self, referrer_id, referred_id):
        """
        Возвращает:
        - True, "new" — успешное начисление (новый пользователь)
        - False, "already_referred" — этот referred уже был кем-то приглашён
        - False, "already_same" — эта пара уже существует
        """
        conn = self._get_connection()
        if conn.execute("SELECT id FROM referrals WHERE referred_id = ?", (referred_id,)).fetchone():
            conn.close()
            return False, "already_referred"
        conn.execute("INSERT OR IGNORE INTO referrals (referrer_id, referred_id, date) VALUES (?, ?, ?)",
                     (referrer_id, referred_id, datetime.now().isoformat()))
        if conn.total_changes == 0:
            conn.close()
            return False, "already_same"
        conn.commit()
        conn.close()
        self.add_stars(referrer_id, 2)
        return True, "new"

    def get_referrals_count(self, user_id):
        conn = self._get_connection()
        count = conn.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", (user_id,)).fetchone()[0]
        conn.close()
        return count

    def get_referrals_activated(self, user_id):
        conn = self._get_connection()
        count = conn.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", (user_id,)).fetchone()[0]
        conn.close()
        return count

    # ---------- Подарки ----------
    def create_gift_request(self, user_id, gift_name, gift_emoji, stars_cost):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO gift_requests (user_id, gift_name, gift_emoji, stars_cost, request_date) VALUES (?, ?, ?, ?, ?)",
                       (user_id, gift_name, gift_emoji, stars_cost, datetime.now().isoformat()))
        request_id = cursor.lastrowid
        conn.commit()
        conn.close()
        self.add_stars(user_id, -stars_cost)
        return request_id

    def set_gift_output_message_id(self, request_id, message_id):
        conn = self._get_connection()
        conn.execute("UPDATE gift_requests SET output_message_id = ? WHERE id = ?", (message_id, request_id))
        conn.commit()
        conn.close()

    def get_pending_requests(self):
        conn = self._get_connection()
        rows = conn.execute("SELECT * FROM gift_requests WHERE status = 'pending' ORDER BY request_date ASC").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_all_gift_requests(self):
        conn = self._get_connection()
        rows = conn.execute("SELECT * FROM gift_requests ORDER BY request_date DESC").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def complete_request(self, request_id):
        conn = self._get_connection()
        conn.execute("UPDATE gift_requests SET status = 'completed', completed_date = ? WHERE id = ?",
                     (datetime.now().isoformat(), request_id))
        conn.commit()
        conn.close()

    def reject_request(self, request_id):
        conn = self._get_connection()
        req = conn.execute("SELECT * FROM gift_requests WHERE id = ? AND status = 'pending'", (request_id,)).fetchone()
        if not req:
            conn.close()
            return None
        self.add_stars(req['user_id'], req['stars_cost'])
        conn.execute("UPDATE gift_requests SET status = 'rejected', completed_date = ? WHERE id = ?",
                     (datetime.now().isoformat(), request_id))
        conn.commit()
        conn.close()
        return dict(req)

    def get_user_gift_requests(self, user_id):
        conn = self._get_connection()
        rows = conn.execute("SELECT * FROM gift_requests WHERE user_id = ? ORDER BY request_date DESC", (user_id,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ---------- Конфиг ----------
    def get_config(self, key, default=None):
        conn = self._get_connection()
        row = conn.execute("SELECT value FROM bot_config WHERE key = ?", (key,)).fetchone()
        conn.close()
        return row[0] if row else default

    def set_config(self, key, value):
        conn = self._get_connection()
        conn.execute("INSERT OR REPLACE INTO bot_config (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
        conn.close()

    # ---------- Чеки ----------
    def create_check(self, creator_admin_id, password, max_activations, stars_per_activation):
        code = ''.join(random.choices(string.ascii_letters + string.digits, k=16))
        conn = self._get_connection()
        conn.execute("INSERT INTO checks (creator_admin_id, code, password, max_activations, stars_per_activation, created_date) VALUES (?, ?, ?, ?, ?, ?)",
                     (creator_admin_id, code, password, max_activations, stars_per_activation, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return code

    def set_check_output_message_id(self, code, message_id):
        conn = self._get_connection()
        conn.execute("UPDATE checks SET output_message_id = ? WHERE code = ?", (message_id, code))
        conn.commit()
        conn.close()

    def get_check_by_code(self, code):
        conn = self._get_connection()
        row = conn.execute("SELECT * FROM checks WHERE code = ?", (code,)).fetchone()
        conn.close()
        return dict(row) if row else None

    @db_retry
    def activate_check(self, code):
        conn = self._get_connection()
        conn.execute("BEGIN IMMEDIATE")
        check = conn.execute("SELECT * FROM checks WHERE code = ? AND is_active = 1", (code,)).fetchone()
        if not check or check['current_activations'] >= check['max_activations']:
            conn.execute("ROLLBACK")
            conn.close()
            return None
        conn.execute("UPDATE checks SET current_activations = current_activations + 1 WHERE code = ?", (code,))
        conn.execute("COMMIT")
        stars = check['stars_per_activation']
        conn.close()
        return stars

db = Database()

# ---------- Вспомогательные функции ----------
def get_referral_link(user_id):
    return f"https://t.me/{BOT_USERNAME.lstrip('@')}?start=ref_{user_id}"

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
        if "Message is not modified" not in str(e):
            logging.error(f"Ошибка редактирования: {e}")

# ---------- Клавиатуры ----------
def main_menu_keyboard():
    return [
        [InlineKeyboardButton("👤 Профиль", callback_data="profile")],
        [
            InlineKeyboardButton("⭐ Заработать", callback_data="earn_stars"),
            InlineKeyboardButton("🎁 Вывести", callback_data="gift_shop")
        ],
        [
            InlineKeyboardButton("📦 Выводы", callback_data="withdrawn"),
            InlineKeyboardButton("📄 Заявки", callback_data="checks")
        ]
    ]

def admin_panel_keyboard():
    return [
        [InlineKeyboardButton("📦 Выводы", callback_data="admin_withdrawals")],
        [InlineKeyboardButton("📄 Все заявки", callback_data="admin_checks")],
        [InlineKeyboardButton("🧾 Новый чек", callback_data="admin_create_check")],
        [InlineKeyboardButton("🚫 Бан", callback_data="admin_block")],
        [InlineKeyboardButton("📋 Логи", callback_data="admin_logs")],
        [InlineKeyboardButton("❌ Закрыть", callback_data="close_panel")]
    ]

# ---------- Показ главного меню ----------
async def show_main_menu(update, context):
    avatar_file_id = db.get_config("avatar_file_id") or os.getenv("AVATAR_FILE_ID")
    caption = f"<b>{html.escape(db.get_text('welcome_menu', '🍄 Добро пожаловать в Grib Stars Bot!'))}\n{html.escape(db.get_text('choose_action', 'Выберите действие:'))}</b>"
    reply_markup = InlineKeyboardMarkup(main_menu_keyboard())
    try:
        if avatar_file_id:
            await update.message.reply_photo(photo=avatar_file_id, caption=caption, reply_markup=reply_markup, parse_mode='HTML')
        else:
            await update.message.reply_text(caption, reply_markup=reply_markup, parse_mode='HTML')
    except Exception as e:
        logging.error(f"Ошибка в show_main_menu: {e}")

async def show_main_menu_callback(query):
    caption = f"<b>{html.escape(db.get_text('welcome_menu', '🍄 Добро пожаловать в Grib Stars Bot!'))}\n{html.escape(db.get_text('choose_action', 'Выберите действие:'))}</b>"
    reply_markup = InlineKeyboardMarkup(main_menu_keyboard())
    await edit_or_reply(query, caption, reply_markup)

# ---------- Универсальная обработка реферальной ссылки ----------
async def handle_referral(ref_id: int, new_user_id: int, new_user_name: str, context, is_new_user: bool):
    """
    Обрабатывает реферальную ссылку.
    is_new_user: True — пользователь только что зарегистрировался, False — уже был в базе.
    """
    if ref_id == new_user_id:
        return

    # Проверяем, зарегистрирован ли реферер
    referrer = db.get_user(ref_id)
    if not referrer:
        # Реферера нет в боте — звёзды не начисляем, уведомления не отправляем
        return

    # Только для НОВЫХ пользователей пытаемся начислить звёзды
    if is_new_user:
        success, status = db.process_referral(ref_id, new_user_id)

        if success:
            # Уведомление новому пользователю
            try:
                await context.bot.send_message(chat_id=new_user_id,
                    text="<b>🎁 Ты присоединился по пригласительной ссылке! Твой друг получил 2⭐.</b>",
                    parse_mode='HTML')
            except:
                pass

            # Уведомление рефереру об успехе
            new_user = db.get_user(new_user_id)
            if new_user:
                friend_identifier = f"@{new_user['username']}" if new_user.get('username') else new_user['first_name']
                friend_str = f"{friend_identifier} ({new_user_id})"
            else:
                friend_str = f"пользователь ({new_user_id})"
            ref_msg = f"<b>🍄 По твоей ссылке присоединился новый друг: {html.escape(friend_str)}\n⭐ +2 звёзды!</b>"

            try:
                await context.bot.send_message(chat_id=ref_id, text=ref_msg, parse_mode='HTML')
            except Exception as e:
                logging.warning(f"Не удалось отправить уведомление рефереру {ref_id}: {e}")

            # Логирование в топик JOIN
            ref_str = f"@{referrer['username']}" if referrer.get('username') else f"пользователь {ref_id}"
            new_str = f"@{new_user_name}" if new_user_name else f"пользователь {new_user_id}"
            log_text = (
                f"<b>Пользователь {html.escape(new_str)} ({new_user_id}) "
                f"перешёл по реф. ссылке {html.escape(ref_str)} ({ref_id}) "
                f"и получил 2 звёзды</b>"
            )
            try:
                await context.bot.send_message(
                    chat_id=LOG_CHAT_ID,
                    message_thread_id=JOIN_THREAD_ID,
                    text=log_text,
                    parse_mode='HTML'
                )
            except Exception as e:
                logging.error(f"Ошибка лога реферала: {e}")

        else:
            # Не удалось начислить (уже был кем-то приглашён)
            try:
                await context.bot.send_message(chat_id=ref_id,
                    text="<b>ℹ️ По твоей ссылке перешёл пользователь, который уже был приглашён ранее.</b>",
                    parse_mode='HTML')
            except Exception as e:
                logging.warning(f"Не удалось отправить уведомление рефереру {ref_id}: {e}")

    else:
        # Пользователь уже был зарегистрирован — звёзды не начисляем
        try:
            await context.bot.send_message(chat_id=ref_id,
                text="<b>ℹ️ По твоей ссылке перешёл уже зарегистрированный пользователь.</b>",
                parse_mode='HTML')
        except Exception as e:
            logging.warning(f"Не удалось отправить уведомление рефереру {ref_id}: {e}")

# ---------- Старт и капча (с активацией чека) ----------
async def start(update: Update, context):
    user = update.effective_user
    user_id = user.id

    # Активация чека по ссылке
    if context.args and context.args[0].startswith("check_"):
        code = context.args[0][6:]
        check = db.get_check_by_code(code)
        if not check or not check['is_active']:
            await update.message.reply_text("<b>❌ Чек не найден или недействителен.</b>", parse_mode='HTML')
            return ConversationHandler.END
        if check['current_activations'] >= check['max_activations']:
            await update.message.reply_text("<b>❌ Чек уже полностью активирован.</b>", parse_mode='HTML')
            return ConversationHandler.END
        if check['password']:
            context.user_data['activating_check_code'] = code
            await update.message.reply_text("<b>🔐 Введите пароль для активации чека:</b>", parse_mode='HTML')
            return WAITING_CHECK_PASSWORD
        stars = db.activate_check(code)
        if stars is None:
            await update.message.reply_text("<b>❌ Не удалось активировать чек.</b>", parse_mode='HTML')
        else:
            db.add_stars(user_id, stars)
            await update.message.reply_text(f"<b>✅ Чек активирован! Вы получили {stars} ⭐</b>", parse_mode='HTML')
            await log_check_activation(user, code, stars, context)
        return ConversationHandler.END

    # Проверка подписки
    is_subscribed = await check_subscription(user_id, context)
    if not is_subscribed:
        keyboard = [
            [InlineKeyboardButton("📢 Подписаться на канал", url=f"https://t.me/{CHANNEL_ID.lstrip('@')}")],
            [InlineKeyboardButton("✅ Я подписался", callback_data="check_sub")]
        ]
        await update.message.reply_text(
            f"<b>Чтобы использовать бота, необходимо быть подписанным на канал {CHANNEL_ID}.</b>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )
        return ConversationHandler.END

    ref_id = None
    if context.args:
        try:
            ref_id = int(context.args[0].replace("ref_", ""))
        except:
            pass

    existing_user = db.get_user(user_id)
    if existing_user:
        if ref_id and ref_id != user_id:
            await handle_referral(ref_id, user_id, user.username, context, is_new_user=False)
        await show_main_menu(update, context)
        return ConversationHandler.END

    num1, num2 = random.randint(1, 10), random.randint(1, 10)
    captcha_storage[user_id] = num1 + num2
    await update.message.reply_text(
        "<b>🤖 Докажи, что ты не робот!\n\n"
        f"Реши пример: {num1} + {num2} = ?\n\n"
        "Отправь ответ числом 👇</b>",
        parse_mode='HTML'
    )
    return CAPTCHA

# ---------- Отправка лога активации чека ----------
async def log_check_activation(user, code, stars, context):
    check = db.get_check_by_code(code)
    if not check or not check.get('output_message_id'):
        return

    chat_id_str = str(LOG_CHAT_ID).replace('-100', '')
    check_link = f"https://t.me/c/{chat_id_str}/{check['output_message_id']}?thread={ACTIVATION_THREAD_ID}"

    user_mention = f"@{user.username}" if user.username else user.first_name
    remaining = check['max_activations'] - check['current_activations']

    text = (
        f"<b>Пользователь {html.escape(user_mention)} "
        f"[<code>{user.id}</code>] "
        f"активировал <a href='{check_link}'>чек</a> "
        f"и получил {stars} ⭐️</b>\n"
        f"<b>Осталось активаций: {remaining}</b>"
    )

    try:
        await context.bot.send_message(
            chat_id=LOG_CHAT_ID,
            message_thread_id=ACTIVATION_THREAD_ID,
            text=text,
            parse_mode='HTML',
            disable_web_page_preview=True
        )
    except Exception as e:
        logging.error(f"Ошибка лога активации чека: {e}")

# ---------- Капча ----------
async def captcha_handler(update: Update, context):
    user_id = update.effective_user.id
    if user_id not in captcha_storage:
        await update.message.reply_text("<b>❌ Капча устарела. Напиши /start ещё раз.</b>", parse_mode='HTML')
        return ConversationHandler.END

    answer = update.message.text.strip()
    correct = captcha_storage.pop(user_id)
    if not answer.isdigit() or int(answer) != correct:
        num1, num2 = random.randint(1, 10), random.randint(1, 10)
        captcha_storage[user_id] = num1 + num2
        await update.message.reply_text(
            f"<b>❌ Неправильно! Попробуй ещё раз:\n\n{num1} + {num2} = ?</b>",
            parse_mode='HTML'
        )
        return CAPTCHA

    if not await check_subscription(user_id, context):
        keyboard = [
            [InlineKeyboardButton("📢 Подписаться на канал", url=f"https://t.me/{CHANNEL_ID.lstrip('@')}")],
            [InlineKeyboardButton("✅ Я подписался", callback_data="check_sub")]
        ]
        await update.message.reply_text(
            f"<b>Для завершения регистрации необходимо подписаться на {CHANNEL_ID}.</b>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )
        return ConversationHandler.END

    user_data = db.get_or_create_user(user_id, update.effective_user.username or "", update.effective_user.first_name or "")

    ref_id = None
    if context.args:
        try:
            ref_id = int(context.args[0].replace("ref_", ""))
        except:
            pass

    if ref_id and ref_id != user_id:
        await handle_referral(ref_id, user_id, update.effective_user.username, context, is_new_user=True)

    await show_main_menu(update, context)
    return ConversationHandler.END

# ---------- Проверка подписки по кнопке ----------
async def check_subscription_callback(update: Update, context):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if await check_subscription(user_id, context):
        db.get_or_create_user(user_id, query.from_user.username or "", query.from_user.first_name or "")
        await show_main_menu_callback(query)
    else:
        await query.edit_message_text(
            f"<b>❌ Вы ещё не подписались!\n👉 {CHANNEL_ID}</b>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 Подписаться", url=f"https://t.me/{CHANNEL_ID.lstrip('@')}")],
                [InlineKeyboardButton("✅ Проверить", callback_data="check_sub")]
            ]),
            parse_mode='HTML'
        )

# ---------- Обработчик кнопок ----------
async def button_handler(update: Update, context):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data == "admin_panel_back":
        if user_id not in ADMIN_IDS: return
        await edit_or_reply(query, "<b>🛠️ Админ панель</b>", InlineKeyboardMarkup(admin_panel_keyboard()))
        return

    if data == "close_panel":
        try: await query.message.delete()
        except: pass
        return

    if data == "admin_create_check":
        if user_id not in ADMIN_IDS: return
        keyboard = [
            [InlineKeyboardButton("🔓 Без пароля", callback_data="check_create_nopass"),
             InlineKeyboardButton("🔐 С паролем", callback_data="check_create_pass")],
            [InlineKeyboardButton("🔙 Назад", callback_data="admin_panel_back")]
        ]
        await edit_or_reply(query, "<b>🧾 Создание чека</b>", InlineKeyboardMarkup(keyboard))
        return

    if data == "admin_withdrawals":
        if user_id not in ADMIN_IDS: return
        requests = db.get_pending_requests()
        if not requests:
            await edit_or_reply(query, "<b>📭 Нет новых заявок.</b>",
                                InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="admin_panel_back")]]))
            return
        text = "<b>📦 ВЫВОДЫ (ожидают):</b>\n\n"
        keyboard = []
        for req in requests:
            text += f"<b>🆔 Заявка #{req['id']}\n👤 {req['user_id']}\n{html.escape(req['gift_emoji'])} {html.escape(req['gift_name'])} — {req['stars_cost']}⭐\n📅 {req['request_date'][:16]}</b>\n\n"
            keyboard.append([
                InlineKeyboardButton("✅ Принять", callback_data=f"accept_{req['id']}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{req['id']}")
            ])
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="admin_panel_back")])
        await edit_or_reply(query, text, InlineKeyboardMarkup(keyboard))
        return

    if data.startswith("accept_") or data.startswith("reject_"):
        if user_id not in ADMIN_IDS: return
        action, req_id = data.split("_")
        request_id = int(req_id)
        if action == "accept":
            conn = db._get_connection()
            req = conn.execute("SELECT * FROM gift_requests WHERE id = ? AND status = 'pending'", (request_id,)).fetchone()
            conn.close()
            if not req:
                await query.answer("Заявка уже не актуальна")
                return
            db.complete_request(request_id)
            gift = dict(req)
            try:
                await context.bot.send_message(chat_id=gift['user_id'],
                    text=f"<b>🎉 Заявка на {html.escape(gift['gift_emoji'])} {html.escape(gift['gift_name'])} принята!</b>",
                    parse_mode='HTML')
            except:
                pass
            await query.answer("Принято")
            return await button_handler(update, context)
        else:
            rejected = db.reject_request(request_id)
            if not rejected:
                await query.answer("Уже не актуально")
                return
            try:
                await context.bot.send_message(chat_id=rejected['user_id'],
                    text=f"<b>❌ Заявка на {html.escape(rejected['gift_emoji'])} {html.escape(rejected['gift_name'])} отклонена, звёзды возвращены.</b>",
                    parse_mode='HTML')
            except:
                pass
            await query.answer("Отклонено")
            return await button_handler(update, context)

    if data == "admin_checks":
        if user_id not in ADMIN_IDS: return
        all_reqs = db.get_all_gift_requests()
        if not all_reqs:
            text = "<b>📄 Заявок пока нет.</b>"
        else:
            text = "<b>📄 Все заявки:</b>\n\n"
            for req in all_reqs:
                status = {'completed':'✅','rejected':'❌','pending':'🟢'}.get(req['status'], req['status'])
                text += f"<b>🆔 {req['id']} {html.escape(req['gift_emoji'])} {html.escape(req['gift_name'])} ({req['stars_cost']}⭐) — {status}</b>\n"
        await edit_or_reply(query, text, InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="admin_panel_back")]]))
        return

    # Пользовательские кнопки
    if data == "profile":
        user_data = db.get_user(user_id)
        ref_link = get_referral_link(user_id)
        name = html.escape(user_data['first_name'] or "Не указано")
        username = html.escape(query.from_user.username or "Не указан")
        text = (
            f"<b>👤 Профиль\n\n"
            f"💬 Имя: {name}\n"
            f"👤 Username: @{username}\n"
            f"🆔 ID: {user_id}\n\n"
            f"🔗 Твоя реф ссылка:\n{html.escape(ref_link)}\n\n"
            f"👥 Всего друзей: {db.get_referrals_count(user_id)}\n"
            f"✅ Активировали бота: {db.get_referrals_activated(user_id)}\n"
            f"💰 Баланс: ⭐ {user_data['stars']}</b>"
        )
        keyboard = [
            [InlineKeyboardButton("📤 Поделиться ссылкой", switch_inline_query=f"GRIB STARS — зарабатывай звёзды! {ref_link}")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]
        ]
        await edit_or_reply(query, text, InlineKeyboardMarkup(keyboard))

    elif data == "earn_stars":
        ref_link = get_referral_link(user_id)
        text = (
            "<b>🍄 Получай по 2⭐️ за друга!\n\n"
            "✔️ Как активировать: пройти капчу и подписаться на @Grib_Gifts\n"
            "❗️ Важно: друг не должен отписываться и блокировать бота 1 час.\n\n"
            f"🔗 Твоя личная ссылка:\n{html.escape(ref_link)}\n\n"
            "📩 Когда новый друг зарегистрируется, ты получишь уведомление.</b>"
        )
        keyboard = [
            [InlineKeyboardButton("📤 Поделиться", switch_inline_query=f"GRIB STARS — зарабатывай звёзды! {ref_link}")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]
        ]
        await edit_or_reply(query, text, InlineKeyboardMarkup(keyboard))

    elif data == "withdrawn":
        orders = db.get_user_gift_requests(user_id)
        completed = [o for o in orders if o['status'] == 'completed']
        if not completed:
            text = "<b>📦 У тебя пока нет выполненных выводов.\nПродолжай зарабатывать звёзды! ⭐</b>"
        else:
            text = "<b>📦 Твои выводы:</b>\n\n"
            for o in completed:
                text += f"<b>{html.escape(o['gift_emoji'])} {html.escape(o['gift_name'])} — {o['stars_cost']}⭐ (✅ {o['completed_date'][:10]})</b>\n"
        await edit_or_reply(query, text, InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]]))

    elif data == "checks":
        orders = db.get_user_gift_requests(user_id)
        pending = [o for o in orders if o['status'] == 'pending']
        if not pending:
            text = "<b>📄 У тебя нет активных заявок.\nХочешь получить подарок? Перейди в вывод! 🎁</b>"
        else:
            text = "<b>📄 Активные заявки:</b>\n\n"
            for o in pending:
                text += f"<b>{html.escape(o['gift_emoji'])} {html.escape(o['gift_name'])} — {o['stars_cost']}⭐ (🟢 Ожидает, 📅 {o['request_date'][:10]})</b>\n"
        keyboard = [
            [InlineKeyboardButton("🎁 Вывести", callback_data="gift_shop")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]
        ]
        await edit_or_reply(query, text, InlineKeyboardMarkup(keyboard))

    elif data == "gift_shop":
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
            {"emoji": "🏆", "name": "Кубок", "price": 100}
        ]
        user_data = db.get_user(user_id)
        text = f"<b>💰 Баланс: {user_data['stars']} ⭐\n\n🎁 Выберите подарок:</b>"
        keyboard = []
        row = []
        for gift in gifts:
            btn = InlineKeyboardButton(f"{gift['emoji']} {gift['name']} ({gift['price']}⭐)",
                                       callback_data=f"buy_{gift['name']}_{gift['price']}_{gift['emoji']}")
            row.append(btn)
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")])
        await edit_or_reply(query, text, InlineKeyboardMarkup(keyboard))

    elif data == "back_to_menu":
        await show_main_menu_callback(query)

    elif data.startswith("buy_"):
        _, name, price, emoji = data.split("_")
        price = int(price)
        user_data = db.get_user(user_id)
        if user_data['stars'] < price:
            await edit_or_reply(query,
                f"<b>❌ Недостаточно звёзд!\nНужно: {price}⭐\nУ тебя: {user_data['stars']}⭐</b>",
                InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Вывести", callback_data="gift_shop")]]))
            return
        request_id = db.create_gift_request(user_id, name, emoji, price)
        user_data = db.get_user(user_id)

        user = query.from_user
        user_display = f"@{html.escape(user.username)}" if user.username else html.escape(user.first_name)
        user_info = f"👤 Пользователь: {user_display} ({user_id})"

        log_text = (
            f"<b>📦 Новая заявка #{request_id}\n"
            f"{user_info}\n"
            f"{html.escape(emoji)} Подарок: {html.escape(name)}\n"
            f"⭐ Цена: {price}</b>"
        )
        try:
            msg = await context.bot.send_message(
                chat_id=LOG_CHAT_ID,
                message_thread_id=WITHDRAW_THREAD_ID,
                text=log_text,
                parse_mode='HTML'
            )
            db.set_gift_output_message_id(request_id, msg.message_id)
        except Exception as e:
            logging.error(f"Не удалось отправить лог заявки: {e}")

        await edit_or_reply(query,
            f"<b>✅ Заявка оформлена!\n\n{html.escape(emoji)} {html.escape(name)}\n⭐ Потрачено: {price}⭐\n⭐ Осталось: {user_data['stars']}⭐\n\n⏳ Ожидай администратора.</b>",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 В меню", callback_data="back_to_menu")]]))

    elif data in ["admin_broadcast", "admin_block", "admin_logs"]:
        await query.answer("🚧 В разработке")

# ---------- Обработчик сообщений (включая пароль чека) ----------
async def message_handler(update: Update, context):
    user_id = update.effective_user.id
    if update.effective_chat.id == LOG_CHAT_ID:
        return

    if 'activating_check_code' in context.user_data:
        code = context.user_data.pop('activating_check_code')
        check = db.get_check_by_code(code)
        if not check or not check['is_active']:
            await update.message.reply_text("<b>❌ Чек недействителен.</b>", parse_mode='HTML')
            return ConversationHandler.END
        if update.message.text.strip() == check['password']:
            stars = db.activate_check(code)
            if stars is None:
                await update.message.reply_text("<b>❌ Чек уже активирован максимальное число раз.</b>", parse_mode='HTML')
            else:
                db.add_stars(user_id, stars)
                await update.message.reply_text(f"<b>✅ Пароль верен! Вы получили {stars} ⭐</b>", parse_mode='HTML')
                await log_check_activation(update.effective_user, code, stars, context)
        else:
            await update.message.reply_text("<b>❌ Неверный пароль.</b>", parse_mode='HTML')
        return

    await update.message.reply_text("<b>🍄 Используй /start для входа в GRIB STARS</b>", parse_mode='HTML')

# ---------- Админские команды ----------
async def panel_command(update: Update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return
    await update.message.reply_text("<b>🛠️ Админ панель</b>", reply_markup=InlineKeyboardMarkup(admin_panel_keyboard()), parse_mode='HTML')

async def givestars(update: Update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return
    if not context.args:
        await update.message.reply_text("<b>❌ Укажи количество звёзд.\nПример: /givestars 1000</b>", parse_mode='HTML')
        return
    try:
        amount = float(context.args[0])
        if amount <= 0:
            raise ValueError
    except:
        await update.message.reply_text("<b>❌ Неверное число!</b>", parse_mode='HTML')
        return
    db.add_stars(update.effective_user.id, amount)
    await update.message.reply_text(f"<b>✅ Выдано {amount} ⭐!</b>", parse_mode='HTML')

async def settext(update: Update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("<b>❌ Укажи ключ и текст.\nПример: /settext welcome Новый приветственный текст</b>", parse_mode='HTML')
        return
    key = context.args[0]
    value = ' '.join(context.args[1:])
    db.set_text(key, value)
    await update.message.reply_text(f"<b>✅ Текст '{html.escape(key)}' обновлён!</b>", parse_mode='HTML')

# ---------- Сброс всех данных ----------
async def reset_all_users(update: Update, context):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("<b>⛔ Нет доступа!</b>", parse_mode='HTML')
        return
    conn = db._get_connection()
    conn.execute("DELETE FROM users")
    conn.execute("DELETE FROM referrals")
    conn.execute("DELETE FROM gift_requests")
    conn.execute("DELETE FROM checks")
    conn.commit()
    conn.close()
    await update.message.reply_text("<b>🗑️ Все данные о пользователях, рефералах, заявках и чеках удалены.</b>", parse_mode='HTML')

# ---------- Аватар ----------
async def setavatar_start(update: Update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return ConversationHandler.END
    await update.message.reply_text("<b>📸 Отправь мне фото, которое будет аватаркой бота.</b>", parse_mode='HTML')
    return WAITING_FOR_AVATAR

async def setavatar_photo(update: Update, context):
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        db.set_config("avatar_file_id", file_id)
        await update.message.reply_text(f"<b>✅ Аватарка сохранена!</b>\n<code>{html.escape(file_id)}</code>", parse_mode='HTML')
        return ConversationHandler.END
    else:
        await update.message.reply_text("<b>❌ Пожалуйста, отправь фото.</b>", parse_mode='HTML')
        return WAITING_FOR_AVATAR

async def setavatar_cancel(update: Update, context):
    await update.message.reply_text("<b>❌ Отменено.</b>", parse_mode='HTML')
    return ConversationHandler.END

# ---------- Создание чека (conversation) ----------
async def start_create_check(update: Update, context):
    query = update.callback_query
    await query.answer()
    if query.from_user.id not in ADMIN_IDS:
        return ConversationHandler.END
    context.user_data['check_password'] = query.data == "check_create_pass"
    await query.message.reply_text("<b>📊 Введите количество активаций чека (число):</b>", parse_mode='HTML')
    return CHECK_ACTIVATIONS

async def check_activations_input(update: Update, context):
    msg = update.message.text.strip()
    if not msg.isdigit() or int(msg) <= 0:
        await update.message.reply_text("<b>❌ Введите целое положительное число.</b>", parse_mode='HTML')
        return CHECK_ACTIVATIONS
    context.user_data['check_max_activations'] = int(msg)
    await update.message.reply_text("<b>⭐ Сколько звёзд даётся за одну активацию? (можно дробное, например 3.5)</b>", parse_mode='HTML')
    return CHECK_STARS

async def check_stars_input(update: Update, context):
    msg = update.message.text.strip().replace(',', '.')
    try:
        stars = float(msg)
        if stars <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("<b>❌ Введите положительное число.</b>", parse_mode='HTML')
        return CHECK_STARS
    context.user_data['check_stars'] = stars
    if context.user_data.get('check_password'):
        await update.message.reply_text("<b>🔐 Введите пароль для чека:</b>", parse_mode='HTML')
        return CHECK_PASSWORD
    else:
        return await finish_check_creation(update, context)

async def check_password_input(update: Update, context):
    context.user_data['check_password_value'] = update.message.text.strip()
    return await finish_check_creation(update, context)

async def finish_check_creation(update, context):
    user_id = update.effective_user.id
    code = db.create_check(
        user_id,
        context.user_data.get('check_password_value', ''),
        context.user_data['check_max_activations'],
        context.user_data['check_stars']
    )
    link = f"https://t.me/{BOT_USERNAME.lstrip('@')}?start=check_{code}"
    text = (
        f"<b>🧾 Создан новый чек:</b>\n"
        f"<b>Код: <code>{html.escape(code)}</code></b>\n"
        f"<b>Ссылка: {html.escape(link)}</b>\n"
        f"<b>Активаций: {context.user_data['check_max_activations']}</b>\n"
        f"<b>Звёзд за активацию: {context.user_data['check_stars']} ⭐</b>"
    )
    if context.user_data.get('check_password_value'):
        text += f"\n<b>Пароль: {html.escape(context.user_data['check_password_value'])}</b>"

    try:
        msg = await context.bot.send_message(
            chat_id=LOG_CHAT_ID,
            message_thread_id=ACTIVATION_THREAD_ID,
            text=text,
            parse_mode='HTML'
        )
        db.set_check_output_message_id(code, msg.message_id)
    except Exception as e:
        logging.error(f"Не удалось отправить лог создания чека: {e}")

    await update.message.reply_text(
        f"<b>✅ Чек создан!</b>\n"
        f"<b>Код: <code>{html.escape(code)}</code></b>\n"
        f"<b>Ссылка: {html.escape(link)}</b>\n"
        f"<b>Активаций: {context.user_data['check_max_activations']}</b>\n"
        f"<b>Звёзд за активацию: {context.user_data['check_stars']} ⭐</b>",
        parse_mode='HTML'
    )
    return ConversationHandler.END

async def cancel_check(update: Update, context):
    await update.message.reply_text("<b>❌ Создание чека отменено.</b>", parse_mode='HTML')
    return ConversationHandler.END

# ---------- Запуск ----------
def main():
    app = Application.builder().token(TOKEN).build()

    captcha_conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CAPTCHA: [MessageHandler(filters.TEXT & ~filters.COMMAND, captcha_handler)],
        },
        fallbacks=[],
    )
    app.add_handler(captcha_conv)

    check_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_create_check, pattern="^check_create_nopass$|^check_create_pass$")],
        states={
            CHECK_ACTIVATIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, check_activations_input)],
            CHECK_STARS: [MessageHandler(filters.TEXT & ~filters.COMMAND, check_stars_input)],
            CHECK_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, check_password_input)],
        },
        fallbacks=[CommandHandler("cancel", cancel_check)],
    )
    app.add_handler(check_conv)

    avatar_conv = ConversationHandler(
        entry_points=[CommandHandler("setavatar", setavatar_start)],
        states={WAITING_FOR_AVATAR: [MessageHandler(filters.PHOTO, setavatar_photo)]},
        fallbacks=[CommandHandler("cancel", setavatar_cancel)],
    )
    app.add_handler(avatar_conv)

    app.add_handler(CommandHandler("panel", panel_command))
    app.add_handler(CommandHandler("givestars", givestars))
    app.add_handler(CommandHandler("settext", settext))
    app.add_handler(CommandHandler("delavatar", lambda u, c: db.set_config("avatar_file_id", None) if u.effective_user.id in ADMIN_IDS else None))
    app.add_handler(CommandHandler("resetall", reset_all_users))

    app.add_handler(CallbackQueryHandler(check_subscription_callback, pattern="^check_sub$"))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    print("=" * 50)
    print("🍄 GRIB STARS БОТ ЗАПУЩЕН И ГОТОВ К РАБОТЕ")
    print("=" * 50)
    app.run_polling()

if __name__ == "__main__":
    main()
