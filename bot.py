# -*- coding: utf-8 -*- a change
import os
import telebot
import openai
from dotenv import load_dotenv
import pandas as pd
import requests
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import FAISS
from langchain.text_splitter import MarkdownHeaderTextSplitter
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
MASLA_FILE = "/app/mono_oils.txt"  # Файл с описанием масел
FAISS_INDEX_FILE = "/app/index.faiss"  # Файл хранилища FAISS

# === ПРОВЕРКА И ЗАГРУЗКА FAISS ===
embs = OpenAIEmbeddings()

if os.path.exists(FAISS_INDEX_FILE):
    print(" Загружаем FAISS-хранилище из файла...")
    db = FAISS.load_local(FAISS_INDEX_FILE, embs, allow_dangerous_deserialization=True)
else:
    print("Файл FAISS-хранилища не найден, создаем заново...")

    if not os.path.exists(MASLA_FILE):
        print(f"Проверяем путь: {MASLA_FILE}")

        raise FileNotFoundError(f"❌ Файл {MASLA_FILE} не найден! Добавь его в каталог.")

    with open(MASLA_FILE, 'r', encoding='utf-8') as f:
        my_text = f.read()

    # Разбиваем текст на части
    splitter = MarkdownHeaderTextSplitter(headers_to_split_on=[("#", "Header 1")])
    chunks = splitter.split_text(my_text)

    # Создаем векторное хранилище
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
drops_counts = {}
current_oils = {}
task_is_over = {}
drop_session_changes = {}

WAITING_OIL_NAME = "waiting_for_oil"
WAITING_DROPS = 'waiting_for_drop_quantity'
WAITING_NEXT_OIL = 'waiting_for_next_oil'
DROP_STOP = 'drop_stop'

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

@bot.message_handler(func=lambda message: True)
def handle_input(message):
    user_input = message.text.strip().lower()
    
    if message.chat.id in user_states:
        if user_states[message.chat.id] == WAITING_OIL_NAME:
            docs = db.similarity_search_with_score(user_input, k=1)
            if docs[0][1] < 0.37:
                bot.reply_to(message, f"Информация о {user_input}: {docs[0][0].page_content}")
            else:
                docs = db.similarity_search(gpt_for_query(user_input, s2), k=1)
                bot.reply_to(message, f'Под ваш запрос {user_input} подходит это: {docs[0].page_content}')

    else:
        gpt_generated = gpt_for_query(message.text, s1)
        docs = db.similarity_search(gpt_generated, k=5)
        response_text = "\n".join([doc.page_content for doc in docs])
        bot.reply_to(message, response_text)

if __name__ == "__main__":
    print("🤖 Бот запущен! Ожидаем сообщения...")
    bot.infinity_polling()
