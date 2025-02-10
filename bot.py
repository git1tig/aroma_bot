# -*- coding: utf-8 -*-
import os
import telebot
import openai
from dotenv import load_dotenv
import pandas as pd
import requests
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import FAISS
from langchain.text_splitter import MarkdownHeaderTextSplitter
from langchain.schema import Document  # Добавляем импорт

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
db = None  # Объявляем db заранее

if os.path.exists(FAISS_INDEX_FILE):
    print("✅ Загружаем FAISS-хранилище из файла...")
    db = FAISS.load_local(FAISS_INDEX_FILE, embs, allow_dangerous_deserialization=True)
else:
    print("⚠️ Файл FAISS-хранилища не найден, создаем заново...")

    if not os.path.exists(MASLA_FILE):
        raise FileNotFoundError(f"❌ Файл {MASLA_FILE} не найден! Добавь его в каталог.")

    with open(MASLA_FILE, 'r', encoding='utf-8') as f:
        my_text = f.read()

    # Разбиваем текст на части
    splitter = MarkdownHeaderTextSplitter(headers_to_split_on=[("#", "Header 1")])
    chunks = splitter.split_text(my_text)  # ✅ `split_text()` уже возвращает нужные объекты


    # Создаем векторное хранилище с OpenAI Embeddings
    db = FAISS.from_documents(chunks, embs)

    # Сохраняем в файл
    db.save_local(FAISS_INDEX_FILE)
    print("✅ FAISS-хранилище создано и сохранено!")

# === ЗАГРУЗКА ДАННЫХ ИЗ GOOGLE SHEETS (UTF-8) ===
SHEET_URL = "https://docs.google.com/spreadsheets/d/1MknmvI9_YvjM7bge9tPryRKrrzCi-Ywc5ZfYiyb6Bdg/export?format=csv"
COLUMN_NAMES = ["Name", "Vol", "Price"]

try:
    df = pd.read_csv(SHEET_URL, names=COLUMN_NAMES, encoding='utf-8')
    print("✅ Данные о маслах успешно загружены!")
except Exception as e:
    print("❌ Ошибка загрузки данных:", e)
    df = pd.DataFrame(columns=COLUMN_NAMES)  # Пустая таблица, если не загрузилась

# === GPT-ФУНКЦИИ ===
s1 = "Сгенерируй ключевые слова для поиска по векторной базе данных масел..."
s2 = "Определи, какое масло подходит под запрос клиента..."

def gpt_for_query(prompt, system):
    response = openai.chat.completions.create(
        model='gpt-4o-mini',
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": prompt}],
        temperature=1
    )
    return response.choices[0].message.content

# === ТЕЛЕГРАМ-БОТ ===
user_states = {}

WAITING_OIL_NAME = "waiting_for_oil"
WAITING_NEXT_OIL = "waiting_for_next_oil"

@bot.message_handler(commands=['р'])
def oil_command(message):
    bot.reply_to(message, "Введите название масла ('*' - закончить ввод):")
    user_states[message.chat.id] = WAITING_NEXT_OIL

@bot.message_handler(commands=['м'])
def oil_command(message):
    bot.reply_to(message, "Введите название масла:")
    user_states[message.chat.id] = WAITING_OIL_NAME

@bot.message_handler(commands=['стоп'])
def cancel_command(message):
    bot.reply_to(message, "Команда отменена. Начните заново с /м.")
    user_states.pop(message.chat.id, None)

# === ОТПРАВКА ДЛИННЫХ СООБЩЕНИЙ ===
MAX_MESSAGE_LENGTH = 4000  # Чуть меньше 4096, чтобы избежать ошибок

def send_long_message(chat_id, text):
    """Функция для отправки длинных сообщений частями."""
    for i in range(0, len(text), MAX_MESSAGE_LENGTH):
        bot.send_message(chat_id, text[i:i + MAX_MESSAGE_LENGTH])

@bot.message_handler(func=lambda message: True)
def handle_input(message):
    print(f"📩 Получено сообщение: {message.text}")  # <--- Добавлено для отладки
    user_input = message.text.strip().lower()

    if message.chat.id in user_states:
        if user_states[message.chat.id] == WAITING_OIL_NAME:
            if db:
                docs = db.similarity_search_with_score(user_input, k=1)
                if docs[0][1] < 0.37:
                    bot.reply_to(message, f"Информация о {user_input}: {docs[0][0].page_content}")
                else:
                    docs = db.similarity_search(gpt_for_query(user_input, s2), k=1)
                    bot.reply_to(message, f'Под ваш запрос {user_input} подходит это: {docs[0].page_content}')
            else:
                bot.reply_to(message, "⚠️ База данных FAISS не загружена, поиск недоступен.")
    else:
        if db:
            gpt_generated = gpt_for_query(message.text, s1)
            docs = db.similarity_search(gpt_generated, k=5)
            response_text = "\n".join([doc.page_content for doc in docs])

            if len(response_text) > MAX_MESSAGE_LENGTH:
                send_long_message(message.chat.id, response_text)
            else:
                bot.reply_to(message, response_text)
        else:
            bot.reply_to(message, "⚠️ База данных FAISS не загружена, поиск недоступен.")

if __name__ == "__main__":
    print("🤖 Бот запущен! Ожидаем сообщения...")
    bot.infinity_polling()
