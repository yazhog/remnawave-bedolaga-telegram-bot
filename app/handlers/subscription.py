import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Tuple, Optional

from aiogram import Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings, PERIOD_PRICES, get_traffic_prices
from app.database.crud.discount_offer import (
    get_offer_by_id,
    mark_offer_claimed,
)
from app.database.crud.promo_offer_template import get_promo_offer_template_by_id
from app.database.crud.subscription import (
    create_trial_subscription,
    create_paid_subscription, add_subscription_traffic, add_subscription_devices,
    update_subscription_autopay
)
from app.database.crud.transaction import create_transaction
from app.database.crud.user import subtract_user_balance
from app.database.models import (
    User, TransactionType, SubscriptionStatus,
    Subscription
)
from app.keyboards.inline import (
    get_subscription_keyboard, get_trial_keyboard,
    get_subscription_period_keyboard, get_traffic_packages_keyboard,
    get_countries_keyboard, get_devices_keyboard,
    get_subscription_confirm_keyboard, get_autopay_keyboard,
    get_autopay_days_keyboard, get_back_keyboard,
    get_add_traffic_keyboard,
    get_change_devices_keyboard, get_reset_traffic_confirm_keyboard,
    get_manage_countries_keyboard,
    get_device_selection_keyboard, get_connection_guide_keyboard,
    get_app_selection_keyboard, get_specific_app_keyboard,
    get_updated_subscription_settings_keyboard, get_insufficient_balance_keyboard,
    get_extend_subscription_keyboard_with_prices, get_confirm_change_devices_keyboard,
    get_devices_management_keyboard, get_device_management_help_keyboard,
    get_happ_cryptolink_keyboard,
    get_happ_download_platform_keyboard, get_happ_download_link_keyboard,
    get_happ_download_button_row,
    get_payment_methods_keyboard_with_cart,
    get_subscription_confirm_keyboard_with_cart,
    get_insufficient_balance_keyboard_with_cart
)
from app.localization.texts import get_texts
from app.services.admin_notification_service import AdminNotificationService
from app.services.remnawave_service import RemnaWaveService
from app.services.subscription_checkout_service import (
    clear_subscription_checkout_draft,
    get_subscription_checkout_draft,
    save_subscription_checkout_draft,
    should_offer_checkout_resume,
)
from app.services.subscription_service import SubscriptionService
from app.services.promo_offer_service import promo_offer_service
from app.states import SubscriptionStates
from app.utils.pagination import paginate_list
from app.utils.pricing_utils import (
    calculate_months_from_days,
    get_remaining_months,
    calculate_prorated_price,
    validate_pricing_calculation,
    format_period_description,
    apply_percentage_discount,
)
from app.utils.subscription_utils import (
    get_display_subscription_link,
    get_happ_cryptolink_redirect_link,
    convert_subscription_link_to_happ_scheme,
)
from app.utils.promo_offer import (
    build_promo_offer_hint,
    get_user_active_promo_discount_percent,
)

logger = logging.getLogger(__name__)

TRAFFIC_PRICES = get_traffic_prices()


class _SafeFormatDict(dict):
    def __missing__(self, key: str) -> str:  # pragma: no cover - defensive fallback
        return "{" + key + "}"


def _format_text_with_placeholders(template: str, values: Dict[str, Any]) -> str:
    if not isinstance(template, str):
        return template

    safe_values = _SafeFormatDict()
    safe_values.update(values)

    try:
        return template.format_map(safe_values)
    except Exception:  # pragma: no cover - defensive logging
        logger.warning("Failed to format template '%s' with values %s", template, values)
        return template


def _get_addon_discount_percent_for_user(
        user: Optional[User],
        category: str,
        period_days_hint: Optional[int] = None,
) -> int:
    if user is None:
        return 0

    promo_group = getattr(user, "promo_group", None)
    if promo_group is None:
        return 0

    if not getattr(promo_group, "apply_discounts_to_addons", True):
        return 0

    try:
        return user.get_promo_discount(category, period_days_hint)
    except AttributeError:
        return 0


def _apply_addon_discount(
        user: Optional[User],
        category: str,
        amount: int,
        period_days_hint: Optional[int] = None,
) -> Dict[str, int]:
    percent = _get_addon_discount_percent_for_user(user, category, period_days_hint)
    discounted_amount, discount_value = apply_percentage_discount(amount, percent)

    return {
        "discounted": discounted_amount,
        "discount": discount_value,
        "percent": percent,
    }


def _get_promo_offer_discount_percent(user: Optional[User]) -> int:
    return get_user_active_promo_discount_percent(user)


def _apply_promo_offer_discount(user: Optional[User], amount: int) -> Dict[str, int]:
    percent = _get_promo_offer_discount_percent(user)

    if amount <= 0 or percent <= 0:
        return {"discounted": amount, "discount": 0, "percent": 0}

    discounted, discount_value = apply_percentage_discount(amount, percent)
    return {"discounted": discounted, "discount": discount_value, "percent": percent}


async def _get_promo_offer_hint(
        db: AsyncSession,
        db_user: User,
        texts,
        percent: Optional[int] = None,
) -> Optional[str]:
    return await build_promo_offer_hint(db, db_user, texts, percent)


def _get_period_hint_from_subscription(subscription: Optional[Subscription]) -> Optional[int]:
    if not subscription:
        return None

    months_remaining = get_remaining_months(subscription.end_date)
    if months_remaining <= 0:
        return None

    return months_remaining * 30


def _apply_discount_to_monthly_component(
        amount_per_month: int,
        percent: int,
        months: int,
) -> Dict[str, int]:
    discounted_per_month, discount_per_month = apply_percentage_discount(amount_per_month, percent)

    return {
        "original_per_month": amount_per_month,
        "discounted_per_month": discounted_per_month,
        "discount_percent": max(0, min(100, percent)),
        "discount_per_month": discount_per_month,
        "total": discounted_per_month * months,
        "discount_total": discount_per_month * months,
    }


async def _prepare_subscription_summary(
        db_user: User,
        data: Dict[str, Any],
        texts,
) -> Tuple[str, Dict[str, Any]]:
    summary_data = dict(data)
    countries = await _get_available_countries(db_user.promo_group_id)

    months_in_period = calculate_months_from_days(summary_data['period_days'])
    period_display = format_period_description(summary_data['period_days'], db_user.language)

    base_price_original = PERIOD_PRICES[summary_data['period_days']]
    period_discount_percent = db_user.get_promo_discount(
        "period",
        summary_data['period_days'],
    )
    base_price, base_discount_total = apply_percentage_discount(
        base_price_original,
        period_discount_percent,
    )

    if settings.is_traffic_fixed():
        traffic_limit = settings.get_fixed_traffic_limit()
        traffic_price_per_month = settings.get_traffic_price(traffic_limit)
        final_traffic_gb = traffic_limit
    else:
        traffic_gb = summary_data.get('traffic_gb', 0)
        traffic_price_per_month = settings.get_traffic_price(traffic_gb)
        final_traffic_gb = traffic_gb

    traffic_discount_percent = db_user.get_promo_discount(
        "traffic",
        summary_data['period_days'],
    )
    traffic_component = _apply_discount_to_monthly_component(
        traffic_price_per_month,
        traffic_discount_percent,
        months_in_period,
    )
    total_traffic_price = traffic_component["total"]

    countries_price_per_month = 0
    selected_countries_names: List[str] = []
    selected_server_prices: List[int] = []
    server_monthly_prices: List[int] = []

    selected_country_ids = set(summary_data.get('countries', []))
    for country in countries:
        if country['uuid'] in selected_country_ids:
            server_price_per_month = country['price_kopeks']
            countries_price_per_month += server_price_per_month
            selected_countries_names.append(country['name'])
            server_monthly_prices.append(server_price_per_month)

    servers_discount_percent = db_user.get_promo_discount(
        "servers",
        summary_data['period_days'],
    )
    total_countries_price = 0
    total_servers_discount = 0
    discounted_servers_price_per_month = 0

    for server_price_per_month in server_monthly_prices:
        discounted_per_month, discount_per_month = apply_percentage_discount(
            server_price_per_month,
            servers_discount_percent,
        )
        total_price_for_server = discounted_per_month * months_in_period
        total_discount_for_server = discount_per_month * months_in_period

        discounted_servers_price_per_month += discounted_per_month
        total_countries_price += total_price_for_server
        total_servers_discount += total_discount_for_server
        selected_server_prices.append(total_price_for_server)

    devices_selected = summary_data.get('devices', settings.DEFAULT_DEVICE_LIMIT)
    additional_devices = max(0, devices_selected - settings.DEFAULT_DEVICE_LIMIT)
    devices_price_per_month = additional_devices * settings.PRICE_PER_DEVICE
    devices_discount_percent = db_user.get_promo_discount(
        "devices",
        summary_data['period_days'],
    )
    devices_component = _apply_discount_to_monthly_component(
        devices_price_per_month,
        devices_discount_percent,
        months_in_period,
    )
    total_devices_price = devices_component["total"]

    total_price = base_price + total_traffic_price + total_countries_price + total_devices_price

    discounted_monthly_additions = (
            traffic_component["discounted_per_month"]
            + discounted_servers_price_per_month
            + devices_component["discounted_per_month"]
    )

    is_valid = validate_pricing_calculation(
        base_price,
        discounted_monthly_additions,
        months_in_period,
        total_price,
    )

    if not is_valid:
        raise ValueError("Subscription price calculation validation failed")

    original_total_price = total_price
    promo_offer_component = _apply_promo_offer_discount(db_user, total_price)
    if promo_offer_component["discount"] > 0:
        total_price = promo_offer_component["discounted"]

    summary_data['total_price'] = total_price
    if promo_offer_component["discount"] > 0:
        summary_data['promo_offer_discount_percent'] = promo_offer_component["percent"]
        summary_data['promo_offer_discount_value'] = promo_offer_component["discount"]
        summary_data['total_price_before_promo_offer'] = original_total_price
    else:
        summary_data.pop('promo_offer_discount_percent', None)
        summary_data.pop('promo_offer_discount_value', None)
        summary_data.pop('total_price_before_promo_offer', None)
    summary_data['server_prices_for_period'] = selected_server_prices
    summary_data['months_in_period'] = months_in_period
    summary_data['base_price'] = base_price
    summary_data['base_price_original'] = base_price_original
    summary_data['base_discount_percent'] = period_discount_percent
    summary_data['base_discount_total'] = base_discount_total
    summary_data['final_traffic_gb'] = final_traffic_gb
    summary_data['traffic_price_per_month'] = traffic_price_per_month
    summary_data['traffic_discount_percent'] = traffic_component["discount_percent"]
    summary_data['traffic_discount_total'] = traffic_component["discount_total"]
    summary_data['traffic_discounted_price_per_month'] = traffic_component["discounted_per_month"]
    summary_data['total_traffic_price'] = total_traffic_price
    summary_data['servers_price_per_month'] = countries_price_per_month
    summary_data['countries_price_per_month'] = countries_price_per_month
    summary_data['servers_discount_percent'] = servers_discount_percent
    summary_data['servers_discount_total'] = total_servers_discount
    summary_data['servers_discounted_price_per_month'] = discounted_servers_price_per_month
    summary_data['total_servers_price'] = total_countries_price
    summary_data['total_countries_price'] = total_countries_price
    summary_data['devices_price_per_month'] = devices_price_per_month
    summary_data['devices_discount_percent'] = devices_component["discount_percent"]
    summary_data['devices_discount_total'] = devices_component["discount_total"]
    summary_data['devices_discounted_price_per_month'] = devices_component["discounted_per_month"]
    summary_data['total_devices_price'] = total_devices_price
    summary_data['discounted_monthly_additions'] = discounted_monthly_additions

    if settings.is_traffic_fixed():
        if final_traffic_gb == 0:
            traffic_display = "Безлимитный"
        else:
            traffic_display = f"{final_traffic_gb} ГБ"
    else:
        if summary_data.get('traffic_gb', 0) == 0:
            traffic_display = "Безлимитный"
        else:
            traffic_display = f"{summary_data.get('traffic_gb', 0)} ГБ"

    if base_discount_total > 0:
        base_line = (
            f"- Базовый период: <s>{texts.format_price(base_price_original)}</s> "
            f"{texts.format_price(base_price)}"
            f" (скидка {period_discount_percent}%:"
            f" -{texts.format_price(base_discount_total)})"
        )
    else:
        base_line = f"- Базовый период: {texts.format_price(base_price_original)}"

    details_lines = [base_line]

    if total_traffic_price > 0:
        traffic_line = (
            f"- Трафик: {texts.format_price(traffic_price_per_month)}/мес × {months_in_period}"
            f" = {texts.format_price(total_traffic_price)}"
        )
        if traffic_component["discount_total"] > 0:
            traffic_line += (
                f" (скидка {traffic_component['discount_percent']}%:"
                f" -{texts.format_price(traffic_component['discount_total'])})"
            )
        details_lines.append(traffic_line)
    if total_countries_price > 0:
        servers_line = (
            f"- Серверы: {texts.format_price(countries_price_per_month)}/мес × {months_in_period}"
            f" = {texts.format_price(total_countries_price)}"
        )
        if total_servers_discount > 0:
            servers_line += (
                f" (скидка {servers_discount_percent}%:"
                f" -{texts.format_price(total_servers_discount)})"
            )
        details_lines.append(servers_line)
    if total_devices_price > 0:
        devices_line = (
            f"- Доп. устройства: {texts.format_price(devices_price_per_month)}/мес × {months_in_period}"
            f" = {texts.format_price(total_devices_price)}"
        )
        if devices_component["discount_total"] > 0:
            devices_line += (
                f" (скидка {devices_component['discount_percent']}%:"
                f" -{texts.format_price(devices_component['discount_total'])})"
            )
        details_lines.append(devices_line)

    if promo_offer_component["discount"] > 0:
        details_lines.append(
            texts.t(
                "SUBSCRIPTION_SUMMARY_PROMO_DISCOUNT",
                "- Промо-предложение: -{amount} ({percent}% дополнительно)",
            ).format(
                amount=texts.format_price(promo_offer_component["discount"]),
                percent=promo_offer_component["percent"],
            )
        )

    details_text = "\n".join(details_lines)

    summary_text = (
        "📋 <b>Сводка заказа</b>\n\n"
        f"📅 <b>Период:</b> {period_display}\n"
        f"📊 <b>Трафик:</b> {traffic_display}\n"
        f"🌍 <b>Страны:</b> {', '.join(selected_countries_names)}\n"
        f"📱 <b>Устройства:</b> {devices_selected}\n\n"
        "💰 <b>Детализация стоимости:</b>\n"
        f"{details_text}\n\n"
        f"💎 <b>Общая стоимость:</b> {texts.format_price(total_price)}\n\n"
        "Подтверждаете покупку?"
    )

    return summary_text, summary_data


def _build_promo_group_discount_text(
        db_user: User,
        periods: Optional[List[int]] = None,
        texts=None,
) -> str:
    promo_group = getattr(db_user, "promo_group", None)

    if not promo_group:
        return ""

    if texts is None:
        texts = get_texts(db_user.language)

    service_lines: List[str] = []

    if promo_group.server_discount_percent > 0:
        service_lines.append(
            texts.PROMO_GROUP_DISCOUNT_SERVERS.format(
                percent=promo_group.server_discount_percent
            )
        )

    if promo_group.traffic_discount_percent > 0:
        service_lines.append(
            texts.PROMO_GROUP_DISCOUNT_TRAFFIC.format(
                percent=promo_group.traffic_discount_percent
            )
        )

    if promo_group.device_discount_percent > 0:
        service_lines.append(
            texts.PROMO_GROUP_DISCOUNT_DEVICES.format(
                percent=promo_group.device_discount_percent
            )
        )

    period_lines: List[str] = []

    period_candidates: set[int] = set(periods or [])

    raw_period_discounts = getattr(promo_group, "period_discounts", None)
    if isinstance(raw_period_discounts, dict):
        for key in raw_period_discounts.keys():
            try:
                period_candidates.add(int(key))
            except (TypeError, ValueError):
                continue

    for period_days in sorted(period_candidates):
        percent = promo_group.get_discount_percent("period", period_days)

        if percent <= 0:
            continue

        period_display = format_period_description(period_days, db_user.language)
        period_lines.append(
            texts.PROMO_GROUP_PERIOD_DISCOUNT_ITEM.format(
                period=period_display,
                percent=percent,
            )
        )

    if not service_lines and not period_lines:
        return ""

    lines: List[str] = [texts.PROMO_GROUP_DISCOUNTS_HEADER]

    if service_lines:
        lines.extend(service_lines)

    if period_lines:
        if service_lines:
            lines.append("")

        lines.append(texts.PROMO_GROUP_PERIOD_DISCOUNTS_HEADER)
        lines.extend(period_lines)

    return "\n".join(lines)


async def _build_subscription_period_prompt(
        db_user: User,
        texts,
        db: AsyncSession,
) -> str:
    base_text = texts.BUY_SUBSCRIPTION_START.rstrip()

    lines: List[str] = [base_text]

    promo_offer_hint = await _get_promo_offer_hint(db, db_user, texts)
    if promo_offer_hint:
        lines.extend(["", promo_offer_hint])

    promo_text = _build_promo_group_discount_text(
        db_user,
        settings.get_available_subscription_periods(),
        texts=texts,
    )

    if promo_text:
        lines.extend(["", promo_text])

    return "\n".join(lines) + "\n"


async def show_subscription_info(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    await db.refresh(db_user)

    texts = get_texts(db_user.language)
    subscription = db_user.subscription

    if not subscription:
        await callback.message.edit_text(
            texts.SUBSCRIPTION_NONE,
            reply_markup=get_back_keyboard(db_user.language)
        )
        await callback.answer()
        return

    from app.database.crud.subscription import check_and_update_subscription_status
    subscription = await check_and_update_subscription_status(db, subscription)

    subscription_service = SubscriptionService()
    await subscription_service.sync_subscription_usage(db, subscription)

    await db.refresh(subscription)

    current_time = datetime.utcnow()

    if subscription.status == "expired" or subscription.end_date <= current_time:
        actual_status = "expired"
        status_display = texts.t("SUBSCRIPTION_STATUS_EXPIRED", "Истекла")
        status_emoji = "🔴"
    elif subscription.status == "active" and subscription.end_date > current_time:
        if subscription.is_trial:
            actual_status = "trial_active"
            status_display = texts.t("SUBSCRIPTION_STATUS_TRIAL", "Тестовая")
            status_emoji = "🎯"
        else:
            actual_status = "paid_active"
            status_display = texts.t("SUBSCRIPTION_STATUS_ACTIVE", "Активна")
            status_emoji = "💎"
    else:
        actual_status = "unknown"
        status_display = texts.t("SUBSCRIPTION_STATUS_UNKNOWN", "Неизвестно")
        status_emoji = "❓"

    if subscription.end_date <= current_time:
        days_left = 0
        time_left_text = texts.t("SUBSCRIPTION_TIME_LEFT_EXPIRED", "истёк")
        warning_text = ""
    else:
        delta = subscription.end_date - current_time
        days_left = delta.days
        hours_left = delta.seconds // 3600

        if days_left > 1:
            time_left_text = texts.t("SUBSCRIPTION_TIME_LEFT_DAYS", "{days} дн.").format(days=days_left)
            warning_text = ""
        elif days_left == 1:
            time_left_text = texts.t("SUBSCRIPTION_TIME_LEFT_DAYS", "{days} дн.").format(days=days_left)
            warning_text = texts.t("SUBSCRIPTION_WARNING_TOMORROW", "\n⚠️ истекает завтра!")
        elif hours_left > 0:
            time_left_text = texts.t("SUBSCRIPTION_TIME_LEFT_HOURS", "{hours} ч.").format(hours=hours_left)
            warning_text = texts.t("SUBSCRIPTION_WARNING_TODAY", "\n⚠️ истекает сегодня!")
        else:
            minutes_left = (delta.seconds % 3600) // 60
            time_left_text = texts.t("SUBSCRIPTION_TIME_LEFT_MINUTES", "{minutes} мин.").format(
                minutes=minutes_left
            )
            warning_text = texts.t(
                "SUBSCRIPTION_WARNING_MINUTES",
                "\n🔴 истекает через несколько минут!",
            )

    subscription_type = (
        texts.t("SUBSCRIPTION_TYPE_TRIAL", "Триал")
        if subscription.is_trial
        else texts.t("SUBSCRIPTION_TYPE_PAID", "Платная")
    )

    used_traffic = f"{subscription.traffic_used_gb:.1f}"
    if subscription.traffic_limit_gb == 0:
        traffic_used_display = texts.t(
            "SUBSCRIPTION_TRAFFIC_UNLIMITED",
            "∞ (безлимит) | Использовано: {used} ГБ",
        ).format(used=used_traffic)
    else:
        traffic_used_display = texts.t(
            "SUBSCRIPTION_TRAFFIC_LIMITED",
            "{used} / {limit} ГБ",
        ).format(used=used_traffic, limit=subscription.traffic_limit_gb)

    devices_used_str = "—"
    devices_list = []
    devices_count = 0

    try:
        if db_user.remnawave_uuid:
            from app.services.remnawave_service import RemnaWaveService
            service = RemnaWaveService()

            async with service.get_api_client() as api:
                response = await api._make_request('GET', f'/api/hwid/devices/{db_user.remnawave_uuid}')

                if response and 'response' in response:
                    devices_info = response['response']
                    devices_count = devices_info.get('total', 0)
                    devices_list = devices_info.get('devices', [])
                    devices_used_str = str(devices_count)
                    logger.info(f"Найдено {devices_count} устройств для пользователя {db_user.telegram_id}")
                else:
                    logger.warning(f"Не удалось получить информацию об устройствах для {db_user.telegram_id}")

    except Exception as e:
        logger.error(f"Ошибка получения устройств для отображения: {e}")
        devices_used_str = await get_current_devices_count(db_user)

    servers_names = await get_servers_display_names(subscription.connected_squads)
    servers_display = (
        servers_names
        if servers_names
        else texts.t("SUBSCRIPTION_NO_SERVERS", "Нет серверов")
    )

    message = texts.t(
        "SUBSCRIPTION_OVERVIEW_TEMPLATE",
        """👤 {full_name}
💰 Баланс: {balance}
📱 Подписка: {status_emoji} {status_display}{warning}

📱 Информация о подписке
🎭 Тип: {subscription_type}
📅 Действует до: {end_date}
⏰ Осталось: {time_left}
📈 Трафик: {traffic}
🌍 Серверы: {servers}
📱 Устройства: {devices_used} / {device_limit}""",
    ).format(
        full_name=db_user.full_name,
        balance=settings.format_price(db_user.balance_kopeks),
        status_emoji=status_emoji,
        status_display=status_display,
        warning=warning_text,
        subscription_type=subscription_type,
        end_date=subscription.end_date.strftime("%d.%m.%Y %H:%M"),
        time_left=time_left_text,
        traffic=traffic_used_display,
        servers=servers_display,
        devices_used=devices_used_str,
        device_limit=subscription.device_limit,
    )

    if devices_list and len(devices_list) > 0:
        message += "\n\n" + texts.t(
            "SUBSCRIPTION_CONNECTED_DEVICES_TITLE",
            "<blockquote>📱 <b>Подключенные устройства:</b>\n",
        )
        for device in devices_list[:5]:
            platform = device.get('platform', 'Unknown')
            device_model = device.get('deviceModel', 'Unknown')
            device_info = f"{platform} - {device_model}"

            if len(device_info) > 35:
                device_info = device_info[:32] + "..."
            message += f"• {device_info}\n"
        message += texts.t("SUBSCRIPTION_CONNECTED_DEVICES_FOOTER", "</blockquote>")

    subscription_link = get_display_subscription_link(subscription)
    hide_subscription_link = settings.should_hide_subscription_link()

    if (
            subscription_link
            and actual_status in ["trial_active", "paid_active"]
            and not hide_subscription_link
    ):
        message += "\n\n" + texts.t(
            "SUBSCRIPTION_CONNECT_LINK_SECTION",
            "🔗 <b>Ссылка для подключения:</b>\n<code>{subscription_url}</code>",
        ).format(subscription_url=subscription_link)
        message += "\n\n" + texts.t(
            "SUBSCRIPTION_CONNECT_LINK_PROMPT",
            "📱 Скопируйте ссылку и добавьте в ваше VPN приложение",
        )

    await callback.message.edit_text(
        message,
        reply_markup=get_subscription_keyboard(
            db_user.language,
            has_subscription=True,
            is_trial=subscription.is_trial,
            subscription=subscription
        ),
        parse_mode="HTML"
    )
    await callback.answer()


async def get_current_devices_detailed(db_user: User) -> dict:
    try:
        if not db_user.remnawave_uuid:
            return {"count": 0, "devices": []}

        from app.services.remnawave_service import RemnaWaveService
        service = RemnaWaveService()

        async with service.get_api_client() as api:
            response = await api._make_request('GET', f'/api/hwid/devices/{db_user.remnawave_uuid}')

            if response and 'response' in response:
                devices_info = response['response']
                total_devices = devices_info.get('total', 0)
                devices_list = devices_info.get('devices', [])

                return {
                    "count": total_devices,
                    "devices": devices_list[:5]
                }
            else:
                return {"count": 0, "devices": []}

    except Exception as e:
        logger.error(f"Ошибка получения детальной информации об устройствах: {e}")
        return {"count": 0, "devices": []}


async def get_servers_display_names(squad_uuids: List[str]) -> str:
    if not squad_uuids:
        return "Нет серверов"

    try:
        from app.database.database import AsyncSessionLocal
        from app.database.crud.server_squad import get_server_squad_by_uuid

        server_names = []

        async with AsyncSessionLocal() as db:
            for uuid in squad_uuids:
                server = await get_server_squad_by_uuid(db, uuid)
                if server:
                    server_names.append(server.display_name)
                    logger.debug(f"Найден сервер в БД: {uuid} -> {server.display_name}")
                else:
                    logger.warning(f"Сервер с UUID {uuid} не найден в БД")

        if not server_names:
            countries = await _get_available_countries()
            for uuid in squad_uuids:
                for country in countries:
                    if country['uuid'] == uuid:
                        server_names.append(country['name'])
                        logger.debug(f"Найден сервер в кэше: {uuid} -> {country['name']}")
                        break

        if not server_names:
            if len(squad_uuids) == 1:
                return "🎯 Тестовый сервер"
            return f"{len(squad_uuids)} стран"

        if len(server_names) > 6:
            displayed = ", ".join(server_names[:6])
            remaining = len(server_names) - 6
            return f"{displayed} и ещё {remaining}"
        else:
            return ", ".join(server_names)

    except Exception as e:
        logger.error(f"Ошибка получения названий серверов: {e}")
        if len(squad_uuids) == 1:
            return "🎯 Тестовый сервер"
        return f"{len(squad_uuids)} стран"


async def get_current_devices_count(db_user: User) -> str:
    try:
        if not db_user.remnawave_uuid:
            return "—"

        from app.services.remnawave_service import RemnaWaveService
        service = RemnaWaveService()

        async with service.get_api_client() as api:
            response = await api._make_request('GET', f'/api/hwid/devices/{db_user.remnawave_uuid}')

            if response and 'response' in response:
                total_devices = response['response'].get('total', 0)
                return str(total_devices)
            else:
                return "—"

    except Exception as e:
        logger.error(f"Ошибка получения количества устройств: {e}")
        return "—"


async def get_subscription_cost(subscription, db: AsyncSession) -> int:
    try:
        if subscription.is_trial:
            return 0

        from app.config import settings
        from app.services.subscription_service import SubscriptionService

        subscription_service = SubscriptionService()

        base_cost_original = PERIOD_PRICES.get(30, 0)
        try:
            owner = subscription.user
        except AttributeError:
            owner = None

        promo_group_id = getattr(owner, "promo_group_id", None) if owner else None

        period_discount_percent = 0
        if owner:
            try:
                period_discount_percent = owner.get_promo_discount("period", 30)
            except AttributeError:
                period_discount_percent = 0

        base_cost, _ = apply_percentage_discount(
            base_cost_original,
            period_discount_percent,
        )

        try:
            servers_cost, _ = await subscription_service.get_countries_price_by_uuids(
                subscription.connected_squads,
                db,
                promo_group_id=promo_group_id,
            )
        except AttributeError:
            servers_cost, _ = await get_countries_price_by_uuids_fallback(
                subscription.connected_squads,
                db,
                promo_group_id=promo_group_id,
            )

        traffic_cost = settings.get_traffic_price(subscription.traffic_limit_gb)
        devices_cost = max(0, subscription.device_limit - settings.DEFAULT_DEVICE_LIMIT) * settings.PRICE_PER_DEVICE

        total_cost = base_cost + servers_cost + traffic_cost + devices_cost

        logger.info(f"📊 Месячная стоимость конфигурации подписки {subscription.id}:")
        base_log = f"   📅 Базовый тариф (30 дней): {base_cost_original / 100}₽"
        if period_discount_percent > 0:
            discount_value = base_cost_original * period_discount_percent // 100
            base_log += (
                f" → {base_cost / 100}₽"
                f" (скидка {period_discount_percent}%: -{discount_value / 100}₽)"
            )
        logger.info(base_log)
        if servers_cost > 0:
            logger.info(f"   🌍 Серверы: {servers_cost / 100}₽")
        if traffic_cost > 0:
            logger.info(f"   📊 Трафик: {traffic_cost / 100}₽")
        if devices_cost > 0:
            logger.info(f"   📱 Устройства: {devices_cost / 100}₽")
        logger.info(f"   💎 ИТОГО: {total_cost / 100}₽")

        return total_cost

    except Exception as e:
        logger.error(f"⚠️ Ошибка расчета стоимости подписки: {e}")
        return 0


async def show_trial_offer(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    texts = get_texts(db_user.language)

    if db_user.subscription or db_user.has_had_paid_subscription:
        await callback.message.edit_text(
            texts.TRIAL_ALREADY_USED,
            reply_markup=get_back_keyboard(db_user.language)
        )
        await callback.answer()
        return

    trial_server_name = "🎯 Тестовый сервер"
    try:
        from app.database.crud.server_squad import get_server_squad_by_uuid

        if settings.TRIAL_SQUAD_UUID:
            trial_server = await get_server_squad_by_uuid(db, settings.TRIAL_SQUAD_UUID)
            if trial_server:
                trial_server_name = trial_server.display_name
            else:
                logger.warning(f"Триальный сервер с UUID {settings.TRIAL_SQUAD_UUID} не найден в БД")
        else:
            logger.warning("TRIAL_SQUAD_UUID не настроен в конфигурации")

    except Exception as e:
        logger.error(f"Ошибка получения триального сервера: {e}")

    trial_text = texts.TRIAL_AVAILABLE.format(
        days=settings.TRIAL_DURATION_DAYS,
        traffic=settings.TRIAL_TRAFFIC_LIMIT_GB,
        devices=settings.TRIAL_DEVICE_LIMIT,
        server_name=trial_server_name
    )

    await callback.message.edit_text(
        trial_text,
        reply_markup=get_trial_keyboard(db_user.language)
    )
    await callback.answer()


async def activate_trial(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    from app.services.admin_notification_service import AdminNotificationService

    texts = get_texts(db_user.language)

    if db_user.subscription or db_user.has_had_paid_subscription:
        await callback.message.edit_text(
            texts.TRIAL_ALREADY_USED,
            reply_markup=get_back_keyboard(db_user.language)
        )
        await callback.answer()
        return

    try:
        subscription = await create_trial_subscription(db, db_user.id)

        await db.refresh(db_user)

        subscription_service = SubscriptionService()
        remnawave_user = await subscription_service.create_remnawave_user(
            db, subscription
        )

        await db.refresh(db_user)

        try:
            notification_service = AdminNotificationService(callback.bot)
            await notification_service.send_trial_activation_notification(db, db_user, subscription)
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления о триале: {e}")

        subscription_link = get_display_subscription_link(subscription)
        hide_subscription_link = settings.should_hide_subscription_link()

        if remnawave_user and subscription_link:
            if settings.is_happ_cryptolink_mode():
                trial_success_text = (
                        f"{texts.TRIAL_ACTIVATED}\n\n"
                        + texts.t(
                    "SUBSCRIPTION_HAPP_LINK_PROMPT",
                    "🔒 Ссылка на подписку создана. Нажмите кнопку \"Подключиться\" ниже, чтобы открыть её в Happ.",
                )
                        + "\n\n"
                        + texts.t(
                    "SUBSCRIPTION_IMPORT_INSTRUCTION_PROMPT",
                    "📱 Нажмите кнопку ниже, чтобы получить инструкцию по настройке VPN на вашем устройстве",
                )
                )
            elif hide_subscription_link:
                trial_success_text = (
                        f"{texts.TRIAL_ACTIVATED}\n\n"
                        + texts.t(
                    "SUBSCRIPTION_LINK_HIDDEN_NOTICE",
                    "ℹ️ Ссылка подписки доступна по кнопкам ниже или в разделе \"Моя подписка\".",
                )
                        + "\n\n"
                        + texts.t(
                    "SUBSCRIPTION_IMPORT_INSTRUCTION_PROMPT",
                    "📱 Нажмите кнопку ниже, чтобы получить инструкцию по настройке VPN на вашем устройстве",
                )
                )
            else:
                subscription_import_link = texts.t(
                    "SUBSCRIPTION_IMPORT_LINK_SECTION",
                    "🔗 <b>Ваша ссылка для импорта в VPN приложение:</b>\n<code>{subscription_url}</code>",
                ).format(subscription_url=subscription_link)

                trial_success_text = (
                    f"{texts.TRIAL_ACTIVATED}\n\n"
                    f"{subscription_import_link}\n\n"
                    f"{texts.t('SUBSCRIPTION_IMPORT_INSTRUCTION_PROMPT', '📱 Нажмите кнопку ниже, чтобы получить инструкцию по настройке VPN на вашем устройстве')}"
                )

            connect_mode = settings.CONNECT_BUTTON_MODE

            if connect_mode == "miniapp_subscription":
                connect_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=texts.t("CONNECT_BUTTON", "🔗 Подключиться"),
                            web_app=types.WebAppInfo(url=subscription_link),
                        )
                    ],
                    [InlineKeyboardButton(text=texts.t("BACK_TO_MAIN_MENU_BUTTON", "⬅️ В главное меню"),
                                          callback_data="back_to_menu")],
                ])
            elif connect_mode == "miniapp_custom":
                if not settings.MINIAPP_CUSTOM_URL:
                    await callback.answer(
                        texts.t(
                            "CUSTOM_MINIAPP_URL_NOT_SET",
                            "⚠ Кастомная ссылка для мини-приложения не настроена",
                        ),
                        show_alert=True,
                    )
                    return

                connect_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=texts.t("CONNECT_BUTTON", "🔗 Подключиться"),
                            web_app=types.WebAppInfo(url=settings.MINIAPP_CUSTOM_URL),
                        )
                    ],
                    [InlineKeyboardButton(text=texts.t("BACK_TO_MAIN_MENU_BUTTON", "⬅️ В главное меню"),
                                          callback_data="back_to_menu")],
                ])
            elif connect_mode == "link":
                rows = [
                    [InlineKeyboardButton(text=texts.t("CONNECT_BUTTON", "🔗 Подключиться"), url=subscription_link)]
                ]
                happ_row = get_happ_download_button_row(texts)
                if happ_row:
                    rows.append(happ_row)
                rows.append([
                    InlineKeyboardButton(
                        text=texts.t("BACK_TO_MAIN_MENU_BUTTON", "⬅️ В главное меню"),
                        callback_data="back_to_menu"
                    )
                ])
                connect_keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
            elif connect_mode == "happ_cryptolink":
                rows = [
                    [
                        InlineKeyboardButton(
                            text=texts.t("CONNECT_BUTTON", "🔗 Подключиться"),
                            callback_data="open_subscription_link",
                        )
                    ]
                ]
                happ_row = get_happ_download_button_row(texts)
                if happ_row:
                    rows.append(happ_row)
                rows.append([
                    InlineKeyboardButton(
                        text=texts.t("BACK_TO_MAIN_MENU_BUTTON", "⬅️ В главное меню"),
                        callback_data="back_to_menu"
                    )
                ])
                connect_keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
            else:
                connect_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=texts.t("CONNECT_BUTTON", "🔗 Подключиться"),
                                          callback_data="subscription_connect")],
                    [InlineKeyboardButton(text=texts.t("BACK_TO_MAIN_MENU_BUTTON", "⬅️ В главное меню"),
                                          callback_data="back_to_menu")],
                ])

            await callback.message.edit_text(
                trial_success_text,
                reply_markup=connect_keyboard,
                parse_mode="HTML"
            )
        else:
            await callback.message.edit_text(
                f"{texts.TRIAL_ACTIVATED}\n\n⚠️ Ссылка генерируется, попробуйте перейти в раздел 'Моя подписка' через несколько секунд.",
                reply_markup=get_back_keyboard(db_user.language)
            )

        logger.info(f"✅ Активирована тестовая подписка для пользователя {db_user.telegram_id}")

    except Exception as e:
        logger.error(f"Ошибка активации триала: {e}")
        await callback.message.edit_text(
            texts.ERROR,
            reply_markup=get_back_keyboard(db_user.language)
        )

    await callback.answer()


async def start_subscription_purchase(
        callback: types.CallbackQuery,
        state: FSMContext,
        db_user: User,
        db: AsyncSession,
):
    texts = get_texts(db_user.language)

    await callback.message.edit_text(
        await _build_subscription_period_prompt(db_user, texts, db),
        reply_markup=get_subscription_period_keyboard(db_user.language),
        parse_mode="HTML",
    )

    subscription = getattr(db_user, 'subscription', None)
    initial_devices = settings.DEFAULT_DEVICE_LIMIT

    if subscription and getattr(subscription, 'device_limit', None):
        initial_devices = max(settings.DEFAULT_DEVICE_LIMIT, subscription.device_limit)

    initial_data = {
        'period_days': None,
        'countries': [],
        'devices': initial_devices,
        'total_price': 0
    }

    if settings.is_traffic_fixed():
        initial_data['traffic_gb'] = settings.get_fixed_traffic_limit()
    else:
        initial_data['traffic_gb'] = None

    await state.set_data(initial_data)
    await state.set_state(SubscriptionStates.selecting_period)
    await callback.answer()


async def save_cart_and_redirect_to_topup(
        callback: types.CallbackQuery,
        state: FSMContext,
        db_user: User,
        missing_amount: int
):
    texts = get_texts(db_user.language)
    data = await state.get_data()

    await state.set_state(SubscriptionStates.cart_saved_for_topup)
    await state.update_data({
        **data,
        'saved_cart': True,
        'missing_amount': missing_amount,
        'return_to_cart': True
    })

    await callback.message.edit_text(
        f"💰 Недостаточно средств для оформления подписки\n\n"
        f"Требуется: {texts.format_price(missing_amount)}\n"
        f"У вас: {texts.format_price(db_user.balance_kopeks)}\n\n"
        f"🛒 Ваша корзина сохранена!\n"
        f"После пополнения баланса вы сможете вернуться к оформлению подписки.\n\n"
        f"Выберите способ пополнения:",
        reply_markup=get_payment_methods_keyboard_with_cart(
            db_user.language,
            missing_amount,
        ),
        parse_mode="HTML"
    )


async def return_to_saved_cart(
        callback: types.CallbackQuery,
        state: FSMContext,
        db_user: User,
        db: AsyncSession
):
    data = await state.get_data()
    texts = get_texts(db_user.language)

    if not data.get('saved_cart'):
        await callback.answer("❌ Сохраненная корзина не найдена", show_alert=True)
        return

    total_price = data.get('total_price', 0)

    if db_user.balance_kopeks < total_price:
        missing_amount = total_price - db_user.balance_kopeks
        await callback.message.edit_text(
            f"❌ Все еще недостаточно средств\n\n"
            f"Требуется: {texts.format_price(total_price)}\n"
            f"У вас: {texts.format_price(db_user.balance_kopeks)}\n"
            f"Не хватает: {texts.format_price(missing_amount)}",
            reply_markup=get_insufficient_balance_keyboard_with_cart(
                db_user.language,
                missing_amount,
            )
        )
        return

    countries = await _get_available_countries(db_user.promo_group_id)
    selected_countries_names = []

    months_in_period = calculate_months_from_days(data['period_days'])
    period_display = format_period_description(data['period_days'], db_user.language)

    for country in countries:
        if country['uuid'] in data['countries']:
            selected_countries_names.append(country['name'])

    if settings.is_traffic_fixed():
        traffic_display = "Безлимитный" if data['traffic_gb'] == 0 else f"{data['traffic_gb']} ГБ"
    else:
        traffic_display = "Безлимитный" if data['traffic_gb'] == 0 else f"{data['traffic_gb']} ГБ"

    summary_text = (
        "🛒 Восстановленная корзина\n\n"
        f"📅 Период: {period_display}\n"
        f"📊 Трафик: {traffic_display}\n"
        f"🌍 Страны: {', '.join(selected_countries_names)}\n"
        f"📱 Устройства: {data['devices']}\n\n"
        f"💎 Общая стоимость: {texts.format_price(total_price)}\n\n"
        "Подтверждаете покупку?"
    )

    await callback.message.edit_text(
        summary_text,
        reply_markup=get_subscription_confirm_keyboard_with_cart(db_user.language),
        parse_mode="HTML"
    )

    await state.set_state(SubscriptionStates.confirming_purchase)
    await callback.answer("✅ Корзина восстановлена!")


async def handle_add_countries(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession,
        state: FSMContext
):
    if not await _should_show_countries_management(db_user):
        texts = get_texts(db_user.language)
        await callback.answer(
            texts.t(
                "COUNTRY_MANAGEMENT_UNAVAILABLE",
                "ℹ️ Управление серверами недоступно - доступен только один сервер",
            ),
            show_alert=True,
        )
        return

    texts = get_texts(db_user.language)
    subscription = db_user.subscription

    if not subscription or subscription.is_trial:
        await callback.answer(
            texts.t("PAID_FEATURE_ONLY", "⚠ Эта функция доступна только для платных подписок"),
            show_alert=True,
        )
        return

    countries = await _get_available_countries(db_user.promo_group_id)
    current_countries = subscription.connected_squads

    period_hint_days = _get_period_hint_from_subscription(subscription)
    servers_discount_percent = _get_addon_discount_percent_for_user(
        db_user,
        "servers",
        period_hint_days,
    )

    current_countries_names = []
    for country in countries:
        if country['uuid'] in current_countries:
            current_countries_names.append(country['name'])

    current_list = (
        "\n".join(f"• {name}" for name in current_countries_names)
        if current_countries_names
        else texts.t("COUNTRY_MANAGEMENT_NONE", "Нет подключенных стран")
    )

    text = texts.t(
        "COUNTRY_MANAGEMENT_PROMPT",
        (
            "🌍 <b>Управление странами подписки</b>\n\n"
            "📋 <b>Текущие страны ({current_count}):</b>\n"
            "{current_list}\n\n"
            "💡 <b>Инструкция:</b>\n"
            "✅ - страна подключена\n"
            "➕ - будет добавлена (платно)\n"
            "➖ - будет отключена (бесплатно)\n"
            "⚪ - не выбрана\n\n"
            "⚠️ <b>Важно:</b> Повторное подключение отключенных стран будет платным!"
        ),
    ).format(
        current_count=len(current_countries),
        current_list=current_list,
    )

    await state.update_data(countries=current_countries.copy())

    await callback.message.edit_text(
        text,
        reply_markup=get_manage_countries_keyboard(
            countries,
            current_countries.copy(),
            current_countries,
            db_user.language,
            subscription.end_date,
            servers_discount_percent,
        ),
        parse_mode="HTML"
    )

    await callback.answer()


async def get_countries_price_by_uuids_fallback(
        country_uuids: List[str],
        db: AsyncSession,
        promo_group_id: Optional[int] = None,
) -> Tuple[int, List[int]]:
    try:
        from app.database.crud.server_squad import get_server_squad_by_uuid

        total_price = 0
        prices_list = []

        for country_uuid in country_uuids:
            try:
                server = await get_server_squad_by_uuid(db, country_uuid)
                is_allowed = True
                if promo_group_id is not None and server:
                    allowed_ids = {pg.id for pg in server.allowed_promo_groups}
                    is_allowed = promo_group_id in allowed_ids

                if server and server.is_available and not server.is_full and is_allowed:
                    price = server.price_kopeks
                    total_price += price
                    prices_list.append(price)
                else:
                    default_price = 0
                    total_price += default_price
                    prices_list.append(default_price)
            except Exception:
                default_price = 0
                total_price += default_price
                prices_list.append(default_price)

        return total_price, prices_list

    except Exception as e:
        logger.error(f"Ошибка fallback функции: {e}")
        default_prices = [0] * len(country_uuids)
        return sum(default_prices), default_prices


async def handle_manage_country(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession,
        state: FSMContext
):
    logger.info(f"🔍 Управление страной: {callback.data}")

    country_uuid = callback.data.split('_')[2]

    subscription = db_user.subscription
    if not subscription or subscription.is_trial:
        texts = get_texts(db_user.language)
        await callback.answer(
            texts.t("PAID_FEATURE_ONLY_SHORT", "⚠ Только для платных подписок"),
            show_alert=True,
        )
        return

    data = await state.get_data()
    current_selected = data.get('countries', subscription.connected_squads.copy())

    countries = await _get_available_countries(db_user.promo_group_id)
    allowed_country_ids = {country['uuid'] for country in countries}

    if country_uuid not in allowed_country_ids and country_uuid not in current_selected:
        texts = get_texts(db_user.language)
        await callback.answer(
            texts.t(
                "COUNTRY_NOT_AVAILABLE_PROMOGROUP",
                "❌ Сервер недоступен для вашей промогруппы",
            ),
            show_alert=True,
        )
        return

    if country_uuid in current_selected:
        current_selected.remove(country_uuid)
        action = "removed"
    else:
        current_selected.append(country_uuid)
        action = "added"

    logger.info(f"🔍 Страна {country_uuid} {action}")

    await state.update_data(countries=current_selected)

    period_hint_days = _get_period_hint_from_subscription(subscription)
    servers_discount_percent = _get_addon_discount_percent_for_user(
        db_user,
        "servers",
        period_hint_days,
    )

    try:
        await callback.message.edit_reply_markup(
            reply_markup=get_manage_countries_keyboard(
                countries,
                current_selected,
                subscription.connected_squads,
                db_user.language,
                subscription.end_date,
                servers_discount_percent,
            )
        )
        logger.info(f"✅ Клавиатура обновлена")

    except Exception as e:
        logger.error(f"⚠ Ошибка обновления клавиатуры: {e}")

    await callback.answer()


async def apply_countries_changes(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession,
        state: FSMContext
):
    logger.info(f"🔧 Применение изменений стран")

    data = await state.get_data()
    texts = get_texts(db_user.language)

    await save_subscription_checkout_draft(db_user.id, dict(data))
    resume_callback = (
        "subscription_resume_checkout"
        if should_offer_checkout_resume(db_user, True)
        else None
    )
    subscription = db_user.subscription

    selected_countries = data.get('countries', [])
    current_countries = subscription.connected_squads

    countries = await _get_available_countries(db_user.promo_group_id)
    allowed_country_ids = {country['uuid'] for country in countries}

    selected_countries = [
        country_uuid
        for country_uuid in selected_countries
        if country_uuid in allowed_country_ids or country_uuid in current_countries
    ]

    added = [c for c in selected_countries if c not in current_countries]
    removed = [c for c in current_countries if c not in selected_countries]

    if not added and not removed:
        await callback.answer(
            texts.t("COUNTRY_CHANGES_NOT_FOUND", "⚠️ Изменения не обнаружены"),
            show_alert=True,
        )
        return

    logger.info(f"🔧 Добавлено: {added}, Удалено: {removed}")

    months_to_pay = get_remaining_months(subscription.end_date)

    period_hint_days = months_to_pay * 30 if months_to_pay > 0 else None
    servers_discount_percent = _get_addon_discount_percent_for_user(
        db_user,
        "servers",
        period_hint_days,
    )

    cost_per_month = 0
    added_names = []
    removed_names = []

    added_server_components: List[Dict[str, int]] = []

    for country in countries:
        if not country.get('is_available', True):
            continue

        if country['uuid'] in added:
            server_price_per_month = country['price_kopeks']
            discounted_per_month, discount_per_month = apply_percentage_discount(
                server_price_per_month,
                servers_discount_percent,
            )
            cost_per_month += discounted_per_month
            added_names.append(country['name'])
            added_server_components.append(
                {
                    "discounted_per_month": discounted_per_month,
                    "discount_per_month": discount_per_month,
                    "original_per_month": server_price_per_month,
                }
            )
        if country['uuid'] in removed:
            removed_names.append(country['name'])

    total_cost, charged_months = calculate_prorated_price(cost_per_month, subscription.end_date)

    added_server_prices = [
        component["discounted_per_month"] * charged_months
        for component in added_server_components
    ]

    total_discount = sum(
        component["discount_per_month"] * charged_months
        for component in added_server_components
    )

    if added_names:
        logger.info(
            "Стоимость новых серверов: %.2f₽/мес × %s мес = %.2f₽ (скидка %.2f₽)",
            cost_per_month / 100,
            charged_months,
            total_cost / 100,
            total_discount / 100,
        )

    if total_cost > 0 and db_user.balance_kopeks < total_cost:
        missing_kopeks = total_cost - db_user.balance_kopeks
        required_text = f"{texts.format_price(total_cost)} (за {charged_months} мес)"
        message_text = texts.t(
            "ADDON_INSUFFICIENT_FUNDS_MESSAGE",
            (
                "⚠️ <b>Недостаточно средств</b>\n\n"
                "Стоимость услуги: {required}\n"
                "На балансе: {balance}\n"
                "Не хватает: {missing}\n\n"
                "Выберите способ пополнения. Сумма подставится автоматически."
            ),
        ).format(
            required=required_text,
            balance=texts.format_price(db_user.balance_kopeks),
            missing=texts.format_price(missing_kopeks),
        )

        await callback.message.answer(
            message_text,
            reply_markup=get_insufficient_balance_keyboard(
                db_user.language,
                resume_callback=resume_callback,
                amount_kopeks=missing_kopeks,
            ),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    try:
        if added and total_cost > 0:
            success = await subtract_user_balance(
                db, db_user, total_cost,
                f"Добавление стран: {', '.join(added_names)} на {charged_months} мес"
            )
            if not success:
                await callback.answer(
                    texts.t("PAYMENT_CHARGE_ERROR", "⚠️ Ошибка списания средств"),
                    show_alert=True,
                )
                return

            await create_transaction(
                db=db,
                user_id=db_user.id,
                type=TransactionType.SUBSCRIPTION_PAYMENT,
                amount_kopeks=total_cost,
                description=f"Добавление стран к подписке: {', '.join(added_names)} на {charged_months} мес"
            )

        if added:
            from app.database.crud.server_squad import get_server_ids_by_uuids, add_user_to_servers
            from app.database.crud.subscription import add_subscription_servers

            added_server_ids = await get_server_ids_by_uuids(db, added)

            if added_server_ids:
                await add_subscription_servers(db, subscription, added_server_ids, added_server_prices)
                await add_user_to_servers(db, added_server_ids)

                logger.info(
                    f"📊 Добавлены серверы с ценами за {charged_months} мес: {list(zip(added_server_ids, added_server_prices))}")

        subscription.connected_squads = selected_countries
        subscription.updated_at = datetime.utcnow()
        await db.commit()

        subscription_service = SubscriptionService()
        await subscription_service.update_remnawave_user(db, subscription)

        await db.refresh(subscription)

        try:
            from app.services.admin_notification_service import AdminNotificationService
            notification_service = AdminNotificationService(callback.bot)
            await notification_service.send_subscription_update_notification(
                db, db_user, subscription, "servers", current_countries, selected_countries, total_cost
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления об изменении серверов: {e}")

        success_text = texts.t(
            "COUNTRY_CHANGES_SUCCESS_HEADER",
            "✅ <b>Страны успешно обновлены!</b>\n\n",
        )

        if added_names:
            success_text += texts.t(
                "COUNTRY_CHANGES_ADDED_HEADER",
                "➕ <b>Добавлены страны:</b>\n",
            )
            success_text += "\n".join(f"• {name}" for name in added_names)
            if total_cost > 0:
                success_text += "\n" + texts.t(
                    "COUNTRY_CHANGES_CHARGED",
                    "💰 Списано: {amount} (за {months} мес)",
                ).format(
                    amount=texts.format_price(total_cost),
                    months=charged_months,
                )
                if total_discount > 0:
                    success_text += texts.t(
                        "COUNTRY_CHANGES_DISCOUNT_INFO",
                        " (скидка {percent}%: -{amount})",
                    ).format(
                        percent=servers_discount_percent,
                        amount=texts.format_price(total_discount),
                    )
            success_text += "\n"

        if removed_names:
            success_text += "\n" + texts.t(
                "COUNTRY_CHANGES_REMOVED_HEADER",
                "➖ <b>Отключены страны:</b>\n",
            )
            success_text += "\n".join(f"• {name}" for name in removed_names)
            success_text += "\n" + texts.t(
                "COUNTRY_CHANGES_REMOVED_WARNING",
                "ℹ️ Повторное подключение будет платным",
            ) + "\n"

        success_text += "\n" + texts.t(
            "COUNTRY_CHANGES_ACTIVE_COUNT",
            "🌐 <b>Активных стран:</b> {count}",
        ).format(count=len(selected_countries))

        await callback.message.edit_text(
            success_text,
            reply_markup=get_back_keyboard(db_user.language),
            parse_mode="HTML"
        )

        await state.clear()
        logger.info(
            f"✅ Пользователь {db_user.telegram_id} обновил страны. Добавлено: {len(added)}, удалено: {len(removed)}, заплатил: {total_cost / 100}₽")

    except Exception as e:
        logger.error(f"⚠️ Ошибка применения изменений: {e}")
        await callback.message.edit_text(
            texts.ERROR,
            reply_markup=get_back_keyboard(db_user.language)
        )

    await callback.answer()


async def handle_add_traffic(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    from app.config import settings

    texts = get_texts(db_user.language)

    if settings.is_traffic_fixed():
        await callback.answer(
            texts.t(
                "TRAFFIC_FIXED_MODE",
                "⚠️ В текущем режиме трафик фиксированный и не может быть изменен",
            ),
            show_alert=True,
        )
        return

    subscription = db_user.subscription

    if not subscription or subscription.is_trial:
        await callback.answer(
            texts.t("PAID_FEATURE_ONLY", "⚠ Эта функция доступна только для платных подписок"),
            show_alert=True,
        )
        return

    if subscription.traffic_limit_gb == 0:
        await callback.answer(
            texts.t("TRAFFIC_ALREADY_UNLIMITED", "⚠ У вас уже безлимитный трафик"),
            show_alert=True,
        )
        return

    current_traffic = subscription.traffic_limit_gb
    period_hint_days = _get_period_hint_from_subscription(subscription)
    traffic_discount_percent = _get_addon_discount_percent_for_user(
        db_user,
        "traffic",
        period_hint_days,
    )

    prompt_text = texts.t(
        "ADD_TRAFFIC_PROMPT",
        (
            "📈 <b>Добавить трафик к подписке</b>\n\n"
            "Текущий лимит: {current_traffic}\n"
            "Выберите дополнительный трафик:"
        ),
    ).format(current_traffic=texts.format_traffic(current_traffic))

    await callback.message.edit_text(
        prompt_text,
        reply_markup=get_add_traffic_keyboard(
            db_user.language,
            subscription.end_date,
            traffic_discount_percent,
        ),
        parse_mode="HTML"
    )

    await callback.answer()


async def handle_change_devices(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    texts = get_texts(db_user.language)
    subscription = db_user.subscription

    if not subscription or subscription.is_trial:
        await callback.answer(
            texts.t("PAID_FEATURE_ONLY", "⚠️ Эта функция доступна только для платных подписок"),
            show_alert=True,
        )
        return

    current_devices = subscription.device_limit

    period_hint_days = _get_period_hint_from_subscription(subscription)
    devices_discount_percent = _get_addon_discount_percent_for_user(
        db_user,
        "devices",
        period_hint_days,
    )

    prompt_text = texts.t(
        "CHANGE_DEVICES_PROMPT",
        (
            "📱 <b>Изменение количества устройств</b>\n\n"
            "Текущий лимит: {current_devices} устройств\n"
            "Выберите новое количество устройств:\n\n"
            "💡 <b>Важно:</b>\n"
            "• При увеличении - доплата пропорционально оставшемуся времени\n"
            "• При уменьшении - возврат средств не производится"
        ),
    ).format(current_devices=current_devices)

    await callback.message.edit_text(
        prompt_text,
        reply_markup=get_change_devices_keyboard(
            current_devices,
            db_user.language,
            subscription.end_date,
            devices_discount_percent,
        ),
        parse_mode="HTML"
    )

    await callback.answer()


async def confirm_change_devices(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    new_devices_count = int(callback.data.split('_')[2])
    texts = get_texts(db_user.language)
    subscription = db_user.subscription

    current_devices = subscription.device_limit

    if new_devices_count == current_devices:
        await callback.answer(
            texts.t("DEVICES_NO_CHANGE", "ℹ️ Количество устройств не изменилось"),
            show_alert=True,
        )
        return

    if settings.MAX_DEVICES_LIMIT > 0 and new_devices_count > settings.MAX_DEVICES_LIMIT:
        await callback.answer(
            texts.t(
                "DEVICES_LIMIT_EXCEEDED",
                "⚠️ Превышен максимальный лимит устройств ({limit})",
            ).format(limit=settings.MAX_DEVICES_LIMIT),
            show_alert=True
        )
        return

    devices_difference = new_devices_count - current_devices

    if devices_difference > 0:
        additional_devices = devices_difference

        if current_devices < settings.DEFAULT_DEVICE_LIMIT:
            free_devices = settings.DEFAULT_DEVICE_LIMIT - current_devices
            chargeable_devices = max(0, additional_devices - free_devices)
        else:
            chargeable_devices = additional_devices

        devices_price_per_month = chargeable_devices * settings.PRICE_PER_DEVICE
        months_hint = get_remaining_months(subscription.end_date)
        period_hint_days = months_hint * 30 if months_hint > 0 else None
        devices_discount_percent = _get_addon_discount_percent_for_user(
            db_user,
            "devices",
            period_hint_days,
        )
        discounted_per_month, discount_per_month = apply_percentage_discount(
            devices_price_per_month,
            devices_discount_percent,
        )
        price, charged_months = calculate_prorated_price(
            discounted_per_month,
            subscription.end_date,
        )
        total_discount = discount_per_month * charged_months

        if price > 0 and db_user.balance_kopeks < price:
            missing_kopeks = price - db_user.balance_kopeks
            required_text = f"{texts.format_price(price)} (за {charged_months} мес)"
            message_text = texts.t(
                "ADDON_INSUFFICIENT_FUNDS_MESSAGE",
                (
                    "⚠️ <b>Недостаточно средств</b>\n\n"
                    "Стоимость услуги: {required}\n"
                    "На балансе: {balance}\n"
                    "Не хватает: {missing}\n\n"
                    "Выберите способ пополнения. Сумма подставится автоматически."
                ),
            ).format(
                required=required_text,
                balance=texts.format_price(db_user.balance_kopeks),
                missing=texts.format_price(missing_kopeks),
            )

            await callback.message.answer(
                message_text,
                reply_markup=get_insufficient_balance_keyboard(
                    db_user.language,
                    amount_kopeks=missing_kopeks,
                ),
                parse_mode="HTML",
            )
            await callback.answer()
            return

        action_text = texts.t(
            "DEVICE_CHANGE_ACTION_INCREASE",
            "увеличить до {count}",
        ).format(count=new_devices_count)
        if price > 0:
            cost_text = texts.t(
                "DEVICE_CHANGE_EXTRA_COST",
                "Доплата: {amount} (за {months} мес)",
            ).format(
                amount=texts.format_price(price),
                months=charged_months,
            )
            if total_discount > 0:
                cost_text += texts.t(
                    "DEVICE_CHANGE_DISCOUNT_INFO",
                    " (скидка {percent}%: -{amount})",
                ).format(
                    percent=devices_discount_percent,
                    amount=texts.format_price(total_discount),
                )
        else:
            cost_text = texts.t("DEVICE_CHANGE_FREE", "Бесплатно")

    else:
        price = 0
        action_text = texts.t(
            "DEVICE_CHANGE_ACTION_DECREASE",
            "уменьшить до {count}",
        ).format(count=new_devices_count)
        cost_text = texts.t("DEVICE_CHANGE_NO_REFUND", "Возврат средств не производится")

    confirm_text = texts.t(
        "DEVICE_CHANGE_CONFIRMATION",
        (
            "📱 <b>Подтверждение изменения</b>\n\n"
            "Текущее количество: {current} устройств\n"
            "Новое количество: {new} устройств\n\n"
            "Действие: {action}\n"
            "💰 {cost}\n\n"
            "Подтвердить изменение?"
        ),
    ).format(
        current=current_devices,
        new=new_devices_count,
        action=action_text,
        cost=cost_text,
    )

    await callback.message.edit_text(
        confirm_text,
        reply_markup=get_confirm_change_devices_keyboard(new_devices_count, price, db_user.language),
        parse_mode="HTML"
    )

    await callback.answer()


async def execute_change_devices(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    callback_parts = callback.data.split('_')
    new_devices_count = int(callback_parts[3])
    price = int(callback_parts[4])

    texts = get_texts(db_user.language)
    subscription = db_user.subscription
    current_devices = subscription.device_limit

    try:
        if price > 0:
            success = await subtract_user_balance(
                db, db_user, price,
                f"Изменение количества устройств с {current_devices} до {new_devices_count}"
            )

            if not success:
                await callback.answer(
                    texts.t("PAYMENT_CHARGE_ERROR", "⚠️ Ошибка списания средств"),
                    show_alert=True,
                )
                return

            charged_months = get_remaining_months(subscription.end_date)
            await create_transaction(
                db=db,
                user_id=db_user.id,
                type=TransactionType.SUBSCRIPTION_PAYMENT,
                amount_kopeks=price,
                description=f"Изменение устройств с {current_devices} до {new_devices_count} на {charged_months} мес"
            )

        subscription.device_limit = new_devices_count
        subscription.updated_at = datetime.utcnow()

        await db.commit()

        subscription_service = SubscriptionService()
        await subscription_service.update_remnawave_user(db, subscription)

        await db.refresh(db_user)
        await db.refresh(subscription)

        try:
            from app.services.admin_notification_service import AdminNotificationService
            notification_service = AdminNotificationService(callback.bot)
            await notification_service.send_subscription_update_notification(
                db, db_user, subscription, "devices", current_devices, new_devices_count, price
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления об изменении устройств: {e}")

        if new_devices_count > current_devices:
            success_text = texts.t(
                "DEVICE_CHANGE_INCREASE_SUCCESS",
                "✅ Количество устройств увеличено!\n\n",
            )
            success_text += texts.t(
                "DEVICE_CHANGE_RESULT_LINE",
                "📱 Было: {old} → Стало: {new}\n",
            ).format(old=current_devices, new=new_devices_count)
            if price > 0:
                success_text += texts.t(
                    "DEVICE_CHANGE_CHARGED",
                    "💰 Списано: {amount}",
                ).format(amount=texts.format_price(price))
        else:
            success_text = texts.t(
                "DEVICE_CHANGE_DECREASE_SUCCESS",
                "✅ Количество устройств уменьшено!\n\n",
            )
            success_text += texts.t(
                "DEVICE_CHANGE_RESULT_LINE",
                "📱 Было: {old} → Стало: {new}\n",
            ).format(old=current_devices, new=new_devices_count)
            success_text += texts.t(
                "DEVICE_CHANGE_NO_REFUND_INFO",
                "ℹ️ Возврат средств не производится",
            )

        await callback.message.edit_text(
            success_text,
            reply_markup=get_back_keyboard(db_user.language)
        )

        logger.info(
            f"✅ Пользователь {db_user.telegram_id} изменил количество устройств с {current_devices} на {new_devices_count}, доплата: {price / 100}₽")

    except Exception as e:
        logger.error(f"Ошибка изменения количества устройств: {e}")
        await callback.message.edit_text(
            texts.ERROR,
            reply_markup=get_back_keyboard(db_user.language)
        )

    await callback.answer()


async def handle_device_management(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    texts = get_texts(db_user.language)
    subscription = db_user.subscription

    if not subscription or subscription.is_trial:
        await callback.answer(
            texts.t("PAID_FEATURE_ONLY", "⚠️ Эта функция доступна только для платных подписок"),
            show_alert=True,
        )
        return

    if not db_user.remnawave_uuid:
        await callback.answer(
            texts.t("DEVICE_UUID_NOT_FOUND", "❌ UUID пользователя не найден"),
            show_alert=True,
        )
        return

    try:
        from app.services.remnawave_service import RemnaWaveService
        service = RemnaWaveService()

        async with service.get_api_client() as api:
            response = await api._make_request('GET', f'/api/hwid/devices/{db_user.remnawave_uuid}')

            if response and 'response' in response:
                devices_info = response['response']
                total_devices = devices_info.get('total', 0)
                devices_list = devices_info.get('devices', [])

                if total_devices == 0:
                    await callback.message.edit_text(
                        texts.t("DEVICE_NONE_CONNECTED", "ℹ️ У вас нет подключенных устройств"),
                        reply_markup=get_back_keyboard(db_user.language)
                    )
                    await callback.answer()
                    return

                await show_devices_page(callback, db_user, devices_list, page=1)
            else:
                await callback.answer(
                    texts.t(
                        "DEVICE_FETCH_INFO_ERROR",
                        "❌ Ошибка получения информации об устройствах",
                    ),
                    show_alert=True,
                )

    except Exception as e:
        logger.error(f"Ошибка получения списка устройств: {e}")
        await callback.answer(
            texts.t(
                "DEVICE_FETCH_INFO_ERROR",
                "❌ Ошибка получения информации об устройствах",
            ),
            show_alert=True,
        )

    await callback.answer()


async def show_devices_page(
        callback: types.CallbackQuery,
        db_user: User,
        devices_list: List[dict],
        page: int = 1
):
    texts = get_texts(db_user.language)
    devices_per_page = 5

    pagination = paginate_list(devices_list, page=page, per_page=devices_per_page)

    devices_text = texts.t(
        "DEVICE_MANAGEMENT_OVERVIEW",
        (
            "🔄 <b>Управление устройствами</b>\n\n"
            "📊 Всего подключено: {total} устройств\n"
            "📄 Страница {page} из {pages}\n\n"
        ),
    ).format(total=len(devices_list), page=pagination.page, pages=pagination.total_pages)

    if pagination.items:
        devices_text += texts.t(
            "DEVICE_MANAGEMENT_CONNECTED_HEADER",
            "<b>Подключенные устройства:</b>\n",
        )
        for i, device in enumerate(pagination.items, 1):
            platform = device.get('platform', 'Unknown')
            device_model = device.get('deviceModel', 'Unknown')
            device_info = f"{platform} - {device_model}"

            if len(device_info) > 35:
                device_info = device_info[:32] + "..."

            devices_text += texts.t(
                "DEVICE_MANAGEMENT_LIST_ITEM",
                "• {device}\n",
            ).format(device=device_info)

    devices_text += texts.t(
        "DEVICE_MANAGEMENT_ACTIONS",
        (
            "\n💡 <b>Действия:</b>\n"
            "• Выберите устройство для сброса\n"
            "• Или сбросьте все устройства сразу"
        ),
    )

    await callback.message.edit_text(
        devices_text,
        reply_markup=get_devices_management_keyboard(
            pagination.items,
            pagination,
            db_user.language
        ),
        parse_mode="HTML"
    )


async def handle_devices_page(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    page = int(callback.data.split('_')[2])
    texts = get_texts(db_user.language)

    try:
        from app.services.remnawave_service import RemnaWaveService
        service = RemnaWaveService()

        async with service.get_api_client() as api:
            response = await api._make_request('GET', f'/api/hwid/devices/{db_user.remnawave_uuid}')

            if response and 'response' in response:
                devices_list = response['response'].get('devices', [])
                await show_devices_page(callback, db_user, devices_list, page=page)
            else:
                await callback.answer(
                    texts.t("DEVICE_FETCH_ERROR", "❌ Ошибка получения устройств"),
                    show_alert=True,
                )

    except Exception as e:
        logger.error(f"Ошибка перехода на страницу устройств: {e}")
        await callback.answer(
            texts.t("DEVICE_PAGE_LOAD_ERROR", "❌ Ошибка загрузки страницы"),
            show_alert=True,
        )


async def handle_single_device_reset(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    try:
        callback_parts = callback.data.split('_')
        if len(callback_parts) < 4:
            logger.error(f"Некорректный формат callback_data: {callback.data}")
            await callback.answer(
                texts.t("DEVICE_RESET_INVALID_REQUEST", "❌ Ошибка: некорректный запрос"),
                show_alert=True,
            )
            return

        device_index = int(callback_parts[2])
        page = int(callback_parts[3])

        logger.info(f"🔧 Сброс устройства: index={device_index}, page={page}")

    except (ValueError, IndexError) as e:
        logger.error(f"❌ Ошибка парсинга callback_data {callback.data}: {e}")
        await callback.answer(
            texts.t("DEVICE_RESET_PARSE_ERROR", "❌ Ошибка обработки запроса"),
            show_alert=True,
        )
        return

    texts = get_texts(db_user.language)

    try:
        from app.services.remnawave_service import RemnaWaveService
        service = RemnaWaveService()

        async with service.get_api_client() as api:
            response = await api._make_request('GET', f'/api/hwid/devices/{db_user.remnawave_uuid}')

            if response and 'response' in response:
                devices_list = response['response'].get('devices', [])

                devices_per_page = 5
                pagination = paginate_list(devices_list, page=page, per_page=devices_per_page)

                if device_index < len(pagination.items):
                    device = pagination.items[device_index]
                    device_hwid = device.get('hwid')

                    if device_hwid:
                        delete_data = {
                            "userUuid": db_user.remnawave_uuid,
                            "hwid": device_hwid
                        }

                        await api._make_request('POST', '/api/hwid/devices/delete', data=delete_data)

                        platform = device.get('platform', 'Unknown')
                        device_model = device.get('deviceModel', 'Unknown')
                        device_info = f"{platform} - {device_model}"

                        await callback.answer(
                            texts.t(
                                "DEVICE_RESET_SUCCESS",
                                "✅ Устройство {device} успешно сброшено!",
                            ).format(device=device_info),
                            show_alert=True,
                        )

                        updated_response = await api._make_request('GET', f'/api/hwid/devices/{db_user.remnawave_uuid}')
                        if updated_response and 'response' in updated_response:
                            updated_devices = updated_response['response'].get('devices', [])

                            if updated_devices:
                                updated_pagination = paginate_list(updated_devices, page=page,
                                                                   per_page=devices_per_page)
                                if not updated_pagination.items and page > 1:
                                    page = page - 1

                                await show_devices_page(callback, db_user, updated_devices, page=page)
                            else:
                                await callback.message.edit_text(
                                    texts.t(
                                        "DEVICE_RESET_ALL_DONE",
                                        "ℹ️ Все устройства сброшены",
                                    ),
                                    reply_markup=get_back_keyboard(db_user.language)
                                )

                        logger.info(f"✅ Пользователь {db_user.telegram_id} сбросил устройство {device_info}")
                    else:
                        await callback.answer(
                            texts.t(
                                "DEVICE_RESET_ID_FAILED",
                                "❌ Не удалось получить ID устройства",
                            ),
                            show_alert=True,
                        )
                else:
                    await callback.answer(
                        texts.t("DEVICE_RESET_NOT_FOUND", "❌ Устройство не найдено"),
                        show_alert=True,
                    )
            else:
                await callback.answer(
                    texts.t("DEVICE_FETCH_ERROR", "❌ Ошибка получения устройств"),
                    show_alert=True,
                )

    except Exception as e:
        logger.error(f"Ошибка сброса устройства: {e}")
        await callback.answer(
            texts.t("DEVICE_RESET_ERROR", "❌ Ошибка сброса устройства"),
            show_alert=True,
        )


async def handle_all_devices_reset_from_management(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    texts = get_texts(db_user.language)

    if not db_user.remnawave_uuid:
        await callback.answer(
            texts.t("DEVICE_UUID_NOT_FOUND", "❌ UUID пользователя не найден"),
            show_alert=True,
        )
        return

    try:
        from app.services.remnawave_service import RemnaWaveService
        service = RemnaWaveService()

        async with service.get_api_client() as api:
            devices_response = await api._make_request('GET', f'/api/hwid/devices/{db_user.remnawave_uuid}')

            if not devices_response or 'response' not in devices_response:
                await callback.answer(
                    texts.t(
                        "DEVICE_LIST_FETCH_ERROR",
                        "❌ Ошибка получения списка устройств",
                    ),
                    show_alert=True,
                )
                return

            devices_list = devices_response['response'].get('devices', [])

            if not devices_list:
                await callback.answer(
                    texts.t("DEVICE_NONE_CONNECTED", "ℹ️ У вас нет подключенных устройств"),
                    show_alert=True,
                )
                return

            logger.info(f"🔧 Найдено {len(devices_list)} устройств для сброса")

            success_count = 0
            failed_count = 0

            for device in devices_list:
                device_hwid = device.get('hwid')
                if device_hwid:
                    try:
                        delete_data = {
                            "userUuid": db_user.remnawave_uuid,
                            "hwid": device_hwid
                        }

                        await api._make_request('POST', '/api/hwid/devices/delete', data=delete_data)
                        success_count += 1
                        logger.info(f"✅ Устройство {device_hwid} удалено")

                    except Exception as device_error:
                        failed_count += 1
                        logger.error(f"❌ Ошибка удаления устройства {device_hwid}: {device_error}")
                else:
                    failed_count += 1
                    logger.warning(f"⚠️ У устройства нет HWID: {device}")

            if success_count > 0:
                if failed_count == 0:
                    await callback.message.edit_text(
                        texts.t(
                            "DEVICE_RESET_ALL_SUCCESS_MESSAGE",
                            (
                                "✅ <b>Все устройства успешно сброшены!</b>\n\n"
                                "🔄 Сброшено: {count} устройств\n"
                                "📱 Теперь вы можете заново подключить свои устройства\n\n"
                                "💡 Используйте ссылку из раздела 'Моя подписка' для повторного подключения"
                            ),
                        ).format(count=success_count),
                        reply_markup=get_back_keyboard(db_user.language),
                        parse_mode="HTML"
                    )
                    logger.info(f"✅ Пользователь {db_user.telegram_id} успешно сбросил {success_count} устройств")
                else:
                    await callback.message.edit_text(
                        texts.t(
                            "DEVICE_RESET_PARTIAL_MESSAGE",
                            (
                                "⚠️ <b>Частичный сброс устройств</b>\n\n"
                                "✅ Удалено: {success} устройств\n"
                                "❌ Не удалось удалить: {failed} устройств\n\n"
                                "Попробуйте еще раз или обратитесь в поддержку."
                            ),
                        ).format(success=success_count, failed=failed_count),
                        reply_markup=get_back_keyboard(db_user.language),
                        parse_mode="HTML"
                    )
                    logger.warning(
                        f"⚠️ Частичный сброс у пользователя {db_user.telegram_id}: {success_count}/{len(devices_list)}")
            else:
                await callback.message.edit_text(
                    texts.t(
                        "DEVICE_RESET_ALL_FAILED_MESSAGE",
                        (
                            "❌ <b>Не удалось сбросить устройства</b>\n\n"
                            "Попробуйте еще раз позже или обратитесь в техподдержку.\n\n"
                            "Всего устройств: {total}"
                        ),
                    ).format(total=len(devices_list)),
                    reply_markup=get_back_keyboard(db_user.language),
                    parse_mode="HTML"
                )
                logger.error(f"❌ Не удалось сбросить ни одного устройства у пользователя {db_user.telegram_id}")

    except Exception as e:
        logger.error(f"Ошибка сброса всех устройств: {e}")
        await callback.message.edit_text(
            texts.ERROR,
            reply_markup=get_back_keyboard(db_user.language)
        )

    await callback.answer()


async def handle_extend_subscription(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    texts = get_texts(db_user.language)
    subscription = db_user.subscription

    if not subscription or subscription.is_trial:
        await callback.answer("⚠ Продление доступно только для платных подписок", show_alert=True)
        return

    subscription_service = SubscriptionService()

    available_periods = settings.get_available_renewal_periods()
    renewal_prices = {}
    promo_offer_percent = _get_promo_offer_discount_percent(db_user)

    for days in available_periods:
        try:
            months_in_period = calculate_months_from_days(days)

            from app.config import PERIOD_PRICES
            base_price_original = PERIOD_PRICES.get(days, 0)
            period_discount_percent = db_user.get_promo_discount("period", days)
            base_price, _ = apply_percentage_discount(
                base_price_original,
                period_discount_percent,
            )

            servers_price_per_month, _ = await subscription_service.get_countries_price_by_uuids(
                subscription.connected_squads,
                db,
                promo_group_id=db_user.promo_group_id,
            )
            servers_discount_percent = db_user.get_promo_discount(
                "servers",
                days,
            )
            servers_discount_per_month = servers_price_per_month * servers_discount_percent // 100
            total_servers_price = (servers_price_per_month - servers_discount_per_month) * months_in_period

            additional_devices = max(0, subscription.device_limit - settings.DEFAULT_DEVICE_LIMIT)
            devices_price_per_month = additional_devices * settings.PRICE_PER_DEVICE
            devices_discount_percent = db_user.get_promo_discount(
                "devices",
                days,
            )
            devices_discount_per_month = devices_price_per_month * devices_discount_percent // 100
            total_devices_price = (devices_price_per_month - devices_discount_per_month) * months_in_period

            traffic_price_per_month = settings.get_traffic_price(subscription.traffic_limit_gb)
            traffic_discount_percent = db_user.get_promo_discount(
                "traffic",
                days,
            )
            traffic_discount_per_month = traffic_price_per_month * traffic_discount_percent // 100
            total_traffic_price = (traffic_price_per_month - traffic_discount_per_month) * months_in_period

            price = base_price + total_servers_price + total_devices_price + total_traffic_price
            promo_component = _apply_promo_offer_discount(db_user, price)
            renewal_prices[days] = promo_component["discounted"]

        except Exception as e:
            logger.error(f"Ошибка расчета цены для периода {days}: {e}")
            continue

    if not renewal_prices:
        await callback.answer("⚠ Нет доступных периодов для продления", show_alert=True)
        return

    prices_text = ""

    for days in available_periods:
        if days in renewal_prices:
            period_display = format_period_description(days, db_user.language)
            prices_text += f"📅 {period_display} - {texts.format_price(renewal_prices[days])}\n"

    promo_discounts_text = _build_promo_group_discount_text(
        db_user,
        available_periods,
        texts=texts,
    )

    message_text = (
        "⏰ Продление подписки\n\n"
        f"Осталось дней: {subscription.days_left}\n\n"
        f"<b>Ваша текущая конфигурация:</b>\n"
        f"🌍 Серверов: {len(subscription.connected_squads)}\n"
        f"📊 Трафик: {texts.format_traffic(subscription.traffic_limit_gb)}\n"
        f"📱 Устройств: {subscription.device_limit}\n\n"
        f"<b>Выберите период продления:</b>\n"
        f"{prices_text.rstrip()}\n\n"
    )

    if promo_discounts_text:
        message_text += f"{promo_discounts_text}\n\n"

    promo_offer_hint = await _get_promo_offer_hint(
        db,
        db_user,
        texts,
        promo_offer_percent,
    )
    if promo_offer_hint:
        message_text += f"{promo_offer_hint}\n\n"

    message_text += "💡 <i>Цена включает все ваши текущие серверы и настройки</i>"

    await callback.message.edit_text(
        message_text,
        reply_markup=get_extend_subscription_keyboard_with_prices(db_user.language, renewal_prices),
        parse_mode="HTML"
    )

    await callback.answer()


async def handle_reset_traffic(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    from app.config import settings

    if settings.is_traffic_fixed():
        await callback.answer("⚠️ В текущем режиме трафик фиксированный и не может быть сброшен", show_alert=True)
        return

    texts = get_texts(db_user.language)
    subscription = db_user.subscription

    if not subscription or subscription.is_trial:
        await callback.answer("⌛ Эта функция доступна только для платных подписок", show_alert=True)
        return

    if subscription.traffic_limit_gb == 0:
        await callback.answer("⌛ У вас безлимитный трафик", show_alert=True)
        return

    reset_price = PERIOD_PRICES[30]

    if db_user.balance_kopeks < reset_price:
        await callback.answer("⌛ Недостаточно средств на балансе", show_alert=True)
        return

    await callback.message.edit_text(
        f"🔄 <b>Сброс трафика</b>\n\n"
        f"Использовано: {texts.format_traffic(subscription.traffic_used_gb)}\n"
        f"Лимит: {texts.format_traffic(subscription.traffic_limit_gb)}\n\n"
        f"Стоимость сброса: {texts.format_price(reset_price)}\n\n"
        "После сброса счетчик использованного трафика станет равным 0.",
        reply_markup=get_reset_traffic_confirm_keyboard(reset_price, db_user.language)
    )

    await callback.answer()


def update_traffic_prices():
    from app.config import refresh_traffic_prices
    refresh_traffic_prices()
    logger.info("🔄 TRAFFIC_PRICES обновлены из конфигурации")


async def confirm_add_devices(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    devices_count = int(callback.data.split('_')[2])
    texts = get_texts(db_user.language)
    subscription = db_user.subscription

    resume_callback = None

    new_total_devices = subscription.device_limit + devices_count

    if settings.MAX_DEVICES_LIMIT > 0 and new_total_devices > settings.MAX_DEVICES_LIMIT:
        await callback.answer(
            f"⚠️ Превышен максимальный лимит устройств ({settings.MAX_DEVICES_LIMIT}). "
            f"У вас: {subscription.device_limit}, добавляете: {devices_count}",
            show_alert=True
        )
        return

    devices_price_per_month = devices_count * settings.PRICE_PER_DEVICE
    months_hint = get_remaining_months(subscription.end_date)
    period_hint_days = months_hint * 30 if months_hint > 0 else None
    devices_discount_percent = _get_addon_discount_percent_for_user(
        db_user,
        "devices",
        period_hint_days,
    )
    discounted_per_month, discount_per_month = apply_percentage_discount(
        devices_price_per_month,
        devices_discount_percent,
    )
    price, charged_months = calculate_prorated_price(
        discounted_per_month,
        subscription.end_date,
    )
    total_discount = discount_per_month * charged_months

    logger.info(
        "Добавление %s устройств: %.2f₽/мес × %s мес = %.2f₽ (скидка %.2f₽)",
        devices_count,
        discounted_per_month / 100,
        charged_months,
        price / 100,
        total_discount / 100,
    )

    if db_user.balance_kopeks < price:
        missing_kopeks = price - db_user.balance_kopeks
        required_text = f"{texts.format_price(price)} (за {charged_months} мес)"
        message_text = texts.t(
            "ADDON_INSUFFICIENT_FUNDS_MESSAGE",
            (
                "⚠️ <b>Недостаточно средств</b>\n\n"
                "Стоимость услуги: {required}\n"
                "На балансе: {balance}\n"
                "Не хватает: {missing}\n\n"
                "Выберите способ пополнения. Сумма подставится автоматически."
            ),
        ).format(
            required=required_text,
            balance=texts.format_price(db_user.balance_kopeks),
            missing=texts.format_price(missing_kopeks),
        )

        await callback.message.edit_text(
            message_text,
            reply_markup=get_insufficient_balance_keyboard(
                db_user.language,
                resume_callback=resume_callback,
                amount_kopeks=missing_kopeks,
            ),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    try:
        success = await subtract_user_balance(
            db, db_user, price,
            f"Добавление {devices_count} устройств на {charged_months} мес"
        )

        if not success:
            await callback.answer("⚠️ Ошибка списания средств", show_alert=True)
            return

        await add_subscription_devices(db, subscription, devices_count)

        subscription_service = SubscriptionService()
        await subscription_service.update_remnawave_user(db, subscription)

        await create_transaction(
            db=db,
            user_id=db_user.id,
            type=TransactionType.SUBSCRIPTION_PAYMENT,
            amount_kopeks=price,
            description=f"Добавление {devices_count} устройств на {charged_months} мес"
        )

        await db.refresh(db_user)
        await db.refresh(subscription)

        success_text = (
            "✅ Устройства успешно добавлены!\n\n"
            f"📱 Добавлено: {devices_count} устройств\n"
            f"Новый лимит: {subscription.device_limit} устройств\n"
        )
        success_text += f"💰 Списано: {texts.format_price(price)} (за {charged_months} мес)"
        if total_discount > 0:
            success_text += (
                f" (скидка {devices_discount_percent}%:"
                f" -{texts.format_price(total_discount)})"
            )

        await callback.message.edit_text(
            success_text,
            reply_markup=get_back_keyboard(db_user.language)
        )

        logger.info(f"✅ Пользователь {db_user.telegram_id} добавил {devices_count} устройств за {price / 100}₽")

    except Exception as e:
        logger.error(f"Ошибка добавления устройств: {e}")
        await callback.message.edit_text(
            texts.ERROR,
            reply_markup=get_back_keyboard(db_user.language)
        )

    await callback.answer()


async def confirm_extend_subscription(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    from app.services.admin_notification_service import AdminNotificationService

    days = int(callback.data.split('_')[2])
    texts = get_texts(db_user.language)
    subscription = db_user.subscription

    if not subscription:
        await callback.answer("⚠ У вас нет активной подписки", show_alert=True)
        return

    months_in_period = calculate_months_from_days(days)
    old_end_date = subscription.end_date
    server_uuid_prices: Dict[str, int] = {}

    try:
        from app.config import PERIOD_PRICES

        base_price_original = PERIOD_PRICES.get(days, 0)
        period_discount_percent = db_user.get_promo_discount("period", days)
        base_price, base_discount_total = apply_percentage_discount(
            base_price_original,
            period_discount_percent,
        )

        subscription_service = SubscriptionService()
        servers_price_per_month, per_server_monthly_prices = await subscription_service.get_countries_price_by_uuids(
            subscription.connected_squads,
            db,
            promo_group_id=db_user.promo_group_id,
        )
        servers_discount_percent = db_user.get_promo_discount(
            "servers",
            days,
        )
        total_servers_price = 0
        total_servers_discount = 0

        for squad_uuid, server_monthly_price in zip(subscription.connected_squads, per_server_monthly_prices):
            discount_per_month = server_monthly_price * servers_discount_percent // 100
            discounted_per_month = server_monthly_price - discount_per_month
            total_servers_price += discounted_per_month * months_in_period
            total_servers_discount += discount_per_month * months_in_period
            server_uuid_prices[squad_uuid] = discounted_per_month * months_in_period

        discounted_servers_price_per_month = servers_price_per_month - (
                servers_price_per_month * servers_discount_percent // 100
        )

        additional_devices = max(0, subscription.device_limit - settings.DEFAULT_DEVICE_LIMIT)
        devices_price_per_month = additional_devices * settings.PRICE_PER_DEVICE
        devices_discount_percent = db_user.get_promo_discount(
            "devices",
            days,
        )
        devices_discount_per_month = devices_price_per_month * devices_discount_percent // 100
        discounted_devices_price_per_month = devices_price_per_month - devices_discount_per_month
        total_devices_price = discounted_devices_price_per_month * months_in_period

        traffic_price_per_month = settings.get_traffic_price(subscription.traffic_limit_gb)
        traffic_discount_percent = db_user.get_promo_discount(
            "traffic",
            days,
        )
        traffic_discount_per_month = traffic_price_per_month * traffic_discount_percent // 100
        discounted_traffic_price_per_month = traffic_price_per_month - traffic_discount_per_month
        total_traffic_price = discounted_traffic_price_per_month * months_in_period

        price = base_price + total_servers_price + total_devices_price + total_traffic_price
        original_price = price
        promo_component = _apply_promo_offer_discount(db_user, price)
        if promo_component["discount"] > 0:
            price = promo_component["discounted"]

        monthly_additions = (
                discounted_servers_price_per_month
                + discounted_devices_price_per_month
                + discounted_traffic_price_per_month
        )
        is_valid = validate_pricing_calculation(base_price, monthly_additions, months_in_period, original_price)

        if not is_valid:
            logger.error(f"Ошибка в расчете цены продления для пользователя {db_user.telegram_id}")
            await callback.answer("Ошибка расчета цены. Обратитесь в поддержку.", show_alert=True)
            return

        logger.info(f"💰 Расчет продления подписки {subscription.id} на {days} дней ({months_in_period} мес):")
        base_log = f"   📅 Период {days} дней: {base_price_original / 100}₽"
        if base_discount_total > 0:
            base_log += (
                f" → {base_price / 100}₽"
                f" (скидка {period_discount_percent}%: -{base_discount_total / 100}₽)"
            )
        logger.info(base_log)
        if total_servers_price > 0:
            logger.info(
                f"   🌐 Серверы: {servers_price_per_month / 100}₽/мес × {months_in_period}"
                f" = {total_servers_price / 100}₽"
                + (
                    f" (скидка {servers_discount_percent}%:"
                    f" -{total_servers_discount / 100}₽)"
                    if total_servers_discount > 0
                    else ""
                )
            )
        if total_devices_price > 0:
            logger.info(
                f"   📱 Устройства: {devices_price_per_month / 100}₽/мес × {months_in_period}"
                f" = {total_devices_price / 100}₽"
                + (
                    f" (скидка {devices_discount_percent}%:"
                    f" -{devices_discount_per_month * months_in_period / 100}₽)"
                    if devices_discount_percent > 0 and devices_discount_per_month > 0
                    else ""
                )
            )
        if total_traffic_price > 0:
            logger.info(
                f"   📊 Трафик: {traffic_price_per_month / 100}₽/мес × {months_in_period}"
                f" = {total_traffic_price / 100}₽"
                + (
                    f" (скидка {traffic_discount_percent}%:"
                    f" -{traffic_discount_per_month * months_in_period / 100}₽)"
                    if traffic_discount_percent > 0 and traffic_discount_per_month > 0
                    else ""
                )
            )
        if promo_component["discount"] > 0:
            logger.info(
                "   🎯 Промо-предложение: -%s₽ (%s%%)",
                promo_component["discount"] / 100,
                promo_component["percent"],
            )
        logger.info(f"   💎 ИТОГО: {price / 100}₽")

    except Exception as e:
        logger.error(f"⚠ ОШИБКА РАСЧЕТА ЦЕНЫ: {e}")
        await callback.answer("⚠ Ошибка расчета стоимости", show_alert=True)
        return

    if db_user.balance_kopeks < price:
        missing_kopeks = price - db_user.balance_kopeks
        required_text = texts.format_price(price)
        message_text = texts.t(
            "ADDON_INSUFFICIENT_FUNDS_MESSAGE",
            (
                "⚠️ <b>Недостаточно средств</b>\n\n"
                "Стоимость услуги: {required}\n"
                "На балансе: {balance}\n"
                "Не хватает: {missing}\n\n"
                "Выберите способ пополнения. Сумма подставится автоматически."
            ),
        ).format(
            required=required_text,
            balance=texts.format_price(db_user.balance_kopeks),
            missing=texts.format_price(missing_kopeks),
        )

        await callback.message.edit_text(
            message_text,
            reply_markup=get_insufficient_balance_keyboard(
                db_user.language,
                amount_kopeks=missing_kopeks,
            ),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    try:
        success = await subtract_user_balance(
            db,
            db_user,
            price,
            f"Продление подписки на {days} дней",
            consume_promo_offer=promo_component["discount"] > 0,
        )

        if not success:
            await callback.answer("⚠ Ошибка списания средств", show_alert=True)
            return

        current_time = datetime.utcnow()

        if subscription.end_date > current_time:
            new_end_date = subscription.end_date + timedelta(days=days)
        else:
            new_end_date = current_time + timedelta(days=days)

        subscription.end_date = new_end_date

        subscription.status = SubscriptionStatus.ACTIVE.value
        subscription.updated_at = current_time

        await db.commit()
        await db.refresh(subscription)
        await db.refresh(db_user)

        # ensure freshly loaded values are available even if SQLAlchemy expires
        # attributes on subsequent access
        refreshed_end_date = subscription.end_date
        refreshed_balance = db_user.balance_kopeks

        from app.database.crud.server_squad import get_server_ids_by_uuids
        from app.database.crud.subscription import add_subscription_servers

        server_ids = await get_server_ids_by_uuids(db, subscription.connected_squads)
        if server_ids:
            from sqlalchemy import select
            from app.database.models import ServerSquad

            result = await db.execute(
                select(ServerSquad.id, ServerSquad.squad_uuid).where(ServerSquad.id.in_(server_ids))
            )
            id_to_uuid = {row.id: row.squad_uuid for row in result}
            default_price = total_servers_price // len(server_ids) if server_ids else 0
            server_prices_for_period = [
                server_uuid_prices.get(id_to_uuid.get(server_id, ""), default_price)
                for server_id in server_ids
            ]
            await add_subscription_servers(db, subscription, server_ids, server_prices_for_period)

        try:
            remnawave_result = await subscription_service.update_remnawave_user(
                db,
                subscription,
                reset_traffic=settings.RESET_TRAFFIC_ON_PAYMENT,
                reset_reason="продление подписки",
            )
            if remnawave_result:
                logger.info("✅ RemnaWave обновлен успешно")
            else:
                logger.error("⚠ ОШИБКА ОБНОВЛЕНИЯ REMNAWAVE")
        except Exception as e:
            logger.error(f"⚠ ИСКЛЮЧЕНИЕ ПРИ ОБНОВЛЕНИИ REMNAWAVE: {e}")

        transaction = await create_transaction(
            db=db,
            user_id=db_user.id,
            type=TransactionType.SUBSCRIPTION_PAYMENT,
            amount_kopeks=price,
            description=f"Продление подписки на {days} дней ({months_in_period} мес)"
        )

        try:
            notification_service = AdminNotificationService(callback.bot)
            await notification_service.send_subscription_extension_notification(
                db,
                db_user,
                subscription,
                transaction,
                days,
                old_end_date,
                new_end_date=refreshed_end_date,
                balance_after=refreshed_balance,
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления о продлении: {e}")

        success_message = (
            "✅ Подписка успешно продлена!\n\n"
            f"⏰ Добавлено: {days} дней\n"
            f"Действует до: {refreshed_end_date.strftime('%d.%m.%Y %H:%M')}\n\n"
            f"💰 Списано: {texts.format_price(price)}"
        )

        if promo_component["discount"] > 0:
            success_message += (
                f" (включая доп. скидку {promo_component['percent']}%:"
                f" -{texts.format_price(promo_component['discount'])})"
            )

        await callback.message.edit_text(
            success_message,
            reply_markup=get_back_keyboard(db_user.language)
        )

        logger.info(f"✅ Пользователь {db_user.telegram_id} продлил подписку на {days} дней за {price / 100}₽")

    except Exception as e:
        logger.error(f"⚠ КРИТИЧЕСКАЯ ОШИБКА ПРОДЛЕНИЯ: {e}")
        import traceback
        logger.error(f"TRACEBACK: {traceback.format_exc()}")

        await callback.message.edit_text(
            "⚠ Произошла ошибка при продлении подписки. Обратитесь в поддержку.",
            reply_markup=get_back_keyboard(db_user.language)
        )

    await callback.answer()


async def confirm_reset_traffic(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    from app.config import settings

    if settings.is_traffic_fixed():
        await callback.answer("⚠️ В текущем режиме трафик фиксированный", show_alert=True)
        return

    texts = get_texts(db_user.language)
    subscription = db_user.subscription

    reset_price = PERIOD_PRICES[30]

    if db_user.balance_kopeks < reset_price:
        missing_kopeks = reset_price - db_user.balance_kopeks
        message_text = texts.t(
            "ADDON_INSUFFICIENT_FUNDS_MESSAGE",
            (
                "⚠️ <b>Недостаточно средств</b>\n\n"
                "Стоимость услуги: {required}\n"
                "На балансе: {balance}\n"
                "Не хватает: {missing}\n\n"
                "Выберите способ пополнения. Сумма подставится автоматически."
            ),
        ).format(
            required=texts.format_price(reset_price),
            balance=texts.format_price(db_user.balance_kopeks),
            missing=texts.format_price(missing_kopeks),
        )

        await callback.message.edit_text(
            message_text,
            reply_markup=get_insufficient_balance_keyboard(
                db_user.language,
                amount_kopeks=missing_kopeks,
            ),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    try:
        success = await subtract_user_balance(
            db, db_user, reset_price,
            "Сброс трафика"
        )

        if not success:
            await callback.answer("⌛ Ошибка списания средств", show_alert=True)
            return

        subscription.traffic_used_gb = 0.0
        subscription.updated_at = datetime.utcnow()
        await db.commit()

        subscription_service = SubscriptionService()
        remnawave_service = RemnaWaveService()

        user = db_user
        if user.remnawave_uuid:
            async with remnawave_service.get_api_client() as api:
                await api.reset_user_traffic(user.remnawave_uuid)

        await create_transaction(
            db=db,
            user_id=db_user.id,
            type=TransactionType.SUBSCRIPTION_PAYMENT,
            amount_kopeks=reset_price,
            description="Сброс трафика"
        )

        await db.refresh(db_user)
        await db.refresh(subscription)

        await callback.message.edit_text(
            f"✅ Трафик успешно сброшен!\n\n"
            f"🔄 Использованный трафик обнулен\n"
            f"📊 Лимит: {texts.format_traffic(subscription.traffic_limit_gb)}",
            reply_markup=get_back_keyboard(db_user.language)
        )

        logger.info(f"✅ Пользователь {db_user.telegram_id} сбросил трафик")

    except Exception as e:
        logger.error(f"Ошибка сброса трафика: {e}")
        await callback.message.edit_text(
            texts.ERROR,
            reply_markup=get_back_keyboard(db_user.language)
        )

    await callback.answer()


async def select_period(
        callback: types.CallbackQuery,
        state: FSMContext,
        db_user: User
):
    period_days = int(callback.data.split('_')[1])
    texts = get_texts(db_user.language)

    data = await state.get_data()
    data['period_days'] = period_days
    data['total_price'] = PERIOD_PRICES[period_days]

    if settings.is_traffic_fixed():
        fixed_traffic_price = settings.get_traffic_price(settings.get_fixed_traffic_limit())
        data['total_price'] += fixed_traffic_price
        data['traffic_gb'] = settings.get_fixed_traffic_limit()

    await state.set_data(data)

    if settings.is_traffic_selectable():
        available_packages = [pkg for pkg in settings.get_traffic_packages() if pkg['enabled']]

        if not available_packages:
            await callback.answer("⚠️ Пакеты трафика не настроены", show_alert=True)
            return

        await callback.message.edit_text(
            texts.SELECT_TRAFFIC,
            reply_markup=get_traffic_packages_keyboard(db_user.language)
        )
        await state.set_state(SubscriptionStates.selecting_traffic)
    else:
        if await _should_show_countries_management(db_user):
            countries = await _get_available_countries(db_user.promo_group_id)
            await callback.message.edit_text(
                texts.SELECT_COUNTRIES,
                reply_markup=get_countries_keyboard(countries, [], db_user.language)
            )
            await state.set_state(SubscriptionStates.selecting_countries)
        else:
            countries = await _get_available_countries(db_user.promo_group_id)
            available_countries = [c for c in countries if c.get('is_available', True)]
            data['countries'] = [available_countries[0]['uuid']] if available_countries else []
            await state.set_data(data)

            selected_devices = data.get('devices', settings.DEFAULT_DEVICE_LIMIT)

            await callback.message.edit_text(
                texts.SELECT_DEVICES,
                reply_markup=get_devices_keyboard(selected_devices, db_user.language)
            )
            await state.set_state(SubscriptionStates.selecting_devices)

    await callback.answer()


async def refresh_traffic_config():
    try:
        from app.config import refresh_traffic_prices
        refresh_traffic_prices()

        packages = settings.get_traffic_packages()
        enabled_count = sum(1 for pkg in packages if pkg['enabled'])

        logger.info(f"🔄 Конфигурация трафика обновлена: {enabled_count} активных пакетов")
        for pkg in packages:
            if pkg['enabled']:
                gb_text = "♾️ Безлимит" if pkg['gb'] == 0 else f"{pkg['gb']} ГБ"
                logger.info(f"   📦 {gb_text}: {pkg['price'] / 100}₽")

        return True

    except Exception as e:
        logger.error(f"⚠️ Ошибка обновления конфигурации трафика: {e}")
        return False


async def get_traffic_packages_info() -> str:
    try:
        packages = settings.get_traffic_packages()

        info_lines = ["📦 Настроенные пакеты трафика:"]

        enabled_packages = [pkg for pkg in packages if pkg['enabled']]
        disabled_packages = [pkg for pkg in packages if not pkg['enabled']]

        if enabled_packages:
            info_lines.append("\n✅ Активные:")
            for pkg in enabled_packages:
                gb_text = "♾️ Безлимит" if pkg['gb'] == 0 else f"{pkg['gb']} ГБ"
                info_lines.append(f"   • {gb_text}: {pkg['price'] // 100}₽")

        if disabled_packages:
            info_lines.append("\n❌ Отключенные:")
            for pkg in disabled_packages:
                gb_text = "♾️ Безлимит" if pkg['gb'] == 0 else f"{pkg['gb']} ГБ"
                info_lines.append(f"   • {gb_text}: {pkg['price'] // 100}₽")

        info_lines.append(f"\n📊 Всего пакетов: {len(packages)}")
        info_lines.append(f"🟢 Активных: {len(enabled_packages)}")
        info_lines.append(f"🔴 Отключенных: {len(disabled_packages)}")

        return "\n".join(info_lines)

    except Exception as e:
        return f"⚠️ Ошибка получения информации: {e}"


async def get_subscription_info_text(subscription, texts, db_user, db: AsyncSession):
    devices_used = await get_current_devices_count(db_user)
    countries_info = await _get_countries_info(subscription.connected_squads)
    countries_text = ", ".join([c['name'] for c in countries_info]) if countries_info else "Нет"

    subscription_url = getattr(subscription, 'subscription_url', None) or "Генерируется..."

    if subscription.is_trial:
        status_text = "🎁 Тестовая"
        type_text = "Триал"
    else:
        if subscription.is_active:
            status_text = "✅ Оплачена"
        else:
            status_text = "⌛ Истекла"
        type_text = "Платная подписка"

    if subscription.traffic_limit_gb == 0:
        if settings.is_traffic_fixed():
            traffic_text = "∞ Безлимитный"
        else:
            traffic_text = "∞ Безлимитный"
    else:
        if settings.is_traffic_fixed():
            traffic_text = f"{subscription.traffic_limit_gb} ГБ"
        else:
            traffic_text = f"{subscription.traffic_limit_gb} ГБ"

    subscription_cost = await get_subscription_cost(subscription, db)

    info_text = texts.SUBSCRIPTION_INFO.format(
        status=status_text,
        type=type_text,
        end_date=subscription.end_date.strftime("%d.%m.%Y %H:%M"),
        days_left=max(0, subscription.days_left),
        traffic_used=texts.format_traffic(subscription.traffic_used_gb),
        traffic_limit=traffic_text,
        countries_count=len(subscription.connected_squads),
        devices_used=devices_used,
        devices_limit=subscription.device_limit,
        autopay_status="✅ Включен" if subscription.autopay_enabled else "⌛ Выключен"
    )

    if subscription_cost > 0:
        info_text += f"\n💰 <b>Стоимость подписки в месяц:</b> {texts.format_price(subscription_cost)}"

    if (
            subscription_url
            and subscription_url != "Генерируется..."
            and not settings.should_hide_subscription_link()
    ):
        info_text += f"\n\n🔗 <b>Ваша ссылка для импорта в VPN приложениe:</b>\n<code>{subscription_url}</code>"

    return info_text


def format_traffic_display(traffic_gb: int, is_fixed_mode: bool = None) -> str:
    if is_fixed_mode is None:
        is_fixed_mode = settings.is_traffic_fixed()

    if traffic_gb == 0:
        if is_fixed_mode:
            return "Безлимитный"
        else:
            return "Безлимитный"
    else:
        if is_fixed_mode:
            return f"{traffic_gb} ГБ"
        else:
            return f"{traffic_gb} ГБ"


async def select_traffic(
        callback: types.CallbackQuery,
        state: FSMContext,
        db_user: User
):
    traffic_gb = int(callback.data.split('_')[1])
    texts = get_texts(db_user.language)

    data = await state.get_data()
    data['traffic_gb'] = traffic_gb

    traffic_price = settings.get_traffic_price(traffic_gb)
    data['total_price'] += traffic_price

    await state.set_data(data)

    if await _should_show_countries_management(db_user):
        countries = await _get_available_countries(db_user.promo_group_id)
        await callback.message.edit_text(
            texts.SELECT_COUNTRIES,
            reply_markup=get_countries_keyboard(countries, [], db_user.language)
        )
        await state.set_state(SubscriptionStates.selecting_countries)
    else:
        countries = await _get_available_countries(db_user.promo_group_id)
        available_countries = [c for c in countries if c.get('is_available', True)]
        data['countries'] = [available_countries[0]['uuid']] if available_countries else []
        await state.set_data(data)

        selected_devices = data.get('devices', settings.DEFAULT_DEVICE_LIMIT)

        await callback.message.edit_text(
            texts.SELECT_DEVICES,
            reply_markup=get_devices_keyboard(selected_devices, db_user.language)
        )
        await state.set_state(SubscriptionStates.selecting_devices)

    await callback.answer()


async def select_country(
        callback: types.CallbackQuery,
        state: FSMContext,
        db_user: User,
        db: AsyncSession
):
    country_uuid = callback.data.split('_')[1]
    data = await state.get_data()

    selected_countries = data.get('countries', [])
    if country_uuid in selected_countries:
        selected_countries.remove(country_uuid)
    else:
        selected_countries.append(country_uuid)

    countries = await _get_available_countries(db_user.promo_group_id)
    allowed_country_ids = {country['uuid'] for country in countries}

    if country_uuid not in allowed_country_ids and country_uuid not in selected_countries:
        await callback.answer("❌ Сервер недоступен для вашей промогруппы", show_alert=True)
        return

    period_base_price = PERIOD_PRICES[data['period_days']]

    discounted_base_price, _ = apply_percentage_discount(
        period_base_price,
        db_user.get_promo_discount("period", data['period_days']),
    )

    base_price = discounted_base_price + settings.get_traffic_price(data['traffic_gb'])

    try:
        subscription_service = SubscriptionService()
        countries_price, _ = await subscription_service.get_countries_price_by_uuids(
            selected_countries,
            db,
            promo_group_id=db_user.promo_group_id,
        )
    except AttributeError:
        logger.warning("Используем fallback функцию для расчета цен стран")
        countries_price, _ = await get_countries_price_by_uuids_fallback(
            selected_countries,
            db,
            promo_group_id=db_user.promo_group_id,
        )

    data['countries'] = selected_countries
    data['total_price'] = base_price + countries_price
    await state.set_data(data)

    await callback.message.edit_reply_markup(
        reply_markup=get_countries_keyboard(countries, selected_countries, db_user.language)
    )
    await callback.answer()


async def countries_continue(
        callback: types.CallbackQuery,
        state: FSMContext,
        db_user: User
):
    data = await state.get_data()
    texts = get_texts(db_user.language)

    if not data.get('countries'):
        await callback.answer("⚠️ Выберите хотя бы одну страну!", show_alert=True)
        return

    selected_devices = data.get('devices', settings.DEFAULT_DEVICE_LIMIT)

    await callback.message.edit_text(
        texts.SELECT_DEVICES,
        reply_markup=get_devices_keyboard(selected_devices, db_user.language)
    )

    await state.set_state(SubscriptionStates.selecting_devices)
    await callback.answer()


async def select_devices(
        callback: types.CallbackQuery,
        state: FSMContext,
        db_user: User
):
    if not callback.data.startswith("devices_") or callback.data == "devices_continue":
        await callback.answer("❌ Некорректный запрос", show_alert=True)
        return

    try:
        devices = int(callback.data.split('_')[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Некорректное количество устройств", show_alert=True)
        return

    data = await state.get_data()

    base_price = (
            PERIOD_PRICES[data['period_days']] +
            settings.get_traffic_price(data['traffic_gb'])
    )

    countries = await _get_available_countries(db_user.promo_group_id)
    countries_price = sum(
        c['price_kopeks'] for c in countries
        if c['uuid'] in data['countries']
    )

    devices_price = max(0, devices - settings.DEFAULT_DEVICE_LIMIT) * settings.PRICE_PER_DEVICE

    data['devices'] = devices
    data['total_price'] = base_price + countries_price + devices_price
    await state.set_data(data)

    await callback.message.edit_reply_markup(
        reply_markup=get_devices_keyboard(devices, db_user.language)
    )
    await callback.answer()


async def devices_continue(
        callback: types.CallbackQuery,
        state: FSMContext,
        db_user: User,
        db: AsyncSession
):
    if not callback.data == "devices_continue":
        await callback.answer("⚠️ Некорректный запрос", show_alert=True)
        return

    data = await state.get_data()
    texts = get_texts(db_user.language)

    try:
        summary_text, prepared_data = await _prepare_subscription_summary(db_user, data, texts)
    except ValueError:
        logger.error(f"Ошибка в расчете цены подписки для пользователя {db_user.telegram_id}")
        await callback.answer("Ошибка расчета цены. Обратитесь в поддержку.", show_alert=True)
        return

    await state.set_data(prepared_data)
    await save_subscription_checkout_draft(db_user.id, prepared_data)

    await callback.message.edit_text(
        summary_text,
        reply_markup=get_subscription_confirm_keyboard(db_user.language),
        parse_mode="HTML",
    )

    await state.set_state(SubscriptionStates.confirming_purchase)
    await callback.answer()


async def confirm_purchase(
        callback: types.CallbackQuery,
        state: FSMContext,
        db_user: User,
        db: AsyncSession
):
    from app.services.admin_notification_service import AdminNotificationService

    data = await state.get_data()
    texts = get_texts(db_user.language)

    await save_subscription_checkout_draft(db_user.id, dict(data))
    resume_callback = (
        "subscription_resume_checkout"
        if should_offer_checkout_resume(db_user, True)
        else None
    )

    countries = await _get_available_countries(db_user.promo_group_id)

    months_in_period = data.get(
        'months_in_period', calculate_months_from_days(data['period_days'])
    )

    base_price = data.get('base_price')
    base_price_original = data.get('base_price_original')
    base_discount_percent = data.get('base_discount_percent')
    base_discount_total = data.get('base_discount_total')

    if base_price is None:
        base_price_original = PERIOD_PRICES[data['period_days']]
        base_discount_percent = db_user.get_promo_discount(
            "period",
            data['period_days'],
        )
        base_price, base_discount_total = apply_percentage_discount(
            base_price_original,
            base_discount_percent,
        )
    else:
        if base_price_original is None:
            base_price_original = PERIOD_PRICES[data['period_days']]
        if base_discount_percent is None:
            base_discount_percent = db_user.get_promo_discount(
                "period",
                data['period_days'],
            )
        if base_discount_total is None:
            _, base_discount_total = apply_percentage_discount(
                base_price_original,
                base_discount_percent,
            )
    server_prices = data.get('server_prices_for_period', [])

    if not server_prices:
        countries_price_per_month = 0
        per_month_prices: List[int] = []
        for country in countries:
            if country['uuid'] in data['countries']:
                server_price_per_month = country['price_kopeks']
                countries_price_per_month += server_price_per_month
                per_month_prices.append(server_price_per_month)

        servers_discount_percent = db_user.get_promo_discount(
            "servers",
            data['period_days'],
        )
        total_servers_price = 0
        total_servers_discount = 0
        discounted_servers_price_per_month = 0
        server_prices = []

        for server_price_per_month in per_month_prices:
            discounted_per_month, discount_per_month = apply_percentage_discount(
                server_price_per_month,
                servers_discount_percent,
            )
            total_price_for_server = discounted_per_month * months_in_period
            total_discount_for_server = discount_per_month * months_in_period

            discounted_servers_price_per_month += discounted_per_month
            total_servers_price += total_price_for_server
            total_servers_discount += total_discount_for_server
            server_prices.append(total_price_for_server)

        total_countries_price = total_servers_price
    else:
        total_countries_price = data.get('total_servers_price', sum(server_prices))
        countries_price_per_month = data.get('servers_price_per_month', 0)
        discounted_servers_price_per_month = data.get('servers_discounted_price_per_month', countries_price_per_month)
        total_servers_discount = data.get('servers_discount_total', 0)
        servers_discount_percent = data.get('servers_discount_percent', 0)

    additional_devices = max(0, data['devices'] - settings.DEFAULT_DEVICE_LIMIT)
    devices_price_per_month = data.get(
        'devices_price_per_month', additional_devices * settings.PRICE_PER_DEVICE
    )
    if 'devices_discount_percent' in data:
        devices_discount_percent = data.get('devices_discount_percent', 0)
        discounted_devices_price_per_month = data.get(
            'devices_discounted_price_per_month', devices_price_per_month
        )
        devices_discount_total = data.get('devices_discount_total', 0)
        total_devices_price = data.get(
            'total_devices_price', discounted_devices_price_per_month * months_in_period
        )
    else:
        devices_discount_percent = db_user.get_promo_discount(
            "devices",
            data['period_days'],
        )
        discounted_devices_price_per_month, discount_per_month = apply_percentage_discount(
            devices_price_per_month,
            devices_discount_percent,
        )
        devices_discount_total = discount_per_month * months_in_period
        total_devices_price = discounted_devices_price_per_month * months_in_period

    if settings.is_traffic_fixed():
        final_traffic_gb = settings.get_fixed_traffic_limit()
        traffic_price_per_month = data.get(
            'traffic_price_per_month', settings.get_traffic_price(final_traffic_gb)
        )
    else:
        final_traffic_gb = data.get('final_traffic_gb', data.get('traffic_gb'))
        traffic_price_per_month = data.get(
            'traffic_price_per_month', settings.get_traffic_price(data['traffic_gb'])
        )

    if 'traffic_discount_percent' in data:
        traffic_discount_percent = data.get('traffic_discount_percent', 0)
        discounted_traffic_price_per_month = data.get(
            'traffic_discounted_price_per_month', traffic_price_per_month
        )
        traffic_discount_total = data.get('traffic_discount_total', 0)
        total_traffic_price = data.get(
            'total_traffic_price', discounted_traffic_price_per_month * months_in_period
        )
    else:
        traffic_discount_percent = db_user.get_promo_discount(
            "traffic",
            data['period_days'],
        )
        discounted_traffic_price_per_month, discount_per_month = apply_percentage_discount(
            traffic_price_per_month,
            traffic_discount_percent,
        )
        traffic_discount_total = discount_per_month * months_in_period
        total_traffic_price = discounted_traffic_price_per_month * months_in_period

    total_servers_price = data.get('total_servers_price', total_countries_price)

    cached_total_price = data['total_price']
    cached_promo_discount_value = data.get('promo_offer_discount_value', 0)

    validation_total_price = data.get('total_price_before_promo_offer')
    if validation_total_price is None and cached_promo_discount_value > 0:
        validation_total_price = cached_total_price + cached_promo_discount_value
    if validation_total_price is None:
        validation_total_price = cached_total_price

    current_promo_offer_percent = _get_promo_offer_discount_percent(db_user)
    if current_promo_offer_percent > 0:
        final_price, promo_offer_discount_value = apply_percentage_discount(
            validation_total_price,
            current_promo_offer_percent,
        )
        promo_offer_discount_percent = current_promo_offer_percent
    else:
        final_price = validation_total_price
        promo_offer_discount_value = 0
        promo_offer_discount_percent = 0

    discounted_monthly_additions = data.get(
        'discounted_monthly_additions',
        discounted_traffic_price_per_month
        + discounted_servers_price_per_month
        + discounted_devices_price_per_month,
    )

    is_valid = validate_pricing_calculation(
        base_price,
        discounted_monthly_additions,
        months_in_period,
        validation_total_price,
    )

    if not is_valid:
        logger.error(f"Ошибка в расчете цены подписки для пользователя {db_user.telegram_id}")
        await callback.answer("Ошибка расчета цены. Обратитесь в поддержку.", show_alert=True)
        return

    logger.info(f"Расчет покупки подписки на {data['period_days']} дней ({months_in_period} мес):")
    base_log = f"   Период: {base_price_original / 100}₽"
    if base_discount_total and base_discount_total > 0:
        base_log += (
            f" → {base_price / 100}₽"
            f" (скидка {base_discount_percent}%: -{base_discount_total / 100}₽)"
        )
    logger.info(base_log)
    if total_traffic_price > 0:
        message = (
            f"   Трафик: {traffic_price_per_month / 100}₽/мес × {months_in_period}"
            f" = {total_traffic_price / 100}₽"
        )
        if traffic_discount_total > 0:
            message += (
                f" (скидка {traffic_discount_percent}%:"
                f" -{traffic_discount_total / 100}₽)"
            )
        logger.info(message)
    if total_servers_price > 0:
        message = (
            f"   Серверы: {countries_price_per_month / 100}₽/мес × {months_in_period}"
            f" = {total_servers_price / 100}₽"
        )
        if total_servers_discount > 0:
            message += (
                f" (скидка {servers_discount_percent}%:"
                f" -{total_servers_discount / 100}₽)"
            )
        logger.info(message)
    if total_devices_price > 0:
        message = (
            f"   Устройства: {devices_price_per_month / 100}₽/мес × {months_in_period}"
            f" = {total_devices_price / 100}₽"
        )
        if devices_discount_total > 0:
            message += (
                f" (скидка {devices_discount_percent}%:"
                f" -{devices_discount_total / 100}₽)"
            )
        logger.info(message)
    if promo_offer_discount_value > 0:
        logger.info(
            "   🎯 Промо-предложение: -%s₽ (%s%%)",
            promo_offer_discount_value / 100,
            promo_offer_discount_percent,
        )
    logger.info(f"   ИТОГО: {final_price / 100}₽")

    if db_user.balance_kopeks < final_price:
        missing_kopeks = final_price - db_user.balance_kopeks
        message_text = texts.t(
            "ADDON_INSUFFICIENT_FUNDS_MESSAGE",
            (
                "⚠️ <b>Недостаточно средств</b>\n\n"
                "Стоимость услуги: {required}\n"
                "На балансе: {balance}\n"
                "Не хватает: {missing}\n\n"
                "Выберите способ пополнения. Сумма подставится автоматически."
            ),
        ).format(
            required=texts.format_price(final_price),
            balance=texts.format_price(db_user.balance_kopeks),
            missing=texts.format_price(missing_kopeks),
        )

        await callback.message.edit_text(
            message_text,
            reply_markup=get_insufficient_balance_keyboard(
                db_user.language,
                resume_callback=resume_callback,
                amount_kopeks=missing_kopeks,
            ),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    purchase_completed = False

    try:
        success = await subtract_user_balance(
            db,
            db_user,
            final_price,
            f"Покупка подписки на {data['period_days']} дней",
            consume_promo_offer=promo_offer_discount_value > 0,
        )

        if not success:
            missing_kopeks = final_price - db_user.balance_kopeks
            message_text = texts.t(
                "ADDON_INSUFFICIENT_FUNDS_MESSAGE",
                (
                    "⚠️ <b>Недостаточно средств</b>\n\n"
                    "Стоимость услуги: {required}\n"
                    "На балансе: {balance}\n"
                    "Не хватает: {missing}\n\n"
                    "Выберите способ пополнения. Сумма подставится автоматически."
                ),
            ).format(
                required=texts.format_price(final_price),
                balance=texts.format_price(db_user.balance_kopeks),
                missing=texts.format_price(missing_kopeks),
            )

            await callback.message.edit_text(
                message_text,
                reply_markup=get_insufficient_balance_keyboard(
                    db_user.language,
                    resume_callback=resume_callback,
                    amount_kopeks=missing_kopeks,
                ),
                parse_mode="HTML",
            )
            await callback.answer()
            return

        existing_subscription = db_user.subscription
        was_trial_conversion = False
        current_time = datetime.utcnow()

        if existing_subscription:
            logger.info(f"Обновляем существующую подписку пользователя {db_user.telegram_id}")

            bonus_period = timedelta()

            if existing_subscription.is_trial:
                logger.info(f"Конверсия из триала в платную для пользователя {db_user.telegram_id}")
                was_trial_conversion = True

                trial_duration = (current_time - existing_subscription.start_date).days

                if settings.TRIAL_ADD_REMAINING_DAYS_TO_PAID and existing_subscription.end_date:
                    remaining_trial_delta = existing_subscription.end_date - current_time
                    if remaining_trial_delta.total_seconds() > 0:
                        bonus_period = remaining_trial_delta
                        logger.info(
                            "Добавляем оставшееся время триала (%s) к новой подписке пользователя %s",
                            bonus_period,
                            db_user.telegram_id,
                        )

                try:
                    from app.database.crud.subscription_conversion import create_subscription_conversion
                    await create_subscription_conversion(
                        db=db,
                        user_id=db_user.id,
                        trial_duration_days=trial_duration,
                        payment_method="balance",
                        first_payment_amount_kopeks=final_price,
                        first_paid_period_days=data['period_days']
                    )
                    logger.info(
                        f"Записана конверсия: {trial_duration} дн. триал → {data['period_days']} дн. платная за {final_price / 100}₽")
                except Exception as conversion_error:
                    logger.error(f"Ошибка записи конверсии: {conversion_error}")

            existing_subscription.is_trial = False
            existing_subscription.status = SubscriptionStatus.ACTIVE.value
            existing_subscription.traffic_limit_gb = final_traffic_gb
            existing_subscription.device_limit = data['devices']
            existing_subscription.connected_squads = data['countries']

            existing_subscription.start_date = current_time
            existing_subscription.end_date = current_time + timedelta(days=data['period_days']) + bonus_period
            existing_subscription.updated_at = current_time

            existing_subscription.traffic_used_gb = 0.0

            await db.commit()
            await db.refresh(existing_subscription)
            subscription = existing_subscription

        else:
            logger.info(f"Создаем новую подписку для пользователя {db_user.telegram_id}")
            subscription = await create_paid_subscription_with_traffic_mode(
                db=db,
                user_id=db_user.id,
                duration_days=data['period_days'],
                device_limit=data['devices'],
                connected_squads=data['countries'],
                traffic_gb=final_traffic_gb
            )

        from app.utils.user_utils import mark_user_as_had_paid_subscription
        await mark_user_as_had_paid_subscription(db, db_user)

        from app.database.crud.server_squad import get_server_ids_by_uuids, add_user_to_servers
        from app.database.crud.subscription import add_subscription_servers

        server_ids = await get_server_ids_by_uuids(db, data['countries'])

        if server_ids:
            await add_subscription_servers(db, subscription, server_ids, server_prices)
            await add_user_to_servers(db, server_ids)

            logger.info(f"Сохранены цены серверов за весь период: {server_prices}")

        await db.refresh(db_user)

        subscription_service = SubscriptionService()

        if db_user.remnawave_uuid:
            remnawave_user = await subscription_service.update_remnawave_user(
                db,
                subscription,
                reset_traffic=settings.RESET_TRAFFIC_ON_PAYMENT,
                reset_reason="покупка подписки",
            )
        else:
            remnawave_user = await subscription_service.create_remnawave_user(
                db,
                subscription,
                reset_traffic=settings.RESET_TRAFFIC_ON_PAYMENT,
                reset_reason="покупка подписки",
            )

        if not remnawave_user:
            logger.error(f"Не удалось создать/обновить RemnaWave пользователя для {db_user.telegram_id}")
            remnawave_user = await subscription_service.create_remnawave_user(
                db,
                subscription,
                reset_traffic=settings.RESET_TRAFFIC_ON_PAYMENT,
                reset_reason="покупка подписки (повторная попытка)",
            )

        transaction = await create_transaction(
            db=db,
            user_id=db_user.id,
            type=TransactionType.SUBSCRIPTION_PAYMENT,
            amount_kopeks=final_price,
            description=f"Подписка на {data['period_days']} дней ({months_in_period} мес)"
        )

        try:
            notification_service = AdminNotificationService(callback.bot)
            await notification_service.send_subscription_purchase_notification(
                db, db_user, subscription, transaction, data['period_days'], was_trial_conversion
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления о покупке: {e}")

        await db.refresh(db_user)
        await db.refresh(subscription)

        subscription_link = get_display_subscription_link(subscription)
        hide_subscription_link = settings.should_hide_subscription_link()

        discount_note = ""
        if promo_offer_discount_value > 0:
            discount_note = texts.t(
                "SUBSCRIPTION_PROMO_DISCOUNT_NOTE",
                "⚡ Доп. скидка {percent}%: -{amount}",
            ).format(
                percent=promo_offer_discount_percent,
                amount=texts.format_price(promo_offer_discount_value),
            )

        if remnawave_user and subscription_link:
            if settings.is_happ_cryptolink_mode():
                success_text = (
                        f"{texts.SUBSCRIPTION_PURCHASED}\n\n"
                        + texts.t(
                    "SUBSCRIPTION_HAPP_LINK_PROMPT",
                    "🔒 Ссылка на подписку создана. Нажмите кнопку \"Подключиться\" ниже, чтобы открыть её в Happ.",
                )
                        + "\n\n"
                        + texts.t(
                    "SUBSCRIPTION_IMPORT_INSTRUCTION_PROMPT",
                    "📱 Нажмите кнопку ниже, чтобы получить инструкцию по настройке VPN на вашем устройстве",
                )
                )
            elif hide_subscription_link:
                success_text = (
                        f"{texts.SUBSCRIPTION_PURCHASED}\n\n"
                        + texts.t(
                    "SUBSCRIPTION_LINK_HIDDEN_NOTICE",
                    "ℹ️ Ссылка подписки доступна по кнопкам ниже или в разделе \"Моя подписка\".",
                )
                        + "\n\n"
                        + texts.t(
                    "SUBSCRIPTION_IMPORT_INSTRUCTION_PROMPT",
                    "📱 Нажмите кнопку ниже, чтобы получить инструкцию по настройке VPN на вашем устройстве",
                )
                )
            else:
                import_link_section = texts.t(
                    "SUBSCRIPTION_IMPORT_LINK_SECTION",
                    "🔗 <b>Ваша ссылка для импорта в VPN приложение:</b>\\n<code>{subscription_url}</code>",
                ).format(subscription_url=subscription_link)

                success_text = (
                    f"{texts.SUBSCRIPTION_PURCHASED}\n\n"
                    f"{import_link_section}\n\n"
                    f"{texts.t('SUBSCRIPTION_IMPORT_INSTRUCTION_PROMPT', '📱 Нажмите кнопку ниже, чтобы получить инструкцию по настройке VPN на вашем устройстве')}"
                )

            if discount_note:
                success_text = f"{success_text}\n\n{discount_note}"

            connect_mode = settings.CONNECT_BUTTON_MODE

            if connect_mode == "miniapp_subscription":
                connect_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=texts.t("CONNECT_BUTTON", "🔗 Подключиться"),
                            web_app=types.WebAppInfo(url=subscription_link),
                        )
                    ],
                    [InlineKeyboardButton(text=texts.t("BACK_TO_MAIN_MENU_BUTTON", "⬅️ В главное меню"),
                                          callback_data="back_to_menu")],
                ])
            elif connect_mode == "miniapp_custom":
                if not settings.MINIAPP_CUSTOM_URL:
                    await callback.answer(
                        texts.t(
                            "CUSTOM_MINIAPP_URL_NOT_SET",
                            "⚠ Кастомная ссылка для мини-приложения не настроена",
                        ),
                        show_alert=True,
                    )
                    return

                connect_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=texts.t("CONNECT_BUTTON", "🔗 Подключиться"),
                            web_app=types.WebAppInfo(url=settings.MINIAPP_CUSTOM_URL),
                        )
                    ],
                    [InlineKeyboardButton(text=texts.t("BACK_TO_MAIN_MENU_BUTTON", "⬅️ В главное меню"),
                                          callback_data="back_to_menu")],
                ])
            elif connect_mode == "link":
                rows = [
                    [InlineKeyboardButton(text=texts.t("CONNECT_BUTTON", "🔗 Подключиться"), url=subscription_link)]
                ]
                happ_row = get_happ_download_button_row(texts)
                if happ_row:
                    rows.append(happ_row)
                rows.append([InlineKeyboardButton(text=texts.t("BACK_TO_MAIN_MENU_BUTTON", "⬅️ В главное меню"),
                                                  callback_data="back_to_menu")])
                connect_keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
            elif connect_mode == "happ_cryptolink":
                rows = [
                    [
                        InlineKeyboardButton(
                            text=texts.t("CONNECT_BUTTON", "🔗 Подключиться"),
                            callback_data="open_subscription_link",
                        )
                    ]
                ]
                happ_row = get_happ_download_button_row(texts)
                if happ_row:
                    rows.append(happ_row)
                rows.append([InlineKeyboardButton(text=texts.t("BACK_TO_MAIN_MENU_BUTTON", "⬅️ В главное меню"),
                                                  callback_data="back_to_menu")])
                connect_keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
            else:
                connect_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=texts.t("CONNECT_BUTTON", "🔗 Подключиться"),
                                          callback_data="subscription_connect")],
                    [InlineKeyboardButton(text=texts.t("BACK_TO_MAIN_MENU_BUTTON", "⬅️ В главное меню"),
                                          callback_data="back_to_menu")],
                ])

            await callback.message.edit_text(
                success_text,
                reply_markup=connect_keyboard,
                parse_mode="HTML"
            )
        else:
            purchase_text = texts.SUBSCRIPTION_PURCHASED
            if discount_note:
                purchase_text = f"{purchase_text}\n\n{discount_note}"
            await callback.message.edit_text(
                texts.t(
                    "SUBSCRIPTION_LINK_GENERATING_NOTICE",
                    "{purchase_text}\n\nСсылка генерируется, перейдите в раздел 'Моя подписка' через несколько секунд.",
                ).format(purchase_text=purchase_text),
                reply_markup=get_back_keyboard(db_user.language)
            )

        purchase_completed = True
        logger.info(
            f"Пользователь {db_user.telegram_id} купил подписку на {data['period_days']} дней за {final_price / 100}₽")

    except Exception as e:
        logger.error(f"Ошибка покупки подписки: {e}")
        await callback.message.edit_text(
            texts.ERROR,
            reply_markup=get_back_keyboard(db_user.language)
        )

    if purchase_completed:
        await clear_subscription_checkout_draft(db_user.id)

    await state.clear()
    await callback.answer()


async def resume_subscription_checkout(
        callback: types.CallbackQuery,
        state: FSMContext,
        db_user: User,
):
    texts = get_texts(db_user.language)

    draft = await get_subscription_checkout_draft(db_user.id)

    if not draft:
        await callback.answer(texts.NO_SAVED_SUBSCRIPTION_ORDER, show_alert=True)
        return

    try:
        summary_text, prepared_data = await _prepare_subscription_summary(db_user, draft, texts)
    except ValueError as exc:
        logger.error(
            f"Ошибка восстановления заказа подписки для пользователя {db_user.telegram_id}: {exc}"
        )
        await clear_subscription_checkout_draft(db_user.id)
        await callback.answer(texts.NO_SAVED_SUBSCRIPTION_ORDER, show_alert=True)
        return

    await state.set_data(prepared_data)
    await state.set_state(SubscriptionStates.confirming_purchase)
    await save_subscription_checkout_draft(db_user.id, prepared_data)

    await callback.message.edit_text(
        summary_text,
        reply_markup=get_subscription_confirm_keyboard(db_user.language),
        parse_mode="HTML",
    )

    await callback.answer()


async def add_traffic(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    if settings.is_traffic_fixed():
        await callback.answer("⚠️ В текущем режиме трафик фиксированный", show_alert=True)
        return

    traffic_gb = int(callback.data.split('_')[2])
    texts = get_texts(db_user.language)
    subscription = db_user.subscription

    base_price = settings.get_traffic_price(traffic_gb)

    if base_price == 0 and traffic_gb != 0:
        await callback.answer("⚠️ Цена для этого пакета не настроена", show_alert=True)
        return

    period_hint_days = _get_period_hint_from_subscription(subscription)
    discount_result = _apply_addon_discount(
        db_user,
        "traffic",
        base_price,
        period_hint_days,
    )

    discounted_per_month = discount_result["discounted"]
    discount_per_month = discount_result["discount"]
    charged_months = 1

    if subscription:
        price, charged_months = calculate_prorated_price(
            discounted_per_month,
            subscription.end_date,
        )
    else:
        price = discounted_per_month

    total_discount_value = discount_per_month * charged_months

    if db_user.balance_kopeks < price:
        missing_kopeks = price - db_user.balance_kopeks
        message_text = texts.t(
            "ADDON_INSUFFICIENT_FUNDS_MESSAGE",
            (
                "⚠️ <b>Недостаточно средств</b>\n\n"
                "Стоимость услуги: {required}\n"
                "На балансе: {balance}\n"
                "Не хватает: {missing}\n\n"
                "Выберите способ пополнения. Сумма подставится автоматически."
            ),
        ).format(
            required=texts.format_price(price),
            balance=texts.format_price(db_user.balance_kopeks),
            missing=texts.format_price(missing_kopeks),
        )

        await callback.message.edit_text(
            message_text,
            reply_markup=get_insufficient_balance_keyboard(
                db_user.language,
                amount_kopeks=missing_kopeks,
            ),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    try:
        success = await subtract_user_balance(
            db,
            db_user,
            price,
            f"Добавление {traffic_gb} ГБ трафика",
        )

        if not success:
            await callback.answer("⚠️ Ошибка списания средств", show_alert=True)
            return

        if traffic_gb == 0:
            subscription.traffic_limit_gb = 0
        else:
            await add_subscription_traffic(db, subscription, traffic_gb)

        subscription_service = SubscriptionService()
        await subscription_service.update_remnawave_user(db, subscription)

        await create_transaction(
            db=db,
            user_id=db_user.id,
            type=TransactionType.SUBSCRIPTION_PAYMENT,
            amount_kopeks=price,
            description=f"Добавление {traffic_gb} ГБ трафика",
        )

        await db.refresh(db_user)
        await db.refresh(subscription)

        success_text = f"✅ Трафик успешно добавлен!\n\n"
        if traffic_gb == 0:
            success_text += "🎉 Теперь у вас безлимитный трафик!"
        else:
            success_text += f"📈 Добавлено: {traffic_gb} ГБ\n"
            success_text += f"Новый лимит: {texts.format_traffic(subscription.traffic_limit_gb)}"

        if price > 0:
            success_text += f"\n💰 Списано: {texts.format_price(price)}"
            if total_discount_value > 0:
                success_text += (
                    f" (скидка {discount_result['percent']}%:"
                    f" -{texts.format_price(total_discount_value)})"
                )

        await callback.message.edit_text(
            success_text,
            reply_markup=get_back_keyboard(db_user.language)
        )

        logger.info(f"✅ Пользователь {db_user.telegram_id} добавил {traffic_gb} ГБ трафика")

    except Exception as e:
        logger.error(f"Ошибка добавления трафика: {e}")
        await callback.message.edit_text(
            texts.ERROR,
            reply_markup=get_back_keyboard(db_user.language)
        )

    await callback.answer()


async def create_paid_subscription_with_traffic_mode(
        db: AsyncSession,
        user_id: int,
        duration_days: int,
        device_limit: int,
        connected_squads: List[str],
        traffic_gb: Optional[int] = None
):
    from app.config import settings

    if traffic_gb is None:
        if settings.is_traffic_fixed():
            traffic_limit_gb = settings.get_fixed_traffic_limit()
        else:
            traffic_limit_gb = 0
    else:
        traffic_limit_gb = traffic_gb

    subscription = await create_paid_subscription(
        db=db,
        user_id=user_id,
        duration_days=duration_days,
        traffic_limit_gb=traffic_limit_gb,
        device_limit=device_limit,
        connected_squads=connected_squads
    )

    logger.info(f"📋 Создана подписка с трафиком: {traffic_limit_gb} ГБ (режим: {settings.TRAFFIC_SELECTION_MODE})")

    return subscription


def validate_traffic_price(gb: int) -> bool:
    from app.config import settings

    price = settings.get_traffic_price(gb)
    if gb == 0:
        return True

    return price > 0


async def handle_subscription_settings(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    texts = get_texts(db_user.language)
    subscription = db_user.subscription

    if not subscription or subscription.is_trial:
        await callback.answer(
            texts.t(
                "SUBSCRIPTION_SETTINGS_PAID_ONLY",
                "⚠️ Настройки доступны только для платных подписок",
            ),
            show_alert=True,
        )
        return

    devices_used = await get_current_devices_count(db_user)

    settings_text = texts.t(
        "SUBSCRIPTION_SETTINGS_OVERVIEW",
        (
            "⚙️ <b>Настройки подписки</b>\n\n"
            "📊 <b>Текущие параметры:</b>\n"
            "🌐 Стран: {countries_count}\n"
            "📈 Трафик: {traffic_used} / {traffic_limit}\n"
            "📱 Устройства: {devices_used} / {devices_limit}\n\n"
            "Выберите что хотите изменить:"
        ),
    ).format(
        countries_count=len(subscription.connected_squads),
        traffic_used=texts.format_traffic(subscription.traffic_used_gb),
        traffic_limit=texts.format_traffic(subscription.traffic_limit_gb),
        devices_used=devices_used,
        devices_limit=subscription.device_limit,
    )

    show_countries = await _should_show_countries_management(db_user)

    await callback.message.edit_text(
        settings_text,
        reply_markup=get_updated_subscription_settings_keyboard(db_user.language, show_countries),
        parse_mode="HTML"
    )
    await callback.answer()


async def handle_autopay_menu(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    texts = get_texts(db_user.language)
    subscription = db_user.subscription
    if not subscription:
        await callback.answer(
            texts.t("SUBSCRIPTION_ACTIVE_REQUIRED", "⚠️ У вас нет активной подписки!"),
            show_alert=True,
        )
        return

    status = (
        texts.t("AUTOPAY_STATUS_ENABLED", "включен")
        if subscription.autopay_enabled
        else texts.t("AUTOPAY_STATUS_DISABLED", "выключен")
    )
    days = subscription.autopay_days_before

    text = texts.t(
        "AUTOPAY_MENU_TEXT",
        (
            "💳 <b>Автоплатеж</b>\n\n"
            "📊 <b>Статус:</b> {status}\n"
            "⏰ <b>Списание за:</b> {days} дн. до окончания\n\n"
            "Выберите действие:"
        ),
    ).format(status=status, days=days)

    await callback.message.edit_text(
        text,
        reply_markup=get_autopay_keyboard(db_user.language),
        parse_mode="HTML",
    )
    await callback.answer()


async def toggle_autopay(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    subscription = db_user.subscription
    enable = callback.data == "autopay_enable"

    await update_subscription_autopay(db, subscription, enable)

    texts = get_texts(db_user.language)
    status = (
        texts.t("AUTOPAY_STATUS_ENABLED", "включен")
        if enable
        else texts.t("AUTOPAY_STATUS_DISABLED", "выключен")
    )
    await callback.answer(
        texts.t("AUTOPAY_TOGGLE_SUCCESS", "✅ Автоплатеж {status}!").format(status=status)
    )

    await handle_autopay_menu(callback, db_user, db)


async def show_autopay_days(
        callback: types.CallbackQuery,
        db_user: User
):
    texts = get_texts(db_user.language)
    await callback.message.edit_text(
        texts.t(
            "AUTOPAY_SELECT_DAYS_PROMPT",
            "⏰ Выберите за сколько дней до окончания списывать средства:",
        ),
        reply_markup=get_autopay_days_keyboard(db_user.language)
    )
    await callback.answer()


async def set_autopay_days(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    days = int(callback.data.split('_')[2])
    subscription = db_user.subscription

    await update_subscription_autopay(
        db, subscription, subscription.autopay_enabled, days
    )

    texts = get_texts(db_user.language)
    await callback.answer(
        texts.t("AUTOPAY_DAYS_SET", "✅ Установлено {days} дней!").format(days=days)
    )

    await handle_autopay_menu(callback, db_user, db)


async def handle_subscription_config_back(
        callback: types.CallbackQuery,
        state: FSMContext,
        db_user: User,
        db: AsyncSession
):
    current_state = await state.get_state()
    texts = get_texts(db_user.language)

    if current_state == SubscriptionStates.selecting_traffic.state:
        await callback.message.edit_text(
            await _build_subscription_period_prompt(db_user, texts, db),
            reply_markup=get_subscription_period_keyboard(db_user.language),
            parse_mode="HTML",
        )
        await state.set_state(SubscriptionStates.selecting_period)

    elif current_state == SubscriptionStates.selecting_countries.state:
        if settings.is_traffic_selectable():
            await callback.message.edit_text(
                texts.SELECT_TRAFFIC,
                reply_markup=get_traffic_packages_keyboard(db_user.language)
            )
            await state.set_state(SubscriptionStates.selecting_traffic)
        else:
            await callback.message.edit_text(
                await _build_subscription_period_prompt(db_user, texts, db),
                reply_markup=get_subscription_period_keyboard(db_user.language),
                parse_mode="HTML",
            )
            await state.set_state(SubscriptionStates.selecting_period)

    elif current_state == SubscriptionStates.selecting_devices.state:
        if await _should_show_countries_management(db_user):
            countries = await _get_available_countries(db_user.promo_group_id)
            data = await state.get_data()
            selected_countries = data.get('countries', [])

            await callback.message.edit_text(
                texts.SELECT_COUNTRIES,
                reply_markup=get_countries_keyboard(countries, selected_countries, db_user.language)
            )
            await state.set_state(SubscriptionStates.selecting_countries)
        elif settings.is_traffic_selectable():
            await callback.message.edit_text(
                texts.SELECT_TRAFFIC,
                reply_markup=get_traffic_packages_keyboard(db_user.language)
            )
            await state.set_state(SubscriptionStates.selecting_traffic)
        else:
            await callback.message.edit_text(
                await _build_subscription_period_prompt(db_user, texts, db),
                reply_markup=get_subscription_period_keyboard(db_user.language),
                parse_mode="HTML",
            )
            await state.set_state(SubscriptionStates.selecting_period)

    else:
        from app.handlers.menu import show_main_menu
        await show_main_menu(callback, db_user, db)
        await state.clear()

    await callback.answer()


async def handle_subscription_cancel(
        callback: types.CallbackQuery,
        state: FSMContext,
        db_user: User,
        db: AsyncSession
):
    texts = get_texts(db_user.language)

    await state.clear()
    await clear_subscription_checkout_draft(db_user.id)

    from app.handlers.menu import show_main_menu
    await show_main_menu(callback, db_user, db)

    await callback.answer("❌ Покупка отменена")


async def _get_available_countries(promo_group_id: Optional[int] = None):
    from app.utils.cache import cache, cache_key
    from app.database.database import AsyncSessionLocal
    from app.database.crud.server_squad import get_available_server_squads

    cache_key_value = cache_key("available_countries", promo_group_id or "all")
    cached_countries = await cache.get(cache_key_value)
    if cached_countries:
        return cached_countries

    try:
        async with AsyncSessionLocal() as db:
            available_servers = await get_available_server_squads(
                db, promo_group_id=promo_group_id
            )

        if promo_group_id is not None and not available_servers:
            logger.info(
                "Промогруппа %s не имеет доступных серверов, возврат пустого списка",
                promo_group_id,
            )
            await cache.set(cache_key_value, [], 60)
            return []

        countries = []
        for server in available_servers:
            countries.append({
                "uuid": server.squad_uuid,
                "name": server.display_name,
                "price_kopeks": server.price_kopeks,
                "country_code": server.country_code,
                "is_available": server.is_available and not server.is_full
            })

        if not countries:
            logger.info("🔄 Серверов в БД нет, получаем из RemnaWave...")
            from app.services.remnawave_service import RemnaWaveService

            service = RemnaWaveService()
            squads = await service.get_all_squads()

            for squad in squads:
                squad_name = squad["name"]

                if not any(flag in squad_name for flag in
                           ["🇳🇱", "🇩🇪", "🇺🇸", "🇫🇷", "🇬🇧", "🇮🇹", "🇪🇸", "🇨🇦", "🇯🇵", "🇸🇬", "🇦🇺"]):
                    name_lower = squad_name.lower()
                    if "netherlands" in name_lower or "нидерланды" in name_lower or "nl" in name_lower:
                        squad_name = f"🇳🇱 {squad_name}"
                    elif "germany" in name_lower or "германия" in name_lower or "de" in name_lower:
                        squad_name = f"🇩🇪 {squad_name}"
                    elif "usa" in name_lower or "сша" in name_lower or "america" in name_lower or "us" in name_lower:
                        squad_name = f"🇺🇸 {squad_name}"
                    else:
                        squad_name = f"🌐 {squad_name}"

                countries.append({
                    "uuid": squad["uuid"],
                    "name": squad_name,
                    "price_kopeks": 0,
                    "is_available": True
                })

        await cache.set(cache_key_value, countries, 300)
        return countries

    except Exception as e:
        logger.error(f"Ошибка получения списка стран: {e}")
        fallback_countries = [
            {"uuid": "default-free", "name": "🆓 Бесплатный сервер", "price_kopeks": 0, "is_available": True},
        ]

        await cache.set(cache_key_value, fallback_countries, 60)
        return fallback_countries


async def _get_countries_info(squad_uuids):
    countries = await _get_available_countries()
    return [c for c in countries if c['uuid'] in squad_uuids]


async def handle_reset_devices(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    await handle_device_management(callback, db_user, db)


async def handle_add_country_to_subscription(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession,
        state: FSMContext
):
    logger.info(f"🔍 handle_add_country_to_subscription вызван для {db_user.telegram_id}")
    logger.info(f"🔍 Callback data: {callback.data}")

    current_state = await state.get_state()
    logger.info(f"🔍 Текущее состояние: {current_state}")

    country_uuid = callback.data.split('_')[1]
    data = await state.get_data()
    logger.info(f"🔍 Данные состояния: {data}")

    selected_countries = data.get('countries', [])
    countries = await _get_available_countries(db_user.promo_group_id)
    allowed_country_ids = {country['uuid'] for country in countries}

    if country_uuid not in allowed_country_ids and country_uuid not in selected_countries:
        await callback.answer("❌ Сервер недоступен для вашей промогруппы", show_alert=True)
        return

    if country_uuid in selected_countries:
        selected_countries.remove(country_uuid)
        logger.info(f"🔍 Удалена страна: {country_uuid}")
    else:
        selected_countries.append(country_uuid)
        logger.info(f"🔍 Добавлена страна: {country_uuid}")

    total_price = 0
    subscription = db_user.subscription
    period_hint_days = _get_period_hint_from_subscription(subscription)
    servers_discount_percent = _get_addon_discount_percent_for_user(
        db_user,
        "servers",
        period_hint_days,
    )

    for country in countries:
        if not country.get('is_available', True):
            continue

        if (
                country['uuid'] in selected_countries
                and country['uuid'] not in subscription.connected_squads
        ):
            server_price = country['price_kopeks']
            if servers_discount_percent > 0 and server_price > 0:
                discounted_price, _ = apply_percentage_discount(
                    server_price,
                    servers_discount_percent,
                )
            else:
                discounted_price = server_price
            total_price += discounted_price

    data['countries'] = selected_countries
    data['total_price'] = total_price
    await state.set_data(data)

    logger.info(f"🔍 Новые выбранные страны: {selected_countries}")
    logger.info(f"🔍 Общая стоимость: {total_price}")

    try:
        from app.keyboards.inline import get_manage_countries_keyboard
        await callback.message.edit_reply_markup(
            reply_markup=get_manage_countries_keyboard(
                countries,
                selected_countries,
                subscription.connected_squads,
                db_user.language,
                subscription.end_date,
                servers_discount_percent,
            )
        )
        logger.info(f"✅ Клавиатура обновлена")
    except Exception as e:
        logger.error(f"❌ Ошибка обновления клавиатуры: {e}")

    await callback.answer()


async def _should_show_countries_management(user: Optional[User] = None) -> bool:
    try:
        promo_group_id = user.promo_group_id if user else None

        promo_group = getattr(user, "promo_group", None) if user else None
        if promo_group and getattr(promo_group, "server_squads", None):
            allowed_servers = [
                server
                for server in promo_group.server_squads
                if server.is_available and not server.is_full
            ]

            if allowed_servers:
                if len(allowed_servers) > 1:
                    logger.debug(
                        "Промогруппа %s имеет %s доступных серверов, показываем управление странами",
                        promo_group.id,
                        len(allowed_servers),
                    )
                    return True

                logger.debug(
                    "Промогруппа %s имеет всего %s доступный сервер, пропускаем шаг выбора стран",
                    promo_group.id,
                    len(allowed_servers),
                )
                return False

        countries = await _get_available_countries(promo_group_id)
        available_countries = [c for c in countries if c.get('is_available', True)]
        return len(available_countries) > 1
    except Exception as e:
        logger.error(f"Ошибка проверки доступных серверов: {e}")
        return True


async def confirm_add_countries_to_subscription(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession,
        state: FSMContext
):
    data = await state.get_data()
    texts = get_texts(db_user.language)
    subscription = db_user.subscription

    selected_countries = data.get('countries', [])
    current_countries = subscription.connected_squads

    countries = await _get_available_countries(db_user.promo_group_id)
    allowed_country_ids = {country['uuid'] for country in countries}

    selected_countries = [
        country_uuid
        for country_uuid in selected_countries
        if country_uuid in allowed_country_ids or country_uuid in current_countries
    ]

    new_countries = [c for c in selected_countries if c not in current_countries]
    removed_countries = [c for c in current_countries if c not in selected_countries]

    if not new_countries and not removed_countries:
        await callback.answer("⚠️ Изменения не обнаружены", show_alert=True)
        return

    total_price = 0
    new_countries_names = []
    removed_countries_names = []

    period_hint_days = _get_period_hint_from_subscription(subscription)
    servers_discount_percent = _get_addon_discount_percent_for_user(
        db_user,
        "servers",
        period_hint_days,
    )
    total_discount_value = 0

    for country in countries:
        if not country.get('is_available', True):
            continue

        if country['uuid'] in new_countries:
            server_price = country['price_kopeks']
            if servers_discount_percent > 0 and server_price > 0:
                discounted_per_month, discount_per_month = apply_percentage_discount(
                    server_price,
                    servers_discount_percent,
                )
            else:
                discounted_per_month = server_price
                discount_per_month = 0

            charged_price, charged_months = calculate_prorated_price(
                discounted_per_month,
                subscription.end_date,
            )

            total_price += charged_price
            total_discount_value += discount_per_month * charged_months
            new_countries_names.append(country['name'])
        if country['uuid'] in removed_countries:
            removed_countries_names.append(country['name'])

    if new_countries and db_user.balance_kopeks < total_price:
        missing_kopeks = total_price - db_user.balance_kopeks
        message_text = texts.t(
            "ADDON_INSUFFICIENT_FUNDS_MESSAGE",
            (
                "⚠️ <b>Недостаточно средств</b>\n\n"
                "Стоимость услуги: {required}\n"
                "На балансе: {balance}\n"
                "Не хватает: {missing}\n\n"
                "Выберите способ пополнения. Сумма подставится автоматически."
            ),
        ).format(
            required=texts.format_price(total_price),
            balance=texts.format_price(db_user.balance_kopeks),
            missing=texts.format_price(missing_kopeks),
        )

        await callback.message.edit_text(
            message_text,
            reply_markup=get_insufficient_balance_keyboard(
                db_user.language,
                amount_kopeks=missing_kopeks,
            ),
            parse_mode="HTML",
        )
        await state.clear()
        await callback.answer()
        return

    try:
        if new_countries and total_price > 0:
            success = await subtract_user_balance(
                db, db_user, total_price,
                f"Добавление стран к подписке: {', '.join(new_countries_names)}"
            )

            if not success:
                await callback.answer("❌ Ошибка списания средств", show_alert=True)
                return

            await create_transaction(
                db=db,
                user_id=db_user.id,
                type=TransactionType.SUBSCRIPTION_PAYMENT,
                amount_kopeks=total_price,
                description=f"Добавление стран к подписке: {', '.join(new_countries_names)}"
            )

        subscription.connected_squads = selected_countries
        subscription.updated_at = datetime.utcnow()
        await db.commit()

        subscription_service = SubscriptionService()
        await subscription_service.update_remnawave_user(db, subscription)

        await db.refresh(db_user)
        await db.refresh(subscription)

        success_text = "✅ Страны успешно обновлены!\n\n"

        if new_countries_names:
            success_text += f"➕ Добавлены страны:\n{chr(10).join(f'• {name}' for name in new_countries_names)}\n"
            if total_price > 0:
                success_text += f"💰 Списано: {texts.format_price(total_price)}"
                if total_discount_value > 0:
                    success_text += (
                        f" (скидка {servers_discount_percent}%:"
                        f" -{texts.format_price(total_discount_value)})"
                    )
                success_text += "\n"

        if removed_countries_names:
            success_text += f"\n➖ Отключены страны:\n{chr(10).join(f'• {name}' for name in removed_countries_names)}\n"
            success_text += "ℹ️ Повторное подключение будет платным\n"

        success_text += f"\n🌍 Активных стран: {len(selected_countries)}"

        await callback.message.edit_text(
            success_text,
            reply_markup=get_back_keyboard(db_user.language)
        )

        logger.info(
            f"✅ Пользователь {db_user.telegram_id} обновил страны подписки. Добавлено: {len(new_countries)}, убрано: {len(removed_countries)}")

    except Exception as e:
        logger.error(f"Ошибка обновления стран подписки: {e}")
        await callback.message.edit_text(
            texts.ERROR,
            reply_markup=get_back_keyboard(db_user.language)
        )

    await state.clear()
    await callback.answer()


async def confirm_reset_devices(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    await handle_device_management(callback, db_user, db)


async def handle_happ_download_request(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    texts = get_texts(db_user.language)
    prompt_text = texts.t(
        "HAPP_DOWNLOAD_PROMPT",
        "📥 <b>Скачать Happ</b>\nВыберите ваше устройство:",
    )

    keyboard = get_happ_download_platform_keyboard(db_user.language)

    await callback.message.answer(prompt_text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


async def handle_happ_download_platform_choice(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    platform = callback.data.split('_')[-1]
    if platform == "pc":
        platform = "windows"
    texts = get_texts(db_user.language)
    link = settings.get_happ_download_link(platform)

    if not link:
        await callback.answer(
            texts.t("HAPP_DOWNLOAD_LINK_NOT_SET", "❌ Ссылка для этого устройства не настроена"),
            show_alert=True,
        )
        return

    platform_names = {
        "ios": texts.t("HAPP_PLATFORM_IOS", "🍎 iOS"),
        "android": texts.t("HAPP_PLATFORM_ANDROID", "🤖 Android"),
        "macos": texts.t("HAPP_PLATFORM_MACOS", "🖥️ Mac OS"),
        "windows": texts.t("HAPP_PLATFORM_WINDOWS", "💻 Windows"),
    }

    link_text = texts.t(
        "HAPP_DOWNLOAD_LINK_MESSAGE",
        "⬇️ Скачайте Happ для {platform}:",
    ).format(platform=platform_names.get(platform, platform.upper()))

    keyboard = get_happ_download_link_keyboard(db_user.language, link)

    await callback.message.edit_text(link_text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


async def handle_happ_download_close(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    try:
        await callback.message.delete()
    except Exception:
        pass

    await callback.answer()


async def handle_happ_download_back(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    texts = get_texts(db_user.language)
    prompt_text = texts.t(
        "HAPP_DOWNLOAD_PROMPT",
        "📥 <b>Скачать Happ</b>\nВыберите ваше устройство:",
    )

    keyboard = get_happ_download_platform_keyboard(db_user.language)

    await callback.message.edit_text(prompt_text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


async def handle_connect_subscription(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    texts = get_texts(db_user.language)
    subscription = db_user.subscription
    subscription_link = get_display_subscription_link(subscription)
    hide_subscription_link = settings.should_hide_subscription_link()

    if not subscription_link:
        await callback.answer(
            texts.t(
                "SUBSCRIPTION_NO_ACTIVE_LINK",
                "⚠ У вас нет активной подписки или ссылка еще генерируется",
            ),
            show_alert=True,
        )
        return

    connect_mode = settings.CONNECT_BUTTON_MODE

    if connect_mode == "miniapp_subscription":
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.t("CONNECT_BUTTON", "🔗 Подключиться"),
                    web_app=types.WebAppInfo(url=subscription_link)
                )
            ],
            [
                InlineKeyboardButton(text=texts.BACK, callback_data="menu_subscription")
            ]
        ])

        await callback.message.edit_text(
            texts.t(
                "SUBSCRIPTION_CONNECT_MINIAPP_MESSAGE",
                """📱 <b>Подключить подписку</b>

🚀 Нажмите кнопку ниже, чтобы открыть подписку в мини-приложении Telegram:""",
            ),
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    elif connect_mode == "miniapp_custom":
        if not settings.MINIAPP_CUSTOM_URL:
            await callback.answer(
                texts.t(
                    "CUSTOM_MINIAPP_URL_NOT_SET",
                    "⚠ Кастомная ссылка для мини-приложения не настроена",
                ),
                show_alert=True,
            )
            return

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.t("CONNECT_BUTTON", "🔗 Подключиться"),
                    web_app=types.WebAppInfo(url=settings.MINIAPP_CUSTOM_URL)
                )
            ],
            [
                InlineKeyboardButton(text=texts.BACK, callback_data="menu_subscription")
            ]
        ])

        await callback.message.edit_text(
            texts.t(
                "SUBSCRIPTION_CONNECT_CUSTOM_MESSAGE",
                """🚀 <b>Подключить подписку</b>

📱 Нажмите кнопку ниже, чтобы открыть приложение:""",
            ),
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    elif connect_mode == "link":
        rows = [
            [
                InlineKeyboardButton(
                    text=texts.t("CONNECT_BUTTON", "🔗 Подключиться"),
                    url=subscription_link
                )
            ]
        ]
        happ_row = get_happ_download_button_row(texts)
        if happ_row:
            rows.append(happ_row)
        rows.append([
            InlineKeyboardButton(text=texts.BACK, callback_data="menu_subscription")
        ])

        keyboard = InlineKeyboardMarkup(inline_keyboard=rows)

        await callback.message.edit_text(
            texts.t(
                "SUBSCRIPTION_CONNECT_LINK_MESSAGE",
                """🚀 <b>Подключить подписку</b>",

🔗 Нажмите кнопку ниже, чтобы открыть ссылку подписки:""",
            ),
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    elif connect_mode == "happ_cryptolink":
        rows = [
            [
                InlineKeyboardButton(
                    text=texts.t("CONNECT_BUTTON", "🔗 Подключиться"),
                    callback_data="open_subscription_link",
                )
            ]
        ]
        happ_row = get_happ_download_button_row(texts)
        if happ_row:
            rows.append(happ_row)
        rows.append([
            InlineKeyboardButton(text=texts.BACK, callback_data="menu_subscription")
        ])

        keyboard = InlineKeyboardMarkup(inline_keyboard=rows)

        await callback.message.edit_text(
            texts.t(
                "SUBSCRIPTION_CONNECT_LINK_MESSAGE",
                """🚀 <b>Подключить подписку</b>",

🔗 Нажмите кнопку ниже, чтобы открыть ссылку подписки:""",
            ),
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    else:
        if hide_subscription_link:
            device_text = texts.t(
                "SUBSCRIPTION_CONNECT_DEVICE_MESSAGE_HIDDEN",
                """📱 <b>Подключить подписку</b>

ℹ️ Ссылка подписки доступна по кнопкам ниже или в разделе "Моя подписка".

💡 <b>Выберите ваше устройство</b> для получения подробной инструкции по настройке:""",
            )
        else:
            device_text = texts.t(
                "SUBSCRIPTION_CONNECT_DEVICE_MESSAGE",
                """📱 <b>Подключить подписку</b>

🔗 <b>Ссылка подписки:</b>
<code>{subscription_url}</code>

💡 <b>Выберите ваше устройство</b> для получения подробной инструкции по настройке:""",
            ).format(subscription_url=subscription_link)

        await callback.message.edit_text(
            device_text,
            reply_markup=get_device_selection_keyboard(db_user.language),
            parse_mode="HTML"
        )

    await callback.answer()


async def claim_discount_offer(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession,
):
    texts = get_texts(db_user.language)

    try:
        offer_id = int(callback.data.split("_")[-1])
    except (ValueError, AttributeError):
        await callback.answer(
            texts.get("DISCOUNT_CLAIM_NOT_FOUND", "❌ Предложение не найдено"),
            show_alert=True,
        )
        return

    offer = await get_offer_by_id(db, offer_id)
    if not offer or offer.user_id != db_user.id:
        await callback.answer(
            texts.get("DISCOUNT_CLAIM_NOT_FOUND", "❌ Предложение не найдено"),
            show_alert=True,
        )
        return

    now = datetime.utcnow()
    if offer.claimed_at is not None:
        await callback.answer(
            texts.get("DISCOUNT_CLAIM_ALREADY", "ℹ️ Скидка уже была активирована"),
            show_alert=True,
        )
        return

    if not offer.is_active or offer.expires_at <= now:
        offer.is_active = False
        await db.commit()
        await callback.answer(
            texts.get("DISCOUNT_CLAIM_EXPIRED", "⚠️ Время действия предложения истекло"),
            show_alert=True,
        )
        return

    effect_type = (offer.effect_type or "percent_discount").lower()
    if effect_type == "balance_bonus":
        effect_type = "percent_discount"

    if effect_type == "test_access":
        success, newly_added, expires_at, error_code = await promo_offer_service.grant_test_access(
            db,
            db_user,
            offer,
        )

        if not success:
            if error_code == "subscription_missing":
                error_message = texts.get(
                    "TEST_ACCESS_NO_SUBSCRIPTION",
                    "❌ Для активации предложения необходима действующая подписка.",
                )
            elif error_code == "squads_missing":
                error_message = texts.get(
                    "TEST_ACCESS_NO_SQUADS",
                    "❌ Не удалось определить список серверов для теста. Обратитесь к администратору.",
                )
            elif error_code == "already_connected":
                error_message = texts.get(
                    "TEST_ACCESS_ALREADY_CONNECTED",
                    "ℹ️ Этот сервер уже подключен к вашей подписке.",
                )
            elif error_code == "remnawave_sync_failed":
                error_message = texts.get(
                    "TEST_ACCESS_REMNAWAVE_ERROR",
                    "❌ Не удалось подключить серверы. Попробуйте позже или обратитесь в поддержку.",
                )
            else:
                error_message = texts.get(
                    "TEST_ACCESS_UNKNOWN_ERROR",
                    "❌ Не удалось активировать предложение. Попробуйте позже.",
                )
            await callback.answer(error_message, show_alert=True)
            return

        await mark_offer_claimed(
            db,
            offer,
            details={
                "context": "test_access_claim",
                "new_squads": newly_added,
                "expires_at": expires_at.isoformat() if expires_at else None,
            },
        )

        expires_text = expires_at.strftime("%d.%m.%Y %H:%M") if expires_at else ""
        success_message = texts.get(
            "TEST_ACCESS_ACTIVATED_MESSAGE",
            "🎉 Тестовые сервера подключены! Доступ активен до {expires_at}.",
        ).format(expires_at=expires_text)

        popup_text = texts.get("TEST_ACCESS_ACTIVATED_POPUP", "✅ Доступ выдан!")
        await callback.answer(popup_text, show_alert=True)
        back_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=texts.get("BACK_TO_MENU", "🏠 В главное меню"),
                        callback_data="back_to_menu",
                    )
                ]
            ]
        )
        await callback.message.answer(success_message, reply_markup=back_keyboard)
        return

    discount_percent = int(offer.discount_percent or 0)
    if discount_percent <= 0:
        await callback.answer(
            texts.get("DISCOUNT_CLAIM_ERROR", "❌ Не удалось активировать скидку. Попробуйте позже."),
            show_alert=True,
        )
        return

    db_user.promo_offer_discount_percent = discount_percent
    db_user.promo_offer_discount_source = offer.notification_type
    db_user.updated_at = now

    extra_data = offer.extra_data or {}
    raw_duration = extra_data.get("active_discount_hours")
    template_id = extra_data.get("template_id")

    if raw_duration in (None, "") and template_id:
        try:
            template = await get_promo_offer_template_by_id(db, int(template_id))
        except (ValueError, TypeError):
            template = None
        if template and template.active_discount_hours:
            raw_duration = template.active_discount_hours

    try:
        duration_hours = int(raw_duration) if raw_duration is not None else None
    except (TypeError, ValueError):
        duration_hours = None

    if duration_hours and duration_hours > 0:
        discount_expires_at = now + timedelta(hours=duration_hours)
    else:
        discount_expires_at = None

    db_user.promo_offer_discount_expires_at = discount_expires_at

    await mark_offer_claimed(
        db,
        offer,
        details={
            "context": "discount_claim",
            "discount_percent": discount_percent,
            "discount_expires_at": discount_expires_at.isoformat() if discount_expires_at else None,
        },
    )
    await db.refresh(db_user)

    success_template = texts.get(
        "DISCOUNT_CLAIM_SUCCESS",
        "🎉 Скидка {percent}% активирована! Она автоматически применится при следующей оплате.",
    )

    expires_text = (
        discount_expires_at.strftime("%d.%m.%Y %H:%M") if discount_expires_at else ""
    )

    format_values: Dict[str, Any] = {"percent": discount_percent}

    if duration_hours and duration_hours > 0:
        format_values.setdefault("hours", duration_hours)
        format_values.setdefault("duration_hours", duration_hours)

    if discount_expires_at:
        format_values.setdefault("expires_at", expires_text)
        format_values.setdefault("expires_at_iso", discount_expires_at.isoformat())
        try:
            expires_timestamp = int(discount_expires_at.timestamp())
        except (OverflowError, OSError, ValueError):
            expires_timestamp = None
        if expires_timestamp:
            format_values.setdefault("expires_at_ts", expires_timestamp)
        remaining_hours = int((discount_expires_at - now).total_seconds() // 3600)
        if remaining_hours > 0:
            format_values.setdefault("expires_in_hours", remaining_hours)

    amount_text = ""
    if isinstance(extra_data, dict):
        raw_amount_text = (
            extra_data.get("amount_text")
            or extra_data.get("discount_amount_text")
            or extra_data.get("formatted_amount")
        )
        if isinstance(raw_amount_text, str) and raw_amount_text.strip():
            amount_text = raw_amount_text.strip()
        else:
            raw_amount = extra_data.get("amount") or extra_data.get("discount_amount")
            if isinstance(raw_amount, (int, float)):
                amount_text = settings.format_price(int(raw_amount))
            elif isinstance(raw_amount, str) and raw_amount.strip():
                amount_text = raw_amount.strip()

        if not amount_text:
            for key in ("discount_amount_kopeks", "amount_kopeks", "bonus_amount_kopeks"):
                maybe_amount = extra_data.get(key)
                try:
                    amount_value = int(maybe_amount)
                except (TypeError, ValueError):
                    continue
                if amount_value > 0:
                    amount_text = settings.format_price(amount_value)
                    break

        for key, value in extra_data.items():
            if (
                isinstance(key, str)
                and key.isidentifier()
                and key not in format_values
                and isinstance(value, (str, int, float))
            ):
                format_values[key] = value

    if not amount_text:
        try:
            bonus_amount = int(getattr(offer, "bonus_amount_kopeks", 0))
        except (TypeError, ValueError):
            bonus_amount = 0
        if bonus_amount > 0:
            amount_text = settings.format_price(bonus_amount)

    if amount_text:
        format_values.setdefault("amount", amount_text)

    success_message = _format_text_with_placeholders(success_template, format_values)

    await callback.answer("✅ Скидка активирована!", show_alert=True)

    offer_type = None
    if isinstance(extra_data, dict):
        offer_type = extra_data.get("offer_type")

    subscription = getattr(db_user, "subscription", None)

    if offer_type == "purchase_discount":
        button_text = texts.get("MENU_BUY_SUBSCRIPTION", "💎 Купить подписку")
        button_callback = "subscription_upgrade"
    elif offer_type == "extend_discount":
        button_text = texts.get("SUBSCRIPTION_EXTEND", "💎 Продлить подписку")
        button_callback = "subscription_extend"
    else:
        has_active_paid_subscription = bool(
            subscription
            and getattr(subscription, "is_active", False)
            and not getattr(subscription, "is_trial", False)
        )

        if has_active_paid_subscription:
            button_text = texts.get("SUBSCRIPTION_EXTEND", "💎 Продлить подписку")
            button_callback = "subscription_extend"
        else:
            button_text = texts.get("MENU_BUY_SUBSCRIPTION", "💎 Купить подписку")
            button_callback = "subscription_upgrade"

    buy_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=button_text,
                    callback_data=button_callback,
                )
            ]
        ]
    )
    await callback.message.answer(success_message, reply_markup=buy_keyboard)


async def handle_promo_offer_close(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession,
):
    try:
        await callback.message.delete()
    except Exception:
        try:
            await callback.message.edit_reply_markup()
        except Exception:
            pass

    await callback.answer()


async def handle_device_guide(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    device_type = callback.data.split('_')[2]
    texts = get_texts(db_user.language)
    subscription = db_user.subscription
    subscription_link = get_display_subscription_link(subscription)

    if not subscription_link:
        await callback.answer(
            texts.t("SUBSCRIPTION_LINK_UNAVAILABLE", "❌ Ссылка подписки недоступна"),
            show_alert=True,
        )
        return

    apps = get_apps_for_device(device_type, db_user.language)
    hide_subscription_link = settings.should_hide_subscription_link()

    if not apps:
        await callback.answer(
            texts.t("SUBSCRIPTION_DEVICE_APPS_NOT_FOUND", "❌ Приложения для этого устройства не найдены"),
            show_alert=True,
        )
        return

    featured_app = next((app for app in apps if app.get('isFeatured', False)), apps[0])

    if hide_subscription_link:
        link_section = (
                texts.t("SUBSCRIPTION_DEVICE_LINK_TITLE", "🔗 <b>Ссылка подписки:</b>")
                + "\n"
                + texts.t(
            "SUBSCRIPTION_LINK_HIDDEN_NOTICE",
            "ℹ️ Ссылка подписки доступна по кнопкам ниже или в разделе \"Моя подписка\".",
        )
                + "\n\n"
        )
    else:
        link_section = (
                texts.t("SUBSCRIPTION_DEVICE_LINK_TITLE", "🔗 <b>Ссылка подписки:</b>")
                + f"\n<code>{subscription_link}</code>\n\n"
        )

    guide_text = (
            texts.t(
                "SUBSCRIPTION_DEVICE_GUIDE_TITLE",
                "📱 <b>Настройка для {device_name}</b>",
            ).format(device_name=get_device_name(device_type, db_user.language))
            + "\n\n"
            + link_section
            + texts.t(
        "SUBSCRIPTION_DEVICE_FEATURED_APP",
        "📋 <b>Рекомендуемое приложение:</b> {app_name}",
    ).format(app_name=featured_app['name'])
            + "\n\n"
            + texts.t("SUBSCRIPTION_DEVICE_STEP_INSTALL_TITLE", "<b>Шаг 1 - Установка:</b>")
            + f"\n{featured_app['installationStep']['description'][db_user.language]}\n\n"
            + texts.t("SUBSCRIPTION_DEVICE_STEP_ADD_TITLE", "<b>Шаг 2 - Добавление подписки:</b>")
            + f"\n{featured_app['addSubscriptionStep']['description'][db_user.language]}\n\n"
            + texts.t("SUBSCRIPTION_DEVICE_STEP_CONNECT_TITLE", "<b>Шаг 3 - Подключение:</b>")
            + f"\n{featured_app['connectAndUseStep']['description'][db_user.language]}\n\n"
            + texts.t("SUBSCRIPTION_DEVICE_HOW_TO_TITLE", "💡 <b>Как подключить:</b>")
            + "\n"
            + "\n".join(
        [
            texts.t(
                "SUBSCRIPTION_DEVICE_HOW_TO_STEP1",
                "1. Установите приложение по ссылке выше",
            ),
            texts.t(
                "SUBSCRIPTION_DEVICE_HOW_TO_STEP2",
                "2. Скопируйте ссылку подписки (нажмите на неё)",
            ),
            texts.t(
                "SUBSCRIPTION_DEVICE_HOW_TO_STEP3",
                "3. Откройте приложение и вставьте ссылку",
            ),
            texts.t(
                "SUBSCRIPTION_DEVICE_HOW_TO_STEP4",
                "4. Подключитесь к серверу",
            ),
        ]
    )
    )

    await callback.message.edit_text(
        guide_text,
        reply_markup=get_connection_guide_keyboard(
            subscription_link,
            featured_app,
            db_user.language
        ),
        parse_mode="HTML"
    )
    await callback.answer()


async def handle_app_selection(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    device_type = callback.data.split('_')[2]
    texts = get_texts(db_user.language)
    subscription = db_user.subscription

    apps = get_apps_for_device(device_type, db_user.language)

    if not apps:
        await callback.answer(
            texts.t("SUBSCRIPTION_DEVICE_APPS_NOT_FOUND", "❌ Приложения для этого устройства не найдены"),
            show_alert=True,
        )
        return

    app_text = (
            texts.t(
                "SUBSCRIPTION_APPS_TITLE",
                "📱 <b>Приложения для {device_name}</b>",
            ).format(device_name=get_device_name(device_type, db_user.language))
            + "\n\n"
            + texts.t("SUBSCRIPTION_APPS_PROMPT", "Выберите приложение для подключения:")
    )

    await callback.message.edit_text(
        app_text,
        reply_markup=get_app_selection_keyboard(device_type, apps, db_user.language),
        parse_mode="HTML"
    )
    await callback.answer()


async def handle_specific_app_guide(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    _, device_type, app_id = callback.data.split('_')
    texts = get_texts(db_user.language)
    subscription = db_user.subscription

    subscription_link = get_display_subscription_link(subscription)

    if not subscription_link:
        await callback.answer(
            texts.t("SUBSCRIPTION_LINK_UNAVAILABLE", "❌ Ссылка подписки недоступна"),
            show_alert=True,
        )
        return

    apps = get_apps_for_device(device_type, db_user.language)
    app = next((a for a in apps if a['id'] == app_id), None)

    if not app:
        await callback.answer(
            texts.t("SUBSCRIPTION_APP_NOT_FOUND", "❌ Приложение не найдено"),
            show_alert=True,
        )
        return

    hide_subscription_link = settings.should_hide_subscription_link()

    if hide_subscription_link:
        link_section = (
                texts.t("SUBSCRIPTION_DEVICE_LINK_TITLE", "🔗 <b>Ссылка подписки:</b>")
                + "\n"
                + texts.t(
            "SUBSCRIPTION_LINK_HIDDEN_NOTICE",
            "ℹ️ Ссылка подписки доступна по кнопкам ниже или в разделе \"Моя подписка\".",
        )
                + "\n\n"
        )
    else:
        link_section = (
                texts.t("SUBSCRIPTION_DEVICE_LINK_TITLE", "🔗 <b>Ссылка подписки:</b>")
                + f"\n<code>{subscription_link}</code>\n\n"
        )

    guide_text = (
            texts.t(
                "SUBSCRIPTION_SPECIFIC_APP_TITLE",
                "📱 <b>{app_name} - {device_name}</b>",
            ).format(app_name=app['name'], device_name=get_device_name(device_type, db_user.language))
            + "\n\n"
            + link_section
            + texts.t("SUBSCRIPTION_DEVICE_STEP_INSTALL_TITLE", "<b>Шаг 1 - Установка:</b>")
            + f"\n{app['installationStep']['description'][db_user.language]}\n\n"
            + texts.t("SUBSCRIPTION_DEVICE_STEP_ADD_TITLE", "<b>Шаг 2 - Добавление подписки:</b>")
            + f"\n{app['addSubscriptionStep']['description'][db_user.language]}\n\n"
            + texts.t("SUBSCRIPTION_DEVICE_STEP_CONNECT_TITLE", "<b>Шаг 3 - Подключение:</b>")
            + f"\n{app['connectAndUseStep']['description'][db_user.language]}"
    )

    if 'additionalAfterAddSubscriptionStep' in app:
        additional = app['additionalAfterAddSubscriptionStep']
        guide_text += (
                "\n\n"
                + texts.t(
            "SUBSCRIPTION_ADDITIONAL_STEP_TITLE",
            "<b>{title}:</b>",
        ).format(title=additional['title'][db_user.language])
                + f"\n{additional['description'][db_user.language]}"
        )

    await callback.message.edit_text(
        guide_text,
        reply_markup=get_specific_app_keyboard(
            subscription_link,
            app,
            device_type,
            db_user.language
        ),
        parse_mode="HTML"
    )
    await callback.answer()


async def handle_no_traffic_packages(
        callback: types.CallbackQuery,
        db_user: User
):
    await callback.answer(
        "⚠️ В данный момент нет доступных пакетов трафика. "
        "Обратитесь в техподдержку для получения информации.",
        show_alert=True
    )


async def handle_open_subscription_link(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    texts = get_texts(db_user.language)
    subscription = db_user.subscription
    subscription_link = get_display_subscription_link(subscription)

    if not subscription_link:
        await callback.answer(
            texts.t("SUBSCRIPTION_LINK_UNAVAILABLE", "❌ Ссылка подписки недоступна"),
            show_alert=True,
        )
        return

    if settings.is_happ_cryptolink_mode():
        redirect_link = get_happ_cryptolink_redirect_link(subscription_link)
        happ_scheme_link = convert_subscription_link_to_happ_scheme(subscription_link)
        happ_message = (
                texts.t(
                    "SUBSCRIPTION_HAPP_OPEN_TITLE",
                    "🔗 <b>Подключение через Happ</b>",
                )
                + "\n\n"
                + texts.t(
            "SUBSCRIPTION_HAPP_OPEN_LINK",
            "<a href=\"{subscription_link}\">🔓 Открыть ссылку в Happ</a>",
        ).format(subscription_link=happ_scheme_link)
                + "\n\n"
                + texts.t(
            "SUBSCRIPTION_HAPP_OPEN_HINT",
            "💡 Если ссылка не открывается автоматически, скопируйте её вручную:",
        )
        )

        if redirect_link:
            happ_message += "\n\n" + texts.t(
                "SUBSCRIPTION_HAPP_OPEN_BUTTON_HINT",
                "▶️ Нажмите кнопку \"Подключиться\" ниже, чтобы открыть Happ и добавить подписку автоматически.",
            )

        happ_message += "\n\n" + texts.t(
            "SUBSCRIPTION_HAPP_CRYPTOLINK_BLOCK",
            "<blockquote expandable><code>{crypto_link}</code></blockquote>",
        ).format(crypto_link=subscription_link)

        keyboard = get_happ_cryptolink_keyboard(
            subscription_link,
            db_user.language,
            redirect_link=redirect_link,
        )

        await callback.message.answer(
            happ_message,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=keyboard,
        )
        await callback.answer()
        return

    link_text = (
            texts.t("SUBSCRIPTION_DEVICE_LINK_TITLE", "🔗 <b>Ссылка подписки:</b>")
            + "\n\n"
            + f"<code>{subscription_link}</code>\n\n"
            + texts.t("SUBSCRIPTION_LINK_USAGE_TITLE", "📱 <b>Как использовать:</b>")
            + "\n"
            + "\n".join(
        [
            texts.t(
                "SUBSCRIPTION_LINK_STEP1",
                "1. Нажмите на ссылку выше чтобы её скопировать",
            ),
            texts.t(
                "SUBSCRIPTION_LINK_STEP2",
                "2. Откройте ваше VPN приложение",
            ),
            texts.t(
                "SUBSCRIPTION_LINK_STEP3",
                "3. Найдите функцию \"Добавить подписку\" или \"Import\"",
            ),
            texts.t(
                "SUBSCRIPTION_LINK_STEP4",
                "4. Вставьте скопированную ссылку",
            ),
        ]
    )
            + "\n\n"
            + texts.t(
        "SUBSCRIPTION_LINK_HINT",
        "💡 Если ссылка не скопировалась, выделите её вручную и скопируйте.",
    )
    )

    await callback.message.edit_text(
        link_text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text=texts.t("CONNECT_BUTTON", "🔗 Подключиться"),
                                     callback_data="subscription_connect")
            ],
            [
                InlineKeyboardButton(text=texts.BACK, callback_data="menu_subscription")
            ]
        ]),
        parse_mode="HTML"
    )
    await callback.answer()


def load_app_config() -> Dict[str, Any]:
    try:
        from app.config import settings
        config_path = settings.get_app_config_path()

        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Ошибка загрузки конфига приложений: {e}")
        return {}


def get_apps_for_device(device_type: str, language: str = "ru") -> List[Dict[str, Any]]:
    config = load_app_config()['platforms']

    device_mapping = {
        'ios': 'ios',
        'android': 'android',
        'windows': 'windows',
        'mac': 'macos',
        'tv': 'androidTV'
    }

    config_key = device_mapping.get(device_type, device_type)
    return config.get(config_key, [])


def get_device_name(device_type: str, language: str = "ru") -> str:
    if language == "en":
        names = {
            'ios': 'iPhone/iPad',
            'android': 'Android',
            'windows': 'Windows',
            'mac': 'macOS',
            'tv': 'Android TV'
        }
    else:
        names = {
            'ios': 'iPhone/iPad',
            'android': 'Android',
            'windows': 'Windows',
            'mac': 'macOS',
            'tv': 'Android TV'
        }

    return names.get(device_type, device_type)


def create_deep_link(app: Dict[str, Any], subscription_url: str) -> str:
    return subscription_url


def get_reset_devices_confirm_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Да, сбросить все устройства",
                callback_data="confirm_reset_devices"
            )
        ],
        [
            InlineKeyboardButton(text="❌ Отмена", callback_data="menu_subscription")
        ]
    ])


async def send_trial_notification(callback: types.CallbackQuery, db: AsyncSession, db_user: User,
                                  subscription: Subscription):
    try:
        notification_service = AdminNotificationService(callback.bot)
        await notification_service.send_trial_activation_notification(db, db_user, subscription)
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления о триале: {e}")


async def show_device_connection_help(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    subscription = db_user.subscription
    subscription_link = get_display_subscription_link(subscription)

    if not subscription_link:
        await callback.answer("❌ Ссылка подписки недоступна", show_alert=True)
        return

    help_text = f"""
📱 <b>Как подключить устройство заново</b>

После сброса устройства вам нужно:

<b>1. Получить ссылку подписки:</b>
📋 Скопируйте ссылку ниже или найдите её в разделе "Моя подписка"

<b>2. Настроить VPN приложение:</b>
• Откройте ваше VPN приложение
• Найдите функцию "Добавить подписку" или "Import"
• Вставьте скопированную ссылку

<b>3. Подключиться:</b>
• Выберите сервер
• Нажмите "Подключить"

<b>🔗 Ваша ссылка подписки:</b>
<code>{subscription_link}</code>

💡 <b>Совет:</b> Сохраните эту ссылку - она понадобится для подключения новых устройств
"""

    await callback.message.edit_text(
        help_text,
        reply_markup=get_device_management_help_keyboard(db_user.language),
        parse_mode="HTML"
    )
    await callback.answer()


async def send_purchase_notification(
        callback: types.CallbackQuery,
        db: AsyncSession,
        db_user: User,
        subscription: Subscription,
        transaction_id: int,
        period_days: int,
        was_trial_conversion: bool = False
):
    try:
        from app.database.crud.transaction import get_transaction_by_id

        transaction = await get_transaction_by_id(db, transaction_id)
        if transaction:
            notification_service = AdminNotificationService(callback.bot)
            await notification_service.send_subscription_purchase_notification(
                db, db_user, subscription, transaction, period_days, was_trial_conversion
            )
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления о покупке: {e}")


async def send_extension_notification(
        callback: types.CallbackQuery,
        db: AsyncSession,
        db_user: User,
        subscription: Subscription,
        transaction_id: int,
        extended_days: int,
        old_end_date: datetime
):
    try:
        from app.database.crud.transaction import get_transaction_by_id

        transaction = await get_transaction_by_id(db, transaction_id)
        if transaction:
            notification_service = AdminNotificationService(callback.bot)
            await notification_service.send_subscription_extension_notification(
                db, db_user, subscription, transaction, extended_days, old_end_date
            )
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления о продлении: {e}")


async def handle_switch_traffic(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    from app.config import settings

    if settings.is_traffic_fixed():
        await callback.answer("⚠️ В текущем режиме трафик фиксированный", show_alert=True)
        return

    texts = get_texts(db_user.language)
    subscription = db_user.subscription

    if not subscription or subscription.is_trial:
        await callback.answer("⚠️ Эта функция доступна только для платных подписок", show_alert=True)
        return

    current_traffic = subscription.traffic_limit_gb
    period_hint_days = _get_period_hint_from_subscription(subscription)
    traffic_discount_percent = _get_addon_discount_percent_for_user(
        db_user,
        "traffic",
        period_hint_days,
    )

    await callback.message.edit_text(
        f"🔄 <b>Переключение лимита трафика</b>\n\n"
        f"Текущий лимит: {texts.format_traffic(current_traffic)}\n"
        f"Выберите новый лимит трафика:\n\n"
        f"💡 <b>Важно:</b>\n"
        f"• При увеличении - доплата за разницу\n"
        f"• При уменьшении - возврат средств не производится",
        reply_markup=get_traffic_switch_keyboard(
            current_traffic,
            db_user.language,
            subscription.end_date,
            traffic_discount_percent,
        ),
        parse_mode="HTML"
    )

    await callback.answer()


async def confirm_switch_traffic(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    new_traffic_gb = int(callback.data.split('_')[2])
    texts = get_texts(db_user.language)
    subscription = db_user.subscription

    current_traffic = subscription.traffic_limit_gb

    if new_traffic_gb == current_traffic:
        await callback.answer("ℹ️ Лимит трафика не изменился", show_alert=True)
        return

    old_price_per_month = settings.get_traffic_price(current_traffic)
    new_price_per_month = settings.get_traffic_price(new_traffic_gb)

    months_remaining = get_remaining_months(subscription.end_date)
    period_hint_days = months_remaining * 30 if months_remaining > 0 else None
    traffic_discount_percent = _get_addon_discount_percent_for_user(
        db_user,
        "traffic",
        period_hint_days,
    )

    discounted_old_per_month, _ = apply_percentage_discount(
        old_price_per_month,
        traffic_discount_percent,
    )
    discounted_new_per_month, _ = apply_percentage_discount(
        new_price_per_month,
        traffic_discount_percent,
    )
    price_difference_per_month = discounted_new_per_month - discounted_old_per_month
    discount_savings_per_month = (
            (new_price_per_month - old_price_per_month) - price_difference_per_month
    )

    if price_difference_per_month > 0:
        total_price_difference = price_difference_per_month * months_remaining

        if db_user.balance_kopeks < total_price_difference:
            missing_kopeks = total_price_difference - db_user.balance_kopeks
            message_text = texts.t(
                "ADDON_INSUFFICIENT_FUNDS_MESSAGE",
                (
                    "⚠️ <b>Недостаточно средств</b>\n\n"
                    "Стоимость услуги: {required}\n"
                    "На балансе: {balance}\n"
                    "Не хватает: {missing}\n\n"
                    "Выберите способ пополнения. Сумма подставится автоматически."
                ),
            ).format(
                required=f"{texts.format_price(total_price_difference)} (за {months_remaining} мес)",
                balance=texts.format_price(db_user.balance_kopeks),
                missing=texts.format_price(missing_kopeks),
            )

            await callback.message.edit_text(
                message_text,
                reply_markup=get_insufficient_balance_keyboard(
                    db_user.language,
                    amount_kopeks=missing_kopeks,
                ),
                parse_mode="HTML",
            )
            await callback.answer()
            return

        action_text = f"увеличить до {texts.format_traffic(new_traffic_gb)}"
        cost_text = f"Доплата: {texts.format_price(total_price_difference)} (за {months_remaining} мес)"
        if discount_savings_per_month > 0:
            total_discount_savings = discount_savings_per_month * months_remaining
            cost_text += (
                f" (скидка {traffic_discount_percent}%:"
                f" -{texts.format_price(total_discount_savings)})"
            )
    else:
        total_price_difference = 0
        action_text = f"уменьшить до {texts.format_traffic(new_traffic_gb)}"
        cost_text = "Возврат средств не производится"

    confirm_text = f"🔄 <b>Подтверждение переключения трафика</b>\n\n"
    confirm_text += f"Текущий лимит: {texts.format_traffic(current_traffic)}\n"
    confirm_text += f"Новый лимит: {texts.format_traffic(new_traffic_gb)}\n\n"
    confirm_text += f"Действие: {action_text}\n"
    confirm_text += f"💰 {cost_text}\n\n"
    confirm_text += "Подтвердить переключение?"

    await callback.message.edit_text(
        confirm_text,
        reply_markup=get_confirm_switch_traffic_keyboard(new_traffic_gb, total_price_difference, db_user.language),
        parse_mode="HTML"
    )

    await callback.answer()


async def clear_saved_cart(
        callback: types.CallbackQuery,
        state: FSMContext,
        db_user: User,
        db: AsyncSession
):
    await state.clear()

    from app.handlers.menu import show_main_menu
    await show_main_menu(callback, db_user, db)

    await callback.answer("🗑️ Корзина очищена")


async def execute_switch_traffic(
        callback: types.CallbackQuery,
        db_user: User,
        db: AsyncSession
):
    callback_parts = callback.data.split('_')
    new_traffic_gb = int(callback_parts[3])
    price_difference = int(callback_parts[4])

    texts = get_texts(db_user.language)
    subscription = db_user.subscription
    current_traffic = subscription.traffic_limit_gb

    try:
        if price_difference > 0:
            success = await subtract_user_balance(
                db, db_user, price_difference,
                f"Переключение трафика с {current_traffic}GB на {new_traffic_gb}GB"
            )

            if not success:
                await callback.answer("⚠️ Ошибка списания средств", show_alert=True)
                return

            months_remaining = get_remaining_months(subscription.end_date)
            await create_transaction(
                db=db,
                user_id=db_user.id,
                type=TransactionType.SUBSCRIPTION_PAYMENT,
                amount_kopeks=price_difference,
                description=f"Переключение трафика с {current_traffic}GB на {new_traffic_gb}GB на {months_remaining} мес"
            )

        subscription.traffic_limit_gb = new_traffic_gb
        subscription.updated_at = datetime.utcnow()

        await db.commit()

        subscription_service = SubscriptionService()
        await subscription_service.update_remnawave_user(db, subscription)

        await db.refresh(db_user)
        await db.refresh(subscription)

        try:
            from app.services.admin_notification_service import AdminNotificationService
            notification_service = AdminNotificationService(callback.bot)
            await notification_service.send_subscription_update_notification(
                db, db_user, subscription, "traffic", current_traffic, new_traffic_gb, price_difference
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления об изменении трафика: {e}")

        if new_traffic_gb > current_traffic:
            success_text = f"✅ Лимит трафика увеличен!\n\n"
            success_text += f"📊 Было: {texts.format_traffic(current_traffic)} → "
            success_text += f"Стало: {texts.format_traffic(new_traffic_gb)}\n"
            if price_difference > 0:
                success_text += f"💰 Списано: {texts.format_price(price_difference)}"
        elif new_traffic_gb < current_traffic:
            success_text = f"✅ Лимит трафика уменьшен!\n\n"
            success_text += f"📊 Было: {texts.format_traffic(current_traffic)} → "
            success_text += f"Стало: {texts.format_traffic(new_traffic_gb)}\n"
            success_text += f"ℹ️ Возврат средств не производится"

        await callback.message.edit_text(
            success_text,
            reply_markup=get_back_keyboard(db_user.language)
        )

        logger.info(
            f"✅ Пользователь {db_user.telegram_id} переключил трафик с {current_traffic}GB на {new_traffic_gb}GB, доплата: {price_difference / 100}₽")

    except Exception as e:
        logger.error(f"Ошибка переключения трафика: {e}")
        await callback.message.edit_text(
            texts.ERROR,
            reply_markup=get_back_keyboard(db_user.language)
        )

    await callback.answer()


def get_traffic_switch_keyboard(
        current_traffic_gb: int,
        language: str = "ru",
        subscription_end_date: datetime = None,
        discount_percent: int = 0,
) -> InlineKeyboardMarkup:
    from app.config import settings

    months_multiplier = 1
    period_text = ""
    if subscription_end_date:
        months_multiplier = get_remaining_months(subscription_end_date)
        if months_multiplier > 1:
            period_text = f" (за {months_multiplier} мес)"

    packages = settings.get_traffic_packages()
    enabled_packages = [pkg for pkg in packages if pkg['enabled']]

    current_price_per_month = settings.get_traffic_price(current_traffic_gb)
    discounted_current_per_month, _ = apply_percentage_discount(
        current_price_per_month,
        discount_percent,
    )

    buttons = []

    for package in enabled_packages:
        gb = package['gb']
        price_per_month = package['price']
        discounted_price_per_month, _ = apply_percentage_discount(
            price_per_month,
            discount_percent,
        )

        price_diff_per_month = discounted_price_per_month - discounted_current_per_month
        total_price_diff = price_diff_per_month * months_multiplier

        if gb == current_traffic_gb:
            emoji = "✅"
            action_text = " (текущий)"
            price_text = ""
        elif total_price_diff > 0:
            emoji = "⬆️"
            action_text = ""
            price_text = f" (+{total_price_diff // 100}₽{period_text})"
            if discount_percent > 0:
                discount_total = (
                        (price_per_month - current_price_per_month) * months_multiplier
                        - total_price_diff
                )
                if discount_total > 0:
                    price_text += f" (скидка {discount_percent}%: -{discount_total // 100}₽)"
        elif total_price_diff < 0:
            emoji = "⬇️"
            action_text = ""
            price_text = " (без возврата)"
        else:
            emoji = "🔄"
            action_text = ""
            price_text = " (бесплатно)"

        if gb == 0:
            traffic_text = "Безлимит"
        else:
            traffic_text = f"{gb} ГБ"

        button_text = f"{emoji} {traffic_text}{action_text}{price_text}"

        buttons.append([
            InlineKeyboardButton(text=button_text, callback_data=f"switch_traffic_{gb}")
        ])

    buttons.append([
        InlineKeyboardButton(
            text="⬅️ Назад" if language == "ru" else "⬅️ Back",
            callback_data="subscription_settings"
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_confirm_switch_traffic_keyboard(
        new_traffic_gb: int,
        price_difference: int,
        language: str = "ru"
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Подтвердить переключение",
                callback_data=f"confirm_switch_traffic_{new_traffic_gb}_{price_difference}"
            )
        ],
        [
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data="subscription_settings"
            )
        ]
    ])


def register_handlers(dp: Dispatcher):
    update_traffic_prices()

    dp.callback_query.register(
        show_subscription_info,
        F.data == "menu_subscription"
    )

    dp.callback_query.register(
        show_trial_offer,
        F.data == "menu_trial"
    )

    dp.callback_query.register(
        activate_trial,
        F.data == "trial_activate"
    )

    dp.callback_query.register(
        start_subscription_purchase,
        F.data.in_(["menu_buy", "subscription_upgrade"])
    )

    dp.callback_query.register(
        handle_add_countries,
        F.data == "subscription_add_countries"
    )

    dp.callback_query.register(
        handle_switch_traffic,
        F.data == "subscription_switch_traffic"
    )

    dp.callback_query.register(
        confirm_switch_traffic,
        F.data.startswith("switch_traffic_")
    )

    dp.callback_query.register(
        execute_switch_traffic,
        F.data.startswith("confirm_switch_traffic_")
    )

    dp.callback_query.register(
        handle_change_devices,
        F.data == "subscription_change_devices"
    )

    dp.callback_query.register(
        confirm_change_devices,
        F.data.startswith("change_devices_")
    )

    dp.callback_query.register(
        execute_change_devices,
        F.data.startswith("confirm_change_devices_")
    )

    dp.callback_query.register(
        handle_extend_subscription,
        F.data == "subscription_extend"
    )

    dp.callback_query.register(
        handle_reset_traffic,
        F.data == "subscription_reset_traffic"
    )

    dp.callback_query.register(
        confirm_add_devices,
        F.data.startswith("add_devices_")
    )

    dp.callback_query.register(
        confirm_extend_subscription,
        F.data.startswith("extend_period_")
    )

    dp.callback_query.register(
        confirm_reset_traffic,
        F.data == "confirm_reset_traffic"
    )

    dp.callback_query.register(
        handle_reset_devices,
        F.data == "subscription_reset_devices"
    )

    dp.callback_query.register(
        confirm_reset_devices,
        F.data == "confirm_reset_devices"
    )

    dp.callback_query.register(
        select_period,
        F.data.startswith("period_"),
        SubscriptionStates.selecting_period
    )

    dp.callback_query.register(
        select_traffic,
        F.data.startswith("traffic_"),
        SubscriptionStates.selecting_traffic
    )

    dp.callback_query.register(
        select_devices,
        F.data.startswith("devices_") & ~F.data.in_(["devices_continue"]),
        SubscriptionStates.selecting_devices
    )

    dp.callback_query.register(
        devices_continue,
        F.data == "devices_continue",
        SubscriptionStates.selecting_devices
    )

    dp.callback_query.register(
        confirm_purchase,
        F.data == "subscription_confirm",
        SubscriptionStates.confirming_purchase
    )

    dp.callback_query.register(
        resume_subscription_checkout,
        F.data == "subscription_resume_checkout",
    )

    dp.callback_query.register(
        return_to_saved_cart,
        F.data == "return_to_saved_cart",
    )

    dp.callback_query.register(
        clear_saved_cart,
        F.data == "clear_saved_cart",
    )

    dp.callback_query.register(
        handle_autopay_menu,
        F.data == "subscription_autopay"
    )

    dp.callback_query.register(
        toggle_autopay,
        F.data.in_(["autopay_enable", "autopay_disable"])
    )

    dp.callback_query.register(
        show_autopay_days,
        F.data == "autopay_set_days"
    )

    dp.callback_query.register(
        handle_subscription_config_back,
        F.data == "subscription_config_back"
    )

    dp.callback_query.register(
        handle_subscription_cancel,
        F.data == "subscription_cancel"
    )

    dp.callback_query.register(
        set_autopay_days,
        F.data.startswith("autopay_days_")
    )

    dp.callback_query.register(
        select_country,
        F.data.startswith("country_"),
        SubscriptionStates.selecting_countries
    )

    dp.callback_query.register(
        countries_continue,
        F.data == "countries_continue",
        SubscriptionStates.selecting_countries
    )

    dp.callback_query.register(
        handle_manage_country,
        F.data.startswith("country_manage_")
    )

    dp.callback_query.register(
        apply_countries_changes,
        F.data == "countries_apply"
    )

    dp.callback_query.register(
        claim_discount_offer,
        F.data.startswith("claim_discount_")
    )

    dp.callback_query.register(
        handle_promo_offer_close,
        F.data == "promo_offer_close",
    )

    dp.callback_query.register(
        handle_happ_download_request,
        F.data == "subscription_happ_download"
    )

    dp.callback_query.register(
        handle_happ_download_platform_choice,
        F.data.in_([
            "happ_download_ios",
            "happ_download_android",
            "happ_download_pc",
            "happ_download_macos",
            "happ_download_windows",
        ])
    )

    dp.callback_query.register(
        handle_happ_download_close,
        F.data == "happ_download_close"
    )

    dp.callback_query.register(
        handle_happ_download_back,
        F.data == "happ_download_back"
    )

    dp.callback_query.register(
        handle_connect_subscription,
        F.data == "subscription_connect"
    )

    dp.callback_query.register(
        handle_device_guide,
        F.data.startswith("device_guide_")
    )

    dp.callback_query.register(
        handle_app_selection,
        F.data.startswith("app_list_")
    )

    dp.callback_query.register(
        handle_specific_app_guide,
        F.data.startswith("app_")
    )

    dp.callback_query.register(
        handle_open_subscription_link,
        F.data == "open_subscription_link"
    )

    dp.callback_query.register(
        handle_subscription_settings,
        F.data == "subscription_settings"
    )

    dp.callback_query.register(
        handle_no_traffic_packages,
        F.data == "no_traffic_packages"
    )

    dp.callback_query.register(
        handle_device_management,
        F.data == "subscription_manage_devices"
    )

    dp.callback_query.register(
        handle_devices_page,
        F.data.startswith("devices_page_")
    )

    dp.callback_query.register(
        handle_single_device_reset,
        F.data.regexp(r"^reset_device_\d+_\d+$")
    )

    dp.callback_query.register(
        handle_all_devices_reset_from_management,
        F.data == "reset_all_devices"
    )

    dp.callback_query.register(
        show_device_connection_help,
        F.data == "device_connection_help"
    )