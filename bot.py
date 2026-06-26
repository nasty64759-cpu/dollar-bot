import telebot
import requests
import time
import threading
from telebot import types

TOKEN = "8838571832:AAElqHv_qPr8EUY42vJh0EQBQDU7rAGqfRg"  # Можно оставить пока

bot = telebot.TeleBot(TOKEN)

subscribers = {}  # chat_id: порог скачка в %

def get_hype_data():
    try:
        # Используем CoinGecko (более стабильный)
        url = "https://api.coingecko.com/api/v3/coins/hyperliquid"
        response = requests.get(url, timeout=10)
        data = response.json()
        
        price = data['market_data']['current_price']['usd']
        volume_24h = data['market_data']['total_volume']['usd']
        change_24h = data['market_data']['price_change_percentage_24h']
        
        return {
            'price': price,
            'volume': volume_24h,
            'change': change_24h
        }
    except Exception as e:
        print(f"Ошибка API: {e}")
        return None

@bot.message_handler(commands=['start'])
def start(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add("💰 Курс HYPE", "📊 Объём 24h")
    markup.add("🔔 Настроить уведомления", "❌ Отписаться")
    
    bot.send_message(message.chat.id, 
                     "👋 Привет! Я бот мониторинга **HYPE** (Hyperliquid)\n\n"
                     "Выбери кнопку:", 
                     reply_markup=markup)

@bot.message_handler(func=lambda m: True)
def handle_text(message):
    data = get_hype_data()
    if not data:
        bot.send_message(message.chat.id, "❌ Не удалось получить данные. Попробуйте через минуту.")
        return

    if message.text == "💰 Курс HYPE":
        bot.send_message(message.chat.id,
            f"💰 **HYPE / USD**\n\n"
            f"Цена: `${data['price']:.4f}`\n"
            f"Изменение 24ч: {data['change']:+.2f}%\n"
            f"Объём 24ч: `${data['volume']:,.0f}` USD")

    elif message.text == "📊 Объём 24h":
        bot.send_message(message.chat.id,
            f"📊 **Объём торгов HYPE 24ч**\n\n"
            f"`${data['volume']:,.0f}` USD")

    elif message.text == "🔔 Настроить уведомления":
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(types.InlineKeyboardButton("50%", callback_data="th_50"))
        markup.add(types.InlineKeyboardButton("80%", callback_data="th_80"))
        markup.add(types.InlineKeyboardButton("100%", callback_data="th_100"))
        markup.add(types.InlineKeyboardButton("150%", callback_data="th_150"))
        
        bot.send_message(message.chat.id, "При каком росте объёма присылать оповещение?", reply_markup=markup)

    elif message.text == "❌ Отписаться":
        subscribers.pop(message.chat.id, None)
        bot.send_message(message.chat.id, "✅ Вы отписались от уведомлений.")

@bot.callback_query_handler(func=lambda call: True)
def callback(call):
    if call.data.startswith("th_"):
        threshold = int(call.data.split("_")[1])
        subscribers[call.message.chat.id] = threshold
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, f"✅ Уведомления включены! Порог: **{threshold}%**")

# Мониторинг скачков объёма
def volume_monitor():
    global last_volume
    last_volume = 0
    while True:
        data = get_hype_data()
        if data and last_volume > 0:
            change = ((data['volume'] - last_volume) / last_volume) * 100
            if change >= 50:  # базовый порог
                for chat_id, threshold in list(subscribers.items()):
                    if change >= threshold:
                        alert = f"🚨 **СКАЧОК ОБЪЁМА HYPE!**\n\n" \
                                f"Рост: **+{change:.1f}%**\n" \
                                f"Объём: `${data['volume']:,.0f}`\n" \
                                f"Цена: `${data['price']:.4f}`"
                        try:
                            bot.send_message(chat_id, alert)
                        except:
                            pass
        if data:
            last_volume = data['volume']
        time.sleep(240)  # каждые 4 минуты

threading.Thread(target=volume_monitor, daemon=True).start()

print("Бот запущен...")
bot.polling(none_stop=True)
