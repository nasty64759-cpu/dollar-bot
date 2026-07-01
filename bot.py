# ============================================================
#  HYPE Monitor Bot
# ============================================================

import telebot
import requests
import time
import threading
import os
import io
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import Rectangle
from datetime import datetime, timezone
from telebot import types

TOKEN = os.getenv("BOT_TOKEN", "8838571832:AAElqHv_qPr8EUY42vJh0EQBQDU7rAGqfRg")

# ── Убиваем старые сессии через прямой HTTP запрос ──────────
# (до создания объекта TeleBot — иначе 409 при polling)
def delete_webhook():
    """Сбрасываем вебхук напрямую через HTTP — надёжнее чем через telebot"""
    for attempt in range(5):
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{TOKEN}/deleteWebhook",
                params={"drop_pending_updates": True},
                timeout=10
            )
            result = r.json()
            print(f"[Webhook] deleteWebhook: {result}")
            if result.get("ok"):
                return True
        except Exception as e:
            print(f"[Webhook] попытка {attempt+1} не удалась: {e}")
        time.sleep(3)
    return False

print("[Init] сброс вебхука...")
delete_webhook()
print("[Init] ждём завершения старого контейнера...")
time.sleep(15)
print("[Init] готово, стартуем")

bot = telebot.TeleBot(TOKEN, parse_mode="Markdown")

# ── Глобальный кэш ───────────────────────────────────────────
_cache_lock = threading.Lock()
_latest: dict | None = None
_price_history: list = []

def get_cached() -> dict | None:
    with _cache_lock:
        return dict(_latest) if _latest else None

# ── Свечи с Hyperliquid ───────────────────────────────────────
def get_hype_candles(interval: str = "5m", hours: int = 3) -> list | None:
    """
    Получаем OHLCV свечи с Hyperliquid Info API.
    interval: '5m', '15m', '1h'
    """
    try:
        now_ms    = int(time.time() * 1000)
        start_ms  = now_ms - hours * 3600 * 1000
        url = "https://api.hyperliquid.xyz/info"
        payload = {
            "type": "candleSnapshot",
            "req": {
                "coin":       "HYPE",
                "interval":   interval,
                "startTime":  start_ms,
                "endTime":    now_ms,
            }
        }
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        candles = r.json()   # [{t, o, h, l, c, v}, ...]
        return candles
    except Exception as e:
        print(f"[Candles error] {e}")
        return None

def build_chart(candles: list, current_price: float) -> io.BytesIO:
    """
    Рисуем свечной график в стиле TradingView dark theme.
    Возвращает BytesIO с PNG.
    """
    n = len(candles)
    opens   = [float(c['o']) for c in candles]
    highs   = [float(c['h']) for c in candles]
    lows    = [float(c['l']) for c in candles]
    closes  = [float(c['c']) for c in candles]
    volumes = [float(c['v']) for c in candles]
    times   = [datetime.fromtimestamp(c['t'] / 1000, tz=timezone.utc) for c in candles]

    # ── Цвета ──
    BG       = '#131722'
    GRID     = '#1e2638'
    UP       = '#26a69a'   # зелёный как TradingView
    DOWN     = '#ef5350'   # красный
    VOL_UP   = '#1a4a47'
    VOL_DOWN = '#4a1a1a'
    TEXT     = '#d1d4dc'
    PRICE_LINE = '#f0c040'

    fig, (ax, ax_vol) = plt.subplots(
        2, 1,
        figsize=(12, 7),
        gridspec_kw={'height_ratios': [3, 1], 'hspace': 0.04},
        facecolor=BG
    )

    for ax_ in (ax, ax_vol):
        ax_.set_facecolor(BG)
        ax_.tick_params(colors=TEXT, labelsize=8)
        ax_.spines[:].set_color(GRID)

    # ── Свечи ──
    width      = 0.6
    width_wick = 0.08

    for i, (o, h, l, c, v) in enumerate(zip(opens, highs, lows, closes, volumes)):
        color = UP if c >= o else DOWN
        vol_color = VOL_UP if c >= o else VOL_DOWN

        # Тело
        body_h = abs(c - o) if abs(c - o) > 0 else 0.001
        ax.add_patch(Rectangle(
            (i - width / 2, min(o, c)), width, body_h,
            color=color, zorder=3
        ))
        # Фитили
        ax.plot([i, i], [l, min(o, c)], color=color, linewidth=width_wick * 10, zorder=2)
        ax.plot([i, i], [max(o, c), h], color=color, linewidth=width_wick * 10, zorder=2)

        # Объём
        ax_vol.add_patch(Rectangle(
            (i - width / 2, 0), width, v,
            color=vol_color, zorder=2
        ))

    # ── Линия текущей цены ──
    ax.axhline(current_price, color=PRICE_LINE, linewidth=0.8, linestyle='--', zorder=4)
    ax.text(n + 0.3, current_price, f'${current_price:.2f}',
            color=PRICE_LINE, fontsize=8, va='center',
            bbox=dict(boxstyle='round,pad=0.2', facecolor=PRICE_LINE, alpha=0.85))

    # ── Сетка ──
    ax.yaxis.grid(True, color=GRID, linewidth=0.5, zorder=0)
    ax_vol.yaxis.grid(True, color=GRID, linewidth=0.5, zorder=0)

    # ── Метки времени на оси X ──
    step = max(1, n // 8)
    x_ticks = list(range(0, n, step))
    x_labels = [times[i].strftime('%H:%M') for i in x_ticks]
    ax.set_xticks([])
    ax_vol.set_xticks(x_ticks)
    ax_vol.set_xticklabels(x_labels, color=TEXT, fontsize=7)

    # ── Диапазон осей ──
    price_pad = (max(highs) - min(lows)) * 0.05
    ax.set_xlim(-1, n + 2)
    ax.set_ylim(min(lows) - price_pad, max(highs) + price_pad)
    ax_vol.set_xlim(-1, n + 2)
    ax_vol.set_ylim(0, max(volumes) * 1.3)

    ax.yaxis.set_label_position('right')
    ax.yaxis.tick_right()
    ax_vol.yaxis.set_label_position('right')
    ax_vol.yaxis.tick_right()

    # ── Заголовок ──
    change = ((closes[-1] - opens[0]) / opens[0]) * 100
    sign   = '+' if change >= 0 else ''
    color_title = UP if change >= 0 else DOWN
    ax.set_title(
        f'HYPE/USDC  •  5м  •  3 часа       {sign}{change:.2f}%',
        color=TEXT, fontsize=10, loc='left', pad=8,
        fontweight='bold'
    )

    # ── Метка объёма ──
    ax_vol.set_ylabel('Vol', color=TEXT, fontsize=7, rotation=0, labelpad=20)

    plt.tight_layout(pad=0.5)

    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=130, facecolor=BG, bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return buf

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
    caption = (
        f"💰 *HYPE / USD*\n\n"
        f"Цена:         `${data['price']:.4f}`\n"
        f"Изм. 24ч:  {em} `{sign}{data['change_24h']:.2f}%`\n"
        f"Макс. 24ч: `${data['high_24h']:.4f}`\n"
        f"Мин. 24ч:   `${data['low_24h']:.4f}`"
    )
    # Пробуем прикрепить график
    candles = get_hype_candles("5m", 3)
    if candles and len(candles) > 5:
        try:
            chart = build_chart(candles, data["price"])
            bot.send_photo(message.chat.id, chart, caption=caption, parse_mode="Markdown")
            return
        except Exception as e:
            print(f"[Chart error] {e}")
    # Fallback — просто текст
    bot.send_message(message.chat.id, caption)

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

# ── Уведомления ───────────────────────────────────────────────
@bot.message_handler(func=lambda m: m.text == "🔔 Уведомления")
def cmd_notify(message):
    current = price_subscribers.get(message.chat.id)
    note    = f"\n\n_Сейчас активно: {current}%_" if current else ""
    markup  = types.InlineKeyboardMarkup()
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

# ── Callback ──────────────────────────────────────────────────
@bot.callback_query_handler(func=lambda call: call.data.startswith("p"))
def cb_threshold(call):
    print(f"[CB] {call.data} от {call.message.chat.id}")
    try:
        bot.answer_callback_query(call.id)
    except Exception as e:
        print(f"[CB] answer error: {e}")

    try:
        threshold = int(call.data[1:])
        cid = call.message.chat.id
        price_subscribers[cid] = threshold
        data = get_cached()
        if data:
            subscriber_base[cid] = data["price"]
            extra = f"\n_Базовая цена: `${data['price']:.4f}`_"
        else:
            extra = ""
        bot.send_message(
            cid,
            f"✅ *Уведомления включены!*\n\n"
            f"Пришлю сигнал при изменении HYPE на *{threshold}%* за 10 минут.{extra}",
        )
    except Exception as e:
        print(f"[CB] error: {e}")

# ── Fallback ──────────────────────────────────────────────────
@bot.message_handler(func=lambda m: True)
def fallback(message):
    bot.send_message(message.chat.id, "🤔 Воспользуйся кнопками ниже.", reply_markup=main_markup())

# ── Фоновый монитор ───────────────────────────────────────────
def price_monitor():
    global _latest, _price_history
    print("[Monitor] первый запрос к CoinGecko...")
    data = _fetch_hype()
    if data:
        with _cache_lock:
            _latest = data
            _price_history.append((data["price"], time.time()))
        print(f"[Monitor] кэш заполнен: ${data['price']:.4f}")

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
                                f"Цена сейчас: `${data['price']:.4f}`\n"
                                f"Было: `${base:.4f}`",
                            )
                            subscriber_base[cid] = data["price"]
                        except Exception:
                            price_subscribers.pop(cid, None)
                            subscriber_base.pop(cid, None)

threading.Thread(target=price_monitor, daemon=True).start()

print("✅ Бот запущен...")
while True:
    try:
        bot.polling(none_stop=True, timeout=30, long_polling_timeout=20, skip_pending=True)
    except Exception as e:
        err = str(e)
        if "409" in err:
            print("[Polling] 409 — сбрасываем вебхук и ждём...")
            delete_webhook()
            time.sleep(10)
        else:
            print(f"[Polling error] {e} — перезапуск через 5с")
            time.sleep(5)
