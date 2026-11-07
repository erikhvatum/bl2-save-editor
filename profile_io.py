"""Lossless BL2 PC profile codec and narrowly scoped account customizations.

The raw entry stream is retained verbatim. Only explicitly edited fixed-size
payloads change; we never reconstruct BAR/settings/stash/unknown records.
See PROFILE_FORMAT.md for provenance, bit layout, and rarity-proxy limitations.
"""
from __future__ import annotations

import hashlib
import json
import random
import struct
from dataclasses import dataclass
from pathlib import Path

MAX_RAW_SIZE = 16 * 1024 * 1024
ALPHABET = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'
MAGIC = 0x9A3652D9
BONUS_NAMES = [
    'Maximum health', 'Shield capacity', 'Shield recharge delay', 'Shield recharge rate',
    'Melee damage', 'Grenade damage', 'Gun accuracy', 'Gun damage', 'Fire rate',
    'Recoil reduction', 'Reload speed', 'Elemental effect chance',
    'Elemental effect damage', 'Critical hit damage',
]


def _codecs():
    # save_io establishes the configured library path, including standalone use.
    import save_io  # noqa: F401
    from borderlands.datautil.lzo1x import lzo1x_decompress, lzo1x_1_compress
    return lzo1x_decompress, lzo1x_1_compress


@dataclass(frozen=True)
class Entry:
    kind: int
    start: int
    end: int


class Profile:
    def __init__(self, raw: bytes, original: bytes | None = None):
        self.raw = bytearray(raw)
        self.original_raw = bytes(raw)
        self.original = original
        self.entries: dict[int, Entry] = {}
        if not 4 <= len(raw) <= MAX_RAW_SIZE:
            raise ValueError('Invalid profile entry stream size')
        count, = struct.unpack_from('>I', raw)
        pos = 4
        for _ in range(count):
            if pos + 6 > len(raw):
                raise ValueError('Truncated profile entry header')
            _, entry_id, kind = struct.unpack_from('>BIB', raw, pos)
            pos += 6
            if kind in (1, 5):
                length = 4
            elif kind == 8:
                length = 1
            elif kind in (4, 6):
                if pos + 4 > len(raw):
                    raise ValueError('Truncated profile length')
                length, = struct.unpack_from('>I', raw, pos)
                pos += 4
            else:
                raise ValueError(f'Unsupported profile entry type {kind}; refusing to rewrite it')
            if pos + length + 1 > len(raw):
                raise ValueError('Truncated profile entry payload')
            if entry_id in self.entries:
                raise ValueError('Duplicate profile entry ID; refusing ambiguous edits')
            self.entries[entry_id] = Entry(kind, pos, pos + length)
            pos += length + 1  # preserve both markers and any trailing bytes

    @classmethod
    def decode(cls, data: bytes) -> 'Profile':
        if len(data) < 28 or data[:20] != hashlib.sha1(data[20:]).digest():
            raise ValueError('Profile SHA-1 mismatch or truncated file')
        length, = struct.unpack_from('>I', data, 20)
        if not 4 <= length <= MAX_RAW_SIZE:
            raise ValueError('Unsupported decompressed profile size')
        decompress, _ = _codecs()
        try:
            raw = bytes(decompress(b'\xf0' + data[20:]))
        except Exception as exc:
            raise ValueError('Cannot decompress profile') from exc
        if len(raw) != length:
            raise ValueError('Profile decompressed size mismatch')
        return cls(raw, data)

    def encode(self) -> bytes:
        if bytes(self.raw) == self.original_raw and self.original is not None:
            return self.original  # byte-identical no-edit round trip, not just semantic
        _, compress = _codecs()
        body = compress(bytes(self.raw))[1:]
        encoded = hashlib.sha1(body).digest() + body
        if Profile.decode(encoded).raw != self.raw:
            raise ValueError('Profile round-trip verification failed')
        return encoded

    def payload(self, entry_id: int, kind: int) -> bytes:
        entry = self.entries.get(entry_id)
        if entry is None or entry.kind != kind:
            raise ValueError(f'Profile entry {entry_id} is missing or has an unsupported type')
        return bytes(self.raw[entry.start:entry.end])

    def set_payload(self, entry_id: int, kind: int, data: bytes):
        old = self.payload(entry_id, kind)
        if len(old) != len(data):
            raise ValueError('Profile payload resizing is intentionally unsupported')
        entry = self.entries[entry_id]
        self.raw[entry.start:entry.end] = data

    def integer(self, entry_id: int) -> int:
        return struct.unpack('>i', self.payload(entry_id, 1))[0]

    def set_integer(self, entry_id: int, value: int):
        self.set_payload(entry_id, 1, struct.pack('>i', value))

    def owned(self, customization: dict) -> bool:
        index = customization['index']
        if index is None:
            return customization['availability'] == 'default'
        data = self.payload(customization['profile_id'], 6)
        offset = index // 4
        if index < 0 or offset >= len(data):
            raise ValueError('Customization index exceeds this profile mask; no changes written')
        return bool(data[offset] & (1 << ((index * 2) % 8)))

    def set_owned(self, customization: dict, value: bool):
        if customization['index'] is None or not customization.get('verified', True):
            raise ValueError('Default/entitlement customizations cannot be edited as profile bits')
        self.owned(customization)  # type/capacity validation before touching bytes
        entry = self.entries[customization['profile_id']]
        offset = entry.start + customization['index'] // 4
        mask = 1 << ((customization['index'] * 2) % 8)
        if value:
            self.raw[offset] |= mask
        else:
            self.raw[offset] &= ~mask
        # The adjacent "new" flag, all skin bits, and every other bit survive.


def catalog() -> list[dict]:
    return json.loads(Path(__file__).with_name('profile_customizations.json').read_text())['customizations']


def transfer_heads(profile: Profile, old_class: str, new_class: str, seed: int = 0,
                   definitions: list[dict] | None = None) -> dict:
    """Bring target owned-count up to source count, never grant duplicates or remove.

    Existing target heads count toward the target total, even when their theme
    differs. Each target is counted at most once. Exact theme matches are reserved
    globally before fallback, preventing a fallback stealing a later counterpart.
    """
    rng = random.Random(seed)
    defs = catalog() if definitions is None else definitions
    eligible = [d for d in defs if d['kind'] == 'head' and d['index'] is not None
                and d.get('verified', True)]
    source = sorted((d for d in eligible if d['class'] == old_class and profile.owned(d)), key=lambda d: d['asset'])
    targets = sorted((d for d in eligible if d['class'] == new_class), key=lambda d: d['asset'])
    existing = [d for d in targets if profile.owned(d)]
    used_sources, used_targets, rows = set(), set(), []

    def record(s, t, tier):
        owned = profile.owned(t)
        if not owned:
            profile.set_owned(t, True)
        used_sources.add(s['asset']); used_targets.add(t['asset'])
        rows.append({'source': s['name'], 'source_asset': s['asset'], 'target': t['name'],
                     'target_asset': t['asset'], 'tier': tier, 'already_owned': owned,
                     'unlocked': not owned})

    # First credit existing exact counterparts, then other already-owned heads.
    for s in source:
        matches = [t for t in existing if t['asset'] not in used_targets
                   and s.get('group') and t.get('group') == s['group']]
        if matches:
            record(s, matches[0], 'similar')
    for t in existing:
        pending = [s for s in source if s['asset'] not in used_sources]
        if not pending:
            break
        if t['asset'] not in used_targets:
            record(pending[0], t, 'already_owned')
    # Remaining required heads: exact source/pack, then comparable tier, then RNG.
    for s in source:
        if s['asset'] in used_sources or not s.get('group'):
            continue
        matches = [t for t in targets if t['asset'] not in used_targets and t.get('group') == s['group']]
        if matches:
            record(s, matches[0], 'similar')
    for s in source:
        if s['asset'] in used_sources:
            continue
        available = [t for t in targets if t['asset'] not in used_targets]
        comparable = [t for t in available if t['tier'] == s['tier']]
        if comparable:
            record(s, rng.choice(comparable), 'comparable')
        elif available:
            record(s, rng.choice(available), 'random')
        else:
            rows.append({'source': s['name'], 'source_asset': s['asset'], 'target': None,
                         'tier': 'exhausted', 'already_owned': False, 'unlocked': False})
    return {'source_count': len(source), 'target_owned_before': len(existing),
            'target_owned_after': sum(profile.owned(t) for t in targets),
            'unlocked_count': sum(r['unlocked'] for r in rows),
            'tiers': {tier: sum(r['tier'] == tier for r in rows)
                      for tier in ('similar', 'comparable', 'random', 'already_owned', 'exhausted')},
            'mappings': rows,
            'warnings': ['Heads are account-wide. Skins, existing unlocks, BAR, keys and settings are unchanged.',
                         'Base-head source tiers are verified; other heads use DLC/pack tiers as a proxy, not measured drop rates.',
                         'Default and entitlement-only pack heads have no ownership bit and are excluded; DLC ownership is not bypassed.']}


def decode_bonuses(data: bytes) -> list[int]:
    if not data:
        return [0] * 14
    if len(data) != 90 or any(chr(c) not in ALPHABET for c in data):
        raise ValueError('Unsupported BAR bonus encoding')
    bits = sum(ALPHABET.index(chr(c)) << (5 * i) for i, c in enumerate(data))
    return [((bits >> (32 * i)) & 0xffffffff) ^ MAGIC for i in range(14)]


def encode_bonuses(values: list[int]) -> bytes:
    bits = sum((v ^ MAGIC) << (32 * i) for i, v in enumerate(values))
    return ''.join(ALPHABET[(bits >> (5 * i)) & 31] for i in range(90)).encode('ascii')


def _integer(value, minimum, maximum, label):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'{label} must be an integer from {minimum} to {maximum}')
    return value


def account_view(profile: Profile) -> dict:
    customizations = []
    for d in catalog():
        row = dict(d)
        try:
            row['owned'] = profile.owned(d) if d['availability'] != 'entitlement' else None
            row['editable'] = d['availability'] == 'bitmap' and d['verified']
        except ValueError:
            row['owned'] = None; row['editable'] = False
        customizations.append(row)
    result = {'customizations': customizations, 'bonus_names': BONUS_NAMES,
              'warnings': ['Account-wide data affects all characters. Close the game before saving.',
                           'Default heads/skins are always available; pack entitlement cannot be inferred from profile.bin.']}
    # Unsupported/missing optional entries are visible as unavailable, not fabricated.
    for key, reader in (
        ('rank', lambda: (profile.integer(136) + profile.integer(137)) // 10),
        ('tokens', lambda: profile.integer(138)),
        ('tokens_earned', lambda: profile.integer(139)),
        ('bonuses', lambda: decode_bonuses(profile.payload(143, 4))),
        ('fov', lambda: profile.integer(129)),
    ):
        try:
            result[key] = reader()
        except ValueError:
            result[key] = None
    data = profile.payload(162, 6) if 162 in profile.entries else b''
    if len(data) % 3:
        result['keys'] = None
    else:
        result['keys'] = [{'source': data[i], 'total': data[i+1], 'used': data[i+2],
                           'available': max(0, data[i+1] - data[i+2])} for i in range(0, len(data), 3)]
    result['bonus_percentages'] = ([round(v ** .75, 1) for v in result['bonuses']]
                                    if result['bonuses'] is not None else None)
    return result


def apply_account_patch(profile: Profile, changes: dict):
    if not isinstance(changes, dict) or set(changes) - {'customizations', 'rank', 'tokens', 'bonuses', 'keys', 'fov'}:
        raise ValueError('Unsupported profile changes')
    # Validate on an independent raw stream: any invalid part leaves caller intact.
    trial = Profile(bytes(profile.raw))
    custom = changes.get('customizations', {})
    if not isinstance(custom, dict):
        raise ValueError('Customizations must be an asset-to-boolean object')
    lookup = {d['asset']: d for d in catalog()}
    for asset, owned in custom.items():
        if asset not in lookup or type(owned) is not bool:
            raise ValueError('Unknown customization or non-boolean ownership')
        trial.set_owned(lookup[asset], owned)
    if 'rank' in changes:
        rank = _integer(changes['rank'], 0, 0x7fffffff // 5, 'Badass Rank')
        if rank != (trial.integer(136) + trial.integer(137)) // 10:
            trial.set_integer(136, rank * 5); trial.set_integer(137, rank * 5)
    if 'bonuses' in changes or 'tokens' in changes:
        old = decode_bonuses(trial.payload(143, 4)); old_tokens = trial.integer(138)
        bonuses = changes.get('bonuses', old)
        if not isinstance(bonuses, list) or len(bonuses) != 14:
            raise ValueError('Specify exactly 14 BAR invested-token counts')
        bonuses = [_integer(v, 0, 62530, 'BAR invested tokens') for v in bonuses]
        tokens = _integer(changes.get('tokens', old_tokens), 0, 62530, 'Available BAR tokens')
        earned = tokens + sum(bonuses)
        if earned > 62530:
            raise ValueError('Total earned BAR tokens exceeds 62530')
        if bonuses != old:
            if not trial.payload(143, 4):
                raise ValueError('Empty BAR bonus payload cannot be safely resized')
            trial.set_payload(143, 4, encode_bonuses(bonuses))
        if tokens != old_tokens or bonuses != old:
            trial.set_integer(138, tokens); trial.set_integer(139, earned)
    if 'keys' in changes:
        updates = changes['keys']
        if not isinstance(updates, dict):
            raise ValueError('Golden keys must be a source-to-available-count object')
        data = bytearray(trial.payload(162, 6))
        if len(data) % 3 or len({data[i] for i in range(0, len(data), 3)}) != len(data) // 3:
            raise ValueError('Unsupported golden-key records')
        offsets = {str(data[i]): i for i in range(0, len(data), 3)}
        for source, available in updates.items():
            if source not in offsets:
                raise ValueError('Golden-key source does not exist in this profile')
            i = offsets[source]
            available = _integer(available, 0, 255 - data[i+2], 'Available golden keys')
            if available != max(0, data[i+1] - data[i+2]):
                data[i+1] = available + data[i+2]
        trial.set_payload(162, 6, bytes(data))
    if 'fov' in changes:
        trial.set_integer(129, _integer(changes['fov'], 60, 120, 'Field of view'))
    profile.raw[:] = trial.raw
