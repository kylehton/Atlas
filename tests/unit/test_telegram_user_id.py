from atlas.telegram.user_id import extract_latest_private_sender


def test_extract_latest_private_sender_ignores_groups_and_bots() -> None:
    sender = extract_latest_private_sender(
        [
            {
                "update_id": 10,
                "message": {
                    "chat": {"type": "private"},
                    "from": {"id": 100, "is_bot": False, "first_name": "Old"},
                },
            },
            {
                "update_id": 11,
                "message": {
                    "chat": {"type": "group"},
                    "from": {"id": 200, "is_bot": False, "first_name": "Group"},
                },
            },
            {
                "update_id": 12,
                "message": {
                    "chat": {"type": "private"},
                    "from": {"id": 300, "is_bot": True, "first_name": "Bot"},
                },
            },
            {
                "update_id": 13,
                "message": {
                    "chat": {"type": "private"},
                    "from": {
                        "id": 400,
                        "is_bot": False,
                        "first_name": "Kyle",
                        "last_name": "Tester",
                        "username": "kyle",
                    },
                },
            },
        ],
        after_update_id=10,
    )

    assert sender is not None
    assert sender.user_id == 400
    assert sender.display_name == "Kyle Tester"
    assert sender.username == "kyle"
