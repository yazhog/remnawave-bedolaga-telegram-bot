import logging
import os
import re
import html
from collections import defaultdict
from datetime import time
from typing import List, Optional, Union, Dict
from pydantic_settings import BaseSettings
from pydantic import field_validator, Field
from pathlib import Path


class Settings(BaseSettings):
    
    BOT_TOKEN: str
    ADMIN_IDS: str = ""
    SUPPORT_USERNAME: str = "@support"
    SUPPORT_MENU_ENABLED: bool = True
    SUPPORT_SYSTEM_MODE: str = "both"  # one of: tickets, contact, both
    SUPPORT_MENU_ENABLED: bool = True
    # SLA for support tickets
    SUPPORT_TICKET_SLA_ENABLED: bool = True
    SUPPORT_TICKET_SLA_MINUTES: int = 5
    SUPPORT_TICKET_SLA_CHECK_INTERVAL_SECONDS: int = 60
    SUPPORT_TICKET_SLA_REMINDER_COOLDOWN_MINUTES: int = 15

    ADMIN_NOTIFICATIONS_ENABLED: bool = False
    ADMIN_NOTIFICATIONS_CHAT_ID: Optional[str] = None
    ADMIN_NOTIFICATIONS_TOPIC_ID: Optional[int] = None
    ADMIN_NOTIFICATIONS_TICKET_TOPIC_ID: Optional[int] = None

    ADMIN_REPORTS_ENABLED: bool = False
    ADMIN_REPORTS_CHAT_ID: Optional[str] = None
    ADMIN_REPORTS_TOPIC_ID: Optional[int] = None
    ADMIN_REPORTS_SEND_TIME: Optional[str] = None

    CHANNEL_SUB_ID: Optional[str] = None
    CHANNEL_LINK: Optional[str] = None
    CHANNEL_IS_REQUIRED_SUB: bool = False
    
    DATABASE_URL: Optional[str] = None
    
    POSTGRES_HOST: str = "postgres"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "remnawave_bot"
    POSTGRES_USER: str = "remnawave_user" 
    POSTGRES_PASSWORD: str = "secure_password_123"
    
    SQLITE_PATH: str = "./data/bot.db"
    LOCALES_PATH: str = "./locales"
    
    DATABASE_MODE: str = "auto"
    
    REDIS_URL: str = "redis://localhost:6379/0"
    
    REMNAWAVE_API_URL: Optional[str] = None
    REMNAWAVE_API_KEY: Optional[str] = None
    REMNAWAVE_SECRET_KEY: Optional[str] = None

    REMNAWAVE_USERNAME: Optional[str] = None
    REMNAWAVE_PASSWORD: Optional[str] = None
    REMNAWAVE_AUTH_TYPE: str = "api_key"
    REMNAWAVE_USER_DESCRIPTION_TEMPLATE: str = "Bot user: {full_name} {username}"
    REMNAWAVE_USER_DELETE_MODE: str = "delete"  # "delete" или "disable"
    
    TRIAL_DURATION_DAYS: int = 3
    TRIAL_TRAFFIC_LIMIT_GB: int = 10
    TRIAL_DEVICE_LIMIT: int = 2
    TRIAL_ADD_REMAINING_DAYS_TO_PAID: bool = False
    DEFAULT_TRAFFIC_LIMIT_GB: int = 100
    DEFAULT_DEVICE_LIMIT: int = 1
    TRIAL_SQUAD_UUID: Optional[str] = None
    DEFAULT_TRAFFIC_RESET_STRATEGY: str = "MONTH"
    RESET_TRAFFIC_ON_PAYMENT: bool = False
    MAX_DEVICES_LIMIT: int = 20
    
    TRIAL_WARNING_HOURS: int = 2 
    ENABLE_NOTIFICATIONS: bool = True 
    NOTIFICATION_RETRY_ATTEMPTS: int = 3 
    
    MONITORING_LOGS_RETENTION_DAYS: int = 30
    NOTIFICATION_CACHE_HOURS: int = 24

    SERVER_STATUS_MODE: str = "disabled"
    SERVER_STATUS_EXTERNAL_URL: Optional[str] = None
    SERVER_STATUS_METRICS_URL: Optional[str] = None
    SERVER_STATUS_METRICS_USERNAME: Optional[str] = None
    SERVER_STATUS_METRICS_PASSWORD: Optional[str] = None
    SERVER_STATUS_METRICS_VERIFY_SSL: bool = True
    SERVER_STATUS_REQUEST_TIMEOUT: int = 10
    SERVER_STATUS_ITEMS_PER_PAGE: int = 10
    
    BASE_SUBSCRIPTION_PRICE: int = 50000
    AVAILABLE_SUBSCRIPTION_PERIODS: str = "14,30,60,90,180,360"
    AVAILABLE_RENEWAL_PERIODS: str = "30,90,180"
    PRICE_14_DAYS: int = 50000
    PRICE_30_DAYS: int = 99000
    PRICE_60_DAYS: int = 189000
    PRICE_90_DAYS: int = 269000
    PRICE_180_DAYS: int = 499000
    PRICE_360_DAYS: int = 899000

    PRICE_TRAFFIC_5GB: int = 2000
    PRICE_TRAFFIC_10GB: int = 3500
    PRICE_TRAFFIC_25GB: int = 7000
    PRICE_TRAFFIC_50GB: int = 11000
    PRICE_TRAFFIC_100GB: int = 15000
    PRICE_TRAFFIC_250GB: int = 17000
    PRICE_TRAFFIC_500GB: int = 19000
    PRICE_TRAFFIC_1000GB: int = 19500
    PRICE_TRAFFIC_UNLIMITED: int = 20000
    
    TRAFFIC_PACKAGES_CONFIG: str = ""

    PRICE_PER_DEVICE: int = 5000

    BASE_PROMO_GROUP_PERIOD_DISCOUNTS_ENABLED: bool = False
    BASE_PROMO_GROUP_PERIOD_DISCOUNTS: str = ""

    TRAFFIC_SELECTION_MODE: str = "selectable" 
    FIXED_TRAFFIC_LIMIT_GB: int = 100 
    
    REFERRAL_MINIMUM_TOPUP_KOPEKS: int = 10000 
    REFERRAL_FIRST_TOPUP_BONUS_KOPEKS: int = 10000 
    REFERRAL_INVITER_BONUS_KOPEKS: int = 10000 
    REFERRAL_COMMISSION_PERCENT: int = 25 

    REFERRAL_NOTIFICATIONS_ENABLED: bool = True
    REFERRAL_NOTIFICATION_RETRY_ATTEMPTS: int = 3
    REFERRED_USER_REWARD: int = 0 
    
    AUTOPAY_WARNING_DAYS: str = "3,1"
    
    DEFAULT_AUTOPAY_DAYS_BEFORE: int = 3 
    MIN_BALANCE_FOR_AUTOPAY_KOPEKS: int = 10000  
    
    MONITORING_INTERVAL: int = 60
    INACTIVE_USER_DELETE_MONTHS: int = 3

    MAINTENANCE_MODE: bool = False
    MAINTENANCE_CHECK_INTERVAL: int = 30 
    MAINTENANCE_AUTO_ENABLE: bool = True 
    MAINTENANCE_MESSAGE: str = "🔧 Ведутся технические работы. Сервис временно недоступен. Попробуйте позже."
    
    TELEGRAM_STARS_ENABLED: bool = True
    TELEGRAM_STARS_RATE_RUB: float = 1.3
    
    TRIBUTE_ENABLED: bool = False
    TRIBUTE_API_KEY: Optional[str] = None
    TRIBUTE_DONATE_LINK: Optional[str] = None
    TRIBUTE_WEBHOOK_PATH: str = "/tribute-webhook"
    TRIBUTE_WEBHOOK_HOST: str = "0.0.0.0"
    TRIBUTE_WEBHOOK_PORT: int = 8081

    YOOKASSA_ENABLED: bool = False
    YOOKASSA_SHOP_ID: Optional[str] = None
    YOOKASSA_SECRET_KEY: Optional[str] = None
    YOOKASSA_RETURN_URL: Optional[str] = None
    YOOKASSA_DEFAULT_RECEIPT_EMAIL: Optional[str] = None
    YOOKASSA_VAT_CODE: int = 1
    YOOKASSA_SBP_ENABLED: bool = False 
    YOOKASSA_PAYMENT_MODE: str = "full_payment"
    YOOKASSA_PAYMENT_SUBJECT: str = "service"
    YOOKASSA_WEBHOOK_PATH: str = "/yookassa-webhook"
    YOOKASSA_WEBHOOK_HOST: str = "0.0.0.0"
    YOOKASSA_WEBHOOK_PORT: int = 8082
    YOOKASSA_WEBHOOK_SECRET: Optional[str] = None
    YOOKASSA_MIN_AMOUNT_KOPEKS: int = 5000
    YOOKASSA_MAX_AMOUNT_KOPEKS: int = 1000000
    YOOKASSA_QUICK_AMOUNT_SELECTION_ENABLED: bool = False
    PAYMENT_BALANCE_DESCRIPTION: str = "Пополнение баланса"
    PAYMENT_SUBSCRIPTION_DESCRIPTION: str = "Оплата подписки"
    PAYMENT_SERVICE_NAME: str = "Интернет-сервис"
    PAYMENT_BALANCE_TEMPLATE: str = "{service_name} - {description}"
    PAYMENT_SUBSCRIPTION_TEMPLATE: str = "{service_name} - {description}"

    CRYPTOBOT_ENABLED: bool = False
    CRYPTOBOT_API_TOKEN: Optional[str] = None
    CRYPTOBOT_WEBHOOK_SECRET: Optional[str] = None
    CRYPTOBOT_BASE_URL: str = "https://pay.crypt.bot"
    CRYPTOBOT_TESTNET: bool = False
    CRYPTOBOT_WEBHOOK_PATH: str = "/cryptobot-webhook"
    CRYPTOBOT_WEBHOOK_PORT: int = 8083
    CRYPTOBOT_DEFAULT_ASSET: str = "USDT"
    CRYPTOBOT_ASSETS: str = "USDT,TON,BTC,ETH"
    CRYPTOBOT_INVOICE_EXPIRES_HOURS: int = 24

    MULENPAY_ENABLED: bool = False
    MULENPAY_API_KEY: Optional[str] = None
    MULENPAY_SECRET_KEY: Optional[str] = None
    MULENPAY_SHOP_ID: Optional[int] = None
    MULENPAY_BASE_URL: str = "https://mulenpay.ru/api"
    MULENPAY_WEBHOOK_PATH: str = "/mulenpay-webhook"
    MULENPAY_DESCRIPTION: str = "Пополнение баланса"
    MULENPAY_LANGUAGE: str = "ru"
    MULENPAY_VAT_CODE: int = 0
    MULENPAY_PAYMENT_SUBJECT: int = 4
    MULENPAY_PAYMENT_MODE: int = 4
    MULENPAY_MIN_AMOUNT_KOPEKS: int = 10000
    MULENPAY_MAX_AMOUNT_KOPEKS: int = 10000000

    PAL24_ENABLED: bool = False
    PAL24_API_TOKEN: Optional[str] = None
    PAL24_SHOP_ID: Optional[str] = None
    PAL24_SIGNATURE_TOKEN: Optional[str] = None
    PAL24_BASE_URL: str = "https://pal24.pro/api/v1/"
    PAL24_WEBHOOK_PATH: str = "/pal24-webhook"
    PAL24_WEBHOOK_PORT: int = 8084
    PAL24_PAYMENT_DESCRIPTION: str = "Пополнение баланса"
    PAL24_MIN_AMOUNT_KOPEKS: int = 10000
    PAL24_MAX_AMOUNT_KOPEKS: int = 100000000
    PAL24_REQUEST_TIMEOUT: int = 30
    PAL24_SBP_BUTTON_TEXT: Optional[str] = None
    PAL24_CARD_BUTTON_TEXT: Optional[str] = None

    CONNECT_BUTTON_MODE: str = "guide"
    MINIAPP_CUSTOM_URL: str = ""
    MINIAPP_PURCHASE_URL: str = ""
    MINIAPP_SERVICE_NAME_EN: str = "Bedolaga VPN"
    MINIAPP_SERVICE_NAME_RU: str = "Bedolaga VPN"
    MINIAPP_SERVICE_DESCRIPTION_EN: str = "Secure & Fast Connection"
    MINIAPP_SERVICE_DESCRIPTION_RU: str = "Безопасное и быстрое подключение"
    CONNECT_BUTTON_HAPP_DOWNLOAD_ENABLED: bool = False
    HAPP_CRYPTOLINK_REDIRECT_TEMPLATE: Optional[str] = None
    HAPP_DOWNLOAD_LINK_IOS: Optional[str] = None
    HAPP_DOWNLOAD_LINK_ANDROID: Optional[str] = None
    HAPP_DOWNLOAD_LINK_MACOS: Optional[str] = None
    HAPP_DOWNLOAD_LINK_WINDOWS: Optional[str] = None
    HAPP_DOWNLOAD_LINK_PC: Optional[str] = None
    HIDE_SUBSCRIPTION_LINK: bool = False
    ENABLE_LOGO_MODE: bool = True
    LOGO_FILE: str = "vpn_logo.png"
    SKIP_RULES_ACCEPT: bool = False
    SKIP_REFERRAL_CODE: bool = False

    DEFAULT_LANGUAGE: str = "ru"
    AVAILABLE_LANGUAGES: str = "ru,en"
    LANGUAGE_SELECTION_ENABLED: bool = True
    
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = "logs/bot.log"
    
    DEBUG: bool = False
    WEBHOOK_URL: Optional[str] = None
    WEBHOOK_PATH: str = "/webhook"

    WEB_API_ENABLED: bool = False
    WEB_API_HOST: str = "0.0.0.0"
    WEB_API_PORT: int = 8080
    WEB_API_WORKERS: int = 1
    WEB_API_ALLOWED_ORIGINS: str = "*"
    WEB_API_DOCS_ENABLED: bool = False
    WEB_API_TITLE: str = "Remnawave Bot Admin API"
    WEB_API_VERSION: str = "1.0.0"
    WEB_API_DEFAULT_TOKEN: Optional[str] = None
    WEB_API_DEFAULT_TOKEN_NAME: str = "Bootstrap Token"
    WEB_API_TOKEN_HASH_ALGORITHM: str = "sha256"
    WEB_API_REQUEST_LOGGING: bool = True
    
    APP_CONFIG_PATH: str = "app-config.json"
    ENABLE_DEEP_LINKS: bool = True
    APP_CONFIG_CACHE_TTL: int = 3600 

    VERSION_CHECK_ENABLED: bool = True
    VERSION_CHECK_REPO: str = "fr1ngg/remnawave-bedolaga-telegram-bot"
    VERSION_CHECK_INTERVAL_HOURS: int = 1

    BACKUP_AUTO_ENABLED: bool = True
    BACKUP_INTERVAL_HOURS: int = 24
    BACKUP_TIME: str = "03:00"
    BACKUP_MAX_KEEP: int = 7
    BACKUP_COMPRESSION: bool = True
    BACKUP_INCLUDE_LOGS: bool = False
    BACKUP_LOCATION: str = "/app/data/backups"
    BACKUP_SEND_ENABLED: bool = False
    BACKUP_SEND_CHAT_ID: Optional[str] = None
    BACKUP_SEND_TOPIC_ID: Optional[int] = None

    @field_validator('SERVER_STATUS_MODE', mode='before')
    @classmethod
    def normalize_server_status_mode(cls, value: Optional[str]) -> str:
        if not value:
            return "disabled"

        normalized = str(value).strip().lower()
        aliases = {
            "off": "disabled",
            "none": "disabled",
            "disabled": "disabled",
            "external": "external_link",
            "link": "external_link",
            "url": "external_link",
            "external_link": "external_link",
            "miniapp": "external_link_miniapp",
            "mini_app": "external_link_miniapp",
            "mini-app": "external_link_miniapp",
            "webapp": "external_link_miniapp",
            "web_app": "external_link_miniapp",
            "web-app": "external_link_miniapp",
            "external_link_miniapp": "external_link_miniapp",
            "xray": "xray",
            "xraychecker": "xray",
            "xray_metrics": "xray",
            "metrics": "xray",
        }

        mode = aliases.get(normalized, normalized)
        if mode not in {"disabled", "external_link", "external_link_miniapp", "xray"}:
            raise ValueError(
                "SERVER_STATUS_MODE must be one of: disabled, external_link, external_link_miniapp, xray"
            )
        return mode

    @field_validator('SERVER_STATUS_ITEMS_PER_PAGE', mode='before')
    @classmethod
    def ensure_positive_server_status_page_size(cls, value: Optional[int]) -> int:
        try:
            if value is None:
                return 10
            value_int = int(value)
            return max(1, value_int)
        except (TypeError, ValueError):
            return 10

    @field_validator('SERVER_STATUS_REQUEST_TIMEOUT', mode='before')
    @classmethod
    def ensure_positive_server_status_timeout(cls, value: Optional[int]) -> int:
        try:
            if value is None:
                return 10
            value_int = int(value)
            return max(1, value_int)
        except (TypeError, ValueError):
            return 10
    
    @field_validator('LOG_FILE', mode='before')
    @classmethod
    def ensure_log_dir(cls, v):
        log_path = Path(v)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        return str(log_path)
    
    def get_database_url(self) -> str:
        if self.DATABASE_URL and self.DATABASE_URL.strip():
            return self.DATABASE_URL
            
        mode = self.DATABASE_MODE.lower()
        
        if mode == "sqlite":
            return self._get_sqlite_url()
        elif mode == "postgresql":
            return self._get_postgresql_url()
        elif mode == "auto":
            if os.getenv("DOCKER_ENV") == "true" or os.path.exists("/.dockerenv"):
                return self._get_postgresql_url()
            else:
                return self._get_sqlite_url()
        else:
            return self._get_auto_database_url()
    
    def _get_sqlite_url(self) -> str:
        sqlite_path = Path(self.SQLITE_PATH)
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{sqlite_path.absolute()}"
    
    def _get_postgresql_url(self) -> str:
        return (f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
                f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}")
    
    def _get_auto_database_url(self) -> str:
        if (os.getenv("DOCKER_ENV") == "true" or 
            os.path.exists("/.dockerenv")):
            return self._get_postgresql_url()
        else:
            return self._get_sqlite_url()
    
    def is_postgresql(self) -> bool:
        """Проверяет, используется ли PostgreSQL"""
        return "postgresql" in self.get_database_url()
    
    def is_sqlite(self) -> bool:
        """Проверяет, используется ли SQLite"""
        return "sqlite" in self.get_database_url()
    
    def is_admin(self, user_id: int) -> bool:
        return user_id in self.get_admin_ids()
    
    def get_admin_ids(self) -> List[int]:
        try:
            admin_ids = self.ADMIN_IDS
            
            if isinstance(admin_ids, str):
                if not admin_ids.strip():
                    return []
                return [int(x.strip()) for x in admin_ids.split(',') if x.strip()]
            
            return []
            
        except (ValueError, AttributeError):
            return []

    def get_remnawave_auth_params(self) -> Dict[str, Optional[str]]:
        return {
            "base_url": self.REMNAWAVE_API_URL,
            "api_key": self.REMNAWAVE_API_KEY,
            "secret_key": self.REMNAWAVE_SECRET_KEY,
            "username": self.REMNAWAVE_USERNAME,
            "password": self.REMNAWAVE_PASSWORD,
            "auth_type": self.REMNAWAVE_AUTH_TYPE
        }

    def get_pal24_sbp_button_text(self, fallback: str) -> str:
        value = (self.PAL24_SBP_BUTTON_TEXT or "").strip()
        return value or fallback

    def get_pal24_card_button_text(self, fallback: str) -> str:
        value = (self.PAL24_CARD_BUTTON_TEXT or "").strip()
        return value or fallback
    
    def get_remnawave_user_delete_mode(self) -> str:
        """Возвращает режим удаления пользователей: 'delete' или 'disable'"""
        mode = self.REMNAWAVE_USER_DELETE_MODE.lower().strip()
        return mode if mode in ["delete", "disable"] else "delete"

    def format_remnawave_user_description(
        self,
        *,
        full_name: str,
        username: Optional[str],
        telegram_id: int
    ) -> str:
        template = self.REMNAWAVE_USER_DESCRIPTION_TEMPLATE or "Bot user: {full_name} {username}"
        template_for_formatting = template.replace("@{username}", "{username}")

        username_clean = (username or "").lstrip("@")
        values = defaultdict(str, {
            "full_name": full_name,
            "username": f"@{username_clean}" if username_clean else "",
            "username_clean": username_clean,
            "telegram_id": str(telegram_id)
        })

        description = template_for_formatting.format_map(values)

        if not username_clean:
            description = re.sub(r'@(?=\W|$)', '', description)
            description = re.sub(r'\(\s*\)', '', description)

        description = re.sub(r'\s+', ' ', description).strip()
        return description
    
    def get_autopay_warning_days(self) -> List[int]:
        try:
            days = self.AUTOPAY_WARNING_DAYS
            if isinstance(days, str):
                if not days.strip():
                    return [3, 1]
                return [int(x.strip()) for x in days.split(',') if x.strip()]
            return [3, 1]
        except (ValueError, AttributeError):
            return [3, 1]
    
    def get_available_languages(self) -> List[str]:
        try:
            langs = self.AVAILABLE_LANGUAGES
            if isinstance(langs, str):
                if not langs.strip():
                    return ["ru", "en"]
                return [x.strip() for x in langs.split(',') if x.strip()]
            return ["ru", "en"]
        except AttributeError:
            return ["ru", "en"]

    def is_language_selection_enabled(self) -> bool:
        return bool(getattr(self, "LANGUAGE_SELECTION_ENABLED", True))

    def format_price(self, price_kopeks: int) -> str:
        rubles = price_kopeks // 100
        return f"{rubles} ₽"

    def get_reports_chat_id(self) -> Optional[str]:
        if self.ADMIN_REPORTS_CHAT_ID:
            return self.ADMIN_REPORTS_CHAT_ID
        return self.ADMIN_NOTIFICATIONS_CHAT_ID

    def get_reports_topic_id(self) -> Optional[int]:
        return self.ADMIN_REPORTS_TOPIC_ID or None

    def get_reports_send_time(self) -> Optional[time]:
        value = self.ADMIN_REPORTS_SEND_TIME
        if not value:
            return None

        try:
            hours_str, minutes_str = value.strip().split(":", 1)
            hours = int(hours_str)
            minutes = int(minutes_str)
            if not (0 <= hours <= 23 and 0 <= minutes <= 59):
                raise ValueError
            return time(hour=hours, minute=minutes)
        except (ValueError, AttributeError):
            logging.getLogger(__name__).warning(
                "Некорректное значение ADMIN_REPORTS_SEND_TIME: %s", value
            )
            return None
    
    def kopeks_to_rubles(self, kopeks: int) -> float:
        return kopeks / 100
    
    def rubles_to_kopeks(self, rubles: float) -> int:
        return int(rubles * 100)
    
    def get_trial_warning_hours(self) -> int:
        return self.TRIAL_WARNING_HOURS
    
    def is_notifications_enabled(self) -> bool:
        return self.ENABLE_NOTIFICATIONS
    
    def get_app_config_path(self) -> str:
        if os.path.isabs(self.APP_CONFIG_PATH):
            return self.APP_CONFIG_PATH
        
        project_root = Path(__file__).parent.parent
        return str(project_root / self.APP_CONFIG_PATH)
    
    def is_deep_links_enabled(self) -> bool:
        return self.ENABLE_DEEP_LINKS

    def get_miniapp_branding(self) -> Dict[str, Dict[str, Optional[str]]]:
        def _clean(value: Optional[str]) -> Optional[str]:
            if value is None:
                return None
            value_str = str(value).strip()
            return value_str or None

        name_en = _clean(self.MINIAPP_SERVICE_NAME_EN)
        name_ru = _clean(self.MINIAPP_SERVICE_NAME_RU)
        desc_en = _clean(self.MINIAPP_SERVICE_DESCRIPTION_EN)
        desc_ru = _clean(self.MINIAPP_SERVICE_DESCRIPTION_RU)

        default_name = name_en or name_ru or "RemnaWave VPN"
        default_description = desc_en or desc_ru or "Secure & Fast Connection"

        return {
            "service_name": {
                "default": default_name,
                "en": name_en,
                "ru": name_ru,
            },
            "service_description": {
                "default": default_description,
                "en": desc_en,
                "ru": desc_ru,
            },
        }
    
    def get_app_config_cache_ttl(self) -> int:
        return self.APP_CONFIG_CACHE_TTL
    
    def is_traffic_selectable(self) -> bool:
        return self.TRAFFIC_SELECTION_MODE.lower() == "selectable"
    
    def is_traffic_fixed(self) -> bool:
        return self.TRAFFIC_SELECTION_MODE.lower() == "fixed"
    
    def get_fixed_traffic_limit(self) -> int:
        return self.FIXED_TRAFFIC_LIMIT_GB
    
    def is_yookassa_enabled(self) -> bool:
        return (self.YOOKASSA_ENABLED and 
                self.YOOKASSA_SHOP_ID is not None and 
                self.YOOKASSA_SECRET_KEY is not None)
    
    def get_yookassa_return_url(self) -> str:
        if self.YOOKASSA_RETURN_URL:
            return self.YOOKASSA_RETURN_URL
        elif self.WEBHOOK_URL:
            return f"{self.WEBHOOK_URL}/payment-success"
        return "https://t.me/"

    def is_cryptobot_enabled(self) -> bool:
        return (self.CRYPTOBOT_ENABLED and
                self.CRYPTOBOT_API_TOKEN is not None)

    def is_mulenpay_enabled(self) -> bool:
        return (
            self.MULENPAY_ENABLED
            and self.MULENPAY_API_KEY is not None
            and self.MULENPAY_SECRET_KEY is not None
            and self.MULENPAY_SHOP_ID is not None
        )

    def is_pal24_enabled(self) -> bool:
        return (
            self.PAL24_ENABLED
            and self.PAL24_API_TOKEN is not None
            and self.PAL24_SHOP_ID is not None
        )

    def get_cryptobot_base_url(self) -> str:
        if self.CRYPTOBOT_TESTNET:
            return "https://testnet-pay.crypt.bot"
        return self.CRYPTOBOT_BASE_URL
    
    def get_cryptobot_assets(self) -> List[str]:
        try:
            assets = self.CRYPTOBOT_ASSETS.strip()
            if not assets:
                return ["USDT", "TON"]
            return [asset.strip() for asset in assets.split(',') if asset.strip()]
        except (ValueError, AttributeError):
            return ["USDT", "TON"]
    
    def get_cryptobot_invoice_expires_seconds(self) -> int:
        return self.CRYPTOBOT_INVOICE_EXPIRES_HOURS * 3600

    def is_happ_cryptolink_mode(self) -> bool:
        return self.CONNECT_BUTTON_MODE == "happ_cryptolink"

    def is_happ_download_button_enabled(self) -> bool:
        return self.is_happ_cryptolink_mode() and self.CONNECT_BUTTON_HAPP_DOWNLOAD_ENABLED

    def should_hide_subscription_link(self) -> bool:
        """Returns True when subscription links must be hidden from the interface."""

        if self.is_happ_cryptolink_mode():
            return False
        return self.HIDE_SUBSCRIPTION_LINK

    def get_happ_cryptolink_redirect_template(self) -> Optional[str]:
        template = (self.HAPP_CRYPTOLINK_REDIRECT_TEMPLATE or "").strip()
        return template or None

    def get_happ_download_link(self, platform: str) -> Optional[str]:
        platform_key = platform.lower()

        if platform_key == "pc":
            platform_key = "windows"

        links = {
            "ios": (self.HAPP_DOWNLOAD_LINK_IOS or "").strip(),
            "android": (self.HAPP_DOWNLOAD_LINK_ANDROID or "").strip(),
            "macos": (self.HAPP_DOWNLOAD_LINK_MACOS or "").strip(),
            "windows": (
                (self.HAPP_DOWNLOAD_LINK_WINDOWS or "").strip()
                or (self.HAPP_DOWNLOAD_LINK_PC or "").strip()
            ),
        }
        link = links.get(platform_key)
        return link if link else None

    def is_maintenance_mode(self) -> bool:
        return self.MAINTENANCE_MODE
    
    def get_maintenance_message(self) -> str:
        return self.MAINTENANCE_MESSAGE
    
    def get_maintenance_check_interval(self) -> int:
        return self.MAINTENANCE_CHECK_INTERVAL

    def is_base_promo_group_period_discount_enabled(self) -> bool:
        return self.BASE_PROMO_GROUP_PERIOD_DISCOUNTS_ENABLED

    def get_base_promo_group_period_discounts(self) -> Dict[int, int]:
        try:
            config_str = (self.BASE_PROMO_GROUP_PERIOD_DISCOUNTS or "").strip()
            if not config_str:
                return {}

            discounts: Dict[int, int] = {}
            for part in config_str.split(','):
                part = part.strip()
                if not part:
                    continue

                period_and_discount = part.split(':')
                if len(period_and_discount) != 2:
                    continue

                period_str, discount_str = period_and_discount
                try:
                    period_days = int(period_str.strip())
                    discount_percent = int(discount_str.strip())
                except ValueError:
                    continue

                discounts[period_days] = max(0, min(100, discount_percent))

            return discounts
        except Exception:
            return {}

    def get_base_promo_group_period_discount(self, period_days: Optional[int]) -> int:
        if not period_days or not self.is_base_promo_group_period_discount_enabled():
            return 0

        discounts = self.get_base_promo_group_period_discounts()
        return discounts.get(period_days, 0)

    def is_maintenance_auto_enable(self) -> bool:
        return self.MAINTENANCE_AUTO_ENABLE

    def get_available_subscription_periods(self) -> List[int]:
        try:
            periods_str = self.AVAILABLE_SUBSCRIPTION_PERIODS
            if not periods_str.strip():
                return [30, 90, 180] 
            
            periods = []
            for period_str in periods_str.split(','):
                period_str = period_str.strip()
                if period_str:
                    period = int(period_str)
                    if hasattr(self, f'PRICE_{period}_DAYS'):
                        periods.append(period)
            
            return periods if periods else [30, 90, 180]
            
        except (ValueError, AttributeError):
            return [30, 90, 180]
    
    def get_available_renewal_periods(self) -> List[int]:
        try:
            periods_str = self.AVAILABLE_RENEWAL_PERIODS
            if not periods_str.strip():
                return [30, 90, 180] 
            
            periods = []
            for period_str in periods_str.split(','):
                period_str = period_str.strip()
                if period_str:
                    period = int(period_str)
                    if hasattr(self, f'PRICE_{period}_DAYS'):
                        periods.append(period)
            
            return periods if periods else [30, 90, 180]
            
        except (ValueError, AttributeError):
            return [30, 90, 180]

    def get_balance_payment_description(self, amount_kopeks: int) -> str:
        return self.PAYMENT_BALANCE_TEMPLATE.format(
            service_name=self.PAYMENT_SERVICE_NAME,
            description=f"{self.PAYMENT_BALANCE_DESCRIPTION} на {self.format_price(amount_kopeks)}"
        )
    
    def get_subscription_payment_description(self, period_days: int, amount_kopeks: int) -> str:
        return self.PAYMENT_SUBSCRIPTION_TEMPLATE.format(
            service_name=self.PAYMENT_SERVICE_NAME,
            description=f"{self.PAYMENT_SUBSCRIPTION_DESCRIPTION} на {period_days} дней"
        )
    
    def get_custom_payment_description(self, description: str) -> str:
        return self.PAYMENT_BALANCE_TEMPLATE.format(
            service_name=self.PAYMENT_SERVICE_NAME,
            description=description
        )

    def get_stars_rate(self) -> float:
        return self.TELEGRAM_STARS_RATE_RUB
    
    def stars_to_rubles(self, stars: int) -> float:
        return stars * self.get_stars_rate()
    
    def rubles_to_stars(self, rubles: float) -> int:
        return max(1, int(rubles / self.get_stars_rate()))

    def get_admin_notifications_chat_id(self) -> Optional[int]:
        if not self.ADMIN_NOTIFICATIONS_CHAT_ID:
            return None
        
        try:
            return int(self.ADMIN_NOTIFICATIONS_CHAT_ID)
        except (ValueError, TypeError):
            return None
    
    def is_admin_notifications_enabled(self) -> bool:
        return (self.ADMIN_NOTIFICATIONS_ENABLED and
                self.get_admin_notifications_chat_id() is not None)

    def get_backup_send_chat_id(self) -> Optional[int]:
        if not self.BACKUP_SEND_CHAT_ID:
            return None

        try:
            return int(self.BACKUP_SEND_CHAT_ID)
        except (ValueError, TypeError):
            return None

    def is_backup_send_enabled(self) -> bool:
        return (self.BACKUP_SEND_ENABLED and
                self.get_backup_send_chat_id() is not None)

    def get_referral_settings(self) -> Dict:
        return {
            "minimum_topup_kopeks": self.REFERRAL_MINIMUM_TOPUP_KOPEKS,
            "first_topup_bonus_kopeks": self.REFERRAL_FIRST_TOPUP_BONUS_KOPEKS,
            "inviter_bonus_kopeks": self.REFERRAL_INVITER_BONUS_KOPEKS,
            "commission_percent": self.REFERRAL_COMMISSION_PERCENT,
            "notifications_enabled": self.REFERRAL_NOTIFICATIONS_ENABLED,
            "referred_user_reward": self.REFERRED_USER_REWARD
        }
    
    def is_referral_notifications_enabled(self) -> bool:
        return self.REFERRAL_NOTIFICATIONS_ENABLED
    
    def get_traffic_packages(self) -> List[Dict]:
        import logging
        logger = logging.getLogger(__name__)
        
        try:
            packages = []
            config_str = self.TRAFFIC_PACKAGES_CONFIG.strip()
            
            logger.info(f"CONFIG STRING: '{config_str}'")
            
            if not config_str:
                logger.info("CONFIG EMPTY, USING FALLBACK")
                return self._get_fallback_traffic_packages()
            
            logger.info("PARSING CONFIG...")
            
            for package_config in config_str.split(','):
                package_config = package_config.strip()
                if not package_config:
                    continue
                    
                parts = package_config.split(':')
                if len(parts) != 3:
                    continue
                    
                try:
                    gb = int(parts[0])
                    price = int(parts[1])
                    enabled = parts[2].lower() == 'true'
                    
                    packages.append({
                        "gb": gb,
                        "price": price,
                        "enabled": enabled
                    })
                except ValueError:
                    continue
            
            logger.info(f"PARSED {len(packages)} packages from config")
            return packages if packages else self._get_fallback_traffic_packages()
            
        except Exception as e:
            logger.info(f"ERROR PARSING CONFIG: {e}")
            return self._get_fallback_traffic_packages()

    def is_version_check_enabled(self) -> bool:
        return self.VERSION_CHECK_ENABLED
    
    def get_version_check_repo(self) -> str:
        return self.VERSION_CHECK_REPO
    
    def get_version_check_interval(self) -> int:
        return self.VERSION_CHECK_INTERVAL_HOURS
    
    def _get_fallback_traffic_packages(self) -> List[Dict]:
        try:
            if self.TRAFFIC_PACKAGES_CONFIG.strip():
                packages = []
                for package_config in self.TRAFFIC_PACKAGES_CONFIG.split(','):
                    package_config = package_config.strip()
                    if not package_config:
                        continue
                        
                    parts = package_config.split(':')
                    if len(parts) != 3:
                        continue
                        
                    try:
                        gb = int(parts[0])
                        price = int(parts[1])
                        enabled = parts[2].lower() == 'true'
                        
                        packages.append({
                            "gb": gb,
                            "price": price,
                            "enabled": enabled
                        })
                    except ValueError:
                        continue
                
                if packages:
                    return packages
        except Exception as e:
            pass
        
        return [
            {"gb": 5, "price": self.PRICE_TRAFFIC_5GB, "enabled": True},
            {"gb": 10, "price": self.PRICE_TRAFFIC_10GB, "enabled": True},
            {"gb": 25, "price": self.PRICE_TRAFFIC_25GB, "enabled": True},
            {"gb": 50, "price": self.PRICE_TRAFFIC_50GB, "enabled": True},
            {"gb": 100, "price": self.PRICE_TRAFFIC_100GB, "enabled": True},
            {"gb": 250, "price": self.PRICE_TRAFFIC_250GB, "enabled": True},
            {"gb": 500, "price": self.PRICE_TRAFFIC_500GB, "enabled": True},
            {"gb": 1000, "price": self.PRICE_TRAFFIC_1000GB, "enabled": True},
            {"gb": 0, "price": self.PRICE_TRAFFIC_UNLIMITED, "enabled": True}, 
        ]
    
    def get_traffic_price(self, gb: Optional[int]) -> int:
        packages = self.get_traffic_packages()
        enabled_packages = [pkg for pkg in packages if pkg["enabled"]]

        if not enabled_packages:
            return 0

        if gb is None:
            gb = 0

        for package in enabled_packages:
            if package["gb"] == gb:
                return package["price"]

        unlimited_package = next((pkg for pkg in enabled_packages if pkg["gb"] == 0), None)

        if gb <= 0:
            return unlimited_package["price"] if unlimited_package else 0

        finite_packages = [pkg for pkg in enabled_packages if pkg["gb"] > 0]

        if not finite_packages:
            return unlimited_package["price"] if unlimited_package else 0

        max_package = max(finite_packages, key=lambda x: x["gb"])

        if gb >= max_package["gb"]:
            return unlimited_package["price"] if unlimited_package else max_package["price"]

        suitable_packages = [pkg for pkg in finite_packages if pkg["gb"] >= gb]

        if suitable_packages:
            nearest_package = min(suitable_packages, key=lambda x: x["gb"])
            return nearest_package["price"]

        return unlimited_package["price"] if unlimited_package else 0

    def _clean_support_contact(self) -> str:
        return (self.SUPPORT_USERNAME or "").strip()

    def get_support_contact_url(self) -> Optional[str]:
        contact = self._clean_support_contact()

        if not contact:
            return None

        if contact.startswith(("http://", "https://", "tg://")):
            return contact

        contact_without_prefix = contact.lstrip("@")

        if contact_without_prefix.startswith(("t.me/", "telegram.me/", "telegram.dog/")):
            return f"https://{contact_without_prefix}"

        if contact.startswith(("t.me/", "telegram.me/", "telegram.dog/")):
            return f"https://{contact}"

        if "." in contact_without_prefix:
            return f"https://{contact_without_prefix}"

        if contact_without_prefix:
            return f"https://t.me/{contact_without_prefix}"

        return None

    def get_support_contact_display(self) -> str:
        contact = self._clean_support_contact()

        if not contact:
            return ""

        if contact.startswith("@"):
            return contact

        if contact.startswith(("http://", "https://", "tg://")):
            return contact

        if contact.startswith(("t.me/", "telegram.me/", "telegram.dog/")):
            url = self.get_support_contact_url()
            return url if url else contact

        contact_without_prefix = contact.lstrip("@")

        if "." in contact_without_prefix:
            url = self.get_support_contact_url()
            return url if url else contact

        if re.fullmatch(r"[A-Za-z0-9_]{3,}", contact_without_prefix):
            return f"@{contact_without_prefix}"

        return contact

    def get_support_contact_display_html(self) -> str:
        return html.escape(self.get_support_contact_display())

    def get_server_status_mode(self) -> str:
        return self.SERVER_STATUS_MODE

    def is_server_status_enabled(self) -> bool:
        return self.get_server_status_mode() != "disabled"

    def get_server_status_external_url(self) -> Optional[str]:
        url = (self.SERVER_STATUS_EXTERNAL_URL or "").strip()
        return url or None

    def get_server_status_metrics_url(self) -> Optional[str]:
        url = (self.SERVER_STATUS_METRICS_URL or "").strip()
        return url or None

    def get_server_status_metrics_auth(self) -> Optional[tuple[str, str]]:
        username = (self.SERVER_STATUS_METRICS_USERNAME or "").strip()
        password_raw = self.SERVER_STATUS_METRICS_PASSWORD

        if not username:
            return None

        password = "" if password_raw is None else str(password_raw)
        return username, password

    def get_server_status_items_per_page(self) -> int:
        return max(1, self.SERVER_STATUS_ITEMS_PER_PAGE)

    def get_server_status_request_timeout(self) -> int:
        return max(1, self.SERVER_STATUS_REQUEST_TIMEOUT)

    def is_web_api_enabled(self) -> bool:
        return bool(self.WEB_API_ENABLED)

    def get_web_api_allowed_origins(self) -> list[str]:
        raw = (self.WEB_API_ALLOWED_ORIGINS or "").split(",")
        origins = [origin.strip() for origin in raw if origin.strip()]
        return origins or ["*"]

    def get_web_api_docs_config(self) -> Dict[str, Optional[str]]:
        if self.WEB_API_DOCS_ENABLED:
            return {
                "docs_url": "/docs",
                "redoc_url": "/redoc",
                "openapi_url": "/openapi.json",
            }

        return {"docs_url": None, "redoc_url": None, "openapi_url": None}

    def get_support_system_mode(self) -> str:
        mode = (self.SUPPORT_SYSTEM_MODE or "both").strip().lower()
        return mode if mode in {"tickets", "contact", "both"} else "both"
    
    def is_support_tickets_enabled(self) -> bool:
        return self.get_support_system_mode() in {"tickets", "both"}
    
    def is_support_contact_enabled(self) -> bool:
        return self.get_support_system_mode() in {"contact", "both"}
    
    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore"  
    }


settings = Settings()

_PERIOD_PRICE_FIELDS: Dict[int, str] = {
    14: "PRICE_14_DAYS",
    30: "PRICE_30_DAYS",
    60: "PRICE_60_DAYS",
    90: "PRICE_90_DAYS",
    180: "PRICE_180_DAYS",
    360: "PRICE_360_DAYS",
}


def refresh_period_prices() -> None:
    """Rebuild cached period price mapping using the latest settings."""

    PERIOD_PRICES.clear()
    PERIOD_PRICES.update(
        {
            days: getattr(settings, field_name, 0)
            for days, field_name in _PERIOD_PRICE_FIELDS.items()
        }
    )


PERIOD_PRICES: Dict[int, int] = {}
refresh_period_prices()

def get_traffic_prices() -> Dict[int, int]:
    packages = settings.get_traffic_packages()
    return {package["gb"]: package["price"] for package in packages}

TRAFFIC_PRICES = get_traffic_prices()

def refresh_traffic_prices():
    global TRAFFIC_PRICES
    TRAFFIC_PRICES = get_traffic_prices()

refresh_traffic_prices()

settings._original_database_url = settings.DATABASE_URL
settings.DATABASE_URL = settings.get_database_url()
