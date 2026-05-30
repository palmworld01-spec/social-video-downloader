import os
import re
import requests
from flask import Flask, request, jsonify, render_template, redirect
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

COBALT_API_URL = os.environ.get(
    "COBALT_API_URL",
    "https://cobalt-api-production-4edc.up.railway.app"
).rstrip("/")

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
    text = text.strip()

    return text[:120] if text else "Video"


def platform_name(url):
    url_lower = url.lower()

    if "youtube.com" in url_lower or "youtu.be" in url_lower:
        return "YouTube"
    if "facebook.com" in url_lower or "fb.watch" in url_lower:
        return "Facebook"
    if "tiktok.com" in url_lower or "vm.tiktok.com" in url_lower:
        return "TikTok"

    return "Social"


def call_cobalt_api(url):
    payload = {
        "url": url,
        "downloadMode": "auto",
        "filenameStyle": "basic",
        "videoQuality": "1080",
        "youtubeVideoCodec": "h264",
        "youtubeVideoContainer": "mp4",
        "alwaysProxy": True,
        "localProcessing": "disabled"
    }

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "FastVid Downloader/1.0"
    }

    response = requests.post(
        COBALT_API_URL + "/",
        json=payload,
        headers=headers,
        timeout=90
    )

    try:
        data = response.json()
    except Exception:
        raise Exception("Cobalt API returned invalid response.")

    if response.status_code >= 400:
        raise Exception(str(data))

    if data.get("status") == "error":
        error = data.get("error", {})
        code = error.get("code", "unknown_error")
        service = error.get("context", {}).get("service", "unknown")
        raise Exception(f"{service}: {code}")

    return data


def get_filename_without_ext(filename):
    if not filename:
        return "Video"

    filename = str(filename)

    for ext in [".mp4", ".webm", ".mov", ".m4v", ".avi", ".mkv", ".mp3", ".m4a"]:
        if filename.lower().endswith(ext):
            filename = filename[: -len(ext)]
            break

    return clean_text(filename)


def build_formats_from_cobalt(cobalt):
    status = cobalt.get("status")

    if status in ["tunnel", "redirect"] and cobalt.get("url"):
        filename = cobalt.get("filename", "Video.mp4")
        ext = "mp4"

        if "." in filename:
            ext = filename.split(".")[-1].lower()[:8]

        return [
            {
                "format_id": "cobalt",
                "label": f"Best Quality {ext.upper()}",
                "ext": ext,
                "height": 0,
                "filesize": 0,
                "has_audio": True
            }
        ]

    if status == "picker":
        picker = cobalt.get("picker", [])
        formats = []

        for index, item in enumerate(picker):
            item_url = item.get("url")
            if not item_url:
                continue

            item_type = item.get("type", "video")
            item_ext = item.get("ext", "mp4")
            item_label = item.get("label") or item.get("quality") or f"Option {index + 1}"

            formats.append({
                "format_id": f"picker_{index}",
                "label": f"{item_label} {str(item_ext).upper()}",
                "ext": item_ext,
                "height": 0,
                "filesize": 0,
                "has_audio": item_type != "audio"
            })

        return formats

    return []


@app.route("/")
def home():
    return render_template("index.html")
@app.route("/ads.txt")
def ads_txt():
    return "google.com, pub-8236790641060877, DIRECT, f08c47fec0942fa0", 200, {"Content-Type": "text/plain"}


@app.route("/api/health")
def health():
    return jsonify({
        "success": True,
        "app": "FastVid Social Downloader",
        "status": "running",
        "backend": "Cobalt API",
        "cobalt_api": COBALT_API_URL,
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

        cobalt = call_cobalt_api(url)
        formats = build_formats_from_cobalt(cobalt)

        if not formats:
            return jsonify({
                "success": False,
                "message": "No downloadable format found. This video may be private, live, protected, unavailable, or unsupported."
            }), 400

        filename = cobalt.get("filename") or f"{platform_name(url)} Video.mp4"
        title = get_filename_without_ext(filename)

        return jsonify({
            "success": True,
            "title": title,
            "thumbnail": "",
            "duration": 0,
            "platform": platform_name(url),
            "webpage_url": url,
            "formats": formats
        })

    except Exception as e:
        error_text = str(e)
        print("INFO ERROR:", error_text, flush=True)

        friendly_message = "Server error: " + error_text

        if "video.unavailable" in error_text:
            friendly_message = "This video is unavailable from the current server. Try another public video link."
        elif "invalid_body" in error_text:
            friendly_message = "Cobalt API request body is invalid. Please check backend settings."
        elif "rate" in error_text.lower():
            friendly_message = "The server is receiving too many requests. Please wait and try again."
        elif "private" in error_text.lower():
            friendly_message = "This video may be private or login-required."
        elif "unsupported" in error_text.lower():
            friendly_message = "This link is unsupported or protected."

        return jsonify({
            "success": False,
            "message": friendly_message
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

        cobalt = call_cobalt_api(url)
        status = cobalt.get("status")

        if status in ["tunnel", "redirect"] and cobalt.get("url"):
            return redirect(cobalt.get("url"))

        if status == "picker":
            picker = cobalt.get("picker", [])

            if format_id and format_id.startswith("picker_"):
                try:
                    index = int(format_id.replace("picker_", ""))
                    if picker[index].get("url"):
                        return redirect(picker[index].get("url"))
                except Exception:
                    pass

            for item in picker:
                if item.get("url"):
                    return redirect(item.get("url"))

        return "Download link could not be generated.", 400

    except Exception as e:
        error_text = str(e)
        print("DOWNLOAD ERROR:", error_text, flush=True)
        return "Could not generate download link: " + error_text, 500


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
