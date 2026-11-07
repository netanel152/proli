"""PRO-224 — a pro's phone is stored in the bare ``9725…`` form, whatever was
typed into the admin panel's pro form, so the bot's ``[phone, chat_id]``
lookup finds the pro.

Covers ``app.core.phone.to_il_mobile``, the panel's streamlit-free
``admin_panel.core.pro_phone.check_pro_phone`` seam (sync mongomock, as the
panel is pymongo), and ``scripts/normalize_pro_phones.py`` against the shared
async ``mock_db`` (the script binds ``users_collection`` at import, so it is
monkeypatched directly, per tests/test_check_pro_service_areas.py).
"""

from unittest.mock import AsyncMock

import mongomock
import pytest
import pytest_asyncio
from bson import ObjectId

import scripts.normalize_pro_phones as script
from admin_panel.core.config import TRANS
from admin_panel.core.pro_phone import ERR_INVALID, ERR_TAKEN, check_pro_phone
from app.core.phone import to_chat_id, to_il_mobile

CANONICAL = "972501234567"


@pytest.mark.parametrize(
    "raw",
    [
        "972501234567",
        "0501234567",
        "050-123-4567",
        "050 1234567",
        "(050) 123-4567",
        "+972501234567",
        "+972 50-123-4567",
        "+972 050 123 4567",
        "00972501234567",
        "972501234567@c.us",
        "  0501234567  ",
    ],
)
def test_every_format_normalises_to_972(raw):
    assert to_il_mobile(raw) == CANONICAL


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "12345",
        "031234567",  # landline
        "0721234567",  # 072 is not mobile
        "05012345678",  # one digit too many
        "050123456",  # one digit too few
        "050-abc-4567",
        "972012345678",  # the seed_coverage_matrix reserved block
    ],
)
def test_non_mobile_is_rejected(raw):
    assert to_il_mobile(raw) == ""


@pytest.fixture
def users():
    return mongomock.MongoClient().proli_db.users


@pytest.mark.parametrize("raw", ["050-123-4567", "+972 50 123 4567", "0501234567"])
def test_form_saves_972_form(users, raw):
    assert check_pro_phone(users, raw) == (CANONICAL, None)


def test_form_rejects_invalid_phone(users):
    assert check_pro_phone(users, "03-1234567") == ("", ERR_INVALID)


@pytest.mark.parametrize("stored", [CANONICAL, "0501234567", CANONICAL + "@c.us"])
def test_form_refuses_a_phone_another_pro_has(users, stored):
    users.insert_one({"role": "professional", "phone_number": stored})
    assert check_pro_phone(users, "050-123-4567") == ("", ERR_TAKEN)


def test_editing_a_pro_does_not_clash_with_itself(users):
    pro_id = users.insert_one({"phone_number": CANONICAL}).inserted_id
    assert check_pro_phone(users, "0501234567", exclude_id=pro_id) == (CANONICAL, None)


def test_error_keys_exist_in_both_languages():
    for lang in TRANS.values():
        assert lang[ERR_INVALID] and lang[ERR_TAKEN]


@pytest.mark.asyncio
async def test_normalised_pro_is_found_by_the_bot_lookup(monkeypatch, mock_db, users):
    import app.services.pro_onboarding_service as onboarding

    phone, _ = check_pro_phone(users, "050-123-4567")
    await mock_db.users.delete_many({})
    await mock_db.users.insert_one({"role": "professional", "phone_number": phone})
    monkeypatch.setattr(onboarding, "users_collection", mock_db.users)
    whatsapp = AsyncMock()

    # False = "already registered": the [phone, chat_id] lookup found the pro.
    assert await onboarding.start_onboarding(to_chat_id(phone), whatsapp) is False


# --- scripts/normalize_pro_phones.py ---


@pytest.fixture
def script_users(monkeypatch, mock_db):
    monkeypatch.setattr(script, "users_collection", mock_db.users)
    return mock_db.users


@pytest_asyncio.fixture(autouse=True)
async def _clean_users(mock_db):
    await mock_db.users.delete_many({})
    yield


def _pro(phone):
    return {"_id": ObjectId(), "role": "professional", "phone_number": phone}


@pytest.mark.asyncio
async def test_script_dry_run_reports_and_writes_nothing(script_users, capsys):
    await script_users.insert_many([_pro("050-123-4567"), _pro("972521111111")])
    assert await script.run(apply=False) == script.EXIT_NEEDS_FIX
    out = capsys.readouterr().out
    assert "FIX" in out and "...4567" in out and "050-123-4567" not in out
    assert await script_users.count_documents({"phone_number": CANONICAL}) == 0


@pytest.mark.asyncio
async def test_script_apply_normalises(script_users):
    await script_users.insert_one(_pro("+972 50-123-4567"))
    assert await script.run(apply=True) == script.EXIT_OK
    assert await script_users.count_documents({"phone_number": CANONICAL}) == 1


@pytest.mark.asyncio
async def test_script_refuses_apply_on_duplicate(script_users):
    await script_users.insert_many([_pro("0501234567"), _pro(CANONICAL)])
    assert await script.run(apply=True) == script.EXIT_DUPLICATE
    assert await script_users.count_documents({"phone_number": "0501234567"}) == 1


@pytest.mark.asyncio
async def test_script_leaves_invalid_phone_alone(script_users):
    await script_users.insert_one(_pro("03-1234567"))
    assert await script.run(apply=True) == script.EXIT_NEEDS_FIX
    assert await script_users.count_documents({"phone_number": "03-1234567"}) == 1


@pytest.mark.asyncio
async def test_script_with_no_pros_checks_nothing(script_users):
    assert await script.run(apply=False) == script.EXIT_NOTHING_CHECKED
