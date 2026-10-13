"""Spec for identifying a sound ROM set from the images themselves."""

from wpcaudio import games
import pytest

from wpcaudio.games import GAMES, identify, verify


def _tz_images():
    """Stand-ins with the right sizes; identification is by CRC32."""
    return {"u18": bytes(0x80000), "u15": bytes(0x80000), "u14": bytes(0x20000)}


def test_a_known_set_is_identified_by_its_hashes(monkeypatch):
    images = _tz_images()
    # Pin the fixture's own CRCs onto the Twilight Zone entry so the
    # test exercises the matching, not the particular numbers.
    import zlib
    crcs = {socket: zlib.crc32(data) & 0xFFFFFFFF for socket, data in images.items()}
    monkeypatch.setitem(GAMES["tz_l2"], "roms", crcs)

    game = identify(images)

    assert game is not None
    assert game.key == "tz_l2"


def test_an_unknown_set_identifies_as_nothing():
    assert identify({"u18": b"\x00" * 16}) is None


def test_identification_needs_every_socket_the_game_lists(monkeypatch):
    import zlib
    images = _tz_images()
    crcs = {socket: zlib.crc32(data) & 0xFFFFFFFF for socket, data in images.items()}
    monkeypatch.setitem(GAMES["tz_l2"], "roms", crcs)

    # U14 missing: a partial set must not pass as the whole game.
    assert identify({"u18": images["u18"], "u15": images["u15"]}) is None


def test_verify_reports_each_socket():
    report = verify({"u18": b"wrong"}, "tz_l2")

    assert report["u18"].ok is False
    assert report["u18"].expected == GAMES["tz_l2"]["roms"]["u18"]
    # Sockets the game expects but the user did not supply are flagged too.
    assert report["u15"].found is None


def test_verifying_an_unknown_game_is_refused():
    with pytest.raises(KeyError):
        verify({}, "no_existe")


def test_the_registry_describes_twilight_zone():
    game = GAMES["tz_l2"]

    assert "Twilight Zone" in game["name"]
    assert set(game["roms"]) == {"u18", "u15", "u14"}
    assert game["sizes"] == {"u18": 0x80000, "u15": 0x80000, "u14": 0x20000}


def test_the_search_url_is_ipdbs_own_quick_search():
    from wpcaudio.games import ipdb_search_url

    url = ipdb_search_url("twilight zone")
    assert url.startswith("https://www.ipdb.org/search.pl?any=twilight+zone")
    assert "searchtype=quick" in url


def test_known_games_can_be_searched_by_name():
    from wpcaudio.games import search

    found = search("twilight")
    assert {game["key"] for game in found} == {"tz_l1", "tz_l2"}
    assert all(game["url"].startswith("https://www.ipdb.org/machine.cgi?id=")
               for game in found)


def test_an_empty_search_lists_everything():
    from wpcaudio.games import search

    assert len(search("")) == len(GAMES)


def test_the_flyer_link_lands_on_the_right_letter():
    from wpcaudio.games import flyer_url

    assert flyer_url("tz_l2").endswith("/pinballs/gallery/T")



def test_a_dcs_set_names_its_platform_and_romset_files():
    game = games._as_game("sttng_l1")

    assert game.platform == "dcs" and game.pinmame == "sttng_l7"
    assert game.files["u2"] == "ng_u2_s.l1" and sorted(game.roms) == sorted(game.files)


def test_pre_dcs_games_default_to_their_platform():
    assert games._as_game("tz_l2").platform == games.PRE_DCS


def test_the_candidate_follows_the_sockets_supplied():
    assert games.candidate({}).key == next(iter(games.GAMES))
    assert games.candidate({"u2": b"x"}).platform == "dcs"
    assert games.candidate({"u18": b"x"}).platform == games.PRE_DCS


def test_with_nothing_loaded_the_board_named_decides_the_sockets():
    assert games.candidate({}, "dcs").platform == "dcs"
    assert games.candidate({}, games.PRE_DCS).platform == games.PRE_DCS
    # an image always outweighs what the user said
    assert games.candidate({"u18": b"x"}, "dcs").platform == games.PRE_DCS
