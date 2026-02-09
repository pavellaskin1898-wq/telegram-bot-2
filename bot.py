from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import Command
from yandex_cloud_ml_sdk import YCloudML
from openai import AsyncOpenAI
import asyncio
import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
YC_FOLDER_ID = os.getenv("YC_FOLDER_ID")
YC_API_KEY = os.getenv("YC_API_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID"))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Инициализация YandexGPT
sdk = YCloudML(folder_id=YC_FOLDER_ID, auth=YC_API_KEY)
yandex_model = sdk.models.completions('yandexgpt-lite')

# Инициализация DeepSeek
deepseek_client = AsyncOpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com"
)

current_model = "yandex"

class QuotaExceededException(Exception):
    pass

async def ask_yandex(prompt: str) -> str:
    try:
        result = await asyncio.to_thread(
            yandex_model.run,
            prompt,
            instruction="Ты дружелюбный ассистент Академика Fallout. Отвечай кратко на русском.",
            temperature=0.7,
            max_tokens=512
        )
        return result.text
    except Exception as e:
        err_str = str(e).lower()
        if "quota" in err_str or "limit" in err_str or "exceeded" in err_str:
            raise QuotaExceededException("Квота исчерпана")
        raise

async def ask_deepseek(prompt: str) -> str:
    try:
        response = await deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "Ты дружелюбный ассистент Академика Fallout. Отвечай кратко на русском."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=512
        )
        return response.choices[0].message.content
    except Exception as e:
        raise

async def ask_ai(prompt: str) -> tuple[str, str]:
    global current_model
    if current_model == "yandex":
        try:
            response = await ask_yandex(prompt)
            return response, "YandexGPT"
        except QuotaExceededException:
            print("YandexGPT quota exceeded. Switching to DeepSeek...")
            current_model = "deepseek"
        except Exception as e:
            print(f"YandexGPT error: {e}. Switching to DeepSeek...")
            current_model = "deepseek"
    response = await ask_deepseek(prompt)
    return response, "DeepSeek"

@dp.message(Command("start"))
async def start_handler(message: Message):
    if message.from_user.id != ALLOWED_USER_ID:
        return
    await message.answer(
        "👋 Привет! Добро пожаловать в Академик Fallout\n\n"
        "https://t.me/levperegrev\n\n"
        "🤖 Я — ваш ИИ-ассистент.\n"
        "Напишите что-нибудь!"
    )

@dp.message(Command("status"))
async def status_handler(message: Message):
    if message.from_user.id != ALLOWED_USER_ID:
        return
    status = "🟢 YandexGPT" if current_model == "yandex" else "🟢 DeepSeek"
    await message.answer(f"Текущая модель: {status}")

@dp.message()
async def ai_handler(message: Message):
    if message.from_user.id != ALLOWED_USER_ID:
        return
    try:
        await bot.send_chat_action(message.chat.id, "typing")
        response, model_used = await ask_ai(message.text)
        await message.answer(f"{response}\n\n🧠 {model_used}")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")

async def main():
    print("✅ Bot started!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())