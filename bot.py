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
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, parse_mode="MarkdownV2")

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

# === ФУНКЦИЯ GPT-4o ===
def gpt_for_query(prompt: str, system_message: str) -> str:
    """Отправляет запрос в ChatGPT-4o-mini и получает ответ."""
    response = openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_message},
            {"role": "user", "content": prompt}
        ],
        temperature=1
    )
    return response.choices[0].message.content

# === ФУНКЦИЯ ЭКРАНИРОВАНИЯ ДЛЯ MARKDOWNV2 ===
def escape_markdown(text):
    """Экранирует символы для MarkdownV2, чтобы избежать ошибок Telegram."""
    escape_chars = r"\_*[]()~`>#+-=|{}.!<>"
    return "".join(f"\\{char}" if char in escape_chars else char for char in text)

# === ФУНКЦИЯ ВЫВОДА ВОЗМОЖНОСТЕЙ БОТА ===
def send_bot_options(chat_id):
    bot.send_message(chat_id, 
                     escape_markdown("*✨ Что я могу для вас сделать? ✨*\n\n"
                     "🛠 *Мои возможности:*\n"
                     "✅ `/р` – *Создать свою уникальную смесь масел*\n"
                     "✅ `/м` – *Получить информацию о любом эфирном масле*\n"
                     "✅ *Просто напишите свой вопрос*, и я помогу разобраться!\n\n"
                     "💡 *Попробуйте прямо сейчас!* 😊"), 
                     parse_mode="MarkdownV2")

# === ОБРАБОТЧИК КОМАНД ===
@bot.message_handler(commands=['start'])
def start_command(message):
    bot.reply_to(message, escape_markdown("Привет! 👋 Я ваш помощник по эфирным маслам.\n\nДавайте начнём! 😊"))
    send_bot_options(message.chat.id)

@bot.message_handler(commands=['р'])
def oil_command(message):
    bot.reply_to(
        message, 
        escape_markdown("Введите название масла (например, *Лаванда*, *Лимон*, *Мята*).\n\n"
                        "🛑 Чтобы закончить ввод смеси, отправьте `*`."), 
        parse_mode="MarkdownV2"
    )
    user_states[message.chat.id] = WAITING_NEXT_OIL
    send_bot_options(message.chat.id)

@bot.message_handler(commands=['м'])
def oil_command(message):
    bot.reply_to(message, escape_markdown("🔎 Введите название масла, и я найду информацию о нём!"), parse_mode="MarkdownV2")
    user_states[message.chat.id] = WAITING_OIL_NAME
    send_bot_options(message.chat.id)

@bot.message_handler(func=lambda message: True)
def handle_input(message):
    user_input = message.text.strip().lower()

    if db:
        # 🔹 1. Генерируем ключевые слова
        gpt_prompt_keywords = f"Определи ключевые слова для поиска по базе знаний об эфирных маслах на основе запроса: '{user_input}'."
        keywords = gpt_for_query(gpt_prompt_keywords, "Ты эксперт по эфирным маслам. Генерируй ключевые слова для поиска.")

        # 🔹 2. Выполняем поиск в FAISS
        docs = db.similarity_search(keywords, k=3)
        extracted_texts = [doc.page_content for doc in docs]
        faiss_results = "\n\n".join(extracted_texts)

        # 🔹 3. Передаём в GPT-4o
        gpt_prompt_final = f"Вопрос пользователя: '{user_input}'.\n\nНайденная информация из базы знаний:\n{faiss_results}\n\nИспользуй эти данные и ответь пользователю понятным языком."
        gpt_response = gpt_for_query(gpt_prompt_final, "Ты эксперт по эфирным маслам. Ответь развернуто и понятно.")

        # 🔹 4. Отправляем ответ пользователю
        bot.reply_to(message, escape_markdown(gpt_response), parse_mode="MarkdownV2")
    else:
        bot.reply_to(message, "⚠️ *База данных FAISS не загружена, поиск недоступен.*")

# === ЗАПУСК БОТА ===
if __name__ == "__main__":
    bot.infinity_polling()
