import json
import logging
from typing import Any, Optional, Union
from datetime import datetime, timedelta
import redis.asyncio as redis

from app.config import settings

logger = logging.getLogger(__name__)


class CacheService:
    
    def __init__(self):
        self.redis_client: Optional[redis.Redis] = None
        self._connected = False
    
    async def connect(self):
        try:
            self.redis_client = redis.from_url(settings.REDIS_URL)
            await self.redis_client.ping()
            self._connected = True
            logger.info("✅ Подключение к Redis кешу установлено")
        except Exception as e:
            logger.warning(f"⚠️ Не удалось подключиться к Redis: {e}")
            self._connected = False
    
    async def disconnect(self):
        if self.redis_client:
            await self.redis_client.close()
            self._connected = False
    
    async def get(self, key: str) -> Optional[Any]:
        if not self._connected:
            return None
        
        try:
            value = await self.redis_client.get(key)
            if value:
                return json.loads(value)
            return None
        except Exception as e:
            logger.error(f"Ошибка получения из кеша {key}: {e}")
            return None
    
    async def set(
        self, 
        key: str, 
        value: Any, 
        expire: Union[int, timedelta] = None
    ) -> bool:
        if not self._connected:
            return False
        
        try:
            serialized_value = json.dumps(value, default=str)
            
            if isinstance(expire, timedelta):
                expire = int(expire.total_seconds())
            
            await self.redis_client.set(key, serialized_value, ex=expire)
            return True
        except Exception as e:
            logger.error(f"Ошибка записи в кеш {key}: {e}")
            return False
    
    async def delete(self, key: str) -> bool:
        if not self._connected:
            return False

        try:
            deleted = await self.redis_client.delete(key)
            return deleted > 0
        except Exception as e:
            logger.error(f"Ошибка удаления из кеша {key}: {e}")
            return False

    async def delete_pattern(self, pattern: str) -> int:
        if not self._connected:
            return 0

        try:
            keys = await self.redis_client.keys(pattern)
            if not keys:
                return 0

            deleted = await self.redis_client.delete(*keys)
            return int(deleted)
        except Exception as e:
            logger.error(f"Ошибка удаления ключей по шаблону {pattern}: {e}")
            return 0
    
    async def exists(self, key: str) -> bool:
        if not self._connected:
            return False
        
        try:
            return await self.redis_client.exists(key)
        except Exception as e:
            logger.error(f"Ошибка проверки существования в кеше {key}: {e}")
            return False
    
    async def expire(self, key: str, seconds: int) -> bool:
        if not self._connected:
            return False
        
        try:
            return await self.redis_client.expire(key, seconds)
        except Exception as e:
            logger.error(f"Ошибка установки TTL для {key}: {e}")
            return False
    
    async def get_keys(self, pattern: str = "*") -> list:
        if not self._connected:
            return []
        
        try:
            keys = await self.redis_client.keys(pattern)
            return [key.decode() if isinstance(key, bytes) else key for key in keys]
        except Exception as e:
            logger.error(f"Ошибка получения ключей по паттерну {pattern}: {e}")
            return []
    
    async def flush_all(self) -> bool:
        if not self._connected:
            return False
        
        try:
            await self.redis_client.flushall()
            logger.info("🗑️ Кеш полностью очищен")
            return True
        except Exception as e:
            logger.error(f"Ошибка очистки кеша: {e}")
            return False
    
    async def increment(self, key: str, amount: int = 1) -> Optional[int]:
        if not self._connected:
            return None
        
        try:
            return await self.redis_client.incrby(key, amount)
        except Exception as e:
            logger.error(f"Ошибка инкремента {key}: {e}")
            return None
    
    async def set_hash(self, name: str, mapping: dict, expire: int = None) -> bool:
        if not self._connected:
            return False
        
        try:
            await self.redis_client.hset(name, mapping=mapping)
            if expire:
                await self.redis_client.expire(name, expire)
            return True
        except Exception as e:
            logger.error(f"Ошибка записи хеша {name}: {e}")
            return False
    
    async def get_hash(self, name: str, key: str = None) -> Optional[Union[dict, str]]:
        if not self._connected:
            return None
        
        try:
            if key:
                value = await self.redis_client.hget(name, key)
                return value.decode() if value else None
            else:
                hash_data = await self.redis_client.hgetall(name)
                return {k.decode(): v.decode() for k, v in hash_data.items()}
        except Exception as e:
            logger.error(f"Ошибка получения хеша {name}: {e}")
            return None


cache = CacheService()


def cache_key(*parts) -> str:
    return ":".join(str(part) for part in parts)


async def cached_function(key: str, expire: int = 300):
    def decorator(func):
        async def wrapper(*args, **kwargs):
            cache_result = await cache.get(key)
            if cache_result is not None:
                return cache_result
            
            result = await func(*args, **kwargs)
            await cache.set(key, result, expire)
            return result
        
        return wrapper
    return decorator


class UserCache:
    
    @staticmethod
    async def get_user_data(user_id: int) -> Optional[dict]:
        key = cache_key("user", user_id)
        return await cache.get(key)
    
    @staticmethod
    async def set_user_data(user_id: int, data: dict, expire: int = 3600) -> bool:
        key = cache_key("user", user_id)
        return await cache.set(key, data, expire)
    
    @staticmethod
    async def delete_user_data(user_id: int) -> bool:
        key = cache_key("user", user_id)
        return await cache.delete(key)
    
    @staticmethod
    async def get_user_session(user_id: int, session_key: str) -> Optional[Any]:
        key = cache_key("session", user_id, session_key)
        return await cache.get(key)
    
    @staticmethod
    async def set_user_session(
        user_id: int,
        session_key: str,
        data: Any,
        expire: int = 1800
    ) -> bool:
        key = cache_key("session", user_id, session_key)
        return await cache.set(key, data, expire)

    @staticmethod
    async def delete_user_session(user_id: int, session_key: str) -> bool:
        key = cache_key("session", user_id, session_key)
        return await cache.delete(key)


class SystemCache:
    
    @staticmethod
    async def get_system_stats() -> Optional[dict]:
        return await cache.get("system:stats")
    
    @staticmethod
    async def set_system_stats(stats: dict, expire: int = 300) -> bool:
        return await cache.set("system:stats", stats, expire)
    
    @staticmethod
    async def get_nodes_status() -> Optional[list]:
        return await cache.get("remnawave:nodes")
    
    @staticmethod
    async def set_nodes_status(nodes: list, expire: int = 60) -> bool:
        return await cache.set("remnawave:nodes", nodes, expire)
    
    @staticmethod
    async def get_daily_stats(date: str) -> Optional[dict]:
        key = cache_key("stats", "daily", date)
        return await cache.get(key)
    
    @staticmethod
    async def set_daily_stats(date: str, stats: dict) -> bool:
        key = cache_key("stats", "daily", date)
        return await cache.set(key, stats, 86400)  # 24 часа


class RateLimitCache:
    
    @staticmethod
    async def is_rate_limited(user_id: int, action: str, limit: int, window: int) -> bool:
        key = cache_key("rate_limit", user_id, action)
        current = await cache.get(key)
        
        if current is None:
            await cache.set(key, 1, window)
            return False
        
        if current >= limit:
            return True
        
        await cache.increment(key)
        return False
    
    @staticmethod
    async def reset_rate_limit(user_id: int, action: str) -> bool:
        key = cache_key("rate_limit", user_id, action)
        return await cache.delete(key)