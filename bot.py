from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import Command
import asyncio
import aiohttp
import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID"))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

async def get_deepseek_response(prompt: str) -> str:
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    
    data = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "Ты дружелюбный ассистент Академика Fallout. Отвечай кратко на русском."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.7,
        "max_tokens": 512
    }
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers=headers,
                json=data
            ) as response:
                result = await response.json()
                
                # Проверяем, есть ли ошибка в ответе
                if response.status != 200:
                    return f"❌ Ошибка API: {result.get('error', {}).get('message', 'Неизвестная ошибка')}"
                
                # Проверяем, есть ли choices в ответе
                if 'choices' not in result or not result['choices']:
                    return f"❌ Нет ответа от модели. Ответ: {result}"
                
                # Возвращаем текст ответа
                return result['choices'][0]['message']['content']
                
        except Exception as e:
            return f"❌ Ошибка при запросе: {str(e)}"

@dp.message(Command("start"))
async def start_handler(message: Message):
    if message.from_user.id != ALLOWED_USER_ID:
        return
    await message.answer(
        "👋 Привет! Я — ИИ-ассистент Академика Fallout.\n\n"
        "https://t.me/levperegrev\n\n"
        "Задавай вопросы — отвечу через DeepSeek!"
    )

@dp.message()
async def ai_handler(message: Message):
    if message.from_user.id != ALLOWED_USER_ID:
        return
    try:
        await bot.send_chat_action(message.chat.id, "typing")
        response = await get_deepseek_response(message.text)
        await message.answer(response)
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")

async def main():
    print("✅ Bot started on DeepSeek only!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
