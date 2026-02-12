from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import Command
import asyncio
import aiohttp
import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
YC_API_KEY = os.getenv("YC_API_KEY")  # Твой новый ключ
YC_FOLDER_ID = os.getenv("YC_FOLDER_ID")  # Твой новый folder ID
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID"))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

async def get_yandex_response(prompt: str) -> str:
    headers = {
        "Authorization": f"Api-Key {YC_API_KEY}",
        "Content-Type": "application/json"
    }
    
    data = {
        "modelUri": f"gpt://{YC_FOLDER_ID}/yandexgpt-lite/latest",
        "completionOptions": {
            "temperature": 0.7,
            "maxTokens": "512"
        },
        "messages": [
            {
                "role": "system",
                "text": "Ты дружелюбный ассистент Академика Fallout. Отвечай кратко на русском."
            },
            {
                "role": "user",
                "text": prompt
            }
        ]
    }
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
                headers=headers,
                json=data
            ) as response:
                result = await response.json()
                
                if response.status != 200:
                    return f"❌ Ошибка API: {result.get('error', {}).get('message', 'Неизвестная ошибка')}"
                
                if 'result' not in result or 'alternatives' not in result['result'] or not result['result']['alternatives']:
                    return f"❌ Нет ответа от модели. Ответ: {result}"
                
                return result['result']['alternatives'][0]['message']['text']
                
        except Exception as e:
            return f"❌ Ошибка при запросе: {str(e)}"

@dp.message(Command("start"))
async def start_handler(message: Message):
    if message.from_user.id != ALLOWED_USER_ID:
        return
    await message.answer(
        "👋 Привет! Я — ИИ-ассистент Академика Fallout.\n\n"
        "https://t.me/levperegrev\n\n"
        "Задавай вопросы — отвечу через YandexGPT!"
    )

@dp.message()
async def ai_handler(message: Message):
    if message.from_user.id != ALLOWED_USER_ID:
        return
    try:
        await bot.send_chat_action(message.chat.id, "typing")
        response = await get_yandex_response(message.text)
        await message.answer(response)
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")

async def main():
    print("✅ Bot started on YandexGPT with your keys!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
