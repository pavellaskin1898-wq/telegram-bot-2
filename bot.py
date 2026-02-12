from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import Command
import asyncio
import aiohttp
import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
YC_API_KEY = os.getenv("YC_API_KEY")
YC_FOLDER_ID = os.getenv("YC_FOLDER_ID")
ALLOWED_USER_ID = os.getenv("ALLOWED_USER_ID")  # Пока не преобразуем в int, чтобы не было ошибки

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Показываем ВСЕ переменные окружения
print("=== ENVIRONMENT VARIABLES ===")
for key, value in os.environ.items():
    if 'KEY' in key or 'TOKEN' in key or 'ID' in key or 'USER' in key:
        print(f"{key} = {value[:10]}..." if len(value) > 10 else f"{key} = {value}")

print(f"YC_API_KEY = {YC_API_KEY}")
print(f"YC_FOLDER_ID = {YC_FOLDER_ID}")
print(f"BOT_TOKEN = {BOT_TOKEN}")
print(f"ALLOWED_USER_ID = {ALLOWED_USER_ID}")
print("============================")

async def get_yandex_response(prompt: str) -> str:
    if not YC_API_KEY:
        return "❌ ОШИБКА: YC_API_KEY не найден в переменных окружения"
    
    if not YC_FOLDER_ID:
        return "❌ ОШИБКА: YC_FOLDER_ID не найден в переменных окружения"
    
    headers = {
        "Authorization": f"Api-Key {YC_API_KEY}",
        "Content-Type": "application/json"
    }
    
    data = {
        "modelUri": f"gpt://{YC_FOLDER_ID}/yandexgpt/rc",
        "completionOptions": {"temperature": 0.7, "maxTokens": "512"},
        "messages": [
            {"role": "system", "text": "Ты дружелюбный ассистент Академика Fallout. Отвечай кратко на русском."},
            {"role": "user", "text": prompt}
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
                
                print(f"Status: {response.status}")
                print(f"Response: {result}")
                
                if response.status != 200:
                    return f"❌ Ошибка {response.status}: {result.get('error', {}).get('message', 'Неизвестная ошибка')}"
                
                if 'result' not in result or not result['result'].get('alternatives'):
                    return f"❌ Нет ответа. Ответ: {result}"
                
                return result['result']['alternatives'][0]['message']['text']
                
        except Exception as e:
            return f"❌ Ошибка: {str(e)}"

@dp.message(Command("start"))
async def start_handler(message: Message):
    # Проверяем ID пользователя
    user_id = message.from_user.id
    allowed_id = int(ALLOWED_USER_ID) if ALLOWED_USER_ID else None
    
    print(f"User ID: {user_id}, Allowed ID: {allowed_id}")
    
    if allowed_id != user_id:
        return
    await message.answer(
        "👋 Привет! Я — ИИ-ассистент Академика Fallout.\n\n"
        "https://t.me/levperegrev\n\n"
        "Задавай вопросы — отвечу через YandexGPT!"
    )

@dp.message()
async def ai_handler(message: Message):
    user_id = message.from_user.id
    allowed_id = int(ALLOWED_USER_ID) if ALLOWED_USER_ID else None
    
    if allowed_id != user_id:
        return
        
    try:
        await bot.send_chat_action(message.chat.id, "typing")
        response = await get_yandex_response(message.text)
        await message.answer(response)
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")

async def main():
    print("🚀 Bot started with debug info")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
