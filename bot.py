import telebot
import requests
import time
import threading
from telebot import types

TOKEN = "8838571832:AAElqHv_qPr8EUY42vJh0EQBQDU7rAGqfRg"

bot = telebot.TeleBot(TOKEN)

# Настройки подписок на изменение цены
price_subscribers = {}  # chat_id: порог в процентах (1, 2 или 5)

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
    except:
        return None

# ==================== КОМАНДЫ ====================

@bot.message_handler(commands=['start'])
def start(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add("💰 Курс HYPE", "📊 Объём 24h")
    markup.add("🔔 Настроить уведомления по цене", "❌ Отписаться")
    
    bot.send_message(message.chat.id, 
                     "👋 Добро пожаловать в бот мониторинга **HYPE**\n\n"
                     "Выберите действие:", 
                     reply_markup=markup)

@bot.message_handler(commands=['clear'])
def clear(message):
    bot.send_message(message.chat.id, "🧹 Переписка очищена (насколько это возможно в Telegram).")
    start(message)  # возвращаем главное меню

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

    elif message.text == "🔔 Настроить уведомления по цене":
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(types.InlineKeyboardButton("1% за 10 минут", callback_data="price_1"))
        markup.add(types.InlineKeyboardButton("2% за 10 минут", callback_data="price_2"))
        markup.add(types.InlineKeyboardButton("5% за 10 минут", callback_data="price_5"))
        bot.send_message(message.chat.id, "При каком изменении цены за ~10 минут присылать уведомление?", reply_markup=markup)

    elif message.text == "❌ Отписаться":
        price_subscribers.pop(message.chat.id, None)
        bot.send_message(message.chat.id, "✅ Вы отписались от уведомлений по цене.")

# Обработка кнопок
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    bot.answer_callback_query(call.id)
    chat_id = call.message.chat.id
    
    if call.data.startswith("price_"):
        threshold = int(call.data.split("_")[1])
        price_subscribers[chat_id] = threshold
        bot.send_message(chat_id, f"✅ Уведомления включены!\nБуду оповещать при изменении цены на **{threshold}%** за 10 минут.")

# ==================== МОНИТОРИНГ ЦЕНЫ ====================

def price_monitor():
    global last_price, last_price_time
    
    while True:
        data = get_hype_data()
        if data and last_price > 0:
            price_change = abs((data['price'] - last_price) / last_price * 100)
            time_diff = (time.time() - last_price_time) / 60  # минуты
            
            if time_diff <= 15:  # около 10-15 минут
                for chat_id, threshold in list(price_subscribers.items()):
                    if price_change >= threshold:
                        alert = f"⚡ **ИЗМЕНЕНИЕ ЦЕНЫ HYPE**\n\n" \
                                f"Изменение за ~10 мин: **{price_change:.2f}%**\n" \
                                f"Текущая цена: `${data['price']:.4f}`"
                        try:
                            bot.send_message(chat_id, alert)
                        except:
                            price_subscribers.pop(chat_id, None)
        
        if data:
            last_price = data['price']
            last_price_time = time.time()
        
        time.sleep(120)  # проверка каждые 2 минуты

# Запуск мониторинга
threading.Thread(target=price_monitor, daemon=True).start()

print("Бот запущен...")
bot.polling(none_stop=True)
