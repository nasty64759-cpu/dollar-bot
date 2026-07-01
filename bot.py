# ============================================================
#  HYPE Monitor Bot — WEBHOOK режим (Railway)
#  Зависимости: pip install pyTelegramBotAPI requests matplotlib flask
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
from matplotlib.patches import Rectangle
from datetime import datetime, timezone
from telebot import types
from flask import Flask, request as flask_request

TOKEN    = os.getenv("BOT_TOKEN", "8838571832:AAElqHv_qPr8EUY42vJh0EQBQDU7rAGqfRg")
# Railway автоматически даёт переменную RAILWAY_PUBLIC_DOMAIN
# Формат: your-app.up.railway.app
DOMAIN   = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
PORT     = int(os.getenv("PORT", 8080))

bot = telebot.TeleBot(TOKEN, parse_mode="Markdown")
app = Flask(__name__)

# ── Вебхук endpoint ──────────────────────────────────────────
@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    update = telebot.types.Update.de_json(flask_request.get_json())
    bot.process_new_updates([update])
    return "ok", 200

@app.route("/")
def health():
    return "HYPE Bot running", 200

# ── Глобальный кэш ───────────────────────────────────────────
_cache_lock = threading.Lock()
_latest: dict | None = None
_price_history: list = []

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

# ── Свечи (Hyperliquid → Bybit → Gate.io) ───────────────────
def get_hype_candles(hours: int = 4) -> list | None:
    now_ms   = int(time.time() * 1000)
    start_ms = now_ms - hours * 3600 * 1000

    # 1️⃣ Hyperliquid
    try:
        r = requests.post(
            "https://api.hyperliquid.xyz/info",
            json={"type": "candleSnapshot",
                  "req": {"coin": "HYPE", "interval": "5m",
                          "startTime": start_ms, "endTime": now_ms}},
            timeout=10
        )
        raw = r.json()
        if raw and isinstance(raw, list) and len(raw) > 5:
            print(f"[Candles] Hyperliquid OK: {len(raw)} свечей")
            return [{"t": c["t"], "o": float(c["o"]), "h": float(c["h"]),
                     "l": float(c["l"]), "c": float(c["c"]), "v": float(c["v"])}
                    for c in raw]
    except Exception as e:
        print(f"[Candles] Hyperliquid failed: {e}")

    # 2️⃣ Bybit
    try:
        r = requests.get(
            "https://api.bybit.com/v5/market/kline",
            params={"category": "spot", "symbol": "HYPEUSDT", "interval": "5",
                    "limit": min(200, 12 * hours), "start": start_ms, "end": now_ms},
            timeout=10
        )
        raw = r.json().get("result", {}).get("list", [])
        if raw and len(raw) > 5:
            candles = [{"t": int(c[0]), "o": float(c[1]), "h": float(c[2]),
                        "l": float(c[3]), "c": float(c[4]), "v": float(c[5])}
                       for c in sorted(raw, key=lambda x: int(x[0]))]
            print(f"[Candles] Bybit OK: {len(candles)} свечей")
            return candles
    except Exception as e:
        print(f"[Candles] Bybit failed: {e}")

    # 3️⃣ Gate.io
    try:
        r = requests.get(
            "https://api.gateio.ws/api/v4/spot/candlesticks",
            params={"currency_pair": "HYPE_USDT", "interval": "5m",
                    "limit": min(200, 12 * hours)},
            timeout=10
        )
        raw = r.json()
        if raw and len(raw) > 5:
            candles = [{"t": int(c[0]) * 1000, "o": float(c[5]), "h": float(c[3]),
                        "l": float(c[4]), "c": float(c[2]), "v": float(c[1])}
                       for c in sorted(raw, key=lambda x: int(x[0]))]
            print(f"[Candles] Gate.io OK: {len(candles)} свечей")
            return candles
    except Exception as e:
        print(f"[Candles] Gate.io failed: {e}")

    return None

# ── График ────────────────────────────────────────────────────
def build_chart(candles: list, current_price: float) -> io.BytesIO:
    opens   = [c['o'] for c in candles]
    highs   = [c['h'] for c in candles]
    lows    = [c['l'] for c in candles]
    closes  = [c['c'] for c in candles]
    volumes = [c['v'] for c in candles]
    times   = [datetime.fromtimestamp(c['t'] / 1000, tz=timezone.utc) for c in candles]
    n = len(candles)

    BG, GRID = '#131722', '#1e2638'
    UP, DOWN = '#26a69a', '#ef5350'
    TEXT, PRICE_LINE = '#d1d4dc', '#f0c040'

    fig = plt.figure(figsize=(12, 7), facecolor=BG)
    ax     = fig.add_axes([0.02, 0.18, 0.84, 0.72], facecolor=BG)
    ax_vol = fig.add_axes([0.02, 0.05, 0.84, 0.11], facecolor=BG)

    for ax_ in (ax, ax_vol):
        ax_.tick_params(colors=TEXT, labelsize=8)
        for spine in ax_.spines.values():
            spine.set_color(GRID)

    width = 0.6
    for i, (o, h, l, c, v) in enumerate(zip(opens, highs, lows, closes, volumes)):
        color = UP if c >= o else DOWN
        body_h = max(abs(c - o), 0.001)
        ax.add_patch(Rectangle((i - width/2, min(o,c)), width, body_h, color=color, zorder=3))
        ax.plot([i, i], [l, min(o,c)], color=color, linewidth=1, zorder=2)
        ax.plot([i, i], [max(o,c), h], color=color, linewidth=1, zorder=2)
        ax_vol.add_patch(Rectangle(
            (i - width/2, 0), width, v,
            color='#1a4a47' if c >= o else '#4a1a1a', zorder=2
        ))

    ax.axhline(current_price, color=PRICE_LINE, linewidth=0.9, linestyle='--', zorder=4)
    ax.text(n + 0.5, current_price, f'${current_price:.2f}',
            color='#131722', fontsize=8, va='center', fontweight='bold',
            bbox=dict(boxstyle='round,pad=0.3', facecolor=PRICE_LINE, edgecolor='none'))

    ax.yaxis.grid(True, color=GRID, linewidth=0.5, zorder=0)
    ax_vol.yaxis.grid(True, color=GRID, linewidth=0.5, zorder=0)

    step = max(1, n // 8)
    x_ticks = list(range(0, n, step))
    ax.set_xticks([])
    ax_vol.set_xticks(x_ticks)
    ax_vol.set_xticklabels([times[i].strftime('%H:%M') for i in x_ticks], color=TEXT, fontsize=7)

    price_pad = (max(highs) - min(lows)) * 0.05
    ax.set_xlim(-1, n + 3)
    ax.set_ylim(min(lows) - price_pad, max(highs) + price_pad)
    ax_vol.set_xlim(-1, n + 3)
    ax_vol.set_ylim(0, max(volumes) * 1.3)

    ax.yaxis.set_label_position('right')
    ax.yaxis.tick_right()
    ax_vol.yaxis.set_label_position('right')
    ax_vol.yaxis.tick_right()

    change = (closes[-1] - opens[0]) / opens[0] * 100
    sign   = '+' if change >= 0 else ''
    ax.set_title(f'HYPE/USDC  •  5м  •  4 часа       {sign}{change:.2f}%',
                 color=TEXT, fontsize=10, loc='left', pad=8, fontweight='bold')

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=130, facecolor=BG)
    plt.close(fig)
    buf.seek(0)
    return buf

# ── /start ────────────────────────────────────────────────────
@bot.message_handler(commands=["start"])
def cmd_start(message):
    bot.send_message(
        message.chat.id,
        "👋 *HYPE Monitor Bot*\n\nСлежу за ценой *Hyperliquid (HYPE)* в реальном времени.\n\nВыбери действие 👇",
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
    candles = get_hype_candles(4)
    if candles and len(candles) > 5:
        try:
            chart = build_chart(candles, data["price"])
            bot.send_photo(message.chat.id, chart, caption=caption, parse_mode="Markdown")
            return
        except Exception as e:
            print(f"[Chart error] {e}")
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
    markup.row(types.InlineKeyboardButton("⚡ 1% за 10 мин",  callback_data="p1"))
    markup.row(types.InlineKeyboardButton("🔥 3% за 10 мин",  callback_data="p3"))
    markup.row(types.InlineKeyboardButton("🚨 5% за 10 мин",  callback_data="p5"))
    markup.row(types.InlineKeyboardButton("💥 10% за 10 мин", callback_data="p10"))
    bot.send_message(
        message.chat.id,
        f"🔔 *Уведомления о движении цены*\n\n"
        f"Бот смотрит последние 10 свечей по 1 минуте.\n"
        f"Если цена изменится на выбранный % — пришлю сигнал.{note}",
        reply_markup=markup,
    )

# ── Callback ──────────────────────────────────────────────────
@bot.callback_query_handler(func=lambda call: call.data.startswith("p"))
def cb_threshold(call):
    print(f"[CB] получен: {call.data} от {call.message.chat.id}")
    bot.answer_callback_query(call.id)
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
            f"Порог: *{threshold}%* за 10 минут (10 свечей по 1м).{extra}",
        )
        print(f"[CB] OK для {cid}, порог {threshold}%")
    except Exception as e:
        print(f"[CB] error: {e}")

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
        "💰 *Курс HYPE* — цена + график 5м за 4 часа\n"
        "📊 *Статистика 24ч* — объём, капитализация\n"
        "🔔 *Уведомления* — сигнал при резком движении\n"
        "❌ *Отписаться* — выключить уведомления\n\n"
        "_Данные обновляются раз в 2 минуты_",
    )

# ── Fallback ──────────────────────────────────────────────────
@bot.message_handler(func=lambda m: True)
def fallback(message):
    bot.send_message(message.chat.id, "🤔 Воспользуйся кнопками ниже.", reply_markup=main_markup())

# ── Монитор цены — 1м свечи за последние 10 минут ────────────
def price_monitor():
    global _latest, _price_history
    print("[Monitor] первый запрос к CoinGecko...")
    data = _fetch_hype()
    if data:
        with _cache_lock:
            _latest = data
        print(f"[Monitor] кэш заполнен: ${data['price']:.4f}")

    while True:
        time.sleep(60)  # проверяем каждую минуту
        data = _fetch_hype()
        now  = time.time()
        if data:
            with _cache_lock:
                _latest = data
                _price_history.append((data["price"], now))
                # храним только последние 15 минут
                _price_history = [(p, t) for p, t in _price_history if now - t <= 900]
                history_snap = list(_price_history)

            # Проверяем подписчиков — смотрим 10 свечей назад (~10 минут)
            for cid, threshold in list(price_subscribers.items()):
                ten_min_ago = [p for p, t in history_snap if 540 <= now - t <= 660]
                base = ten_min_ago[0] if ten_min_ago else subscriber_base.get(cid, 0)

                if base > 0:
                    change_pct = (data["price"] - base) / base * 100
                    if abs(change_pct) >= threshold:
                        direction = "вырос 🚀" if change_pct > 0 else "упал 🔻"
                        try:
                            bot.send_message(
                                cid,
                                f"⚡ *СИГНАЛ: HYPE {direction}*\n\n"
                                f"Изменение за 10 мин: `{change_pct:+.2f}%`\n"
                                f"Цена сейчас: `${data['price']:.4f}`\n"
                                f"Было 10 мин назад: `${base:.4f}`",
                            )
                            subscriber_base[cid] = data["price"]
                        except Exception:
                            price_subscribers.pop(cid, None)
                            subscriber_base.pop(cid, None)

threading.Thread(target=price_monitor, daemon=True).start()

# ── Запуск: webhook если есть DOMAIN, иначе polling ──────────
print("✅ Бот запущен...")
if DOMAIN:
    webhook_url = f"https://{DOMAIN}/{TOKEN}"
    bot.remove_webhook()
    time.sleep(1)
    bot.set_webhook(url=webhook_url)
    print(f"[Webhook] установлен: {webhook_url}")
    app.run(host="0.0.0.0", port=PORT)
else:
    # Локальный запуск — polling
    print("[Polling] DOMAIN не задан, используем polling")
    bot.remove_webhook()
    while True:
        try:
            bot.polling(none_stop=True, timeout=30, long_polling_timeout=20, skip_pending=True)
        except Exception as e:
            print(f"[Polling error] {e} — перезапуск через 5с")
            time.sleep(5)
