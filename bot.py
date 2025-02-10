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
gpt_sys = 'Ты гениальный ароматерапевт и специалист по эфирным маслам, дай развернутый ответ на вопрос клиента, если это проблема, определи пути её решения с помощью эфирных масел, если это запрос на информацию, дай структурированный ответ. На вопросы, никак не связанные с эфирными маслами отвечай крайне коротко, с юмором, и говори, что тебя такие темы не интересуют. Если речь идёт о персонаже, предположи какое эфирное масло ему соответствует'


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
    user_input = message.text.strip().lower()
    print(f"📩 Получено сообщение: {user_input}")  # Логируем входящее сообщение

    if message.chat.id in user_states:
        state = user_states[message.chat.id]

        if state == WAITING_OIL_NAME:
            if db:
                docs = db.similarity_search_with_score(user_input, k=3)
                extracted_texts = [doc[0].page_content for doc in docs]
                faiss_results = "\n".join(extracted_texts)

                gpt_prompt = f"""
                Пользователь задал вопрос: "{user_input}".
                Вот информация, найденная в базе знаний:
                {faiss_results}
                Используй эти данные и ответь пользователю понятным языком.
                """

                gpt_response = gpt_for_query(gpt_prompt, "Ты эксперт по эфирным маслам. Ответь развернуто и понятно.")

                bot.reply_to(message, gpt_response)
                del user_states[message.chat.id]  # Удаляем состояние после ответа
            else:
                bot.reply_to(message, "⚠️ База данных FAISS не загружена, поиск недоступен.")

        elif state == WAITING_NEXT_OIL:
            # Проверяем, есть ли масло в базе из таблицы
            oil_names = df["Name"].str.lower().tolist()  # Приводим названия масел к нижнему регистру
            if user_input in oil_names:
                bot.reply_to(message, f"Вы выбрали масло: {user_input}. Сколько капель?")
                user_states[message.chat.id] = WAITING_DROPS  # Переключаем состояние
            else:
                bot.reply_to(message, f"⚠️ Масло '{user_input}' не найдено в базе. Попробуйте ввести другое название.")

        elif state == WAITING_DROPS:
            try:
                drops = int(user_input)
                bot.reply_to(message, f"Вы добавили {drops} капель масла.")
                del user_states[message.chat.id]  # Убираем состояние
            except ValueError:
                bot.reply_to(message, "Введите корректное число капель.")

    else:
        if db:
            docs = db.similarity_search(user_input, k=5)
            extracted_texts = [doc.page_content for doc in docs]
            faiss_results = "\n".join(extracted_texts)

            gpt_prompt = f"""
            Пользователь задал вопрос: "{user_input}".
            Вот информация, найденная в базе знаний:
            {faiss_results}
            Используй эти данные и ответь пользователю понятным языком.
            """

            gpt_response = gpt_for_query(gpt_prompt, "Ты эксперт по эфирным маслам. Ответь развернуто и понятно.")

            if len(gpt_response) > 4000:
                send_long_message(message.chat.id, gpt_response)
            else:
                bot.reply_to(message, gpt_response)
        else:
            bot.reply_to(message, "⚠️ База данных FAISS не загружена, поиск недоступен.")



if __name__ == "__main__":
    print("🤖 Бот запущен! Ожидаем сообщения...")
    bot.infinity_polling()
