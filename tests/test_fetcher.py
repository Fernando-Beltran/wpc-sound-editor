"""Spec for the HTTP client's configurable headers."""

import json

import pytest

from wpcaudio import fetcher


def test_the_defaults_identify_the_program():
    assert "WPCSoundEditor" in fetcher.settings("no-existe.json")["user_agent"]
    assert fetcher.settings("no-existe.json")["timeout"] > 0


def test_a_settings_file_overrides_the_defaults(tmp_path):
    path = tmp_path / "ajustes.json"
    path.write_text(json.dumps({"user_agent": "El mio/1.0", "timeout": 5}),
                    encoding="utf-8")

    values = fetcher.settings(path)
    assert values["user_agent"] == "El mio/1.0"
    assert values["timeout"] == 5
    # Anything not mentioned keeps its default.
    assert values["max_bytes"] == fetcher.DEFAULTS["max_bytes"]


def test_unknown_keys_are_not_stored(tmp_path):
    path = tmp_path / "ajustes.json"

    saved = fetcher.save_settings({"user_agent": "X/1", "cualquiera": 1}, path)

    assert saved["user_agent"] == "X/1"
    assert "cualquiera" not in saved
    assert "cualquiera" not in json.loads(path.read_text(encoding="utf-8"))


def test_saving_keeps_what_was_already_there(tmp_path):
    path = tmp_path / "ajustes.json"
    fetcher.save_settings({"user_agent": "X/1"}, path)

    fetcher.save_settings({"timeout": 3}, path)

    assert fetcher.settings(path)["user_agent"] == "X/1"
    assert fetcher.settings(path)["timeout"] == 3


def test_only_http_urls_are_fetched():
    with pytest.raises(fetcher.FetchError):
        fetcher.fetch("file:///etc/passwd")
    with pytest.raises(fetcher.FetchError):
        fetcher.fetch("javascript:alert(1)")


def test_the_configured_headers_are_the_ones_sent(tmp_path, monkeypatch):
    path = tmp_path / "ajustes.json"
    fetcher.save_settings({"user_agent": "Prueba/9", "referer": "https://ejemplo"}, path)
    seen = {}

    class Answer:
        headers = type("H", (), {"get_content_type": staticmethod(lambda: "image/png")})()

        def read(self, _):
            return b"datos"

        def __enter__(self):
            return self

        def __exit__(self, *ignored):
            return False

    def fake_open(request, timeout=None):
        seen["ua"] = request.get_header("User-agent")
        seen["referer"] = request.get_header("Referer")
        seen["timeout"] = timeout
        return Answer()

    monkeypatch.setattr(fetcher.urllib.request, "urlopen", fake_open)
    payload, kind = fetcher.fetch("https://ejemplo/x.png", path)

    assert payload == b"datos" and kind == "image/png"
    assert seen["ua"] == "Prueba/9"
    assert seen["referer"] == "https://ejemplo"
