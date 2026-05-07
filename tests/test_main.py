import asyncio

import main


def make_payload(text="", *, new=False, message_id=1, session_id="session-1", application_id="alice-app"):
    return main.AliceRequest(
        request={
            "command": text,
            "original_utterance": text,
        },
        session={
            "new": new,
            "session_id": session_id,
            "message_id": message_id,
            "application": {
                "application_id": application_id,
            },
        },
        version="1.0",
    )


def run_webhook(payload):
    return asyncio.run(main.webhook(payload))


def test_parse_items_handles_fillers_lists_quantities_and_negative_phrases():
    assert main.parse_items("Алиса, добавь молоко, хлеб и яйца") == ["молоко", "хлеб", "яйца"]
    assert main.parse_items("молоко хлеб яйца") == ["молоко", "хлеб", "яйца"]
    assert main.parse_items("добавь два литра молока и батон") == ["два литра молока", "батон"]
    assert main.parse_items("не добавляй хлеб, добавь молоко") == ["молоко"]


def test_parse_items_keeps_adjective_product_names_together():
    assert main.parse_items("оливковое масло") == ["оливковое масло"]
    assert main.parse_items("добавь крабовые палочки") == ["крабовые палочки"]
    assert main.parse_items("оливковое масло клубнику кабачки") == ["оливковое масло", "клубнику", "кабачки"]


def test_build_telegram_message_uses_markdown_and_escapes_items(monkeypatch):
    monkeypatch.setattr(main, "now_datetime_string", lambda: "08.05.2026 12:34")

    message = main.build_telegram_message(["чай (зелёный)", "сыр 45% + хлеб"])

    assert message == (
        "*Список покупок*\n"
        "_Обновлено: 08\\.05\\.2026 12:34_\n"
        "\n"
        "• чай \\(зелёный\\)\n"
        "• сыр 45% \\+ хлеб"
    )


def test_build_telegram_message_formats_empty_list(monkeypatch):
    monkeypatch.setattr(main, "now_datetime_string", lambda: "08.05.2026 12:34")

    message = main.build_telegram_message([])

    assert message == "*Список покупок*\n_Обновлено: 08\\.05\\.2026 12:34_\n\n• пусто"


def test_upsert_telegram_list_sends_markdown_parse_mode(monkeypatch):
    monkeypatch.setattr(main, "TELEGRAM_CHAT_ID", "chat-1")
    calls = []

    async def fake_telegram_api_call(method, payload):
        calls.append((method, payload))
        return {"result": {"message_id": 42}}

    monkeypatch.setattr(main, "telegram_api_call", fake_telegram_api_call)

    message_id = asyncio.run(main.upsert_telegram_list(["молоко"], None))

    assert message_id == 42
    assert calls[0][0] == "sendMessage"
    assert calls[0][1]["parse_mode"] == "MarkdownV2"


def test_view_list_command_reads_current_items():
    main.ACTIVE_SESSIONS.clear()
    main.ACTIVE_SESSIONS["session-1"] = {
        "items": ["молоко", "хлеб"],
        "telegram_message_id": 10,
        "last_processed_message_id": None,
        "last_response_text": "",
        "last_end_session": False,
    }

    response = run_webhook(make_payload("что в списке", message_id=2))

    assert response["response"]["text"] == "В списке сейчас: молоко, хлеб"
    assert response["response"]["end_session"] is False


def test_natural_view_list_command_reads_current_items():
    main.ACTIVE_SESSIONS.clear()
    main.ACTIVE_SESSIONS["session-1"] = {
        "items": ["молоко", "хлеб"],
        "telegram_message_id": 10,
        "last_processed_message_id": None,
        "last_response_text": "",
        "last_end_session": False,
    }

    response = run_webhook(make_payload("что я уже добавил", message_id=2))

    assert response["response"]["text"] == "В списке сейчас: молоко, хлеб"


def test_delete_item_updates_session_and_telegram(monkeypatch):
    main.ACTIVE_SESSIONS.clear()
    main.ACTIVE_SESSIONS["session-1"] = {
        "items": ["молоко", "хлеб", "яйца"],
        "telegram_message_id": 10,
        "last_processed_message_id": None,
        "last_response_text": "",
        "last_end_session": False,
    }
    calls = []

    async def fake_upsert(items, message_id):
        calls.append((items, message_id))
        return message_id

    monkeypatch.setattr(main, "upsert_telegram_list", fake_upsert)

    response = run_webhook(make_payload("убери хлеб из списка", message_id=2))

    assert response["response"]["text"] == "Убрала: хлеб"
    assert main.ACTIVE_SESSIONS["session-1"]["items"] == ["молоко", "яйца"]
    assert calls == [(["молоко", "яйца"], 10)]


def test_delete_item_with_natural_word_order(monkeypatch):
    main.ACTIVE_SESSIONS.clear()
    main.ACTIVE_SESSIONS["session-1"] = {
        "items": ["молоко", "хлеб"],
        "telegram_message_id": 10,
        "last_processed_message_id": None,
        "last_response_text": "",
        "last_end_session": False,
    }

    async def fake_upsert(items, message_id):
        return message_id

    monkeypatch.setattr(main, "upsert_telegram_list", fake_upsert)

    response = run_webhook(make_payload("убери из покупок молоко", message_id=2))

    assert response["response"]["text"] == "Убрала: молоко"
    assert main.ACTIVE_SESSIONS["session-1"]["items"] == ["хлеб"]


def test_delete_item_when_command_goes_after_item(monkeypatch):
    main.ACTIVE_SESSIONS.clear()
    main.ACTIVE_SESSIONS["session-1"] = {
        "items": ["молоко", "хлеб"],
        "telegram_message_id": 10,
        "last_processed_message_id": None,
        "last_response_text": "",
        "last_end_session": False,
    }

    async def fake_upsert(items, message_id):
        return message_id

    monkeypatch.setattr(main, "upsert_telegram_list", fake_upsert)

    response = run_webhook(make_payload("молоко убери из списка", message_id=2))

    assert response["response"]["text"] == "Убрала: молоко"
    assert main.ACTIVE_SESSIONS["session-1"]["items"] == ["хлеб"]


def test_delete_item_by_partial_name(monkeypatch):
    main.ACTIVE_SESSIONS.clear()
    main.ACTIVE_SESSIONS["session-1"] = {
        "items": ["молоко", "кукурузные палочки"],
        "telegram_message_id": 10,
        "last_processed_message_id": None,
        "last_response_text": "",
        "last_end_session": False,
    }

    async def fake_upsert(items, message_id):
        return message_id

    monkeypatch.setattr(main, "upsert_telegram_list", fake_upsert)

    response = run_webhook(make_payload("удали палочки", message_id=2))

    assert response["response"]["text"] == "Убрала: кукурузные палочки"
    assert main.ACTIVE_SESSIONS["session-1"]["items"] == ["молоко"]


def test_delete_item_by_typo(monkeypatch):
    main.ACTIVE_SESSIONS.clear()
    main.ACTIVE_SESSIONS["session-1"] = {
        "items": ["кукурузные палочки"],
        "telegram_message_id": 10,
        "last_processed_message_id": None,
        "last_response_text": "",
        "last_end_session": False,
    }

    async def fake_upsert(items, message_id):
        return message_id

    monkeypatch.setattr(main, "upsert_telegram_list", fake_upsert)

    response = run_webhook(make_payload("удали кукурзные палочки", message_id=2))

    assert response["response"]["text"] == "Убрала: кукурузные палочки"
    assert main.ACTIVE_SESSIONS["session-1"]["items"] == []


def test_delete_item_asks_for_clarification_when_partial_name_is_ambiguous(monkeypatch):
    main.ACTIVE_SESSIONS.clear()
    main.ACTIVE_SESSIONS["session-1"] = {
        "items": ["кукурузные палочки", "крабовые палочки"],
        "telegram_message_id": 10,
        "last_processed_message_id": None,
        "last_response_text": "",
        "last_end_session": False,
    }

    async def fail_upsert(items, message_id):
        raise AssertionError("ambiguous delete should not update Telegram")

    monkeypatch.setattr(main, "upsert_telegram_list", fail_upsert)

    response = run_webhook(make_payload("удали палочки", message_id=2))

    assert response["response"]["text"] == (
        "Нашла несколько похожих товаров: кукурузные палочки, крабовые палочки. Назовите точнее"
    )
    assert main.ACTIVE_SESSIONS["session-1"]["items"] == ["кукурузные палочки", "крабовые палочки"]


def test_clear_list_updates_existing_telegram_message(monkeypatch):
    main.ACTIVE_SESSIONS.clear()
    main.ACTIVE_SESSIONS["session-1"] = {
        "items": ["молоко"],
        "telegram_message_id": 10,
        "last_processed_message_id": None,
        "last_response_text": "",
        "last_end_session": False,
    }
    calls = []

    async def fake_upsert(items, message_id):
        calls.append((items, message_id))
        return message_id

    monkeypatch.setattr(main, "upsert_telegram_list", fake_upsert)

    response = run_webhook(make_payload("очисти список", message_id=2))

    assert response["response"]["text"] == "Очистила список. Можно начинать заново"
    assert main.ACTIVE_SESSIONS["session-1"]["items"] == []
    assert calls == [([], 10)]


def test_natural_clear_list_command(monkeypatch):
    main.ACTIVE_SESSIONS.clear()
    main.ACTIVE_SESSIONS["session-1"] = {
        "items": ["молоко"],
        "telegram_message_id": 10,
        "last_processed_message_id": None,
        "last_response_text": "",
        "last_end_session": False,
    }

    async def fake_upsert(items, message_id):
        return message_id

    monkeypatch.setattr(main, "upsert_telegram_list", fake_upsert)

    response = run_webhook(make_payload("сотри всё", message_id=2))

    assert response["response"]["text"] == "Очистила список. Можно начинать заново"
    assert main.ACTIVE_SESSIONS["session-1"]["items"] == []


def test_natural_undo_last_command(monkeypatch):
    main.ACTIVE_SESSIONS.clear()
    main.ACTIVE_SESSIONS["session-1"] = {
        "items": ["молоко", "хлеб"],
        "telegram_message_id": 10,
        "last_processed_message_id": None,
        "last_response_text": "",
        "last_end_session": False,
    }

    async def fake_upsert(items, message_id):
        return message_id

    monkeypatch.setattr(main, "upsert_telegram_list", fake_upsert)

    response = run_webhook(make_payload("последний пункт убери", message_id=2))

    assert response["response"]["text"] == "Убрала последнее: хлеб"
    assert main.ACTIVE_SESSIONS["session-1"]["items"] == ["молоко"]


def test_duplicate_request_returns_previous_response_without_updating_telegram(monkeypatch):
    main.ACTIVE_SESSIONS.clear()
    main.ACTIVE_SESSIONS["session-1"] = {
        "items": ["молоко"],
        "telegram_message_id": 10,
        "last_processed_message_id": 2,
        "last_response_text": "Записала: молоко. Что еще добавить?",
        "last_end_session": False,
    }

    async def fail_upsert(items, message_id):
        raise AssertionError("duplicate request should not update Telegram")

    monkeypatch.setattr(main, "upsert_telegram_list", fail_upsert)

    response = run_webhook(make_payload("молоко", message_id=2))

    assert response["response"]["text"] == "Записала: молоко. Что еще добавить?"


def test_application_id_check_rejects_foreign_skill(monkeypatch):
    monkeypatch.setattr(main, "ALICE_APPLICATION_ID", "expected-app")

    response = run_webhook(make_payload("молоко", application_id="foreign-app"))

    assert response["response"]["text"] == "Этот адрес не подключен к вашему навыку"
    assert response["response"]["end_session"] is True
