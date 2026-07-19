import asyncio
import logging
from typing import Dict, Any

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

import yookassa
from yookassa import Payment, Configuration
from aiohttp import web

# -------------------------- ПЕРЕМЕННЫЕ / КОНФИГУРАЦИЯ --------------------------
BOT_TOKEN = "8752066625:AAHCff5uh3MbspMS-RnR_HJax3R4NoNzfOg"                # Токен бота из @BotFather
ADMIN_CHAT_ID = 123456789                   # ID чата, куда придут уведомления об оплате (можно получить у @userinfobot)

# Реквизиты YooKassa (тестовые или боевые)
YOOKASSA_SHOP_ID = "your_shop_id"           # shopId из личного кабинета ЮKassa
YOOKASSA_SECRET_KEY = "your_secret_key"     # Секретный ключ

# Настройки вебхука для приёма уведомлений от ЮKassa
WEBHOOK_HOST = "https://your-server.com"    # Ваш домен с https
WEBHOOK_PORT = 8080                         # Порт (должен быть доступен извне)
WEBHOOK_PATH = "/yookassa-webhook"          # Путь, который вы укажете в настройках ЮKassa

# Предложения (id, название, описание, цена в рублях)
OFFERS = [
    {
        "id": "offer_1",
        "name": "Пост на 24 часа",
        "description": "Один рекламный пост в моём чате, закреплённый на 24 часа.",
        "price": 500.00
    },
    {
        "id": "offer_2",
        "name": "Пост на неделю",
        "description": "Рекламный пост, закреплённый на целую неделю. Идеально для акций.",
        "price": 2500.00
    },
    {
        "id": "offer_3",
        "name": "VIP-пакет",
        "description": "Пост + упоминание во всех соцсетях + закреп на месяц.",
        "price": 10000.00
    },
]

# ------------------------------------------------------------------------------

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация YooKassa
Configuration.account_id = YOOKASSA_SHOP_ID
Configuration.secret_key = YOOKASSA_SECRET_KEY

# Хранилище созданных платежей (в реальном проекте лучше использовать БД)
payment_storage: Dict[str, Dict[str, Any]] = {}  # payment_id -> {user_id, offer_id, ...}

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# -------------------------- КЛАВИАТУРЫ --------------------------
def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Главное меню."""
    builder = InlineKeyboardBuilder()
    builder.button(text="📢 Заказать рекламу", callback_data="menu_order")
    builder.button(text="❓ Помощь", callback_data="menu_help")
    builder.button(text="📋 Мои заказы", callback_data="menu_orders")
    builder.adjust(1)
    return builder.as_markup()

def build_offers_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура с выбором предложения."""
    builder = InlineKeyboardBuilder()
    for offer in OFFERS:
        builder.button(
            text=f"{offer['name']} — {offer['price']} ₽",
            callback_data=f"select_offer:{offer['id']}"
        )
    builder.button(text="🔙 Назад в меню", callback_data="menu_back")
    builder.adjust(1)
    return builder.as_markup()

# -------------------------- ОБРАБОТЧИКИ КОМАНД --------------------------
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Приветствие и главное меню."""
    welcome_text = (
        "👋 Добро пожаловать в витрину рекламы!\n\n"
        "Здесь вы можете заказать рекламу в моём канале.\n"
        "Выберите действие:"
    )
    await message.answer(welcome_text, reply_markup=main_menu_keyboard())

# -------------------------- ОБРАБОТЧИКИ ГЛАВНОГО МЕНЮ --------------------------
@dp.callback_query(F.data == "menu_order")
async def menu_order(callback: types.CallbackQuery):
    """Показать витрину с предложениями."""
    await callback.message.answer(
        "Выберите подходящее предложение:",
        reply_markup=build_offers_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data == "menu_help")
async def menu_help(callback: types.CallbackQuery):
    """Заглушка «Помощь»."""
    await callback.message.answer(
        "❓ *Помощь*\n\n"
        "Для заказа рекламы выберите нужный тариф и оплатите его картой.\n"
        "После оплаты я получу уведомление и свяжусь с вами для уточнения деталей.",
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data == "menu_orders")
async def menu_orders(callback: types.CallbackQuery):
    """Заглушка «Мои заказы»."""
    await callback.message.answer(
        "📋 *Мои заказы*\n\n"
        "Здесь пока пусто. История ваших заказов появится позже.",
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data == "menu_back")
async def menu_back(callback: types.CallbackQuery):
    """Вернуться в главное меню."""
    await callback.message.answer(
        "Главное меню:",
        reply_markup=main_menu_keyboard()
    )
    await callback.answer()

# -------------------------- ОБРАБОТЧИК ВЫБОРА ПРЕДЛОЖЕНИЯ --------------------------
@dp.callback_query(F.data.startswith("select_offer:"))
async def process_offer_selection(callback: types.CallbackQuery):
    """Создание платежа в YooKassa и выдача ссылки на оплату."""
    offer_id = callback.data.split(":")[1]
    offer = next((o for o in OFFERS if o["id"] == offer_id), None)
    if not offer:
        await callback.answer("Предложение не найдено.", show_alert=True)
        return

    user_id = callback.from_user.id

    try:
        # Создаём платёж с передачей метаданных
        payment = Payment.create({
            "amount": {
                "value": f"{offer['price']:.2f}",
                "currency": "RUB"
            },
            "confirmation": {
                "type": "redirect",
                "return_url": "https://t.me/your_bot"   # Куда вернуть пользователя после оплаты
            },
            "capture": True,  # Автоматически списывать деньги
            "description": f"Реклама: {offer['name']}",
            "metadata": {
                "user_id": user_id,
                "offer_id": offer_id
            }
        })

        # Сохраняем информацию о платеже
        payment_storage[payment.id] = {
            "user_id": user_id,
            "offer_id": offer_id,
            "status": payment.status
        }

        # Отправляем пользователю кнопку для оплаты
        pay_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить картой", url=payment.confirmation.confirmation_url)]
        ])
        await callback.message.answer(
            f"✅ Вы выбрали: *{offer['name']}*\n"
            f"Стоимость: *{offer['price']} ₽*\n\n"
            "Нажмите кнопку ниже, чтобы перейти к безопасной оплате.",
            parse_mode="Markdown",
            reply_markup=pay_keyboard
        )
    except Exception as e:
        logger.error(f"Ошибка при создании платежа: {e}")
        await callback.message.answer("❌ Не удалось создать платёж. Попробуйте позже.")
    finally:
        await callback.answer()

# -------------------------- ВЕБХУК ДЛЯ YOOKASSA --------------------------
async def yookassa_webhook_handler(request: web.Request):
    """Обработка уведомлений от ЮKassa."""
    try:
        body = await request.json()
        event = body.get("event")
        payment_data = body.get("object", {})

        if event == "payment.succeeded":
            payment_id = payment_data.get("id")
            metadata = payment_data.get("metadata", {})
            user_id = metadata.get("user_id")
            offer_id = metadata.get("offer_id")

            if payment_id and user_id and offer_id:
                # Обновляем статус в хранилище
                if payment_id in payment_storage:
                    payment_storage[payment_id]["status"] = "succeeded"

                offer = next((o for o in OFFERS if o["id"] == offer_id), None)
                offer_name = offer["name"] if offer else "Неизвестное предложение"

                # Уведомляем администратора
                await bot.send_message(
                    ADMIN_CHAT_ID,
                    f"🎉 *Новая оплата!*\n\n"
                    f"Пользователь: `{user_id}`\n"
                    f"Предложение: *{offer_name}*\n"
                    f"Платёж ID: `{payment_id}`\n\n"
                    f"Пора запускать рекламу!",
                    parse_mode="Markdown"
                )
                # Уведомляем пользователя
                await bot.send_message(
                    user_id,
                    f"✅ Ваш платёж прошёл успешно! Реклама *{offer_name}* скоро будет запущена.",
                    parse_mode="Markdown"
                )
                logger.info(f"Платёж {payment_id} выполнен, user={user_id}, offer={offer_id}")
        else:
            logger.info(f"Получено событие: {event}")

    except Exception as e:
        logger.error(f"Ошибка в вебхуке: {e}")

    return web.Response(text="OK")

# -------------------------- ЗАПУСК ПРИЛОЖЕНИЯ --------------------------
async def start_webhook_server():
    """Запуск простого HTTP‑сервера для приёма вебхуков."""
    app = web.Application()
    app.router.add_post(WEBHOOK_PATH, yookassa_webhook_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEBHOOK_PORT)
    await site.start()
    logger.info(f"Webhook server started on port {WEBHOOK_PORT} at {WEBHOOK_PATH}")

async def main():
    """Главная функция: поллинг + вебхук‑сервер."""
    # Запускаем вебхук‑сервер параллельно с поллингом
    await start_webhook_server()
    # Удаляем вебхук Telegram (если был) и запускаем поллинг
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
