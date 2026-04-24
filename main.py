import os
import re
from datetime import datetime
from typing import Optional

import httpx
from fastapi import FastAPI
from pydantic import BaseModel, Field

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

FINISH_WORDS = {
    "завершить",
    "стоп",
    "хватит",
    "выход",
    "закрыть",
    "отмена",
    "все",
    "готово",
}

FILLER_PATTERNS = [
    r"\bалиса\b",
    r"\bпожалуйста\b",
    r"\bдобавь\b",
    r"\bдобавить\b",
    r"\bзапиши\b",
    r"\bзаписать\b",
    r"\bкупи\b",
    r"\bкупить\b",
    r"\bв\s+список\s+покупок\b",
    r"\bв\s+список\b",
    r"\bсписок\s+покупок\b",
    r"\bмне\b",
]

app = FastAPI(title="Покупки домой")

ACTIVE_SESSIONS: dict[str, dict] = {}


class AliceRequest(BaseModel):
    request: dict
    session: dict
    version: str
    state: dict = Field(default_factory=dict)


def log(message: str):
    print(message, flush=True)


def now_date_string() -> str:
    try:
        if ZoneInfo is not None:
            return datetime.now(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y")
    except Exception:
        pass
    return datetime.now().strftime("%d.%m.%Y")


def extract_user_text(payload: AliceRequest) -> str:
    command = (payload.request.get("command") or "").strip()
    original_utterance = (payload.request.get("original_utterance") or "").strip()
    return command or original_utterance


def get_session_id(payload: AliceRequest) -> str:
    return str(payload.session.get("session_id", ""))


def get_message_id(payload: AliceRequest) -> int:
    return int(payload.session.get("message_id", 0))


def alice_response(
    request: AliceRequest,
    text: str,
    *,
    end_session: bool = False,
    session_state: Optional[dict] = None,
) -> dict:
    response = {
        "version": request.version,
        "session": request.session,
        "response": {
            "text": text,
            "end_session": end_session,
        },
    }

    if session_state is not None:
        response["session_state"] = session_state

    return response


def clean_text(text: str) -> str:
    text = text.lower().strip()

    for pattern in FILLER_PATTERNS:
        text = re.sub(pattern, " ", text, flags=re.IGNORECASE)

    text = text.replace(";", ",")
    text = text.replace(" ещё ", ", ")
    text = text.replace(" еще ", ", ")
    text = re.sub(r"\s+и\s+", ", ", text, flags=re.IGNORECASE)

    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r"(,\s*){2,}", ", ", text)

    return text.strip(" ,.!?:;")


def split_short_plain_list(text: str) -> list[str]:
    words = text.split()

    if len(words) <= 1:
        return [text] if text else []

    if 2 <= len(words) <= 4 and all(len(word) <= 12 for word in words):
        return words

    return [text]


def parse_items(text: str) -> list[str]:
    text = clean_text(text)

    if not text:
        return []

    if "," in text:
        raw_parts = [part.strip(" .,!?:;") for part in text.split(",")]
        parts = [part for part in raw_parts if part]
    else:
        parts = split_short_plain_list(text)

    cleaned_parts = []
    for part in parts:
        part = re.sub(r"\s+", " ", part).strip(" .,!?:;")
        if part:
            cleaned_parts.append(part)

    return cleaned_parts


def build_telegram_message(items: list[str]) -> str:
    title = f"🛒 Список покупок - {now_date_string()}"
    if not items:
        return f"{title}\n- пусто"

    lines = [title]
    lines.extend(f"- {item}" for item in items)
    return "\n".join(lines)


async def telegram_api_call(method: str, payload: dict) -> dict:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"

    safe_payload = dict(payload)
    if "chat_id" in safe_payload:
        safe_payload["chat_id"] = str(safe_payload["chat_id"])

    log(f"TELEGRAM REQUEST METHOD: {method}")
    log(f"TELEGRAM REQUEST PAYLOAD: {safe_payload}")

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(url, json=payload)

    log(f"TELEGRAM HTTP STATUS: {response.status_code}")
    log(f"TELEGRAM RAW RESPONSE: {response.text}")

    response.raise_for_status()
    data = response.json()

    if not data.get("ok"):
        description = data.get("description", "Unknown Telegram API error")
        raise RuntimeError(f"Telegram API error in {method}: {description}")

    return data


async def upsert_telegram_list(items: list[str], message_id: Optional[int]) -> int:
    if not TELEGRAM_CHAT_ID:
        raise RuntimeError("TELEGRAM_CHAT_ID is not set")

    text = build_telegram_message(items)

    if message_id is None:
        log("TELEGRAM ACTION: sendMessage because message_id is None")
        data = await telegram_api_call(
            "sendMessage",
            {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
            },
        )
        new_message_id = int(data["result"]["message_id"])
        log(f"TELEGRAM NEW MESSAGE_ID: {new_message_id}")
        return new_message_id

    log(f"TELEGRAM ACTION: editMessageText message_id={message_id}")

    try:
        await telegram_api_call(
            "editMessageText",
            {
                "chat_id": TELEGRAM_CHAT_ID,
                "message_id": message_id,
                "text": text,
            },
        )
        return message_id

    except RuntimeError as exc:
        error_text = str(exc).lower()
        log(f"TELEGRAM EDIT ERROR: {str(exc)}")

        if "message is not modified" in error_text:
            log("TELEGRAM EDIT IGNORED: message is not modified")
            return message_id

        raise

    except Exception as exc:
        log(f"TELEGRAM UNEXPECTED ERROR: {repr(exc)}")
        raise


def get_or_create_session_data(session_id: str) -> dict:
    if session_id not in ACTIVE_SESSIONS:
        ACTIVE_SESSIONS[session_id] = {
            "items": [],
            "telegram_message_id": None,
            "last_processed_message_id": None,
            "last_response_text": "Говорите, что добавить в список покупок",
            "last_end_session": False,
        }
    return ACTIVE_SESSIONS[session_id]


@app.get("/")
async def healthcheck():
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(payload: AliceRequest) -> dict:
    session_id = get_session_id(payload)
    message_id = get_message_id(payload)

    log(f"ALICE REQUEST: session_id={session_id}, message_id={message_id}, new={payload.session.get('new')}")
    log(f"ALICE TEXT: {extract_user_text(payload)}")

    if not session_id:
        return alice_response(
            payload,
            "Ошибка сессии. Попробуйте запустить навык еще раз",
            end_session=True,
        )

    if payload.session.get("new"):
        ACTIVE_SESSIONS[session_id] = {
            "items": [],
            "telegram_message_id": None,
            "last_processed_message_id": None,
            "last_response_text": "Говорите, что добавить в список покупок",
            "last_end_session": False,
        }
        return alice_response(
            payload,
            "Говорите, что добавить в список покупок",
            session_state={"stage": "awaiting_items"},
        )

    session_data = get_or_create_session_data(session_id)

    if session_data.get("last_processed_message_id") == message_id:
        log("ALICE DUPLICATE REQUEST IGNORED")
        return alice_response(
            payload,
            session_data.get("last_response_text", "Хорошо"),
            end_session=session_data.get("last_end_session", False),
            session_state={"stage": "awaiting_items"} if not session_data.get("last_end_session", False) else None,
        )

    items = session_data.get("items", [])
    telegram_message_id = session_data.get("telegram_message_id")

    user_text = extract_user_text(payload)
    normalized_text = user_text.lower().strip()

    if normalized_text in FINISH_WORDS:
        if not items:
            ACTIVE_SESSIONS.pop(session_id, None)
            return alice_response(
                payload,
                "Список пустой. Закрываю",
                end_session=True,
            )

        ACTIVE_SESSIONS.pop(session_id, None)
        return alice_response(
            payload,
            "Готово. Список сохранен",
            end_session=True,
        )

    if not user_text:
        response_text = "Я не расслышала. Скажите, что добавить, или скажите завершить"
        session_data["last_processed_message_id"] = message_id
        session_data["last_response_text"] = response_text
        session_data["last_end_session"] = False
        return alice_response(
            payload,
            response_text,
            session_state={"stage": "awaiting_items"},
        )

    new_items = parse_items(user_text)
    log(f"PARSED ITEMS: {new_items}")

    if not new_items:
        response_text = "Не поняла, что добавить. Скажите товар еще раз"
        session_data["last_processed_message_id"] = message_id
        session_data["last_response_text"] = response_text
        session_data["last_end_session"] = False
        return alice_response(
            payload,
            response_text,
            session_state={"stage": "awaiting_items"},
        )

    items = items + new_items

    try:
        telegram_message_id = await upsert_telegram_list(items, telegram_message_id)
    except Exception as exc:
        log(f"FINAL TELEGRAM ERROR: {repr(exc)}")

        response_text = "Не получилось обновить список в Телеграм. Попробуйте еще раз"
        session_data["items"] = items
        session_data["telegram_message_id"] = telegram_message_id
        session_data["last_processed_message_id"] = message_id
        session_data["last_response_text"] = response_text
        session_data["last_end_session"] = False
        return alice_response(
            payload,
            response_text,
            end_session=False,
            session_state={"stage": "awaiting_items"},
        )

    added_text = ", ".join(new_items)
    response_text = f"Добавила: {added_text}. Говорите еще или скажите завершить"

    session_data["items"] = items
    session_data["telegram_message_id"] = telegram_message_id
    session_data["last_processed_message_id"] = message_id
    session_data["last_response_text"] = response_text
    session_data["last_end_session"] = False

    log(f"SESSION UPDATED: items={items}, telegram_message_id={telegram_message_id}")

    return alice_response(
        payload,
        response_text,
        session_state={"stage": "awaiting_items"},
    )
