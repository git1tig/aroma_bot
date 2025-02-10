import os
import telebot
import openai
from dotenv import load_dotenv
import pandas as pd
import requests
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import FAISS
from langchain.text_splitter import MarkdownHeaderTextSplitter
from langchain.schema import Document
import re

# === ЗАГРУЗКА API-КЛЮЧЕЙ ===
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not TELEGRAM_BOT_TOKEN or not OPENAI_API_KEY:
    raise ValueError("❌ Отсутствует TELEGRAM_BOT_TOKEN или OPENAI_API_KEY в .env файле!")

openai.api_key = OPENAI_API_KEY
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

# === ПУТИ К ФАЙЛАМ ===
MASLA_FILE = "/app/mono_oils.txt"
FAISS_INDEX_FILE = "/app/index.faiss"

# === ПРОВЕРКА И ЗАГРУЗКА FAISS ===
embs = OpenAIEmbeddings()
db = None

if os.path.exists(FAISS_INDEX_FILE):
    print("✅ Загружаем FAISS-хранилище из файла...")
    db = FAISS.load_local(FAISS_INDEX_FILE, embs, allow_dangerous_deserialization=True)
else:
    print("⚠️ Файл FAISS-хранилища не найден, создаем заново...")

    if not os.path.exists(MASLA_FILE):
        raise FileNotFoundError(f"❌ Файл {MASLA_FILE} не найден! Добавьте его в каталог.")

    with open(MASLA_FILE, 'r', encoding='utf-8') as f:
        my_text = f.read()

    splitter = MarkdownHeaderTextSplitter(headers_to_split_on=[("#", "Header 1")])
    chunks = splitter.split_text(my_text)  # ✅ `split_text()` уже возвращает нужные объекты

    db = FAISS.from_documents(chunks, embs)

    db.save_local(FAISS_INDEX_FILE)
    print("✅ FAISS-хранилище создано и сохранено!")

# === ЗАГРУЗКА ДАННЫХ ИЗ GOOGLE SHEETS ===
SHEET_URL = "https://docs.google.com/spreadsheets/d/1MknmvI9_YvjM7bge9tPryRKrrzCi-Ywc5ZfYiyb6Bdg/export?format=csv"
COLUMN_NAMES = ["Name", "Vol", "Price"]

try:
    df = pd.read_csv(SHEET_URL, names=COLUMN_NAMES, encoding='utf-8')
    print("✅ Данные о маслах успешно загружены!")
except Exception as e:
    print("❌ Ошибка загрузки данных:", e)
    df = pd.DataFrame(columns=COLUMN_NAMES)

# === ХРАНИЛИЩЕ СОСТОЯНИЙ ===
user_states = {}
drops_counts = {}  
current_oils = {}  
drop_session_changes = {}  

WAITING_OIL_NAME = "waiting_for_oil"
WAITING_DROPS = "waiting_for_drop_quantity"
WAITING_NEXT_OIL = "waiting_for_next_oil"

# === ФУНКЦИЯ ВЫВОДА ВОЗМОЖНОСТЕЙ БОТА ===
def send_bot_options(chat_id):
    bot.send_message(chat_id, 
                     "✨ Что я могу для вас сделать? ✨\n\n"
                     "🛠 *Мои возможности:*\n"
                     "✅ `/р` – создать свою уникальную смесь масел\n"
                     "✅ `/м` – получить информацию о любом эфирном масле\n"
                     "✅ Или просто напишите свой вопрос, и я помогу разобраться!\n\n"
                     "💡 Попробуйте прямо сейчас! 😊", 
                     parse_mode="Markdown")

# === ОБРАБОТЧИК КОМАНД ===
@bot.message_handler(commands=['start'])
def start_command(message):
    bot.reply_to(message, "Привет! 👋 Я ваш помощник по эфирным маслам. Давайте начнём!")
    send_bot_options(message.chat.id)

@bot.message_handler(commands=['р'])
def oil_command(message):
    bot.reply_to(message, "Введите название масла ('*' - закончить ввод):")
    user_states[message.chat.id] = WAITING_NEXT_OIL
    send_bot_options(message.chat.id)

@bot.message_handler(commands=['м'])
def oil_command(message):
    bot.reply_to(message, "Введите название масла:")
    user_states[message.chat.id] = WAITING_OIL_NAME
    send_bot_options(message.chat.id)

@bot.message_handler(commands=['стоп'])
def cancel_command(message):
    bot.reply_to(message, "Команда отменена. Начните заново с /м.")
    user_states.pop(message.chat.id, None)

MAX_MESSAGE_LENGTH = 4000  

def send_long_message(chat_id, text):
    for i in range(0, len(text), MAX_MESSAGE_LENGTH):
        bot.send_message(chat_id, text[i:i + MAX_MESSAGE_LENGTH])

@bot.message_handler(func=lambda message: True)
def handle_input(message):
    user_input = message.text.strip().lower()

    if message.chat.id in user_states:
        state = user_states[message.chat.id]

        if state == WAITING_OIL_NAME:
            if db:
                docs = db.similarity_search_with_score(user_input, k=1)
                if docs[0][1] < 0.37:
                    bot.reply_to(message, f"Информация о {user_input}: {docs[0][0].page_content}")
                else:
                    docs = db.similarity_search(user_input, k=1)
                    bot.reply_to(message, f'Под ваш запрос {user_input} подходит это: {docs[0].page_content}')
            else:
                bot.reply_to(message, "⚠️ База данных FAISS не загружена, поиск недоступен.")

        elif state == WAITING_NEXT_OIL:
            if user_input != '*':
                if user_input.capitalize() not in df['Name'].values:
                    bot.reply_to(message, f'⚠️ Масло "{user_input}" не найдено. Попробуйте другое:')
                    return
                
                if message.chat.id not in drop_session_changes:
                    drop_session_changes[message.chat.id] = []
                    drops_counts[message.chat.id] = 0

                current_oils[message.chat.id] = user_input
                user_states[message.chat.id] = WAITING_DROPS

                existing_oils = "; ".join(drop_session_changes[message.chat.id])
                bot.reply_to(message, f"Уже введено: {existing_oils}. Теперь введите количество капель для {user_input}:")
            else:
                total_cost = int(drops_counts.get(message.chat.id, 0))
                mix_info = "; ".join(drop_session_changes.get(message.chat.id, []))
                bot.reply_to(message, f"🎉 Смесь завершена!\n\n"
                                      f"🧪 *Состав смеси:* {mix_info}\n"
                                      f"💰 *Общая стоимость:* {total_cost}р.", parse_mode="Markdown")
                
                drop_session_changes.pop(message.chat.id, None)
                drops_counts.pop(message.chat.id, None)
                user_states.pop(message.chat.id, None)

                send_bot_options(message.chat.id)

        elif state == WAITING_DROPS:
            if not user_input.isdigit():
                bot.reply_to(message, f'⚠️ "{user_input}" не является числом. Введите количество капель.')
                return

            drops = int(user_input)
            oil_name = current_oils[message.chat.id].capitalize()
            price_per_drop = int(df.loc[df["Name"] == oil_name, "Price"]) / (int(df.loc[df["Name"] == oil_name, "Vol"]) * 25)

            drops_counts[message.chat.id] += drops * price_per_drop
            drop_session_changes[message.chat.id].append(f"{oil_name}, {drops} капель")

            bot.reply_to(message, f"Добавлено: {oil_name}, {drops} капель. Введите следующее масло или '*' для завершения.")

            user_states[message.chat.id] = WAITING_NEXT_OIL

if __name__ == "__main__":
    bot.infinity_polling()
