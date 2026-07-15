# 모바일 ChatGPT용 무료 우선 장편 AI 영상 시스템

## 목표

모바일 ChatGPT에서 자연어로 장편 영상을 요청하고 다음 순서로 무료 자원을 사용한다.

1. **Beam + Helios-Distilled**: 주력 비동기 GPU 작업 큐
2. **Kaggle + Wan2.1-T2V-1.3B**: 무료 GPU 배치 폴백
3. **Hugging Face 공개 Gradio Space**: 남은 무료 할당량을 쓰는 보조 폴백
4. **로컬 Helios/Wan/ComfyUI**: 사용 가능한 경우 최종 폴백
5. **Modal**: 나중에 동일한 공급자 계약으로 추가할 예약 슬롯

유료 영상 API는 포함하지 않는다. 다만 Beam과 공개 GPU 서비스의 무료 크레딧·할당량·정책은 바뀔 수 있으므로, 하루 10~20분은 보장 수치가 아니라 실제 계정에서 벤치마크해야 하는 운영 목표다.

## 계정 경계

저장소 구현과 배포 템플릿은 완료할 수 있지만 실제 원격 배포에는 사용자의 계정 인증이 필요하다.

- Beam: `BEAM_TOKEN`
- Kaggle: `~/.kaggle/kaggle.json` 또는 `KAGGLE_USERNAME`·`KAGGLE_KEY`
- Hugging Face: 공개 Space는 토큰 없이 가능한 경우가 있으나 사용자 무료 할당량을 쓰려면 `HF_TOKEN` 권장

토큰은 저장소에 커밋하지 않는다.

## 전체 구조

```text
모바일 ChatGPT
  │
  ├─ Remote MCP
  └─ Custom GPT Action / REST
          │
          ▼
FastAPI + 영상 오케스트레이터
          │
          ├─ Beam Task Queue + Helios-Distilled
          ├─ Kaggle private GPU kernel + Wan2.1 1.3B
          ├─ Hugging Face public Gradio Space
          ├─ local Helios / Wan2.1 / ComfyUI
          └─ Modal reserved adapter
          │
          ▼
장면별 MP4
          │
          ▼
FFmpeg 해상도·FPS·코덱 정규화와 연결
          │
          ▼
최종 MP4 다운로드
```

## 공급자 선택

기본값은 다음과 같다.

```env
VIDEO_PROVIDER_ORDER=beam,kaggle,hf,helios,wan21,comfyui
```

`provider=auto`는 위 순서로 구성된 공급자를 장면마다 시도한다. 앞 공급자가 무료 할당량 소진, 대기열 오류, 실행 실패 등으로 실패하면 다음 공급자로 넘어간다.

직접 지정도 가능하다.

- `provider=beam`
- `provider=kaggle`
- `provider=hf`
- `provider=helios`
- `provider=wan21`
- `provider=comfyui`
- `provider=modal`: 예약되어 있으나 현재는 의도적으로 실패한다.

## 1. Beam + Helios 배포

### 필요한 것

- Beam 계정과 API 토큰
- Hugging Face 공개 모델 다운로드용 토큰은 선택 사항
- 배포를 실행할 PC 또는 개발 환경

### 설치와 배포

```bash
export BEAM_TOKEN='your-token'
bash scripts/deploy_beam_helios.sh
```

내부적으로 다음 명령을 사용한다.

```bash
pip install beam-client
beam configure default --token "$BEAM_TOKEN"
beam deploy workers/beam_helios.py:generate
```

배포가 끝나면 Beam이 출력하는 Task Queue 호출 URL을 복사한다.

```env
BEAM_TASK_QUEUE_URL=https://your-beam-task-queue-url
BEAM_TOKEN=your-token
BEAM_TASK_STATUS_URL=https://api.beam.cloud/v2/task/{task_id}/
```

### Beam 워커 동작

`workers/beam_helios.py`는 다음을 수행한다.

- `@task_queue` 기반 비동기 작업
- GPU 우선순위: H100 → RTX4090 → A10G
- Helios 코드가 포함된 Beam 이미지 생성
- Beam Volume에 Helios-Distilled 가중치 캐시
- 장면당 최대 90초
- 33프레임 배수로 프레임 수 보정
- 결과를 정확한 요청 길이로 FFmpeg 재인코딩
- `Output`으로 MP4 공개

첫 이미지 빌드와 첫 모델 다운로드는 후속 작업보다 오래 걸린다. `keep_warm_seconds=0`으로 두어 유휴 GPU 사용을 피한다.

### Beam 사용량 가드

```env
BEAM_DAILY_VIDEO_SECONDS=1200
```

이는 하루 요청 결과 길이를 1,200초로 제한하는 로컬 안전장치다. Beam의 실제 청구 단위나 무료 크레딧 잔액을 조회하는 기능은 아니므로 Beam 대시보드 예산도 함께 설정해야 한다.

## 2. Kaggle 폴백

Kaggle은 상시 서비스가 아니라 무료 GPU 배치 작업으로 사용한다. GPU 종류와 주간 사용량은 계정 및 시점에 따라 달라질 수 있다.

### 인증

Kaggle API 토큰을 다음 중 하나로 구성한다.

```text
~/.kaggle/kaggle.json
```

또는:

```env
KAGGLE_USERNAME=your-name
KAGGLE_KEY=your-key
```

### 초기 시험 배포

```bash
export KAGGLE_KERNEL_ID='your-name/helios-video-worker'
bash scripts/deploy_kaggle_worker.sh
```

오케스트레이터 설정:

```env
KAGGLE_KERNEL_ID=your-name/helios-video-worker
KAGGLE_WORKER_TEMPLATE=workers/kaggle_helios
KAGGLE_ACCELERATOR=NvidiaTeslaT4
KAGGLE_SCENE_TIMEOUT=21600
KAGGLE_DAILY_VIDEO_SECONDS=600
```

### Kaggle 워커 동작

현재 Kaggle 템플릿은 Helios 14B가 아니라 더 가벼운 Apache-2.0 `Wan2.1-T2V-1.3B`를 사용한다.

- 공식 Kaggle CLI의 `kernels push`로 실행
- `kernels status`로 완료 여부 확인
- `kernels output`으로 MP4 다운로드
- 5초 단위 Wan 장면을 필요한 수만큼 만든 뒤 연결
- 비공개 커널 사용
- T4를 기본 요청하되 계정에서 사용 가능한 GPU에 따라 실패 가능

첫 실행마다 모델을 다시 다운로드하면 매우 비효율적이다. 안정화 단계에서는 Wan2.1 가중치를 개인 Kaggle Dataset으로 한 번 업로드하고 `dataset_sources`로 연결하는 것이 좋다.

## 3. Hugging Face 공개 Space 폴백

공개 Gradio Space는 모델마다 API 입력 구조가 다르다. 따라서 Space ID만 넣는 것으로는 부족하고 실제 API 스키마를 확인해야 한다.

### API 스키마 확인

```bash
python scripts/inspect_hf_space.py BestWishYsh/Helios-14B-RealTime-AOTI
```

출력된 `api_name`, 위치 인수 또는 키워드 인수에 맞춰 설정한다.

```env
HF_TOKEN=optional-user-token
HF_VIDEO_SPACE_ID=BestWishYsh/Helios-14B-RealTime-AOTI
HF_VIDEO_SPACE_API_NAME=/generate
HF_VIDEO_SPACE_INPUTS_JSON=[]
HF_VIDEO_SPACE_KWARGS_JSON={}
HF_DAILY_VIDEO_SECONDS=120
```

템플릿에서 다음 토큰을 쓸 수 있다.

```text
{{PROMPT}}
{{NEGATIVE_PROMPT}}
{{DURATION_SECONDS}}
{{WIDTH}}
{{HEIGHT}}
{{FPS}}
{{SEED}}
```

예시:

```env
HF_VIDEO_SPACE_KWARGS_JSON={"prompt":"{{PROMPT}}","seed":"{{SEED}}"}
```

공개 Space는 대기열, 중지 상태, API 변경, 사용자별 무료 GPU 할당량 때문에 실패할 수 있다. 따라서 자동 체인의 세 번째 보조 경로로만 둔다.

## 4. 로컬 무료 오픈소스 폴백

### Helios-Distilled

```bash
bash scripts/bootstrap_free_video_worker.sh helios
```

```env
HELIOS_ROOT=/path/vendor/Helios
HELIOS_INFER_SCRIPT=/path/vendor/Helios/infer_helios.py
HELIOS_MODEL=/path/models/Helios-Distilled
```

### Wan2.1-T2V-1.3B

```bash
bash scripts/bootstrap_free_video_worker.sh wan21
```

```env
WAN21_ROOT=/path/vendor/Wan2.1
WAN21_MODEL=/path/models/Wan2.1-T2V-1.3B
WAN21_NATIVE_FPS=16
```

## 5. Modal 예약 슬롯

현재 설정은 다음과 같다.

```env
MODAL_ENABLED=false
MODAL_ENDPOINT_URL=
MODAL_TOKEN=
```

`ModalReservedProvider`는 공급자 계약과 환경변수 자리만 확보한다. 나중에 구현해도 장면 계획, MCP 도구, REST API, FFmpeg 렌더러를 변경할 필요가 없다.

## 모바일 ChatGPT 연결

### REST

```bash
pip install -r requirements-video.txt
uvicorn video_api:app --host 0.0.0.0 --port 7860
```

주요 경로:

- `GET /health`
- `GET /v1/providers`
- `POST /v1/videos`
- `GET /v1/videos/{job_id}`
- `POST /v1/videos/{job_id}/cancel`
- `GET /v1/videos/{job_id}/download`
- `GET /openapi.json`

### MCP

```bash
python video_mcp_server.py
```

도구:

- `create_long_video`
- `get_video_status`
- `cancel_video`
- `estimate_video_plan`
- `get_video_provider_status`

예시 요청:

```text
이 설교문으로 15분짜리 16:9 영상을 만들어줘.
30초 장면으로 나누고 provider는 auto로 해줘.
```

## 길이 제한

최종 영상 길이에는 애플리케이션 하드캡이 없다.

```text
20분 / 장면당 30초 = 40개 장면
60분 / 장면당 60초 = 60개 장면
```

실제 한계는 무료 크레딧, GPU 시간, Kaggle 주간 할당량, Space 대기열, 모델 다운로드, 저장공간이다.

## 보안

- Beam, Kaggle, Hugging Face 토큰을 저장소에 커밋하지 않는다.
- FastAPI와 MCP는 `VIDEO_API_TOKEN`과 HTTPS 뒤에 둔다.
- Kaggle 커널은 비공개로 유지한다.
- Beam Queue의 `authorized=True`를 해제하지 않는다.
- 출력 파일은 일정 기간 후 삭제한다.
- 여러 사용자가 접근할 경우 사용자별 길이·동시성 제한을 추가한다.

## 검증

자동 검증은 CircleCI와 GitHub Actions 양쪽에 구성한다.

- Python 문법 검사
- 원격 공급자 응답 파싱 테스트
- 공급자 순서와 템플릿 테스트
- 30분 장면 계획 테스트
- Docker 컨트롤러 빌드

GPU 실기기 검증에는 사용자 계정의 Beam 토큰과 Kaggle 인증이 필요하다. 저장소에는 인증정보를 넣지 않는다.

## 현재 남은 실기기 단계

1. Beam 계정에서 `workers/beam_helios.py:generate` 배포
2. Beam 호출 URL을 `.env.video`에 입력
3. 5초·30초·60초 Helios 벤치마크 실행
4. Kaggle 비공개 커널 첫 실행 및 GPU 할당 확인
5. 사용할 HF Space의 `view_api()` 결과에 맞춰 JSON 템플릿 입력
6. 실제 하루 사용량을 7일간 측정해 일일 가드 조정
