"""Synthetic profile fixtures only: never locate the user's real save directory."""
import hashlib
import json
import struct
from pathlib import Path

import pytest

import profile_io as pio
import save_io
import app as flask_app
from tests.test_class_change import player_for


def entry(entry_id, kind, value, start=0xA7, end=0xD3):
    prefix = struct.pack('>BIB', start, entry_id, kind)
    if kind in (4, 6):
        prefix += struct.pack('>I', len(value))
    return prefix + value + bytes([end])


def make_profile():
    entries = [entry(136, 1, struct.pack('>i', 500)),
               entry(137, 1, struct.pack('>i', 500)),
               entry(138, 1, struct.pack('>i', 3)),
               entry(139, 1, struct.pack('>i', 17)),
               entry(143, 4, pio.encode_bonuses([1] * 14)),
               entry(162, 6, bytes([0, 20, 5, 173, 6, 2, 254, 8, 1])),
               entry(129, 1, struct.pack('>i', 90)),
               entry(300, 6, bytes(1001)), entry(301, 6, b'other DLC bitmap'),
               entry(919, 5, bytes.fromhex('7fc01234')),  # retain NaN payload exactly
               entry(920, 8, b'\xf1'), entry(921, 4, b'opaque\xff\x00settings'),
               entry(130, 6, b'opaque bank/stash/item bytes'),
               entry(164, 4, b'preserve next bonus stats')]
    raw = struct.pack('>I', len(entries)) + b''.join(entries) + b'future trailing bytes'
    return pio.Profile(raw)


def definition(asset, cls, index, group=None, tier=2, kind='head'):
    return dict(asset=asset, name=asset, kind=kind, **{'class': cls}, profile_id=300,
                index=index, group=group, tier=tier, verified=True, availability='bitmap')


def test_no_edit_byte_and_semantic_round_trip():
    p = make_profile(); encoded = p.encode(); decoded = pio.Profile.decode(encoded)
    assert decoded.encode() == encoded
    assert decoded.raw == p.raw
    assert decoded.payload(919, 5) == bytes.fromhex('7fc01234')
    assert decoded.payload(921, 4) == b'opaque\xff\x00settings'
    assert decoded.raw.endswith(b'future trailing bytes')


@pytest.mark.parametrize('bad', [b'', b'x' * 50, b'\x00' * 100])
def test_invalid_hash_rejected(bad):
    with pytest.raises(ValueError):
        pio.Profile.decode(bad)


def test_invalid_entry_types_lengths_and_duplicates_rejected():
    for raw in [struct.pack('>I', 1) + b'\0',
                struct.pack('>I', 1) + entry(1, 9, b'x'),
                struct.pack('>I', 2) + entry(1, 8, b'a') * 2,
                struct.pack('>I', 1) + entry(1, 6, b'abcdef')[:-2]]:
        with pytest.raises(ValueError):
            pio.Profile(raw)
    body = struct.pack('>I', pio.MAX_RAW_SIZE + 1) + b'abcd'
    with pytest.raises(ValueError, match='size'):
        pio.Profile.decode(hashlib.sha1(body).digest() + body)


def test_only_ownership_bit_changes_seen_and_neighbor_skins_preserved():
    p = make_profile(); head = definition('head', 'Axton', 7)
    skin = definition('skin', 'Axton', 6, kind='skin')
    p.set_owned(skin, True)
    e = p.entries[300]
    p.raw[e.start + 1] |= 0x80  # adjacent "new" flag for head 7
    before = bytes(p.raw)
    p.set_owned(head, True)
    assert p.owned(head) and p.owned(skin)
    assert p.raw[e.start+1] == before[e.start+1] | 0x40
    changed = [i for i, (a, b) in enumerate(zip(before, p.raw)) if a != b]
    assert changed == [e.start + 1]
    p.set_owned(head, False)
    assert bytes(p.raw) == before  # removing ownership did not clear the flag
    with pytest.raises(ValueError):
        p.set_owned(definition('out of bounds', 'Maya', 50000), True)


def test_similar_comparable_and_random_fallbacks_seeded_and_unique():
    defs = [definition('old-pack', 'Gaige', 10, 'xmas', 4),
            definition('old-regular', 'Gaige', 11), definition('old-rare', 'Gaige', 12, tier=3),
            definition('new-pack', 'Maya', 20, 'xmas', 4),
            definition('new-regular', 'Maya', 21), definition('new-other', 'Maya', 22, tier=1)]
    original = make_profile()
    for d in defs[:3]:
        original.set_owned(d, True)
    a = pio.Profile(bytes(original.raw)); b = pio.Profile(bytes(original.raw))
    result = pio.transfer_heads(a, 'Gaige', 'Maya', 123, defs)
    assert result == pio.transfer_heads(b, 'Gaige', 'Maya', 123, defs)
    assert result['tiers'] == dict(similar=1, comparable=1, random=1, already_owned=0, exhausted=0)
    assert result['unlocked_count'] == 3 and result['target_owned_after'] == 3
    assert all(a.owned(d) for d in defs)
    assert len({r['target_asset'] for r in result['mappings']}) == 3


def test_existing_heads_count_even_if_different_theme_and_no_extra_grants():
    defs = [definition('s1', 'Gaige', 1, 'a'), definition('s2', 'Gaige', 2, 'b'),
            definition('t1', 'Maya', 3, 'a'), definition('t2', 'Maya', 4, 'different'),
            definition('t3', 'Maya', 5, 'b')]
    p = make_profile()
    for d in defs[:4]: p.set_owned(d, True)
    before = bytes(p.raw)
    result = pio.transfer_heads(p, 'Gaige', 'Maya', definitions=defs)
    assert result['unlocked_count'] == 0
    assert result['tiers']['similar'] == 1 and result['tiers']['already_owned'] == 1
    assert bytes(p.raw) == before


def test_source_equivalents_are_reserved_before_fallback():
    defs = [definition('a-fallback', 'Gaige', 1, tier=4),
            definition('b-similar', 'Gaige', 2, 'theme', 4),
            definition('only-counterpart', 'Maya', 3, 'theme', 4),
            definition('fallback', 'Maya', 4, tier=4)]
    p = make_profile()
    for d in defs[:2]: p.set_owned(d, True)
    result = pio.transfer_heads(p, 'Gaige', 'Maya', definitions=defs)
    row = next(r for r in result['mappings'] if r['source_asset'] == 'b-similar')
    assert row['target_asset'] == 'only-counterpart' and row['tier'] == 'similar'


def test_exhaustion_is_reported_and_skins_are_not_transferred():
    defs = [definition('s1', 'Gaige', 1), definition('s2', 'Gaige', 2),
            definition('skin', 'Gaige', 3, kind='skin'), definition('target', 'Maya', 4)]
    p = make_profile()
    for d in defs[:3]: p.set_owned(d, True)
    result = pio.transfer_heads(p, 'Gaige', 'Maya', definitions=defs)
    assert result['source_count'] == 2 and result['tiers']['exhausted'] == 1
    assert p.owned(defs[2])


def test_catalog_verified_unique_and_all_six_classes():
    data = pio.catalog(); heads = [d for d in data if d['kind'] == 'head']
    assert len(heads) == 216
    assert sum(d['index'] is not None for d in heads) == 168
    assert len(data) == 838 and sum(d['index'] is not None for d in data) == 646
    assert set(d['class'] for d in heads) == set(save_io.CLASS_NAMES.values())
    assert all(d['verified'] for d in data)
    keys = [(d['profile_id'], d['index']) for d in data if d['index'] is not None]
    assert len(keys) == len(set(keys))
    assert all(d['index'] >= 0 for d in data if d['index'] is not None)
    # Names from same Xmas/TG themed DLC sets are grouped independently.
    gaige = [d for d in heads if d['class'] == 'Gaige' and d['pack'] == 'allium']
    assert len({d['group'] for d in gaige}) == 2
    # Starting heads and entitlement-only purchased packs have no stored bit.
    assert next(d for d in heads if d['name'] == 'Keeping it Real')['availability'] == 'default'
    assert next(d for d in heads if d['name'] == 'Necrosis')['availability'] == 'entitlement'


def test_bar_codec_known_bit_formula_and_stat_order():
    values = list(range(14)); encoded = pio.encode_bonuses(values)
    assert pio.decode_bonuses(encoded) == values
    # Independent C#/B2Profile decoding algorithm from reference implementation.
    shift = 0; value = pio.MAGIC; decoded = []
    for char in encoded.decode():
        index = pio.ALPHABET.index(char); value ^= index << shift; shift += 5
        if shift > 31:
            decoded.append(value & 0xffffffff); shift &= 7
            value = pio.MAGIC ^ (index >> (5 - shift))
    assert decoded == values
    assert pio.decode_bonuses(b'') == [0] * 14
    with pytest.raises(ValueError): pio.decode_bonuses(b'INVALID')


def test_account_patch_preserves_all_unrelated_records():
    p = make_profile(); before = bytes(p.raw)
    skin = next(d for d in pio.catalog() if d['kind'] == 'skin' and d['index'] is not None)
    changes = dict(rank=123, tokens=4, bonuses=list(range(14)), keys={'0': 30}, fov=100,
                   customizations={skin['asset']: True})
    pio.apply_account_patch(p, changes)
    view = pio.account_view(p)
    assert view['rank'] == 123 and view['tokens'] == 4 and view['tokens_earned'] == 95
    assert view['bonuses'] == list(range(14)) and p.owned(skin)
    assert p.payload(162, 6) == bytes([0, 35, 5, 173, 6, 2, 254, 8, 1])
    for entry_id in (301, 919, 920, 921, 130, 164):
        e = p.entries[entry_id]
        assert p.raw[e.start:e.end] == before[e.start:e.end]
    assert pio.Profile.decode(p.encode()).raw == p.raw


def test_noop_patch_is_byte_identical():
    p = pio.Profile.decode(make_profile().encode()); old = p.encode(); view = pio.account_view(p)
    pio.apply_account_patch(p, dict(rank=view['rank'], tokens=view['tokens'], bonuses=view['bonuses'],
                                  keys={str(k['source']): k['available'] for k in view['keys']}, fov=view['fov']))
    assert p.encode() == old


@pytest.mark.parametrize('changes', [None, [], {'unknown': 1}, {'rank': True}, {'rank': -1},
    {'tokens': 62531}, {'bonuses': [1]}, {'bonuses': [5000] * 14}, {'keys': {'0': 251}},
    {'keys': {'not a source': 1}}, {'fov': 10}, {'customizations': {'unknown': True}},
    {'customizations': {next(d['asset'] for d in pio.catalog() if d['availability'] == 'default'): True}}])
def test_invalid_account_changes_do_not_mutate(changes):
    p = make_profile(); before = bytes(p.raw)
    with pytest.raises(ValueError): pio.apply_account_patch(p, changes)
    assert bytes(p.raw) == before


@pytest.fixture
def disk_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(save_io, 'SAVE_DIR', str(tmp_path))
    monkeypatch.setattr(flask_app, '_is_game_running', lambda: False)
    p = make_profile()
    head = next(d for d in pio.catalog() if d['class'] == 'Axton' and d['kind'] == 'head' and d['index'] is not None)
    p.set_owned(head, True)
    (tmp_path / 'profile.bin').write_bytes(p.encode())
    save_io.write_save('Save0001.sav', player_for())
    return tmp_path


def test_disk_backups_revision_guard_and_noop(disk_profile):
    path = disk_profile / 'profile.bin'; old = path.read_bytes(); view = save_io.read_account_profile()
    assert not save_io.update_account_profile({}, view['revision'])['changed']
    assert not path.with_suffix('.bin.bak').exists()
    result = save_io.update_account_profile({'fov': 100}, view['revision'])
    assert result['changed'] and Path(str(path) + '.bak').read_bytes() == old
    with pytest.raises(ValueError, match='revision'): save_io.update_account_profile({}, view['revision'])
    assert save_io.read_account_profile()['fov'] == 100


def test_class_change_writes_both_and_preserves_all_other_profile_bits(disk_profile):
    path = disk_profile / 'profile.bin'; old = path.read_bytes(); oldp = pio.Profile.decode(old)
    save_path = disk_profile / 'Save0001.sav'; oldsave = save_path.read_bytes()
    result = save_io.change_character_class('Save0001.sav', 'Maya', True, 2)
    assert result['heads']['source_count'] == 1 and result['heads']['unlocked_count'] == 1
    assert Path(str(path) + '.bak').read_bytes() == old
    assert Path(str(save_path) + '.bak').read_bytes() == oldsave
    newp = pio.Profile.decode(path.read_bytes())
    changed = [i for i, (a, b) in enumerate(zip(oldp.raw, newp.raw)) if a != b]
    assert len(changed) == 1
    assert all((newp.raw[i] | oldp.raw[i]) == newp.raw[i] for i in range(len(newp.raw)))
    _, saved = save_io.read_save('Save0001.sav')
    assert saved[4][0][1] == 13 and saved[38] == player_for()[38]


def test_pair_profile_failure_rolls_back_save(disk_profile, monkeypatch):
    path = disk_profile / 'profile.bin'; save_path = disk_profile / 'Save0001.sav'
    old_profile = path.read_bytes(); old_save = save_path.read_bytes(); replace = save_io.os.replace
    def fail_profile(src, dest):
        if str(dest) == str(path): raise OSError('injected profile replace failure')
        return replace(src, dest)
    monkeypatch.setattr(save_io.os, 'replace', fail_profile)
    with pytest.raises(OSError, match='injected'): save_io.change_character_class('Save0001.sav', 'Maya', True)
    assert path.read_bytes() == old_profile and save_path.read_bytes() == old_save
    assert not list(disk_profile.glob('.bl2-profile-*'))


def test_missing_or_invalid_profile_prevents_class_write(disk_profile):
    path = disk_profile / 'profile.bin'; save_path = disk_profile / 'Save0001.sav'; old = save_path.read_bytes()
    path.write_bytes(b'invalid')
    with pytest.raises(ValueError): save_io.change_character_class('Save0001.sav', 'Maya', True)
    assert save_path.read_bytes() == old
    path.unlink()
    with pytest.raises(FileNotFoundError): save_io.change_character_class('Save0001.sav', 'Maya', True)
    assert save_path.read_bytes() == old


def test_profile_api_explicit_confirmation_and_game_guard(disk_profile, monkeypatch):
    with flask_app.app.test_client() as client:
        view = client.get('/api/profile').get_json()
        assert view['rank'] == 100
        assert client.post('/api/profile', json={'changes': {'rank': 101}, 'revision': view['revision']}).status_code == 400
        assert client.post('/api/profile', json={'changes': {'rank': 101}, 'revision': view['revision'], 'confirm_profile_write': True}).status_code == 200
        assert client.post('/api/save/Save0001.sav/class', json={'class': 'Maya', 'unlock_heads': True}).status_code == 400
        monkeypatch.setattr(flask_app, '_is_game_running', lambda: True)
        assert client.post('/api/profile', json={'changes': {}, 'confirm_profile_write': True}).status_code == 409
        assert client.post('/api/save/Save0001.sav/class', json={'class': 'Maya', 'unlock_heads': True, 'confirm_profile_write': True}).status_code == 409

@pytest.mark.parametrize('source', list(save_io.CLASS_NAMES.values()))
@pytest.mark.parametrize('target', list(save_io.CLASS_NAMES.values()))
def test_all_class_head_mapping_pairs(source, target):
    if source == target:
        return
    p = make_profile()
    heads = [d for d in pio.catalog() if d['kind'] == 'head' and d['index'] is not None]
    source_heads = [d for d in heads if d['class'] == source]
    for d in source_heads: p.set_owned(d, True)
    old = bytes(p.raw)
    result = pio.transfer_heads(p, source, target, 123)
    target_count = sum(d['class'] == target for d in heads)
    assert result['target_owned_after'] == min(len(source_heads), target_count)
    assert all(p.owned(d) for d in source_heads)
    assert all((a | b) == b for a, b in zip(old, p.raw))
    assert result['tiers']['similar'] >= 10


def test_partial_good_then_invalid_change_is_atomic():
    p = make_profile(); old = bytes(p.raw)
    with pytest.raises(ValueError):
        pio.apply_account_patch(p, {'rank': 10, 'fov': -3})
    assert bytes(p.raw) == old


def test_external_profile_change_during_class_write_rolls_back_save(disk_profile, monkeypatch):
    path = disk_profile / 'profile.bin'; save = disk_profile / 'Save0001.sav'; old_save = save.read_bytes()
    write = save_io._write_save_locked
    external = b'external game write; must not be overwritten'
    def external_write(*args, **kwargs):
        write(*args, **kwargs); path.write_bytes(external)
    monkeypatch.setattr(save_io, '_write_save_locked', external_write)
    with pytest.raises(ValueError, match='externally'):
        save_io.change_character_class('Save0001.sav', 'Maya', True)
    assert path.read_bytes() == external and save.read_bytes() == old_save


def test_account_ui_synthetic_dom_smoke():
    import shutil
    import subprocess
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is optional; run tools/profile_ui_smoke.js when available')
    subprocess.run([node, 'tools/profile_ui_smoke.js'], check=True,
                   cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)


def test_verified_current_head_reward_counterparts_all_classes():
    heads = [d for d in pio.catalog() if d['kind'] == 'head']
    gaige = {d['name']: d for d in heads if d['class'] == 'Gaige'}
    p = make_profile()
    p.set_owned(gaige['Read All About It'], True)
    p.set_owned(gaige['Speak No Evil'], True)
    expected = {'Axton': ['Grizzled Veteran', 'Roguish Renegade'],
                'Maya': ['One-Eye Spy', 'Nevermore'], 'Zer0': ['Blast Shield', 'Punk'],
                'Salvador': ['A Sense of Dread', 'Bowler Badass'],
                'Krieg': ['STAY IN SCHOOL KIDS', 'COMING FOR YOU']}
    for cls, names in expected.items():
        result = pio.transfer_heads(pio.Profile(bytes(p.raw)), 'Gaige', cls)
        assert result['tiers']['similar'] == 2
        assert [r['target'] for r in result['mappings']] == names
    assert gaige['Read All About It']['tier'] == 1
    assert gaige['Speak No Evil']['tier_basis'] == 'verified_source'


def test_profile_keyboard_save_targets_account_button():
    source = (Path(__file__).resolve().parents[1] / 'static/app.js').read_text()
    assert 'if (tabKey === "profile") saveBtn = document.getElementById("btn-profile-save")' in source
