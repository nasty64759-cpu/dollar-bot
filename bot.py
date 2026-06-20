# 8838571832:AAElqHv_qPr8EUY42vJh0EQBQDU7rAGqfRg

import telebot
from telebot import types
import requests
import datetime

# Вставь сюда свой токен
TOKEN = "8838571832:AAElqHv_qPr8EUY42vJh0EQBQDU7rAGqfRg"

bot = telebot.TeleBot(TOKEN)

def get_dollar_rate():
    try:
        response = requests.get("https://www.cbr-xml-daily.ru/daily_json.js")
        data = response.json()
        usd = data['Valute']['USD']['Value']
        date = data['Date'][:10]
        return f"💵 Курс доллара на {date}:\n\n1 USD = {usd:.2f} ₽"
    except:
        return "❌ Не удалось получить курс. Попробуйте позже."

@bot.message_handler(commands=['start'])
def start(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    btn1 = types.KeyboardButton("Курс доллара")
    btn2 = types.KeyboardButton("Обновить")
    markup.add(btn1, btn2)
    
    bot.send_message(message.chat.id, 
                     "👋 Привет! Я бот для курса доллара.\n\nНажми кнопку ниже:", 
                     reply_markup=markup)

@bot.message_handler(func=lambda message: True)
def handle_text(message):
    if message.text == "Курс доллара" or message.text == "Обновить":
        rate = get_dollar_rate()
        bot.send_message(message.chat.id, rate)
    else:
        bot.send_message(message.chat.id, "Нажми одну из кнопок ниже 👇")

print("Бот запущен...")
bot.polling(none_stop=True)