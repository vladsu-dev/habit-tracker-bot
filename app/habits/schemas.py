from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    field_validator,
    model_validator,
)

Name = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
]
Target = Annotated[int, Field(strict=True, ge=1, le=2_147_483_647)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Patch(Input):
    @model_validator(mode="after")
    def require_values(self):
        if not self.model_fields_set or any(
            getattr(self, key) is None for key in self.model_fields_set
        ):
            raise ValueError(
                "Укажите хотя бы одно поле и заполните каждое переданное значение"
            )
        return self


class HabitCreate(Input):
    name: Name
    target: Target = 21


class HabitPatch(Patch):
    name: Name | None = None
    target: Target | None = None


class MarkInput(Input):
    completed: StrictBool


class SettingsPatch(Patch):
    timezone: str | None = None
    reminder_time: (
        Annotated[str, Field(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")] | None
    ) = None

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value):
        if value is not None:
            try:
                ZoneInfo(value)
            except (ValueError, ZoneInfoNotFoundError) as exc:
                raise ValueError(
                    "Укажите часовой пояс IANA, например Europe/Moscow"
                ) from exc
        return value
