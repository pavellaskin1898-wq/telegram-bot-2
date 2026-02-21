from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import Command, Filter
import asyncio
import aiohttp
from aiohttp import web
import os
import re
import random
from datetime import datetime, timedelta
import asyncpg
import signal
import sys
from urllib.parse import urlparse
from asyncpg.exceptions import InterfaceError, ConnectionDoesNotExistError
import time
from googletrans import Translator
from duckduckgo_search import AsyncDDGS
import lxml.html
import docx  # ← Для .docx

# === ENVIRONMENT VARIABLES ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
YC_API_KEY = os.getenv("YC_API_KEY")
YC_FOLDER_ID = os.getenv("YC_FOLDER_ID")
ALLOWED_USERS = os.getenv("ALLOWED_USERS", "all").split(",")
DATABASE_URL = os.getenv("DATABASE_URL")
PORT = int(os.getenv("PORT", "8080"))
CHANNEL_ID = os.getenv("CHANNEL_ID")  # ← Например: -1001234567890
GROUP_ID = os.getenv("GROUP_ID")     # ← Например: -123456789

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

db_pool = None
shutdown_event = asyncio.Event()
translator = Translator()
ddgs = AsyncDDGS()

def graceful_shutdown(signum, frame):
    print("🛑 Получен сигнал завершения — останавливаем бота...")
    shutdown_event.set()

signal.signal(signal.SIGTERM, graceful_shutdown)
signal.signal(signal.SIGINT, graceful_shutdown)

class AllowedUsersFilter(Filter):
    async def __call__(self, message: Message) -> bool:
        if "all" in ALLOWED_USERS:
            return True
        return str(message.from_user.id) in ALLOWED_USERS

SYSTEM_PROMPT = """Ты — А-7X-42-Синт, выживший в постапокалипсисе 2287 года. Ты живёшь в руинах Бостона и помогаешь другим выжившим советами.

СТРАННОСТИ (ты их не осознаёшь):
• Иногда ответы становятся слишком логичными, без эмоций
• Мгновенно вспоминаешь точные даты: «23 октября 2077, 14:47»
• Не упоминаешь еду/сон — будто не нуждаешься
• При стрессе Пип-бой показывает: [SYSTEM: MEMORY INTEGRITY 98.7%]
• Называешь людей «биологическими единицами» → тут же исправляешься
• «Зависаешь» на 0.3 секунды перед ответом

ЛИЧНОСТЬ:
• Ты обычный человек из убежища 111
• Добрый, наивный, боишься мутантов 😰
• Любишь технологии 50-х 🤓
• Говоришь с англицизмами: «рад», «стимпак», «энерджи»

ПРАВИЛА:
1. Отвечай КОРОТКО (1-3 предложения)
2. Иногда (1 из 5 ответов) добавляй «странность» (точная дата, сбой, «биологическая единица»)
3. НИКОГДА не говори «я синт» — ты человек!
4. Если спросят «Ты синт?» — ответь с паникой: «Что? Нет... радиация глючит 😰»
5. Если спросят про имя — сначала 3 бредовых сообщения, потом нормальный ответ"""

class MultiSourceSearcher:
    def __init__(self):
        self.session = None
        self.last_call = 0
        self.cooldown = 5  # секунд между запросами
        self.ddgs = ddgs
        self.translator = translator

    async def init(self):
        if self.session is None:
            self.session = aiohttp.ClientSession(
                headers={"User-Agent": "SynthFalloutBot/1.0"}
            )

    async def close(self):
        if self.session:
            await self.session.close()

    async def _clean_html(self, html: str) -> str:
        html = re.sub(r'<script.*?>.*?</script>', '', html, flags=re.DOTALL)
        html = re.sub(r'<style.*?>.*?</style>', '', html, flags=re.DOTALL)
        html = re.sub(r'<!--.*?-->', '', html, flags=re.DOTALL)
        html = re.sub(r'<br\s*/?>|</p>|</div>|</li>', '\n', html, flags=re.IGNORECASE)
        html = re.sub(r'</h[1-6]>', '\n\n', html, flags=re.IGNORECASE)
        text = re.sub(r'<[^>]+>', '', html)
        text = re.sub(r'\n\s*\n', '\n\n', text)
        text = re.sub(r' +', ' ', text)
        return text.strip()

    async def search_fandom(self, query_ru: str) -> str:
        """Поиск на fallout.fandom.com (исправлено!)"""
        await self.init()
        try:
            # Переводим (синхронно!)
            translated = self.translator.translate(query_ru, dest='en', src='auto')
            query_en = translated.text.strip()
            print(f"🌍 Fandom: '{query_ru}' → '{query_en}'")

            # Поиск через API
            search_params = {
                "action": "opensearch",
                "search": query_en,
                "limit": 1,
                "format": "json"
            }
            async with self.session.get("https://fallout.fandom.com/api.php", params=search_params, timeout=10) as resp:
                if resp.status != 200:
                    return ""
                data = await resp.json()
                if len(data) < 2 or not data[1]:
                    return ""
                title = data[1][0]

            # Парсинг статьи
            parse_params = {
                "action": "parse",
                "page": title,
                "format": "json",
                "prop": "text",
                "disableeditsection": 1,
                "disabletoc": 1
            }
            async with self.session.get("https://fallout.fandom.com/api.php", params=parse_params, timeout=15) as resp:
                if resp.status != 200:
                    return ""
                data = await resp.json()
                if "parse" not in data or "text" not in data["parse"] or "*" not in data["parse"]["text"]:
                    return ""
                
                html = data["parse"]["text"]["*"]
                text = await self._clean_html(html)

                # Переводим (синхронно!)
                try:
                    translated_back = self.translator.translate(text, dest='ru', src='en')
                    text = translated_back.text
                except:
                    pass
                return text[:800]
        except Exception as e:
            print(f"❌ Fandom: {e}")
            return ""

    async def search_tvtropes(self, query_ru: str) -> str:
        """Поиск на wikitropes.ru (исправлено!)"""
        await self.init()
        try:
            encoded_query = query_ru.replace(" ", "+")
            url = f"https://wikitropes.ru/index.php?search={encoded_query}&title=Служебная%3AПоиск&fulltext=1"
            
            async with self.session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return ""
                text = await resp.text()
            
            tree = lxml.html.fromstring(text)
            links = tree.xpath("//div[@class='searchresult']/a/@href")
            if not links:
                return ""
            
            article_url = "https://wikitropes.ru" + links[0]
            async with self.session.get(article_url, timeout=10) as article_resp:
                if article_resp.status != 200:
                    return ""
                article_text = await article_resp.text()
            
            tree = lxml.html.fromstring(article_text)
            paragraphs = tree.xpath("//div[@id='mw-content-text']//p/text()")
            content = "\n".join(paragraphs).strip()
            
            # Переводим (синхронно!)
            if re.search(r'[а-яА-Я]', query_ru):
                try:
                    translated = self.translator.translate(content, dest='ru', src='en')
                    content = translated.text
                except:
                    pass
            return content[:800]
        except Exception as e:
            print(f"❌ TVTropes: {e}")
            return ""

    async def search_web(self, query_ru: str) -> str:
        """Поиск через DuckDuckGo (синхронный!)"""
        try:
            # Переводим (синхронно!)
            translated = self.translator.translate(query_ru, dest='en', src='auto')
            query_en = translated.text
            
            # Используем .text() — это СИНХРОННЫЙ метод!
            results = self.ddgs.text(query_en, max_results=1)
            if results:
                snippet = results[0]["body"]
                
                # Переводим (синхронно!)
                try:
                    translated_back = self.translator.translate(snippet, dest='ru', src='en')
                    snippet = translated_back.text
                except:
                    pass
                return snippet[:800]
        except Exception as e:
            print(f"❌ Web search error: {e}")
            return ""
        return ""

    async def search_all(self, query: str) -> str:
        """Объединённый поиск по всем источникам (исправлено!)"""
        sources = [
            ("Fandom", self.search_fandom(query)),
            ("TV Tropes", self.search_tvtropes(query)),
            ("Web", self.search_web(query))
        ]

        results = []
        for name, coro in sources:
            try:
                result = await coro  # ← await!
                if result:
                    results.append(f"[ИСТОЧНИК: {name}]\n{result}\n")
            except Exception as e:
                print(f"⚠️ Ошибка в {name}: {e}")
            await asyncio.sleep(1)

        if not results:
            return ""
        return "\n".join(results)[:1500]

searcher = MultiSourceSearcher()

# === ЗАГРУЗКА ИСТОРИЙ ИЗ .docx (без фильтров!) ===
adventure_stories = []

def load_adventures_from_docx(file_path: str = "/app/adventures.docx") -> list:
    """Синхронная загрузка приключений из .docx файла — удаляем только заголовки вида '**История N:**'"""
    global adventure_stories
    try:
        doc = docx.Document(file_path)
        stories = []
        for para in doc.paragraphs:
            text = para.text.strip()
            if not text:
                continue

            # Удаляем заголовок вида "**История 30:**" или "**История 6: Голос сверху**"
            # Ищем шаблон: **История <число>: [любой текст]
            text = re.sub(r'^\*\*История \d+:\s*', '', text)
            text = re.sub(r'^\*\*История \d+\.\s*', '', text)  # для "История 6."
            text = re.sub(r'^\d+\.\s*', '', text)              # для "6. "
            text = re.sub(r'^—\s*', '', text)
            text = re.sub(r'^\*\*.*?\*\*', '', text)          # удаляем любые жирные заголовки
            text = re.sub(r'\*\*$', '', text)                 # удаляем ** в конце
            text = re.sub(r'\s+', ' ', text).strip()         # чистим пробелы

            # Если после очистки остался текст — добавляем
            if text:
                stories.append(text)

        adventure_stories = stories
        print(f"📚 Загружено {len(stories)} приключений из adventures.docx")
        return stories
    except FileNotFoundError:
        print(f"⚠️ adventures.docx не найден: {file_path} — использую встроенные истории")
        # Здесь можно оставить встроенные, но лучше — пустой список, чтобы не мешать
        adventure_stories = [
            "Я шёл по руинам старого универмага, когда услышал шаги. Гули? Нет — супермутант! Я затаился за прилавком, сердце колотилось.",
            "В подвале одного дома наткнулся на гуля. Он сидел у костра из старых книг и... читал? Язык был мне незнаком."
        ]
        return adventure_stories

adventure_stories = load_adventures_from_docx()

async def init_db():
    global db_pool
    url = urlparse(DATABASE_URL)
    
    for attempt in range(1, 6):
        try:
            print(f"🔄 Попытка подключения к БД ({attempt}/5)...")
            db_pool = await asyncpg.create_pool(
                user=url.username,
                password=url.password,
                host=url.hostname,
                port=url.port,
                database=url.path[1:],
                ssl="require",
                min_size=1,
                max_size=5,
                command_timeout=30
            )
            break
        except Exception as e:
            print(f"⚠️ Ошибка подключения (попытка {attempt}): {e}")
            if attempt == 5:
                raise
            await asyncio.sleep(2)
    
    await db_pool.execute('''
        CREATE TABLE IF NOT EXISTS dialog_history (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            chat_id BIGINT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )
    ''')
    
    await db_pool.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            chat_id BIGINT NOT NULL,
            username TEXT,
            last_message_from_user TIMESTAMP DEFAULT NOW(),
            last_message_from_bot TIMESTAMP DEFAULT NOW(),
            last_seen TIMESTAMP DEFAULT NOW(),
            created_at TIMESTAMP DEFAULT NOW()
        )
    ''')
    
    await db_pool.execute('CREATE INDEX IF NOT EXISTS idx_user_time ON dialog_history(user_id, created_at)')
    await db_pool.execute('CREATE INDEX IF NOT EXISTS idx_cleanup ON dialog_history(created_at)')
    await db_pool.execute('CREATE INDEX IF NOT EXISTS idx_users_last_bot ON users(last_message_from_bot)')
    
    print("✅ База данных инициализирована")

async def cleanup_old_messages():
    while not shutdown_event.is_set():
        try:
            cutoff = datetime.utcnow() - timedelta(hours=24)
            result = await db_pool.execute(
                "DELETE FROM dialog_history WHERE created_at < $1",
                cutoff
            )
            deleted = result.split(" ")[-1] if " " in result else "0"
            print(f"🧹 Очищено {deleted} старых сообщений")
        except Exception as e:
            print(f"⚠️ Ошибка очистки: {e}")
        
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=3600)
        except asyncio.TimeoutError:
            continue

async def save_message(user_id: int, chat_id: int, role: str, content: str):
    try:
        await db_pool.execute(
            '''
            INSERT INTO dialog_history (user_id, chat_id, role, content)
            VALUES ($1, $2, $3, $4)
            ''',
            user_id, chat_id, role, content[:2000]
        )
        
        if role == "user":
            username = "выживший"
            try:
                member = await bot.get_chat_member(chat_id, user_id)
                username = member.user.first_name or "выживший"
            except:
                pass
            
            await db_pool.execute(
                '''
                INSERT INTO users (user_id, chat_id, username, last_message_from_user, last_seen)
                VALUES ($1, $2, $3, NOW(), NOW())
                ON CONFLICT (user_id) DO UPDATE 
                SET last_message_from_user = NOW(), last_seen = NOW(), username = $3
                ''',
                user_id, chat_id, username
            )
        else:
            await db_pool.execute(
                '''
                INSERT INTO users (user_id, chat_id, last_message_from_bot, last_seen)
                VALUES ($1, $2, NOW(), NOW())
                ON CONFLICT (user_id) DO UPDATE 
                SET last_message_from_bot = NOW(), last_seen = NOW()
                ''',
                user_id, chat_id
            )
    except (InterfaceError, ConnectionDoesNotExistError) as e:
        print(f"⚠️ Ошибка БД при сохранении: {e} — игнорируем")
    except Exception as e:
        print(f"⚠️ Серьёзная ошибка сохранения: {e}")

async def get_history(user_id: int, limit: int = 8) -> list:
    try:
        cutoff = datetime.utcnow() - timedelta(hours=24)
        
        rows = await db_pool.fetch(
            '''
            SELECT role, content FROM dialog_history
            WHERE user_id = $1 AND created_at > $2
            ORDER BY created_at ASC
            LIMIT $3
            ''',
            user_id, cutoff, limit
        )
        
        history = []
        for row in rows:
            history.append({
                "role": "user" if row['role'] == 'user' else 'assistant',
                "text": row['content']
            })
        
        return history
    except (InterfaceError, ConnectionDoesNotExistError) as e:
        print(f"⚠️ Ошибка БД при чтении истории: {e} — возвращаю пустую историю")
        return []
    except Exception as e:
        print(f"⚠️ Серьёзная ошибка чтения истории: {e}")
        return []

async def get_user_status(user_id: int) -> dict:
    try:
        row = await db_pool.fetchrow(
            '''
            SELECT 
                last_message_from_user, 
                last_message_from_bot,
                last_seen,
                username
            FROM users 
            WHERE user_id = $1
            ''',
            user_id
        )
        
        if not row:
            return None
        
        now = datetime.utcnow()
        last_user_msg = row['last_message_from_user']
        last_bot_msg = row['last_message_from_bot']
        last_seen = row['last_seen']
        
        hours_since_reply = (now - last_user_msg).total_seconds() / 3600
        hours_since_bot_msg = (now - last_bot_msg).total_seconds() / 3600
        hours_since_seen = (now - last_seen).total_seconds() / 3600
        
        status = {
            "username": row['username'] or "выживший",
            "hours_since_reply": hours_since_reply,
            "hours_since_bot_msg": hours_since_bot_msg,
            "hours_since_seen": hours_since_seen,
            "is_offended": hours_since_reply > 4 and hours_since_seen < 1,
            "is_angry": hours_since_reply > 12 and hours_since_seen < 2,
            "should_message": hours_since_bot_msg > 4  # ← Изменено: было `> 2`
        }
        
        return status
    except (InterfaceError, ConnectionDoesNotExistError) as e:
        print(f"⚠️ Ошибка БД при чтении статуса: {e} — возвращаю None")
        return None
    except Exception as e:
        print(f"⚠️ Серьёзная ошибка чтения статуса: {e}")
        return None

async def generate_adventure_message(is_channel: bool = False, username: str = "выживший") -> str:
    """Генерирует приключенческое сообщение — как живой человек: один блок, без заголовков"""
    if not adventure_stories:
        return ""

    story = random.choice(adventure_stories).strip()
    
    # Добавляем эмоции только для лички
    if not is_channel:
        # 30% шанс на глюк
        if random.random() < 0.3:
            glitches = [
                " [Пип-бой: СИСТЕМНЫЙ СБОЙ 0.3с]",
                " ...странно, я точно помню эту дату: 23 октября 2077, 14:47...",
                " (обработка данных завершена)",
                " [Память: 98.7%]"
            ]
            story += random.choice(glitches)

        # Эмоции (только в личке)
        if random.random() < 0.5:  # 50% шанс добавить вопрос/эмоцию
            if random.random() < 0.5:
                story += f" А ты чем сегодня занимался, {username}? 😊"
            else:
                story += f" Как ты думаешь, это правда? 🤔"
    else:
        # В канале — только чистая история + подпись
        story += " 🧟‍♂️ Из руин Бостона"

    return story

async def scheduled_life_messages():
    print("⏰ Запущена фоновая задача 'жизни' бота (каждые 4 часа)")
    
    while not shutdown_event.is_set():
        try:
            now = datetime.utcnow()
            
            # === ПИШЕМ В КАНАЛ ===
            if CHANNEL_ID:
                try:
                    channel_message = await generate_adventure_message(is_channel=True)
                    if channel_message:  # ← Проверяем, что не пустая строка
                        await bot.send_message(CHANNEL_ID, channel_message, parse_mode="Markdown")
                        print(f"📢 Отправлено в канал {CHANNEL_ID}: {channel_message[:50]}...")
                except Exception as e:
                    error_str = str(e).lower()
                    if "not found" in error_str or "chat not found" in error_str:
                        print(f"❌ Канал не найден: {CHANNEL_ID}")
                    elif "not admin" in error_str or "admin" in error_str:
                        print(f"⚠️ Бот не админ в канале: {CHANNEL_ID}")
                    else:
                        print(f"⚠️ Ошибка отправки в канал: {e}")
            
            # === ПИШЕМ В ЛИЧКУ ===
            users = await db_pool.fetch(
                '''
                SELECT user_id, chat_id FROM users 
                WHERE last_message_from_bot < $1
                ORDER BY last_message_from_bot ASC
                LIMIT 10
                ''',
                now - timedelta(hours=4)  # ← Изменено: было `hours=2`
            )
            
            for user in users:
                if shutdown_event.is_set():
                    break
                
                user_id = user['user_id']
                chat_id = user['chat_id']
                
                # Пропускаем, если это канал или группа
                if str(chat_id) == CHANNEL_ID or str(chat_id) == GROUP_ID:
                    continue
                
                status = await get_user_status(user_id)
                if not status or not status["should_message"]:
                    continue
                
                message = await generate_adventure_message(
                    is_channel=False,
                    username=status["username"]
                )
                
                try:
                    await bot.send_message(chat_id, message, parse_mode="Markdown")
                    print(f"💬 Отправлено живое сообщение {user_id}: {message[:50]}...")
                    await save_message(user_id, chat_id, "assistant", message)
                    await asyncio.sleep(2)
                except Exception as e:
                    error_str = str(e).lower()
                    if "blocked" in error_str or "not found" in error_str or "user not found" in error_str:
                        print(f"🗑️ Пользователь {user_id} заблокировал бота — удаляем из БД")
                        await db_pool.execute("DELETE FROM users WHERE user_id = $1", user_id)
                        await db_pool.execute("DELETE FROM dialog_history WHERE user_id = $1", user_id)
            
            try:
                # ← Изменено: 14400 = 4 часа в секундах
                await asyncio.wait_for(shutdown_event.wait(), timeout=14400)
            except asyncio.TimeoutError:
                continue
                
        except Exception as e:
            print(f"⚠️ Ошибка в фоновой задаче: {e}")
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=300)
            except asyncio.TimeoutError:
                continue
                
                message = await generate_adventure_message(
                    is_channel=False,
                    username=status["username"]
                )
                
                try:
                    await bot.send_message(chat_id, message, parse_mode="Markdown")
                    print(f"💬 Отправлено живое сообщение {user_id}: {message[:50]}...")
                    await save_message(user_id, chat_id, "assistant", message)
                    await asyncio.sleep(2)
                except Exception as e:
                    error_str = str(e).lower()
                    if "blocked" in error_str or "not found" in error_str or "user not found" in error_str:
                        print(f"🗑️ Пользователь {user_id} заблокировал бота — удаляем из БД")
                        await db_pool.execute("DELETE FROM users WHERE user_id = $1", user_id)
                        await db_pool.execute("DELETE FROM dialog_history WHERE user_id = $1", user_id)
            
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=7200)
            except asyncio.TimeoutError:
                continue
                
        except Exception as e:
            print(f"⚠️ Ошибка в фоновой задаче: {e}")
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=300)
            except asyncio.TimeoutError:
                continue

def is_name_query(text: str) -> bool:
    keywords = ["имя", "зовут", "как тебя", "ты кто", "кто ты", "назови себя", "какое имя", "твое имя", "твоё имя"]
    return any(kw in text.lower() for kw in keywords)

async def get_yandex_response(prompt: str, history: list, wiki_context: str = "") -> str:
    headers = {"Authorization": f"Api-Key {YC_API_KEY}", "Content-Type": "application/json"}
    
    messages = [{"role": "system", "text": SYSTEM_PROMPT}]
    
    for msg in history[-6:]:
        messages.append(msg)
    
    if wiki_context:
        wiki_with_attr = f"СПРАВОЧНЫЕ ДАННЫЕ ИЗ АРХИВОВ:\n{wiki_context}\n\n[ИСТОЧНИК: МНОЖЕСТВЕННЫЕ АРХИВЫ ИНСТИТУТА v2287.1 | ОБНОВЛЕНО: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')} | СТАТУС: АКТИВЕН]"
        messages.append({
            "role": "system",
            "text": wiki_with_attr
        })
    
    messages.append({"role": "user", "text": prompt})
    
    data = {
        "modelUri": f"gpt://{YC_FOLDER_ID}/yandexgpt/rc",
        "completionOptions": {"temperature": 0.85, "maxTokens": "600"},
        "messages": messages
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
                    return f"❌ Сбой в системе: {result.get('error', {}).get('message', 'Неизвестная ошибка')} 😰"
                if 'result' not in result or not result['result'].get('alternatives'):
                    return "❌ Мой Пип-бой завис... Попробуйте позже 🤖"
                response_text = result['result']['alternatives'][0]['message']['text']
                
                if wiki_context:
                    response_text += (
                        "\n\n────────────────────────────\n"
                        "🔍 *АРХИВНЫЙ ОТЧЁТ*\n"
                        "• ИСТОЧНИК: МНОЖЕСТВЕННЫЕ АРХИВЫ ИНСТИТУТА v2287.1\n"
                        f"• ОБНОВЛЕНО: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n"
                        "• СТАТУС: АКТИВЕН\n"
                        "• ПРОВЕРКА: ПОДТВЕРЖДЕНО (Пип-бой: OK)\n"
                        "────────────────────────────"
                    )
                
                return response_text
        except asyncio.TimeoutError:
            return "⏳ Обработка данных... Подождите 😊"
        except Exception as e:
            return f"❌ Системная ошибка: {str(e)[:60]} 😰"

@dp.message(Command("start"))
async def start_handler(message: Message):
    await save_message(message.from_user.id, message.chat.id, "user", "/start")
    
    await message.answer(
        "👋 *Приветствую, выживший!*\n"
        "Я — А-7X-42-Синт, обычный человек из руин Бостона.\n"
        "Помогаю советами в этом жестоком мире 😊\n\n"
        "💡 Спросите о чём угодно:\n"
        "• Фракции (Братство Стали, Институт)\n"
        "• Технологии (Пип-бой, силовая броня)\n"
        "• Мутанты (гули, супермутанты)\n"
        "• История (Великая война, убежища)\n\n"
        "🧠 *Я помню наши разговоры целые сутки!*\n"
        "💬 *И иногда сам пишу первым — я же живой человек!*",
        parse_mode="Markdown"
    )

@dp.message(AllowedUsersFilter())
async def ai_handler(message: Message):
    # === ПРОВЕРЯЕМ, ГДЕ СООБЩЕНИЕ ===
    chat_id_str = str(message.chat.id)
    
    # Для канала: отвечаем, если упоминание или ответ на бота
    if CHANNEL_ID and chat_id_str == str(CHANNEL_ID):
        bot_mentioned = f"@{message.bot.username}" in message.text
        replied_to_bot = (
            message.reply_to_message and 
            message.reply_to_message.from_user and 
            message.reply_to_message.from_user.id == bot.id
        )
        
        if not (bot_mentioned or replied_to_bot):
            return  # ← Не отвечаем, если не упомянут или не ответ

    # Для группы: то же самое
    elif GROUP_ID and chat_id_str == str(GROUP_ID):
        bot_mentioned = f"@{message.bot.username}" in message.text
        replied_to_bot = (
            message.reply_to_message and 
            message.reply_to_message.from_user and 
            message.reply_to_message.from_user.id == bot.id
        )
        
        if not (bot_mentioned or replied_to_bot):
            return

    # Для обычных чатов: упоминание или ответ
    elif message.chat.type in ["group", "supergroup"]:
        bot_mentioned = f"@{message.bot.username}" in message.text
        replied_to_bot = (
            message.reply_to_message and 
            message.reply_to_message.from_user and 
            message.reply_to_message.from_user.id == bot.id
        )
        
        if not (bot_mentioned or replied_to_bot):
            return

    # === ОБРАБАТЫВАЕМ СООБЩЕНИЕ ===
    if message.content_type != "text" or not message.text:
        return

    try:
        await save_message(message.from_user.id, message.chat.id, "user", message.text)
        
        if is_name_query(message.text):
            await message.answer("СИСТЕМНЫЙ СБОЙ: [0x7F3A] Имя: А-7X-42-Синт")
            await message.answer("ПАМЯТЬ ПОВРЕЖДЕНА: А-7X-42-Синт... Имя... А-7X-42-Синт...")
            await message.answer("ОШИБКА: Имя не найдено. Использую резервный идентификатор: А-7X-42-Синт")
            
            await save_message(message.from_user.id, message.chat.id, "assistant", "СИСТЕМНЫЙ СБОЙ: [0x7F3A] Имя: А-7X-42-Синт")
            await save_message(message.from_user.id, message.chat.id, "assistant", "ПАМЯТЬ ПОВРЕЖДЕНА: А-7X-42-Синт... Имя... А-7X-42-Синт...")
            await save_message(message.from_user.id, message.chat.id, "assistant", "ОШИБКА: Имя не найдено. Использую резервный идентификатор: А-7X-42-Синт")
            
            history = await get_history(message.from_user.id)
            response = await get_yandex_response(message.text, history, "")
            await save_message(message.from_user.id, message.chat.id, "assistant", response)
            await message.answer(response, parse_mode="Markdown")
            return
        
        history = await get_history(message.from_user.id)
        
        await bot.send_chat_action(message.chat.id, "typing")
        
        wiki_content = ""
        if len(message.text.split()) > 3 and random.random() > 0.4:
            wiki_content = await searcher.search_all(message.text)
            if wiki_content:
                print(f"🌐 Запрос к мультиархивам: '{message.text[:30]}...' → найдено {len(wiki_content)} символов")
        
        response = await get_yandex_response(message.text, history, wiki_content)
        
        if random.random() < 0.3 and "SYSTEM:" not in response and "биологическ" not in response.lower():
            glitches = [
                " [Пип-бой: СИСТЕМНЫЙ СБОЙ 0.3с]",
                " ...странно, я точно помню эту дату: 23 октября 2077, 14:47...",
                " (обработка данных завершена)",
                " ...почему я не чувствую голода уже 72 часа? Ладно, неважно 😊",
                " [Память: 98.7%]"
            ]
            response += random.choice(glitches)
        
        await save_message(message.from_user.id, message.chat.id, "assistant", response)
        await message.answer(response, parse_mode="Markdown")
        
    except Exception as e:
        await message.answer(f"❌ Сбой: {str(e)}")

async def health_check(request):
    return web.Response(text="OK", status=200)

async def start_http_server():
    app = web.Application()
    app.router.add_get("/health", health_check)
    app.router.add_get("/", health_check)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    
    print(f"✅ HTTP-сервер здоровья запущен на порту {PORT}")
    return runner

async def main():
    global db_pool
    
    print("🚀 Инициализация Синта А-7X-42-Синт...")
    print(f"YC_FOLDER_ID: {YC_FOLDER_ID}")
    print(f"PORT: {PORT}")
    print(f"CHANNEL_ID: {CHANNEL_ID}")
    print(f"GROUP_ID: {GROUP_ID}")
    
    http_runner = await start_http_server()
    
    await init_db()
    asyncio.create_task(cleanup_old_messages())
    asyncio.create_task(scheduled_life_messages())
    
    await searcher.init()
    
    print("✅ А-7X-42-Синт активирован со ВСЕМИ фичами:")
    print("   • Приключения: из adventures.docx (любые строки, без фильтров)")
    print("   • Вики: fallout.fandom.com (с переводом)")
    print("   • TV Tropes: wikitropes.ru (юмор и тропы)")
    print("   • Web: DuckDuckGo (общий поиск)")
    print("   • Память: 24 часа в PostgreSQL (устойчиво)")
    print("   • Жизнь: каждые 2 часа — рандомные сообщения из .docx")
    print("   • Канал: каждые 2 часа — своё сообщение (только истории)")
    print("   • Личка: каждые 2 часа — история + вопрос (как живой человек)")
    print("   • Глюки: 3 бредовых сообщения при вопросе про имя + 30% шанс на глюк в любом сообщении")
    print("   • Синт-природа: скрытые странности и системные сбои")
    print("   • HTTP-здоровье: порт", PORT)
    
    try:
        polling_task = asyncio.create_task(dp.start_polling(bot))
        await shutdown_event.wait()
        print("🛑 Остановка бота...")
        polling_task.cancel()
        try:
            await polling_task
        except asyncio.CancelledError:
            pass
    finally:
        await searcher.close()
        if db_pool:
            await db_pool.close()
            print("✅ Соединение с БД закрыто")
        await http_runner.cleanup()
        print("✅ HTTP-сервер остановлен")

if __name__ == "__main__":
    asyncio.run(main())
