FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        git \
        libmagic1 \
        libxml2 \
        libxslt1.1 \
        zlib1g \
        bzip2 \
        pkg-config \
        libicu-dev \
        unzip \
        p7zip-full \
        cabextract \
        xz-utils \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir scancode-toolkit liscopelens

COPY . .

RUN pip install --no-cache-dir .

# Switch to a mount-friendly workspace for project paths passed at runtime.
WORKDIR /workspace

ENTRYPOINT ["python", "/app/scripts/run_license_compatibility.py"]
CMD ["--output-dir", "/workspace/output", "--cache-dir", "/workspace/.dep_cache"]
