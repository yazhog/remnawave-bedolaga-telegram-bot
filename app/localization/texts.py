import asyncio
from typing import Dict, Any
from app.config import settings

_cached_rules = {}

def _get_default_rules(language: str = "ru") -> str:
    if language == "en":
        return """
🔒 <b>Service Usage Rules</b>

1. It is forbidden to use the service for illegal activities
2. Copyright infringement is prohibited
3. Spam and malware distribution are prohibited
4. Using the service for DDoS attacks is prohibited
5. One account - one user
6. Refunds are made only in exceptional cases
7. Administration reserves the right to block an account for violating the rules

<b>By accepting the rules, you agree to comply with them.</b>
"""
    else:
        return """
📋 <b>Правила использования сервиса</b>

1. Запрещается использование сервиса для незаконной деятельности
2. Запрещается нарушение авторских прав
3. Запрещается спам и рассылка вредоносного ПО
4. Запрещается использование сервиса для DDoS атак
5. Один аккаунт - один пользователь
6. Возврат средств производится только в исключительных случаях
7. Администрация оставляет за собой право заблокировать аккаунт при нарушении правил

<b>Принимая правила, вы соглашаетесь соблюдать их.</b>
"""

class Texts:
    def __init__(self, language: str = "ru"):
        self.language = language
    
    @property
    def RULES_TEXT(self) -> str:
        if self.language in _cached_rules:
            return _cached_rules[self.language]
        
        return _get_default_rules(self.language)
    
    BACK = "⬅️ Назад"
    CANCEL = "❌ Отмена"
    CONFIRM = "✅ Подтвердить"
    CONTINUE = "➡️ Продолжить"
    YES = "✅ Да"
    NO = "❌ Нет"
    LOADING = "⏳ Загрузка..."
    ERROR = "❌ Произошла ошибка"
    SUCCESS = "✅ Успешно"
    
    @staticmethod
    def format_price(kopeks: int) -> str:
        return f"{int(kopeks / 100)} ₽"
    
    @staticmethod
    def format_traffic(gb: float) -> str:
        if gb == 0:
            return "∞ (безлимит)"
        elif gb >= 1024:
            return f"{gb/1024:.1f} ТБ"
        else:
            return f"{gb:.0f} ГБ"


class RussianTexts(Texts):
    
    def __init__(self):
        super().__init__("ru")
    
    WELCOME = """
🎉 <b>Добро пожаловать в VPN сервис!</b>

Наш сервис предоставляет быстрый и безопасный доступ к интернету без ограничений.

🔐 <b>Преимущества:</b>
• Высокая скорость подключения
• Серверы в разных странах
• Надежная защита данных
• Круглосуточная поддержка

Для начала работы выберите язык интерфейса:
"""
    
    LANGUAGE_SELECTED = "🌐 Язык интерфейса установлен: <b>Русский</b>"
    
    RULES_ACCEPT = "✅ Принимаю правила"
    RULES_DECLINE = "❌ Не принимаю"
    RULES_REQUIRED = "❗️ Для использования сервиса необходимо принять правила!"
    
    REFERRAL_CODE_QUESTION = """
🤝 <b>У вас есть реферальный код от друга?</b>

Если у вас есть промокод или реферальная ссылка от друга, введите её сейчас, чтобы получить бонус!

Введите код или нажмите "Пропустить":
"""
    
    REFERRAL_CODE_APPLIED = "🎁 Реферальный код применен! Вы получите бонус после первой покупки."
    REFERRAL_CODE_INVALID = "❌ Неверный реферальный код"
    REFERRAL_CODE_SKIP = "⏭️ Пропустить"
       
    MAIN_MENU = """👤 <b>{user_name}</b>
    
📱 <b>Подписка:</b> {subscription_status}

Выберите действие:
"""
    
    MENU_BALANCE = "💰 Баланс"
    MENU_SUBSCRIPTION = "📱 Подписка"
    MENU_TRIAL = "🧪 Тестовая подписка"
    MENU_BUY_SUBSCRIPTION = "💎 Купить подписку"
    MENU_EXTEND_SUBSCRIPTION = "⏰ Продлить подписку"
    MENU_PROMOCODE = "🎫 Промокод"
    MENU_REFERRALS = "🤝 Рефералы"
    MENU_SUPPORT = "🛠️ Техподдержка"
    MENU_RULES = "📋 Правила сервиса"
    MENU_LANGUAGE = "🌐 Язык"
    MENU_ADMIN = "⚙️ Админ-панель"
    BALANCE_BUTTON = "💰 Баланс: {balance}"
    BALANCE_BUTTON_ZERO = "💰 Баланс: 0 ₽"
    
    SUBSCRIPTION_NONE = "❌ Нет активной подписки"
    SUBSCRIPTION_TRIAL = "🧪 Тестовая подписка"
    SUBSCRIPTION_ACTIVE = "✅ Активна"
    SUBSCRIPTION_EXPIRED = "⏰ Истекла"
    
    SUBSCRIPTION_INFO = """
📱 <b>Информация о подписке</b>

📊 <b>Статус:</b> {status}
🎭 <b>Тип:</b> {type}
📅 <b>Действует до:</b> {end_date}
⏰ <b>Осталось дней:</b> {days_left}

📈 <b>Трафик:</b> {traffic_used} / {traffic_limit}
🌍 <b>Серверы:</b> {countries_count} стран
📱 <b>Устройства:</b> {devices_used} / {devices_limit}

💳 <b>Автоплатеж:</b> {autopay_status}
"""
    
    TRIAL_AVAILABLE = """
🎁 <b>Тестовая подписка</b>

Вы можете получить бесплатную тестовую подписку:

⏰ <b>Период:</b> {days} дней
📈 <b>Трафик:</b> {traffic} ГБ
📱 <b>Устройства:</b> {devices} шт.
🌍 <b>Сервер:</b> 1 страна

Активировать тестовую подписку?
"""
    
    TRIAL_ACTIVATED = "🎉 Тестовая подписка активирована!"
    TRIAL_ALREADY_USED = "❌ Тестовая подписка уже была использована"
    
    BUY_SUBSCRIPTION_START = """
💎 <b>Настройка подписки</b>

Давайте настроим вашу подписку под ваши потребности.

Сначала выберите период подписки:
"""
    
    SELECT_PERIOD = "Выберите период:"
    SELECT_TRAFFIC = "Выберите пакет трафика:"
    SELECT_COUNTRIES = "Выберите страны:"
    SELECT_DEVICES = "Количество устройств:"
    
    PERIOD_14_DAYS = f"📅 14 дней - {settings.format_price(settings.PRICE_14_DAYS)}"
    PERIOD_30_DAYS = f"📅 30 дней - {settings.format_price(settings.PRICE_30_DAYS)}"
    PERIOD_60_DAYS = f"📅 60 дней - {settings.format_price(settings.PRICE_60_DAYS)}"
    PERIOD_90_DAYS = f"📅 90 дней - {settings.format_price(settings.PRICE_90_DAYS)}"
    PERIOD_180_DAYS = f"📅 180 дней - {settings.format_price(settings.PRICE_180_DAYS)}"
    PERIOD_360_DAYS = f"📅 360 дней - {settings.format_price(settings.PRICE_360_DAYS)}"
    
    TRAFFIC_5GB = f"📊 5 ГБ - {settings.format_price(settings.PRICE_TRAFFIC_5GB)}"
    TRAFFIC_10GB = f"📊 10 ГБ - {settings.format_price(settings.PRICE_TRAFFIC_10GB)}"
    TRAFFIC_25GB = f"📊 25 ГБ - {settings.format_price(settings.PRICE_TRAFFIC_25GB)}"
    TRAFFIC_50GB = f"📊 50 ГБ - {settings.format_price(settings.PRICE_TRAFFIC_50GB)}"
    TRAFFIC_100GB = f"📊 100 ГБ - {settings.format_price(settings.PRICE_TRAFFIC_100GB)}"
    TRAFFIC_250GB = f"📊 250 ГБ - {settings.format_price(settings.PRICE_TRAFFIC_250GB)}"
    TRAFFIC_UNLIMITED = f"📊 Безлимит - {settings.format_price(settings.PRICE_TRAFFIC_UNLIMITED)}"
    
    SUBSCRIPTION_SUMMARY = """
📋 <b>Итоговая конфигурация</b>

📅 <b>Период:</b> {period} дней
📈 <b>Трафик:</b> {traffic}
🌍 <b>Страны:</b> {countries}
📱 <b>Устройства:</b> {devices}

💰 <b>Итого к оплате:</b> {total_price}

Подтвердить покупку?
"""
    
    INSUFFICIENT_BALANCE = """❌ Недостаточно средств на балансе.  
    
    <b>Пополните баланс на {amount} и попробуйте снова.</b>
    """
    GO_TO_BALANCE_TOP_UP = "💳 Перейти к пополнению баланса"
    SUBSCRIPTION_PURCHASED = "🎉 Подписка успешно приобретена!"
    
    BALANCE_INFO = """
💰 <b>Баланс: {balance}</b>

Выберите действие:
"""
    
    BALANCE_HISTORY = "📊 История операций"
    BALANCE_TOP_UP = "💳 Пополнить"
    BALANCE_SUPPORT_REQUEST = "🛠️ Запрос через поддержку"
    
    TOP_UP_AMOUNT = "💳 Введите сумму для пополнения (в рублях):"
    TOP_UP_METHODS = """
💳 <b>Выберите способ оплаты</b>

Сумма: {amount}
"""
    
    TOP_UP_STARS = "⭐ Telegram Stars"
    TOP_UP_TRIBUTE = "💎 Банковская карта"
    
    PROMOCODE_ENTER = "🎫 Введите промокод:"
    PROMOCODE_SUCCESS = "🎉 Промокод активирован! {description}"
    PROMOCODE_INVALID = "❌ Неверный промокод"
    PROMOCODE_EXPIRED = "❌ Промокод истек"
    PROMOCODE_USED = "❌ Промокод уже использован"
    
    REFERRAL_INFO = """
🤝 <b>Реферальная программа</b>

👥 <b>Приглашено:</b> {referrals_count} друзей
💰 <b>Заработано:</b> {earned_amount}

🔗 <b>Ваша реферальная ссылка:</b>
<code>{referral_link}</code>

🎫 <b>Ваш промокод:</b>
<code>{referral_code}</code>

💰 <b>Условия:</b>
• За каждого друга: {registration_bonus}
• Процент с пополнений: {commission_percent}%
"""
    
    REFERRAL_INVITE_MESSAGE = """
🎯 <b>Приглашение в VPN сервис</b>

Привет! Приглашаю тебя в отличный VPN сервис!

🎁 По моей ссылке ты получишь бонус: {bonus}

🔗 Переходи: {link}
🎫 Или используй промокод: {code}

💪 Быстро, надежно, недорого!
"""
    
    CREATE_INVITE = "📝 Создать приглашение"

    TRIAL_ENDING_SOON = """
🎁 <b>Тестовая подписка скоро закончится!</b>

Ваша тестовая подписка истекает через несколько часов.

💎 <b>Не хотите остаться без VPN?</b>
Переходите на полную подписку!

🔥 <b>Специальное предложение:</b>
• 30 дней всего за {price}
• Безлимитный трафик  
• Все серверы доступны
• Скорость до 1ГБит/сек

⚡️ Успейте оформить до окончания тестового периода!
"""

    MAINTENANCE_MODE_ACTIVE = """
🔧 Технические работы!

Сервис временно недоступен. Ведутся технические работы по улучшению качества обслуживания.

⏰ Ориентировочное время завершения: неизвестно
🔄 Попробуйте позже

Приносим извинения за временные неудобства.
"""

    MAINTENANCE_MODE_API_ERROR = """
🔧 Технические работы!

Сервис временно недоступен из-за проблем с подключением к серверам.

⏰ Мы работаем над восстановлением. Попробуйте через несколько минут.

🔄 Последняя проверка: {last_check}
"""

    SUBSCRIPTION_EXPIRING_PAID = """
⚠️ <b>Подписка истекает через {days_text}!</b>

Ваша платная подписка истекает {end_date}.

💳 <b>Автоплатеж:</b> {autopay_status}

{action_text}
"""

    AUTOPAY_ENABLED_TEXT = "Включен - подписка продлится автоматически"
    AUTOPAY_DISABLED_TEXT = "Отключен - не забудьте продлить вручную!"

    SUBSCRIPTION_EXPIRED = """
❌ <b>Подписка истекла</b>

Ваша подписка истекла. Для восстановления доступа продлите подписку.

🔧 Доступ к серверам заблокирован до продления.
"""

    AUTOPAY_SUCCESS = """
✅ <b>Автоплатеж выполнен</b>

Ваша подписка автоматически продлена на {days} дней.
Списано с баланса: {amount}

Новая дата окончания: {new_end_date}
"""

    AUTOPAY_FAILED = """
❌ <b>Ошибка автоплатежа</b>

Не удалось списать средства для продления подписки.

💰 Ваш баланс: {balance}
💳 Требуется: {required}

Пополните баланс и продлите подписку вручную.
"""
    
    SUPPORT_INFO = f"""
🛠️ <b>Техническая поддержка</b>

По всем вопросам обращайтесь к нашей поддержке:

👤 {settings.SUPPORT_USERNAME}

Мы поможем с:
• Настройкой подключения
• Решением технических проблем  
• Вопросами по оплате
• Другими вопросами

⏰ Время ответа: обычно в течение 1-2 часов
"""
    
    CONTACT_SUPPORT = "💬 Написать в поддержку"
    
    ADMIN_PANEL = """
⚙️ <b>Административная панель</b>

Выберите раздел для управления:
"""
    
    ADMIN_USERS = "👥 Пользователи"
    ADMIN_SUBSCRIPTIONS = "📱 Подписки"
    ADMIN_PROMOCODES = "🎫 Промокоды"
    ADMIN_MESSAGES = "📨 Рассылки"
    ADMIN_MONITORING = "🔍 Мониторинг"
    ADMIN_REFERRALS = "🤝 Рефералы"
    ADMIN_RULES = "📋 Правила"
    ADMIN_REMNAWAVE = "🖥️ Remnawave"
    ADMIN_STATISTICS = "📊 Статистика"
    
    ACCESS_DENIED = "❌ Доступ запрещен"
    USER_NOT_FOUND = "❌ Пользователь не найден"
    SUBSCRIPTION_NOT_FOUND = "❌ Подписка не найдена"
    INVALID_AMOUNT = "❌ Неверная сумма"
    OPERATION_CANCELLED = "❌ Операция отменена"
    
    SUBSCRIPTION_EXPIRING = """
⚠️ <b>Подписка истекает!</b>

Ваша подписка истекает через {days} дней.

Не забудьте продлить подписку, чтобы не потерять доступ к серверам.
"""
    
    SUBSCRIPTION_EXPIRED = """
❌ <b>Подписка истекла</b>

Ваша подписка истекла. Для восстановления доступа продлите подписку.
"""
    
    AUTOPAY_SUCCESS = """
✅ <b>Автоплатеж выполнен</b>

Ваша подписка автоматически продлена на {days} дней.
Списано с баланса: {amount}
"""
    
    AUTOPAY_FAILED = """
❌ <b>Ошибка автоплатежа</b>

Не удалось списать средства для продления подписки.
Недостаточно средств на балансе: {balance}
Требуется: {required}

Пополните баланс и продлите подписку вручную.
"""


class EnglishTexts(Texts):
    
    def __init__(self):
        super().__init__("en")
    
    WELCOME = """
🎉 <b>Welcome to VPN Service!</b>

Our service provides fast and secure internet access without restrictions.

🔐 <b>Advantages:</b>
• High connection speed
• Servers in different countries  
• Reliable data protection
• 24/7 support

To get started, select interface language:
"""
    
    LANGUAGE_SELECTED = "🌐 Interface language set: <b>English</b>"
    
    BACK = "⬅️ Back"
    CANCEL = "❌ Cancel"
    CONFIRM = "✅ Confirm"
    CONTINUE = "➡️ Continue"
    YES = "✅ Yes"
    NO = "❌ No"

    MENU_BALANCE = "💰 Balance"
    MENU_SUBSCRIPTION = "📱 Subscription"
    MENU_TRIAL = "🎁 Trial subscription"
    INSUFFICIENT_BALANCE = """❌ Insufficient balance. " \
    
    Top up {amount} and try again."""
    GO_TO_BALANCE_TOP_UP = "💳 Go to balance top up"
    

LANGUAGES = {
    "ru": RussianTexts,
    "en": EnglishTexts
}


def get_texts(language: str = "ru") -> Texts:
    return LANGUAGES.get(language, RussianTexts)()

async def get_rules_from_db(language: str = "ru") -> str:
    try:
        from app.database.database import get_db
        from app.database.crud.rules import get_current_rules_content
        
        async for db in get_db():
            rules = await get_current_rules_content(db, language)
            if rules:
                _cached_rules[language] = rules
                return rules
            break
            
    except Exception as e:
        print(f"Ошибка получения правил из БД: {e}")
    
    default_rules = _get_default_rules(language)
    _cached_rules[language] = default_rules
    return default_rules

def get_rules_sync(language: str = "ru") -> str:
    try:
        if language in _cached_rules:
            return _cached_rules[language]
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        rules = loop.run_until_complete(get_rules_from_db(language))
        loop.close()
        return rules
        
    except Exception as e:
        print(f"Ошибка получения правил: {e}")
        return _get_default_rules(language)

async def refresh_rules_cache(language: str = "ru"):
    try:
        if language in _cached_rules:
            del _cached_rules[language]
        
        await get_rules_from_db(language)
        print(f"✅ Кеш правил для языка {language} обновлен")
        
    except Exception as e:
        print(f"Ошибка обновления кеша правил: {e}")

def clear_rules_cache():
    global _cached_rules
    _cached_rules.clear()
    print("✅ Кеш правил очищен")
