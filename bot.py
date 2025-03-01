import os
import telebot
import openai
import tempfile
import io
from dotenv import load_dotenv
import pandas as pd
from pydub import AudioSegment, silence
from assistent import AssistantDialogManager

# === ЗАГРУЗКА API-КЛЮЧЕЙ И СОЗДАНИЕ ОБЪЕКТА БОТА ===
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not TELEGRAM_BOT_TOKEN or not OPENAI_API_KEY:
    raise ValueError("❌ Отсутствует TELEGRAM_BOT_TOKEN или OPENAI_API_KEY в .env файле!")

openai.api_key = OPENAI_API_KEY
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, parse_mode="MarkdownV2")
print("[DEBUG] Телеграм-бот инициализирован")

# === ЗАГРУЗКА ДАННЫХ ИЗ GOOGLE SHEETS ДЛЯ РЕЖИМА /р ===
SHEET_URL = "https://docs.google.com/spreadsheets/d/1MknmvI9_YvjM7bge9tPryRKrrzCi-Ywc5ZfYiyb6Bdg/export?format=csv"
COLUMN_NAMES = ["Name", "Vol", "Price"]

try:
    df = pd.read_csv(SHEET_URL, names=COLUMN_NAMES, encoding="utf-8")
    print("[DEBUG] Данные о маслах успешно загружены из Google Sheets")
except Exception as e:
    print("[DEBUG] Ошибка загрузки данных:", e)
    df = pd.DataFrame(columns=COLUMN_NAMES)

# === Ассистент для диалога (из assistent.py) ===
assistant_manager = AssistantDialogManager(time_limit=1200)  # 20 минут неактивности

# === Глобальное состояние ===
user_states = {}
drops_counts = {}
current_oils = {}
drop_session_changes = {}

# Состояния диалога (используются только для режима /р)
WAITING_NEXT_OIL = "waiting_for_next_oil"
WAITING_DROPS = "waiting_for_drop_quantity"

def escape_markdown(text):
    """Экранирует специальные символы для MarkdownV2."""
    escape_chars = r"\_*[]()~`>#+-=|{}.!<>"
    return "".join(f"\\{char}" if char in escape_chars else char for char in text)

def show_bot_capabilities(chat_id):
    """
    Меню возможностей бота.
    """
    capabilities = (
        "*Возможности бота:*\n\n"
        "✅ `/start` – Приветствие и вывод возможностей\n"
        "✅ `/р` – Рассчитать смесь масел\n"
        "✅ Или просто напишите свой вопрос, и я отвечу, используя свои знания.\n\n"
    )
    bot.send_message(chat_id, escape_markdown(capabilities), parse_mode="MarkdownV2")

def simple_transcribe_audio(audio_file_path, silence_thresh=-40.0, min_silence_len=1000):
    """
    Упрощённая транскрипция аудио с проверкой на пустоту.
    Если аудиофайл пустой (содержит только тишину или нулевую длительность), возвращается None.
    """
    try:
        audio = AudioSegment.from_file(audio_file_path)
        
        # Если аудио имеет нулевую продолжительность
        if len(audio) == 0:
            print("Аудиофайл имеет нулевую длительность.")
            return None

        # Проверяем, содержит ли аудио ненулевые сегменты
        nonsilent_segments = silence.detect_nonsilent(
            audio, 
            min_silence_len=min_silence_len, 
            silence_thresh=silence_thresh
        )
        if len(nonsilent_segments) == 0:
            print("Аудиофайл содержит только тишину.")
            return None
        
        # Конвертируем в моно WAV
        audio = audio.set_channels(1)
        wav_io = io.BytesIO()
        audio.export(wav_io, format="wav")
        wav_io.seek(0)
        wav_io.name = "audio.wav"
        
        # Отправляем аудио в Whisper API для транскрипции
        transcript = openai.audio.transcriptions.create(
            file=wav_io,
            model="whisper-1",
            language="ru",
            response_format="json"
        )
        text = transcript.text.strip()
        return text if text else None
    except Exception as e:
        print(f"Ошибка транскрипции: {e}", flush=True)
        return None

@bot.message_handler(commands=['start'])
def start_command(message):
    print(f"[DEBUG] /start от chat_id={message.chat.id}")
    bot.reply_to(
        message,
        escape_markdown("Привет! 👋 Я ваш помощник по эфирным маслам\\.\n\nДавайте начнём!"),
        parse_mode="MarkdownV2"
    )
    show_bot_capabilities(message.chat.id)

@bot.message_handler(commands=['р'])
def mix_command(message):
    """
    Начало режима расчёта смеси масел.
    """
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
    """
    Обработка голосовых сообщений (Speech-to-Text).
    """
    print(f"[DEBUG] Получено голосовое сообщение от chat_id={message.chat.id}")
    try:
        file_info = bot.get_file(message.voice.file_id)
        downloaded_file = bot.download_file(file_info.file_path)

        # Сохраняем аудиофайл во временный файл
        with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as temp_audio:
            temp_audio.write(downloaded_file)
            temp_audio_path = temp_audio.name

        recognized_text = simple_transcribe_audio(temp_audio_path)
        os.remove(temp_audio_path)

        if recognized_text:
            print(f"[DEBUG] Распознанный текст: {recognized_text}")
            # Переиспользуем функцию handle_input, подменив message.text
            message.text = recognized_text
            handle_input(message)
   
