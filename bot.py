import os
import telebot
import openai
import tempfile
import io
from dotenv import load_dotenv
import pandas as pd
import requests
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import FAISS
from langchain.text_splitter import MarkdownHeaderTextSplitter
from langchain.schema import Document
import re
from pydub import AudioSegment, silence

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

# === ЗАГРУЗКА FAISS ===
embs = OpenAIEmbeddings()
db = None

if os.path.exists(FAISS_INDEX_FILE):
    print("[DEBUG] FAISS-хранилище найдено, загружаем...")
    db = FAISS.load_local(FAISS_INDEX_FILE, embs, allow_dangerous_deserialization=True)
else:
    print("[DEBUG] FAISS-хранилище не найдено, создаём заново...")
    if not os.path.exists(MASLA_FILE):
        raise FileNotFoundError(f"❌ Файл {MASLA_FILE} не найден! Добавьте его в каталог.")
    with open(MASLA_FILE, "r", encoding="utf-8") as f:
        my_text = f.read()
    splitter = MarkdownHeaderTextSplitter(headers_to_split_on=[("#", "Header 1")])
    chunks = splitter.split_text(my_text)
    db = FAISS.from_documents(chunks, embs)
    db.save_local(FAISS_INDEX_FILE)
    print("[DEBUG] FAISS-хранилище создано и сохранено!")

# === ЗАГРУЗКА ДАННЫХ ИЗ GOOGLE SHEETS ===
SHEET_URL = "https://docs.google.com/spreadsheets/d/1MknmvI9_YvjM7bge9tPryRKrrzCi-Ywc5ZfYiyb6Bdg/export?format=csv"
COLUMN_NAMES = ["Name", "Vol", "Price"]

try:
    df = pd.read_csv(SHEET_URL, names=COLUMN_NAMES, encoding="utf-8")
    print("[DEBUG] Данные о маслах успешно загружены из Google Sheets")
except Exception as e:
    print("[DEBUG] Ошибка загрузки данных:", e)
    df = pd.DataFrame(columns=COLUMN_NAMES)

# === Глобальное состояние ===
user_states = {}
drops_counts = {}
current_oils = {}
drop_session_changes = {}
sys1 = ("Ты гениальный ароматерапевт и специалист по эфирным маслам, "
        "дай развернутый ответ на вопрос клиента, если это проблема, определи пути её решения с помощью эфирных масел, "
        "если это запрос на информацию, дай структурированный ответ. На вопросы, никак не связанные с эфирными маслами отвечай крайне коротко, с юмором, и говори, что тебя такие темы не интересуют. "
        "Если речь идёт о персонаже, предположи какое эфирное масло ему соответствует")

# Состояния диалога
WAITING_OIL_NAME = "waiting_for_oil"
WAITING_NEXT_OIL = "waiting_for_next_oil"
WAITING_DROPS = "waiting_for_drop_quantity"

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
    print("[DEBUG] Ответ GPT-4o получен")
    return result

def escape_markdown(text):
    """Экранирует специальные символы для MarkdownV2."""
    escape_chars = r"\_*[]()~`>#+-=|{}.!<>"
    return "".join(f"\\{char}" if char in escape_chars else char for char in text)

def show_bot_capabilities(chat_id):
    capabilities = (
        "*Возможности бота:*\n\n"
        "✅ `/start` – Приветствие и вывод возможностей\n"
        "✅ `/м` – Получить информацию об эфирном масле\n"
        "✅ `/р` – Рассчитать смесь масел\n"
        "✅ Или просто напишите свой вопрос, и я отвечу, используя свои знания.\n\n"
    )
    bot.send_message(chat_id, escape_markdown(capabilities), parse_mode="MarkdownV2")

def simple_transcribe_audio(audio_file_path):
    """
    Упрощённая транскрипция аудио с использованием Whisper API.
    Аудиофайл конвертируется в моно WAV с использованием BytesIO,
    после чего отправляется для распознавания.
    Возвращается распознанный текст или None.
    """
    try:
        # Конвертация в моно WAV и экспорт в BytesIO
        audio = AudioSegment.from_file(audio_file_path)
        audio = audio.set_channels(1)
        wav_io = io.BytesIO()
        audio.export(wav_io, format="wav")
        wav_io.seek(0)
        
        # Отправляем на транскрипцию
        transcript = openai.Audio.transcribe("whisper-1", wav_io, language="ru")
        text = transcript.get("text", "").strip()
        return text if text else None
    except Exception as e:
        print(f"Ошибка транскрипции: {e}", flush=True)
        return None

@bot.message_handler(commands=['start'])
def start_command(message):
    print(f"[DEBUG] /start от chat_id={message.chat.id}")
    bot.reply_to(message, escape_markdown("Привет! 👋 Я ваш помощник по эфирным маслам\\.\n\nДавайте начнём!"), parse_mode="MarkdownV2")
    show_bot_capabilities(message.chat.id)

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
        escape_markdown("Введите название масла \\(например, Лаванда, Лимон, Мята\\)\\.\n\n"
                        "🛑 Чтобы закончить ввод смеси, отправьте `*`\\."), 
        parse_mode="MarkdownV2"
    )
    user_states[message.chat.id] = WAITING_NEXT_OIL
    print(f"[DEBUG] Состояние для chat_id={message.chat.id} установлено в {WAITING_NEXT_OIL}")

@bot.message_handler(content_types=['voice'])
def handle_voice_message(message):
    print(f"[DEBUG] Получено голосовое сообщение от chat_id={message.chat.id}")
    try:
        file_info = bot.get_file(message.voice.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        # Сохраняем аудиофайл во временный файл с расширением .ogg
        with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as temp_audio:
            temp_audio.write(downloaded_file)
            temp_audio_path = temp_audio.name

        recognized_text = simple_transcribe_audio(temp_audio_path)
        os.remove(temp_audio_path)

        if recognized_text:
            print(f"[DEBUG] Распознанный текст: {recognized_text}")
            message.text = recognized_text
            handle_input(message)
        else:
            bot.reply_to(message, escape_markdown("❌ Не удалось распознать голосовое сообщение или аудио пустое\\."), parse_mode="MarkdownV2")
    except Exception as e:
        print(f"[DEBUG] Ошибка при обработке голосового сообщения: {e}")
        bot.reply_to(message, escape_markdown("❌ Ошибка при обработке голосового сообщения\\."), parse_mode="MarkdownV2")

@bot.message_handler(func=lambda message: True)
def handle_input(message):
    print(f"[DEBUG] Получено сообщение от chat_id={message.chat.id}: {message.text}")
    user_input = message.text.strip().lower()

    if message.chat.id in user_states:
        state = user_states[message.chat.id]
        print(f"[DEBUG] Текущее состояние для chat_id={message.chat.id}: {state}")
        if state == WAITING_OIL_NAME:
            docs = db.similarity_search(user_input, k=1)
            if docs:
                bot.reply_to(message, escape_markdown(f"📖 *Информация о {user_input}*\n\n{docs[0].page_content}"), parse_mode="MarkdownV2")
                print(f"[DEBUG] Информация найдена для '{user_input}'")
                show_bot_capabilities(message.chat.id)
            else:
                bot.reply_to(message, escape_markdown("❌ Информация не найдена в базе\\."), parse_mode="MarkdownV2")
                print(f"[DEBUG] Информация не найдена для '{user_input}'")
                show_bot_capabilities(message.chat.id)
            user_states.pop(message.chat.id, None)
            print(f"[DEBUG] Состояние для chat_id={message.chat.id} очищено")
        elif state == WAITING_NEXT_OIL:
            if user_input == "*":
                total_cost = int(drops_counts.get(message.chat.id, 0))
                mix_info = "\n".join(drop_session_changes.get(message.chat.id, []))
                bot.reply_to(message, escape_markdown(f"🎉 Смесь завершена\\!\n\n"
                                                      f"🧪 *Состав смеси:*\n{mix_info}\n\n"
                                                      f"💰 *Общая стоимость:* {total_cost}р\\."), parse_mode="MarkdownV2")
                print(f"[DEBUG] Смесь завершена для chat_id={message.chat.id}")
                show_bot_capabilities(message.chat.id)
                drops_counts.pop(message.chat.id, None)
                drop_session_changes.pop(message.chat.id, None)
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
            if oil_name in df["Name"].values:
                oil_price = df.loc[df["Name"] == oil_name, "Price"].values[0]
                oil_volume = df.loc[df["Name"] == oil_name, "Vol"].values[0]
                drop_price = oil_price / (oil_volume * 25)
                total_price = drop_price * drop_count
            else:
                total_price = 0
            drops_counts[message.chat.id] = drops_counts.get(message.chat.id, 0) + total_price
            drop_session_changes[message.chat.id] = drop_session_changes.get(message.chat.id, []) + [f"{oil_name}, {drop_count} капель"]
            summary = (f"✅ Добавлено: *{oil_name}* — {drop_count} капель\\.\n"
                       f"Текущий состав смеси:\n{'; '.join(drop_session_changes[message.chat.id])}\n"
                       f"Общая стоимость: {int(drops_counts[message.chat.id])}р\\.\n\n"
                       "Введите название следующего масла или отправьте `*` для завершения\\.")
            bot.reply_to(message, escape_markdown(summary), parse_mode="MarkdownV2")
            print(f"[DEBUG] Сводка обновлена для chat_id={message.chat.id}")
            user_states[message.chat.id] = WAITING_NEXT_OIL
            print(f"[DEBUG] Состояние для chat_id={message.chat.id} изменено на {WAITING_NEXT_OIL}")
    else:
        print(f"[DEBUG] Обработка свободного запроса от chat_id={message.chat.id}")
        keywords = gpt_for_query(user_input, "Выдели ключевые слова для поиска информации о маслах.")
        print(f"[DEBUG] Сгенерированы ключевые слова: {keywords}")
        docs = db.similarity_search(keywords, k=5)
        search_results = "\n\n".join([doc.page_content for doc in docs])
        final_response = gpt_for_query(
            f"Вопрос пользователя: {user_input}\n\nРезультаты поиска:\n{search_results}", sys1
        )
        print(f"[DEBUG] Итоговый ответ сформирован, отправляем пользователю")
        bot.reply_to(message, escape_markdown(final_response), parse_mode="MarkdownV2")
        print(f"[DEBUG] Ответ отправлен chat_id={message.chat.id}")

if __name__ == "__main__":
    print("[DEBUG] Бот запущен, ожидаем сообщений...")
    bot.infinity_polling()
