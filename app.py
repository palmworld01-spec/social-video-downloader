import os
import re
from flask import Flask, request, jsonify, render_template, redirect
from flask_cors import CORS
import yt_dlp

app = Flask(__name__)
CORS(app)

SUPPORTED_DOMAINS = [
    "youtube.com",
    "youtu.be",
    "facebook.com",
    "fb.watch",
    "tiktok.com",
    "vm.tiktok.com"
]


def is_valid_url(url):
    return isinstance(url, str) and url.startswith(("http://", "https://"))


def is_supported_url(url):
    try:
        url_lower = url.lower()
        return any(domain in url_lower for domain in SUPPORTED_DOMAINS)
    except Exception:
        return False


def clean_text(text):
    if not text:
        return "Video"
    text = str(text)
    text = re.sub(r"[^\w\s\-\.\(\)\[\]]", "", text)
    return text[:120] if text else "Video"


def format_filesize(size):
    if not size:
        return ""
    try:
        size = int(size)
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        if size < 1024 * 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MB"
        return f"{size / (1024 * 1024 * 1024):.1f} GB"
    except Exception:
        return ""


def get_ydl_options():
    return {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,

        # Stable option for browser redirect:
        # Prefer progressive MP4 audio+video. YouTube format 18 is usually 360p audio+video.
        "format": "18/best[ext=mp4][vcodec!=none][acodec!=none]/best[vcodec!=none][acodec!=none]/best",

        "socket_timeout": 30,
        "retries": 5,
        "fragment_retries": 5,
        "extractor_retries": 5,
        "geo_bypass": True,

        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 12; Mobile) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Mobile Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9"
        },

        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web"]
            }
        }
    }


def extract_video_info(url):
    with yt_dlp.YoutubeDL(get_ydl_options()) as ydl:
        return ydl.extract_info(url, download=False)


def get_progressive_formats(info):
    formats = []
    seen = set()

    raw_formats = info.get("formats", [])

    for f in raw_formats:
        file_url = f.get("url")
        format_id = f.get("format_id")
        ext = f.get("ext", "")
        height = f.get("height")
        filesize = f.get("filesize") or f.get("filesize_approx")
        acodec = f.get("acodec")
        vcodec = f.get("vcodec")
        protocol = f.get("protocol", "")

        if not file_url or not format_id:
            continue

        # Skip audio-only
        if vcodec == "none":
            continue

        # Skip video-only because browser redirect cannot merge audio later
        if acodec == "none":
            continue

        # Prefer http/https direct formats
        if protocol and "m3u8" in protocol:
            continue

        label = f"{height}p" if height else "Best"
        if ext:
            label += f" {ext.upper()}"

        size_text = format_filesize(filesize)
        if size_text:
            label += f" • {size_text}"

        key = f"{format_id}-{label}"
        if key in seen:
            continue

        seen.add(key)

        formats.append({
            "format_id": str(format_id),
            "label": label,
            "ext": ext,
            "height": height or 0,
            "filesize": filesize or 0,
            "has_audio": True
        })

    formats = sorted(
        formats,
        key=lambda x: x.get("height") or 0,
        reverse=True
    )

    return formats[:8]


def fallback_format_from_info(info):
    """
    Some platforms return a direct info.url without a normal formats list.
    This fallback creates a safe single download option.
    """
    direct_url = info.get("url")
    ext = info.get("ext", "mp4")
    if direct_url:
        return [{
            "format_id": "best",
            "label": f"Best {str(ext).upper()}",
            "ext": ext,
            "height": info.get("height") or 0,
            "filesize": info.get("filesize") or 0,
            "has_audio": True
        }]
    return []


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return jsonify({
        "success": True,
        "app": "FastVid Social Downloader",
        "status": "running",
        "supported": ["YouTube", "Facebook", "TikTok"]
    })


@app.route("/api/info", methods=["POST"])
def video_info():
    try:
        data = request.get_json(force=True)
        url = data.get("url", "").strip()

        if not url:
            return jsonify({
                "success": False,
                "message": "Please paste a video URL."
            }), 400

        if not is_valid_url(url):
            return jsonify({
                "success": False,
                "message": "Invalid URL. Please paste a valid video link."
            }), 400

        if not is_supported_url(url):
            return jsonify({
                "success": False,
                "message": "Only YouTube, Facebook, and TikTok public links are supported."
            }), 400

        info = extract_video_info(url)

        formats = get_progressive_formats(info)

        if not formats:
            formats = fallback_format_from_info(info)

        if not formats:
            return jsonify({
                "success": False,
                "message": "No downloadable audio-video format found. This video may be private, live, protected, or unsupported."
            }), 400

        return jsonify({
            "success": True,
            "title": clean_text(info.get("title")),
            "thumbnail": info.get("thumbnail", ""),
            "duration": info.get("duration", 0),
            "platform": info.get("extractor_key", "Social"),
            "webpage_url": info.get("webpage_url", url),
            "formats": formats
        })

    except Exception as e:
        print("INFO ERROR:", str(e))
        return jsonify({
            "success": False,
            "message": "Could not fetch this video. It may be private, live, protected, blocked, or unsupported."
        }), 500


@app.route("/api/download")
def download_video():
    try:
        url = request.args.get("url", "").strip()
        format_id = request.args.get("format_id", "").strip()

        if not url:
            return "Missing video URL.", 400

        if not is_valid_url(url) or not is_supported_url(url):
            return "Unsupported URL.", 400

        options = get_ydl_options()

        if format_id and format_id != "best":
            options["format"] = (
                f"{format_id}/18/"
                "best[ext=mp4][vcodec!=none][acodec!=none]/"
                "best[vcodec!=none][acodec!=none]/best"
            )
        else:
            options["format"] = (
                "18/"
                "best[ext=mp4][vcodec!=none][acodec!=none]/"
                "best[vcodec!=none][acodec!=none]/best"
            )

        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)

        direct_url = info.get("url")

        if not direct_url and "formats" in info:
            for f in info.get("formats", []):
                if str(f.get("format_id")) == str(format_id) and f.get("url"):
                    direct_url = f.get("url")
                    break

        if not direct_url:
            return "Download link could not be generated.", 400

        return redirect(direct_url)

    except Exception as e:
        print("DOWNLOAD ERROR:", str(e))
        return "Could not generate download link. The video may be private, live, protected, or blocked.", 500


@app.errorhandler(404)
def not_found(e):
    return jsonify({
        "success": False,
        "message": "Route not found."
    }), 404


@app.errorhandler(500)
def server_error(e):
    return jsonify({
        "success": False,
        "message": "Internal server error."
    }), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
