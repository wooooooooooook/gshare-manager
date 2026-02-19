FROM python:3.9-slim

WORKDIR /app

# 필요한 패키지 설치 (NFS, SMB 클라이언트 포함)
RUN apt-get update && apt-get install -y     procps     curl     nfs-common     smbclient     cifs-utils     iputils-ping     net-tools     gawk     samba     ffmpeg     && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN pip install poetry

# Copy poetry files
COPY pyproject.toml .

# Install dependencies
RUN poetry config virtualenvs.create false     && poetry install --no-interaction --no-ansi --no-root

# 디렉토리 생성
RUN mkdir -p /mnt/gshare /mnt/gshare_links /config /logs

# 템플릿 설정 파일 복사
COPY config.yaml.template /config/config.yaml.template

# 애플리케이션 코드 복사
COPY app/ .

# 포트 설정
EXPOSE 5000

# 환경 변수 설정
ENV PYTHONUNBUFFERED=1
ENV TZ=Asia/Seoul

# 볼륨 설정
VOLUME ["/config"]

# 실행 명령
CMD ["python", "main.py"]
