from flask import Flask, request, render_template_string, redirect, url_for, session, send_from_directory, jsonify
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import datetime
import os
import ssl
import threading
import subprocess
import uuid
import yt_dlp
import glob
import re

app = Flask(__name__)
app.secret_key = "supersecret"
CERT_FILE = "cert.pem"
KEY_FILE = "key.pem"
RESULTS_DIR = "results"

os.makedirs(RESULTS_DIR, exist_ok=True)

status_store = {}

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin1")  # domyślnie "admin1"

def generate_self_signed_cert():
    if os.path.exists(CERT_FILE) and os.path.exists(KEY_FILE):
        return

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, u"PL"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, u"Some-State"),
        x509.NameAttribute(NameOID.LOCALITY_NAME, u"Some-City"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, u"MyOrg"),
        x509.NameAttribute(NameOID.COMMON_NAME, u"localhost"),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.utcnow())
        .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(u"localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )

    with open(CERT_FILE, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(KEY_FILE, "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))

def sanitize_filename(name):
    return "".join(c if c.isalnum() or c in " -_." else "_" for c in name)

def get_audio_duration(wav_path):
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", wav_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        return float(result.stdout.strip())
    except Exception as e:
        print(f"[ERROR] Nie można odczytać długości audio: {e}")
        return 0.0

def process_video(url, lang, do_transcript=True, do_translate=False):
    try:
        ydl_opts_info = {"quiet": True, "skip_download": True}
        with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
            info = ydl.extract_info(url, download=False)
            title = sanitize_filename(info.get("title", str(uuid.uuid4())))

        webm_path = os.path.join(RESULTS_DIR, f"{title}.webm")
        wav_path = os.path.join(RESULTS_DIR, f"{title}.wav")
        txt_path = os.path.join(RESULTS_DIR, f"{title}.txt")
        txt_pl_path = os.path.join(RESULTS_DIR, f"{title}_pl.txt")

        for path in [webm_path, wav_path, txt_path, txt_pl_path]:
            if os.path.exists(path):
                os.remove(path)

        status_store[title] = {"step": "pobieranie video", "progress": 5}

        subprocess.run([
            "yt-dlp", "-f", "bestvideo+bestaudio", "--merge-output-format", "webm",
            "-o", webm_path, url
        ], check=True)

        status_store[title] = {"step": "konwersja do wav", "progress": 25}

        subprocess.run([
            "ffmpeg", "-i", webm_path, "-ar", "16000", "-ac", "1", wav_path
        ], check=True)

        if do_transcript:
            status_store[title] = {"step": "transkrypcja", "progress": 30}
            duration = get_audio_duration(wav_path)
            if duration == 0:
                status_store[title] = {"step": "błąd: brak długości audio", "progress": 0}
                return

            whisper_cmd = [
                "whisper", wav_path, "--language", lang,
                "--output_format", "txt", "--output_dir", RESULTS_DIR
            ]
            print(f"[DEBUG] Uruchamiam whisper: {' '.join(whisper_cmd)}")
            process = subprocess.Popen(whisper_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

            for line in process.stdout:
                print(f"[WHISPER] {line.strip()}")
                match = re.search(r"--> ([0-9:.]+)]", line)
                if match:
                    timestamp = match.group(1)
                    t_parts = [float(x) for x in timestamp.split(":")]
                    seconds = sum(x * 60**i for i, x in enumerate(reversed(t_parts)))
                    percent = min(99, int((seconds / duration) * 100))
                    status_store[title] = {"step": "transkrypcja", "progress": percent}

            process.wait()
            if process.returncode != 0:
                status_store[title] = {"step": "błąd whisper", "progress": 0}
                return

            txt_candidates = glob.glob(os.path.join(RESULTS_DIR, "*.txt"))
            if txt_candidates:
                latest_txt = max(txt_candidates, key=os.path.getctime)
                if latest_txt != txt_path:
                    os.rename(latest_txt, txt_path)

            # Tłumaczenie jeśli trzeba
            if do_translate and lang != "pl":
                status_store[title] = {"step": "tłumaczenie na PL", "progress": 99}
                try:
                    with open(txt_path, "r") as f:
                        original_text = f.read()

                    result = subprocess.run(
                        ["trans", "-b", ":pl"],
                        input=original_text,
                        text=True,
                        capture_output=True,
                        check=True
                    )

                    with open(txt_pl_path, "w") as f:
                        f.write(result.stdout)

                except Exception as e:
                    print(f"[ERROR] Błąd tłumaczenia: {e}")
                    status_store[title] = {"step": f"błąd tłumaczenia: {e}", "progress": 0}
                    return

        status_store[title] = {"step": "zakończono", "progress": 100}

    except Exception as e:
        print(f"[ERROR] Błąd przetwarzania: {e}")
        status_store[title] = {"step": f"błąd: {e}", "progress": 0}

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        password = request.form.get("password")
        if password == ADMIN_PASSWORD:
            session["authenticated"] = True
            return redirect(url_for("url_input"))
        else:
            return render_template_string(PASSWORD_FORM, error="Złe hasło")
    return render_template_string(PASSWORD_FORM)

@app.route("/url", methods=["GET", "POST"])
def url_input():
    if not session.get("authenticated"):
        return redirect(url_for("index"))

    if request.method == "POST":
        url = request.form.get("url")
        lang = request.form.get("language")
        do_transcript = request.form.get("transcript") == "on"
        do_translate = request.form.get("translate") == "on"
        threading.Thread(target=process_video, args=(url, lang, do_transcript, do_translate)).start()
        return render_template_string(URL_FORM, message="Film dodany do kolejki.")

    return render_template_string(URL_FORM)

@app.route("/list")
def list_results():
    files = sorted(os.listdir(RESULTS_DIR))
    return render_template_string(LIST_FILES, files=files)

@app.route("/download/<path:filename>")
def download_file(filename):
    ext = os.path.splitext(filename)[1].lower()
    inline = ext in [".txt", ".webm"]
    return send_from_directory(RESULTS_DIR, filename, as_attachment=not inline)

@app.route("/status")
def status():
    html = '''
    <h2>Status przetwarzania</h2>
    <div id="status-output">Ładowanie...</div>
    <script>
    async function fetchStatus() {
        const res = await fetch("/status.json");
        const data = await res.json();
        const output = Object.entries(data).map(([title, info]) => `
            <div><strong>${title}</strong>: ${info.step} (${info.progress}%)</div>
        `).join("<br>");
        document.getElementById("status-output").innerHTML = output || "Brak aktywności.";
    }
    setInterval(fetchStatus, 2000);
    fetchStatus();
    </script>
    <br><a href="/url">⬅ Wróć do formularza</a>
    '''
    return html

@app.route("/status.json")
def status_json():
    return jsonify(status_store)

PASSWORD_FORM = '''
    <h2>Podaj hasło</h2>
    {% if error %}<p style="color:red;">{{ error }}</p>{% endif %}
    <form method="POST">
        <input type="password" name="password" placeholder="Hasło">
        <input type="submit" value="Dalej">
    </form>
'''

URL_FORM = '''
    <h2>Dodaj film z YouTube do transkrypcji</h2>
    {% if message %}<p style="color:green;">{{ message }}</p>{% endif %}
    <form method="POST">
        <input type="text" name="url" placeholder="https://youtube.com/..."><br>
        <input type="text" name="language" placeholder="np. pl, en"><br>
        <label><input type="checkbox" name="transcript" checked> Wykonaj transkrypcję</label><br>
        <label><input type="checkbox" name="translate"> Przetłumacz na PL</label><br>
        <input type="submit" value="Prześlij">
    </form>
    <br><a href="/list">📄 Zobacz pliki wynikowe</a>
    <br><a href="/status">📊 Sprawdź status</a>
'''

LIST_FILES = '''
    <h2>Pliki wynikowe</h2>
    <ul>
    {% for file in files %}
        <li><a href="/download/{{ file }}">{{ file }}</a></li>
    {% endfor %}
    </ul>
    <br><a href="/url">⬅ Wróć</a>
'''

if __name__ == "__main__":
    generate_self_signed_cert()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=CERT_FILE, keyfile=KEY_FILE)
    app.run(host="0.0.0.0", port=8443, ssl_context=context)

