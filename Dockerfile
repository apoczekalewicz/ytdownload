FROM fedora:latest

# Zmienna środowiskowa do unikania interakcji podczas instalacji
ENV PYTHONUNBUFFERED=1
ENV ADMIN_PASSWORD=admin1

# Zainstaluj pakiety systemowe
RUN dnf install -y \
    python3 \
    python3-pip \
    ffmpeg \
    yt-dlp \
    git \
    gcc \
    redhat-rpm-config \
    libffi-devel \
    openssl-devel \
    translate-shell \
    && dnf clean all

# Zainstaluj Whisper i zależności
RUN pip3 install --upgrade pip \
    && pip3 install git+https://github.com/openai/whisper.git \
    && pip3 install flask cryptography yt_dlp

# Utwórz katalog aplikacji i ustaw jako roboczy
WORKDIR /app
USER 1000

RUN chown -R 1000 /app
# Skopiuj aplikację do kontenera
COPY app.py /app/app.py

# Utwórz katalog na wyniki
RUN mkdir /app/results && chmod 777 /app/results

# Otwórz port HTTPS
EXPOSE 8443

# Uruchom aplikację
CMD ["python3", "app.py"]

