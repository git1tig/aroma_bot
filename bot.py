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
        else:
            bot.reply_to(
                message,
                escape_markdown("❌ Не удалось распознать голосовое сообщение или аудио пустое\\."),
                parse_mode="MarkdownV2"
            )
    except Exception as e:
        print(f"[DEBUG] Ошибка при обработке голосового сообщения: {e}")
        bot.reply_to(
            message,
            escape_markdown("❌ Ошибка при обработке голосового сообщения\\."),
            parse_mode="MarkdownV2"
        )

@bot.message_handler(func=lambda message: True)
def handle_input(message):
    """
    Обработка всех прочих сообщений (не /start, не /р).
    """
    print(f"[DEBUG] Получено сообщение от chat_id={message.chat.id}: {message.text}")
    user_input = message.text.strip().lower()

    # Проверяем, находится ли пользователь в каком-то "режиме"
    if message.chat.id in user_states:
        state = user_states[message.chat.id]
        print(f"[DEBUG] Текущее состояние для chat_id={message.chat.id}: {state}")

        # РЕЖИМ "/р": ВВОД НАЗВАНИЙ МАСЕЛ И ИХ КОЛИЧЕСТВА
        if state == WAITING_NEXT_OIL:
            if user_input == "*":
                # Завершаем ввод смеси
                total_cost = int(drops_counts.get(message.chat.id, 0))
                mix_info = "\n".join(drop_session_changes.get(message.chat.id, []))
                bot.reply_to(
                    message,
                    escape_markdown(
                        f"🎉 Смесь завершена\\!\n\n"
                        f"🧪 *Состав смеси:*\n{mix_info}\n\n"
                        f"💰 *Общая стоимость:* {total_cost}р\\."
                    ),
                    parse_mode="MarkdownV2"
                )
                print(f"[DEBUG] Смесь завершена для chat_id={message.chat.id}")
                show_bot_capabilities(message.chat.id)
                drops_counts.pop(message.chat.id, None)
                drop_session_changes.pop(message.chat.id, None)
                user_states.pop(message.chat.id, None)
                return

            # Проверяем, есть ли это масло в таблице
            if user_input.capitalize() not in df['Name'].values:
                bot.reply_to(
                    message,
                    escape_markdown(f'❌ Масло "{user_input}" не найдено\\.\nПопробуйте снова:'),
                    parse_mode="MarkdownV2"
                )
                print(f"[DEBUG] Масло '{user_input}' не найдено в базе")
                return

            # Если нашли масло
            current_oils[message.chat.id] = user_input
            user_states[message.chat.id] = WAITING_DROPS
            bot.reply_to(
                message,
                escape_markdown(f"Введите количество капель для {user_input}\\:"),
                parse_mode="MarkdownV2"
            )
            print(f"[DEBUG] Состояние для chat_id={message.chat.id} изменено на {WAITING_DROPS}")

        elif state == WAITING_DROPS:
            # Проверяем, что пользователь ввёл целое число
            if not user_input.replace(" ", "").isdigit():
                bot.reply_to(
                    message,
                    escape_markdown("❌ Введите корректное количество капель\\:"),
                    parse_mode="MarkdownV2"
                )
                print(f"[DEBUG] Некорректное количество капель от chat_id={message.chat.id}")
                return

            drop_count = int(user_input.replace(" ", ""))
            oil_name = current_oils[message.chat.id].capitalize()

            # Считаем цену (исходя из Price / (Vol * 25))
            if oil_name in df["Name"].values:
                oil_price = df.loc[df["Name"] == oil_name, "Price"].values[0]
                oil_volume = df.loc[df["Name"] == oil_name, "Vol"].values[0]
                drop_price = oil_price / (oil_volume * 25)
                total_price = drop_price * drop_count
            else:
                total_price = 0

            # Обновляем общую стоимость
            drops_counts[message.chat.id] = drops_counts.get(message.chat.id, 0) + total_price

            # Запоминаем изменения в составе
            drop_session_changes[message.chat.id] = drop_session_changes.get(message.chat.id, []) + [
                f"{oil_name}, {drop_count} капель"
            ]

            summary = (
                f"✅ Добавлено: *{oil_name}* — {drop_count} капель\\.\n"
                f"Текущий состав смеси:\n{'; '.join(drop_session_changes[message.chat.id])}\n"
                f"Общая стоимость: {int(drops_counts[message.chat.id])}р\\.\n\n"
                "Введите название следующего масла или отправьте `*` для завершения\\."
            )
            bot.reply_to(message, escape_markdown(summary), parse_mode="MarkdownV2")
            print(f"[DEBUG] Сводка обновлена для chat_id={message.chat.id}")
            user_states[message.chat.id] = WAITING_NEXT_OIL
            print(f"[DEBUG] Состояние для chat_id={message.chat.id} изменено на {WAITING_NEXT_OIL}")

    else:
        # ОБЫЧНЫЙ РЕЖИМ: ПЕРЕДАЁМ СООБЩЕНИЕ АССИСТЕНТУ (БЕЗ ЛОКАЛЬНОЙ БАЗЫ)
        print(f"[DEBUG] Обработка свободного запроса от chat_id={message.chat.id}")

        # Вызываем ассистента с учётом истории диалога
        assistant_reply = assistant_manager.ask_assistant(message.chat.id, message.text.strip())

        # Отправляем ответ пользователю
        bot.reply_to(message, escape_markdown(assistant_reply), parse_mode="MarkdownV2")
        print(f"[DEBUG] Ответ ассистента отправлен chat_id={message.chat.id}")

if __name__ == "__main__":
    print("[DEBUG] Бот запущен, ожидаем сообщений...")
    bot.infinity_polling()
