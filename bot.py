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
import matplotlib.gridspec as gridspec
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

# ── Кэш цены ─────────────────────────────────────────────────
_cache_lock   = threading.Lock()
_latest: dict | None = None
_price_history: list = []

# ── Быстрые снимки (75 шт, каждые 8 сек) ────────────────────
_fast_snapshots = []
_fast_snap_lock = threading.Lock()

# ── Долгоживущие стены (каждые 30 сек, 4 часа) ───────────────
_persistent_walls = {}
_wall_snap_lock   = threading.Lock()

# ── Глубокий стакан (каждые 15 сек, 3 часа = 720 снимков) ───
_deep_snapshots = []
_deep_snap_lock = threading.Lock()
DEEP_MAX_SNAPS  = 720

def get_cached():
    with _cache_lock:
        return dict(_latest) if _latest else None

# ── Подписчики ────────────────────────────────────────────────
SUBS_FILE = "subscribers.json"
subscribers: set      = set()
sub_base: dict        = {}
bull_subscribers: set = set()

def load_subscribers():
    global subscribers, sub_base, bull_subscribers
    try:
        with open(SUBS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            subscribers.update(data.get("subscribers", []))
            sub_base.update({int(k): v for k, v in data.get("sub_base", {}).items()})
            bull_subscribers.update(data.get("bull_subscribers", []))
        print(f"[Subs] Загружено {len(subscribers)} подп., {len(bull_subscribers)} bull-подп.")
    except Exception:
        pass

def save_subscribers():
    try:
        with open(SUBS_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "subscribers":      list(subscribers),
                "sub_base":         sub_base,
                "bull_subscribers": list(bull_subscribers),
            }, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Subs] Ошибка сохранения: {e}")

# ── Меню ─────────────────────────────────────────────────────
def main_markup():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    m.add("💰 Курс HYPE",      "📊 Статистика 24ч")
    m.add("📖 Стакан заявок",  "🔭 Глубокий стакан")
    m.add("🔔 Уведомления 1%", "🐂 Bull Score 60+")
    m.add("❌ Отписаться",     "ℹ️ Помощь")
    return m

# ── CoinGecko ─────────────────────────────────────────────────
def _fetch_hype():
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/coins/hyperliquid",
            timeout=12)
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
def get_candles(hours=6):
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
                     "l":float(c["l"]),"c":float(c["c"]),"v":float(c["v"])}
                    for c in raw]
    except Exception as e:
        print(f"[Candles] error: {e}")
    return None

# ── Pivot Points ──────────────────────────────────────────────
def get_pivot_levels():
    try:
        now_ms   = int(time.time()*1000)
        start_ms = now_ms - 5*86400*1000
        r = requests.post("https://api.hyperliquid.xyz/info",
            json={"type":"candleSnapshot",
                  "req":{"coin":"HYPE","interval":"1d",
                         "startTime":start_ms,"endTime":now_ms}},
            timeout=12)
        days = r.json()
        if not isinstance(days, list) or len(days) < 2:
            return None
        completed = [d for d in days if d.get('c') and d.get('h') and d.get('l')]
        if len(completed) < 2:
            completed = days[-3:] if len(days) >= 3 else days
        highs  = [float(d['h']) for d in completed[-3:]]
        lows   = [float(d['l']) for d in completed[-3:]]
        closes = [float(d['c']) for d in completed[-3:]]
        if not highs: return None
        H = max(highs); L = min(lows); C = closes[-1]
        P  = (H+L+C)/3
        R1 = 2*P-L;  R2 = P+(H-L)
        S1 = 2*P-H;  S2 = P-(H-L)
        return {"P":P,"R1":R1,"R2":R2,"S1":S1,"S2":S2,
                "local_high": max(highs[-2:]) if len(highs)>=2 else H,
                "local_low":  min(lows[-2:])  if len(lows) >=2 else L}
    except Exception as e:
        print(f"[Pivots] error: {e}")
        return None

# ── RSI ───────────────────────────────────────────────────────
def calculate_rsi(prices, period=14):
    if len(prices) < period+1: return 50
    gains, losses = [], []
    for i in range(1, len(prices)):
        ch = prices[i]-prices[i-1]
        gains.append(ch if ch>0 else 0)
        losses.append(-ch if ch<0 else 0)
    ag = sum(gains[-period:])/period
    al = sum(losses[-period:])/period or 0.0001
    return 100-(100/(1+ag/al))

# ── EMA ───────────────────────────────────────────────────────
def calculate_ema(prices, period):
    """Возвращает список EMA той же длины что и prices (начало — None)"""
    if len(prices) < period:
        return [None]*len(prices)
    k   = 2/(period+1)
    ema = [None]*(period-1)
    ema.append(sum(prices[:period])/period)
    for p in prices[period:]:
        ema.append(p*k + ema[-1]*(1-k))
    return ema

# ── RSI серия (для графика) ───────────────────────────────────
def calculate_rsi_series(prices, period=14):
    """Возвращает список RSI той же длины что и prices"""
    result = [None]*period
    for i in range(period, len(prices)):
        result.append(calculate_rsi(prices[max(0,i-28):i+1]))
    return result

# ── Дивергенция RSI ───────────────────────────────────────────
def detect_rsi_divergence(candles):
    """
    Бычья: цена делает новый минимум, RSI — нет → разворот вверх.
    Медвежья: цена делает новый максимум, RSI — нет → разворот вниз.
    Возвращает: 'bullish', 'bearish' или None
    """
    if len(candles) < 20:
        return None
    closes = [c["c"] for c in candles]
    rsi_vals = []
    for i in range(14, len(closes)):
        rsi_vals.append(calculate_rsi(closes[max(0,i-28):i+1]))
    if len(rsi_vals) < 8:
        return None

    # Сравниваем первую и вторую половины последних 10 точек
    rc = closes[-10:]; rr = rsi_vals[-10:]
    p_lo1 = min(rc[:5]); p_lo2 = min(rc[5:])
    p_hi1 = max(rc[:5]); p_hi2 = max(rc[5:])
    r_lo1 = min(rr[:5]); r_lo2 = min(rr[5:])
    r_hi1 = max(rr[:5]); r_hi2 = max(rr[5:])

    if p_lo2 < p_lo1*0.998 and r_lo2 > r_lo1*1.01:
        return "bullish"
    if p_hi2 > p_hi1*1.002 and r_hi2 < r_hi1*0.99:
        return "bearish"
    return None

# ── Volume Profile ────────────────────────────────────────────
def calculate_volume_profile(candles, bins=20):
    """
    Считает объём по ценовым уровням (volume profile).
    Возвращает список (price_level, volume) отсортированный по цене.
    """
    if not candles: return []
    highs  = [c["h"] for c in candles]
    lows   = [c["l"] for c in candles]
    vols   = [c["v"] for c in candles]
    closes = [c["c"] for c in candles]

    price_min = min(lows)
    price_max = max(highs)
    if price_max == price_min: return []

    step = (price_max - price_min) / bins
    profile = [0.0]*bins

    for i, c in enumerate(candles):
        # Распределяем объём свечи равномерно по её диапазону
        lo, hi, v = c["l"], c["h"], c["v"]
        candle_range = hi - lo if hi > lo else step
        for b in range(bins):
            level_lo = price_min + b*step
            level_hi = level_lo + step
            overlap  = max(0, min(hi, level_hi) - max(lo, level_lo))
            profile[b] += v * (overlap / candle_range)

    result = []
    for b in range(bins):
        price_mid = price_min + (b+0.5)*step
        result.append((price_mid, profile[b]))
    return result

def get_poc(volume_profile):
    """Point of Control — уровень с максимальным объёмом"""
    if not volume_profile: return None
    return max(volume_profile, key=lambda x: x[1])[0]

# ── BTC корреляция ────────────────────────────────────────────
def get_btc_change():
    """Получаем изменение BTC за последние 15 минут через Hyperliquid"""
    try:
        now_ms   = int(time.time()*1000)
        start_ms = now_ms - 20*60*1000  # 20 минут назад
        r = requests.post("https://api.hyperliquid.xyz/info",
            json={"type":"candleSnapshot","req":{"coin":"BTC","interval":"5m",
                  "startTime":start_ms,"endTime":now_ms}}, timeout=8)
        raw = r.json()
        if isinstance(raw, list) and len(raw) >= 2:
            price_now  = float(raw[-1]["c"])
            price_15m  = float(raw[0]["o"])
            chg = (price_now - price_15m) / price_15m * 100
            return {"price": price_now, "change_15m": chg}
    except Exception as e:
        print(f"[BTC] error: {e}")
    return None

# ── Bull Score ────────────────────────────────────────────────
def calculate_bull_score(current_price, candles):
    if not candles or len(candles) < 30:
        return 50, "Нейтральный", "50%"
    closes  = [c["c"] for c in candles]
    volumes = [c["v"] for c in candles]
    levels  = get_pivot_levels()

    pivot_score = 50
    if levels:
        if current_price > levels["R1"]:   pivot_score = 88
        elif current_price > levels["P"]:  pivot_score = 68
        elif current_price < levels["S1"]: pivot_score = 28
        else:                              pivot_score = 52

    ch30 = (current_price-closes[-12])/closes[-12]*100 if len(closes)>=12 else 0
    ch60 = (current_price-closes[-25])/closes[-25]*100 if len(closes)>=25 else ch30
    momentum_score = max(15, min(92, 50 + ch30*2.8 + ch60*1.2))

    rsi = calculate_rsi(closes[-40:])
    rsi_score = 50
    if rsi < 32:    rsi_score = 82
    elif rsi < 45:  rsi_score = 65
    elif rsi > 70:  rsi_score = 22
    elif rsi > 62:  rsi_score = 38

    avg_vol    = sum(volumes[-48:])/48 if len(volumes)>=48 else 1
    recent_vol = sum(volumes[-12:])/12 if len(volumes)>=12 else 1
    vr = recent_vol/avg_vol
    volume_score = 82 if vr>1.9 else 68 if vr>1.4 else 50

    proximity_score = 50
    if levels:
        dists = [abs(current_price-v) for v in
                 [levels["R2"],levels["R1"],levels["P"],levels["S1"],levels["S2"]]]
        cl = min(dists)/current_price
        if cl < 0.006:   proximity_score = 78
        elif cl < 0.012: proximity_score = 62

    bull_score = round(max(10, min(95,
        pivot_score*0.25 + momentum_score*0.20 + rsi_score*0.15 +
        volume_score*0.15 + proximity_score*0.15 + 55*0.10)))

    if   bull_score >= 78: return bull_score, "Сильный бычий", "68-74%"
    elif bull_score >= 68: return bull_score, "Бычий",         "60-67%"
    elif bull_score >= 55: return bull_score, "Скорее бычий",  "53-59%"
    elif bull_score >= 45: return bull_score, "Нейтральный",   "48-52%"
    else:                  return bull_score, "Медвежий",       "35-47%"

# ── Паттерны свечей ───────────────────────────────────────────
def detect_candle_pattern(candles):
    if len(candles) < 3: return "none", 0
    last = candles[-1]; prev = candles[-2]
    o,h,l,c     = last["o"],last["h"],last["l"],last["c"]
    po,ph,pl,pc = prev["o"],prev["h"],prev["l"],prev["c"]
    body = abs(c-o); uw = h-max(o,c); lw = min(o,c)-l
    if o>c and po<pc and c>po and o<pc and body>(pc-po)*0.7: return "bullish_engulfing",75
    if o<c and po>pc and c<po and o>pc and body>(po-pc)*0.7: return "bearish_engulfing",75
    if lw>body*2 and uw<body*0.3 and c>o:                    return "hammer",70
    if uw>body*2 and lw<body*0.3 and c<o:                    return "shooting_star",70
    if lw>body*2.5 and c>o:                                  return "pinbar_bullish",65
    return "none", 0

# ── Конфлюэнс ────────────────────────────────────────────────
def find_confluence_setup(current_price, candles, levels):
    if not candles or not levels: return None, None
    bull_score, _, _ = calculate_bull_score(current_price, candles)
    pattern, pstr    = detect_candle_pattern(candles)
    book      = get_order_book()
    bid_walls = find_walls(book["bids"], 3.5) if book else []
    ask_walls = find_walls(book["asks"], 3.5) if book else []
    setup = None
    if (bull_score>=70 and current_price<=levels.get("S1",99999)*1.008
            and pattern in ["hammer","bullish_engulfing","pinbar_bullish"]):
        setup = {"direction":"LONG","strength":bull_score+pstr,
                 "level":"S1/S2",
                 "reason":f"{pattern.replace('_',' ').title()} + Bull Score {bull_score}"}
        if bid_walls: setup["reason"] += " + стена покупки"; setup["strength"]+=12
    elif (bull_score<=38 and current_price>=levels.get("R1",0)*0.992
            and pattern in ["shooting_star","bearish_engulfing"]):
        setup = {"direction":"SHORT","strength":(100-bull_score)+pstr,
                 "level":"R1/R2",
                 "reason":f"{pattern.replace('_',' ').title()} + Bull Score {bull_score}"}
        if ask_walls: setup["reason"] += " + стена продажи"; setup["strength"]+=12
    return setup, pattern

# ── Order Book ────────────────────────────────────────────────
def get_order_book():
    try:
        r = requests.post("https://api.hyperliquid.xyz/info",
            json={"type":"l2Book","coin":"HYPE","nSigFigs":5}, timeout=10)
        data   = r.json()
        levels = data.get("levels", [])
        if not levels or len(levels)<2: return None
        bids = sorted([(float(b["px"]),float(b["sz"])) for b in levels[0]],
                      key=lambda x: x[0], reverse=True)
        asks = sorted([(float(a["px"]),float(a["sz"])) for a in levels[1]],
                      key=lambda x: x[0])
        return {"bids":bids,"asks":asks}
    except Exception as e:
        print(f"[OrderBook] error: {e}")
        return None

def get_deep_order_book():
    try:
        r = requests.post("https://api.hyperliquid.xyz/info",
            json={"type":"l2Book","coin":"HYPE","nSigFigs":4}, timeout=10)
        data   = r.json()
        levels = data.get("levels", [])
        if not levels or len(levels)<2: return None
        bids = sorted([(float(b["px"]),float(b["sz"])) for b in levels[0]],
                      key=lambda x: x[0], reverse=True)
        asks = sorted([(float(a["px"]),float(a["sz"])) for a in levels[1]],
                      key=lambda x: x[0])
        return {"bids":bids,"asks":asks}
    except Exception as e:
        print(f"[DeepBook] error: {e}")
        return None

def find_walls(levels, threshold_multiplier=3.0):
    if not levels: return []
    sizes = [s for _,s in levels]
    avg   = sum(sizes)/len(sizes)
    return sorted([(p,s) for p,s in levels if s>=avg*threshold_multiplier],
                  key=lambda x: x[1], reverse=True)

# ── Быстрые снимки ───────────────────────────────────────────
def take_fast_snapshot(book):
    if not book: return
    with _fast_snap_lock:
        snap = {
            "timestamp": time.time(),
            "bids": {round(p,2): s for p,s in book["bids"][:60]},
            "asks": {round(p,2): s for p,s in book["asks"][:60]},
        }
        _fast_snapshots.append(snap)
        if len(_fast_snapshots) > 75:
            _fast_snapshots.pop(0)

def calculate_average_book():
    with _fast_snap_lock:
        snaps = list(_fast_snapshots)
    if not snaps: return None
    bid_vol, ask_vol = {}, {}
    for s in snaps:
        for p,v in s["bids"].items(): bid_vol[p] = bid_vol.get(p,0)+v
        for p,v in s["asks"].items(): ask_vol[p] = ask_vol.get(p,0)+v
    n = len(snaps)
    return {
        "bids": {p: v/n for p,v in bid_vol.items()},
        "asks": {p: v/n for p,v in ask_vol.items()},
    }

# ── Глубокие снимки ──────────────────────────────────────────
def take_deep_snapshot(book):
    if not book: return
    with _deep_snap_lock:
        snap = {
            "timestamp": time.time(),
            "bids": {round(p,1): s for p,s in book["bids"][:80]},
            "asks": {round(p,1): s for p,s in book["asks"][:80]},
        }
        _deep_snapshots.append(snap)
        if len(_deep_snapshots) > DEEP_MAX_SNAPS:
            _deep_snapshots.pop(0)

def calculate_deep_average_book():
    with _deep_snap_lock:
        snaps = list(_deep_snapshots)
    if not snaps: return None, 0
    bid_vol, ask_vol = {}, {}
    for s in snaps:
        for p,v in s["bids"].items(): bid_vol[p] = bid_vol.get(p,0)+v
        for p,v in s["asks"].items(): ask_vol[p] = ask_vol.get(p,0)+v
    n = len(snaps)
    return {
        "bids": {p: v/n for p,v in bid_vol.items()},
        "asks": {p: v/n for p,v in ask_vol.items()},
    }, n

# ── Долгоживущие стены ───────────────────────────────────────
def track_persistent_walls(book):
    if not book: return
    now = time.time()
    with _wall_snap_lock:
        for p in list(_persistent_walls.keys()):
            if now - _persistent_walls[p]["first_seen"] > 14400:
                del _persistent_walls[p]
        for side, lvls in [("bid",book["bids"][:40]),("ask",book["asks"][:40])]:
            for price, size in find_walls(lvls, 3.0):
                key = round(price, 2)
                if key in _persistent_walls:
                    _persistent_walls[key]["size"]      = max(_persistent_walls[key]["size"],size)
                    _persistent_walls[key]["last_seen"] = now
                else:
                    _persistent_walls[key] = {
                        "size":size,"first_seen":now,"last_seen":now,"side":side}

# ── Анализ стакана ────────────────────────────────────────────
def analyze_orderbook(book):
    if not book: return None
    bids = book["bids"][:30]; asks = book["asks"][:30]
    bv = sum(s for _,s in bids); av = sum(s for _,s in asks)
    delta = bv-av
    dr    = delta/(bv+av) if (bv+av)>0 else 0
    imb   = None
    if bv>av*1.8:    imb="strong_bid"
    elif av>bv*1.8:  imb="strong_ask"
    elif bv>av*1.35: imb="bid"
    elif av>bv*1.35: imb="ask"
    return {"delta":delta,"delta_ratio":dr,"imbalance":imb,
            "bid_volume":bv,"ask_volume":av,
            "bid_walls":find_walls(bids,3.5),"ask_walls":find_walls(asks,3.5)}

def get_orderbook_summary(analysis):
    if not analysis: return "Не удалось проанализировать стакан."
    t  = ""
    em = "🟢" if analysis["delta"]>0 else "🔴"
    t += f"{em} *Delta*: {analysis['delta']:,.0f} HYPE ({analysis['delta_ratio']:+.1%})\n"
    imb_map = {"strong_bid":"🟢 *Сильный перевес покупателей*",
               "strong_ask":"🔴 *Сильный перевес продавцов*",
               "bid":"🟢 Небольшой перевес покупателей",
               "ask":"🔴 Небольшой перевес продавцов"}
    if analysis["imbalance"]: t += imb_map[analysis["imbalance"]]+"\n"
    if analysis["bid_walls"]:
        t += "\n🟢 *Стены покупки:*\n"
        for p,s in analysis["bid_walls"][:2]: t += f"  `${p:.2f}` — {s:,.0f} HYPE\n"
    if analysis["ask_walls"]:
        t += "\n🔴 *Стены продажи:*\n"
        for p,s in analysis["ask_walls"][:2]: t += f"  `${p:.2f}` — {s:,.0f} HYPE\n"
    return t

# ── Алерты стен ───────────────────────────────────────────────
_last_wall_alert: dict = {}

def check_walls_and_notify():
    book = get_order_book()
    if not book: return
    now       = time.time()
    bid_walls = find_walls(book["bids"], 4.0)
    ask_walls = find_walls(book["asks"], 4.0)
    if not bid_walls and not ask_walls: return
    msg = "🧱 *КРУПНАЯ СТЕНА В СТАКАНЕ*\n\n"
    if ask_walls:
        msg += "🔴 *Продажа (сопротивление):*\n"
        for p,s in ask_walls[:3]: msg += f"`${p:.2f}` — {s:,.0f} HYPE\n"
    if bid_walls:
        msg += "\n🟢 *Покупка (поддержка):*\n"
        for p,s in bid_walls[:3]: msg += f"`${p:.2f}` — {s:,.0f} HYPE\n"
    for cid in list(subscribers):
        if now - _last_wall_alert.get(cid,0) < 1800: continue
        try:
            bot.send_message(cid, msg)
            _last_wall_alert[cid] = now
        except Exception: pass

# ── Тепловая карта (быстрый стакан) ──────────────────────────
def build_heatmap(book: dict, current_price: float) -> io.BytesIO:
    bids = [(float(p),float(s)) for p,s in book["bids"][:60]]
    asks = [(float(p),float(s)) for p,s in book["asks"][:60]]
    BG,TEXT,UP,DOWN,WALL = '#131722','#d1d4dc','#26a69a','#ef5350','#f0c040'
    fig, ax = plt.subplots(figsize=(11,7), facecolor=BG)
    ax.set_facecolor(BG)
    ax.tick_params(colors=TEXT, labelsize=9)
    for sp in ax.spines.values(): sp.set_color('#2a2e39')
    all_sizes = [s for _,s in bids+asks]
    avg_size  = sum(all_sizes)/len(all_sizes) if all_sizes else 1
    max_size  = max(all_sizes) if all_sizes else 1
    bar_h     = current_price*0.0004
    for p,s in asks:
        ax.barh(p,s,height=bar_h,color=WALL if s>=avg_size*3 else DOWN,alpha=0.85,zorder=3)
    for p,s in bids:
        ax.barh(p,s,height=bar_h,color=WALL if s>=avg_size*3 else UP,alpha=0.85,zorder=3)
    ax.axhline(current_price,color=WALL,linewidth=1.8,linestyle='--',zorder=5)
    ax.text(max_size*0.95,current_price+current_price*0.0005,
            f' ${current_price:.2f} ← Текущая',
            color=WALL,fontsize=11,va='bottom',fontweight='bold')
    price_range = current_price*0.008
    ax.set_ylim(current_price-price_range, current_price+price_range)
    ax.set_xlim(0, max_size*1.18)
    ax.yaxis.grid(True,color='#1e2638',linewidth=0.5,zorder=0)
    ax.set_xlabel('Объём (HYPE)',color=TEXT,fontsize=10)
    ax.set_ylabel('Цена ($)',    color=TEXT,fontsize=10)
    ax.set_title('📖 Стакан заявок HYPE  •  усреднено по 75 снимкам  •  диапазон ±0.8%\n'
                 '🟢 Покупки    🔴 Продажи    🟡 Крупная стена (3x среднего)',
                 color=TEXT,fontsize=10,loc='left',pad=8)
    for p,s in sorted(bids+asks,key=lambda x:x[1],reverse=True)[:5]:
        ax.annotate(f'{s:,.0f}',xy=(s,p),xytext=(5,0),
                    textcoords='offset points',color=TEXT,fontsize=8,va='center')
    plt.tight_layout(pad=1.0)
    buf = io.BytesIO()
    fig.savefig(buf,format='png',dpi=130,facecolor=BG)
    plt.close(fig); buf.seek(0)
    return buf

# ── Глубокая тепловая карта ───────────────────────────────────
def build_deep_heatmap(avg_book: dict, current_price: float, n_snaps: int) -> io.BytesIO:
    bids_raw = sorted([(float(p),float(s)) for p,s in avg_book["bids"].items()],
                      key=lambda x: x[0], reverse=True)
    asks_raw = sorted([(float(p),float(s)) for p,s in avg_book["asks"].items()],
                      key=lambda x: x[0])
    price_range = current_price * 0.05
    bids = [(p,s) for p,s in bids_raw if current_price-price_range <= p <= current_price]
    asks = [(p,s) for p,s in asks_raw if current_price <= p <= current_price+price_range]
    if not bids and not asks:
        bids = bids_raw[:40]; asks = asks_raw[:40]
    BG,TEXT,UP,DOWN,WALL = '#131722','#d1d4dc','#26a69a','#ef5350','#f0c040'
    fig, ax = plt.subplots(figsize=(12,9), facecolor=BG)
    ax.set_facecolor(BG)
    ax.tick_params(colors=TEXT, labelsize=9)
    for sp in ax.spines.values(): sp.set_color('#2a2e39')
    all_sizes = [s for _,s in bids+asks]
    if not all_sizes:
        plt.close(fig); raise ValueError("Нет данных")
    avg_size = sum(all_sizes)/len(all_sizes)
    max_size = max(all_sizes)
    bar_h = 0.08
    for p,s in asks:
        ax.barh(p,s,height=bar_h,color=WALL if s>=avg_size*2.5 else DOWN,alpha=0.85,zorder=3)
    for p,s in bids:
        ax.barh(p,s,height=bar_h,color=WALL if s>=avg_size*2.5 else UP,alpha=0.85,zorder=3)
    ax.axhline(current_price,color=WALL,linewidth=2.0,linestyle='--',zorder=5)
    ax.text(max_size*0.97,current_price+0.04,f' ${current_price:.2f} ← Текущая',
            color=WALL,fontsize=11,va='bottom',fontweight='bold')
    ax.set_ylim(current_price-price_range, current_price+price_range)
    ax.set_xlim(0, max_size*1.2)
    ax.yaxis.grid(True,color='#1e2638',linewidth=0.5,zorder=0)
    ax.set_xlabel('Объём (HYPE)',color=TEXT,fontsize=10)
    ax.set_ylabel('Цена ($)',    color=TEXT,fontsize=10)
    hours = round(n_snaps*15/3600,1)
    ax.set_title(
        f'🔭 Глубокий стакан HYPE  •  {n_snaps} снимков ({hours}ч памяти)  •  шаг $0.1\n'
        f'🟢 Покупки    🔴 Продажи    🟡 Стена (2.5x среднего)    диапазон ±5%',
        color=TEXT,fontsize=10,loc='left',pad=8)
    for p,s in sorted(bids+asks,key=lambda x:x[1],reverse=True)[:7]:
        ax.annotate(f'{s:,.0f}',xy=(s,p),xytext=(5,0),
                    textcoords='offset points',color=TEXT,fontsize=8,va='center',fontweight='bold')
    plt.tight_layout(pad=1.0)
    buf = io.BytesIO()
    fig.savefig(buf,format='png',dpi=130,facecolor=BG)
    plt.close(fig); buf.seek(0)
    return buf

# ── График свечей (EMA + RSI панель + Volume Profile) ─────────
def build_chart(candles, price):
    o=[c['o'] for c in candles]; h=[c['h'] for c in candles]
    l=[c['l'] for c in candles]; cl=[c['c'] for c in candles]
    v=[c['v'] for c in candles]
    t=[datetime.fromtimestamp(c['t']/1000,tz=timezone.utc) for c in candles]
    n = len(candles)
    chart_price = cl[-1]

    # EMA
    ema20 = calculate_ema(cl, 20)
    ema50 = calculate_ema(cl, 50)

    # RSI серия
    rsi_series = calculate_rsi_series(cl)

    # Volume Profile
    vp = calculate_volume_profile(candles, bins=25)
    poc = get_poc(vp)

    BG,GRID,UP,DOWN,TEXT,PL = '#131722','#1e2638','#26a69a','#ef5350','#d1d4dc','#f0c040'
    EMA20_C = '#f9a825'   # жёлтый
    EMA50_C = '#7c4dff'   # фиолетовый
    RSI_C   = '#29b6f6'   # голубой
    VP_C    = '#37474f'   # серый для Volume Profile

    # Сетка: свечи (большая) | volume profile (узкая) + RSI снизу + объём
    fig = plt.figure(figsize=(14, 9), facecolor=BG)
    gs  = gridspec.GridSpec(
        3, 2,
        height_ratios=[4, 1, 1],
        width_ratios=[5, 1],
        hspace=0.04, wspace=0.01,
        left=0.02, right=0.92, top=0.93, bottom=0.06
    )

    ax_c  = fig.add_subplot(gs[0, 0], facecolor=BG)   # свечи
    ax_vp = fig.add_subplot(gs[0, 1], facecolor=BG)   # volume profile
    ax_r  = fig.add_subplot(gs[1, 0], facecolor=BG)   # RSI
    ax_v  = fig.add_subplot(gs[2, 0], facecolor=BG)   # объём

    for a in (ax_c, ax_vp, ax_r, ax_v):
        a.tick_params(colors=TEXT, labelsize=7)
        for sp in a.spines.values(): sp.set_color(GRID)

    # ── Свечи ──
    for i,(oi,hi,li,ci,vi) in enumerate(zip(o,h,l,cl,v)):
        col = UP if ci>=oi else DOWN
        ax_c.add_patch(Rectangle((i-.3,min(oi,ci)),.6,max(abs(ci-oi),.001),color=col,zorder=3))
        ax_c.plot([i,i],[li,min(oi,ci)],color=col,linewidth=1,zorder=2)
        ax_c.plot([i,i],[max(oi,ci),hi],color=col,linewidth=1,zorder=2)
        ax_v.add_patch(Rectangle((i-.3,0),.6,vi,
                       color='#1a4a47' if ci>=oi else '#4a1a1a',zorder=2))

    # ── EMA линии ──
    xs = list(range(n))
    ema20_vals = [(i,e) for i,e in enumerate(ema20) if e is not None]
    ema50_vals = [(i,e) for i,e in enumerate(ema50) if e is not None]
    if ema20_vals:
        ax_c.plot([x for x,_ in ema20_vals],[y for _,y in ema20_vals],
                  color=EMA20_C,linewidth=1.2,zorder=4,label='EMA20')
    if ema50_vals:
        ax_c.plot([x for x,_ in ema50_vals],[y for _,y in ema50_vals],
                  color=EMA50_C,linewidth=1.2,zorder=4,label='EMA50',linestyle='--')
    ax_c.legend(loc='upper left',fontsize=7,facecolor=BG,labelcolor=TEXT,framealpha=0.7)

    # ── Pivot уровни ──
    levels = get_pivot_levels()
    if levels:
        pp_pad = (max(h)-min(l))*0.05
        ymin,ymax = min(l)-pp_pad, max(h)+pp_pad
        def draw_level(val,color,label,ls='--',lw=0.8):
            if ymin<=val<=ymax:
                ax_c.axhline(val,color=color,linewidth=lw,linestyle=ls,zorder=3,alpha=0.8)
                ax_c.text(n+0.3,val,label,color=color,fontsize=7,va='center',
                          bbox=dict(boxstyle='round,pad=0.2',facecolor='#131722',
                                    edgecolor=color,alpha=0.85))
        draw_level(levels["R2"],'#ff4444','R2',lw=1.0)
        draw_level(levels["R1"],'#ff8888','R1')
        draw_level(levels["P"], '#ffffff',' P ',ls='-',lw=1.0)
        draw_level(levels["S1"],'#88cc88','S1')
        draw_level(levels["S2"],'#44aa44','S2',lw=1.0)
        draw_level(levels["local_high"],'#ffaa00','LH',ls=':',lw=1.1)
        draw_level(levels["local_low"], '#00aaff','LL',ls=':',lw=1.1)

    # ── POC линия ──
    if poc:
        ax_c.axhline(poc,color='#ff6d00',linewidth=1.0,linestyle=':',zorder=3,alpha=0.9)
        ax_c.text(n+0.3,poc,'POC',color='#ff6d00',fontsize=7,va='center',
                  bbox=dict(boxstyle='round,pad=0.2',facecolor='#131722',
                            edgecolor='#ff6d00',alpha=0.85))

    # ── Цена последней свечи ──
    ax_c.axhline(chart_price,color=PL,linewidth=1,linestyle='--',zorder=4)
    ax_c.text(n+0.3,chart_price,f'${chart_price:.2f}',color='#131722',fontsize=9,
              va='center',fontweight='bold',
              bbox=dict(boxstyle='round,pad=0.3',facecolor=PL,edgecolor='none'))

    ax_c.yaxis.grid(True,color=GRID,linewidth=0.5,zorder=0)
    pp_pad = (max(h)-min(l))*0.05
    ax_c.set_xlim(-1,n+2); ax_c.set_ylim(min(l)-pp_pad,max(h)+pp_pad)
    ax_c.set_xticks([])
    ax_c.yaxis.set_label_position('right'); ax_c.yaxis.tick_right()

    # ── Volume Profile (горизонтальная гистограмма) ──
    if vp:
        vp_prices = [p for p,_ in vp]
        vp_vols   = [vol for _,vol in vp]
        max_vp    = max(vp_vols) if vp_vols else 1
        bar_h_vp  = (max(h)-min(l))/len(vp)*0.85
        for pv,vol in vp:
            color = '#ff6d00' if poc and abs(pv-poc)<bar_h_vp else VP_C
            ax_vp.barh(pv, vol/max_vp, height=bar_h_vp,
                       color=color, alpha=0.7, zorder=2)
        ax_vp.set_ylim(min(l)-pp_pad, max(h)+pp_pad)
        ax_vp.set_xlim(0,1.3)
        ax_vp.set_xticks([]); ax_vp.set_yticks([])
        ax_vp.set_title('VP',color=TEXT,fontsize=7,pad=2)
        if poc:
            ax_vp.axhline(poc,color='#ff6d00',linewidth=1.0,linestyle=':',zorder=3)

    # ── RSI панель ──
    rsi_xs = [i for i,r in enumerate(rsi_series) if r is not None]
    rsi_ys = [r for r in rsi_series if r is not None]
    if rsi_xs:
        ax_r.plot(rsi_xs, rsi_ys, color=RSI_C, linewidth=1.2, zorder=3)
        ax_r.axhline(70, color='#ef5350', linewidth=0.7, linestyle='--', alpha=0.7)
        ax_r.axhline(30, color='#26a69a', linewidth=0.7, linestyle='--', alpha=0.7)
        ax_r.fill_between(rsi_xs, rsi_ys, 70,
                           where=[r>70 for r in rsi_ys],
                           color='#ef5350', alpha=0.15)
        ax_r.fill_between(rsi_xs, rsi_ys, 30,
                           where=[r<30 for r in rsi_ys],
                           color='#26a69a', alpha=0.15)
        # Текущее значение RSI
        cur_rsi = rsi_ys[-1]
        rsi_color = '#ef5350' if cur_rsi>70 else '#26a69a' if cur_rsi<30 else TEXT
        ax_r.text(n+0.3, cur_rsi, f'{cur_rsi:.0f}',
                  color=rsi_color, fontsize=7, va='center')
    ax_r.set_xlim(-1,n+2); ax_r.set_ylim(0,100)
    ax_r.set_yticks([30,70]); ax_r.set_xticks([])
    ax_r.yaxis.set_label_position('right'); ax_r.yaxis.tick_right()
    ax_r.text(-0.5, 50, 'RSI', color=TEXT, fontsize=7, va='center')
    ax_r.yaxis.grid(True,color=GRID,linewidth=0.5,zorder=0)

    # ── Объём ──
    ax_v.yaxis.grid(True,color=GRID,linewidth=0.5,zorder=0)
    step = max(1,n//8)
    xt   = list(range(0,n,step))
    ax_v.set_xticks(xt)
    ax_v.set_xticklabels([t[i].strftime('%H:%M') for i in xt],color=TEXT,fontsize=7)
    ax_v.set_xlim(-1,n+2); ax_v.set_ylim(0,max(v)*1.3)
    ax_v.yaxis.set_label_position('right'); ax_v.yaxis.tick_right()

    chg = (cl[-1]-o[0])/o[0]*100
    fig.suptitle(
        f"HYPE/USDC  •  5м  •  6ч       {'+' if chg>=0 else ''}{chg:.2f}%"
        f"   EMA20/50   RSI   Volume Profile",
        color=TEXT, fontsize=10, fontweight='bold', x=0.02, ha='left')

    buf = io.BytesIO()
    fig.savefig(buf,format='png',dpi=130,facecolor=BG)
    plt.close(fig); buf.seek(0)
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
        bot.send_message(message.chat.id,"⏳ Загружаю данные..."); return
    em = trend_emoji(data["change_24h"])
    s  = "+" if data["change_24h"]>=0 else ""
    candles = get_candles(6)
    levels  = get_pivot_levels()
    bull_score, sentiment, confidence = 50, "Нейтральный", "50%"
    setup_text = ""

    # BTC корреляция
    btc = get_btc_change()
    btc_text = ""
    if btc:
        btc_sign = "+" if btc["change_15m"]>=0 else ""
        btc_em   = "🟢" if btc["change_15m"]>0 else "🔴"
        btc_text = f"\n{btc_em} BTC за 15м: `{btc_sign}{btc['change_15m']:.2f}%` (`${btc['price']:,.0f}`)"

    # RSI дивергенция
    div_text = ""
    if candles and len(candles)>20:
        div = detect_rsi_divergence(candles)
        if div == "bullish":
            div_text = "\n\n⚡ *Бычья дивергенция RSI* — цена падала, RSI рос → возможный разворот вверх"
        elif div == "bearish":
            div_text = "\n\n⚠️ *Медвежья дивергенция RSI* — цена росла, RSI падал → возможный разворот вниз"

    if candles and len(candles)>25:
        bull_score, sentiment, confidence = calculate_bull_score(data["price"], candles)
        setup, _ = find_confluence_setup(data["price"], candles, levels)
        if setup and setup["strength"]>=75:
            setup_text = (f"\n\n🔥 *ТОЧКА ВХОДА*\n"
                          f"*{setup['direction']}* — Сила: {setup['strength']}/100\n"
                          f"Уровень: {setup['level']}\n"
                          f"Причина: {setup['reason']}")

    # Volume Profile POC
    poc_text = ""
    if candles:
        vp  = calculate_volume_profile(candles)
        poc = get_poc(vp)
        if poc:
            poc_text = f"\n🟠 POC (макс. объём): `${poc:.2f}`"

    levels_text = ""
    if levels:
        levels_text = (f"\n\n📊 *Уровни:*\n"
                       f"R2:`${levels['R2']:.2f}` R1:`${levels['R1']:.2f}`\n"
                       f"P:`${levels['P']:.2f}` S1:`${levels['S1']:.2f}`"
                       f"{poc_text}")

    forecast = f"\n\n🔮 *Прогноз 1-4ч*: *{sentiment}* (Bull Score: {bull_score}/100)"
    caption  = (f"💰 *HYPE / USD*\n\n"
                f"Цена: `${data['price']:.4f}`\n"
                f"Изм.24ч: {em} `{s}{data['change_24h']:.2f}%`"
                f"{btc_text}"
                f"{forecast}{levels_text}{div_text}{setup_text}")

    if candles:
        try:
            bot.send_photo(message.chat.id,
                           build_chart(candles,data["price"]),
                           caption=caption,parse_mode="Markdown")
            return
        except Exception as e:
            print(f"[Chart] error: {e}")
    bot.send_message(message.chat.id,caption,parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📊 Статистика 24ч")
def cmd_stats(message):
    data = get_cached()
    if not data:
        bot.send_message(message.chat.id,"⏳ Загружаю данные..."); return
    s7  = "+" if data["change_7d"] >=0 else ""
    s24 = "+" if data["change_24h"]>=0 else ""
    bot.send_message(message.chat.id,
        f"📊 *Статистика HYPE*\n\n"
        f"Объём 24ч:       `${data['volume']:,.0f}`\n"
        f"Капитализация: `${data['cap']:,.0f}`\n\n"
        f"Изм. 24ч: `{s24}{data['change_24h']:.2f}%`\n"
        f"Изм. 7д:    `{s7}{data['change_7d']:.2f}%`")

@bot.message_handler(func=lambda m: m.text == "📖 Стакан заявок")
def cmd_orderbook(message):
    data = get_cached()
    if not data:
        bot.send_message(message.chat.id,"⏳ Загружаю..."); return
    bot.send_message(message.chat.id,"⏳ Анализирую стакан...")
    book = get_order_book()
    if not book:
        bot.send_message(message.chat.id,"❌ Не удалось получить стакан."); return

    best_ask  = book["asks"][0][0] if book["asks"] else None
    best_bid  = book["bids"][0][0] if book["bids"] else None
    mid_price = (best_ask+best_bid)/2 if best_ask and best_bid else data["price"]

    analysis = analyze_orderbook(book)
    text  = f"📖 *Стакан заявок HYPE*\n_Цена ≈ ${mid_price:.4f}_\n\n"
    text += get_orderbook_summary(analysis)

    now = time.time()
    with _wall_snap_lock:
        walls_copy = dict(_persistent_walls)
    persistent = []
    for price, info in sorted(walls_copy.items(),key=lambda x: x[1]["first_seen"]):
        dur = int((now-info["first_seen"])/60)
        if dur >= 5:
            em = "🟢" if info["side"]=="bid" else "🔴"
            persistent.append(f"{em} `${price:.2f}` — {info['size']:,.0f} HYPE ({dur} мин)")
    if persistent:
        text += f"\n\n🕒 *Устойчивые стены (≥5 мин):*\n" + "\n".join(persistent[:8])

    bot.send_message(message.chat.id,text,parse_mode="Markdown")

    try:
        avg = calculate_average_book()
        if avg and avg["bids"] and avg["asks"]:
            avg_bids = sorted(avg["bids"].items(),key=lambda x: x[0],reverse=True)
            avg_asks = sorted(avg["asks"].items(),key=lambda x: x[0])
            heatmap  = build_heatmap({"bids":avg_bids,"asks":avg_asks},mid_price)
            bot.send_photo(message.chat.id,heatmap,
                           caption=f"🌡 *Тепловая карта* (±0.8%, {len(_fast_snapshots)} снимков)",
                           parse_mode="Markdown")
        else:
            bot.send_message(message.chat.id,
                "⏳ Снимки ещё накапливаются (~2 мин). Попробуй позже.")
    except Exception as e:
        print(f"[Heatmap] error: {e}")

@bot.message_handler(func=lambda m: m.text == "🔭 Глубокий стакан")
def cmd_deep_orderbook(message):
    data = get_cached()
    if not data:
        bot.send_message(message.chat.id,"⏳ Загружаю..."); return

    avg_book, n_snaps = calculate_deep_average_book()
    if not avg_book or n_snaps < 4:
        bot.send_message(message.chat.id,
            f"⏳ Накоплено: *{n_snaps}* снимков из 720. Попробуй через минуту.",
            parse_mode="Markdown")
        return

    bot.send_message(message.chat.id,"⏳ Строю глубокую тепловую карту...")

    all_bids = sorted([(float(p),float(s)) for p,s in avg_book["bids"].items()],
                      key=lambda x: x[0], reverse=True)
    all_asks = sorted([(float(p),float(s)) for p,s in avg_book["asks"].items()],
                      key=lambda x: x[0])

    mid_price = data["price"]
    if all_asks and all_bids:
        mid_price = (all_asks[0][0]+all_bids[0][0])/2

    deep_bid_walls = find_walls(all_bids, 2.5)
    deep_ask_walls = find_walls(all_asks, 2.5)

    hours = round(n_snaps*15/3600,1)
    text  = (f"🔭 *Глубокий стакан HYPE*\n"
             f"_Память: {hours}ч ({n_snaps} снимков), шаг $0.1_\n\n"
             f"Цена ≈ `${mid_price:.2f}`\n\n")

    if deep_ask_walls:
        text += "🔴 *Значимые стены продажи:*\n"
        for p,s in deep_ask_walls[:4]:
            text += f"  `${p:.1f}` (+${p-mid_price:.1f}) — {s:,.0f} HYPE\n"
    if deep_bid_walls:
        text += "\n🟢 *Значимые стены покупки:*\n"
        for p,s in deep_bid_walls[:4]:
            text += f"  `${p:.1f}` (-${mid_price-p:.1f}) — {s:,.0f} HYPE\n"
    if not deep_ask_walls and not deep_bid_walls:
        text += "_Значимых стен пока не обнаружено._"

    bot.send_message(message.chat.id,text,parse_mode="Markdown")

    try:
        heatmap = build_deep_heatmap(avg_book, mid_price, n_snaps)
        bot.send_photo(message.chat.id, heatmap,
                       caption=(f"🔭 *Глубокая тепловая карта*\n"
                                f"Диапазон ±5% • {hours}ч памяти • шаг $0.1"),
                       parse_mode="Markdown")
    except Exception as e:
        print(f"[DeepHeatmap] error: {e}")
        bot.send_message(message.chat.id,"❌ Ошибка построения карты.")

@bot.message_handler(func=lambda m: m.text == "🔔 Уведомления 1%")
def cmd_notify(message):
    cid = message.chat.id
    if cid in subscribers:
        bot.send_message(cid,
            "✅ Уведомления уже включены!\n\n"
            "• HYPE изменится на *1%+* за 15 минут\n"
            "• Появится крупная стена в стакане\n\n"
            "Отключить — ❌ Отписаться",parse_mode="Markdown")
    else:
        subscribers.add(cid)
        data = get_cached()
        if data: sub_base[cid] = data["price"]
        save_subscribers()
        msg = ("✅ *Уведомления включены!*\n\n"
               "Пришлю сигнал если:\n"
               "• HYPE изменится на *1%+* за 15 минут\n"
               "• Появится крупная стена 🧱\n")
        if data: msg += f"\nТекущая цена: `${data['price']:.4f}`"
        bot.send_message(cid,msg,parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "🐂 Bull Score 60+")
def cmd_bull_notify(message):
    cid = message.chat.id
    if cid in bull_subscribers:
        bot.send_message(cid,
            "✅ Bull Score уведомления уже включены!\n\n"
            "Пришлю сигнал когда Bull Score превысит *60*.\n\n"
            "Отключить — ❌ Отписаться",parse_mode="Markdown")
    else:
        bull_subscribers.add(cid)
        save_subscribers()
        bot.send_message(cid,
            "✅ *Bull Score уведомления включены!*\n\n"
            "Пришлю сигнал когда Bull Score HYPE превысит *60/100*.",
            parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "❌ Отписаться")
def cmd_unsub(message):
    cid = message.chat.id
    removed = False
    if cid in subscribers:
        subscribers.discard(cid); sub_base.pop(cid,None); removed=True
    if cid in bull_subscribers:
        bull_subscribers.discard(cid); removed=True
    if removed:
        save_subscribers()
        bot.send_message(cid,"✅ Все уведомления отключены.")
    else:
        bot.send_message(cid,"ℹ️ У тебя не было активных уведомлений.")

@bot.message_handler(func=lambda m: m.text == "ℹ️ Помощь")
def cmd_help(message):
    bot.send_message(message.chat.id,
        "📖 *Как пользоваться ботом*\n\n"
        "💰 *Курс HYPE* — цена + график с EMA/RSI/VP + прогноз\n"
        "📊 *Статистика 24ч* — объём, капитализация\n"
        "📖 *Стакан заявок* — анализ ±0.8% + тепловая карта\n"
        "🔭 *Глубокий стакан* — стены ±5%, память 3ч, шаг $0.1\n"
        "🔔 *Уведомления 1%* — сигнал при движении 1%+ за 15 мин\n"
        "🐂 *Bull Score 60+* — сигнал при бычьем настроении\n"
        "❌ *Отписаться* — выключить все уведомления\n\n"
        "📈 *На графике:*\n"
        "🟡 EMA20 — быстрая скользящая | 🟣 EMA50 — медленная\n"
        "🟠 POC — уровень максимального объёма за 6ч\n"
        "RSI панель — перекупленность (>70) / перепроданность (<30)\n"
        "VP — Volume Profile справа от свечей\n\n"
        "📊 *Уровни:* R2/R1 сопротивления | S1/S2 поддержки | P пивот")

@bot.message_handler(func=lambda m: True)
def fallback(message):
    bot.send_message(message.chat.id,"🤔 Воспользуйся кнопками ниже.",
                     reply_markup=main_markup())

# ── Алерт дивергенции ─────────────────────────────────────────
_last_div_alert: dict = {}

def check_divergence_and_notify():
    """Алерт если обнаружена RSI дивергенция"""
    candles = get_candles(6)
    if not candles or len(candles) < 20: return
    data = get_cached()
    if not data: return
    div = detect_rsi_divergence(candles)
    if not div: return
    now = time.time()
    if div == "bullish":
        msg = (f"⚡ *Бычья дивергенция RSI*\n\n"
               f"Цена падала, RSI рос — возможный разворот вверх.\n"
               f"Цена HYPE: `${data['price']:.4f}`")
    else:
        msg = (f"⚠️ *Медвежья дивергенция RSI*\n\n"
               f"Цена росла, RSI падал — возможный разворот вниз.\n"
               f"Цена HYPE: `${data['price']:.4f}`")
    for cid in list(subscribers):
        key = f"{cid}_{div}"
        if now - _last_div_alert.get(key, 0) < 7200: continue
        try:
            bot.send_message(cid, msg)
            _last_div_alert[key] = now
        except Exception: pass

# ── Потоки ────────────────────────────────────────────────────
def fast_snapshot_loop():
    print("[FastSnap] Запуск...")
    while True:
        try:
            book = get_order_book()
            if book: take_fast_snapshot(book)
        except Exception as e:
            print(f"[FastSnap] error: {e}")
        time.sleep(8)

def deep_snapshot_loop():
    print("[DeepSnap] Запуск...")
    while True:
        try:
            book = get_deep_order_book()
            if book: take_deep_snapshot(book)
        except Exception as e:
            print(f"[DeepSnap] error: {e}")
        time.sleep(15)

def wall_track_loop():
    print("[WallTrack] Запуск...")
    while True:
        try:
            book = get_order_book()
            if book: track_persistent_walls(book)
        except Exception as e:
            print(f"[WallTrack] error: {e}")
        time.sleep(30)

_last_bull_alert: dict = {}

def price_monitor():
    global _latest, _price_history
    print("[Monitor] Запуск...")
    data = _fetch_hype()
    if data:
        with _cache_lock:
            _latest = data
        print(f"[Monitor] Кэш: ${data['price']:.4f}")

    wall_counter = 0
    div_counter  = 0
    while True:
        time.sleep(60)
        data = _fetch_hype()
        if not data: continue
        now = time.time()
        with _cache_lock:
            _latest = data
            _price_history.append((data["price"],now))
            _price_history = [(p,t) for p,t in _price_history if now-t<=1200]
            snap = list(_price_history)

        # Алерт 1% за 15 мин
        for cid in list(subscribers):
            old  = [p for p,t in snap if 840<=now-t<=960]
            base = old[0] if old else sub_base.get(cid,0)
            if base>0:
                chg = (data["price"]-base)/base*100
                if abs(chg)>=1.0:
                    d = "вырос 🚀" if chg>0 else "упал 🔻"
                    try:
                        bot.send_message(cid,
                            f"⚡ *СИГНАЛ: HYPE {d}*\n\n"
                            f"За 15 мин: `{chg:+.2f}%`\n"
                            f"Сейчас: `${data['price']:.4f}`\n"
                            f"Было:   `${base:.4f}`")
                        sub_base[cid] = data["price"]
                        save_subscribers()
                    except Exception:
                        subscribers.discard(cid); sub_base.pop(cid,None); save_subscribers()

        # Алерт Bull Score 60+
        if bull_subscribers:
            candles = get_candles(6)
            if candles and len(candles)>25:
                bull_score, sentiment, _ = calculate_bull_score(data["price"],candles)
                if bull_score>=60:
                    for cid in list(bull_subscribers):
                        if now-_last_bull_alert.get(cid,0)<3600: continue
                        try:
                            bot.send_message(cid,
                                f"🐂 *Bull Score {bull_score}/100*\n\n"
                                f"Настроение: *{sentiment}*\n"
                                f"Цена HYPE: `${data['price']:.4f}`\n\n"
                                f"_Рынок склоняется к росту_")
                            _last_bull_alert[cid] = now
                        except Exception:
                            bull_subscribers.discard(cid); save_subscribers()

        # Алерт стен раз в 5 минут
        wall_counter += 1
        if wall_counter>=5 and subscribers:
            wall_counter = 0
            try: check_walls_and_notify()
            except Exception as e: print(f"[Walls] error: {e}")

        # Алерт дивергенции раз в 15 минут
        div_counter += 1
        if div_counter>=15 and subscribers:
            div_counter = 0
            try: check_divergence_and_notify()
            except Exception as e: print(f"[Div] error: {e}")

# ── Запуск ────────────────────────────────────────────────────
load_subscribers()
threading.Thread(target=fast_snapshot_loop,  daemon=True).start()
threading.Thread(target=deep_snapshot_loop,  daemon=True).start()
threading.Thread(target=wall_track_loop,     daemon=True).start()
threading.Thread(target=price_monitor,       daemon=True).start()

print("✅ Бот запущен...")
if DOMAIN:
    webhook_url = f"https://{DOMAIN}/{TOKEN}"
    bot.remove_webhook()
    time.sleep(1)
    bot.set_webhook(url=webhook_url,allowed_updates=["message","callback_query"])
    print(f"[Webhook] установлен: {webhook_url}")
    app.run(host="0.0.0.0",port=PORT)
else:
    print("[Polling] режим")
    bot.remove_webhook()
    while True:
        try:
            bot.polling(none_stop=True,timeout=30,skip_pending=True)
        except Exception as e:
            print(f"[Polling] error: {e}")
            time.sleep(5)
