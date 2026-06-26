import telebot
import requests
import time
import threading
from telebot import types

TOKEN = "8838571832:AAElqHv_qPr8EUY42vJh0EQBQDU7rAGqfRg"   # Лучше использовать os.getenv("TOKEN")

bot = telebot.TeleBot(TOKEN)

# Настройки подписок
volume_subscribers = {}   # chat_id: порог объёма (%)
price_subscribers = {}    # chat_id: порог цены (%)

last_volume = 0
last_price = 0
last_price_time = time.time()

def get_hype_data():
    try:
        url = "https://api.coingecko.com/api/v3/coins/hyperliquid"
        response = requests.get(url, timeout=10)
        data = response.json()
        
        price = data['market_data']['current_price']['usd']
        volume_24h = data['market_data']['total_volume']['usd']
        change_24h = data['market_data']['price_change_percentage_24h']
        
        return {
            'price': price,
            'volume': volume_24h,
            'change_24h': change_24h
        }
    except Exception as e:
        print(f"API Error: {e}")
        return None

# ==================== МЕНЮ ====================

@bot.message_handler(commands=['start'])
def start(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add("💰 Курс HYPE", "📊 Объём 24h")
    markup.add("🔔 Уведомления по цене", "📈 Уведомления по объёму")
    markup.add("❌ Отписаться от всего")
    
    bot.send_message(message.chat.id, 
                     "👋 Бот мониторинга **HYPE** (Hyperliquid)\n\n"
                     "Выберите нужный раздел:", 
                     reply_markup=markup)

@bot.message_handler(func=lambda m: True)
def handle_text(message):
    data = get_hype_data()
    if not data:
        bot.send_message(message.chat.id, "❌ Не удалось получить данные. Попробуйте позже.")
        return

    if message.text == "💰 Курс HYPE":
        bot.send_message(message.chat.id,
            f"💰 **HYPE / USD**\n\n"
            f"Цена: `${data['price']:.4f}`\n"
            f"Изменение 24ч: {data['change_24h']:+.2f}%")

    elif message.text == "📊 Объём 24h":
        bot.send_message(message.chat.id, f"📊 Объём 24ч: `${data['volume']:,.0f}` USD")

    elif message.text == "🔔 Уведомления по цене":
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(types.InlineKeyboardButton("1% за 10 минут", callback_data="price_1"))
        markup.add(types.InlineKeyboardButton("2% за 10 минут", callback_data="price_2"))
        markup.add(types.InlineKeyboardButton("5% за 10 минут", callback_data="price_5"))
        bot.send_message(message.chat.id, "При каком изменении цены за 10 минут присылать уведомление?", reply_markup=markup)

    elif message.text == "📈 Уведомления по объёму":
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(types.InlineKeyboardButton("50%", callback_data="vol_50"))
        markup.add(types.InlineKeyboardButton("80%", callback_data="vol_80"))
        markup.add(types.InlineKeyboardButton("100%", callback_data="vol_100"))
        markup.add(types.InlineKeyboardButton("150%", callback_data="vol_150"))
        bot.send_message(message.chat.id, "При каком скачке объёма присылать уведомление?", reply_markup=markup)

    elif message.text == "❌ Отписаться от всего":
        volume_subscribers.pop(message.chat.id, None)
        price_subscribers.pop(message.chat.id, None)
        bot.send_message(message.chat.id, "✅ Вы отписались от всех уведомлений.")

# Обработка inline-кнопок
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    bot.answer_callback_query(call.id)
    chat_id = call.message.chat.id
    
    if call.data.startswith("price_"):
        threshold = int(call.data.split("_")[1])
        price_subscribers[chat_id] = threshold
        bot.send_message(chat_id, f"✅ Уведомления по цене включены! Порог: **{threshold}%** за 10 минут")
    
    elif call.data.startswith("vol_"):
        threshold = int(call.data.split("_")[1])
        volume_subscribers[chat_id] = threshold
        bot.send_message(chat_id, f"✅ Уведомления по объёму включены! Порог: **{threshold}%**")

# ==================== МОНИТОРИНГ ====================

def monitor():
    global last_volume, last_price, last_price_time
    
    while True:
        data = get_hype_data()
        if data:
            current_time = time.time()
            
            # Мониторинг цены за 10 минут
            if last_price > 0:
                price_change = abs((data['price'] - last_price) / last_price * 100)
                time_diff = (current_time - last_price_time) / 60  # в минутах
                
                if time_diff <= 12:  # примерно 10 минут
                    for chat_id, threshold in list(price_subscribers.items()):
                        if price_change >= threshold:
                            alert = f"⚡ **СКАЧОК ЦЕНЫ HYPE!**\n\n" \
                                    f"Изменение за ~10 мин: **{price_change:.2f}%**\n" \
                                    f"Цена: `${data['price']:.4f}`"
                            try:
                                bot.send_message(chat_id, alert)
                            except:
                                price_subscribers.pop(chat_id, None)
            
            # Мониторинг объёма
            if last_volume > 0:
                volume_change = ((data['volume'] - last_volume) / last_volume) * 100
                if volume_change >= 40:  # базовый фильтр
                    for chat_id, threshold in list(volume_subscribers.items()):
                        if volume_change >= threshold:
                            alert = f"🚨 **СКАЧОК ОБЪЁМА HYPE!**\n\n" \
                                    f"Рост объёма: **+{volume_change:.1f}%**\n" \
                                    f"Объём: `${data['volume']:,.0f}` USD"
                            try:
                                bot.send_message(chat_id, alert)
                            except:
                                volume_subscribers.pop(chat_id, None)
            
            last_volume = data['volume']
            last_price = data['price']
            last_price_time = current_time
        
        time.sleep(180)  # проверка каждые 3 минуты

# Запуск мониторинга
threading.Thread(target=monitor, daemon=True).start()

print("Бот запущен...")
bot.polling(none_stop=True)
