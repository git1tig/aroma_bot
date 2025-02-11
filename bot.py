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
print("[DEBUG] Телеграм-бот инициализирован")

# === ПУТИ К ФАЙЛАМ ===
MASLA_FILE = "/app/mono_oils.txt"
FAISS_INDEX_FILE = "/app/index.faiss"

# === ПРОВЕРКА И ЗАГРУЗКА FAISS ===
embs = OpenAIEmbeddings()
db = None

if os.path.exists(FAISS_INDEX_FILE):
    print("[DEBUG] FAISS-хранилище найдено, загружаем...")
    db = FAISS.load_local(FAISS_INDEX_FILE, embs, allow_dangerous_deserialization=True)
else:
    print("[DEBUG] FAISS-хранилище не найдено, создаём заново...")
    if not os.path.exists(MASLA_FILE):
        raise FileNotFoundError(f"❌ Файл {MASLA_FILE} не найден! Добавьте его в каталог.")
    with open(MASLA_FILE, 'r', encoding='utf-8') as f:
        my_text = f.read()
    splitter = MarkdownHeaderTextSplitter(headers_to_split_on=[("#", "Header 1")])
    chunks = splitter.split_text(my_text)  # ✅ `split_text()` уже возвращает нужные объекты
    print(f"[DEBUG] Текст разбит на {len(text_chunks)} частей")
    db = FAISS.from_documents(chunks, embs)
    db.save_local(FAISS_INDEX_FILE)
    print("[DEBUG] FAISS-хранилище создано и сохранено!")

# === ЗАГРУЗКА ДАННЫХ ИЗ GOOGLE SHEETS ===
SHEET_URL = "https://docs.google.com/spreadsheets/d/1MknmvI9_YvjM7bge9tPryRKrrzCi-Ywc5ZfYiyb6Bdg/export?format=csv"
COLUMN_NAMES = ["Name", "Vol", "Price"]

try:
    df = pd.read_csv(SHEET_URL, names=COLUMN_NAMES, encoding='utf-8')
    print("[DEBUG] Данные о маслах успешно загружены из Google Sheets")
except Exception as e:
    print("[DEBUG] Ошибка загрузки данных:", e)
    df = pd.DataFrame(columns=COLUMN_NAMES)

# === ХРАНИЛИЩЕ СОСТОЯНИЙ ===
user_states = {}
drops_counts = {}
current_oils = {}
drop_session_changes = {}

# Определяем состояния:
WAITING_OIL_NAME = "waiting_for_oil"           # для команды /м
WAITING_NEXT_OIL = "waiting_for_next_oil"        # для команды /р, ожидаем название масла или "*"
WAITING_DROPS = "waiting_for_drop_quantity"      # для команды /р, ожидаем количество капель

# === ФУНКЦИЯ GPT-4o ===
def gpt_for_query(prompt: str, system_message: str) -> str:
    """Отправляет запрос в GPT-4o-mini и получает ответ."""
    print(f"[DEBUG] Отправка запроса в GPT-4o: {prompt} | Система: {system_message}")
    response = openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_message},
            {"role": "user", "content": prompt}
        ],
        temperature=1
    )
    result = response.choices[0].message.content
    print(f"[DEBUG] Ответ GPT-4o получен")
    return result

# === ФУНКЦИЯ ЭКРАНИРОВАНИЯ ДЛЯ MARKDOWNV2 ===
def escape_markdown(text):
    """Экранирует специальные символы для MarkdownV2, чтобы избежать ошибок Telegram."""
    escape_chars = r"\_*[]()~`>#+-=|{}.!<>"
    return "".join(f"\\{char}" if char in escape_chars else char for char in text)

# === ОБРАБОТЧИК КОМАНД ===
@bot.message_handler(commands=['start'])
def start_command(message):
    print(f"[DEBUG] /start от chat_id={message.chat.id}")
    bot.reply_to(message, escape_markdown("Привет! 👋 Я ваш помощник по эфирным маслам\\.\n\nДавайте начнём!"), parse_mode="MarkdownV2")

@bot.message_handler(commands=['м'])
def oil_command(message):
    print(f"[DEBUG] /м от chat_id={message.chat.id}")
    bot.reply_to(message, escape_markdown("🔎 Введите название масла, и я найду информацию о нём\\!"), parse_mode="MarkdownV2")
    user_states[message.chat.id] = WAITING_OIL_NAME
    print(f"[DEBUG] Состояние для chat_id={message.chat.id} установлено в {WAITING_OIL_NAME}")

@bot.message_handler(commands=['р'])
def mix_command(message):
    print(f"[DEBUG] /р от chat_id={message.chat.id}")
    bot.reply_to(
        message,
        escape_markdown("Введите название масла \(например, *Лаванда*, *Лимон*, *Мята*\)\\.\n\n"
                        "🛑 Чтобы закончить ввод смеси, отправьте `*`\\."),
        parse_mode="MarkdownV2"
    )
    user_states[message.chat.id] = WAITING_NEXT_OIL
    print(f"[DEBUG] Состояние для chat_id={message.chat.id} установлено в {WAITING_NEXT_OIL}")

@bot.message_handler(func=lambda message: True)
def handle_input(message):
    print(f"[DEBUG] Получено сообщение от chat_id={message.chat.id}: {message.text}")
    user_input = message.text.strip().lower()
    
    if message.chat.id in user_states:
        state = user_states[message.chat.id]
        print(f"[DEBUG] Обработка состояния {state} для chat_id={message.chat.id}")
        if state == WAITING_OIL_NAME:
            # Обработка команды /м: поиск информации по маслу в FAISS
            docs = db.similarity_search(user_input, k=1)
            if docs:
                bot.reply_to(message, escape_markdown(f"📖 *Информация о {user_input}*\n\n{docs[0].page_content}"), parse_mode="MarkdownV2")
                print(f"[DEBUG] Информация найдена по запросу '{user_input}'")
            else:
                bot.reply_to(message, escape_markdown("❌ Информация не найдена в базе\\."), parse_mode="MarkdownV2")
                print(f"[DEBUG] Информация не найдена для '{user_input}'")
            user_states.pop(message.chat.id, None)
            print(f"[DEBUG] Состояние для chat_id={message.chat.id} очищено")
        elif state == WAITING_NEXT_OIL:
            # Обработка команды /р: ожидание названия масла или '*' для завершения смеси
            if user_input == "*":
                total_cost = int(drops_counts.get(message.chat.id, 0))
                mix_info = "\n".join(drop_session_changes.get(message.chat.id, []))
                bot.reply_to(message, escape_markdown(f"🎉 Смесь завершена\\!\n\n"
                                                      f"🧪 *Состав смеси:*\n{mix_info}\n\n"
                                                      f"💰 *Общая стоимость:* {total_cost}р\\."), parse_mode="MarkdownV2")
                print(f"[DEBUG] Смесь завершена для chat_id={message.chat.id}")
                user_states.pop(message.chat.id, None)
                return
            if user_input.capitalize() not in df['Name'].values:
                bot.reply_to(message, escape_markdown(f'❌ Масло "{user_input}" не найдено\\.\nПопробуйте снова:'), parse_mode="MarkdownV2")
                print(f"[DEBUG] Масло '{user_input}' не найдено в базе")
                return
            current_oils[message.chat.id] = user_input
            user_states[message.chat.id] = WAITING_DROPS
            bot.reply_to(message, escape_markdown(f"Введите количество капель для {user_input}\\:"), parse_mode="MarkdownV2")
            print(f"[DEBUG] Состояние для chat_id={message.chat.id} изменено на {WAITING_DROPS}")
        elif state == WAITING_DROPS:
            if not user_input.replace(" ", "").isdigit():
                bot.reply_to(message, escape_markdown("❌ Введите корректное количество капель\\:"), parse_mode="MarkdownV2")
                print(f"[DEBUG] Некорректное количество капель от chat_id={message.chat.id}")
                return
            drop_count = int(user_input.replace(" ", ""))
            oil_name = current_oils[message.chat.id].capitalize()
            # Расчёт стоимости одной капли
            if oil_name in df["Name"].values:
                oil_price = df.loc[df["Name"] == oil_name, "Price"].values[0]
                oil_volume = df.loc[df["Name"] == oil_name, "Vol"].values[0]
                drop_price = oil_price / (oil_volume * 25)
                total_price = drop_price * drop_count
            else:
                total_price = 0
            # Обновляем данные
            drops_counts[message.chat.id] = drops_counts.get(message.chat.id, 0) + total_price
            drop_session_changes[message.chat.id] = drop_session_changes.get(message.chat.id, []) + [f"{oil_name}, {drop_count} капель"]
            # Формируем сводку
            summary = (
                f"✅ Добавлено: *{oil_name}* — {drop_count} капель\\.\n"
                f"Текущий состав смеси:\n{'; '.join(drop_session_changes[message.chat.id])}\n"
                f"Общая стоимость: {int(drops_counts[message.chat.id])}р\\.\n\n"
                "Введите название следующего масла или отправьте `*` для завершения\\."
            )
            bot.reply_to(message, escape_markdown(summary), parse_mode="MarkdownV2")
            print(f"[DEBUG] Обновлена сводка для chat_id={message.chat.id}")
            user_states[message.chat.id] = WAITING_NEXT_OIL
            print(f"[DEBUG] Состояние для chat_id={message.chat.id} изменено на {WAITING_NEXT_OIL}")
    else:
        # Обработка свободного запроса: если ни одна команда не активна
        print(f"[DEBUG] Обработка свободного запроса от chat_id={message.chat.id}")
        keywords = gpt_for_query(user_input, "Выдели ключевые слова для поиска информации о маслах.")
        print(f"[DEBUG] Сгенерированы ключевые слова: {keywords}")
        docs = db.similarity_search(keywords, k=5)
        search_results = "\n\n".join([doc.page_content for doc in docs])
        final_response = gpt_for_query(
            f"Вопрос пользователя: {user_input}\n\nРезультаты поиска:\n{search_results}",
            "Ты эксперт по эфирным маслам. Дай развернутый и понятный ответ, основываясь на результатах поиска."
        )
        print(f"[DEBUG] Получен итоговый ответ от GPT-4o")
        bot.reply_to(message, escape_markdown(final_response), parse_mode="MarkdownV2")
        print(f"[DEBUG] Ответ отправлен пользователю chat_id={message.chat.id}")

if __name__ == "__main__":
    print("[DEBUG] Бот запущен, ожидаем сообщений...")
    bot.infinity_polling()
