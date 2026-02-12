import os
import requests
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
import asyncio

BOT_TOKEN = os.getenv("BOT_TOKEN")
YANDEX_API_KEY = os.getenv("YANDEX_API_KEY")
YANDEX_FOLDER_ID = os.getenv("YANDEX_FOLDER_ID")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

def get_ai_response(user_message: str) -> str:
    url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
    headers = {
        "Authorization": f"Api-Key {YANDEX_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt-lite/latest",
        "completionOptions": {"stream": False, "temperature": 0.6, "maxTokens": 500},
        "messages": [
            {"role": "system", "text": "Ты полезный Telegram-бот. Отвечай кратко и понятно."},
            {"role": "user", "text": user_message}
        ]
    }
    response = requests.post(url, headers=headers, json=data)
    if response.status_code != 200:
        return f"Ошибка API: {response.text}"
    result = response.json()
    return result["result"]["alternatives"][0]["message"]["text"]

@dp.message(CommandStart())
async def start_handler(message: types.Message):
    await message.answer("Привет! Напиши мне что-нибудь 😊")

@dp.message()
async def chat_handler(message: types.Message):
    await message.answer("Думаю...")
    ai_reply = get_ai_response(message.text)
    await message.answer(ai_reply)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
