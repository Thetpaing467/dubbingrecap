import streamlit as st
import os, re, hashlib, ffmpeg, subprocess, asyncio, time
import concurrent.futures
import numpy as np
import edge_tts
from PIL import Image, ImageDraw, ImageFont
import cv2

# ==================== Config ====================
PASSWORD = "voxcpm2026"
FONT_FILE = "MyanmarPadaung.ttf"

FS, BH, BA = 30, 100, 200
ENC_PRESET = "fast"
ENC_CRF = 18
FINAL_PRESET = "ultrafast"
FINAL_CRF = 20
AUDIO_BITRATE = "128k"
EDGE_CHUNK = 400
PNG_WORKERS = 4

BOX_WIDTH_RATIO = 1.0
PADDING_Y = 15
CORNER_RADIUS = 20

TIKTOK_CYAN = "#25F4EE"
TIKTOK_MAGENTA = "#FE2C55"
TIKTOK_BLACK = "#000000"

EDGE_VOICES = {
    "female": "my-MM-NilarNeural",
    "male":   "my-MM-ThihaNeural",
}
EDGE_VOICE_FIXED = "male"

st.set_page_config(page_title="Myanmar TTS Recap", page_icon="🎬", layout="centered")

# ==================== Styling ====================
st.markdown("""
<style>
.stApp{background:linear-gradient(160deg,#0f0f23,#1a1a35,#0f0f23);color:#e8e8f0}
#MainMenu,footer,header{visibility:hidden}
.main-title{text-align:center;font-size:2.2rem;font-weight:900;
 background:linear-gradient(90deg,#ff6b9d,#c66bff,#6ba8ff);
 -webkit-background-clip:text;-webkit-text-fill-color:transparent;
 background-clip:text;margin-bottom:4px}
.main-sub{text-align:center;color:#8888aa;font-size:.9rem;margin-bottom:20px}
.stButton>button{background:linear-gradient(135deg,#667eea,#764ba2)!important;
 color:#fff!important;border:none!important;border-radius:10px!important;
 padding:12px 20px!important;font-weight:600!important;
 box-shadow:0 4px 15px rgba(102,126,234,.3)!important}
.stTextArea textarea{background:rgba(255,255,255,.04)!important;
 border:1px solid rgba(255,255,255,.1)!important;color:#fff!important;
 border-radius:10px!important}
.stFileUploader{background:rgba(255,255,255,.02);border-radius:10px;padding:8px}
.stAlert{border-radius:10px!important;border:none!important}
hr{border-color:rgba(255,255,255,.08);margin:24px 0}
</style>
""", unsafe_allow_html=True)

# ==================== Auth ====================
if "auth" not in st.session_state:
    st.session_state.auth = False

if not st.session_state.auth:
    st.markdown("<div class='main-title'>🔐 Private App</div>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1, 2, 1])
    with c2:
        pwd = st.text_input("Password", type="password", label_visibility="collapsed", placeholder="Password")
        if st.button("Login", use_container_width=True):
            if pwd == PASSWORD:
                st.session_state.auth = True
                st.rerun()
            else:
                st.error("Password မှား")
    st.stop()

# ==================== Session State ====================
if "script" not in st.session_state: st.session_state.script = ""
if "video_up_key" not in st.session_state: st.session_state.video_up_key = 0

# ==================== Utility Functions ====================
def vid_info(p):
    pr = ffmpeg.probe(p)
    v = next(s for s in pr['streams'] if s['codec_type'] == 'video')
    return int(v['width']), int(v['height']), float(pr['format']['duration'])

def t2s(s):
    ms = int(round((s - int(s)) * 1000)); tot = int(s)
    if ms >= 1000: tot += 1; ms = 0
    h, r = divmod(tot, 3600); m, sec = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"

def s2t(ts):
    ts = ts.strip()
    m = re.match(r'^(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})$', ts)
    if m:
        h, mi, se, ms = m.groups()
        return int(h)*3600 + int(mi)*60 + int(se) + int(ms.ljust(3,'0'))/1000
    return None

def render_png(text, out, fp, W, H, fs=30, pos_y=100, bh=100, ba=100,
                box_width_ratio=BOX_WIDTH_RATIO,
                padding_y=PADDING_Y, corner_radius=CORNER_RADIUS):
    img = Image.new("RGBA", (W, H), (0,0,0,0))
    d = ImageDraw.Draw(img)
    try: f = ImageFont.truetype(fp, fs)
    except: f = ImageFont.load_default()

    mc = max(15, int(W/(fs*0.9)))
    lines, cur = [], ""
    for w in text.split():
        if len(cur)+len(w)+1 <= mc: cur = cur+" "+w if cur else w
        else:
            if cur: lines.append(cur)
            cur = w
    if cur: lines.append(cur)
    if not lines: return out

    lh = int(fs * 1.3)
    text_h = len(lines) * lh
    box_w = int(W * box_width_ratio)
    box_h = text_h + padding_y * 2
    box_x = (W - box_w) // 2
    max_y = H - box_h
    box_y = int((pos_y / 100) * max_y)
    box_y = max(0, min(box_y, max_y))

    d.rounded_rectangle(
        [box_x, box_y, box_x + box_w, box_y + box_h],
        radius=corner_radius, fill=(0, 0, 0, ba)
    )

    ty = box_y + padding_y
    for ln in lines:
        bb = d.textbbox((0, 0), ln, font=f)
        lw = bb[2] - bb[0]
        lx = box_x + (box_w - lw) // 2
        for dx in [-2,-1,0,1,2]:
            for dy in [-2,-1,0,1,2]:
                d.text((lx+dx, ty+dy), ln, font=f, fill=(0,0,0,255))
        d.text((lx, ty), ln, font=f, fill=(255,255,255,255))
        ty += lh

    img.save(out, "PNG")
    return out

def scr_to_srt(scr, dur, path, mc=30):
    sents = [s.strip()+"။" for s in scr.replace("။","။|").split("|") if s.strip()]
    if not sents: return None
    parts = []
    for s in sents:
        s = s.replace("။။","။")
        if len(s) <= mc: parts.append(s)
        else:
            cur = ""
            for w in s.split():
                if len(cur)+len(w)+1 <= mc: cur = cur+" "+w if cur else w
                else:
                    if cur: parts.append(cur.strip())
                    cur = w
            if cur: parts.append(cur.strip())
    if not parts: return None
    tot = sum(len(p) for p in parts); cur = 0.0
    with open(path, "w", encoding="utf-8") as f:
        for i, p in enumerate(parts, 1):
            d = (len(p)/tot)*dur
            f.write(f"{i}\n{t2s(cur)} --> {t2s(cur+d)}\n{p}\n\n"); cur += d
    return path

def parse_srt(path):
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read().replace("\r\n","\n").replace("\r","\n")
    segs = []
    for ck in re.split(r"\n\s*\n", raw.strip()):
        ls = [l for l in ck.split("\n") if l.strip()]
        if len(ls) < 3: continue
        ts = next((l for l in ls if "-->" in l), None)
        if not ts: continue
        p = re.split(r"\s*-->\s*", ts)
        if len(p) != 2: continue
        a, b = s2t(p[0]), s2t(p[1])
        if a is None or b is None: continue
        idx = ls.index(ts); txt = " ".join(ls[idx+1:]).strip()
        if txt: segs.append({"start": a, "end": b, "text": txt})
    return segs

def normalize_script(t):
    t = re.sub(r'[\U0001F000-\U0001FFFF\u2600-\u27BF\uFE0F]', '', t)
    t = re.sub(r'[\u201c\u201d"`*_#<>\[\]{}()\uff08\uff09\u300c\u300d\u300e\u300f\u00ab\u00bb~^|\\/]', ' ', t)
    out = []
    for ln in t.splitlines():
        ln = re.sub(r"\s+", " ", ln).strip()
        if not ln: continue
        if not ln.endswith(("။", "၊", "!", "?")): ln += "။"
        out.append(ln)
    t = " ".join(out)
    t = re.sub(r"။(\s*။)+", "။", t)
    return t.strip()

def has_speech(t):
    return re.search(r"[\u1000-\u1049\u1050-\u109F\w]", t) is not None

def split_scr(t, mc=EDGE_CHUNK):
    sents = []
    for p in t.replace("။","။|").split("|"):
        p = p.strip()
        if not p: continue
        sents.append(p if p.endswith("။") else p + "။")
    out, cur = [], ""
    for s in sents:
        if len(cur)+len(s) <= mc: cur += s
        else:
            if cur: out.append(cur)
            if len(s) > mc:
                for i in range(0, len(s), mc): out.append(s[i:i+mc])
                cur = ""
            else: cur = s
    if cur: out.append(cur)
    return out

# ==================== Edge TTS Function ====================
async def _edge_tts_async(text, out_path, voice):
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(out_path)

def edge_tts_generate(text, out_path, voice=EDGE_VOICE_FIXED):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_edge_tts_async(text, out_path, voice))
    finally:
        loop.close()
    return out_path

# ==================== Video Functions ====================
def video_bypass(input_video, output_video="bypass.mp4",
                 crop_ratio=0.95, mirror=True):
    W, H, dur = vid_info(input_video)
    filters = []

    if crop_ratio != 1.0:
        cw = int(W * crop_ratio); ch = int(H * crop_ratio)
        if cw % 2 != 0: cw -= 1
        if ch % 2 != 0: ch -= 1
        cx = (W - cw) // 2; cy = (H - ch) // 2
        filters.append(f"crop={cw}:{ch}:{cx}:{cy}")

    if mirror:
        filters.append("hflip")

    vf = ",".join(filters) if filters else "null"

    cmd = [
        "ffmpeg", "-y", "-i", input_video,
        "-vf", vf,
        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
        "-c:a", "copy", output_video
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if r.returncode != 0:
        raise Exception(f"FFmpeg: {(r.stderr or '')[-300:]}")
    return output_video

def final_render(video_in, audio_in, output_video, tempo,
                 srt_path=None, fp=FONT_FILE, fs=FS, pos_y=100, bh=BH, ba=BA,
                 use_neon=True, thickness=15, speed=0.4, tail=0.5,
                 crop_ratio=0.95, mirror=True):
    W0, H0, _ = vid_info(video_in)
    pr = ffmpeg.probe(video_in)
    vs = next(s for s in pr['streams'] if s['codec_type'] == 'video')
    n, d = vs['r_frame_rate'].split('/')
    fps = float(n) / float(d)

    if crop_ratio != 1.0:
        cw = int(W0 * crop_ratio); ch = int(H0 * crop_ratio)
        if cw % 2: cw -= 1
        if ch % 2: ch -= 1
        cx = (W0 - cw) // 2; cy = (H0 - ch) // 2
    else:
        cw, ch, cx, cy = W0, H0, 0, 0
    W, H = cw, ch

    subs = []
    if srt_path:
        segs = parse_srt(srt_path)
        os.makedirs("subtitle_pngs", exist_ok=True)

        def prep(args):
            i, sg = args
            p = f"subtitle_pngs/s_{i:04d}.png"
            render_png(sg["text"], p, fp, W, H, fs, pos_y, bh, ba,
                       box_width_ratio=BOX_WIDTH_RATIO)
            rgba = cv2.imread(p, cv2.IMREAD_UNCHANGED)
            try: os.remove(p)
            except: pass
            if rgba is None: return None
            rows = np.where(rgba[:, :, 3].any(axis=1))[0]
            if len(rows) == 0: return None
            y0, y1 = int(rows[0]), int(rows[-1]) + 1
            crop = rgba[y0:y1]
            alpha = crop[:, :, 3:4].astype(np.float32) / 255.0
            pre = crop[:, :, :3].astype(np.float32) * alpha
            return {"a": sg["start"], "b": sg["end"], "y0": y0, "y1": y1,
                    "inv": 1.0 - alpha, "pre": pre}

        with concurrent.futures.ThreadPoolExecutor(max_workers=PNG_WORKERS) as ex:
            subs = [x for x in ex.map(prep, enumerate(segs)) if x]

    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
    ]
    if audio_in:
        cmd += ["-i", audio_in, "-af", f"atempo={tempo}", "-map", "0:v", "-map", "1:a"]
    else:
        cmd += ["-an"]
    cmd += ["-c:v", "libx264", "-crf", str(FINAL_CRF), "-preset", FINAL_PRESET,
            "-pix_fmt", "yuv420p", "-threads", "0"]
    if audio_in:
        cmd += ["-c:a", "aac", "-b:a", AUDIO_BITRATE, "-shortest"]
    cmd += [output_video]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    cap = cv2.VideoCapture(video_in)
    i = 0; j = 0; k = 0
    try:
        while True:
            if not cap.grab(): break
            t_in = i / fps
            i += 1
            ok, fr = cap.retrieve()
            if not ok: break
            t = j / fps
            fr = fr[cy:cy + ch, cx:cx + cw]
            if mirror: fr = fr[:, ::-1]
            fr = np.ascontiguousarray(fr)

            while k < len(subs) and subs[k]["b"] < t: k += 1
            if k < len(subs) and subs[k]["a"] <= t:
                sb = subs[k]
                reg = fr[sb["y0"]:sb["y1"]].astype(np.float32) * sb["inv"] + sb["pre"]
                fr[sb["y0"]:sb["y1"]] = reg.astype(np.uint8)

            proc.stdin.write(fr.tobytes())
            j += 1
    finally:
        cap.release()
        try: proc.stdin.close()
        except: pass
        proc.wait()
    if proc.returncode != 0:
        raise Exception("Final render: ffmpeg fail")
    return output_video

# ==================== Main App ====================
st.markdown("<div class='main-title'>🎬 Myanmar TTS Recap</div>", unsafe_allow_html=True)
st.markdown("<div class='main-sub'>Edge TTS Version</div>", unsafe_allow_html=True)

script = st.text_area("Script", value=st.session_state.script, height=200)
st.session_state.script = script

voice_option = st.selectbox("Voice", ["male", "female"])
voice = EDGE_VOICES[voice_option]

video_file = st.file_uploader("Video", type=["mp4", "mov", "avi"], key=f"video_{st.session_state.video_up_key}")

if video_file:
    with open("input_video.mp4", "wb") as f:
        f.write(video_file.read())
    st.success("Video Uploaded")

if st.button("Generate", use_container_width=True):
    if not script:
        st.error("Script Empty")
    elif not video_file:
        st.error("Video Empty")
    else:
        with st.spinner("Processing..."):
            norm_script = normalize_script(script)
            audio_path = "output_audio.mp3"
            edge_tts_generate(norm_script, audio_path, voice)
            W, H, dur = vid_info("input_video.mp4")
            srt_path = scr_to_srt(norm_script, dur, "output.srt")
            final_render(
                "input_video.mp4", audio_path, "output_video.mp4",
                tempo=1.0, srt_path=srt_path,
                fp=FONT_FILE, fs=FS, pos_y=100, bh=BH, ba=BA,
                use_neon=True, thickness=15, speed=0.4, tail=0.5,
                crop_ratio=0.95, mirror=True
            )
            st.success("Done!")
            st.video("output_video.mp4")
