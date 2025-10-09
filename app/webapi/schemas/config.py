from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class SettingCategorySummary(BaseModel):
    """Краткое описание категории настройки."""

    key: str
    label: str
    items: int

    model_config = ConfigDict(extra="forbid")


class SettingCategoryRef(BaseModel):
    """Ссылка на категорию, к которой относится настройка."""

    key: str
    label: str

    model_config = ConfigDict(extra="forbid")


class SettingChoice(BaseModel):
    """Вариант значения для настройки с выбором."""

    value: Any
    label: str
    description: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class SettingDefinition(BaseModel):
    """Полное описание настройки и её текущего состояния."""

    key: str
    name: str
    category: SettingCategoryRef
    type: str
    is_optional: bool
    current: Any | None = Field(default=None)
    original: Any | None = Field(default=None)
    has_override: bool
    read_only: bool = Field(default=False)
    choices: list[SettingChoice] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class SettingUpdateRequest(BaseModel):
    """Запрос на обновление значения настройки."""

    value: Any

    model_config = ConfigDict(extra="forbid")
