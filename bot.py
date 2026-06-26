# ============================================================
#  HYPE Monitor Bot
#  Зависимости: pip install pyTelegramBotAPI requests
# ============================================================

import telebot
import requests
import time
import threading
import os
from telebot import types

# ── Токен ────────────────────────────────────────────────────
# Лучше хранить в переменной окружения:
#   export BOT_TOKEN="ваш_токен"
# Если не задана — берём fallback (только для теста!)
TOKEN = os.getenv("BOT_TOKEN", "8838571832:AAElqHv_qPr8EUY42vJh0EQBQDU7rAGqfRg")
bot = telebot.TeleBot(TOKEN, parse_mode="Markdown")

# ── Хранилище подписчиков ────────────────────────────────────
# { chat_id: threshold_percent }
price_subscribers: dict[int, int] = {}

# ── Хранилище базовой цены для каждого подписчика ───────────
# { chat_id: (price, timestamp) }
subscriber_base: dict[int, tuple[float, float]] = {}

# ── Главное меню ──────────────────────────────────────────────
MAIN_MARKUP = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
MAIN_MARKUP.add(
    types.KeyboardButton("💰 Курс HYPE"),
    types.KeyboardButton("📊 Статистика 24ч"),
)
MAIN_MARKUP.add(
    types.KeyboardButton("🔔 Уведомления"),
    types.KeyboardButton("❌ Отписаться"),
)
MAIN_MARKUP.add(types.KeyboardButton("ℹ️ Помощь"))

# ── CoinGecko ─────────────────────────────────────────────────
def get_hype_data() -> dict | None:
    try:
        url = "https://api.coingecko.com/api/v3/coins/hyperliquid"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        d = r.json()["market_data"]
        return {
            "price":      d["current_price"]["usd"],
            "volume":     d["total_volume"]["usd"],
            "cap":        d["market_cap"]["usd"],
            "change_24h": d["price_change_percentage_24h"],
            "change_7d":  d.get("price_change_percentage_7d", 0),
            "high_24h":   d["high_24h"]["usd"],
            "low_24h":    d["low_24h"]["usd"],
        }
    except Exception as e:
        print(f"[CoinGecko error] {e}")
        return None

def trend_emoji(change: float) -> str:
    if change >= 3:   return "🚀"
    if change >= 0:   return "📈"
    if change >= -3:  return "📉"
    return "🔻"

# ── /start ────────────────────────────────────────────────────
@bot.message_handler(commands=["start"])
def cmd_start(message):
    bot.send_message(
        message.chat.id,
        "👋 *HYPE Monitor Bot*\n\n"
        "Слежу за ценой монеты *Hyperliquid (HYPE)* в реальном времени.\n\n"
        "Выбери действие на клавиатуре ниже 👇",
        reply_markup=MAIN_MARKUP,
    )

# ── Курс ──────────────────────────────────────────────────────
@bot.message_handler(func=lambda m: m.text == "💰 Курс HYPE")
def cmd_price(message):
    data = get_hype_data()
    if not data:
        bot.send_message(message.chat.id, "❌ Не удалось получить данные. Попробуй позже.")
        return
    em = trend_emoji(data["change_24h"])
    sign = "+" if data["change_24h"] >= 0 else ""
    bot.send_message(
        message.chat.id,
        f"💰 *HYPE / USD*\n\n"
        f"Цена:         `${data['price']:.4f}`\n"
        f"Изм. 24ч:  {em} `{sign}{data['change_24h']:.2f}%`\n"
        f"Макс. 24ч: `${data['high_24h']:.4f}`\n"
        f"Мин. 24ч:   `${data['low_24h']:.4f}`",
    )

# ── Статистика ────────────────────────────────────────────────
@bot.message_handler(func=lambda m: m.text == "📊 Статистика 24ч")
def cmd_stats(message):
    data = get_hype_data()
    if not data:
        bot.send_message(message.chat.id, "❌ Не удалось получить данные. Попробуй позже.")
        return
    sign7 = "+" if data["change_7d"] >= 0 else ""
    bot.send_message(
        message.chat.id,
        f"📊 *Статистика HYPE*\n\n"
        f"Объём 24ч:       `${data['volume']:>15,.0f}`\n"
        f"Капитализация: `${data['cap']:>15,.0f}`\n\n"
        f"Изм. 24ч: `{'+' if data['change_24h']>=0 else ''}{data['change_24h']:.2f}%`\n"
        f"Изм. 7д:    `{sign7}{data['change_7d']:.2f}%`",
    )

# ── Уведомления ───────────────────────────────────────────────
@bot.message_handler(func=lambda m: m.text == "🔔 Уведомления")
def cmd_notify(message):
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("⚡ 1% за 10 минут",  callback_data="price_1"),
        types.InlineKeyboardButton("🔥 2% за 10 минут",  callback_data="price_2"),
        types.InlineKeyboardButton("🚨 5% за 10 минут",  callback_data="price_5"),
        types.InlineKeyboardButton("💥 10% за 10 минут", callback_data="price_10"),
    )
    current = price_subscribers.get(message.chat.id)
    note = f"\n\n_Сейчас активно: {current}%_" if current else ""
    bot.send_message(
        message.chat.id,
        f"🔔 *Уведомления о резком движении цены*\n\n"
        f"Выбери порог изменения за ~10 минут:{note}",
        reply_markup=markup,
    )

# ── Отписаться ────────────────────────────────────────────────
@bot.message_handler(func=lambda m: m.text == "❌ Отписаться")
def cmd_unsub(message):
    cid = message.chat.id
    if cid in price_subscribers:
        price_subscribers.pop(cid, None)
        subscriber_base.pop(cid, None)
        bot.send_message(cid, "✅ Уведомления отключены.")
    else:
        bot.send_message(cid, "ℹ️ У тебя не было активных уведомлений.")

# ── Помощь ────────────────────────────────────────────────────
@bot.message_handler(func=lambda m: m.text == "ℹ️ Помощь")
def cmd_help(message):
    bot.send_message(
        message.chat.id,
        "📖 *Как пользоваться ботом*\n\n"
        "💰 *Курс HYPE* — текущая цена + диапазон дня\n"
        "📊 *Статистика 24ч* — объём, капитализация, изменения\n"
        "🔔 *Уведомления* — оповещение при резком скачке цены\n"
        "❌ *Отписаться* — выключить уведомления\n\n"
        "_Данные обновляются раз в 2 минуты (CoinGecko API)_",
    )

# ── Callback от инлайн-кнопок ────────────────────────────────
@bot.callback_query_handler(func=lambda call: call.data.startswith("price_"))
def cb_price_threshold(call):
    bot.answer_callback_query(call.id)
    try:
        threshold = int(call.data.split("_")[1])
        cid = call.message.chat.id
        price_subscribers[cid] = threshold

        # Фиксируем базовую цену прямо сейчас
        data = get_hype_data()
        if data:
            subscriber_base[cid] = (data["price"], time.time())

        bot.send_message(
            cid,
            f"✅ *Уведомления включены!*\n\n"
            f"Пришлю сигнал, если HYPE изменится на *{threshold}%* за 10 минут.\n"
            f"_Базовая цена зафиксирована: ${data['price']:.4f}_" if data else
            f"✅ Уведомления включены на {threshold}%.",
        )
    except Exception as e:
        print(f"[Callback error] {e}")

# ── Неизвестный текст ─────────────────────────────────────────
@bot.message_handler(func=lambda m: True)
def fallback(message):
    bot.send_message(
        message.chat.id,
        "🤔 Не понял команду. Воспользуйся кнопками ниже.",
        reply_markup=MAIN_MARKUP,
    )

# ── Монитор цены (фоновый поток) ─────────────────────────────
def price_monitor():
    """
    Каждые 2 минуты проверяем цену.
    Для каждого подписчика сравниваем с ценой, 
    зафиксированной 10 минут назад (скользящее окно).
    """
    # Кольцевой буфер: последние 6 отсчётов (= 12 минут)
    price_history: list[tuple[float, float]] = []  # (price, timestamp)

    while True:
        data = get_hype_data()
        now  = time.time()

        if data:
            price_history.append((data["price"], now))
            # Оставляем только записи за последние 15 минут
            price_history = [(p, t) for p, t in price_history if now - t <= 900]

            for cid, threshold in list(price_subscribers.items()):
                base_price, base_time = subscriber_base.get(cid, (0, 0))

                # Берём базу ~10 минут назад из истории
                ten_min_ago = [p for p, t in price_history if 540 <= now - t <= 780]
                if ten_min_ago:
                    base_price = ten_min_ago[0]

                if base_price > 0:
                    change_pct = (data["price"] - base_price) / base_price * 100

                    if abs(change_pct) >= threshold:
                        direction = "вырос 🚀" if change_pct > 0 else "упал 🔻"
                        alert = (
                            f"⚡ *СИГНАЛ: HYPE {direction}*\n\n"
                            f"Изменение за ~10 мин: `{change_pct:+.2f}%`\n"
                            f"Цена сейчас: `${data['price']:.4f}`\n"
                            f"Было ~10 мин назад: `${base_price:.4f}`"
                        )
                        try:
                            bot.send_message(cid, alert)
                            # После сигнала сдвигаем базу вперёд
                            subscriber_base[cid] = (data["price"], now)
                        except Exception:
                            price_subscribers.pop(cid, None)
                            subscriber_base.pop(cid, None)

        time.sleep(120)

threading.Thread(target=price_monitor, daemon=True).start()

# ── Запуск ────────────────────────────────────────────────────
print("✅ Бот запущен...")
while True:
    try:
        bot.polling(none_stop=True, timeout=30, long_polling_timeout=20)
    except Exception as e:
        print(f"[Polling error] {e} — перезапуск через 5с")
        time.sleep(5)
