# assistent.py

import os
import time
import openai
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()
# Инициализируем openai.api_key
openai.api_key = os.getenv("OPENAI_API_KEY")

# Если используется ваш ассистент в OpenAI Threads API — укажите его ID:
assistant_id = 'asst_gDfpe4WMzW9bUUaN3IfyivY8'

class AssistantDialogManager:
    def __init__(self, time_limit=1200):
        """
        time_limit = 1200 секунд (20 минут) - 
        по истечении этого времени тред считается «протухшим» 
        и при новом сообщении создаётся заново.
        """
        self.threads = {}  # user_id -> (thread_id, last_access_time)
        self.time_limit = time_limit

    def _parse_content_to_str(self, content):
        """
        Преобразует контент ответа (список TextContentBlock и т.д.) в обычную строку.
        """
        if isinstance(content, list):
            text_blocks = []
            for block in content:
                if hasattr(block, "text") and hasattr(block.text, "value"):
                    text_blocks.append(block.text.value)
                else:
                    text_blocks.append(str(block))
            joined = "\n".join(text_blocks)
        else:
            joined = str(content)
        return joined.replace("\\n", "\n").strip()

    def _get_thread_id(self, user_id: int) -> str:
        """
        Возвращает thread_id для данного user_id.
        Если тред отсутствует или 'протух', создаём новый.
        """
        current_time = time.time()
        # Проверяем, есть ли уже тред для этого пользователя
        if user_id not in self.threads:
            # Создаём новый тред
            thread = openai.beta.threads.create()
            self.threads[user_id] = (thread.id, current_time)
        else:
            thread_id, last_access = self.threads[user_id]
            # Если пользователь молчал дольше, чем time_limit, 
            # закрываем старый тред и создаём новый
            if current_time - last_access > self.time_limit:
                try:
                    openai.beta.threads.delete(thread_id=thread_id)
                except:
                    pass
                # Создаём новый тред
                thread = openai.beta.threads.create()
                self.threads[user_id] = (thread.id, current_time)

        # Обновляем время последнего доступа для пользователя
        thread_id, _ = self.threads[user_id]
        self.threads[user_id] = (thread_id, current_time)
        return thread_id

    def add_user_message(self, user_id: int, content: str):
        """
        Добавляет в диалог сообщение от пользователя (role='user').
        """
        thread_id = self._get_thread_id(user_id)
        openai.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=content
        )
        self.threads[user_id] = (thread_id, time.time())

    def run_assistant(self, user_id: int) -> str:
        """
        Запускает ассистента (создаёт run) и дожидается ответа,
        затем возвращает текст ответа ассистента.
        """
        thread_id = self._get_thread_id(user_id)
        run = openai.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=assistant_id
        )
        # Ждём завершения run
        while True:
            run_status = openai.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
            if run_status.status == "completed":
                break
            time.sleep(1)

        # Получаем самый свежий ответ ассистента (не pinned)
        messages = openai.beta.threads.messages.list(thread_id=thread_id)
        sorted_msgs = sorted(messages.data, key=lambda m: m.created_at, reverse=True)
        for msg in sorted_msgs:
            if msg.role == "assistant":
                # Пропускаем pinned-сообщения
                pinned = getattr(msg, "metadata", {}).get("pinned", False)
                if not pinned:
                    return self._parse_content_to_str(msg.content)
        return ""

    def ask_assistant(self, user_id: int, text: str) -> str:
        """
        Удобная обёртка: добавляет сообщение пользователя и сразу возвращает ответ ассистента.
        """
        self.add_user_message(user_id, text)
        return self.run_assistant(user_id)
