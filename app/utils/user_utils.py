import logging
import secrets
import string
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from sqlalchemy import select, func, and_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import User, ReferralEarning, Transaction, TransactionType

logger = logging.getLogger(__name__)


async def generate_unique_referral_code(db: AsyncSession, telegram_id: int) -> str:
    max_attempts = 10
    
    for _ in range(max_attempts):
        code = f"ref{''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(8))}"
        
        result = await db.execute(
            select(User).where(User.referral_code == code)
        )
        if not result.scalar_one_or_none():
            return code
    
    timestamp = str(int(datetime.utcnow().timestamp()))[-6:]
    return f"ref{timestamp}"


async def mark_user_as_had_paid_subscription(db: AsyncSession, user: User) -> bool:
    try:
        if user.has_had_paid_subscription:
            logger.debug(f"Пользователь {user.id} уже отмечен как имевший платную подписку")
            return True
        
        await db.execute(
            update(User)
            .where(User.id == user.id)
            .values(
                has_had_paid_subscription=True,
                updated_at=datetime.utcnow()
            )
        )
        
        await db.commit()
        logger.info(f"✅ Пользователь {user.id} отмечен как имевший платную подписку")
        return True
        
    except Exception as e:
        logger.error(f"Ошибка отметки пользователя {user.id} как имевшего платную подписку: {e}")
        try:
            await db.rollback()
        except Exception as rollback_error:
            logger.error(f"Ошибка отката транзакции: {rollback_error}")
        return False


async def get_user_referral_summary(db: AsyncSession, user_id: int) -> Dict:
    try:
        invited_count_result = await db.execute(
            select(func.count(User.id)).where(User.referred_by_id == user_id)
        )
        invited_count = invited_count_result.scalar() or 0
        
        referrals_result = await db.execute(
            select(User).where(User.referred_by_id == user_id)
        )
        referrals = referrals_result.scalars().all()
        
        paid_referrals_count = sum(1 for ref in referrals if ref.has_made_first_topup)
        
        total_earnings_result = await db.execute(
            select(func.coalesce(func.sum(ReferralEarning.amount_kopeks), 0))
            .where(ReferralEarning.user_id == user_id)
        )
        total_earned_kopeks = total_earnings_result.scalar() or 0
        
        month_ago = datetime.utcnow() - timedelta(days=30)
        month_earnings_result = await db.execute(
            select(func.coalesce(func.sum(ReferralEarning.amount_kopeks), 0))
            .where(
                and_(
                    ReferralEarning.user_id == user_id,
                    ReferralEarning.created_at >= month_ago
                )
            )
        )
        month_earned_kopeks = month_earnings_result.scalar() or 0
        
        recent_earnings_result = await db.execute(
            select(ReferralEarning)
            .options(selectinload(ReferralEarning.referral))
            .where(ReferralEarning.user_id == user_id)
            .order_by(ReferralEarning.created_at.desc())
            .limit(5)
        )
        recent_earnings_raw = recent_earnings_result.scalars().all()
        
        recent_earnings = []
        for earning in recent_earnings_raw:
            if earning.referral:
                recent_earnings.append({
                    'amount_kopeks': earning.amount_kopeks,
                    'reason': earning.reason,
                    'referral_name': earning.referral.full_name,
                    'created_at': earning.created_at
                })
        
        earnings_by_type = {}
        earnings_by_type_result = await db.execute(
            select(
                ReferralEarning.reason,
                func.count(ReferralEarning.id).label('count'),
                func.coalesce(func.sum(ReferralEarning.amount_kopeks), 0).label('total_amount')
            )
            .where(ReferralEarning.user_id == user_id)
            .group_by(ReferralEarning.reason)
        )
        
        for row in earnings_by_type_result:
            earnings_by_type[row.reason] = {
                'count': row.count,
                'total_amount_kopeks': row.total_amount
            }
        
        active_referrals_count = 0
        for referral in referrals:
            if referral.last_activity and referral.last_activity >= month_ago:
                active_referrals_count += 1
        
        return {
            'invited_count': invited_count,
            'paid_referrals_count': paid_referrals_count,
            'active_referrals_count': active_referrals_count,
            'total_earned_kopeks': total_earned_kopeks,
            'month_earned_kopeks': month_earned_kopeks,
            'recent_earnings': recent_earnings,
            'earnings_by_type': earnings_by_type,
            'conversion_rate': round((paid_referrals_count / invited_count * 100) if invited_count > 0 else 0, 1)
        }
        
    except Exception as e:
        logger.error(f"Ошибка получения статистики рефералов для пользователя {user_id}: {e}")
        return {
            'invited_count': 0,
            'paid_referrals_count': 0,
            'active_referrals_count': 0,
            'total_earned_kopeks': 0,
            'month_earned_kopeks': 0,
            'recent_earnings': [],
            'earnings_by_type': {},
            'conversion_rate': 0.0
        }


async def get_detailed_referral_list(db: AsyncSession, user_id: int, limit: int = 20, offset: int = 0) -> Dict:
    try:
        referrals_result = await db.execute(
            select(User)
            .where(User.referred_by_id == user_id)
            .order_by(User.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        referrals = referrals_result.scalars().all()
        
        total_count_result = await db.execute(
            select(func.count(User.id)).where(User.referred_by_id == user_id)
        )
        total_count = total_count_result.scalar() or 0
        
        detailed_referrals = []
        for referral in referrals:
            earnings_result = await db.execute(
                select(func.coalesce(func.sum(ReferralEarning.amount_kopeks), 0))
                .where(
                    and_(
                        ReferralEarning.user_id == user_id,
                        ReferralEarning.referral_id == referral.id
                    )
                )
            )
            total_earned_from_referral = earnings_result.scalar() or 0
            
            topups_result = await db.execute(
                select(func.count(Transaction.id))
                .where(
                    and_(
                        Transaction.user_id == referral.id,
                        Transaction.type == TransactionType.DEPOSIT.value,
                        Transaction.is_completed.is_(True)
                    )
                )
            )
            topups_count = topups_result.scalar() or 0
            
            days_since_registration = (datetime.utcnow() - referral.created_at).days
            
            days_since_activity = None
            if referral.last_activity:
                days_since_activity = (datetime.utcnow() - referral.last_activity).days
            
            detailed_referrals.append({
                'id': referral.id,
                'telegram_id': referral.telegram_id,
                'full_name': referral.full_name,
                'username': referral.username,
                'created_at': referral.created_at,
                'last_activity': referral.last_activity,
                'has_made_first_topup': referral.has_made_first_topup,
                'balance_kopeks': referral.balance_kopeks,
                'total_earned_kopeks': total_earned_from_referral,
                'topups_count': topups_count,
                'days_since_registration': days_since_registration,
                'days_since_activity': days_since_activity,
                'status': 'active' if days_since_activity is not None and days_since_activity <= 30 else 'inactive'
            })
        
        return {
            'referrals': detailed_referrals,
            'total_count': total_count,
            'has_next': offset + limit < total_count,
            'has_prev': offset > 0,
            'current_page': (offset // limit) + 1,
            'total_pages': (total_count + limit - 1) // limit
        }
        
    except Exception as e:
        logger.error(f"Ошибка получения списка рефералов для пользователя {user_id}: {e}")
        return {
            'referrals': [],
            'total_count': 0,
            'has_next': False,
            'has_prev': False,
            'current_page': 1,
            'total_pages': 1
        }


async def get_referral_analytics(db: AsyncSession, user_id: int) -> Dict:
    try:
        now = datetime.utcnow()
        periods = {
            'today': now.replace(hour=0, minute=0, second=0, microsecond=0),
            'week': now - timedelta(days=7),
            'month': now - timedelta(days=30),
            'quarter': now - timedelta(days=90)
        }
        
        earnings_by_period = {}
        for period_name, start_date in periods.items():
            result = await db.execute(
                select(func.coalesce(func.sum(ReferralEarning.amount_kopeks), 0))
                .where(
                    and_(
                        ReferralEarning.user_id == user_id,
                        ReferralEarning.created_at >= start_date
                    )
                )
            )
            earnings_by_period[period_name] = result.scalar() or 0
        
        top_referrals_result = await db.execute(
            select(
                ReferralEarning.referral_id,
                func.coalesce(func.sum(ReferralEarning.amount_kopeks), 0).label('total_earned'),
                func.count(ReferralEarning.id).label('earnings_count')
            )
            .where(ReferralEarning.user_id == user_id)
            .group_by(ReferralEarning.referral_id)
            .order_by(func.sum(ReferralEarning.amount_kopeks).desc())
            .limit(5)
        )
        
        top_referrals = []
        for row in top_referrals_result:
            referral_result = await db.execute(
                select(User).where(User.id == row.referral_id)
            )
            referral = referral_result.scalar_one_or_none()
            if referral:
                top_referrals.append({
                    'referral_name': referral.full_name,
                    'total_earned_kopeks': row.total_earned,
                    'earnings_count': row.earnings_count
                })
        
        return {
            'earnings_by_period': earnings_by_period,
            'top_referrals': top_referrals
        }
        
    except Exception as e:
        logger.error(f"Ошибка получения аналитики рефералов для пользователя {user_id}: {e}")
        return {
            'earnings_by_period': {
                'today': 0,
                'week': 0,
                'month': 0,
                'quarter': 0
            },
            'top_referrals': []
        }
