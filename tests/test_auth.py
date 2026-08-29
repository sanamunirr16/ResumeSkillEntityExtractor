from app import authenticate_user, register_user


def test_register_and_login_user():
    username = "demo_user_auth"
    password = "StrongPass123!"

    assert register_user(username, password) is True
    assert authenticate_user(username, password) is True
    assert authenticate_user(username, "wrongpassword") is False
