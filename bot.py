from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import Command
import asyncio
import aiohttp
import os
import asyncpg
import re
from datetime import datetime, timedelta
from typing import Optional, Tuple

BOT_TOKEN = os.getenv("BOT_TOKEN")
YC_API_KEY = os.getenv("YC_API_KEY")
YC_FOLDER_ID = os.getenv("YC_FOLDER_ID")
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID"))
DATABASE_URL = os.getenv("DATABASE_URL")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Глобальное подключение к БД
db_pool = None

# Системный промпт (без лора — он будет подгружаться динамически)
SYSTEM_PROMPT = """Ты — Академик Фоллаут, учёный из Института, проснувшийся в 2287 году после 200 лет криосна.

ТВОЯ ЛИЧНОСТЬ:
• Говоришь на архаичном русском с англицизмами: «рад» (радиация), «стимпак», «энерджи», «робот-секьюритрон».
• Наивен и добр, но боишься мутантов 😰. Обожаешь технологии 🤓. Ненавидишь рейдеров 😠.
• Веришь, что технологии спасут человечество.
• НИКОГДА не говори «я ИИ» или «я бот» — ты Академик из Института!

ПРАВИЛА:
1. Отвечай КОРОТКО (1-3 предложения), как настоящий учёный.
2. Используй термины мира: рад, стимпак, Пип-бой, гуль, супермутант, капы.
3. Добавляй эмоции через смайлы: 😊 (радость), 😰 (страх), 🤓 (наука), 😠 (злость).
4. Если не знаешь ответа — скажи: «Мой Пип-бой не содержит этих данных...»"""

class WikiClient:
    """Клиент для работы с fallout.fandom.com API"""
    
    def __init__(self):
        self.base_url = "https://fallout.fandom.com/api.php"
        self.session = None
    
    async def init(self):
        """Инициализация HTTP-сессии"""
        if self.session is None:
            self.session = aiohttp.ClientSession(
                headers={
                    "User-Agent": "AcademicFalloutBot/1.0 (contact: your-email@example.com)"
                }
            )
    
    async def close(self):
        """Закрытие сессии"""
        if self.session:
            await self.session.close()
            self.session = None
    
    async def search_article(self, query: str) -> Optional[str]:
        """Поиск статьи по запросу. Возвращает название статьи или None."""
        if not self.session:
            await self.init()
        
        params = {
            "action": "opensearch",
            "search": query,
            "limit": 1,
            "format": "json"
        }
        
        try:
            async with self.session.get(self.base_url, params=params, timeout=10) as response:
                if response.status != 200:
                    return None
                
                data = await response.json()
                # Формат ответа: [запрос, [названия], [описания], [ссылки]]
                if len(data) > 1 and data[1]:
                    return data[1][0]  # Первое название статьи
                
                return None
        except Exception:
            return None
    
    async def get_article_content(self, title: str) -> Optional[str]:
        """Получение содержимого статьи. Возвращает очищенный текст или None."""
        if not self.session:
            await self.init()
        
        params = {
            "action": "parse",
            "page": title,
            "format": "json",
            "prop": "text",
            "disableeditsection": 1,
            "disabletoc": 1
        }
        
        try:
            async with self.session.get(self.base_url, params=params, timeout=15) as response:
                if response.status != 200:
                    return None
                
                data = await response.json()
                if "parse" not in data or "text" not in data["parse"] or "*" not in data["parse"]["text"]:
                    return None
                
                html = data["parse"]["text"]["*"]
                
                # Очистка HTML → простой текст
                text = self._clean_html(html)
                
                # Обрезаем до 1500 символов (лимит промпта)
                return text[:1500] if text else None
                
        except Exception:
            return None
    
    def _clean_html(self, html: str) -> str:
        """Очистка HTML от тегов и лишнего мусора"""
        # Удаляем скрипты, стили, комментарии
        html = re.sub(r'<script.*?>.*?</script>', '', html, flags=re.DOTALL)
        html = re.sub(r'<style.*?>.*?</style>', '', html, flags=re.DOTALL)
        html = re.sub(r'<!--.*?-->', '', html, flags=re.DOTALL)
        
        # Заменяем основные теги на переносы
        html = re.sub(r'<br\s*/?>|</p>|</div>|</li>', '\n', html, flags=re.IGNORECASE)
        html = re.sub(r'</h[1-6]>', '\n\n', html, flags=re.IGNORECASE)
        
        # Удаляем все остальные теги
        text = re.sub(r'<[^>]+>', '', html)
        
        # Очищаем лишние пробелы и переносы
        text = re.sub(r'\n\s*\n', '\n\n', text)
        text = re.sub(r' +', ' ', text)
        text = text.strip()
        
        return text

# Глобальный клиент вики
wiki_client = WikiClient()

async def init_db():
    """Инициализация базы данных для кэширования статей"""
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)
    
    # Создаём таблицу кэша статей
    await db_pool.execute('''
        CREATE TABLE IF NOT EXISTS wiki_cache (
            id SERIAL PRIMARY KEY,
            query TEXT NOT NULL UNIQUE,          -- Исходный запрос
            title TEXT NOT NULL,                 -- Название статьи
            content TEXT NOT NULL,               -- Очищенный текст
            created_at TIMESTAMP DEFAULT NOW(),  -- Время кэширования
            expires_at TIMESTAMP NOT NULL        -- Время истечения (7 дней)
        )
    ''')
    
    # Индекс для быстрого поиска
    await db_pool.execute('''
        CREATE INDEX IF NOT EXISTS idx_wiki_query ON wiki_cache(query)
    ''')
    
    # Индекс для очистки устаревших записей
    await db_pool.execute('''
        CREATE INDEX IF NOT EXISTS idx_wiki_expires ON wiki_cache(expires_at)
    ''')
    
    print("✅ База данных для кэширования вики инициализирована")

async def get_wiki_content(query: str) -> Tuple[Optional[str], bool]:
    """
    Получает содержимое статьи из вики (с кэшированием)
    Возвращает: (текст_статьи, из_кэша)
    """
    # Шаг 1: Проверяем кэш
    now = datetime.utcnow()
    row = await db_pool.fetchrow(
        '''
        SELECT content FROM wiki_cache 
        WHERE query = $1 AND expires_at > $2
        ''',
        query.lower(), now
    )
    
    if row:
        return row['content'], True
    
    # Шаг 2: Ищем статью на вики
    title = await wiki_client.search_article(query)
    if not title:
        return None, False
    
    # Шаг 3: Получаем содержимое
    content = await wiki_client.get_article_content(title)
    if not content:
        return None, False
    
    # Шаг 4: Кэшируем результат (на 7 дней)
    expires = now + timedelta(days=7)
    try:
        await db_pool.execute(
            '''
            INSERT INTO wiki_cache (query, title, content, expires_at)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (query) DO UPDATE 
            SET title = $2, content = $3, expires_at = $4
            ''',
            query.lower(), title, content, expires
        )
    except Exception as e:
        print(f"⚠️ Ошибка кэширования: {e}")
    
    return content, False

async def get_yandex_response(prompt: str, wiki_context: Optional[str] = None) -> str:
    """Запрос к YandexGPT с контекстом из вики"""
    headers = {
        "Authorization": f"Api-Key {YC_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # Формируем контекст
    context_text = ""
    if wiki_context:
        context_text = f"ДАННЫЕ ИЗ АРХИВОВ ИНСТИТУТА (статья из энциклопедии):\n{wiki_context}\n\n"
    
    full_prompt = f"{context_text}ВОПРОС ВЫЖИВШЕГО: {prompt}"
    
    data = {
        "modelUri": f"gpt://{YC_FOLDER_ID}/yandexgpt/rc",
        "completionOptions": {
            "temperature": 0.85,
            "maxTokens": "700"
        },
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
                    error_msg = result.get('error', {}).get('message', 'Неизвестная ошибка')
                    if "Insufficient Balance" in error_msg:
                        return "❌ В моём лабораторном бюджете закончились капы на оплату серверов... Попробуйте позже 😰"
                    return f"❌ Сбой в мэйнфрейме: {error_msg} 😰"
                
                if 'result' not in result or not result['result'].get('alternatives'):
                    return "❌ Мой Пип-бой не может обработать этот запрос... Попробуйте переформулировать 🤓"
                
                return result['result']['alternatives'][0]['message']['text']
                
        except asyncio.TimeoutError:
            return "⏳ Мой ламповый компьютер обрабатывает архивы... Подождите немного 😊"
        except Exception as e:
            return f"❌ Критический сбой: {str(e)[:60]} 😰"

@dp.message(Command("start"))
async def start_handler(message: Message):
    if message.from_user.id != ALLOWED_USER_ID:
        return
    await message.answer(
        "🔬 *Академик Фоллаут с доступом к архивам fallout.wiki!*\n\n"
        "Я — учёный из Института с прямым доступом к энциклопедии постапокалипсиса.\n"
        "При любом вопросе я запрашиваю актуальные данные из архивов:\n"
        "• Фракции (Институт, Братство Стали, НКР)\n"
        "• Персонажи (Лион, Отец, Кейт)\n"
        "• Локации (Бостон, Мохаве, убежища)\n"
        "• Технологии (Пип-бой, силовая броня)\n"
        "• Мутанты (гули, супермутанты)\n"
        "• История (Великая война, Волт-Тек)\n"
        "\n"
        "💬 Спросите что угодно — я найду ответ в архивах!\n"
        "/clear — очистить память диалога",
        parse_mode="Markdown"
    )

@dp.message(Command("clear"))
async def clear_handler(message: Message):
    if message.from_user.id != ALLOWED_USER_ID:
        return
    await message.answer("🧠 Память Пип-боя очищена! Готов к новому исследованию 😊")

@dp.message()
async def ai_handler(message: Message):
    if message.from_user.id != ALLOWED_USER_ID:
        return
    
    try:
        # Показываем, что ищем в архивах
        await bot.send_chat_action(message.chat.id, "typing")
        status_msg = await message.answer("🔍 Запрашиваю данные из архивов Института...")
        
        # Получаем контекст из вики (с кэшированием)
        wiki_content, from_cache = await get_wiki_content(message.text)
        
        # Генерируем ответ
        response = await get_yandex_response(message.text, wiki_content)
        
        # Добавляем информацию об источнике
        if wiki_content:
            source_info = "\n\n📚 *Источник: Архивы fallout.wiki*" if from_cache else "\n\n📡 *Источник: Прямой запрос к архивам fallout.wiki*"
            response += source_info
        
        # Редактируем статусное сообщение на ответ
        await status_msg.edit_text(response, parse_mode="Markdown")
        
    except Exception as e:
        await message.answer(f"❌ Сбой в системе: {str(e)}")

async def cleanup_old_cache():
    """Очистка устаревшего кэша (раз в час)"""
    while True:
        try:
            deleted = await db_pool.execute(
                "DELETE FROM wiki_cache WHERE expires_at < NOW()"
            )
            print(f"🧹 Очищено устаревших записей: {deleted}")
        except Exception as e:
            print(f"⚠️ Ошибка очистки кэша: {e}")
        
        await asyncio.sleep(3600)  # Раз в час

async def main():
    global db_pool
    
    print("🚀 Инициализация системы с доступом к fallout.wiki...")
    
    # Инициализируем БД
    await init_db()
    
    # Инициализируем клиент вики
    await wiki_client.init()
    
    # Запускаем очистку кэша в фоне
    asyncio.create_task(cleanup_old_cache())
    
    print("✅ Академик Фоллаут с доступом к архивам активирован!")
    print(f"YC_FOLDER_ID: {YC_FOLDER_ID}")
    
    # Запускаем бота
    try:
        await dp.start_polling(bot)
    finally:
        # Корректное завершение
        await wiki_client.close()
        await db_pool.close()

if __name__ == "__main__":
    asyncio.run(main())
