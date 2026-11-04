import os

from app import authenticate_user, register_user, get_upload_folder, get_database_path


def test_register_and_login_user():
    username = "demo_user_auth"
    password = "StrongPass123!"

    assert register_user(username, password) is True
    assert authenticate_user(username, password) is True
    assert authenticate_user(username, "wrongpassword") is False


def test_deployment_paths_use_writable_directory(monkeypatch):
    temp_dir = os.path.join(os.getcwd(), "tmp_deploy_checks")
    monkeypatch.setenv("UPLOAD_FOLDER", temp_dir)
    monkeypatch.setenv("DATABASE_PATH", os.path.join(temp_dir, "resume_history.db"))

    assert get_upload_folder() == temp_dir
    assert get_database_path() == os.path.join(temp_dir, "resume_history.db")
