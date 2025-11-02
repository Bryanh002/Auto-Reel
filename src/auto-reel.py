import os, re, json, math, textwrap, random, tempfile, requests, time
from datetime import datetime
from dotenv import load_dotenv
from openai import OpenAI
from mutagen.mp3 import MP3
from PIL import Image, ImageDraw, ImageFont
from moviepy.editor import (
    AudioFileClip, VideoFileClip, ImageClip,
    CompositeVideoClip, CompositeAudioClip, concatenate_videoclips, vfx
)
from moviepy.video.fx.all import crop
from moviepy.audio.fx.all import audio_loop, audio_fadein, audio_fadeout
from elevenlabs.client import ElevenLabs

# --- Load environment variables ---
dotenv_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(dotenv_path)

# --- Config ---
OUT_DIR = "out"
FONT = "src/assets/Video_Text.ttf"
BG_LOOP = "src/assets/BG_Video.mp4"
BG_MUSIC = "src/assets/BG_Music.wav"
SCRIPT = "src/assets/script.txt"
W, H = 1080, 1920
TARGET_LEN_S = 55

def call_llm(prompt: str) -> str:
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You create descriptions for a reel."},
            {"role": "user", "content": prompt}
        ]
    )
    return resp.choices[0].message.content.strip()

def tts_mp3(text, out_path):
    pass
"""
    client = ElevenLabs(api_key=os.getenv("ELEVEN_API_KEY"))

    audio = client.text_to_speech.convert(
        text=text,
        voice_id="nPczCjzI2devNBz1zQrb",
        model_id="eleven_multilingual_v2",
        output_format="mp3_44100_128",
    )

    with open(out_path, "wb") as f:
        for chunk in audio:
            f.write(chunk)
"""

def use_local_video(min_duration):
    video_path = BG_LOOP if os.path.isabs(BG_LOOP) else BG_LOOP
    start = random.randint(0, int(780 - min_duration))
    stop = start + min_duration
    clip = VideoFileClip(video_path)
    return clip.subclip(start, stop)

def split_lines_for_subs(text):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if len(lines) <= 2:
        lines = textwrap.wrap(text, width=70)

    total_chars = sum(len(l) for l in lines)
    est_total = TARGET_LEN_S
    pos, chunks = 0.0, []

    for l in lines:
        dur = max(0.8, (len(l) / max(16, total_chars)) * est_total)
        chunks.append((pos, pos + dur, l))
        pos += dur
    return chunks


def build_subtitles_clip(subs):
    clips = []

    for (start, end, text) in subs:
        img = Image.new("RGBA", (W, 250), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        try:
            font = ImageFont.truetype(FONT, 60)
        except Exception:
            font = ImageFont.load_default()

        # Word wrapping
        words, lines, line = text.split(), [], ""
        for w in words:
            test = (line + " " + w).strip()
            if draw.textlength(test, font=font) < W - 120:
                line = test
            else:
                lines.append(line)
                line = w
        lines.append(line)

        # Draw semi-transparent background and centered text
        y = 20
        for line in lines:
            txt_w = draw.textlength(line, font=font)
            # Background rectangle
            #rect_h = font.size + 20
            #draw.rectangle(
            #    [(0, y - 10), (W, y + rect_h - 10)],
            #    fill=(0, 0, 0, 128)
            #)
            draw.text(((W - txt_w) / 2, y), line, fill="white", font=font)
            #y += rect_h + 10

        tmpfile = tempfile.NamedTemporaryFile(delete=False, suffix=".png").name
        img.save(tmpfile, "PNG")

        dur = end - start
        clip = (
            ImageClip(tmpfile, transparent=True)
            .set_start(start)
            .set_duration(dur)
            .set_position(("center", H - 700))
        )
        clips.append(clip)

    return clips


def create_video(narration_mp3, background_src, subs, out_path):
    voice = AudioFileClip(narration_mp3)
    duration = voice.duration

    bg = background_src if isinstance(background_src, VideoFileClip) else VideoFileClip(background_src)

    target_ratio = W / H
    bg_ratio = bg.w / bg.h

    if bg_ratio > target_ratio:
        new_w = int(bg.h * target_ratio)
        x_center = bg.w / 2
        bg = crop(bg, width=new_w, height=bg.h, x_center=x_center, y_center=bg.h / 2)
    else:
        new_h = int(bg.w / target_ratio)
        y_center = bg.h / 2
        bg = crop(bg, width=bg.w, height=new_h, x_center=bg.w / 2, y_center=y_center)

    bg = bg.resize((W, H))

    # Gentle zoom for movement
    bg = bg.fx(vfx.resize, lambda t: 1.01 + 0.01 * math.sin(t / 4))

    subs_clips = build_subtitles_clip(subs)

    # Background music mix
    if BG_MUSIC and os.path.exists(BG_MUSIC):
        music = AudioFileClip(BG_MUSIC)
        music = audio_loop(music, duration=duration)
        music = audio_fadein(music.volumex(0.08), 2).audio_fadeout(2)
        final_audio = CompositeAudioClip([
            music,
            audio_fadein(voice.volumex(1.0), 0.5)
        ]).set_duration(duration)
    else:
        final_audio = voice.set_duration(duration)

    clips = [bg] + subs_clips
    comp = CompositeVideoClip(clips, size=(W, H)).set_audio(final_audio)
    comp = comp.crossfadein(0.5).crossfadeout(0.5)

    comp.write_videofile(
        out_path,
        fps=30,
        audio_codec="aac",
        codec="libx264",
        preset="medium",
        bitrate="6000k",
        threads=os.cpu_count() // 2,
        temp_audiofile=os.path.join(OUT_DIR, "temp-audio.m4a"),
        remove_temp=True,
        verbose=False,
        logger=None
    )


def generate_metadata(script_text):
    prompt = f"""Based on this short video narration, write:
    1) A TITLE (<= 90 chars)
    2) A 2-sentence DESCRIPTION with a subtle CTA
    3) 15 short HASHTAGS (platform-friendly)
    Return JSON with keys: title, description, hashtags
    Narration:
    {script_text}"""
    
    raw = call_llm(prompt)
    try:
        return json.loads(raw)
    except:
        title = raw.splitlines()[0][:90].strip()
        desc = "Quick video. If you liked it, follow for more."
        hashtags = ["#fyp", "#reels", "#shorts", "#learn", "#tech"]
        return {"title": title, "description": desc, "hashtags": hashtags}


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    
    try:
        with open(SCRIPT, 'r', encoding='utf-8') as file:
            script = file.read()
    except FileNotFoundError:
        print(f"Error: The file '{script}' was not found.")
    except Exception as e:
        print(f"An error occurred: {e}")

    nar_mp3 = os.path.join(OUT_DIR, "voice.mp3")
    tts_mp3(script, nar_mp3)
    time.sleep(10)

    audio = MP3(nar_mp3)
    audio_length = audio.info.length

    stock = use_local_video(audio_length)
    subs = split_lines_for_subs(script)

    mp4_out = os.path.join(OUT_DIR, f"reel.mp4")
    create_video(nar_mp3, stock, subs, mp4_out)

    meta = generate_metadata(script)
    meta_path = os.path.join(OUT_DIR, f"reel_description.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print("\n✅ DONE!")
    print(f"🎬 Video: {mp4_out}")
    print(f"📝 Metadata: {meta_path}")
    print(f"🕐 Duration: {audio_length:.1f}s")

if __name__ == "__main__":
    main()
