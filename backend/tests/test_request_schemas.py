"""
Request schema and graph guard tests.

Can be run either with pytest or directly:
  python backend/tests/test_request_schemas.py
"""
import os
import sys

from pydantic import ValidationError

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, ROOT)
sys.path.insert(0, BACKEND)

from core.request_schemas import (  # noqa: E402
    LoginUserParams,
    ProfilePayload,
    RegisterUserParams,
    clamp_graph_depth,
)


def test_register_rejects_weak_password():
    try:
        RegisterUserParams(username="demo_user", password="12345678")
    except ValidationError:
        return
    raise AssertionError("weak password should be rejected")


def test_login_keeps_existing_users_compatible():
    params = LoginUserParams(username=" demo_user ", password="old")
    assert params.username == "demo_user"
    assert params.password == "old"


def test_profile_schema_normalizes_known_fields_and_keeps_extras():
    payload = ProfilePayload(
        profile_data={
            "age": "30",
            "height": 175,
            "weight": 70,
            "diseases": ["hypertension", "hypertension", ""],
            "allergies": "penicillin",
            "lifestyle": {"sleep": "regular", "empty": ""},
            "custom_frontend_field": "kept",
        }
    )
    data = payload.profile_data.model_dump(mode="json", exclude_none=True)
    assert data["age"] == 30
    assert data["diseases"] == ["hypertension"]
    assert data["allergies"] == ["penicillin"]
    assert data["lifestyle"] == {"sleep": "regular"}
    assert data["custom_frontend_field"] == "kept"


def test_graph_depth_is_clamped():
    assert clamp_graph_depth(-5) == 1
    assert clamp_graph_depth(2) == 2
    assert clamp_graph_depth(99) == 3
    assert clamp_graph_depth("bad") == 1


def main():
    test_register_rejects_weak_password()
    test_login_keeps_existing_users_compatible()
    test_profile_schema_normalizes_known_fields_and_keeps_extras()
    test_graph_depth_is_clamped()
    print("[OK] request schema tests passed.")


if __name__ == "__main__":
    main()
