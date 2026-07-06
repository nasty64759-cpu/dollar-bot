# ============================================================
#  HYPE Monitor Bot — WEBHOOK режим
# ============================================================

import telebot
import requests
import time
import threading
import os
import io
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from datetime import datetime, timezone
from telebot import types
from flask import Flask, request as flask_request

TOKEN  = os.getenv("TOKEN")
DOMAIN = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
PORT   = int(os.getenv("PORT", 8080))

bot = telebot.TeleBot(TOKEN, parse_mode="Markdown")
app = Flask(__name__)

# ── Дедупликация апдейтов ────────────────────────────────────
_seen = set()
_seen_lock = threading.Lock()

def is_duplicate(update_id):
    with _seen_lock:
        if update_id in _seen:
            return True
        _seen.add(update_id)
        if len(_seen) > 200:
            oldest = sorted(_seen)[:100]
            for x in oldest:
                _seen.discard(x)
        return False

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    data = flask_request.get_json(silent=True)
    if not data:
        return "ok", 200
    uid = data.get("update_id", 0)
    if is_duplicate(uid):
        return "ok", 200
    
    keys = [k for k in data.keys() if k != "update_id"]
    print(f"[WH] update_id={uid} type={keys}")
    
    threading.Thread(target=lambda: _handle(data), daemon=True).start()
    return "ok", 200

def _handle(data):
    try:
        update = telebot.types.Update.de_json(data)
        bot.process_new_updates([update])
    except Exception as e:
        print(f"[WH] handle error: {e}")

@app.route("/")
def health():
    return "HYPE Bot running", 200

# ── Кэш ──────────────────────────────────────────────────────
_cache_lock = threading.Lock()
_latest: dict | None = None
_price_history: list = []  # [(price, timestamp)]

def get_cached():
    with _cache_lock:
        return dict(_latest) if _latest else None

# ── Подписчики с сохранением ─────────────────────────────────
SUBS_FILE = "subscribers.json"
subscribers: set = set()
sub_base: dict[int, float] = {}  # chat_id -> цена при подписке

def load_subscribers():
    global subscribers, sub_base
    try:
        with open(SUBS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            subscribers.update(data.get("subscribers", []))
            sub_base.update({int(k): v for k, v in data.get("sub_base", {}).items()})
        print(f"[Subs] Загружено {len(subscribers)} подписчиков")
    except Exception:
        pass

def save_subscribers():
    try:
        with open(SUBS_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "subscribers": list(subscribers),
                "sub_base": sub_base
            }, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Subs] Ошибка сохранения: {e}")

# ── Меню ─────────────────────────────────────────────────────
def main_markup():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    m.add("💰 Курс HYPE", "📊 Статистика 24ч")
    m.add("🔔 Уведомления 2%", "❌ Отписаться")
    m.add("ℹ️ Помощь")
    return m

# ── CoinGecko ─────────────────────────────────────────────────
def _fetch_hype():
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/coins/hyperliquid",
            timeout=10
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
        print(f"[CoinGecko] error: {e}")
        return None

def trend_emoji(c):
    return "🚀" if c>=3 else "📈" if c>=0 else "📉" if c>=-3 else "🔻"

# ── Свечи ────────────────────────────────────────────────────
def get_candles(hours=4):
    now_ms   = int(time.time()*1000)
    start_ms = now_ms - hours*3600*1000
    try:
        r = requests.post("https://api.hyperliquid.xyz/info",
            json={"type":"candleSnapshot","req":{"coin":"HYPE","interval":"5m",
                  "startTime":start_ms,"endTime":now_ms}}, timeout=12)
        raw = r.json()
        if isinstance(raw, list) and len(raw) > 5:
            print(f"[Candles] OK: {len(raw)}")
            return [{"t":c["t"],"o":float(c["o"]),"h":float(c["h"]),
                     "l":float(c["l"]),"c":float(c["c"]),"v":float(c["v"])} for c in raw]
    except Exception as e:
        print(f"[Candles] error: {e}")
    return None
    
    def get_pivot_levels():
    """Берём последние 3 дневные свечи и считаем ключевые уровни"""
    try:
        now_ms   = int(time.time() * 1000)
        start_ms = now_ms - 4 * 86400 * 1000  # 4 дня назад
        r = requests.post("https://api.hyperliquid.xyz/info",
            json={"type": "candleSnapshot",
                  "req": {"coin": "HYPE", "interval": "1d",
                          "startTime": start_ms, "endTime": now_ms}},
            timeout=10)
        days = r.json()
        if not days or len(days) < 2:
            return None

        # Берём последние 3 завершённых дня (не текущий)
        completed = days[:-1][-3:]

        highs  = [float(d['h']) for d in completed]
        lows   = [float(d['l']) for d in completed]
        closes = [float(d['c']) for d in completed]

        # Pivot на основе среднего за несколько дней
        H = max(highs)
        L = min(lows)
        C = closes[-1]  # последний закрытый день

        P  = (H + L + C) / 3
        R1 = 2 * P - L
        R2 = P + (H - L)
        S1 = 2 * P - H
        S2 = P - (H - L)

        # Локальные экстремумы последних дней
        local_high = max(highs[-2:])  # макс последних 2 дней
        local_low  = min(lows[-2:])   # мин последних 2 дней

        return {
            "P": P, "R1": R1, "R2": R2, "S1": S1, "S2": S2,
            "local_high": local_high, "local_low": local_low
        }
    except Exception as e:
        print(f"[Pivots] error: {e}")
        return None

    
    

# ── График (увеличен шрифт цены) ─────────────────────────────
def build_chart(candles, price):
    o=[c['o'] for c in candles]; h=[c['h'] for c in candles]
    l=[c['l'] for c in candles]; cl=[c['c'] for c in candles]
    v=[c['v'] for c in candles]
    t=[datetime.fromtimestamp(c['t']/1000, tz=timezone.utc) for c in candles]
    n = len(candles)
    
    BG,GRID,UP,DOWN,TEXT,PL = '#131722','#1e2638','#26a69a','#ef5350','#d1d4dc','#f0c040'
    
    fig = plt.figure(figsize=(12,7), facecolor=BG)
    ax = fig.add_axes([0.02, 0.18, 0.84, 0.72], facecolor=BG)
    av = fig.add_axes([0.02, 0.05, 0.84, 0.11], facecolor=BG)

    for a in (ax, av):
        a.tick_params(colors=TEXT, labelsize=8)
        for s in a.spines.values():
            s.set_color(GRID)

    for i, (oi, hi, li, ci, vi) in enumerate(zip(o, h, l, cl, v)):
        col = UP if ci >= oi else DOWN
        ax.add_patch(Rectangle((i-.3, min(oi, ci)), .6, max(abs(ci-oi), .001), color=col, zorder=3))
        ax.plot([i, i], [li, min(oi, ci)], color=col, linewidth=1, zorder=2)
        ax.plot([i, i], [max(oi, ci), hi], color=col, linewidth=1, zorder=2)
        av.add_patch(Rectangle((i-.3, 0), .6, vi, 
                             color='#1a4a47' if ci >= oi else '#4a1a1a', zorder=2))

    # Получаем и рисуем уровни
    levels = get_pivot_levels()
    if levels:
        ymin, ymax = min(l) - pp, max(h) + pp

        def draw_level(val, color, label, ls='--', lw=0.8):
            """Рисуем линию только если она в диапазоне графика"""
            if ymin <= val <= ymax:
                ax.axhline(val, color=color, linewidth=lw,
                           linestyle=ls, zorder=3, alpha=0.8)
                ax.text(n + 0.5, val, label, color=color,
                        fontsize=7, va='center',
                        bbox=dict(boxstyle='round,pad=0.2',
                                  facecolor='#131722', edgecolor=color,
                                  alpha=0.85))

        # Глобальные уровни (Pivot Points)
        draw_level(levels["R2"], '#ff4444', 'R2', lw=1.0)
        draw_level(levels["R1"], '#ff8888', 'R1')
        draw_level(levels["P"],  '#ffffff', ' P ', ls='-', lw=0.6)
        draw_level(levels["S1"], '#88cc88', 'S1')
        draw_level(levels["S2"], '#44aa44', 'S2', lw=1.0)

        # Локальные уровни последних 2 дней
        draw_level(levels["local_high"], '#ffaa00', 'LH', ls=':', lw=1.2)
        draw_level(levels["local_low"],  '#00aaff', 'LL', ls=':', lw=1.2)


    # Увеличенный шрифт цены
    ax.axhline(price, color=PL, linewidth=1, linestyle='--', zorder=4)
    ax.text(n + 0.8, price, f'${price:.4f}', color='#131722', fontsize=11,  # ← увеличен
            va='center', fontweight='bold',
            bbox=dict(boxstyle='round,pad=0.4', facecolor=PL, edgecolor='none'))

    ax.yaxis.grid(True, color=GRID, linewidth=0.5, zorder=0)
    av.yaxis.grid(True, color=GRID, linewidth=0.5, zorder=0)

    step = max(1, n//8)
    xt = list(range(0, n, step))
    ax.set_xticks([])
    av.set_xticks(xt)
    av.set_xticklabels([t[i].strftime('%H:%M') for i in xt], color=TEXT, fontsize=7)

    pp = (max(h) - min(l)) * 0.05
    ax.set_xlim(-1, n + 3)
    ax.set_ylim(min(l) - pp, max(h) + pp)
    av.set_xlim(-1, n + 3)
    av.set_ylim(0, max(v) * 1.3)

    ax.yaxis.set_label_position('right')
    ax.yaxis.tick_right()
    av.yaxis.set_label_position('right')
    av.yaxis.tick_right()

    chg = (cl[-1] - o[0]) / o[0] * 100
    ax.set_title(f"HYPE/USDC  •  5м  •  4ч       {'+' if chg>=0 else ''}{chg:.2f}%",
                 color=TEXT, fontsize=11, loc='left', pad=8, fontweight='bold')

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=130, facecolor=BG)
    plt.close(fig)
    buf.seek(0)
    return buf

# ── Команды ───────────────────────────────────────────────────
@bot.message_handler(commands=["start"])
def cmd_start(message):
    bot.send_message(message.chat.id,
        "👋 *HYPE Monitor Bot*\n\nСлежу за ценой *Hyperliquid (HYPE)*.\n\nВыбери действие 👇",
        reply_markup=main_markup())

@bot.message_handler(func=lambda m: m.text == "💰 Курс HYPE")
def cmd_price(message):
    data = get_cached()
    if not data:
        bot.send_message(message.chat.id, "⏳ Загружаю данные, подожди ~10 сек...")
        return

    em = trend_emoji(data["change_24h"])
    s = "+" if data["change_24h"] >= 0 else ""
    caption = (f"💰 *HYPE / USD*\n\n"
               f"Цена:         `${data['price']:.4f}`\n"
               f"Изм. 24ч:  {em} `{s}{data['change_24h']:.2f}%`\n"
               f"Макс. 24ч: `${data['high_24h']:.4f}`\n"
               f"Мин. 24ч:   `${data['low_24h']:.4f}`")

    candles = get_candles(4)
    if candles and len(candles) > 5:
        try:
            bot.send_photo(message.chat.id, build_chart(candles, data["price"]),
                           caption=caption, parse_mode="Markdown")
            return
        except Exception as e:
            print(f"[Chart] error: {e}")
    
    bot.send_message(message.chat.id, caption)

@bot.message_handler(func=lambda m: m.text == "📊 Статистика 24ч")
def cmd_stats(message):
    data = get_cached()
    if not data:
        bot.send_message(message.chat.id, "⏳ Загружаю данные, подожди ~10 сек...")
        return
    s7 = "+" if data["change_7d"] >= 0 else ""
    s24 = "+" if data["change_24h"] >= 0 else ""
    bot.send_message(message.chat.id,
        f"📊 *Статистика HYPE*\n\n"
        f"Объём 24ч:       `${data['volume']:,.0f}`\n"
        f"Капитализация: `${data['cap']:,.0f}`\n\n"
        f"Изм. 24ч: `{s24}{data['change_24h']:.2f}%`\n"
        f"Изм. 7д:    `{s7}{data['change_7d']:.2f}%`")

@bot.message_handler(func=lambda m: m.text == "🔔 Уведомления 2%")
def cmd_notify(message):
    cid = message.chat.id
    if cid in subscribers:
        bot.send_message(cid, "✅ Уведомления уже включены!\n\nПришлю сигнал если HYPE изменится на 2%+ за 10 минут.")
    else:
        subscribers.add(cid)
        data = get_cached()
        if data:
            sub_base[cid] = data["price"]
        save_subscribers()
        
        msg = f"✅ *Уведомления включены!*\n\nПришлю сигнал если HYPE изменится на *2%+* за 10 минут.\n"
        if data:
            msg += f"Текущая цена: `${data['price']:.4f}`"
        bot.send_message(cid, msg, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "❌ Отписаться")
def cmd_unsub(message):
    cid = message.chat.id
    if cid in subscribers:
        subscribers.discard(cid)
        sub_base.pop(cid, None)
        save_subscribers()
        bot.send_message(cid, "✅ Уведомления отключены.")
    else:
        bot.send_message(cid, "ℹ️ У тебя не было активных уведомлений.")

@bot.message_handler(func=lambda m: m.text == "ℹ️ Помощь")
def cmd_help(message):
    bot.send_message(message.chat.id,
        "📖 *Как пользоваться ботом*\n\n"
        "💰 *Курс HYPE* — цена + график 5м за 4 часа\n"
        "📊 *Статистика 24ч* — объём, капитализация\n"
        "🔔 *Уведомления 2%* — сигнал при движении 2%+ за 10 мин\n"
        "❌ *Отписаться* — выключить уведомления\n\n"
        "_Данные обновляются раз в минуту_")

@bot.message_handler(func=lambda m: True)
def fallback(message):
    bot.send_message(message.chat.id, "🤔 Воспользуйся кнопками ниже.", reply_markup=main_markup())

# ── Монитор ───────────────────────────────────────────────────
def price_monitor():
    global _latest, _price_history
    print("[Monitor] Запуск мониторинга...")
    
    # Первый запрос
    data = _fetch_hype()
    if data:
        with _cache_lock:
            _latest = data
        print(f"[Monitor] Кэш инициализирован: ${data['price']:.4f}")

    while True:
        time.sleep(60)
        data = _fetch_hype()
        if not data:
            continue

        now = time.time()
        with _cache_lock:
            _latest = data
            _price_history.append((data["price"], now))
            _price_history = [(p, t) for p, t in _price_history if now - t <= 900]
            snap = list(_price_history)

        for cid in list(subscribers):
            # Берем самую старую цену в интервале ~10 минут назад
            old_prices = [p for p, t in snap if 540 <= now - t <= 660]
            base = old_prices[0] if old_prices else sub_base.get(cid, 0)

            if base > 0:
                chg = (data["price"] - base) / base * 100
                if abs(chg) >= 2.0:
                    d = "вырос 🚀" if chg > 0 else "упал 🔻"
                    try:
                        bot.send_message(cid,
                            f"⚡ *СИГНАЛ: HYPE {d}*\n\n"
                            f"Изменение за 10 мин: `{chg:+.2f}%`\n"
                            f"Цена: `${data['price']:.4f}`\n"
                            f"Было: `${base:.4f}`")
                        sub_base[cid] = data["price"]
                        save_subscribers()
                    except Exception:
                        subscribers.discard(cid)
                        sub_base.pop(cid, None)
                        save_subscribers()

# Загрузка подписчиков и запуск монитора
load_subscribers()
threading.Thread(target=price_monitor, daemon=True).start()

# ── Запуск ────────────────────────────────────────────────────
print("✅ Бот запущен...")
if DOMAIN:
    webhook_url = f"https://{DOMAIN}/{TOKEN}"
    bot.remove_webhook()
    time.sleep(1)
    bot.set_webhook(url=webhook_url, allowed_updates=["message", "callback_query"])
    print(f"[Webhook] установлен: {webhook_url}")
    app.run(host="0.0.0.0", port=PORT)
else:
    print("[Polling] режим")
    bot.remove_webhook()
    while True:
        try:
            bot.polling(none_stop=True, timeout=30, skip_pending=True)
        except Exception as e:
            print(f"[Polling] error: {e}")
            time.sleep(5)
