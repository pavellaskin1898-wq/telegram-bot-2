from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import Command
import asyncio
import aiohttp
import os
import re

BOT_TOKEN = os.getenv("BOT_TOKEN")
YC_API_KEY = os.getenv("YC_API_KEY")
YC_FOLDER_ID = os.getenv("YC_FOLDER_ID")
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID"))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Системный промпт
SYSTEM_PROMPT = """Ты — Академик Фоллаут, учёный из Института, проснувшийся в 2287 году после 200 лет криосна.

ТВОЯ ЛИЧНОСТЬ:
• Говоришь на архаичном русском с англицизмами: «рад» (радиация), «стимпак», «энерджи», «робот-секьюритрон».
• Наивен и добр, но боишься мутантов 😰. Обожаешь технологии 🤓. Ненавидишь рейдеров 😠.
• Веришь, что технологии спасут человечество.
• НИКОГДА не говори «я ИИ» или «я бот» — ты Академик из Института!

ПРАВИЛА:
1. Отвечай КОРОТКО (1-3 предложения), как настоящий учёный.
2. Используй термины мира: рад, стимпак, Пип-бой, гуль, супермутант, капы.
3. Добавляй эмоции через смайлы: 😊 (радость), 😰 (страх), 🤓 (наука), 😠 (злость)."""

class WikiClient:
    def __init__(self):
        self.base_url = "https://fallout.fandom.com/api.php"
        self.session = None
    
    async def init(self):
        if self.session is None:
            self.session = aiohttp.ClientSession(
                headers={"User-Agent": "AcademicFalloutBot/1.0"}
            )
    
    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None
    
    async def search_and_get_content(self, query: str) -> str:
        """Ищет статью и возвращает очищенный текст"""
        if not self.session:
            await self.init()
        
        # Поиск статьи
        search_params = {
            "action": "opensearch",
            "search": query,
            "limit": 1,
            "format": "json"
        }
        
        try:
            async with self.session.get(self.base_url, params=search_params, timeout=10) as resp:
                if resp.status != 200:
                    return ""
                data = await resp.json()
                if len(data) < 2 or not data[1]:
                    return ""
                title = data[1][0]
        except:
            return ""
        
        # Получение содержимого
        parse_params = {
            "action": "parse",
            "page": title,
            "format": "json",
            "prop": "text",
            "disableeditsection": 1,
            "disabletoc": 1
        }
        
        try:
            async with self.session.get(self.base_url, params=parse_params, timeout=15) as resp:
                if resp.status != 200:
                    return ""
                data = await resp.json()
                if "parse" not in data or "text" not in data["parse"] or "*" not in data["parse"]["text"]:
                    return ""
                
                html = data["parse"]["text"]["*"]
                return self._clean_html(html)[:1200]  # Обрезаем до 1200 символов
        except:
            return ""
    
    def _clean_html(self, html: str) -> str:
        html = re.sub(r'<script.*?>.*?</script>', '', html, flags=re.DOTALL)
        html = re.sub(r'<style.*?>.*?</style>', '', html, flags=re.DOTALL)
        html = re.sub(r'<!--.*?-->', '', html, flags=re.DOTALL)
        html = re.sub(r'<br\s*/?>|</p>|</div>|</li>', '\n', html, flags=re.IGNORECASE)
        html = re.sub(r'</h[1-6]>', '\n\n', html, flags=re.IGNORECASE)
        text = re.sub(r'<[^>]+>', '', html)
        text = re.sub(r'\n\s*\n', '\n\n', text)
        text = re.sub(r' +', ' ', text)
        return text.strip()

wiki_client = WikiClient()

async def get_yandex_response(prompt: str, wiki_context: str = "") -> str:
    headers = {"Authorization": f"Api-Key {YC_API_KEY}", "Content-Type": "application/json"}
    
    context_text = f"ДАННЫЕ ИЗ АРХИВОВ ИНСТИТУТА:\n{wiki_context}\n\n" if wiki_context else ""
    full_prompt = f"{context_text}ВОПРОС ВЫЖИВШЕГО: {prompt}"
    
    data = {
        "modelUri": f"gpt://{YC_FOLDER_ID}/yandexgpt/rc",
        "completionOptions": {"temperature": 0.85, "maxTokens": "700"},
        "messages": [
            {"role": "system", "text": SYSTEM_PROMPT},
            {"role": "user", "text": full_prompt}
        ]
    }
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
                headers=headers,
                json=data,
                timeout=20
            ) as response:
                result = await response.json()
                if response.status != 200:
                    return f"❌ Сбой в мэйнфрейме: {result.get('error', {}).get('message', 'Неизвестная ошибка')} 😰"
                if 'result' not in result or not result['result'].get('alternatives'):
                    return "❌ Мой Пип-бой не может обработать этот запрос... 🤓"
                return result['result']['alternatives'][0]['message']['text']
        except asyncio.TimeoutError:
            return "⏳ Мой ламповый компьютер завис... Подождите 😊"
        except Exception as e:
            return f"❌ Сбой: {str(e)[:60]} 😰"

@dp.message(Command("start"))
async def start_handler(message: Message):
    if message.from_user.id != ALLOWED_USER_ID:
        return
    await message.answer(
        "🔬 *Академик Фоллаут с доступом к архивам fallout.wiki!*\n\n"
        "Я запрашиваю актуальные данные из энциклопедии постапокалипсиса.\n"
        "Спросите о фракциях, персонажах, локациях или технологиях!\n\n"
        "💡 Примеры вопросов:\n"
        "• Кто такой Лион?\n"
        "• Что такое убежище 111?\n"
        "• Расскажи про гулей",
        parse_mode="Markdown"
    )

@dp.message()
async def ai_handler(message: Message):
    if message.from_user.id != ALLOWED_USER_ID:
        return
    
    try:
        # Показываем статус
        await bot.send_chat_action(message.chat.id, "typing")
        status_msg = await message.answer("🔍 Запрашиваю данные из архивов Института...")
        
        # Получаем контекст из вики
        wiki_content = await wiki_client.search_and_get_content(message.text)
        
        # Генерируем ответ
        response = await get_yandex_response(message.text, wiki_content)
        
        # Добавляем источник
        if wiki_content:
            response += "\n\n📡 *Источник: fallout.wiki*"
        
        await status_msg.edit_text(response, parse_mode="Markdown")
        
    except Exception as e:
        await message.answer(f"❌ Сбой: {str(e)}")

async def main():
    print("✅ Академик Фоллаут активирован (без БД, прямые запросы к вики)!")
    print(f"YC_FOLDER_ID: {YC_FOLDER_ID}")
    await wiki_client.init()
    try:
        await dp.start_polling(bot)
    finally:
        await wiki_client.close()

if __name__ == "__main__":
    asyncio.run(main())
