import asyncio
import logging
import html
import uuid
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, Tuple, List

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

import yookassa
from yookassa import Payment, Configuration

from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Enum, Boolean, Text,
    create_engine, select, update, delete, and_, or_
)
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.dialects.sqlite import insert

# -------------------------- ПЕРЕМЕННЫЕ / КОНФИГУРАЦИЯ --------------------------
# Оставлены жёстко (по требованию), но в реальном проекте так делать нельзя
BOT_TOKEN = "8834995392:AAGA0l_Nfcg9yVv8k0R2YOqkMCc9FuehMPo"
ADMIN_CHAT_IDS = [6165273503, 5910455056, 6524224796, 7609958217]
GROUP_CHAT_ID = -1004432776380
SUPPORT_THREAD_ID = 2
YOOKASSA_SHOP_ID = "your_shop_id"
YOOKASSA_SECRET_KEY = "your_secret_key"

GROUPS = [
    {
        "id": "1",
        "name": "Minecraft MINI GAMES",
        "link": "https://t.me/chgmingames",
        "chat_username": "chgmingames",
        "chat_id": None,  # будет заполнен
        "sub": [
            {"hours": 12, "price": 60.0},
            {"hours": 24, "price": 100.0},
            {"hours": 48, "price": 180.0}
        ],
        "pin": [
            {"hours": 12, "price": 50.0},
            {"hours": 24, "price": 70.0},
            {"hours": 48, "price": 100.0}
        ]
    },
    {
        "id": "2",
        "name": "Атмосферный чатик для поиска знакомых",
        "link": "https://t.me/finderfriends",
        "chat_username": "finderfriends",
        "chat_id": None,
        "sub": [
            {"hours": 12, "price": 200.0},
            {"hours": 24, "price": 300.0},
            {"hours": 48, "price": 550.0}
        ],
        "pin": [
            {"hours": 12, "price": 30.0},
            {"hours": 24, "price": 50.0},
            {"hours": 48, "price": 90.0}
        ]
    },
    {
        "id": "3",
        "name": "ВЗ ПОДПИСКИ",
        "link": "https://t.me/vzaimniepodpiski1310",
        "chat_username": "vzaimniepodpiski1310",
        "chat_id": None,
        "sub": [
            {"hours": 12, "price": 90.0},
            {"hours": 24, "price": 150.0},
            {"hours": 48, "price": 270.0}
        ],
        "pin": [
            {"hours": 12, "price": 20.0},
            {"hours": 24, "price": 35.0},
            {"hours": 48, "price": 50.0}
        ]
    },
    {
        "id": "4",
        "name": "чат троллинга",
        "link": "https://t.me/troll1310",
        "chat_username": "troll1310",
        "chat_id": None,
        "sub": [
            {"hours": 12, "price": 200.0},
            {"hours": 24, "price": 300.0},
            {"hours": 48, "price": 550.0}
        ],
        "pin": [
            {"hours": 12, "price": 20.0},
            {"hours": 24, "price": 35.0},
            {"hours": 48, "price": 50.0}
        ]
    },
    {
        "id": "5",
        "name": "Чат общение, ищу девушку/парня",
        "link": "https://t.me/flirtchat13",
        "chat_username": "flirtchat13",
        "chat_id": None,
        "sub": [
            {"hours": 12, "price": 550.0},
            {"hours": 24, "price": 1000.0},
            {"hours": 48, "price": 1800.0}
        ],
        "pin": [
            {"hours": 12, "price": 90.0},
            {"hours": 24, "price": 160.0},
            {"hours": 48, "price": 280.0}
        ]
    }
]

# ------------------------------------------------------------------------------

# Настройка YooKassa
Configuration.account_id = YOOKASSA_SHOP_ID
Configuration.secret_key = YOOKASSA_SECRET_KEY

# База данных
DATABASE_URL = "sqlite+aiosqlite:///bot.db"
engine = create_async_engine(DATABASE_URL, future=True, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False)
Base = declarative_base()

# Модели
class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False)
    group_chat_id = Column(Integer, nullable=False)
    group_name = Column(String, nullable=False)
    group_link = Column(String, nullable=False)
    ad_type = Column(String, nullable=False)  # 'sub' или 'pin'
    duration_hours = Column(Integer, nullable=False)
    price = Column(Float, nullable=False)
    payment_id = Column(String, nullable=True)
    idempotency_key = Column(String, nullable=True)
    status = Column(String, nullable=False, default="CREATED")  # CREATED, PAYMENT_PENDING, PAID, WAITING_FOR_CONTENT, PROCESSING, ACTIVE, EXPIRED, FAILED, CANCELLED
    channel_username = Column(String, nullable=True)  # для sub
    message_id = Column(Integer, nullable=True)  # для pin
    failure_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    paid_at = Column(DateTime, nullable=True)
    started_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)

class SupportTicket(Base):
    __tablename__ = "support_tickets"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False)
    admin_message_id = Column(Integer, nullable=True)  # сообщение в группе админов
    status = Column(String, default="open")  # open, closed

# FSM
class OrderStates(StatesGroup):
    waiting_for_channel_link = State()
    waiting_for_pin_message = State()

class SupportStates(StatesGroup):
    waiting_for_message = State()
    waiting_for_admin_reply = State()

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Глобальные переменные (кэш)
active_menu_messages: Dict[int, Tuple[int, int]] = {}
user_stack: Dict[int, list] = {}
# Кэш доступности групп (чтобы не проверять каждый раз)
group_availability_cache: Dict[int, bool] = {}

# Вспомогательные функции
def get_group_by_id(group_id: str) -> Optional[dict]:
    return next((g for g in GROUPS if g["id"] == group_id), None)

async def initialize_groups():
    """Получение chat_id для всех групп и проверка прав."""
    for g in GROUPS:
        try:
            chat = await bot.get_chat(f"@{g['chat_username']}")
            g["chat_id"] = chat.id
            # Проверяем права бота
            bot_member = await bot.get_chat_member(chat.id, bot.id)
            can_pin = bot_member.can_pin_messages if hasattr(bot_member, 'can_pin_messages') else False
            can_delete = bot_member.can_delete_messages if hasattr(bot_member, 'can_delete_messages') else False
            group_availability_cache[chat.id] = (can_pin and can_delete)  # требуется для закрепа; для sub только can_delete?
        except Exception as e:
            logger.error(f"Не удалось инициализировать группу {g['chat_username']}: {e}")
            group_availability_cache[g.get("chat_id", 0)] = False

async def check_bot_admin_in_channel(channel_username: str) -> bool:
    """Проверяет, является ли бот администратором в канале."""
    try:
        chat = await bot.get_chat(f"@{channel_username}")
        bot_member = await bot.get_chat_member(chat.id, bot.id)
        return bot_member.status in ("administrator", "creator")
    except Exception as e:
        logger.error(f"Ошибка проверки прав бота в {channel_username}: {e}")
        return False

# -------------------------- ЭКРАНЫ --------------------------
def get_main_screen() -> Tuple[str, InlineKeyboardMarkup]:
    text = (
        "<b>👋 Добро пожаловать в витрину рекламы!</b>\n\n"
        "<b>Выберите действие:</b>"
    )
    builder = InlineKeyboardBuilder()
    builder.button(text="📢 Заказать рекламу", callback_data="menu_order")
    builder.button(text="👑 VIP", callback_data="menu_vip")
    builder.button(text="📞 Техподдержка", callback_data="menu_support")
    builder.button(text="❓ Помощь", callback_data="menu_help")
    builder.button(text="📋 Мои заказы", callback_data="menu_orders")
    builder.adjust(1)
    return text, builder.as_markup()

def get_vip_screen() -> Tuple[str, InlineKeyboardMarkup]:
    text = (
        "<b>👑 VIP-статус в группах\n\n"
        "Стоимость VIP участника в любой группе на 30 дней — 200₽ / 150 звёзд\n"
        "за покупкой – <a href=\"https://t.me/ChG1310\">@ChG1310</a>\n\n"
        "...</b>"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="menu_back")]
    ])
    return text, keyboard

def get_group_selection_screen() -> Tuple[str, InlineKeyboardMarkup]:
    text_lines = ["<b>Выберите группу для размещения рекламы:</b>\n"]
    text_lines.insert(0, "<a href=\"https://t.me/pricechg\">\u200b</a>")
    for g in GROUPS:
        text_lines.append(
            f"{g['id']}. <a href=\"{g['link']}\">{html.escape(g['name'])}</a>"
            f"{' ⚠️ (недоступна)' if not group_availability_cache.get(g.get('chat_id', 0), True) else ''}"
        )
    text = "\n".join(text_lines)
    builder = InlineKeyboardBuilder()
    for g in GROUPS:
        # Блокируем выбор, если группа недоступна
        disabled = not group_availability_cache.get(g.get("chat_id", 0), True)
        builder.button(text=g["id"], callback_data=f"select_group:{g['id']}" if not disabled else "none")
    builder.button(text="🔙 Назад", callback_data="menu_back")
    builder.adjust(3)
    return text, builder.as_markup()

def get_ad_type_selection_screen() -> Tuple[str, InlineKeyboardMarkup]:
    text = "<b>Выберите тип рекламы:</b>"
    builder = InlineKeyboardBuilder()
    builder.button(text="Обязательная подписка", callback_data="ad_type:sub")
    builder.button(text="Закреп", callback_data="ad_type:pin")
    builder.button(text="🔙 Назад", callback_data="menu_back")
    builder.adjust(1)
    return text, builder.as_markup()

def get_help_screen() -> Tuple[str, InlineKeyboardMarkup]:
    text = (
        "<b>❓ Помощь</b>\n\n"
        "<b>Для заказа рекламы выберите нужный тип, тайминг и оплатите картой.</b>"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="menu_back")]
    ])
    return text, keyboard

def get_timing_selection_screen(ad_type: str, group_id: str) -> Tuple[str, InlineKeyboardMarkup]:
    group = get_group_by_id(group_id)
    if not group or ad_type not in ("sub", "pin"):
        return get_ad_type_selection_screen()
    emoji = "📢" if ad_type == "sub" else "📌"
    text = f"<b>Выберите длительность ({'обязательная подписка' if ad_type=='sub' else 'закреп'}) для группы {html.escape(group['name'])}:</b>"
    builder = InlineKeyboardBuilder()
    timings = group.get(ad_type, [])
    for t in timings:
        builder.button(text=f"{emoji} {t['hours']} ч — {t['price']:.0f} ₽", callback_data=f"select_timing:{t['hours']}")
    builder.button(text="🔙 Назад", callback_data="menu_back")
    builder.adjust(1)
    return text, builder.as_markup()

def render_payment_screen(order: Order) -> Tuple[str, InlineKeyboardMarkup]:
    group = get_group_by_id(str(order.group_chat_id))  # ищем по chat_id, не совсем точно
    group_name = order.group_name
    group_link = order.group_link
    ad_type_text = {"sub": "Обязательная подписка", "pin": "Закреп"}.get(order.ad_type, "Неизвестный тип")
    duration_str = f"{order.duration_hours} ч"
    group_safe = f"<a href=\"{group_link}\">{html.escape(group_name)}</a>"
    text = (
        f"<b>✅ Вы выбрали:</b>\n"
        f"<b>Группа: {group_safe}</b>\n"
        f"<b>Тип: {ad_type_text}</b>\n"
        f"<b>Длительность: {duration_str}</b>\n"
        f"<b>Стоимость: {order.price:.2f} ₽</b>\n\n"
        "<b>Нажмите «Оплатить картой», затем после оплаты вернитесь и нажмите «Проверить оплату».</b>"
    )
    # Кнопка оплаты будет с URL, который получим при создании платежа
    # Если уже создан платеж, то URL есть в payment_storage? Мы его не храним в Order.
    # Лучше отдельно хранить active_payments: payment_id -> confirmation_url
    # Но для простоты добавим поле confirmation_url в кэш
    # Здесь просто заглушка, реальный URL будет установлен при отображении
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить картой", url="about:blank")],  # заменится
        [InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"check_payment:{order.id}")],
        [InlineKeyboardButton(text="🔙 Назад к выбору", callback_data="menu_back")]
    ])
    return text, keyboard

async def update_menu(user_id: int, screen: str, params: Optional[dict] = None):
    if user_id not in active_menu_messages:
        return
    chat_id, message_id = active_menu_messages[user_id]
    text, markup = None, None
    if screen == "main":
        text, markup = get_main_screen()
    elif screen == "vip":
        text, markup = get_vip_screen()
    elif screen == "group_selection":
        text, markup = get_group_selection_screen()
    elif screen == "ad_type_selection":
        text, markup = get_ad_type_selection_screen()
    elif screen == "help":
        text, markup = get_help_screen()
    elif screen == "orders":
        # Будет позже
        text, markup = get_help_screen()
    elif screen == "timing_selection":
        if params and "ad_type" in params and "group_id" in params:
            text, markup = get_timing_selection_screen(params["ad_type"], params["group_id"])
    elif screen == "payment":
        # order = params.get("order")
        # text, markup = render_payment_screen(order)
        pass  # Платежный экран обновим отдельно
    else:
        return
    if text and markup:
        try:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=message_id, text=text, reply_markup=markup, parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка редактирования меню: {e}")

# -------------------------- ФУНКЦИИ ЗАКАЗОВ --------------------------
async def create_payment(order_id: int, user_id: int, price: float, description: str, metadata: dict) -> Optional[Payment]:
    idempotency_key = f"order_{order_id}_{uuid.uuid4().hex[:10]}"
    try:
        payment = Payment.create({
            "amount": {"value": f"{price:.2f}", "currency": "RUB"},
            "confirmation": {"type": "redirect", "return_url": "https://t.me/your_bot"},
            "capture": True,
            "description": description,
            "metadata": metadata,
        }, idempotency_key)
        # Обновляем order: payment_id, idempotency_key
        async with async_session() as session:
            stmt = update(Order).where(Order.id == order_id).values(
                payment_id=payment.id, idempotency_key=idempotency_key, status="PAYMENT_PENDING"
            )
            await session.execute(stmt)
            await session.commit()
        return payment
    except Exception as e:
        logger.error(f"Ошибка создания платежа для заказа {order_id}: {e}")
        return None

async def finalize_order(order: Order, success: bool, failure_reason: str = None):
    async with async_session() as session:
        if success:
            stmt = update(Order).where(Order.id == order.id).values(
                status="ACTIVE", started_at=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc) + timedelta(hours=order.duration_hours)
            )
        else:
            stmt = update(Order).where(Order.id == order.id).values(
                status="FAILED", failure_reason=failure_reason
            )
        await session.execute(stmt)
        await session.commit()

async def complete_order(order: Order):
    async with async_session() as session:
        stmt = update(Order).where(Order.id == order.id).values(status="EXPIRED")
        await session.execute(stmt)
        await session.commit()

# -------------------------- ФОНОВЫЕ ЗАДАЧИ --------------------------
async def expire_orders_worker():
    while True:
        try:
            async with async_session() as session:
                now = datetime.now(timezone.utc)
                stmt = select(Order).where(
                    Order.status == "ACTIVE",
                    Order.expires_at <= now
                )
                result = await session.execute(stmt)
                expired_orders = result.scalars().all()
                for order in expired_orders:
                    if order.ad_type == "pin":
                        try:
                            await bot.unpin_chat_message(order.group_chat_id, order.message_id)
                            await bot.delete_message(order.group_chat_id, order.message_id)
                        except Exception as e:
                            logger.error(f"Ошибка при завершении заказа {order.id}: {e}")
                    await complete_order(order)
        except Exception as e:
            logger.error(f"Ошибка в фоновом обработчике: {e}")
        await asyncio.sleep(30)  # проверка каждые 30 секунд

# -------------------------- КОМАНДЫ --------------------------
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if user_id in active_menu_messages:
        old_chat_id, old_msg_id = active_menu_messages[user_id]
        try:
            await bot.delete_message(old_chat_id, old_msg_id)
        except:
            pass
    text, markup = get_main_screen()
    sent = await message.answer(text, reply_markup=markup, parse_mode="HTML")
    active_menu_messages[user_id] = (sent.chat.id, sent.message_id)
    user_stack[user_id] = ["main"]
    await state.clear()

# -------------------------- ОБРАБОТЧИКИ МЕНЮ --------------------------
@dp.callback_query(F.data == "menu_order")
async def menu_order(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    user_stack.setdefault(user_id, ["main"])
    user_stack[user_id].append("group_selection")
    await update_menu(user_id, "group_selection")
    await callback.answer()

@dp.callback_query(F.data == "menu_vip")
async def menu_vip(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    user_stack.setdefault(user_id, ["main"])
    user_stack[user_id].append("vip")
    await update_menu(user_id, "vip")
    await callback.answer()

@dp.callback_query(F.data == "menu_help")
async def menu_help(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    user_stack.setdefault(user_id, ["main"])
    user_stack[user_id].append("help")
    await update_menu(user_id, "help")
    await callback.answer()

@dp.callback_query(F.data == "menu_orders")
async def menu_orders(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    # Заглушка
    await callback.answer("История заказов появится позже", show_alert=True)
    await callback.answer()

@dp.callback_query(F.data == "menu_support")
async def menu_support(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    user_stack.setdefault(user_id, ["main"])
    user_stack[user_id].append("support_waiting")
    await update_menu(user_id, "support_waiting")
    await state.set_state(SupportStates.waiting_for_message)
    await callback.answer()

# -------------------------- ВЫБОР ГРУППЫ --------------------------
@dp.callback_query(F.data.startswith("select_group:"))
async def process_group_selection(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    group_id = callback.data.split(":")[1]
    group = get_group_by_id(group_id)
    if not group:
        await callback.answer("Группа не найдена", show_alert=True)
        return
    # Проверяем доступность
    if not group_availability_cache.get(group.get("chat_id", 0), False):
        await callback.answer("Эта группа временно недоступна для размещения рекламы", show_alert=True)
        return
    await state.update_data(selected_group_id=group_id)
    user_stack.setdefault(user_id, ["main"])
    user_stack[user_id].append("ad_type_selection")
    await update_menu(user_id, "ad_type_selection")
    await callback.answer()

# -------------------------- ВЫБОР ТИПА --------------------------
@dp.callback_query(F.data.startswith("ad_type:"))
async def process_ad_type(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    ad_type = callback.data.split(":")[1]
    data = await state.get_data()
    group_id = data.get("selected_group_id")
    if not group_id or ad_type not in ("sub", "pin"):
        await callback.answer("Неверные данные", show_alert=True)
        return
    await state.update_data(selected_ad_type=ad_type)
    user_stack[user_id].append("timing_selection")
    await update_menu(user_id, "timing_selection", params={"ad_type": ad_type, "group_id": group_id})
    await callback.answer()

# -------------------------- ВЫБОР ТАЙМИНГА --------------------------
@dp.callback_query(F.data.startswith("select_timing:"))
async def process_timing(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    hours = int(callback.data.split(":")[1])
    data = await state.get_data()
    group_id = data["selected_group_id"]
    ad_type = data["selected_ad_type"]
    group = get_group_by_id(group_id)
    if not group:
        await callback.answer("Ошибка данных группы", show_alert=True)
        return
    timings = group.get(ad_type, [])
    timing = next((t for t in timings if t["hours"] == hours), None)
    if not timing:
        await callback.answer("Некорректная длительность", show_alert=True)
        return
    price = timing["price"]
    # Создаём заказ в БД
    async with async_session() as session:
        order = Order(
            user_id=user_id,
            group_chat_id=group["chat_id"],
            group_name=group["name"],
            group_link=group["link"],
            ad_type=ad_type,
            duration_hours=hours,
            price=price,
            status="CREATED"
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)
    # Создаём платёж
    metadata = {
        "order_id": order.id,
        "user_id": user_id,
        "group_id": group_id,
        "ad_type": ad_type,
        "duration_hours": hours
    }
    description = f"Реклама: {ad_type} ({hours} ч) в {group['name']}"
    payment = await create_payment(order.id, user_id, price, description, metadata)
    if not payment:
        await callback.answer("Не удалось создать платёж. Попробуйте позже.", show_alert=True)
        return
    # Сохраняем URL в кэш
    payment_storage[payment.id] = {"confirmation_url": payment.confirmation.confirmation_url, "order_id": order.id}
    # Обновляем экран с реальной ссылкой
    text, _ = render_payment_screen(order)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить картой", url=payment.confirmation.confirmation_url)],
        [InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"check_payment:{order.id}")],
        [InlineKeyboardButton(text="🔙 Назад к выбору", callback_data="menu_back")]
    ])
    chat_id, message_id = active_menu_messages[user_id]
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=keyboard, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Ошибка отображения платежа: {e}")
    user_stack[user_id].append("payment")
    await callback.answer()

# -------------------------- ПРОВЕРКА ОПЛАТЫ --------------------------
@dp.callback_query(F.data.startswith("check_payment:"))
async def check_payment(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    order_id = int(callback.data.split(":")[1])
    async with async_session() as session:
        result = await session.execute(select(Order).where(Order.id == order_id, Order.user_id == user_id))
        order = result.scalar_one_or_none()
    if not order:
        await callback.answer("Заказ не найден", show_alert=True)
        return
    if order.status != "PAYMENT_PENDING":
        await callback.answer("Этот заказ уже обработан или не требует оплаты", show_alert=True)
        return
    try:
        payment = Payment.find_one(order.payment_id)
    except Exception as e:
        logger.error(f"Ошибка поиска платежа {order.payment_id}: {e}")
        await callback.answer("Не удалось проверить платёж", show_alert=True)
        return
    if payment.status == "succeeded":
        # Обновляем статус на PAID
        async with async_session() as session:
            stmt = update(Order).where(Order.id == order.id).values(status="PAID", paid_at=datetime.now(timezone.utc))
            await session.execute(stmt)
            await session.commit()
        # Удаляем меню
        if user_id in active_menu_messages:
            chat_id, message_id = active_menu_messages.pop(user_id)
            try:
                await bot.delete_message(chat_id, message_id)
            except:
                pass
        user_stack.pop(user_id, None)
        # Переходим к ожиданию контента
        if order.ad_type == "pin":
            await state.set_state(OrderStates.waiting_for_pin_message)
            await state.update_data(order_id=order.id, group_chat_id=order.group_chat_id, duration_hours=order.duration_hours)
            sent = await bot.send_message(user_id, "<b>📌 Отправьте сообщение для закрепления.</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_order")]
            ]))
            await state.update_data(request_message_id=sent.message_id)
        else:  # sub
            await state.set_state(OrderStates.waiting_for_channel_link)
            await state.update_data(order_id=order.id, group_chat_id=order.group_chat_id, duration_hours=order.duration_hours)
            sent = await bot.send_message(user_id, "<b>🔗 Отправьте ссылку на канал (или @username).</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_order")]
            ]))
            await state.update_data(request_message_id=sent.message_id)
        # Уведомление админам
        ad_type_text = {"sub": "Обязательная подписка", "pin": "Закреп"}.get(order.ad_type, "")
        admin_msg = (f"🎉 Новая оплата!\nПользователь: {user_id}\nГруппа: {order.group_name}\nТип: {ad_type_text}\nДлительность: {order.duration_hours} ч")
        for admin_id in ADMIN_CHAT_IDS:
            try:
                await bot.send_message(admin_id, admin_msg)
            except:
                pass
        await callback.answer("Оплата подтверждена!", show_alert=True)
    else:
        await callback.answer("Платёж не завершён", show_alert=True)

# -------------------------- ОТМЕНА ЗАКАЗА --------------------------
@dp.callback_query(F.data == "cancel_order")
async def cancel_order(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback.from_user.id
    text, markup = get_main_screen()
    sent = await bot.send_message(user_id, text, reply_markup=markup, parse_mode="HTML")
    active_menu_messages[user_id] = (sent.chat.id, sent.message_id)
    user_stack[user_id] = ["main"]
    await callback.answer()

# -------------------------- ПОЛУЧЕНИЕ КАНАЛА ДЛЯ SUB --------------------------
@dp.message(OrderStates.waiting_for_channel_link)
async def process_channel_link(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    text = message.text.strip()
    # Извлечение username
    if text.startswith("https://t.me/"):
        parts = text.split("/")
        username = parts[-1] if parts[-1] else parts[-2]
    elif text.startswith("@"):
        username = text[1:]
    else:
        username = text
    username = username.strip()
    if not username:
        await message.answer("<b>❌ Неверный формат.</b>")
        return
    # Проверяем бота в канале
    if not await check_bot_admin_in_channel(username):
        await message.answer("<b>❌ Бот не является администратором канала.</b>")
        return
    data = await state.get_data()
    order_id = data["order_id"]
    async with async_session() as session:
        result = await session.execute(select(Order).where(Order.id == order_id))
        order = result.scalar_one()
        order.channel_username = username
        await session.commit()
    # Активируем заказ
    await finalize_order(order, success=True)
    await message.answer(f"<b>✅ Обязательная подписка на @{username} активирована.</b>", parse_mode="HTML")
    # Убираем сообщение запроса
    req_msg_id = data.get("request_message_id")
    if req_msg_id:
        try:
            await bot.delete_message(user_id, req_msg_id)
        except:
            pass
    await state.clear()
    text, markup = get_main_screen()
    sent = await message.answer(text, reply_markup=markup, parse_mode="HTML")
    active_menu_messages[user_id] = (sent.chat.id, sent.message_id)

# -------------------------- ПОЛУЧЕНИЕ СООБЩЕНИЯ ДЛЯ PIN --------------------------
@dp.message(OrderStates.waiting_for_pin_message)
async def process_pin_message(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()
    order_id = data["order_id"]
    async with async_session() as session:
        result = await session.execute(select(Order).where(Order.id == order_id))
        order = result.scalar_one()
    # Копируем сообщение в группу
    try:
        sent_msg = await message.copy_to(chat_id=order.group_chat_id)
    except Exception as e:
        logger.error(f"Не удалось скопировать сообщение: {e}")
        await message.answer("<b>❌ Не удалось отправить сообщение в группу.</b>")
        await finalize_order(order, success=False, failure_reason=str(e))
        return
    # Закрепляем
    try:
        await bot.pin_chat_message(order.group_chat_id, sent_msg.message_id)
    except Exception as e:
        logger.error(f"Не удалось закрепить сообщение: {e}")
        # Удаляем скопированное, чтобы не мусорить
        try:
            await bot.delete_message(order.group_chat_id, sent_msg.message_id)
        except:
            pass
        await finalize_order(order, success=False, failure_reason=str(e))
        await message.answer("<b>❌ Не удалось закрепить сообщение.</b>")
        # Уведомление админу
        return
    # Сохраняем message_id и активируем
    async with async_session() as session:
        stmt = update(Order).where(Order.id == order.id).values(message_id=sent_msg.message_id)
        await session.execute(stmt)
        await session.commit()
    await finalize_order(order, success=True)
    await message.answer("<b>✅ Сообщение закреплено!</b>", parse_mode="HTML")
    req_msg_id = data.get("request_message_id")
    if req_msg_id:
        try:
            await bot.delete_message(user_id, req_msg_id)
        except:
            pass
    await state.clear()
    text, markup = get_main_screen()
    sent = await message.answer(text, reply_markup=markup, parse_mode="HTML")
    active_menu_messages[user_id] = (sent.chat.id, sent.message_id)

# -------------------------- ПРОВЕРКА ПОДПИСКИ В ГРУППАХ --------------------------
@dp.message(F.chat.type.in_({'group', 'supergroup'}), ~F.text.startswith("/"))
async def check_subscription_filter(message: types.Message):
    chat_id = message.chat.id
    user = message.from_user
    if user.is_bot:
        return
    async with async_session() as session:
        now = datetime.now(timezone.utc)
        stmt = select(Order).where(
            Order.group_chat_id == chat_id,
            Order.ad_type == "sub",
            Order.status == "ACTIVE",
            Order.expires_at > now
        )
        result = await session.execute(stmt)
        active_orders = result.scalars().all()
    for order in active_orders:
        try:
            member = await bot.get_chat_member(f"@{order.channel_username}", user.id)
            if member.status not in ["member", "administrator", "creator"]:
                await message.delete()
                await bot.send_message(chat_id, f"⛔ {user.mention_html()}, подпишитесь на @{order.channel_username}")
                return
        except Exception as e:
            logger.error(f"Ошибка проверки подписки: {e}")
            # Если ошибка API, пропускаем удаление, но логируем

# -------------------------- ПОДДЕРЖКА --------------------------
@dp.message(SupportStates.waiting_for_message)
async def support_message_handler(message: types.Message, state: FSMContext):
    user = message.from_user
    # Пересылаем в техподдержку
    try:
        forwarded = await message.forward(chat_id=GROUP_CHAT_ID, message_thread_id=SUPPORT_THREAD_ID)
    except Exception as e:
        logger.error(f"Не удалось переслать сообщение в поддержку: {e}")
        await message.answer("Ошибка отправки обращения.")
        return
    # Сохраняем тикет
    async with async_session() as session:
        ticket = SupportTicket(user_id=user.id, admin_message_id=forwarded.message_id)
        session.add(ticket)
        await session.commit()
        ticket_id = ticket.id
    # Добавляем кнопку ответа
    reply_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✉️ Ответить", callback_data=f"reply_to_ticket:{ticket_id}")]
    ])
    await bot.edit_message_reply_markup(chat_id=GROUP_CHAT_ID, message_id=forwarded.message_id, reply_markup=reply_kb)
    await message.answer("<b>✅ Обращение отправлено.</b>", parse_mode="HTML")
    await state.clear()
    # Вернуть меню
    user_id = user.id
    if user_id in active_menu_messages:
        text, markup = get_main_screen()
        try:
            await bot.edit_message_text(chat_id=active_menu_messages[user_id][0], message_id=active_menu_messages[user_id][1], text=text, reply_markup=markup, parse_mode="HTML")
        except:
            pass

@dp.callback_query(F.data.startswith("reply_to_ticket:"))
async def reply_to_ticket(callback: types.CallbackQuery, state: FSMContext):
    ticket_id = int(callback.data.split(":")[1])
    if callback.from_user.id not in ADMIN_CHAT_IDS:
        await callback.answer("⛔ Только администраторы", show_alert=True)
        return
    async with async_session() as session:
        result = await session.execute(select(SupportTicket).where(SupportTicket.id == ticket_id))
        ticket = result.scalar_one_or_none()
    if not ticket:
        await callback.answer("Тикет не найден", show_alert=True)
        return
    await state.set_state(SupportStates.waiting_for_admin_reply)
    await state.update_data(ticket_id=ticket_id, target_user_id=ticket.user_id)
    await callback.message.answer(f"<b>✏️ Введите ответ пользователю {ticket.user_id}</b>", parse_mode="HTML")
    await callback.answer()

@dp.message(SupportStates.waiting_for_admin_reply)
async def admin_reply_send(message: types.Message, state: FSMContext):
    data = await state.get_data()
    target_user_id = data["target_user_id"]
    try:
        # Копируем любое сообщение пользователю
        await message.copy_to(chat_id=target_user_id)
        await message.answer("<b>✅ Ответ отправлен.</b>", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Не удалось отправить ответ пользователю {target_user_id}: {e}")
        await message.answer("<b>❌ Не удалось отправить ответ.</b>", parse_mode="HTML")
    finally:
        await state.clear()

# -------------------------- НАЗАД --------------------------
@dp.callback_query(F.data == "menu_back")
async def menu_back(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    stack = user_stack.get(user_id, ["main"])
    if len(stack) > 1:
        stack.pop()
    new_screen = stack[-1]
    await update_menu(user_id, new_screen)
    await callback.answer()

# -------------------------- ГЛОБАЛЬНАЯ ОБРАБОТКА ОШИБОК --------------------------
@dp.errors()
async def error_handler(update: types.Update, exception: Exception):
    logger.error(f"Update {update.update_id} вызвал ошибку: {exception}")
    return True

# -------------------------- ЗАПУСК --------------------------
async def main():
    # Инициализация БД
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Инициализация групп
    await initialize_groups()
    # Запуск фоновой задачи
    asyncio.create_task(expire_orders_worker())
    # Запуск поллинга
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
