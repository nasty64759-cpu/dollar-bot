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

TOKEN = os.getenv("BOT_TOKEN", "8838571832:AAElqHv_qPr8EUY42vJh0EQBQDU7rAGqfRg")

bot = telebot.TeleBot(TOKEN, parse_mode="Markdown")

# Сбрасываем вебхук и убиваем старые сессии при старте
print("[Init] сброс вебхука и старых сессий...")
try:
    bot.remove_webhook()
    time.sleep(1)
    bot.get_updates(offset=-1)  # сбрасывает очередь
    print("[Init] OK")
except Exception as e:
    print(f"[Init] ошибка сброса: {e}")

# ── Глобальный кэш ───────────────────────────────────────────
_cache_lock   = threading.Lock()
_latest: dict | None = None
_price_history: list = []   # [(price, timestamp)]

def get_cached() -> dict | None:
    with _cache_lock:
        return dict(_latest) if _latest else None

# ── Подписчики ───────────────────────────────────────────────
price_subscribers: dict[int, int]   = {}
subscriber_base:   dict[int, float] = {}

# ── Меню ─────────────────────────────────────────────────────
def main_markup():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    m.add("💰 Курс HYPE", "📊 Статистика 24ч")
    m.add("🔔 Уведомления", "❌ Отписаться")
    m.add("ℹ️ Помощь")
    return m

# ── CoinGecko ─────────────────────────────────────────────────
def _fetch_hype() -> dict | None:
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/coins/hyperliquid",
            timeout=10,
        )
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
    if change >= 3:  return "🚀"
    if change >= 0:  return "📈"
    if change >= -3: return "📉"
    return "🔻"

# ── /start ────────────────────────────────────────────────────
@bot.message_handler(commands=["start"])
def cmd_start(message):
    bot.send_message(
        message.chat.id,
        "👋 *HYPE Monitor Bot*\n\n"
        "Слежу за ценой монеты *Hyperliquid (HYPE)* в реальном времени.\n\n"
        "Выбери действие на клавиатуре ниже 👇",
        reply_markup=main_markup(),
    )

# ── Курс ──────────────────────────────────────────────────────
@bot.message_handler(func=lambda m: m.text == "💰 Курс HYPE")
def cmd_price(message):
    data = get_cached()
    if not data:
        bot.send_message(message.chat.id, "⏳ Данные ещё загружаются, подожди ~10 секунд.")
        return
    em   = trend_emoji(data["change_24h"])
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
    data = get_cached()
    if not data:
        bot.send_message(message.chat.id, "⏳ Данные ещё загружаются, подожди ~10 секунд.")
        return
    s7  = "+" if data["change_7d"]  >= 0 else ""
    s24 = "+" if data["change_24h"] >= 0 else ""
    bot.send_message(
        message.chat.id,
        f"📊 *Статистика HYPE*\n\n"
        f"Объём 24ч:       `${data['volume']:,.0f}`\n"
        f"Капитализация: `${data['cap']:,.0f}`\n\n"
        f"Изм. 24ч: `{s24}{data['change_24h']:.2f}%`\n"
        f"Изм. 7д:    `{s7}{data['change_7d']:.2f}%`",
    )

# ── Уведомления — показываем меню ────────────────────────────
@bot.message_handler(func=lambda m: m.text == "🔔 Уведомления")
def cmd_notify(message):
    current = price_subscribers.get(message.chat.id)
    note    = f"\n\n_Сейчас активно: {current}%_" if current else ""

    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton("⚡ 1%",  callback_data="p1"))
    markup.row(types.InlineKeyboardButton("🔥 2%",  callback_data="p2"))
    markup.row(types.InlineKeyboardButton("🚨 5%",  callback_data="p5"))
    markup.row(types.InlineKeyboardButton("💥 10%", callback_data="p10"))

    bot.send_message(
        message.chat.id,
        f"🔔 *Уведомления о резком движении цены*\n\n"
        f"Выбери порог изменения за ~10 минут:{note}",
        reply_markup=markup,
    )

# ── Callback — МАКСИМАЛЬНО простой, без лишней логики ────────
@bot.callback_query_handler(func=lambda call: call.data.startswith("p"))
def cb_threshold(call):
    print(f"[CB] получен callback: {call.data} от {call.message.chat.id}")

    # Шаг 1 — СРАЗУ отвечаем Telegram, убираем "Загрузка..."
    try:
        bot.answer_callback_query(call.id)
        print("[CB] answer_callback_query OK")
    except Exception as e:
        print(f"[CB] answer_callback_query FAILED: {e}")

    # Шаг 2 — парсим порог
    try:
        threshold = int(call.data[1:])   # "p1" -> 1, "p10" -> 10
    except Exception as e:
        print(f"[CB] parse error: {e}")
        return

    cid = call.message.chat.id
    price_subscribers[cid] = threshold

    data = get_cached()
    if data:
        subscriber_base[cid] = data["price"]
        extra = f"\n_Базовая цена: `${data['price']:.4f}`_"
    else:
        extra = ""

    # Шаг 3 — отправляем подтверждение
    try:
        bot.send_message(
            cid,
            f"✅ *Уведомления включены!*\n\n"
            f"Пришлю сигнал при изменении HYPE на *{threshold}%* за 10 минут.{extra}",
        )
        print(f"[CB] send_message OK для {cid}")
    except Exception as e:
        print(f"[CB] send_message FAILED: {e}")

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

# ── Fallback ──────────────────────────────────────────────────
@bot.message_handler(func=lambda m: True)
def fallback(message):
    bot.send_message(
        message.chat.id,
        "🤔 Не понял команду. Воспользуйся кнопками ниже.",
        reply_markup=main_markup(),
    )

# ── Фоновый монитор ───────────────────────────────────────────
def price_monitor():
    global _latest, _price_history

    # Первый запрос — сразу при старте
    print("[Monitor] первый запрос к CoinGecko...")
    data = _fetch_hype()
    if data:
        with _cache_lock:
            _latest = data
            _price_history.append((data["price"], time.time()))
        print(f"[Monitor] кэш заполнен: ${data['price']:.4f}")
    else:
        print("[Monitor] первый запрос не удался")

    while True:
        time.sleep(120)
        data = _fetch_hype()
        now  = time.time()
        if data:
            with _cache_lock:
                _latest = data
                _price_history.append((data["price"], now))
                _price_history = [(p, t) for p, t in _price_history if now - t <= 900]
                history_snap = list(_price_history)

            for cid, threshold in list(price_subscribers.items()):
                base = subscriber_base.get(cid, 0)
                if base == 0:
                    candidates = [p for p, t in history_snap if 540 <= now - t <= 780]
                    if candidates:
                        base = candidates[0]
                if base > 0:
                    change_pct = (data["price"] - base) / base * 100
                    if abs(change_pct) >= threshold:
                        direction = "вырос 🚀" if change_pct > 0 else "упал 🔻"
                        try:
                            bot.send_message(
                                cid,
                                f"⚡ *СИГНАЛ: HYPE {direction}*\n\n"
                                f"Изменение за ~10 мин: `{change_pct:+.2f}%`\n"
                                f"Цена сейчас:          `${data['price']:.4f}`\n"
                                f"Было ~10 мин назад: `${base:.4f}`",
                            )
                            subscriber_base[cid] = data["price"]
                        except Exception:
                            price_subscribers.pop(cid, None)
                            subscriber_base.pop(cid, None)

threading.Thread(target=price_monitor, daemon=True).start()

# ── Запуск ────────────────────────────────────────────────────
print("✅ Бот запущен...")
while True:
    try:
        bot.polling(none_stop=True, timeout=30, long_polling_timeout=20)
    except Exception as e:
        print(f"[Polling error] {e} — перезапуск через 5с")
        time.sleep(5)
