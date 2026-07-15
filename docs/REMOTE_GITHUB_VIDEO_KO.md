# 모바일 ChatGPT 원격 영상 생성

이 방식은 별도 Vercel 서버나 사용자 PC를 항상 켜두지 않는다.

```text
모바일 ChatGPT
  → GitHub에 [video] 이슈 생성
  → GitHub Actions 실행
  → Beam → Kaggle → Hugging Face 순서로 장면 생성
  → GitHub Actions의 FFmpeg가 MP4 연결
  → 이슈 댓글에 Actions 다운로드 링크 등록
```

## 1회 설정

GitHub 저장소의 Actions secrets에 다음 값을 등록한다.

- `BEAM_TOKEN`
- `KAGGLE_API_TOKEN`
- `HF_TOKEN` — 선택 사항이지만 ZeroGPU 로그인 할당량 사용을 위해 권장

등록 화면:

```text
https://github.com/khs0927/ComfyUI-API/settings/secrets/actions
```

그 다음 Actions의 **Bootstrap free video providers**를 실행한다.

```text
https://github.com/khs0927/ComfyUI-API/actions/workflows/cloud-provider-bootstrap.yml
```

입력:

- `deploy_beam`: true
- `kaggle_kernel_id`: `본인Kaggle사용자명/open-video-worker`
- `submit_kaggle_test`: true
- `inspect_huggingface`: true

워크플로가 다음 비민감 Repository Variables를 자동 저장한다.

- `BEAM_TASK_QUEUE_URL`
- `KAGGLE_KERNEL_ID`

## 모바일 ChatGPT 요청 형식

ChatGPT의 GitHub 연결을 사용해 `khs0927/ComfyUI-API`에 다음 이슈를 만든다.

제목:

```text
[video] 회복과 소망 10분 영상
```

본문:

```json
{
  "prompt": "회복과 소망을 다루는 영화적 다큐멘터리",
  "script": "여기에 전체 원고를 입력",
  "target_duration_minutes": 10,
  "scene_seconds": 30,
  "provider": "auto",
  "aspect_ratio": "16:9",
  "width": 960,
  "height": 544,
  "fps": 24,
  "style": "cinematic, coherent characters, natural motion"
}
```

`provider=auto` 순서:

1. Beam
2. Kaggle
3. Hugging Face

## 결과 받기

작업이 시작되면 이슈에 Actions 실행 링크가 자동 댓글로 달린다.

완료 후:

1. 이슈의 완료 댓글에서 Actions 링크를 연다.
2. 페이지 아래 **Artifacts**를 연다.
3. `ai-video-<job-id>` ZIP을 다운로드한다.
4. ZIP 안의 MP4를 사용한다.

아티팩트 보관 기간은 7일이다.

## 수동 실행

Actions의 **Generate remote AI video**에서도 직접 실행할 수 있다.

```text
https://github.com/khs0927/ComfyUI-API/actions/workflows/generate-remote-video.yml
```

## 보안

- 이슈 본문에는 API 키를 넣지 않는다.
- 키는 GitHub Actions secret에만 저장한다.
- 로그에는 키 값이 출력되지 않는다.
- `BEAM_TASK_QUEUE_URL`과 `KAGGLE_KERNEL_ID`는 비밀이 아니므로 Repository Variable로 관리한다.

## 한계

- 무료 GPU 할당량과 대기열은 공급자가 변경할 수 있다.
- GitHub Actions 한 작업의 최대 실행시간 안에서 완료되어야 한다.
- 하루 10~20분은 실제 생성 속도와 무료 크레딧을 1주 이상 측정해 조정해야 한다.
