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
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, parse_mode="Markdown")

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
                     "*✨ Что я могу для вас сделать? ✨*\n\n"
                     "🛠 *Мои возможности:*\n"
                     "✅ `/р` – *Создать свою уникальную смесь масел*\n"
                     "✅ `/м` – *Получить информацию о любом эфирном масле*\n"
                     "✅ *Просто напишите свой вопрос*, и я помогу разобраться!\n\n"
                     "💡 *Попробуйте прямо сейчас!* 😊", 
                     parse_mode="Markdown")

# === ОБРАБОТЧИК КОМАНД ===
@bot.message_handler(commands=['start'])
def start_command(message):
    bot.reply_to(message, "*Привет! 👋 Я ваш помощник по эфирным маслам.*\n\nДавайте начнём! 😊")
    send_bot_options(message.chat.id)

@bot.message_handler(commands=['р'])
def oil_command(message):
    bot.reply_to(
        message, 
        "Введите название масла (например, *Лаванда*, *Лимон*, *Мята*).\n\n"
        "🛑 *Чтобы закончить ввод смеси, отправьте `\\*`*.", 
        parse_mode="MarkdownV2"
    )
    user_states[message.chat.id] = WAITING_NEXT_OIL
    

@bot.message_handler(commands=['м'])
def oil_command(message):
    bot.reply_to(message, "🔎 *Введите название масла, и я найду информацию о нём!*")
    user_states[message.chat.id] = WAITING_OIL_NAME
    

@bot.message_handler(commands=['стоп'])
def cancel_command(message):
    bot.reply_to(message, "🚫 *Команда отменена.* Начните заново с `/м` или `/р`.")
    user_states.pop(message.chat.id, None)
    send_bot_options(message.chat.id)

MAX_MESSAGE_LENGTH = 4000  

def send_long_message(chat_id, text):
    for i in range(0, len(text), MAX_MESSAGE_LENGTH):
        bot.send_message(chat_id, text[i:i + MAX_MESSAGE_LENGTH])

@bot.message_handler(func=lambda message: True)
def gpt_for_query(prompt, system):
    """Отправляет запрос в ChatGPT-4o-mini и получает ответ."""
    response = openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": 'Ты гениальный ароматерапевт и специалист по эфирным маслам, дай развернутый ответ на вопрос клиента, если это проблема, определи пути её решения с помощью эфирных масел, если это запрос на информацию, дай структурированный ответ. На вопросы, никак не связанные с эфирными маслами отвечай крайне коротко, с юмором, и говори, что тебя такие темы не интересуют. Если речь идёт о персонаже, предположи какое эфирное масло ему соответствует'},
            {"role": "user", "content": prompt}
        ],
        temperature=1
    )
    return response.choices[0].message.content

def handle_input(message):
    user_input = message.text.strip().lower()

    if message.chat.id in user_states:
        state = user_states[message.chat.id]

        if state == WAITING_OIL_NAME:
            if db:
                docs = db.similarity_search_with_score(user_input, k=1)
                if docs[0][1] < 0.37:
                    bot.reply_to(message, f"*Информация о {user_input}:*\n\n{docs[0][0].page_content}", parse_mode="MarkdownV2")
                else:
                    docs = db.similarity_search(user_input, k=1)
                    bot.reply_to(message, f"*Под ваш запрос '{user_input}' подходит:*\n\n{docs[0].page_content}", parse_mode="MarkdownV2")
            else:
                bot.reply_to(message, "⚠️ *База данных FAISS не загружена, поиск недоступен.*")

        elif state == WAITING_NEXT_OIL:
            if user_input != '*':
                if user_input.capitalize() not in df['Name'].values:
                    bot.reply_to(message, f'⚠️ *Масло "{user_input}" не найдено.* Попробуйте другое:')
                    return
                
                if message.chat.id not in drop_session_changes:
                    drop_session_changes[message.chat.id] = []
                    drops_counts[message.chat.id] = 0

                current_oils[message.chat.id] = user_input
                user_states[message.chat.id] = WAITING_DROPS

                existing_oils = "; ".join(drop_session_changes[message.chat.id])
                bot.reply_to(message, f"*Уже введено:*\n\n{existing_oils}\n\n"
                                      f"Теперь введите количество капель для *{user_input}*:", parse_mode="MarkdownV2")
            else:
                total_cost = int(drops_counts.get(message.chat.id, 0))
                mix_info = "; ".join(drop_session_changes.get(message.chat.id, []))
                bot.reply_to(message, f"🎉 *Смесь завершена!*\n\n"
                                      f"🧪 *Состав смеси:* {mix_info}\n"
                                      f"💰 *Общая стоимость:* {total_cost}р.\n", parse_mode="MarkdownV2")
                
                drop_session_changes.pop(message.chat.id, None)
                drops_counts.pop(message.chat.id, None)
                user_states.pop(message.chat.id, None)

                send_bot_options(message.chat.id)

    else:
        if db:
            # 🔹 1. Генерируем ключевые слова с помощью GPT-4o
            gpt_prompt_keywords = f"""
            Пользователь задал вопрос: "{user_input}".
            Определи ключевые слова или фразы для поиска информации в базе знаний об эфирных маслах.
            Ответ должен содержать только ключевые слова, без пояснений.
            """
            keywords = gpt_for_query(gpt_prompt_keywords, "Ты эксперт по эфирным маслам. Определи ключевые слова для поиска.")

            print(f"🔍 Генерированные ключевые слова: {keywords}")

            # 🔹 2. Выполняем поиск в FAISS по ключевым словам
            docs = db.similarity_search(keywords, k=3)
            extracted_texts = [doc.page_content for doc in docs]
            faiss_results = "\n\n".join(extracted_texts)

            # 🔹 3. Передаём запрос пользователя и результаты поиска в GPT-4o
            gpt_prompt_final = f"""
            Пользователь задал вопрос: "{user_input}".

            Вот информация, найденная в базе знаний:
            {faiss_results}

            Используй эти данные и ответь пользователю понятным языком.
            """
            gpt_response = gpt_for_query(gpt_prompt_final, "Ты эксперт по эфирным маслам. Ответь развернуто и понятно.")

            # 🔹 4. Отправляем ответ пользователю
            if len(gpt_response) > 4000:
                send_long_message(message.chat.id, gpt_response)
            else:
                bot.reply_to(message, gpt_response)
        else:
            bot.reply_to(message, "⚠️ *База данных FAISS не загружена, поиск недоступен.*")


if __name__ == "__main__":
    bot.infinity_polling()
