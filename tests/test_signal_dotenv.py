import os

from signal_engine.dotenv import load_dotenv


def test_missing_file_is_a_silent_noop(tmp_path):
    load_dotenv(str(tmp_path / "does-not-exist.env"))  # must not raise


def test_loads_simple_key_value_pairs(tmp_path, monkeypatch):
    monkeypatch.delenv("SIGNAL_TEST_VAR", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("SIGNAL_TEST_VAR=hello\n")

    load_dotenv(str(env_file))

    assert os.environ["SIGNAL_TEST_VAR"] == "hello"


def test_skips_comments_and_blank_lines(tmp_path, monkeypatch):
    monkeypatch.delenv("SIGNAL_TEST_A", raising=False)
    monkeypatch.delenv("SIGNAL_TEST_B", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("# a comment\n\nSIGNAL_TEST_A=1\n   \nSIGNAL_TEST_B=2\n")

    load_dotenv(str(env_file))

    assert os.environ["SIGNAL_TEST_A"] == "1"
    assert os.environ["SIGNAL_TEST_B"] == "2"


def test_strips_surrounding_quotes(tmp_path, monkeypatch):
    monkeypatch.delenv("SIGNAL_TEST_QUOTED", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text('SIGNAL_TEST_QUOTED="quoted value"\n')

    load_dotenv(str(env_file))

    assert os.environ["SIGNAL_TEST_QUOTED"] == "quoted value"


def test_real_environment_variable_is_never_overridden(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNAL_TEST_OVERRIDE", "from_shell")
    env_file = tmp_path / ".env"
    env_file.write_text("SIGNAL_TEST_OVERRIDE=from_file\n")

    load_dotenv(str(env_file))

    assert os.environ["SIGNAL_TEST_OVERRIDE"] == "from_shell"


def test_line_without_equals_sign_is_ignored(tmp_path, monkeypatch):
    monkeypatch.delenv("NOT_A_VALID_LINE", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("this line has no equals sign\n")

    load_dotenv(str(env_file))  # must not raise

    assert "NOT_A_VALID_LINE" not in os.environ
