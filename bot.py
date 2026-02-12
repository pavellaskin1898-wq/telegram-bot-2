import os
import asyncio
import requests
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiogram.enums import ParseMode

# === Переменные окружения ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
YANDEX_API_KEY = os.getenv("YANDEX_API_KEY")
YANDEX_FOLDER_ID = os.getenv("YANDEX_FOLDER_ID")

bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()


# === Функция запроса к YandexGPT ===
def get_ai_response(user_message: str) -> str:
    url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

    headers = {
        "Authorization": f"Api-Key {YANDEX_API_KEY}",
        "Content-Type": "application/json"
    }

    data = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt-lite/latest",
        "completionOptions": {
            "stream": False,
            "temperature": 0.6,
            "maxTokens": 500
        },
        "messages": [
            {
                "role": "system",
                "text": "Ты умный Telegram-бот. Отвечай кратко и понятно."
            },
            {
                "role": "user",
                "text": user_message
            }
        ]
    }

    response = requests.post(url, headers=headers, json=data)

    if response.status_code != 200:
        return f"Ошибка API:\n{response.text}"

    result = response.json()
    return result["result"]["alternatives"][0]["message"]["text"]


# === Команда /start ===
@dp.message(CommandStart())
async def start_handler(message: Message):
    await message.answer("Привет! Напиши мне что-нибудь 😊")


# === Обработка обычных сообщений ===
@dp.message()
async def chat_handler(message: Message):
    await message.answer("Думаю...")

    ai_reply = get_ai_response(message.text)

    await message.answer(ai_reply)


# === Запуск ===
async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
