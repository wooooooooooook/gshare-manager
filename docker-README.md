# Docker로 GShare 실행하기

이 문서는 GShare 애플리케이션을 Docker 환경에서 실행하기 위한 가이드입니다.

## 시작하기 전에

Docker와 Docker Compose가 설치되어 있어야 합니다:
- [Docker 설치 가이드](https://docs.docker.com/get-docker/)
- [Docker Compose 설치 가이드](https://docs.docker.com/compose/install/)

## 빠른 시작

1. 프로젝트 디렉토리에서 Docker Compose를 사용하여 애플리케이션을 시작합니다:

```bash
docker-compose up -d
```

2. 웹 브라우저에서 다음 주소로 접속합니다:

```
http://localhost:5000
```

3. 첫 실행 시 설정 페이지가 표시됩니다. 필요한 정보를 입력하세요.

## 볼륨 설명

Docker Compose 파일에는 다음의 볼륨들이 정의되어 있습니다:

- `./config:/config`: 설정 파일이 저장되는 디렉토리
- `./logs:/logs`: 로그 파일이 저장되는 디렉토리
- `gshare_data:/mnt/gshare`: NFS 또는 외부 저장소를 마운트할 경로
- `gshare_links:/mnt/gshare_links`: SMB 공유를 위한 심볼릭 링크를 저장할 경로

## NFS 마운트 사용하기

NFS 마운트 기능을 사용하려면 컨테이너에 추가 권한이 필요합니다. `docker-compose.yml` 파일에서 다음 부분의 주석을 해제하세요:

```yaml
# privileged: true
# cap_add:
#   - SYS_ADMIN
# devices:
#   - /dev/fuse:/dev/fuse
```

## 도커 컴포즈 명령어

### 컨테이너 시작
```bash
docker-compose up -d
```

### 상태 확인
```bash
docker-compose ps
```

### 로그 보기
```bash
docker-compose logs -f
```

### 컨테이너 중지
```bash
docker-compose down
```

### 컨테이너 재빌드 및 시작
```bash
docker-compose up -d --build
```

## 문제 해결

### 권한 문제
NFS 마운트나 SMB 공유 관련 권한 문제가 발생할 경우, Docker 컨테이너가 충분한 권한을 가지고 있는지 확인하세요.

### 네트워크 문제
Proxmox 서버 연결 문제가 발생할 경우, 도커 네트워크 설정이 올바른지, 그리고 컨테이너에서 Proxmox 서버에 접근할 수 있는지 확인하세요.

### 볼륨 데이터 유지
컨테이너를 삭제하더라도 볼륨에 저장된 데이터는 유지됩니다. 완전히 초기화하려면 볼륨도 삭제해야 합니다:

```bash
docker-compose down -v
``` 