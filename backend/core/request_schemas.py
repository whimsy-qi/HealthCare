import os
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _env_float(name: str, default: float, min_value: float, max_value: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(min_value, min(max_value, value))


GRAPH_MIN_DEPTH = 1
GRAPH_MAX_DEPTH = 3
GRAPH_QUERY_TIMEOUT_SEC = _env_float("NEO4J_QUERY_TIMEOUT_SEC", 5.0, 1.0, 30.0)


def clamp_graph_depth(value: Any) -> int:
    try:
        depth = int(value)
    except (TypeError, ValueError):
        depth = GRAPH_MIN_DEPTH
    return max(GRAPH_MIN_DEPTH, min(GRAPH_MAX_DEPTH, depth))


class LoginUserParams(BaseModel):
    username: str = Field(..., min_length=1, max_length=50)
    password: str = Field(..., min_length=1, max_length=128)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("username is required")
        if any(ch.isspace() for ch in value):
            raise ValueError("username must not contain whitespace")
        return value


class RegisterUserParams(LoginUserParams):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("password must not start or end with whitespace")
        lowered = value.lower()
        weak_passwords = {
            "password123",
            "admin1234",
            "qwerty123",
            "12345678",
            "11111111",
        }
        if lowered in weak_passwords:
            raise ValueError("password is too common")
        if not any(ch.isalpha() for ch in value) or not any(ch.isdigit() for ch in value):
            raise ValueError("password must contain both letters and numbers")
        return value


def _clean_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise ValueError("field must be a list")

    seen: set[str] = set()
    cleaned: list[str] = []
    for item in value:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned


class HealthProfileData(BaseModel):
    model_config = ConfigDict(extra="allow")

    age: Optional[int] = Field(default=None, ge=0, le=130)
    height: Optional[float] = Field(default=None, ge=30, le=250)
    weight: Optional[float] = Field(default=None, ge=1, le=500)
    gender: Optional[str] = Field(default=None, max_length=20)
    blood_type: Optional[str] = Field(default=None, max_length=20)
    exercise: Optional[str] = Field(default=None, max_length=80)
    sleep: Optional[str] = Field(default=None, max_length=80)
    smoking: Optional[str] = Field(default=None, max_length=80)
    alcohol: Optional[str] = Field(default=None, max_length=80)

    diseases: list[str] = Field(default_factory=list, max_length=50)
    allergies: list[str] = Field(default_factory=list, max_length=50)
    surgeries: list[str] = Field(default_factory=list, max_length=50)
    medications: list[str] = Field(default_factory=list, max_length=80)
    past_diseases_common: list[str] = Field(default_factory=list, max_length=80)
    past_diseases_custom: list[str] = Field(default_factory=list, max_length=80)
    family_history: list[Any] = Field(default_factory=list, max_length=100)
    lifestyle: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "diseases",
        "allergies",
        "surgeries",
        "medications",
        "past_diseases_common",
        "past_diseases_custom",
        mode="before",
    )
    @classmethod
    def normalize_string_lists(cls, value: Any) -> list[str]:
        return _clean_string_list(value)

    @field_validator("family_history", mode="before")
    @classmethod
    def normalize_family_history(cls, value: Any) -> list[Any]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("family_history must be a list")
        return [item for item in value if item not in (None, "", [], {})]

    @field_validator("lifestyle", mode="before")
    @classmethod
    def normalize_lifestyle(cls, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("lifestyle must be an object")
        return {str(k): v for k, v in value.items() if v not in (None, "", [], {})}


class ProfilePayload(BaseModel):
    profile_data: HealthProfileData
