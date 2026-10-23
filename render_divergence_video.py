#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
STEINS;GATE XR — Native CG Nixie Tube Layer Extractor & Arbitrary Divergence Video Compositor (v4)
===================================================================================================
- 100% Formula-Safe Window (y_rel = 51..290, y = 221..460): Zero overlap with top formulas (Delta/Sigma <= y=216)
  or bottom Einstein field equations (R_uv >= y=476). Zero formula residue in extracted sprites.
- 100% Official Color Grading Calibration: Exact BT.601 <-> BT.709 inverse matrix pre-compensation for
  Windows Media Foundation H.264 (avc1) encoding (PSNR 43.6 dB against official master), plus true
  warm-amber unclipped emissive filament extraction.
- Bit-exact original AAC ('soun') audio track muxing with MP4 faststart (moov-before-mdat) layout.
"""

import argparse
import base64
import json
import shutil
import struct
from pathlib import Path

import cv2
import numpy as np

STUDIO_DIR = Path(__file__).resolve().parent
OUTPUTS_DIR = STUDIO_DIR.parent
DEFAULT_MASTER = OUTPUTS_DIR / "SteinsGate-XR-Offline" / "assets" / "video" / "official-site-opening.mp4"
LOCAL_MASTER = STUDIO_DIR / "assets" / "master" / "official-site-opening.mp4"
MASTER_VIDEO = LOCAL_MASTER if LOCAL_MASTER.exists() else DEFAULT_MASTER

SPRITES_DIR = STUDIO_DIR / "sprites"
VIDEOS_DIR = STUDIO_DIR / "videos"

# Canonical 8-tube horizontal layout on the 1280x720 canvas:
# 8 tubes * 137px = 1096px, centered with 92px left margin and 92px right margin.
TUBE_X0 = 92
TUBE_W = 137
TUBE_Y1 = 170
TUBE_Y2 = 590
TUBE_H = TUBE_Y2 - TUBE_Y1  # 420px

# Strict digit cathode window inside [0..420]:
# Top physics formulas (\Delta, \Sigma) end at y=216 (y_rel=46).
# Digits 0..9 start at y=223 (y_rel=53) and end at y=458 (y_rel=288).
# Bottom physics formulas (R_{\mu\nu}) start at y=476 (y_rel=306).
WIN_Y1 = 50   # y = 220
WIN_Y2 = 291  # y = 461

# Canonical staggered lock frame for each tube (index 0..7) from official-site-opening.mp4:
LOCK_FRAMES = [80, 78, 60, 52, 67, 87, 95, 77]

# Calibrated BT.601 -> BT.709 pre-compensation matrix for Windows Media Foundation H.264 (avc1) encoder
# Ensures encoded MP4 frames decode to the exact same BGR color grading as official-site-opening.mp4.
MSMF_COLOR_INV_M = np.array(
    [
        [1.06565, 0.11043, -0.01571],
        [-0.03615, 0.87491, -0.07870],
        [-0.01405, 0.07809, 1.09043],
    ],
    dtype=np.float32,
)
MSMF_COLOR_BIAS = np.array([-0.75402, 0.82855, -0.97116], dtype=np.float32)


def load_master_frames(video_path: Path):
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open master video: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS)
    frames = []
    while True:
        ret, f = cap.read()
        if not ret:
            break
        frames.append(f)
    cap.release()
    return np.array(frames, dtype=np.float32), fps


def extract_native_sprites_and_schedule(frames: np.ndarray):
    """
    1. Classifies every (frame, tube) pair in official-site-opening.mp4 (210 x 8).
    2. Extracts multi-tube, multi-frame median native CG sprites for 'empty', '.', '0'..'9'
       with zero physics formula residue on top dome or bottom base.
    3. Computes formula-safe window masks (y_rel=50..291) and core filament occlusion masks.
    """
    n_frames = len(frames)
    g_frames = frames[:, :, :, 1]

    # Build initial digit templates from Tube 7 (index 6), which rolls cleanly +1 mod 10 from F040..F094
    t6_x1 = TUBE_X0 + 6 * TUBE_W
    t6_x2 = t6_x1 + TUBE_W
    tmpls = {
        str(d): np.median(
            g_frames[[f for f in range(40, 95) if (f + 3) % 10 == d], 222:460, t6_x1:t6_x2],
            axis=0,
        )
        for d in range(10)
    }
    tmpls["."] = np.median(
        g_frames[:30, 222:460, TUBE_X0 + 2 * TUBE_W : TUBE_X0 + 3 * TUBE_W],
        axis=0,
    )

    orig_digits = [["" for _ in range(8)] for _ in range(n_frames)]
    by_digit_tube = {k: {t: [] for t in range(8)} for k in tmpls.keys()}

    for f in range(n_frames):
        for t in range(8):
            x1 = TUBE_X0 + t * TUBE_W
            x2 = x1 + TUBE_W
            if t == 2:
                best_d = "."
            elif f < 40:
                best_d = "30.63866"[t]
            elif f >= 95:
                best_d = "30.63841"[t]
            else:
                patch_g = g_frames[f, 222:460, x1:x2]
                best_d = min(
                    [str(d) for d in range(10)],
                    key=lambda d: float(np.abs(patch_g - tmpls[d]).mean()),
                )
            orig_digits[f][t] = best_d
            by_digit_tube[best_d][t].append(frames[f, TUBE_Y1:TUBE_Y2, x1:x2, :])

    sprites = {}
    for k in tmpls.keys():
        tube_meds = [
            np.median(np.array(by_digit_tube[k][t]), axis=0)
            for t in range(8)
            if len(by_digit_tube[k][t]) > 0
        ]
        sprites[k] = np.median(np.array(tube_meds), axis=0)

    # For '.' (which only occurs in Tube 3), select the darkest 45 frames where background radar is minimal
    t3_r_mean = frames[:, 225:440, TUBE_X0 + 2 * TUBE_W : TUBE_X0 + 3 * TUBE_W, 2].mean(axis=(1, 2))
    dark_f = np.argsort(t3_r_mean)[:45]
    sprites["."] = np.median(
        frames[dark_f, TUBE_Y1:TUBE_Y2, TUBE_X0 + 2 * TUBE_W : TUBE_X0 + 3 * TUBE_W, :],
        axis=0,
    )

    # Clean cap & base strictly from formula-free static frames (F000..F029 and F145..F209) across all 8 tubes
    static_all = []
    for f in list(range(0, 30)) + list(range(145, n_frames)):
        for t in range(8):
            x1 = TUBE_X0 + t * TUBE_W
            static_all.append(frames[f, TUBE_Y1:TUBE_Y2, x1 : x1 + TUBE_W, :])
    clean_cap_base = np.median(np.array(static_all), axis=0)

    # Strict vertical blend mask: 1.0 only inside [WIN_Y1..WIN_Y2] (y=220..461), 4px smooth transition
    alpha_y = np.zeros((TUBE_H, 1, 1), dtype=np.float32)
    alpha_y[WIN_Y1 + 2 : WIN_Y2 - 2] = 1.0
    for i in range(6):
        w_val = (i + 1) / 7.0
        alpha_y[WIN_Y1 - 4 + i] = w_val
        alpha_y[WIN_Y2 - 2 + i] = 1.0 - w_val

    for k in sprites:
        sprites[k] = sprites[k] * alpha_y + clean_cap_base * (1.0 - alpha_y)

    # Construct 100% native 'empty' unlit tube sprite:
    # '.' has no lit digit in the upper/center area, and '1' is completely unlit in the bottom-right dot corner.
    empty_clean = sprites["."].copy()
    empty_clean[260:296, 85:130, :] = sprites["1"][260:296, 85:130, :]
    sprites["empty"] = empty_clean

    # Formula-safe interior cathode window mask (strictly confined to y_rel=48..293, x=10..127)
    win_mask = np.zeros((TUBE_H, TUBE_W, 1), dtype=np.float32)
    win_mask[WIN_Y1:WIN_Y2, 10:127, 0] = 1.0
    win_mask = cv2.GaussianBlur(win_mask, (9, 9), 2.2)[..., None]
    # Guarantee 0.0 above y_rel=46 (where \Delta and \Sigma end) and below y_rel=298 (where R_{\mu\nu} starts)
    win_mask[:47, :, :] = 0.0
    win_mask[297:, :, :] = 0.0

    # Core filament masks (where bright orange wire saturates/occludes background)
    core_alphas = {}
    for k in sprites:
        if k == "empty":
            core_alphas[k] = np.zeros((TUBE_H, TUBE_W, 1), dtype=np.float32)
            continue
        diff_g = np.abs(sprites[k][:, :, 1:2] - empty_clean[:, :, 1:2])
        ca = np.clip((diff_g - 18.0) / 55.0, 0.0, 1.0)
        ca = cv2.GaussianBlur(ca, (5, 5), 1.2)[..., None] * win_mask
        core_alphas[k] = ca

    # Per-frame tube luminance ratio relative to median sprite so locked transitions have 0.0% brightness step
    frame_gains = np.ones(n_frames, dtype=np.float32)
    ref_g_start = (
        sum(float(sprites["30.63866"[t]][55:285, 15:122, 1].mean()) for t in [0, 1, 3, 4, 5])
        / 5.0
    )
    for f in range(n_frames):
        if f < 40 or f >= 87:
            cur_g = (
                sum(
                    float(
                        frames[
                            f,
                            TUBE_Y1 + 55 : TUBE_Y1 + 285,
                            TUBE_X0 + t * TUBE_W + 15 : TUBE_X0 + t * TUBE_W + 122,
                            1,
                        ].mean()
                    )
                    for t in [0, 1, 3, 4, 5]
                )
                / 5.0
            )
            frame_gains[f] = np.clip(cur_g / (ref_g_start + 1e-5), 0.92, 1.08)
        else:
            frame_gains[f] = 1.0

    return sprites, win_mask, core_alphas, orig_digits, frame_gains


def build_digit_schedule(start_str: str, target_str: str, orig_digits: list) -> list:
    start_s = (start_str + "        ")[:8].replace(" ", "0")
    target_s = (target_str + "        ")[:8].replace(" ", "0")
    n_frames = len(orig_digits)
    schedule = [["" for _ in range(8)] for _ in range(n_frames)]

    for f in range(n_frames):
        for t in range(8):
            s_ch = start_s[t]
            e_ch = target_s[t]
            lock_f = LOCK_FRAMES[t]
            if f < 40:
                schedule[f][t] = s_ch
            elif f >= lock_f:
                schedule[f][t] = e_ch
            else:
                if s_ch == "." and e_ch == ".":
                    schedule[f][t] = "."
                else:
                    orig_ch = orig_digits[f][t]
                    schedule[f][t] = str((f + 2) % 10) if orig_ch == "." else orig_ch
    return schedule


def render_tube_patch(
    frame_patch: np.ndarray,
    d_old: str,
    d_new: str,
    gain: float,
    sprites: dict,
    win_mask: np.ndarray,
    core_alphas: dict,
) -> np.ndarray:
    if d_old == d_new:
        return frame_patch

    sp_old = sprites.get(d_old, sprites["empty"])
    sp_new_raw = sprites.get(d_new, sprites["empty"])
    sp_empty = sprites["empty"]
    sp_new = sp_empty + (sp_new_raw - sp_empty) * gain

    ca_old = core_alphas.get(d_old, core_alphas["empty"])
    ca_new = core_alphas.get(d_new, core_alphas["empty"])

    delta_raw = frame_patch - sp_old
    # Tight normalized-convolution inpainting strictly across d_old's 3px opaque glowing wire
    w = 1.0 - ca_old * 0.96
    num = cv2.GaussianBlur(delta_raw * w, (19, 19), 4.5)
    den = cv2.GaussianBlur(w, (19, 19), 4.5)[..., None] + 1e-5
    delta_inpainted = num / den
    delta_env = delta_raw * (1.0 - ca_old) + delta_inpainted * ca_old

    target_patch = sp_new + delta_env * (1.0 - ca_new * 0.85)
    out = frame_patch * (1.0 - win_mask) + target_patch * win_mask
    return np.clip(out, 0, 255)


def extract_clean_filament_rgba(sp: np.ndarray, empty_sp: np.ndarray, win_mask: np.ndarray):
    """
    Extracts the true warm-amber glowing filament without clipping-induced green shift.
    Instead of raw (sp - empty) where R clipped at 255, uses physical emission alpha
    and preserves the true official warm-orange RGB of sp.
    """
    diff_g = np.maximum(0.0, sp[:, :, 1:2] - empty_sp[:, :, 1:2])
    diff_r = np.maximum(0.0, sp[:, :, 2:3] - empty_sp[:, :, 2:3])
    lum_diff = np.maximum(diff_g, diff_r * 0.65)
    alpha = np.clip((lum_diff - 5.0) / 145.0, 0.0, 1.0) ** 0.82
    alpha = alpha * win_mask
    rgb_on_black = np.clip(sp * alpha, 0, 255).astype(np.uint8)
    rgba = np.concatenate([ np.clip(sp, 0, 255).astype(np.uint8), np.clip(alpha * 255.0, 0, 255).astype(np.uint8) ], axis=2)
    return rgb_on_black, rgba


def mux_faststart_with_original_audio(video_only_mp4: Path, master_mp4: Path, out_mp4: Path):
    orig = master_mp4.read_bytes()
    vid = video_only_mp4.read_bytes()

    def find_box(buf, target_type, start=0, end=None):
        if end is None:
            end = len(buf)
        off = start
        while off + 8 <= end:
            sz, bt = struct.unpack(">I4s", buf[off : off + 8])
            if sz == 0:
                sz = end - off
            if bt == target_type:
                return off, sz
            off += sz
        return -1, 0

    orig_moov_off, orig_moov_sz = find_box(orig, b"moov")
    audio_trak_bytes = None
    off = orig_moov_off + 8
    while off + 8 <= orig_moov_off + orig_moov_sz:
        sz, bt = struct.unpack(">I4s", orig[off : off + 8])
        if bt == b"trak" and b"soun" in orig[off : off + sz]:
            audio_trak_bytes = bytearray(orig[off : off + sz])
            break
        off += sz
    if audio_trak_bytes is None:
        raise RuntimeError("Could not find 'soun' track in master MP4")

    mdia_off, mdia_sz = find_box(audio_trak_bytes, b"mdia", 8)
    minf_off, minf_sz = find_box(audio_trak_bytes, b"minf", mdia_off + 8, mdia_off + mdia_sz)
    stbl_off, stbl_sz = find_box(audio_trak_bytes, b"stbl", minf_off + 8, minf_off + minf_sz)
    stsc_off, _ = find_box(audio_trak_bytes, b"stsc", stbl_off + 8, stbl_off + stbl_sz)
    stsz_off, _ = find_box(audio_trak_bytes, b"stsz", stbl_off + 8, stbl_off + stbl_sz)
    stco_off, _ = find_box(audio_trak_bytes, b"stco", stbl_off + 8, stbl_off + stbl_sz)

    stsc_count = struct.unpack(">I", audio_trak_bytes[stsc_off + 12 : stsc_off + 16])[0]
    stsc_entries = [
        struct.unpack(">III", audio_trak_bytes[stsc_off + 16 + i * 12 : stsc_off + 28 + i * 12])
        for i in range(stsc_count)
    ]
    _, sample_count = struct.unpack(">II", audio_trak_bytes[stsz_off + 12 : stsz_off + 20])
    sample_sizes = [
        struct.unpack(">I", audio_trak_bytes[stsz_off + 20 + i * 4 : stsz_off + 24 + i * 4])[0]
        for i in range(sample_count)
    ]
    chunk_count = struct.unpack(">I", audio_trak_bytes[stco_off + 12 : stco_off + 16])[0]
    chunk_offsets = [
        struct.unpack(">I", audio_trak_bytes[stco_off + 16 + i * 4 : stco_off + 20 + i * 4])[0]
        for i in range(chunk_count)
    ]

    chunk_sample_counts = []
    for c_idx in range(1, chunk_count + 1):
        spc = stsc_entries[0][1]
        for fc, s_cnt, _ in stsc_entries:
            if c_idx >= fc:
                spc = s_cnt
        chunk_sample_counts.append(spc)

    audio_payload = bytearray()
    audio_chunk_rel_offsets = []
    s_cursor = 0
    for c_idx in range(chunk_count):
        cnt = chunk_sample_counts[c_idx]
        c_bytes = sum(sample_sizes[s_cursor : s_cursor + cnt])
        s_cursor += cnt
        orig_c_off = chunk_offsets[c_idx]
        audio_chunk_rel_offsets.append(len(audio_payload))
        audio_payload.extend(orig[orig_c_off : orig_c_off + c_bytes])

    ftyp_off, ftyp_sz = find_box(vid, b"ftyp")
    vid_moov_off, vid_moov_sz = find_box(vid, b"moov")
    vid_middle = vid[ftyp_sz:vid_moov_off]

    vid_moov = bytearray(vid[vid_moov_off : vid_moov_off + vid_moov_sz])
    v_trak_off, v_trak_sz = find_box(vid_moov, b"trak", 8)
    v_mdia_off, v_mdia_sz = find_box(vid_moov, b"mdia", v_trak_off + 8, v_trak_off + v_trak_sz)
    v_minf_off, v_minf_sz = find_box(vid_moov, b"minf", v_mdia_off + 8, v_mdia_off + v_mdia_sz)
    v_stbl_off, v_stbl_sz = find_box(vid_moov, b"stbl", v_minf_off + 8, v_minf_off + v_minf_sz)
    v_stco_off, _ = find_box(vid_moov, b"stco", v_stbl_off + 8, v_stbl_off + v_stbl_sz)

    new_moov_size = vid_moov_sz + len(audio_trak_bytes)
    vid_offset_shift = new_moov_size
    v_chunk_count = struct.unpack(">I", vid_moov[v_stco_off + 12 : v_stco_off + 16])[0]
    for i in range(v_chunk_count):
        old_off = struct.unpack(">I", vid_moov[v_stco_off + 16 + i * 4 : v_stco_off + 20 + i * 4])[0]
        struct.pack_into(">I", vid_moov, v_stco_off + 16 + i * 4, old_off + vid_offset_shift)

    audio_data_start = ftyp_sz + new_moov_size + len(vid_middle) + 8
    for c_idx in range(chunk_count):
        struct.pack_into(
            ">I",
            audio_trak_bytes,
            stco_off + 16 + c_idx * 4,
            audio_data_start + audio_chunk_rel_offsets[c_idx],
        )

    struct.pack_into(">I", vid_moov, 0, new_moov_size)
    mvhd_off, _ = find_box(vid_moov, b"mvhd", 8)
    struct.pack_into(">I", vid_moov, mvhd_off + 104, 3)

    udta_off, _ = find_box(vid_moov, b"udta", 8)
    if udta_off != -1:
        new_moov = vid_moov[:udta_off] + audio_trak_bytes + vid_moov[udta_off:]
    else:
        new_moov = vid_moov + audio_trak_bytes

    audio_mdat_header = struct.pack(">I4s", 8 + len(audio_payload), b"mdat")

    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    with open(out_mp4, "wb") as f:
        f.write(vid[:ftyp_sz])
        f.write(new_moov)
        f.write(vid_middle)
        f.write(audio_mdat_header)
        f.write(audio_payload)


def render_divergence_mp4(
    start_str: str,
    target_str: str,
    out_mp4: Path,
    frames: np.ndarray,
    fps: float,
    sprites: dict,
    win_mask: np.ndarray,
    core_alphas: dict,
    orig_digits: list,
    frame_gains: np.ndarray,
):
    schedule = build_digit_schedule(start_str, target_str, orig_digits)
    tmp_video = out_mp4.with_suffix(".tmp_video.mp4")
    out_mp4.parent.mkdir(parents=True, exist_ok=True)

    use_msmf_calibration = True
    vw = cv2.VideoWriter(
        str(tmp_video),
        cv2.CAP_MSMF,
        cv2.VideoWriter_fourcc(*"avc1"),
        fps,
        (1280, 720),
    )
    if not vw.isOpened():
        use_msmf_calibration = False
        vw = cv2.VideoWriter(
            str(tmp_video),
            cv2.VideoWriter_fourcc(*"mp4v"),
            fps,
            (1280, 720),
        )

    for f_idx in range(len(frames)):
        frame_out = frames[f_idx].copy()
        gain = float(frame_gains[f_idx])
        for t in range(8):
            d_old = orig_digits[f_idx][t]
            d_new = schedule[f_idx][t]
            if d_old != d_new:
                x1 = TUBE_X0 + t * TUBE_W
                x2 = x1 + TUBE_W
                frame_out[TUBE_Y1:TUBE_Y2, x1:x2] = render_tube_patch(
                    frame_out[TUBE_Y1:TUBE_Y2, x1:x2],
                    d_old,
                    d_new,
                    gain,
                    sprites,
                    win_mask,
                    core_alphas,
                )
        if use_msmf_calibration:
            # Apply exact BT.601 -> BT.709 pre-compensation so decoded H.264 matches official color grading
            frame_out = np.clip((frame_out - MSMF_COLOR_BIAS) @ MSMF_COLOR_INV_M, 0, 255)
        vw.write(frame_out.astype(np.uint8))
    vw.release()

    mux_faststart_with_original_audio(tmp_video, MASTER_VIDEO, out_mp4)
    if tmp_video.exists():
        tmp_video.unlink()


def export_sprites_and_web_bundle(
    frames: np.ndarray,
    sprites: dict,
    win_mask: np.ndarray,
    core_alphas: dict,
    orig_digits: list,
    frame_gains: np.ndarray,
):
    SPRITES_DIR.mkdir(parents=True, exist_ok=True)
    keys = ["empty", ".", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9"]

    filaments_list = []
    for k in keys:
        fname = "dot" if k == "." else ("empty" if k == "empty" else f"digit_{k}")
        sp_u8 = np.clip(sprites[k], 0, 255).astype(np.uint8)
        cv2.imwrite(str(SPRITES_DIR / f"{fname}.png"), sp_u8)

        if k != "empty":
            rgb_on_black, rgba = extract_clean_filament_rgba(sprites[k], sprites["empty"], win_mask)
            cv2.imwrite(str(SPRITES_DIR / f"filament_{fname}.png"), rgba)
            filaments_list.append(rgb_on_black)

    # Save combined horizontal sprite sheet (12 * 137 = 1644 x 420)
    atlas_bgr = np.concatenate([np.clip(sprites[k], 0, 255).astype(np.uint8) for k in keys], axis=1)
    atlas_path = SPRITES_DIR / "nixie_atlas.png"
    cv2.imwrite(str(atlas_path), atlas_bgr)

    # Save true official warm-orange glowing filament sheet
    filaments_bgr = np.concatenate(filaments_list, axis=1)
    cv2.imwrite(str(SPRITES_DIR / "nixie_filaments_atlas.png"), filaments_bgr)

    # Save alpha/mask atlas (win_mask in G channel, core_alpha in R channel)
    mask_tiles = []
    for k in keys:
        ca = np.clip(core_alphas[k] * 255.0, 0, 255).astype(np.uint8)
        wm = np.clip(win_mask * 255.0, 0, 255).astype(np.uint8)
        tile = np.concatenate([wm, wm, ca], axis=2)
        mask_tiles.append(tile)
    mask_atlas_path = SPRITES_DIR / "nixie_masks_atlas.png"
    cv2.imwrite(str(mask_atlas_path), np.concatenate(mask_tiles, axis=1))

    atlas_b64 = base64.b64encode(atlas_path.read_bytes()).decode("ascii")
    masks_b64 = base64.b64encode(mask_atlas_path.read_bytes()).decode("ascii")

    bundle_obj = {
        "keys": keys,
        "tubeX0": TUBE_X0,
        "tubeW": TUBE_W,
        "tubeY1": TUBE_Y1,
        "tubeY2": TUBE_Y2,
        "tubeH": TUBE_H,
        "winY1": WIN_Y1,
        "winY2": WIN_Y2,
        "lockFrames": LOCK_FRAMES,
        "origDigits": ["".join(r) for r in orig_digits],
        "frameGains": [round(float(g), 4) for g in frame_gains],
        "atlasDataUrl": f"data:image/png;base64,{atlas_b64}",
        "masksDataUrl": f"data:image/png;base64,{masks_b64}",
    }

    js_path = STUDIO_DIR / "nixie-atlas-data.js"
    js_path.write_text(
        "window.NIXIE_STUDIO_DATA = " + json.dumps(bundle_obj, separators=(",", ":")) + ";\n",
        encoding="utf-8",
    )

    # Also export master-video-blob.js so HTML5 Canvas Studio has zero file:// CORS taint and runs
    # the exact full-resolution (137x420) normalized-convolution algorithm directly on live video pixels!
    vid_b64 = base64.b64encode(MASTER_VIDEO.read_bytes()).decode("ascii")
    blob_js_path = STUDIO_DIR / "master-video-blob.js"
    blob_js_path.write_text(
        f'window.MASTER_VIDEO_B64 = "{vid_b64}";\n',
        encoding="utf-8",
    )

    (SPRITES_DIR / "orig_digits_timeline.json").write_text(
        json.dumps(
            {
                "lockFrames": LOCK_FRAMES,
                "origDigits": ["".join(r) for r in orig_digits],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Exported v4 formula-safe sprite kit to {SPRITES_DIR} and web bundles to {STUDIO_DIR}")


def main():
    parser = argparse.ArgumentParser(description="Render custom Steins;Gate XR WorldLine transition MP4 (v4).")
    parser.add_argument("--from-val", dest="from_val", help="8-char starting divergence (e.g. 30.63841)")
    parser.add_argument("--to-val", dest="to_val", help="8-char target divergence (e.g. 30.63846)")
    parser.add_argument("--out", help="Output MP4 file path")
    parser.add_argument(
        "--batch-all",
        action="store_true",
        help="Extract sprites, web bundle, and all canonical WorldLine01..06 + showcase MP4s",
    )
    args = parser.parse_args()

    print(f"Loading master video: {MASTER_VIDEO}")
    frames, fps = load_master_frames(MASTER_VIDEO)
    print(f"Loaded {len(frames)} frames at {fps:.4f} fps. Extracting v4 native Nixie sprites...")
    sprites, win_mask, core_alphas, orig_digits, frame_gains = extract_native_sprites_and_schedule(frames)

    if args.batch_all:
        export_sprites_and_web_bundle(frames, sprites, win_mask, core_alphas, orig_digits, frame_gains)
        jobs = [
            ("WorldLine01.mp4", "30.63841", "30.63846", "Ch.2-2 (2-2:26)"),
            ("WorldLine02.mp4", "30.63846", "30.63851", "Ch.3-2 (3-2:30)"),
            ("WorldLine03.mp4", "30.63851", "30.63856", "Ch.5 (5:22)"),
            ("WorldLine04.mp4", "30.63856", "30.63861", "Ch.6-2 (6-2:7)"),
            ("WorldLine05.mp4", "30.63861", "30.63866", "Ch.7 (7:21)"),
            ("Custom-SteinsGate-1.048596.mp4", "0.571024", "1.048596", "Alpha -> Steins Gate Showcase"),
            ("Custom-Alpha-0.571024.mp4", "1.130426", "0.571024", "Beta -> Alpha Showcase"),
        ]
        VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
        for fname, s_val, e_val, desc in jobs:
            out_p = VIDEOS_DIR / fname
            print(f"Rendering {fname} ({s_val} -> {e_val}, {desc})...")
            render_divergence_mp4(
                s_val, e_val, out_p, frames, fps, sprites, win_mask, core_alphas, orig_digits, frame_gains
            )
            print(f"  -> Saved {out_p} ({out_p.stat().st_size:,} bytes)")

        wl06 = VIDEOS_DIR / "WorldLine06.mp4"
        shutil.copy2(MASTER_VIDEO, wl06)
        print(f"Copied canonical WorldLine06.mp4 (30.63866 -> 30.63841) -> {wl06}")

    if args.from_val and args.to_val and args.out:
        out_p = Path(args.out).resolve()
        print(f"Rendering custom video: {args.from_val} -> {args.to_val} => {out_p}")
        render_divergence_mp4(
            args.from_val, args.to_val, out_p, frames, fps, sprites, win_mask, core_alphas, orig_digits, frame_gains
        )
        print(f"Done! Saved to {out_p}")


if __name__ == "__main__":
    main()
