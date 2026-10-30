#!/usr/bin/env python3
"""Export the Mac Borderlands 2 meshes and textures the 3D viewer loads.

UModel runs under Wine. The game install is only read. High-mip .tfc files
are staged with symlinks outside the install, because DLC keeps them in a
sibling Textures directory and UModel looks beside the package it opened.

The Mac cook's game tag is "border". "bl2" is rejected. UModel reads the
LZO wrapper on Startup.upk itself. A whole-package head export crashes while
decoding a cubemap and deletes the files it already wrote, so each head is
exported by mesh name, then by texture name.

Weapon, shield, and grenade normal maps are BC5: the blue channel is empty
and the viewer needs it rebuilt. Class-mod normals already have a blue
channel, and character textures are left untouched.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import urllib.error
import urllib.request
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
CACHE = Path.home() / ".cache" / "bl2-save-editor"
WINEPREFIX = CACHE / "wine"
UMODEL_DIR = CACHE / "umodel"
STAGE = CACHE / "stage"
LOG = CACHE / "extract.log"

UMODEL_URLS = (
    "https://www.gildor.org/down/47/umodel/umodel_win32.zip",
    "https://web.archive.org/web/20230919214331id_/https://www.gildor.org/down/47/umodel/umodel_win32.zip",
)

# Cooked object name -> path under static/models. The Siren and Merc class
# mods really are spelled "Classmod" in the package.
GESTALTS = {
    "GestaltDef_Pistol_GestaltSkeletalMesh": "weapons/Pistol",
    "GestaltDef_AssaultRifle_GestaltSkeletalMesh": "weapons/Assault_Rifle",
    "GestaltDef_SMG_GestaltSkeletalMesh": "weapons/SMG",
    "GestaltDef_Shotgun_GestaltSkeletalMesh": "weapons/Shotgun",
    "GestaltDef_SniperRifle_GestaltSkeletalMesh": "weapons/Sniper_Rifle",
    "GestaltDef_Launcher_GestaltSkeletalMesh": "weapons/Rocket_Launcher",
    "GestaltDef_Shields_GestaltSkeletalMesh": "items/Shield",
    "GestaltDef_Grenades_GestaltSkeletalMesh": "items/Grenade_Mod",
    "GestaltDef_Artifact_GestaltSkeletalMesh": "items/Relic",
}
CLASSMODS = {
    "SoldierClassMod01": "items/classmods/ClassMod_Soldier",
    "ZeroClassMod01": "items/classmods/ClassMod_Assassin",
    "SirenClassmod01": "items/classmods/ClassMod_Siren",
    "MercClassmod01": "items/classmods/ClassMod_Merc",
}

# package, mesh, diffuse, mask, normal, character folder, gltf file name
BODIES = (
    ("GD_Soldier_Streaming_SF", "base", "Skel_SoldierBody",
     "SoldierBody_Dif", "SoldierBody_Msk", "SoldierBody_Nrm",
     "Axton", "Skel_SoldierBody.gltf"),
    ("GD_Assassin_Streaming_SF", "base", "Skel_AssassinBody",
     "AssassinBody_Dif", "AssassinBody_Msk", "AssassinBody_Nrm",
     "Zer0", "Skel_AssassinBody.gltf"),
    ("GD_Siren_Streaming_SF", "base", "Skel_SirenBody",
     "SirenBody_Dif", "SirenBody_Msk", "SirenBody_Nrm",
     "Maya", "Skel_SirenBody.gltf"),
    ("GD_Mercenary_Streaming_SF", "base", "Char_MercBody",
     "MercBody_Dif", "MercBody_Msk", "MercBody_Nrm",
     "Salvador", "Char_MercBody.gltf"),
    ("GD_Tulip_Mechro_Streaming_SF", "tulip", "Skel_MechromancerBody",
     "MechromancerBody_Dif", "MechromancerBody_Msk", "MechromancerBody_Nrm",
     "Gaige", "Skel_MechromancerBody.gltf"),
    ("GD_Lilac_Psycho_Streaming_SF", "lilac", "Skel_PsychoBody",
     "Char_PsychoBody_D", "Char_PsychoBody_Comp", "Char_PsychoBody_N",
     "Krieg", "Skel_PsychoBody.gltf"),
)
DEFAULT_HEADS = (
    ("CD_Soldier_Head_Default_SF", "base", "Skel_Soldier000", "Axton", "SoldierHead_Dif"),
    ("CD_Assassin_Head_Default_SF", "base", "Skel_Zero000", "Zer0", "AssassinHead_Dif"),
    ("CD_Siren_Head_Default_SF", "base", "Skel_Siren000", "Maya", "SirenHead_Dif"),
    ("CD_Mercenary_Head_Default_SF", "base", "Skel_Merc000", "Salvador", "MercHead_Dif"),
    ("CD_Mechro_Head_Default_SF", "tulip", "Skel_Mechro000", "Gaige", "MechromancerHead_Dif"),
    ("CD_Psycho_Head_Default_SF", "lilac", "Skel_Psycho000", "Krieg", "Char_PsychoHead000_D"),
)

ASSET_LISTS = (
    STATIC / "viewer3d.js",
    STATIC / "weapon_materials.json",
    STATIC / "head_models.json",
    STATIC / "head_materials.json",
)

FORCE = False
GAMEDATA = None
COOKED = None


def log(msg):
    print(msg, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(msg + "\n")


def present(path):
    return path.is_file() and path.stat().st_size > 32 and not FORCE


def win_path(path):
    return "Z:" + str(path).replace("/", "\\")


def wine_env():
    env = os.environ.copy()
    env.update({
        "WINEPREFIX": str(WINEPREFIX),
        "WINEDEBUG": "-all",
        "WINEDLLOVERRIDES": "mscoree,mshtml=",
        "MVK_CONFIG_LOG_LEVEL": "0",
    })
    return env


def find_wine():
    for name in ("wine", "wine64"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def ensure_umodel():
    exe = UMODEL_DIR / "umodel_64.exe"
    if exe.is_file():
        return exe
    UMODEL_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = CACHE / "umodel_win32.zip"
    last_error = ""
    for url in UMODEL_URLS:
        log("Downloading UModel from " + url)
        try:
            urllib.request.urlretrieve(url, zip_path)
            if zip_path.stat().st_size < 100000:
                raise OSError("download was only %d bytes" % zip_path.stat().st_size)
            shutil.unpack_archive(str(zip_path), str(UMODEL_DIR))
            break
        except (OSError, urllib.error.URLError, shutil.ReadError) as exc:
            last_error = str(exc)
            log("  download failed: " + last_error)
    if not exe.is_file():
        # Some zips unpack into a nested folder.
        nested = list(UMODEL_DIR.rglob("umodel_64.exe"))
        if nested:
            return nested[0]
        sys.exit("Could not obtain umodel_64.exe (%s)." % last_error)
    return exe


def ensure_wine_prefix(wine):
    if (WINEPREFIX / "system.reg").is_file():
        return
    log("Creating Wine prefix at " + str(WINEPREFIX))
    env = wine_env()
    env["WINEARCH"] = "win64"
    subprocess.run([wine, "wineboot", "--init"], env=env, check=False)


def run_umodel(umodel, wine, args):
    cmd = [wine, str(umodel), "-game=border", *args]
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write("\n$ " + " ".join(cmd) + "\n")
        proc = subprocess.run(
            cmd, stdout=fh, stderr=subprocess.STDOUT,
            env=wine_env(), cwd=str(umodel.parent),
        )
    if proc.returncode != 0:
        log("umodel exited %d (see %s)" % (proc.returncode, LOG))
    return proc.returncode


def content_dir(kind):
    if kind == "base":
        return COOKED
    if kind == "tulip":
        return GAMEDATA / "DLC" / "Tulip" / "Compat" / "Content"
    if kind == "lilac":
        return GAMEDATA / "DLC" / "Lilac" / "Compat" / "Content"
    raise ValueError(kind)


def texture_dirs_for(package):
    found = []
    cursor = package.parent
    for _ in range(6):
        cand = cursor / "Textures"
        if cand.is_dir():
            found.append(cand)
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    return found


def view_package(package):
    """Open a copy of the package that sits next to the .tfc files it needs.

    Symlinks stay in the cache. Nothing is written into the game install.
    """
    if package.parent == COOKED:
        return package
    dest = STAGE / "views" / package.stem
    dest.mkdir(parents=True, exist_ok=True)
    link = dest / package.name
    if not link.exists():
        os.symlink(package, link)
    startup = COOKED / "Startup.upk"
    startup_link = dest / "Startup.upk"
    if startup.is_file() and not startup_link.exists():
        os.symlink(startup, startup_link)
    for folder in texture_dirs_for(package):
        for tfc in folder.glob("*.tfc"):
            tfc_link = dest / tfc.name
            if not tfc_link.exists():
                os.symlink(tfc, tfc_link)
    for tfc in COOKED.glob("*.tfc"):
        tfc_link = dest / tfc.name
        if not tfc_link.exists():
            os.symlink(tfc, tfc_link)
    return link


def place_gltf(src_gltf, dest_gltf):
    dest_gltf.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(src_gltf.read_text(encoding="utf-8"))
    src_dir = src_gltf.parent
    for buf in data.get("buffers") or []:
        uri = buf.get("uri")
        if not uri or "://" in uri:
            continue
        dest_bin = dest_gltf.parent / (dest_gltf.stem + ".bin")
        shutil.copy2(src_dir / uri, dest_bin)
        buf["uri"] = dest_bin.name
    dest_gltf.write_text(json.dumps(data, indent=2), encoding="utf-8")


def link_texture(gltf_path, png_name):
    """Point the glTF's material at a PNG that already sits beside it."""
    data = json.loads(gltf_path.read_text(encoding="utf-8"))
    data["images"] = [{"uri": png_name}]
    data["textures"] = [{"source": 0}]
    for mat in data.get("materials") or []:
        pbr = mat.setdefault("pbrMetallicRoughness", {})
        pbr["baseColorTexture"] = {"index": 0}
        pbr["baseColorFactor"] = [1, 1, 1, 1]
    gltf_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def export_named(umodel, wine, package, out, export_args, objects):
    out.mkdir(parents=True, exist_ok=True)
    opened = view_package(package)
    args = ["-export", *export_args, "-out=" + win_path(out)]
    for name in objects:
        args.append("-obj=" + name)
    args.append(win_path(opened))
    run_umodel(umodel, wine, args)
    return out / package.stem


def startup_texture_dests():
    """PNG paths the viewer and weapon material table actually request."""
    dests = {}
    for path in (STATIC / "viewer3d.js", STATIC / "weapon_materials.json"):
        text = path.read_text(encoding="utf-8")
        for folder, name in re.findall(
            r"/static/textures/(weapons|items)/([A-Za-z0-9_]+)\.png", text
        ):
            dests[name] = STATIC / "textures" / folder / (name + ".png")
    return dests


def export_startup(umodel, wine):
    stage = STAGE / "startup"
    if stage.exists():
        shutil.rmtree(stage)
    pending_meshes = []
    for obj, rel in {**GESTALTS, **CLASSMODS}.items():
        dest = STATIC / "models" / rel / "model.gltf"
        if present(dest) and present(dest.parent / "model.bin"):
            log("keep " + rel + "/model.gltf")
            continue
        pending_meshes.append((obj, rel))
    if pending_meshes:
        mesh_root = export_named(
            umodel, wine, COOKED / "Startup.upk", stage / "mesh",
            ["-gltf", "-noanim", "-notex"], [obj for obj, _rel in pending_meshes],
        )
        src_dir = mesh_root / "SkeletalMesh3"
        for obj, rel in pending_meshes:
            src = src_dir / (obj + ".gltf")
            dest = STATIC / "models" / rel / "model.gltf"
            if not src.is_file():
                log("MISSING mesh " + obj)
                continue
            place_gltf(src, dest)
            log("placed " + rel + "/model.gltf")

    dests = startup_texture_dests()
    pending_tex = {name: dest for name, dest in dests.items() if not present(dest)}
    tex_dir = STAGE / "startup" / "tex" / "Startup" / "Texture2D"
    if pending_tex:
        tex_root = export_named(
            umodel, wine, COOKED / "Startup.upk", stage / "tex",
            ["-png"], sorted(pending_tex),
        )
        tex_dir = tex_root / "Texture2D"
    found = 0
    for name, dest in sorted(dests.items()):
        if present(dest):
            found += 1
            continue
        src = tex_dir / (name + ".png")
        if not src.is_file():
            log("MISSING texture " + name)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        found += 1
    log("startup textures %d/%d" % (found, len(dests)))


def export_one_mesh(umodel, wine, package, mesh, out):
    root = export_named(
        umodel, wine, package, out, ["-gltf", "-noanim", "-notex"], [mesh],
    )
    gltf = root / "SkeletalMesh3" / (mesh + ".gltf")
    if not gltf.is_file():
        log("MISSING %s %s" % (package.name, mesh))
        return None
    return gltf


def export_textures(umodel, wine, package, names, out):
    root = export_named(umodel, wine, package, out, ["-png"], names)
    return root / "Texture2D"


def export_bodies(umodel, wine):
    for pkg, kind, mesh, dif, msk, nrm, who, gltf_name in BODIES:
        package = content_dir(kind) / (pkg + ".upk")
        dest = STATIC / "models" / "characters" / who / gltf_name
        dif_dest = dest.parent / (dif + ".png")
        mask_dest = dest.parent / "body_mask.png"
        nrm_dest = dest.parent / (nrm + ".png")
        if not package.is_file():
            log("MISSING package " + str(package))
            continue
        if present(dest) and present(dif_dest) and present(mask_dest) and present(nrm_dest):
            log("keep character " + who)
            continue
        stage = STAGE / "body" / who
        if stage.exists():
            shutil.rmtree(stage)
        if not present(dest):
            gltf = export_one_mesh(umodel, wine, package, mesh, stage / "mesh")
            if gltf is None:
                continue
            place_gltf(gltf, dest)
        tex_dir = export_textures(
            umodel, wine, package, [dif, msk, nrm], stage / "tex",
        )
        dif_png = tex_dir / (dif + ".png")
        if dif_png.is_file():
            shutil.copy2(dif_png, dest.parent / dif_png.name)
            link_texture(dest, dif_png.name)
        else:
            log("MISSING %s diffuse %s" % (who, dif))
        msk_png = tex_dir / (msk + ".png")
        if msk_png.is_file():
            shutil.copy2(msk_png, mask_dest)
        elif not present(mask_dest):
            log("MISSING %s mask %s" % (who, msk))
        nrm_png = tex_dir / (nrm + ".png")
        if nrm_png.is_file():
            shutil.copy2(nrm_png, dest.parent / nrm_png.name)
        log("placed character " + who)


def export_default_heads(umodel, wine):
    for pkg, kind, mesh, who, dif_name in DEFAULT_HEADS:
        package = content_dir(kind) / (pkg + ".upk")
        dest = STATIC / "models" / "characters" / who / "head.gltf"
        dif_dest = dest.parent / (dif_name + ".png")
        if not package.is_file():
            log("MISSING head package " + str(package))
            continue
        if present(dest) and present(dif_dest):
            log("keep default head " + who)
            continue
        stage = STAGE / "defhead" / who
        if stage.exists():
            shutil.rmtree(stage)
        if not present(dest):
            gltf = export_one_mesh(umodel, wine, package, mesh, stage / "mesh")
            if gltf is None:
                continue
            place_gltf(gltf, dest)
        if present(dif_dest):
            link_texture(dest, dif_dest.name)
        else:
            tex_dir = export_textures(umodel, wine, package, [dif_name], stage / "tex")
            src = tex_dir / (dif_name + ".png")
            if src.is_file():
                shutil.copy2(src, dif_dest)
                link_texture(dest, dif_dest.name)
            else:
                log("MISSING default head diffuse for %s (%s)" % (who, dif_name))
        log("placed default head " + who)


def index_upks():
    found = {}
    for dirpath, _dirs, files in os.walk(GAMEDATA):
        for name in files:
            if not name.lower().endswith(".upk"):
                continue
            path = Path(dirpath) / name
            prev = found.get(name)
            if prev is None or ("/Compat/" in str(path) and "/Compat/" not in str(prev)):
                found[name] = path
    return found


def split_head_url(url):
    parts = url.split("/")
    # /static/models/heads/<outer>/<package>/Texture2D/<name>.png
    if len(parts) < 8 or parts[3] != "heads":
        raise ValueError(url)
    return parts[4], parts[5], parts[7].rsplit(".", 1)[0]


def export_heads(umodel, wine):
    models = json.loads((STATIC / "head_models.json").read_text(encoding="utf-8"))
    materials = json.loads((STATIC / "head_materials.json").read_text(encoding="utf-8"))
    index = index_upks()
    done = missing_pkg = missing_mesh = missing_tex = 0
    startup_names = set()
    startup_dests = []

    for asset, url in models.items():
        _outer, pkg, mesh = split_head_url(url)
        dest_gltf = ROOT / url.lstrip("/")
        package = index.get(pkg + ".upk")
        if package is None:
            log("MISSING head package " + pkg + ".upk")
            missing_pkg += 1
        elif present(dest_gltf):
            done += 1
        else:
            stage = STAGE / "head-mesh" / pkg
            if stage.exists():
                shutil.rmtree(stage)
            gltf = export_one_mesh(umodel, wine, package, mesh, stage)
            if gltf is None:
                missing_mesh += 1
            else:
                place_gltf(gltf, dest_gltf)
                done += 1
                log("placed head %d %s" % (done, mesh))

        mic = materials.get(asset) or {}
        grouped = []
        for tex_url in (mic.get("textures") or {}).values():
            if "/static/models/heads/" not in tex_url:
                continue
            folder_outer, folder, name = split_head_url(tex_url)
            dest_png = (
                STATIC / "models" / "heads" / folder_outer / folder
                / "Texture2D" / (name + ".png")
            )
            if present(dest_png):
                continue
            if folder == "Startup":
                startup_names.add(name)
                startup_dests.append((name, dest_png))
            else:
                grouped.append((name, dest_png))
        if grouped and package is not None:
            stage = STAGE / "head-tex" / pkg
            if stage.exists():
                shutil.rmtree(stage)
            tex_dir = export_textures(
                umodel, wine, package, sorted({n for n, _ in grouped}), stage,
            )
            for name, dest_png in grouped:
                src = tex_dir / (name + ".png")
                if not src.is_file():
                    log("MISSING head texture %s %s" % (pkg, name))
                    missing_tex += 1
                    continue
                dest_png.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest_png)

    if startup_names:
        stage = STAGE / "head-startup-tex"
        if stage.exists():
            shutil.rmtree(stage)
        tex_dir = export_textures(
            umodel, wine, COOKED / "Startup.upk", sorted(startup_names), stage,
        )
        for name, dest_png in startup_dests:
            src = tex_dir / (name + ".png")
            if not src.is_file():
                log("MISSING startup head texture " + name)
                missing_tex += 1
                continue
            if present(dest_png):
                continue
            dest_png.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest_png)
    log(
        "heads done %d missing_pkg %d missing_mesh %d missing_tex %d unique %d"
        % (done, missing_pkg, missing_mesh, missing_tex, len(models))
    )


def _paeth(left, up, up_left):
    estimate = left + up - up_left
    dl = abs(estimate - left)
    du = abs(estimate - up)
    dul = abs(estimate - up_left)
    if dl <= du and dl <= dul:
        return left
    if du <= dul:
        return up
    return up_left


def read_png(path):
    """Read a non-interlaced 8-bit RGB or RGBA PNG. Returns rows of bytes."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a png: " + str(path))
    pos = 8
    width = height = color = None
    idat = []
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if tag == b"IHDR":
            width, height, bit, color, _comp, _filt, inter = struct.unpack(">IIBBBBB", chunk)
            if bit != 8 or inter != 0 or color not in (2, 6):
                raise ValueError(
                    "unsupported png %s %dx%d bit=%d color=%d inter=%d"
                    % (path.name, width, height, bit, color, inter)
                )
        elif tag == b"IDAT":
            idat.append(chunk)
        elif tag == b"IEND":
            break
    raw = zlib.decompress(b"".join(idat))
    channels = 3 if color == 2 else 4
    stride = width * channels
    rows = []
    cursor = 0
    prev = bytearray(stride)
    for _y in range(height):
        filt = raw[cursor]
        cursor += 1
        row = bytearray(raw[cursor:cursor + stride])
        cursor += stride
        if filt == 1:
            for x in range(stride):
                left = row[x - channels] if x >= channels else 0
                row[x] = (row[x] + left) & 255
        elif filt == 2:
            for x in range(stride):
                row[x] = (row[x] + prev[x]) & 255
        elif filt == 3:
            for x in range(stride):
                left = row[x - channels] if x >= channels else 0
                row[x] = (row[x] + ((left + prev[x]) // 2)) & 255
        elif filt == 4:
            for x in range(stride):
                left = row[x - channels] if x >= channels else 0
                up = prev[x]
                up_left = prev[x - channels] if x >= channels else 0
                row[x] = (row[x] + _paeth(left, up, up_left)) & 255
        elif filt != 0:
            raise ValueError("bad png filter %d in %s" % (filt, path.name))
        rows.append(row)
        prev = row
    return width, height, channels, rows


def write_png(path, width, height, channels, rows):
    raw = bytearray()
    for row in rows:
        raw.append(0)
        raw.extend(row)
    compressed = zlib.compress(bytes(raw), 9)

    def chunk(tag, payload):
        return (
            struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    color = 2 if channels == 3 else 6
    ihdr = struct.pack(">IIBBBBB", width, height, 8, color, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", compressed) + chunk(b"IEND", b"")
    path.write_bytes(png)


def mean_blue(width, channels, rows):
    total = 0
    count = width * len(rows)
    for row in rows:
        for x in range(width):
            total += row[x * channels + 2]
    return total / count


def bake_bc5_normal(path):
    """Rebuild the blue channel of a BC5 normal from raw red and green.

    Skips maps that already have a blue channel, including class-mod normals.
    Does not run on character body or head textures.
    """
    width, height, channels, rows = read_png(path)
    before = mean_blue(width, channels, rows)
    if before > 30:
        log("normal already has blue  %s  meanB %.1f" % (path.name, before))
        return
    for row in rows:
        for x in range(width):
            off = x * channels
            nx = row[off] / 255.0 * 2.0 - 1.0
            ny = row[off + 1] / 255.0 * 2.0 - 1.0
            nz2 = 1.0 - nx * nx - ny * ny
            if nz2 < 0.0:
                nz2 = 0.0
            blue = int((math.sqrt(nz2) * 0.5 + 0.5) * 255.0 + 0.5)
            if blue > 255:
                blue = 255
            row[off + 2] = blue
            if channels == 4:
                row[off + 3] = 255
    write_png(path, width, height, channels, rows)
    log("baked normal Z  %s  meanB %.1f -> %.1f" % (path.name, before, mean_blue(width, channels, rows)))


def viewer_normal_files():
    text = (STATIC / "viewer3d.js").read_text(encoding="utf-8")
    names = re.findall(r'nrm:\s*"(/static/textures/[^"]+)"', text)
    return [ROOT / rel.lstrip("/") for rel in names]


def bake_normals():
    for path in viewer_normal_files():
        if not path.is_file():
            log("MISSING normal " + str(path.relative_to(ROOT)))
            continue
        bake_bc5_normal(path)


def referenced_assets():
    urls = []
    pattern = re.compile(r"/static/(?:models|textures)/[^\"'\s)]+")
    for path in ASSET_LISTS:
        if not path.is_file():
            continue
        urls.extend(pattern.findall(path.read_text(encoding="utf-8")))
    missing = []
    seen = set()
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        file_path = ROOT / url.lstrip("/")
        if not file_path.is_file() or file_path.stat().st_size == 0:
            missing.append(url)
            continue
        if file_path.suffix == ".gltf":
            data = json.loads(file_path.read_text(encoding="utf-8"))
            for buf in data.get("buffers") or []:
                uri = buf.get("uri") or ""
                if uri and "://" not in uri and not (file_path.parent / uri).is_file():
                    missing.append(url + " -> " + uri)
    return sorted(seen), missing


def check_assets():
    urls, missing = referenced_assets()
    log("viewer files referenced %d missing %d" % (len(urls), len(missing)))
    for url in missing[:40]:
        log("  missing " + url)
    if len(missing) > 40:
        log("  ... %d more" % (len(missing) - 40))
    return len(missing) == 0


def find_gamedata(explicit):
    sys.path.insert(0, str(ROOT))
    from config import _accept_game_dir, _detect_steam_dir

    candidates = []
    if explicit:
        candidates.append(explicit)
    if os.environ.get("BL2_GAMEDATA"):
        candidates.append(os.environ["BL2_GAMEDATA"])
    cfg = ROOT / "config.json"
    if cfg.is_file():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
        if data.get("game_dir"):
            candidates.append(data["game_dir"])
    detected = _detect_steam_dir()
    if detected:
        candidates.append(detected)
    for candidate in candidates:
        accepted = _accept_game_dir(candidate)
        if accepted:
            return Path(accepted)
    return None


def self_test():
    sample = CACHE / "png-self-test.png"
    sample.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        bytearray([128, 128, 0, 255, 140, 110, 0, 255]),
        bytearray([128, 100, 0, 255, 100, 128, 0, 255]),
    ]
    write_png(sample, 2, 2, 4, rows)
    width, height, channels, got = read_png(sample)
    if (width, height, channels, got) != (2, 2, 4, rows):
        sys.exit("png round trip failed")
    bake_bc5_normal(sample)
    _w, _h, _c, baked = read_png(sample)
    if baked[0][0] != 128 or baked[0][1] != 128 or baked[0][2] < 250:
        sys.exit("normal bake failed: %s" % list(baked[0]))
    pistol = STATIC / "textures" / "weapons" / "Weap_Pistols_Nrm.png"
    if pistol.is_file():
        pw, ph, pc, prows = read_png(pistol)
        blue = mean_blue(pw, pc, prows)
        log("pistol normal %dx%d meanB %.1f" % (pw, ph, blue))
        if blue < 30:
            sys.exit("pistol normal blue channel is still empty")
    sample.unlink()
    log("self-test ok")


def prepare_tools():
    wine = find_wine()
    if not wine:
        sys.exit(
            "Wine is not installed. On this Mac it is Homebrew Wine staging:\n"
            "  brew install --cask wine-staging"
        )
    ensure_wine_prefix(wine)
    umodel = ensure_umodel()
    log("umodel " + str(umodel))
    return umodel, wine


def main():
    global FORCE, GAMEDATA, COOKED
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "phase", nargs="?", default="all",
        choices=("all", "startup", "bodies", "heads", "normals", "check"),
        help="Which part to export. Default is the full viewer set.",
    )
    parser.add_argument("--force", action="store_true", help="Replace files that are already extracted.")
    parser.add_argument("--game-dir", help="Borderlands 2 folder, or the GameData folder inside the .app.")
    parser.add_argument("--self-test", action="store_true", help="Check the PNG reader and normal bake, then exit.")
    args = parser.parse_args()
    FORCE = args.force
    if args.self_test:
        self_test()
        return

    gamedata = find_gamedata(args.game_dir)
    if args.phase != "check" and args.phase != "normals":
        if gamedata is None:
            sys.exit(
                "Could not find Borderlands 2. Pass --game-dir, or set game_dir in config.json."
            )
        cooked = gamedata / "WillowGame" / "CookedPCConsole"
        if not (cooked / "Startup.upk").is_file():
            sys.exit("Startup.upk is not in " + str(cooked))
        GAMEDATA = gamedata
        COOKED = cooked
        log("game data " + str(gamedata))

    if args.phase == "check":
        sys.exit(0 if check_assets() else 1)

    if args.phase in ("all", "normals"):
        if args.phase == "normals":
            bake_normals()
            sys.exit(0 if check_assets() else 1)

    umodel, wine = prepare_tools()
    if args.phase in ("all", "startup"):
        export_startup(umodel, wine)
    if args.phase in ("all", "bodies"):
        export_bodies(umodel, wine)
        export_default_heads(umodel, wine)
    if args.phase in ("all", "heads"):
        export_heads(umodel, wine)
    if args.phase == "all":
        bake_normals()
    ok = check_assets()
    log("EXPORT_DONE" if ok else "EXPORT_DONE_WITH_MISSING")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
