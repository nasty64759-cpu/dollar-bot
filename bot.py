import telebot
import requests
import time
import threading
from telebot import types

TOKEN = "8838571832:AAElqHv_qPr8EUY42vJh0EQBQDU7rAGqfRg"   

bot = telebot.TeleBot(TOKEN)

# Настройки
subscribers = {}  # chat_id: порог в процентах
last_volume = 0

def get_hype_data():
    try:
        # DexScreener API — более точные данные по Hyperliquid
        url = "https://api.dexscreener.com/latest/dex/tokens/0x2f0e27574a9a2f0c5b6c3f9f5c8b2a7e4d9f1c3a"
        response = requests.get(url)
        data = response.json()
        
        pair = data['pairs'][0]
        price = float(pair['priceUsd'])
        volume_24h = float(pair['volume']['h24'])
        price_change_24h = float(pair.get('priceChange', {}).get('h24', 0))
        
        return {
            'price': price,
            'volume': volume_24h,
            'change': price_change_24h
        }
    except:
        return None

# ==================== КОМАНДЫ ====================

@bot.message_handler(commands=['start'])
def start(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add("💰 Курс HYPE", "📊 Объём 24h")
    markup.add("🔔 Настроить уведомления", "❌ Отписаться")
    
    bot.send_message(message.chat.id, 
                     "👋 Привет! Я бот мониторинга **HYPE** (Hyperliquid)\n\n"
                     "Выбери нужную кнопку:", 
                     reply_markup=markup)

@bot.message_handler(func=lambda m: True)
def handle_text(message):
    data = get_hype_data()
    if not data:
        bot.send_message(message.chat.id, "❌ Ошибка получения данных. Попробуйте позже.")
        return

    if message.text == "💰 Курс HYPE":
        bot.send_message(message.chat.id, 
            f"💰 **HYPE / USD**\n\n"
            f"Цена: `${data['price']:.4f}`\n"
            f"Изменение 24ч: {data['change']:+.2f}%\n"
            f"Объём 24ч: `${data['volume']:,.0f}` USD")

    elif message.text == "📊 Объём 24h":
        bot.send_message(message.chat.id, 
            f"📊 **Объём торгов HYPE**\n\n"
            f"24 часа: `${data['volume']:,.0f}` USD")

    elif message.text == "🔔 Настроить уведомления":
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("50%", callback_data="threshold_50"))
        markup.add(types.InlineKeyboardButton("70%", callback_data="threshold_70"))
        markup.add(types.InlineKeyboardButton("100%", callback_data="threshold_100"))
        markup.add(types.InlineKeyboardButton("150%", callback_data="threshold_150"))
        
        bot.send_message(message.chat.id, "При каком скачке объёма присылать уведомление?", reply_markup=markup)

    elif message.text == "❌ Отписаться":
        subscribers.pop(message.chat.id, None)
        bot.send_message(message.chat.id, "✅ Вы успешно отписались от уведомлений.")

# Обработка кнопок настройки
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    if call.data.startswith("threshold_"):
        threshold = int(call.data.split("_")[1])
        subscribers[call.message.chat.id] = threshold
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, f"✅ Уведомления включены! Буду оповещать при скачке объёма от **{threshold}%**")

# ==================== МОНИТОРИНГ ====================

def volume_monitor():
    global last_volume
    while True:
        data = get_hype_data()
        if data and last_volume > 0:
            volume_change = ((data['volume'] - last_volume) / last_volume) * 100
            
            for chat_id, threshold in list(subscribers.items()):
                if volume_change >= threshold:
                    alert = f"🚨 **СКАЧОК ОБЪЁМА HYPE!**\n\n" \
                            f"Объём вырос на **{volume_change:.1f}%**\n" \
                            f"Текущий объём: `${data['volume']:,.0f}` USD\n" \
                            f"Цена: `${data['price']:.4f}`"
                    
                    try:
                        bot.send_message(chat_id, alert)
                    except:
                        subscribers.pop(chat_id, None)  # удаляем, если чат недоступен
        if data:
            last_volume = data['volume']
        
        time.sleep(180)  # проверка каждые 3 минуты

# Запуск мониторинга
threading.Thread(target=volume_monitor, daemon=True).start()

print("Бот запущен...")
bot.polling(none_stop=True)
