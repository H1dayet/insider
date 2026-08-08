"""Runnable check for _latest_message() - the selection logic behind --find-chat-id.
Everything else in notify.py is a direct network call, not worth mocking here."""
from insider.notify import _latest_message


def test_returns_none_when_no_messages():
    assert _latest_message([]) is None
    assert _latest_message([{"update_id": 1}]) is None  # e.g. an edited_message update, no "message" key


def test_returns_the_most_recent_message():
    updates = [
        {"update_id": 1, "message": {"message_id": 10, "chat": {"id": -100, "type": "group"}}},
        {"update_id": 2, "message": {"message_id": 11, "chat": {"id": -100, "type": "group"}}},
    ]
    assert _latest_message(updates)["message_id"] == 11


if __name__ == "__main__":
    test_returns_none_when_no_messages()
    test_returns_the_most_recent_message()
    print("ok")
