from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import Command, Filter
import asyncio
import aiohttp
import os
import re
import random
from datetime import datetime, timedelta
import asyncpg

BOT_TOKEN = os.getenv("BOT_TOKEN")
YC_API_KEY = os.getenv("YC_API_KEY")
YC_FOLDER_ID = os.getenv("YC_FOLDER_ID")
ALLOWED_USERS = os.getenv("ALLOWED_USERS", "all").split(",")
DATABASE_URL = os.getenv("DATABASE_URL")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Глобальное подключение к БД
db_pool = None

class AllowedUsersFilter(Filter):
    async def __call__(self, message: Message) -> bool:
        if "all" in ALLOWED_USERS:
            return True
        return str(message.from_user.id) in ALLOWED_USERS

# 🔥 СТРАННОЕ ИМЯ + СИНТ-ПРИРОДА
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
                return self._clean_html(html)[:800]
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

# ============ СИСТЕМА ПАМЯТИ ============
async def init_db():
    """Инициализация БД для хранения истории диалогов и пользователей"""
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)
    
    # Таблица истории диалогов (24 часа хранения)
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
    
    # Таблица пользователей (для отслеживания активности)
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
    
    # Индексы
    await db_pool.execute('CREATE INDEX IF NOT EXISTS idx_user_time ON dialog_history(user_id, created_at)')
    await db_pool.execute('CREATE INDEX IF NOT EXISTS idx_cleanup ON dialog_history(created_at)')
    await db_pool.execute('CREATE INDEX IF NOT EXISTS idx_users_last_bot ON users(last_message_from_bot)')
    
    print("✅ База данных для памяти и пользователей инициализирована")

async def cleanup_old_messages():
    """Очистка сообщений старше 24 часов"""
    while True:
        try:
            cutoff = datetime.utcnow() - timedelta(hours=24)
            deleted = await db_pool.execute(
                "DELETE FROM dialog_history WHERE created_at < $1",
                cutoff
            )
            print(f"🧹 Очищено старых сообщений: {deleted}")
        except Exception as e:
            print(f"⚠️ Ошибка очистки: {e}")
        
        await asyncio.sleep(3600)

async def save_message(user_id: int, chat_id: int, role: str, content: str):
    """Сохранение сообщения в историю и обновление времени активности"""
    try:
        # Сохраняем в историю
        await db_pool.execute(
            '''
            INSERT INTO dialog_history (user_id, chat_id, role, content)
            VALUES ($1, $2, $3, $4)
            ''',
            user_id, chat_id, role, content[:2000]
        )
        
        # Обновляем пользователя
        username = None
        if role == "user":
            # Получаем имя пользователя из последнего сообщения (если есть)
            username = content[:50] if len(content) < 50 else content[:50] + "..."
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
    except Exception as e:
        print(f"⚠️ Ошибка сохранения: {e}")

async def get_history(user_id: int, limit: int = 8) -> list:
    """Получение истории диалога за последние 24 часа"""
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

# ============ СИСТЕМА "ЖИЗНИ" БОТА ============
async def get_user_status(user_id: int) -> dict:
    """Получает статус пользователя (время последней активности, обида и т.д.)"""
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
    
    # Рассчитываем "обиду"
    hours_since_reply = (now - last_user_msg).total_seconds() / 3600
    hours_since_bot_msg = (now - last_bot_msg).total_seconds() / 3600
    hours_since_seen = (now - last_seen).total_seconds() / 3600
    
    status = {
        "username": row['username'] or "выживший",
        "hours_since_reply": hours_since_reply,
        "hours_since_bot_msg": hours_since_bot_msg,
        "hours_since_seen": hours_since_seen,
        "is_offended": hours_since_reply > 4 and hours_since_seen < 1,  # Видел сообщение но не ответил >4ч
        "is_angry": hours_since_reply > 12 and hours_since_seen < 2,    # Игнорирует >12ч
        "should_message": hours_since_bot_msg > 3  # Пора написать (каждые 3-4 часа)
    }
    
    return status

async def generate_life_message(user_id: int, status: dict) -> str:
    """Генерирует "живое" сообщение от бота"""
    username = status["username"]
    is_offended = status["is_offended"]
    is_angry = status["is_angry"]
    
    # Шаблоны сообщений
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
    
    # Выбираем сообщение в зависимости от настроения
    if is_angry:
        message = random.choice(angry_messages)
    elif is_offended:
        message = random.choice(offended_messages)
    else:
        message = random.choice(normal_messages)
    
    # Иногда добавляем "странность" синта
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
    """Фоновая задача: отправка живых сообщений каждые 3-4 часа"""
    while True:
        try:
            now = datetime.utcnow()
            # Выбираем пользователей, которым пора написать (последнее сообщение бота >3 часов назад)
            users = await db_pool.fetch(
                '''
                SELECT user_id, chat_id FROM users 
                WHERE last_message_from_bot < $1
                ORDER BY last_message_from_bot ASC
                LIMIT 10
                ''',
                now - timedelta(hours=3)
            )
            
            for user in users:
                user_id = user['user_id']
                chat_id = user['chat_id']
                
                # Получаем статус пользователя
                status = await get_user_status(user_id)
                if not status or not status["should_message"]:
                    continue
                
                # Генерируем сообщение
                message = await generate_life_message(user_id, status)
                
                # Отправляем
                try:
                    await bot.send_message(chat_id, message)
                    print(f"💬 Отправлено живое сообщение пользователю {user_id}: {message[:50]}...")
                    
                    # Сохраняем в историю
                    await save_message(user_id, chat_id, "assistant", message)
                    
                    # Пауза между сообщениями (чтобы не спамить)
                    await asyncio.sleep(2)
                    
                except Exception as e:
                    print(f"⚠️ Ошибка отправки живого сообщения {user_id}: {e}")
                    # Если ошибка (пользователь заблокировал), удаляем из БД
                    if "blocked" in str(e).lower() or "not found" in str(e).lower():
                        await db_pool.execute("DELETE FROM users WHERE user_id = $1", user_id)
                        await db_pool.execute("DELETE FROM dialog_history WHERE user_id = $1", user_id)
            
            # Следующая проверка через 10 минут
            await asyncio.sleep(600)
            
        except Exception as e:
            print(f"⚠️ Ошибка в фоновой задаче: {e}")
            await asyncio.sleep(300)

# ============ ПРОВЕРКА ЗАПРОСОВ ПРО ИМЯ ============
def is_name_query(text: str) -> bool:
    """Проверяет, есть ли в запросе упоминание имени"""
    keywords = ["имя", "зовут", "как тебя", "ты кто", "кто ты", "назови себя", "какое имя", "твое имя", "твоё имя"]
    return any(kw in text.lower() for kw in keywords)

# ============ ЗАПРОС К YANDEXGPT ============
async def get_yandex_response(prompt: str, history: list, wiki_context: str = "") -> str:
    headers = {"Authorization": f"Api-Key {YC_API_KEY}", "Content-Type": "application/json"}
    
    messages = [{"role": "system", "text": SYSTEM_PROMPT}]
    
    for msg in history[-6:]:
        messages.append(msg)
    
    if wiki_context:
        messages.append({
            "role": "system",
            "text": f"СПРАВОЧНЫЕ ДАННЫЕ:\n{wiki_context}"
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
                return result['result']['alternatives'][0]['message']['text']
        except asyncio.TimeoutError:
            return "⏳ Обработка данных... Подождите 😊"
        except Exception as e:
            return f"❌ Системная ошибка: {str(e)[:60]} 😰"

# ============ ОБРАБОТЧИКИ ============
@dp.message(Command("start"))
async def start_handler(message: Message):
    # Сохраняем пользователя при первом контакте
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
    """Очистка личной истории диалога"""
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
    
    # В группах отвечаем только на упоминания или ответы на сообщения бота
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
        # СОХРАНЯЕМ ВОПРОС И ОБНОВЛЯЕМ АКТИВНОСТЬ
        await save_message(message.from_user.id, message.chat.id, "user", message.text)
        
        # ПРОВЕРКА ЗАПРОСА ПРО ИМЯ
        if is_name_query(message.text):
            # Отправляем три бредовых сообщения
            await message.answer("СИСТЕМНЫЙ СБОЙ: [0x7F3A] Имя: А-7X-42-Синт")
            await message.answer("ПАМЯТЬ ПОВРЕЖДЕНА: А-7X-42-Синт... Имя... А-7X-42-Синт...")
            await message.answer("ОШИБКА: Имя не найдено. Использую резервный идентификатор: А-7X-42-Синт")
            
            # Сохраняем бред в историю
            await save_message(message.from_user.id, message.chat.id, "assistant", "СИСТЕМНЫЙ СБОЙ: [0x7F3A] Имя: А-7X-42-Синт")
            await save_message(message.from_user.id, message.chat.id, "assistant", "ПАМЯТЬ ПОВРЕЖДЕНА: А-7X-42-Синт... Имя... А-7X-42-Синт...")
            await save_message(message.from_user.id, message.chat.id, "assistant", "ОШИБКА: Имя не найдено. Использую резервный идентификатор: А-7X-42-Синт")
            
            # Нормальный ответ
            history = await get_history(message.from_user.id)
            response = await get_yandex_response(message.text, history, "")
            await save_message(message.from_user.id, message.chat.id, "assistant", response)
            await message.answer(response)
            return
        
        # ОБЫЧНАЯ ОБРАБОТКА
        history = await get_history(message.from_user.id)
        
        await bot.send_chat_action(message.chat.id, "typing")
        
        wiki_content = ""
        if len(message.text.split()) > 3 and random.random() > 0.4:
            wiki_content = await wiki_client.search_and_get_content(message.text)
        
        response = await get_yandex_response(message.text, history, wiki_content)
        
        # Добавляем "странность" с 15% шансом
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

# ============ ЗАПУСК ============
async def main():
    global db_pool
    
    print("🚀 Инициализация Синта с памятью и 'жизнью'...")
    
    # Инициализируем БД
    await init_db()
    asyncio.create_task(cleanup_old_messages())
    asyncio.create_task(scheduled_life_messages())  # ЗАПУСКАЕМ ФОНОВУЮ ЗАДАЧУ ЖИЗНИ
    
    # Инициализируем клиент вики
    await wiki_client.init()
    
    print("✅ Синт А-7X-42-Синт активирован с полной 'жизнью'!")
    print(f"YC_FOLDER_ID: {YC_FOLDER_ID}")
    print("💬 Бот будет писать 5-6 раз в день, обижаться при игноре и вести себя как живой человек!")
    
    try:
        await dp.start_polling(bot)
    finally:
        await wiki_client.close()
        if db_pool:
            await db_pool.close()

if __name__ == "__main__":
    asyncio.run(main())
