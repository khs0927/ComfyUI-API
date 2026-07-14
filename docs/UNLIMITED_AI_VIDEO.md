# 완전 무료 오픈소스 장편 AI 영상 시스템

## 확정 원칙

이 브랜치는 영상 생성 비용이 발생하는 API, 유료 GPU 작업, 구독형 모델을 사용하지 않는다.

- fal.ai, Replicate, Wavespeed 등 과금형 추론 API 사용 금지
- Hugging Face Jobs와 유료 GPU Space 사용 금지
- 공개 ZeroGPU Space를 자동 백엔드로 사용하지 않음
- 코드뿐 아니라 모델 가중치도 Apache-2.0으로 확인된 엔진을 기본 채택
- 사용자가 소유한 NVIDIA GPU에서 직접 추론
- 최종 영상은 FFmpeg로 로컬 조립
- Hugging Face Hub는 무료 모델 파일 다운로드에만 사용

전기료와 이미 보유한 하드웨어 비용을 제외하면 생성 건당 서비스 요금은 0원이다.

## 모델 검토 결론

| 모델 | 코드/가중치 라이선스 | 공식 메모리 정보 | 장편 방식 | 판정 |
|---|---|---:|---|---|
| Helios-Distilled | Apache-2.0 | 그룹 오프로딩 약 6GB VRAM | 33프레임 청크의 분 단위 자기회귀 생성 | 기본 엔진 |
| Wan2.1-T2V-1.3B | Apache-2.0 | 약 8.19GB VRAM | 짧은 클립 생성 후 연속 조립 | 저사양 폴백 |
| Wan2.2-TI2V-5B | Apache-2.0 | 약 24GB VRAM | 720P 장면 생성 후 조립 | 선택적 고화질 엔진 |
| CogVideoX-2B | Apache-2.0 | 저사양 실행 가능 | 장면 생성 후 조립 | 품질이 낮아 보조 후보 |
| FramePack | 코드는 Apache-2.0, 공식 모델 카드 라이선스 불명확 | 6GB, 60초 예시 | 점진적 프레임 생성 | 엄격 모드 제외 |
| SkyReels-V2 | 모델 메타데이터 `license:other` | 1.3B 약 14.7GB | 무한 길이 Diffusion Forcing | 엄격 모드 제외 |
| LTX-Video/LTX-2 | 모델 메타데이터 `license:other` | 버전별 상이 | 최대 60초 또는 확장 | 엄격 모드 제외 |
| HunyuanVideo | 사용자 정의 라이선스 | 고사양 | 짧은 클립/확장 | 엄격 모드 제외 |

엄격한 오픈소스 기준에서는 Helios와 Wan 계열의 Apache-2.0 체크포인트만 활성화한다.

## 최종 구조

```text
모바일 ChatGPT
    │  Custom GPT Action 또는 원격 MCP
    ▼
사용자 PC의 인증된 FastAPI/MCP 컨트롤러
    │
    ├─ Helios-Distilled 로컬 추론
    ├─ Wan2.1-T2V-1.3B 로컬 추론
    └─ 선택: 로컬 ComfyUI의 Wan/Helios 워크플로
    │
    ▼
FFmpeg 장면 정규화·무손실 연결
    │
    ▼
사용자 PC에 최종 MP4 저장
```

Hugging Face Space는 무료 데모와 모델 조사에는 유용하지만, 무료 GPU 사용량과 대기열이 존재하므로 무제한 자동화의 실행 기반으로 간주하지 않는다.

## 길이 제한이 없는 방식

애플리케이션에는 최종 길이 상한을 두지 않는다.

- Helios는 한 장면을 최대 60~90초 단위로 생성한다.
- 더 긴 작품은 원고를 장면으로 분할한다.
- 각 장면을 순차 생성한다.
- 모든 결과를 동일 해상도, FPS, 코덱으로 변환한다.
- FFmpeg concat으로 하나의 MP4를 만든다.

예시:

```text
30분 영상 / 장면당 60초 = 30개 Helios 장면
60분 영상 / 장면당 30초 = 120개 장면
```

저장공간과 처리시간은 증가하지만 코드상 최종 재생시간 제한은 없다.

## 설치

### Helios-Distilled 권장

```bash
bash scripts/bootstrap_free_video_worker.sh helios
```

설치 후 `.env.video`에 출력된 `HELIOS_ROOT`, `HELIOS_INFER_SCRIPT`, `HELIOS_MODEL` 값을 적용한다.

Helios는 33프레임 청크 단위로 동작한다. 컨트롤러는 요청 재생시간과 FPS를 33의 배수 프레임으로 올림한 뒤, 생성 결과를 정확한 장면 길이로 잘라낸다.

### Wan2.1 저사양 폴백

```bash
bash scripts/bootstrap_free_video_worker.sh wan21
```

Wan2.1-T2V-1.3B는 공식 480P 경로와 CPU 오프로딩을 사용한다. 컨트롤러는 프레임 수를 모델 요구사항인 `4n+1`로 맞춘다.

## 공급자 선택

`provider=auto`는 다음 순서로 로컬 엔진을 선택한다.

1. `HELIOS_ROOT`가 있으면 Helios-Distilled
2. `WAN21_ROOT`가 있으면 Wan2.1-T2V-1.3B
3. 로컬 `COMFYUI_URL`과 API 워크플로가 있으면 ComfyUI
4. 아무 엔진도 없으면 오류로 중단

테스트용 `mock` 외에는 가짜 영상이나 유료 폴백으로 자동 전환하지 않는다.

## 모바일 ChatGPT 연결

### REST Action

`video_api.py`를 사용자 PC에서 실행한다.

```bash
uvicorn video_api:app --host 0.0.0.0 --port 7860
```

제공 API:

- `POST /v1/videos`
- `GET /v1/videos/{job_id}`
- `POST /v1/videos/{job_id}/cancel`
- `GET /v1/videos/{job_id}/download`
- `GET /openapi.json`

모바일 ChatGPT가 접근하려면 인증된 HTTPS 주소가 필요하다. 추론 서버나 ComfyUI 포트를 직접 공개하지 말고, `VIDEO_API_TOKEN`을 검사하는 컨트롤러만 공개한다.

### MCP

`video_mcp_server.py`는 다음 도구를 제공한다.

- `create_long_video`
- `get_video_status`
- `cancel_video`
- `estimate_video_plan`

## Hugging Face Spaces 판정

공개 Space는 다음 목적으로만 사용한다.

- 모델 품질을 휴대폰에서 빠르게 시험
- 프롬프트 초안 검증
- 설치 전 UI 확인

사용하지 않는 목적:

- 무제한 장편 생성
- 자동 작업 큐
- 최종 영상 보관
- 항상 켜진 ChatGPT 플러그인 백엔드

공개 ZeroGPU는 무료이지만 사용자별 GPU 할당량, 대기열, 실행시간 제한이 있으므로 완전 무료 무제한 요구를 만족하지 못한다.

## 보안

- ComfyUI와 모델 추론 포트를 인터넷에 직접 공개하지 않는다.
- `VIDEO_API_TOKEN`을 긴 임의 문자열로 설정한다.
- 한 번에 GPU 작업 하나만 실행하는 것이 기본이다.
- 완성된 MP4와 임시 프레임의 자동 삭제 정책을 둔다.
- 외부 사용자를 허용할 경우 요청 길이와 저장공간 제한을 별도로 둔다.

## 현재 구현 범위

완료:

- Helios-Distilled 로컬 CLI 어댑터
- Wan2.1-T2V-1.3B 로컬 CLI 어댑터
- 로컬 ComfyUI 어댑터
- 무제한 장면 계획
- 상태 저장과 취소
- FFmpeg 정규화 및 연결
- FastAPI와 MCP 인터페이스
- 무료 모델 설치 스크립트
- 유료 공급자 코드와 의존성 제거

실기기 검증 필요:

1. 사용자 GPU에서 Helios 저메모리 명령 실행
2. Wan2.1 모델 다운로드 후 480P 샘플 생성
3. Windows 환경의 경로·CUDA·FFmpeg 검증
4. 장면 간 마지막 프레임 조건 연결
5. 한국어 TTS와 자막 모듈 추가
