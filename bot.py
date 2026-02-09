from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import Command
from openai import AsyncOpenAI
import asyncio
import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID"))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

client = AsyncOpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com/v1"
)

@dp.message(Command("start")
async def start_handler(message: Message):
    if message.from_user.id != ALLOWED_USER_ID:
        return
    await message.answer(
        "?? Привет! Я - ИИ-ассистент Академика Fallout.\n\n"
        "https://t.me/levperegrev\n\n"
        "Задавай вопросы - отвечу через DeepSeek!"
    )

@dp.message()
async def ai_handler(message: Message):
    if message.from_user.id != ALLOWED_USER_ID:
        return
    try:
        await bot.send_chat_action(message.chat.id, "typing")
        response = await client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "Ты дружелюбный ассистент Академика Fallout. Отвечай кратко на русском."},
                {"role": "user", "content": message.text}
            ],
            temperature=0.7,
            max_tokens=512
        )
        await message.answer(response.choices[0].message.content)
    except Exception as e:
        await message.answer(f"? Ошибка: {str(e)}")

async def main():
    print("? Bot started on DeepSeek!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main()
