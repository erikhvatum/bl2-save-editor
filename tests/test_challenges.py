"""
Regression tests for challenge handling.

Field 38 is the challenge registry ({1: name, 2: is_from_dlc, 3: dlc_id});
sub-fields 2/3 are read-only. Challenge progress lives in the StatsData blob
(field 15). Rewriting field 38 metadata caused ECHO recorder and boss-kill
records to be lost in-game.
"""
import os
import sys
import shutil
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import save_io
import bl2_stats
from borderlands.savefile import BaseApp
from borderlands.datautil.protobuf import read_protobuf, write_protobuf

FIXTURE_SAVE = os.path.join(os.path.dirname(__file__), "Save0001.sav")
KNOWN_ID = 1633      # GD_Challenges.Weapons.Pistol_Kills
UNKNOWN_ID = 4242    # stands in for ECHO / boss / area stats


def _entry(sid, total, prev=0):
    return {"id": sid, "first_one": 1, "total_value": total, "second_one": 1, "previous_value": prev}


def _synthetic_stats():
    return {"unknown": 4, "entries": [_entry(KNOWN_ID, 7), _entry(UNKNOWN_ID, 3), _entry(1621, 50000, 100)]}


def _decode(path):
    with open(path, "rb") as f:
        return read_protobuf(BaseApp.unwrap_player_data(f.read()))


def _pb_bytes(path):
    with open(path, "rb") as f:
        return BaseApp.unwrap_player_data(f.read())


@pytest.fixture
def save_copy(tmp_path):
    if not os.path.exists(FIXTURE_SAVE):
        pytest.skip("Test fixture Save0001.sav not found")
    dst = tmp_path / "Save0001.sav"
    shutil.copy2(FIXTURE_SAVE, dst)
    return str(dst)


class TestStatsCodec:
    def test_synthetic_round_trip(self):
        stats = _synthetic_stats()
        packed = bl2_stats.pack_stats(stats)
        assert len(packed) == 10 + 12 * 3
        assert bl2_stats.unpack_stats(packed) == stats
        assert bl2_stats.pack_stats(bl2_stats.unpack_stats(packed)) == packed

    def test_bad_size_rejected(self):
        packed = bl2_stats.pack_stats(_synthetic_stats())
        with pytest.raises(bl2_stats.StatsFormatError):
            bl2_stats.unpack_stats(packed[:-1])

    def test_current_value_respects_previous(self):
        e = _entry(1621, 500, 200)
        assert bl2_stats.current_value(e) == 300
        bl2_stats.set_current_value(e, 10)
        assert e["total_value"] == 210 and e["previous_value"] == 200

    def test_defs_loaded(self):
        defs = bl2_stats.challenge_defs()
        assert defs[KNOWN_ID]["id_text"] == "GD_Challenges.Weapons.Pistol_Kills"
        assert UNKNOWN_ID not in defs

    def test_complete_all_only_touches_known(self):
        player = {15: [[2, bl2_stats.pack_stats(_synthetic_stats())]]}
        n = save_io._complete_all_in_player(player)
        out = {e["id"]: e for e in bl2_stats.unpack_stats(player[15][0][1])["entries"]}
        assert n == 2
        assert bl2_stats.current_value(out[KNOWN_ID]) == bl2_stats.challenge_defs()[KNOWN_ID]["levels"][-1]
        assert out[UNKNOWN_ID] == _entry(UNKNOWN_ID, 3)


class TestFixtureRoundTrip:
    def test_plain_round_trip_identical(self, save_copy):
        before = _pb_bytes(save_copy)
        _, player = save_io.read_save(save_copy)
        save_io.write_save(save_copy, player)
        assert _pb_bytes(save_copy) == before

    def test_field15_decodes_and_round_trips(self, save_copy):
        blob = bytes(_decode(save_copy)[15][0][1])
        assert bl2_stats.pack_stats(bl2_stats.unpack_stats(blob)) == blob

    def test_complete_all_leaves_field38(self, save_copy):
        orig = _decode(save_copy)
        n = save_io.complete_all_challenges(save_copy)
        new = _decode(save_copy)
        assert n > 0
        assert new[38] == orig[38]
        old_e = bl2_stats.unpack_stats(bytes(orig[15][0][1]))["entries"]
        new_e = bl2_stats.unpack_stats(bytes(new[15][0][1]))["entries"]
        assert [e["id"] for e in old_e] == [e["id"] for e in new_e]
        defs = bl2_stats.challenge_defs()
        for a, b in zip(old_e, new_e):
            if a["id"] not in defs:
                assert a == b  # unknown (ECHO/boss/etc.) stats untouched
        for k in set(orig) | set(new):
            if k != 15:
                assert orig.get(k) == new.get(k), f"field {k} changed"

    def test_reset_all_leaves_field38(self, save_copy):
        orig = _decode(save_copy)
        save_io.reset_all_challenges(save_copy)
        new = _decode(save_copy)
        assert new[38] == orig[38]
        defs = bl2_stats.challenge_defs()
        for a, b in zip(bl2_stats.unpack_stats(bytes(orig[15][0][1]))["entries"],
                        bl2_stats.unpack_stats(bytes(new[15][0][1]))["entries"]):
            assert a["id"] == b["id"] and a["total_value"] == b["total_value"]
            if a["id"] in defs:
                assert bl2_stats.current_value(b) == 0
            else:
                assert a == b

    def test_unlock_achievements_leaves_field38(self, save_copy):
        orig = _decode(save_copy)
        save_io.unlock_achievements(save_copy)
        assert _decode(save_copy)[38] == orig[38]

    def test_set_progress(self, save_copy):
        info = save_io.extract_challenges(_decode(save_copy))
        ch = next(c for cat in info["categories"].values() for c in cat)
        assert save_io.set_challenge_progress(save_copy, ch["path"], 12345)
        info2 = save_io.extract_challenges(_decode(save_copy))
        ch2 = next(c for cat in info2["categories"].values() for c in cat if c["id"] == ch["id"])
        assert ch2["progress"] == 12345


class TestRegistryGuard:
    def test_guard_blocks_field38_metadata_change(self, save_copy):
        _, player = save_io.read_save(save_copy)
        if not player.get(38):
            pytest.skip("fixture has no field 38")
        before = _pb_bytes(save_copy)
        sub = read_protobuf(player[38][0][1])
        sub[2] = [[0, 99999]]
        sub[3] = [[0, 1]]
        player[38][0][1] = write_protobuf(sub)
        with pytest.raises(ValueError, match="field 38"):
            save_io.write_save(save_copy, player)
        assert _pb_bytes(save_copy) == before

    def test_extract_reports_registry_read_only(self, save_copy):
        info = save_io.extract_challenges(_decode(save_copy))
        assert info["registry"]["read_only"] is True
        assert info["error"] is None
