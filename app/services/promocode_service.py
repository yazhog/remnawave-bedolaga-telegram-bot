import logging
from datetime import datetime
from typing import Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.promocode import (
    get_promocode_by_code, use_promocode, check_user_promocode_usage,
    create_promocode_use, get_promocode_use_by_user_and_code
)
from app.database.crud.user import add_user_balance, get_user_by_id
from app.database.crud.subscription import extend_subscription, get_subscription_by_user_id
from app.database.models import PromoCodeType, SubscriptionStatus, User, PromoCode
from app.services.remnawave_service import RemnaWaveService
from app.services.subscription_service import SubscriptionService

logger = logging.getLogger(__name__)


class PromoCodeService:
    
    def __init__(self):
        self.remnawave_service = RemnaWaveService()
        self.subscription_service = SubscriptionService()
    
    async def activate_promocode(
        self,
        db: AsyncSession,
        user_id: int,
        code: str
    ) -> Dict[str, Any]:
        
        try:
            user = await get_user_by_id(db, user_id)
            if not user:
                return {"success": False, "error": "user_not_found"}
            
            promocode = await get_promocode_by_code(db, code)
            if not promocode:
                return {"success": False, "error": "not_found"}
            
            if not promocode.is_valid:
                if promocode.current_uses >= promocode.max_uses:
                    return {"success": False, "error": "used"}
                else:
                    return {"success": False, "error": "expired"}
            
            existing_use = await check_user_promocode_usage(db, user_id, promocode.id)
            if existing_use:
                return {"success": False, "error": "already_used_by_user"}
            
            result_description = await self._apply_promocode_effects(db, user, promocode)

            if promocode.type == PromoCodeType.SUBSCRIPTION_DAYS.value and promocode.subscription_days > 0:
                from app.utils.user_utils import mark_user_as_had_paid_subscription
                await mark_user_as_had_paid_subscription(db, user)

                logger.info(f"🎯 Пользователь {user.telegram_id} получил платную подписку через промокод {code}")

            await create_promocode_use(db, promocode.id, user_id)

            promocode.current_uses += 1
            await db.commit()

            logger.info(f"✅ Пользователь {user.telegram_id} активировал промокод {code}")

            promocode_data = {
                "code": promocode.code,
                "type": promocode.type,
                "balance_bonus_kopeks": promocode.balance_bonus_kopeks,
                "subscription_days": promocode.subscription_days,
                "max_uses": promocode.max_uses,
                "current_uses": promocode.current_uses,
                "valid_until": promocode.valid_until,
            }

            return {
                "success": True,
                "description": result_description,
                "promocode": promocode_data,
            }
            
        except Exception as e:
            logger.error(f"Ошибка активации промокода {code} для пользователя {user_id}: {e}")
            await db.rollback()
            return {"success": False, "error": "server_error"}

    async def _apply_promocode_effects(self, db: AsyncSession, user: User, promocode: PromoCode) -> str:
        effects = []
        
        if promocode.balance_bonus_kopeks > 0:
            await add_user_balance(
                db, user, promocode.balance_bonus_kopeks,
                f"Бонус по промокоду {promocode.code}"
            )
            
            balance_bonus_rubles = promocode.balance_bonus_kopeks / 100
            effects.append(f"💰 Баланс пополнен на {balance_bonus_rubles}₽")
        
        if promocode.subscription_days > 0:
            from app.config import settings
            
            subscription = await get_subscription_by_user_id(db, user.id)
            
            if subscription:
                await extend_subscription(db, subscription, promocode.subscription_days)
                
                await self.subscription_service.update_remnawave_user(db, subscription)
                
                effects.append(f"⏰ Подписка продлена на {promocode.subscription_days} дней")
                logger.info(f"✅ Подписка пользователя {user.telegram_id} продлена на {promocode.subscription_days} дней в RemnaWave с текущими сквадами")
                
            else:
                from app.database.crud.subscription import create_paid_subscription
                
                trial_squads = []
                if hasattr(settings, 'TRIAL_SQUAD_UUID') and settings.TRIAL_SQUAD_UUID:
                    trial_squads = [settings.TRIAL_SQUAD_UUID]
                
                new_subscription = await create_paid_subscription(
                    db=db,
                    user_id=user.id,
                    duration_days=promocode.subscription_days,
                    traffic_limit_gb=0, 
                    device_limit=1,
                    connected_squads=trial_squads 
                )
                
                await self.subscription_service.create_remnawave_user(db, new_subscription)
                
                effects.append(f"🎉 Получена подписка на {promocode.subscription_days} дней")
                logger.info(f"✅ Создана новая подписка для пользователя {user.telegram_id} на {promocode.subscription_days} дней с триал сквадом {trial_squads}")
        
        if promocode.type == PromoCodeType.TRIAL_SUBSCRIPTION.value:
            from app.database.crud.subscription import create_trial_subscription
            from app.config import settings
            
            subscription = await get_subscription_by_user_id(db, user.id)
            
            if not subscription:
                trial_days = promocode.subscription_days if promocode.subscription_days > 0 else settings.TRIAL_DURATION_DAYS
                
                trial_subscription = await create_trial_subscription(
                    db, 
                    user.id, 
                    duration_days=trial_days 
                )
                
                await self.subscription_service.create_remnawave_user(db, trial_subscription)
                
                effects.append(f"🎁 Активирована тестовая подписка на {trial_days} дней")
                logger.info(f"✅ Создана триал подписка для пользователя {user.telegram_id} на {trial_days} дней")
            else:
                effects.append("ℹ️ У вас уже есть активная подписка")
        
        return "\n".join(effects) if effects else "✅ Промокод активирован"
