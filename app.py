import os
import shutil
import subprocess
import uuid
from pathlib import Path

import yt_dlp
from flask import Flask, redirect, render_template, request, session, url_for
from pydub import AudioSegment
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-voiceremover-secret")

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_FOLDER = BASE_DIR / "uploads"
STATIC_FOLDER = BASE_DIR / "static"
RESULT_FOLDER = BASE_DIR / "result"
LIBRARY_FOLDER = BASE_DIR / "library"
ALLOWED_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}

for folder in (UPLOAD_FOLDER, STATIC_FOLDER, LIBRARY_FOLDER):
    folder.mkdir(parents=True, exist_ok=True)


def srt_time(seconds):
    millis = int(round(seconds * 1000))
    hours, rem = divmod(millis, 3600000)
    minutes, rem = divmod(rem, 60000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{ms:03}"


def generate_simple_srt(text, total_duration, output_path):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return False

    block = total_duration / len(lines)
    with open(output_path, "w", encoding="utf-8") as f:
        for idx, line in enumerate(lines, start=1):
            start = (idx - 1) * block
            end = total_duration if idx == len(lines) else idx * block
            f.write(f"{idx}\n{srt_time(start)} --> {srt_time(end)}\n{line}\n\n")
    return True


def convert_to_mp3(source_path, out_path):
    audio = AudioSegment.from_file(source_path)
    audio.export(out_path, format="mp3")


def list_internal_tracks():
    return sorted(
        [f.name for f in LIBRARY_FOLDER.iterdir() if f.is_file() and f.suffix.lower() in ALLOWED_AUDIO_EXTS]
    )


@app.route('/', methods=['GET', 'POST'])
def index():
    error = None
    tracks = list_internal_tracks()
    input_source = "youtube"
    output_mode = "both"
    if request.method == 'POST':
        input_source = request.form.get("input_source", "youtube").strip()
        output_mode = request.form.get("output_mode", "both").strip()
        if output_mode not in {"music", "vocal", "both"}:
            output_mode = "both"

        yt_link = request.form.get('youtube_link', '').strip()
        file = request.files.get('file')
        internal_track = request.form.get("internal_track", "").strip()

        track_id = f"track_{uuid.uuid4().hex[:10]}"
        filepath = UPLOAD_FOLDER / f"{track_id}.mp3"

        if input_source == "youtube":
            if not yt_link:
                error = "Masukkan link YouTube terlebih dahulu."
                return render_template(
                    'index.html',
                    error=error,
                    tracks=tracks,
                    input_source=input_source,
                    output_mode=output_mode
                )
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': str(UPLOAD_FOLDER / track_id),
                'postprocessors': [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3','preferredquality': '192'}],
            }
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([yt_link])
                return redirect(url_for('process', track=track_id, output=output_mode))
            except Exception:
                error = "Gagal mengunduh dari YouTube. Periksa link lalu coba lagi."
        elif input_source == "upload":
            if not (file and file.filename):
                error = "Upload file audio terlebih dahulu."
                return render_template(
                    'index.html',
                    error=error,
                    tracks=tracks,
                    input_source=input_source,
                    output_mode=output_mode
                )
            temp_name = secure_filename(file.filename)
            temp_path = UPLOAD_FOLDER / f"{track_id}_{temp_name}"
            try:
                file.save(temp_path)
                convert_to_mp3(temp_path, filepath)
                temp_path.unlink(missing_ok=True)
                return redirect(url_for('process', track=track_id, output=output_mode))
            except Exception:
                error = "File audio gagal diproses. Gunakan format audio umum (mp3/wav/m4a)."
                temp_path.unlink(missing_ok=True)
        elif input_source == "internal":
            if internal_track not in tracks:
                error = "Pilih musik dari folder internal yang tersedia."
                return render_template(
                    'index.html',
                    error=error,
                    tracks=tracks,
                    input_source=input_source,
                    output_mode=output_mode
                )
            try:
                convert_to_mp3(LIBRARY_FOLDER / internal_track, filepath)
                return redirect(url_for('process', track=track_id, output=output_mode))
            except Exception:
                error = "Musik internal gagal diproses."
        else:
            error = "Sumber audio tidak valid."

    return render_template(
        'index.html',
        error=error,
        tracks=tracks,
        input_source=input_source,
        output_mode=output_mode
    )

@app.route('/process')
def process():
    track_id = request.args.get("track", "").strip()
    output_mode = request.args.get("output", "both").strip()
    if output_mode not in {"music", "vocal", "both"}:
        output_mode = "both"
    if not track_id:
        return redirect(url_for("index"))

    input_path = UPLOAD_FOLDER / f"{track_id}.mp3"
    if not input_path.exists():
        return "File audio tidak ditemukan."

    command = [
        "python3", "-m", "demucs.separate", "-n", "htdemucs",
        "--two-stems=vocals", "-o", str(RESULT_FOLDER), str(input_path)
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError:
        return "Gagal memproses pemisahan vokal dan musik."

    stem_dir = RESULT_FOLDER / "htdemucs" / track_id
    inst_path = stem_dir / "no_vocals.wav"
    vocal_path = stem_dir / "vocals.wav"
    if not (inst_path.exists() and vocal_path.exists()):
        return "Hasil pemisahan tidak ditemukan."

    instrumental_name = None
    vocal_name = None
    if output_mode in {"music", "both"}:
        instrumental_name = f"karaoke_{track_id}.mp3"
        convert_to_mp3(inst_path, STATIC_FOLDER / instrumental_name)
    if output_mode in {"vocal", "both"}:
        vocal_name = f"vokal_{track_id}.mp3"
        convert_to_mp3(vocal_path, STATIC_FOLDER / vocal_name)

    shutil.rmtree(RESULT_FOLDER, ignore_errors=True)
    input_path.unlink(missing_ok=True)

    return render_template(
        "results.html",
        instrumental_file=instrumental_name,
        vocal_file=vocal_name,
        output_mode=output_mode
    )


@app.route('/create-video', methods=['GET', 'POST'])
def create_video():
    if request.method == 'GET':
        audio_file = request.args.get("audio", "").strip()
        if not audio_file:
            return redirect(url_for("index"))
        ad_unlocked_audio = session.get("ad_unlocked_audio")
        ad_policy_active = session.get("ad_policy_active", False)
        if ad_policy_active and ad_unlocked_audio != audio_file:
            return redirect(url_for("continue_flow", audio=audio_file))
        if ad_unlocked_audio == audio_file:
            session.pop("ad_unlocked_audio", None)
        return render_template("compose.html", audio_file=audio_file, error=None)

    audio_file = request.form.get("audio_file", "").strip()
    subtitle_mode = request.form.get("subtitle_mode", "none")
    subtitle_position = request.form.get("subtitle_position", "bottom")
    subtitle_text = request.form.get("subtitle_text", "").strip()
    media_file = request.files.get("media_file")

    audio_path = STATIC_FOLDER / audio_file
    if not audio_path.exists():
        return "Audio karaoke tidak ditemukan."
    if not media_file or not media_file.filename:
        return render_template("compose.html", audio_file=audio_file, error="Upload gambar atau video terlebih dahulu.")

    media_name = f"media_{uuid.uuid4().hex[:8]}_{secure_filename(media_file.filename)}"
    media_path = UPLOAD_FOLDER / media_name
    media_file.save(media_path)

    is_image = media_file.mimetype.startswith("image/")
    is_video = media_file.mimetype.startswith("video/")
    if not is_image and not is_video:
        media_path.unlink(missing_ok=True)
        return render_template("compose.html", audio_file=audio_file, error="Format harus gambar atau video.")

    duration = len(AudioSegment.from_file(audio_path)) / 1000.0
    video_name = f"video_{uuid.uuid4().hex[:8]}.mp4"
    video_path = STATIC_FOLDER / video_name

    subtitle_file = None
    vf_chain = ["scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2"]

    if subtitle_mode in {"burn", "srt"} and subtitle_text:
        subtitle_file = f"subtitle_{uuid.uuid4().hex[:8]}.srt"
        subtitle_path = STATIC_FOLDER / subtitle_file
        generated = generate_simple_srt(subtitle_text, duration, subtitle_path)
        if generated and subtitle_mode == "burn":
            align = {"top": 8, "center": 5, "bottom": 2}.get(subtitle_position, 2)
            escaped_srt = str(subtitle_path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
            style = (
                f"Alignment={align},Fontsize=28,PrimaryColour=&HFFFFFF&,"
                "OutlineColour=&H000000&,BorderStyle=3,Outline=1,Shadow=1"
            )
            vf_chain.append(f"subtitles='{escaped_srt}':force_style='{style}'")
        if not generated:
            subtitle_file = None

    ffmpeg_cmd = ["ffmpeg", "-y"]
    if is_image:
        ffmpeg_cmd.extend([
            "-loop", "1", "-i", str(media_path), "-i", str(audio_path),
            "-t", f"{duration:.2f}",
            "-vf", ",".join(vf_chain),
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(video_path)
        ])
    else:
        ffmpeg_cmd.extend([
            "-stream_loop", "-1", "-i", str(media_path), "-i", str(audio_path),
            "-t", f"{duration:.2f}",
            "-map", "0:v:0", "-map", "1:a:0",
            "-vf", ",".join(vf_chain),
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(video_path)
        ])

    try:
        subprocess.run(ffmpeg_cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError:
        media_path.unlink(missing_ok=True)
        return render_template(
            "compose.html",
            audio_file=audio_file,
            error="Gagal membuat video. Coba file media lain atau ubah mode subtitle."
        )

    media_path.unlink(missing_ok=True)
    return render_template(
        "video_result.html",
        audio_file=audio_file,
        video_file=video_name,
        subtitle_file=subtitle_file
    )


@app.route("/continue-flow")
def continue_flow():
    audio_file = request.args.get("audio", "").strip()
    if not audio_file:
        return redirect(url_for("index"))

    if not session.get("ad_policy_active", False):
        # Lanjutkan pertama kali gratis, setelah ini aktifkan ad gate.
        session["ad_policy_active"] = True
        return redirect(url_for("create_video", audio=audio_file))

    session["pending_audio"] = audio_file
    return redirect(url_for("ad_gate"))


@app.route("/ad-gate", methods=["GET", "POST"])
def ad_gate():
    pending_audio = session.get("pending_audio")
    if not pending_audio:
        return redirect(url_for("index"))

    if request.method == "POST":
        session["ad_unlocked_audio"] = pending_audio
        session.pop("pending_audio", None)
        return redirect(url_for("create_video", audio=pending_audio))

    return render_template("ad_gate.html", audio_file=pending_audio)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
