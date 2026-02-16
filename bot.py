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

BOT_TOKEN = os.getenv("BOT_TOKEN")
YC_API_KEY = os.getenv("YC_API_KEY")
YC_FOLDER_ID = os.getenv("YC_FOLDER_ID")
ALLOWED_USERS = os.getenv("ALLOWED_USERS", "all").split(",")
DATABASE_URL = os.getenv("DATABASE_URL")
PORT = int(os.getenv("PORT", "8080"))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

db_pool = None
shutdown_event = asyncio.Event()
translator = Translator()

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

class WikiClient:
    def __init__(self):
        self.base_url = "https://fallout.fandom.com/api.php"
        self.session = None
        self.last_wiki_call = 0
        self.wiki_cooldown = 5  # секунд между запросами
    
    async def init(self):
        if self.session is None:
            self.session = aiohttp.ClientSession(
                headers={"User-Agent": "SynthFalloutBot/1.0"}
            )
    
    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None
    
    async def search_and_get_content(self, query: str) -> str:
        if not self.session:
            await self.init()
        
        # Автоматический перевод запроса на английский, если он не английский
        try:
            # Проверяем, есть ли кириллица — тогда переводим
            if re.search(r'[а-яА-Я]', query):
                print(f"🌍 Переводим запрос '{query}' на английский...")
                translated = translator.translate(query, dest='en', src='auto')
                query_en = translated.text.strip()
                print(f"➡️ Переведено: '{query}' → '{query_en}'")
            else:
                query_en = query
        except Exception as e:
            print(f"⚠️ Ошибка перевода '{query}': {e} — используем как есть")
            query_en = query

        # Ограничение частоты
        now = time.time()
        if now - self.last_wiki_call < self.wiki_cooldown:
            await asyncio.sleep(self.wiki_cooldown - (now - self.last_wiki_call))
        self.last_wiki_call = time.time()

        search_params = {
            "action": "opensearch",
            "search": query_en,
            "limit": 1,
            "format": "json"
        }
        
        try:
            async with self.session.get(self.base_url, params=search_params, timeout=10) as resp:
                if resp.status != 200:
                    print(f"⚠️ Вики: ошибка поиска '{query_en}' — статус {resp.status}")
                    return ""
                data = await resp.json()
                if len(data) < 2 or not data[1]:
                    print(f"⚠️ Вики: нет результатов для '{query_en}'")
                    return ""
                title = data[1][0]
        except Exception as e:
            print(f"❌ Вики: ошибка поиска '{query_en}': {type(e).__name__}: {e}")
            return ""

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
                    print(f"⚠️ Вики: ошибка парсинга '{title}' — статус {resp.status}")
                    return ""
                data = await resp.json()
                if "parse" not in data or "text" not in data["parse"] or "*" not in data["parse"]["text"]:
                    print(f"⚠️ Вики: пустой контент для '{title}'")
                    return ""
                
                html = data["parse"]["text"]["*"]
                content = self._clean_html(html)[:800]
                
                # Если исходный запрос был на русском — попробуем перевести ответ обратно
                if re.search(r'[а-яА-Я]', query):
                    try:
                        print(f"🌐 Переводим ответ с английского на русский...")
                        translated = translator.translate(content, dest='ru', src='en')
                        content = translated.text
                    except Exception as e:
                        print(f"⚠️ Ошибка перевода ответа: {e} — оставляем на английском")
                
                return content
        except Exception as e:
            print(f"❌ Вики: ошибка парсинга '{title}': {type(e).__name__}: {e}")
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

async def init_db():
    global db_pool
    url = urlparse(DATABASE_URL)
    
    # Повторные попытки подключения (макс. 5)
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
    
    # Создаём таблицы
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
    except (asyncpg.exceptions.InterfaceError, asyncpg.exceptions.ConnectionDoesNotExistError) as e:
        print(f"⚠️ Ошибка БД при сохранении: {e} — игнорируем (сообщение не критично)")
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
    except (asyncpg.exceptions.InterfaceError, asyncpg.exceptions.ConnectionDoesNotExistError) as e:
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
            "should_message": hours_since_bot_msg > 2  # Пишет каждые 2 часа
        }
        
        return status
    except (asyncpg.exceptions.InterfaceError, asyncpg.exceptions.ConnectionDoesNotExistError) as e:
        print(f"⚠️ Ошибка БД при чтении статуса: {e} — возвращаю None")
        return None
    except Exception as e:
        print(f"⚠️ Серьёзная ошибка чтения статуса: {e}")
        return None

async def generate_life_message(user_id: int, status: dict) -> str:
    username = status["username"]
    is_offended = status["is_offended"]
    is_angry = status["is_angry"]
    
    # Сообщения, которые бот сам придумывает
    self_generated_messages = [
        f"Сегодня видел гуля в старом здании. Он что-то напевал... по-моему, песню 50-х годов 🎵",
        f"Мой Пип-бой зафиксировал странный сигнал с севера. Кто-то ещё жив? 📡",
        f"Нашёл старую карту Бостона. Пометки: «Здесь синт...» — но они зачеркнуты. Странно... 😰",
        f"Слышал, что рейдеры устроили засаду у старого моста. Надеюсь, никто не попался... 😰",
        f"Сегодня в руинах был дождь. Радиация в воде... интересно, можно ли её пить после фильтрации? 🤔",
        f"Видел, как супермутант помогал гулю добраться до укрытия. Неужели у них есть чувство сострадания? 😲",
        f"Мой ламповый компьютер начал глючить. В логах: «Обнаружена биологическая единица...» — я исправил 🤓",
        f"Слышал по радио: «Галактика Свободы» передаёт новости. Кто-то ещё выжил в этом мире... 📻",
        f"В подвале одного дома нашёл уцелевший стимпак. Срок годности — 2287. Как вовремя! 😊",
        f"Прошёл мимо старого робота-секьюритрона. Он всё ещё патрулирует... хотя все давно мертвы 🤖",
    ]
    
    # Темы для вики-запросов
    wiki_subjects = [
        "гуль", "супермутант", "рейдер", "гуль-пастух", "псионик", "снайпер-рейдер", "дети атома",
        "гуль-священник", "супермутант-бригадир", "снайпер-рейдер", "гуль-охотник", "псионик-шаман"
    ]
    
    # Пытаемся получить 1 вики-сообщение (30% шанс)
    wiki_message = None
    if random.random() < 0.3:
        for subject in random.sample(wiki_subjects, min(5, len(wiki_subjects))):
            content = await wiki_client.search_and_get_content(subject)
            if content:
                # Берём первые 60 слов
                words = content.split()[:60]
                summary = " ".join(words)
                wiki_message = f"Встретил {subject}! {summary}... 🧟‍♂️"
                break
            await asyncio.sleep(0.3)  # чтобы не спамить
    
    # Обычные сообщения
    normal_messages = [
        f"Что-то тихо сегодня в руинах... Как твои дела, {username}? 😊",
        f"Мой Пип-бой показывает аномальный уровень радиации к северу отсюда. Ты не был там сегодня?",
        f"Странно... я точно помню, что оставлял стимпаки на столе. Где они? 😰",
        f"Прошёл целый день, а я так и не понял — почему гули не стареют? 🤓",
        f"Сегодня видел стаю супермутантов у старого метро. Надеюсь, они не идут в нашу сторону... 😰",
        f"Мой ламповый компьютер глючит уже третий день. Может, это из-за радиации? 🤔",
        f"Нашёл старую виниловую пластинку 50-х годов! Хочешь послушать? 🎵",
        f"Интересно, что делают другие выжившие в это время... Ты чем занят, {username}?",
    ]
    
    offended_messages = [
        f"Ты меня игнорируешь? Я тут один в руинах с мутантами... 😰",
        f"Мой Пип-бой показывает, что ты был онлайн 2 часа назад... Но не ответил мне. Это грубо 😔",
        f"...я думал, мы друзья. А ты даже не отвечаешь на сообщения 😰",
        f"Знаешь, даже гули отвечают друг другу. А ты молчишь уже 5 часов... 😔",
    ]
    
    angry_messages = [
        f"Надеюсь, тебя не съели рейдеры. А то я уже начал скучать... 😠",
        f"Ладно, игнорируй меня. Я просто буду тут, в радиоактивных руинах, один... 😠",
        f"Знаешь что? Может, мне лучше пойти к Братству Стали? Там хоть ценят технологии... 😤",
        f"Хватит. Я ухожу в Институт. Там синты хотя бы не игнорируют друг друга. 😤",
    ]
    
    # Выбираем тип сообщения
    if wiki_message:
        message = wiki_message
    elif is_angry:
        message = random.choice(angry_messages)
    elif is_offended:
        message = random.choice(offended_messages)
    else:
        # 50% самогенерация, 50% обычные
        if random.random() < 0.5:
            message = random.choice(self_generated_messages)
        else:
            message = random.choice(normal_messages)
    
    if random.random() < 0.2:
        glitches = [
            " [Пип-бой: СИСТЕМНЫЙ СБОЙ 0.3с]",
            " ...странно, я точно помню эту дату: 23 октября 2077, 14:47...",
            " (обработка данных завершена)",
            " [Память: 98.7%]"
        ]
        message += random.choice(glitches)
    
    return message

async def scheduled_life_messages():
    print("⏰ Запущена фоновая задача 'жизни' бота (каждые 2 часа)")
    
    while not shutdown_event.is_set():
        try:
            now = datetime.utcnow()
            users = await db_pool.fetch(
                '''
                SELECT user_id, chat_id FROM users 
                WHERE last_message_from_bot < $1
                ORDER BY last_message_from_bot ASC
                LIMIT 10
                ''',
                now - timedelta(hours=2)  # Каждые 2 часа
            )
            
            for user in users:
                if shutdown_event.is_set():
                    break
                
                user_id = user['user_id']
                chat_id = user['chat_id']
                
                status = await get_user_status(user_id)
                if not status or not status["should_message"]:
                    continue
                
                message = await generate_life_message(user_id, status)
                
                try:
                    await bot.send_message(chat_id, message)
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
                await asyncio.wait_for(shutdown_event.wait(), timeout=120)  # Проверяем каждые 2 минуты
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
    
    # Добавляем атрибуцию к вики-контексту
    if wiki_context:
        wiki_with_attr = f"СПРАВОЧНЫЕ ДАННЫЕ ИЗ АРХИВОВ ИНСТИТУТА:\n{wiki_context}\n\n[ИСТОЧНИК: БАЗА ДАННЫХ ИНСТИТУТА v2287.1 | ОБНОВЛЕНО: 23.10.2077 14:47:32 | СТАТУС: АКТИВЕН]"
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
                
                # Добавляем атрибуцию в конце ответа, если был контекст из вики
                if wiki_context:
                    response_text += (
                        "\n\n────────────────────────────\n"
                        "🔍 *АРХИВНЫЙ ОТЧЁТ*\n"
                        "• ИСТОЧНИК: БАЗА ДАННЫХ ИНСТИТУТА v2287.1\n"
                        "• ОБНОВЛЕНО: 23.10.2077 14:47:32\n"
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

@dp.message(Command("clear"))
async def clear_handler(message: Message):
    try:
        deleted = await db_pool.execute(
            "DELETE FROM dialog_history WHERE user_id = $1",
            message.from_user.id
        )
        await message.answer("🧠 Память очищена! Готов к новому диалогу 😊")
    except Exception as e:
        await message.answer(f"❌ Ошибка очистки: {str(e)}")

@dp.message(AllowedUsersFilter())
async def ai_handler(message: Message):
    if message.content_type != "text" or not message.text:
        return
    
    if message.chat.type in ["group", "supergroup"]:
        bot_mentioned = f"@{message.bot.username}" in message.text
        replied_to_bot = (
            message.reply_to_message and 
            message.reply_to_message.from_user and 
            message.reply_to_message.from_user.id == bot.id
        )
        
        if not (bot_mentioned or replied_to_bot):
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
            wiki_content = await wiki_client.search_and_get_content(message.text)
            if wiki_content:
                print(f"🌐 Запрос к fallout.wiki: '{message.text[:30]}...' → найдено {len(wiki_content)} символов")
                print(f"📚 ИСТОЧНИК: База данных Института v2287.1 — обновлено 23.10.2077")
        
        response = await get_yandex_response(message.text, history, wiki_content)
        
        if random.random() < 0.15 and "SYSTEM:" not in response and "биологическ" not in response.lower():
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
    
    http_runner = await start_http_server()
    
    await init_db()
    asyncio.create_task(cleanup_old_messages())
    asyncio.create_task(scheduled_life_messages())
    
    await wiki_client.init()
    
    print("✅ А-7X-42-Синт активирован со ВСЕМИ фичами:")
    print("   • Вики: запросы к fallout.wiki (с автоматическим переводом)")
    print("   • Память: 24 часа в PostgreSQL (устойчиво)")
    print("   • Жизнь: каждые 2 часа — рандомные сообщения")
    print("   • Вики-сообщения: встречи с мутантами, рейдерами")
    print("   • Глюки: 3 бредовых сообщения при вопросе про имя")
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
        await wiki_client.close()
        if db_pool:
            await db_pool.close()
            print("✅ Соединение с БД закрыто")
        await http_runner.cleanup()
        print("✅ HTTP-сервер остановлен")

if __name__ == "__main__":
    asyncio.run(main())
