# Account profile editing and class-change head compensation

## What is edited

The **Account** tab (also accessible from the sidebar without selecting a save)
reads `profile.bin` in the configured save directory. It supports:

- Viewing per-class heads/skins and checking/unchecking individually owned bits.
- Badass Rank, available BAR tokens, and the 14 per-stat **invested-token counts**.
  The displayed percentage is `round(tokens ** 0.75, 1)`, not an arbitrary float.
  The game applies reduction direction for shield delay/recoil. When investment
  or available tokens change, earned tokens are kept equal to their sum (maximum
  62,530). Rank can be edited independently; it is not silently recalculated.
- Available golden keys in each existing source record; used-key counts survive.
  Total keys remain byte-sized, so available keys cannot exceed `255 - used`.
- FOV (60–120). Unknown settings, stash data, and unsupported fields are read-only.

Profile writes need explicit confirmation, a matching revision, a stopped game,
and rotating `.bak` backups. Unchanged submissions do not write or rotate backups.
Unsupported entry types/encodings fail closed instead of guessing. Empty BAR
bonus strings can be read as zero investment, but resizing them is intentionally
unsupported. No DLC ownership checks or license changes are performed.

Changing class has a separate checked option to compensate heads in the account
profile. Confirmation explicitly names **both** files. Unchecking it changes only
the save. Head/skin wearing slots reset to class defaults; inventory/class mods
remain. All purchased old-class skill grades, **including the action skill's one
point**, return to unspent field 4; new skills/action start at grade zero. The
field-38 challenge-registry guard is unchanged.

## Lossless profile format

Verified against [withmorten/B2Profile, B2Profile.cs](https://github.com/withmorten/B2Profile/blob/master/B2Profile/B2Profile.cs):

1. 20 bytes SHA-1 over the remaining bytes.
2. Big-endian uint32 decompressed byte length, then an LZO1X stream.
3. Decompressed big-endian uint32 entry count.
4. Per entry: start byte, uint32 ID, type byte, value, end byte. Types 1/5 have
   four raw bytes, type 8 has one byte; string 4/binary 6 have uint32 length and
   raw bytes. Both markers, original ordering, NaN/float bit patterns, unknown
   payloads, and trailing bytes are retained verbatim.

No-edit encode returns the original compressed bytes exactly. Edited encode
recompresses and verifies a full decode against the edited raw byte stream.
The BAR code uses the reference's Crockford alphabet and XOR `0x9A3652D9`; keys
(entry 162) are `[source, total, used]` triples. Rank is `(136 + 137) // 10`.
Account edits touch only requested allowlisted payloads, plus earned tokens when
investment/available tokens change. Next-bonus selection (164), BAR bonuses,
skins, golden keys and settings are completely untouched by **head compensation**.

## Customization indexes: never inferred from JSON ordering

Gibbed's [Customizations.json](https://github.com/gibbed/Borderlands2Dumps/blob/master/Customizations.json)
gives names, usage/classes and DLC sets, **not profile bit indexes**. We join those
assets with `CustomizationDefinition.ProfileEntryID` and `ProfileEntryIndex`
extracted from installed UE3 base/DLC startup packages. The checked-in catalog
includes relative package paths and SHA-1 provenance, not game binaries.

[Borderlands2SDK CustomizationDefinition and WillowCustomizationManager declarations](https://github.com/McSimp/Borderlands2SDK/blob/master/src/GameSDK/WillowGame_classes.h)
identify the properties and unlock/new-state distinction. Static read-only
inspection of the installed Mac game's native `IsCustomizationUnlocked` confirmed:

- `ProfileEntryIndex == -1` is inherently unlocked (with a separate DLC gate).
- Byte offset is `index // 4`; ownership is `1 << ((2 * index) % 8)`.
- The adjacent `2 * index + 1` bit is the game's "new" flag. Edits preserve it.
- Player-controller initialization copies the profile binary mask directly.

For audit/reproduction, these native methods were resolved through the executable's
RTTI/vtable (not by running or attaching to the game): Mac virtual addresses
`0x10183c6d0` (ownership), `0x101ad9390` (mask cache initialization). These addresses
are build-specific; no runtime dependency on them exists.

Regenerate on a supported version-832/licensee-46 installation:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tools/extract_profile_catalog.py /path/to/GameData
```

Catalog: **838 class-specific customizations**, of which **646 have writable
profile bits**: 168 heads and 478 skins. All 216 catalog heads are represented;
48 lack bitmap indexes (18 starting/default heads, 30 entitlement-only heads).
Default/entitlement assets are read-only and excluded from acquired-head transfer.
Every class has three inherently available starting heads, so excluding these
from both sides does not reduce the transferred acquired-head count. Purchased
pack availability cannot be inferred from `profile.bin`; this feature does not
manufacture or bypass DLC licenses. Vehicle skins are not included in this UI.

## Head counterpart and rarity table

`head_sources.json` records **74 verified named-head source entries** from
[Gamer Guides — Heads, by Jarrod Garripoli](https://www.gamerguides.com/borderlands-2/guide/extras/character-customization/heads),
read directly during implementation. These cover all six classes' Symbiosis,
In Memoriam, Statuesque, To Pay the Bills, named-enemy and general Badass drops,
plus Krieg's campaign heads (whose asset package is Lilac rather than the
campaign package). A few guide spelling errors are normalized to Gibbed names.

Remaining counterparts use shared Gibbed DLC-set/source metadata. Xmas and Turkey,
Easter and Wedding, and each Power/Cool/Zany pack are kept separate. Promotion
families use their catalog name prefixes. All **168 bitmap heads** have a known
counterpart-family key, though not every family exists for every class.

The supplemental source tiers are:

| Tier | Source category |
| --- | --- |
| 0 | Default (not transferred) |
| 1 | Mission/challenge reward |
| 2 | General/common enemy drop |
| 3 | Named-enemy/rare/boss drop |
| 4 | DLC/pack/promotion |

These are **source categories, not item-color rarity or measured drop rates**.
For the remaining 94 indexed heads, pack/DLC metadata is explicitly used as a
proxy (`tier_basis: pack_proxy`) rather than claiming a verified enemy rarity.

Transfer first credits already-owned counterparts, then other already-owned
new-class heads toward the required count. For the remaining required additions:

1. Reserve same-source/themed-pack counterparts globally.
2. Choose an unowned head in the same source tier.
3. Otherwise choose a random unowned head, using a seedable RNG (UI seed 0).

Each target counts once. Old-class unlocks are never removed, and skin bits are
never compensated. The target ends with at least the source's acquired-head count
unless its complete head pool is smaller; exhaustion is reported rather than
inventing indexes. The API/UI report each mapping, tier, pre-owned status and counts.

## Safety and limitations

- Only `/tmp` copies of real profile/saves were used for end-to-end validation.
- Profile and save locks serialize the editor's writes. Staging, SHA-1 checks,
  revision checks, semantic verification and backups protect handled failures.
- Two filesystem paths cannot be atomically replaced together. A handled profile
  replacement failure rolls back the save; power loss between the two renames
  still requires restoring the matching `.bak` pair. Keep the game closed.
- Steam Cloud or another process can overwrite edits; reload stale account data.
- Not tested in-game. Profile support is tested for the observed PC/Mac format,
  not console-encrypted profiles or every future game build.
- Account locking is intentional removal only when explicitly selected in the
  Account tab; automatic class compensation is strictly additive.
