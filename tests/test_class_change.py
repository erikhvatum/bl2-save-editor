"""Synthetic regression fixtures: never depend on or write a user's save."""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import save_io
import bl2_stats
import app as flask_app

CLASSES = list(save_io.CLASS_NAMES)


def player_for(char_class=CLASSES[0], points=7):
    paths = list(save_io._class_skill_defs(char_class))
    stats = {"unknown": 4, "entries": [
        {"id": 4242, "first_one": 1, "total_value": 17,
         "second_one": 1, "previous_value": 2}]}
    return {
        1: [[2, char_class]], 2: [[0, 20]], 4: [[0, points]],
        8: [save_io._skill_entry(paths[0], 1), save_io._skill_entry(paths[1], 5),
            save_io._skill_entry("GD_Global.SomeSkill", 2)],
        15: [[2, bl2_stats.pack_stats(stats)]],
        35: [[2, b"old.head"], [2, b"vehicle.one"], [2, b"vehicle.two"],
             [2, b"vehicle.three"], [2, b"old.skin"], [2, b"future.cosmetic"]],
        38: [[2, save_io.write_protobuf({1: [[2, b"DLC.challenge"]],
                                       2: [[0, 1]], 3: [[0, 8]]})]],
        99: [[2, b"unknown future data"]],
    }


@pytest.fixture
def synthetic_save(tmp_path, monkeypatch):
    monkeypatch.setattr(save_io, 'SAVE_DIR', str(tmp_path))
    monkeypatch.setattr(flask_app, '_is_game_running', lambda: False)
    save_io.write_save('Save0001.sav', player_for())
    return tmp_path / 'Save0001.sav'


@pytest.mark.parametrize('source', CLASSES)
@pytest.mark.parametrize('target', CLASSES)
def test_all_class_pairs(source, target):
    player = player_for(source)
    # Opaque inventory/class mods must remain byte-for-byte, equipped or not.
    player[53] = [[2, b'opaque equipped class mod']]
    player[54] = [[2, b'opaque weapon']]
    player[41] = [[2, b'opaque bank item']]
    original = copy.deepcopy(player)
    refund = save_io._change_class_in_player(player, target)
    if source == target:
        assert refund == 0
        assert player == original
        return
    assert refund == 6  # five purchased ranks PLUS the one-point action skill
    assert player[4] == [[0, 13]]
    assert player[1] == [[2, target]]
    skills = save_io.extract_skills(player)
    expected = save_io._class_skill_defs(target)
    assert all(skills[path] == 0 for path in expected)
    assert set(skills) == set(expected) | {'GD_Global.SomeSkill'}
    assert skills['GD_Global.SomeSkill'] == 2
    for field in original.keys() - {1, 4, 8, 35}:
        assert player[field] == original[field], f'field {field} changed'
    assert player[35][1:4] == original[35][1:4]
    assert player[35][5:] == original[35][5:]
    prefix = save_io.CLASS_DEFAULT_CUSTOMS[target].encode()
    assert player[35][0] == [2, prefix + b'.Head_Default']
    assert player[35][4] == [2, prefix + b'.Skin_Default']
    for entry in player[8][1:]:
        sub = save_io.read_protobuf(entry[1])
        assert sub[3] == sub[4] == [[0, 0]]


def test_round_trip_and_no_point_minting(synthetic_save):
    _, before = save_io.read_save(str(synthetic_save))
    result = save_io.change_character_class(str(synthetic_save), 'Gaige')
    assert result['refunded_points'] == 6
    assert result['character']['skill_points'] == 13
    assert any('DLC' in w for w in result['warnings'])
    _, after = save_io.read_save(str(synthetic_save))
    for field in before.keys() - {1, 4, 8, 35}:
        assert before[field] == after[field]
    assert all(v == 0 for k, v in result['character']['skills'].items() if k != 'GD_Global.SomeSkill')
    result = save_io.change_character_class(str(synthetic_save), 'Krieg')
    assert result['refunded_points'] == 0
    assert result['character']['skill_points'] == 13
    body = synthetic_save.read_bytes()
    assert save_io.change_character_class(str(synthetic_save), 'Krieg')['changed'] is False
    assert synthetic_save.read_bytes() == body


def test_allocate_and_refund_new_skills(synthetic_save):
    save_io.change_character_class(str(synthetic_save), 'Maya')
    action, passive, *_ = save_io._class_skill_defs(CLASSES[2])
    assert save_io.set_skills(str(synthetic_save), {action: 1, passive: 5}) == 2
    _, p = save_io.read_save(str(synthetic_save))
    assert p[4] == [[0, 7]]
    assert save_io.extract_skills(p)[action] == 1
    assert save_io.set_skills(str(synthetic_save), {action: 0, passive: 0}) == 2
    _, p = save_io.read_save(str(synthetic_save))
    assert p[4] == [[0, 13]]


@pytest.mark.parametrize('target', ['', 'Soldier', 'not a class', None, 42, {}])
def test_invalid_target_is_no_write(synthetic_save, target):
    original = synthetic_save.read_bytes()
    with pytest.raises(ValueError):
        save_io.change_character_class(str(synthetic_save), target)
    assert synthetic_save.read_bytes() == original


def test_missing_fields_and_unpurchased_action():
    p = {1: [[2, CLASSES[0]]], 2: [[0, 1]]}
    assert save_io._change_class_in_player(p, CLASSES[1]) == 0
    assert p[4] == [[0, 0]]
    assert all(v == 0 for v in save_io.extract_skills(p).values())
    assert len(p[35]) == 5
    assert p[35][1:4] == [[2, b'0']] * 3


def test_mixed_class_save_rejected_without_mutating():
    p = player_for()
    p[8].append(save_io._skill_entry(next(iter(save_io._class_skill_defs(CLASSES[1]))), 1))
    original = copy.deepcopy(p)
    with pytest.raises(ValueError, match='another class'):
        save_io._change_class_in_player(p, CLASSES[2])
    assert p == original


def test_unsupported_source_rejected():
    p = {1: [[2, b'unknown']]}
    with pytest.raises(ValueError, match='source class'):
        save_io._change_class_in_player(p, CLASSES[1])


@pytest.mark.parametrize('updates', [
    {'bogus.skill': 1}, {'GD_Soldier_Skills.Guerrilla.Sentry': 6},
    {'GD_Soldier_Skills.Guerrilla.Sentry': -1},
    {'GD_Soldier_Skills.Guerrilla.Sentry': 1.5},
    {'GD_Soldier_Skills.Guerrilla.Sentry': True}, [],
])
def test_invalid_skill_updates_do_not_write(synthetic_save, updates):
    original = synthetic_save.read_bytes()
    with pytest.raises(ValueError):
        save_io.set_skills(str(synthetic_save), updates)
    assert synthetic_save.read_bytes() == original


def test_insufficient_points_do_not_write(synthetic_save):
    _, p = save_io.read_save(str(synthetic_save))
    p[4] = [[0, 0]]
    save_io.write_save(str(synthetic_save), p)
    original = synthetic_save.read_bytes()
    with pytest.raises(ValueError, match='Not enough'):
        save_io.set_skills(str(synthetic_save), {'GD_Soldier_Skills.Guerrilla.Ready': 1})
    assert synthetic_save.read_bytes() == original


def test_case_insensitive_engine_paths(synthetic_save):
    _, p = save_io.read_save(str(synthetic_save))
    sub = save_io.read_protobuf(p[8][1][1])
    sub[1][0][1] = sub[1][0][1].lower()
    p[8][1][1] = save_io.write_protobuf(sub)
    save_io.write_save(str(synthetic_save), p)
    save_io.set_skills(str(synthetic_save), {'GD_Soldier_Skills.Guerrilla.Sentry': 4})
    _, p = save_io.read_save(str(synthetic_save))
    assert p[4] == [[0, 8]]
    assert save_io.extract_skills(p)['GD_Soldier_Skills.Guerrilla.Sentry'] == 4


def test_field38_guard_still_blocks(synthetic_save):
    _, p = save_io.read_save(str(synthetic_save))
    save_io._change_class_in_player(p, CLASSES[2])
    sub = save_io.read_protobuf(p[38][0][1])
    sub[2] = [[0, 0]]
    p[38][0][1] = save_io.write_protobuf(sub)
    with pytest.raises(ValueError, match='field 38'):
        save_io.write_save(str(synthetic_save), p)


def test_api_class_and_game_guard(synthetic_save, monkeypatch):
    with flask_app.app.test_client() as client:
        r = client.post('/api/save/Save0001.sav/class', json={'class': 'Maya'})
        assert r.status_code == 200
        assert r.get_json()['refunded_points'] == 6
        assert r.get_json()['character']['class_name'] == 'Maya'
        assert client.post('/api/save/Save0001.sav/class', json={'class': 'bad'}).status_code == 400
        assert client.post('/api/save/Save0001.sav/class', json=[]).status_code == 400
        assert client.post('/api/save/notasave/class', json={'class': 'Axton'}).status_code == 400
        original = synthetic_save.read_bytes()
        monkeypatch.setattr(flask_app, '_is_game_running', lambda: True)
        assert client.post('/api/save/Save0001.sav/class', json={'class': 'Axton'}).status_code == 409
        assert synthetic_save.read_bytes() == original


def test_verified_asset_regressions():
    # Game/Gibbed class identifiers and representative non-obvious skill paths.
    defs = {save_io.CLASS_NAMES[c]: save_io._class_skill_defs(c) for c in CLASSES}
    assert 'GD_Tulip_Mechromancer_Skills.Action.Skill_DeathTrap' in defs['Gaige']
    assert 'GD_Lilac_SkillsBase.ActionSkill.Skill_Psycho' in defs['Krieg']
    assert 'GD_Mercenary_Skills.Gun_Lust.DivergentLikness' in defs['Salvador']
    assert defs['Zer0']['GD_Assassin_Skills.Sniping.CriticalAscention'] == 1
    assert defs['Axton']['GD_Soldier_Skills.Gunpowder.DoOrDie'] == 1
    assert defs['Gaige']['GD_Tulip_Mechromancer_Skills.EmbraceChaos.SmallerLighterFaster'] == 4
    assert [len(defs[name]) for name in ('Axton','Zer0','Maya','Salvador','Gaige','Krieg')] == [33,31,31,34,37,36]
