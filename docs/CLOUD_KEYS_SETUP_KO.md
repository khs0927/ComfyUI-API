# Beam·Kaggle·Hugging Face 키 등록 가이드

## 보안 원칙

- API 키를 ChatGPT 대화, GitHub 코드, `.env.video.example`, 이슈 또는 PR에 붙여 넣지 않는다.
- 실제 값은 사용자 PC 환경변수, Beam CLI 설정, Kaggle 공식 인증 파일 또는 배포 플랫폼 Secret에만 저장한다.
- 키가 노출되면 즉시 폐기하고 새 키를 발급한다.

## 1. Kaggle API 키 등록

### 권장 방식: 현재 API Token 환경변수

Kaggle 설정 화면에서 API Token을 발급한 뒤 PowerShell에서 다음처럼 현재 사용자 환경변수로 저장한다.

```powershell
$token = Read-Host "Kaggle API Token" -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($token)
try {
  $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
  [Environment]::SetEnvironmentVariable("KAGGLE_API_TOKEN", $plain, "User")
} finally {
  [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
}
```

새 PowerShell 창을 연 뒤 확인한다.

```powershell
python -m pip install --upgrade kaggle
kaggle --version
kaggle kernels list -m --page-size 1
```

토큰 값 자체를 출력하는 명령은 실행하지 않는다.

### 기존 `kaggle.json` 파일을 보유한 경우

Kaggle에서 내려받은 파일을 다음 위치에 둔다.

```text
%USERPROFILE%\.kaggle\kaggle.json
```

PowerShell:

```powershell
New-Item -ItemType Directory -Force "$HOME\.kaggle" | Out-Null
Copy-Item "C:\다운로드\kaggle.json" "$HOME\.kaggle\kaggle.json"
```

WSL 또는 Linux:

```bash
mkdir -p ~/.kaggle
cp /path/to/kaggle.json ~/.kaggle/kaggle.json
chmod 600 ~/.kaggle/kaggle.json
```

파일 형식은 다음과 같지만 실제 값은 저장소에 넣지 않는다.

```json
{"username":"YOUR_USERNAME","key":"YOUR_API_KEY"}
```

### 프로젝트 환경변수

```powershell
[Environment]::SetEnvironmentVariable(
  "KAGGLE_KERNEL_ID",
  "사용자이름/open-video-worker",
  "User"
)
```

새 터미널에서 배포한다.

```powershell
bash scripts/deploy_kaggle_worker.sh
```

Windows에 Bash가 없다면 WSL 또는 Git Bash에서 실행한다.

## 2. Beam API 키 발급 및 등록

1. Beam에 로그인한다.
2. Dashboard의 **API Keys** 화면에서 새 키를 만든다.
3. 키는 생성 직후 안전한 암호 관리자에 저장한다.
4. PowerShell 사용자 환경변수와 Beam CLI에 등록한다.

```powershell
$token = Read-Host "Beam API Token" -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($token)
try {
  $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
  [Environment]::SetEnvironmentVariable("BEAM_TOKEN", $plain, "User")
  python -m pip install --upgrade beam-client
  beam configure default --token $plain
} finally {
  [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
}
```

Beam CLI는 설정을 사용자 홈의 Beam 설정 파일에 저장한다. 새 터미널에서 다음을 실행한다.

```powershell
beam machine list
bash scripts/deploy_beam_helios.sh
```

배포가 끝나면 터미널에 표시되는 invocation URL을 저장한다.

```powershell
[Environment]::SetEnvironmentVariable(
  "BEAM_TASK_QUEUE_URL",
  "https://배포결과에-표시된-url",
  "User"
)
```

배포 예제의 Authorization 헤더가 `Bearer`이면 기본값을 유지한다.

```powershell
[Environment]::SetEnvironmentVariable("BEAM_AUTH_SCHEME", "Bearer", "User")
```

배포 출력이 명시적으로 `Basic`을 요구할 때만 다음으로 바꾼다.

```powershell
[Environment]::SetEnvironmentVariable("BEAM_AUTH_SCHEME", "Basic", "User")
```

## 3. Hugging Face Token

공개 ZeroGPU Space 호출도 로그인 사용량을 적용하려면 Read 권한 Token을 사용하는 것이 좋다.

```powershell
$token = Read-Host "Hugging Face Read Token" -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($token)
try {
  $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
  [Environment]::SetEnvironmentVariable("HF_TOKEN", $plain, "User")
} finally {
  [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
}
[Environment]::SetEnvironmentVariable("HF_VIDEO_SPACE_PRESET", "helios_realtime", "User")
```

## 4. 등록 확인

새 PowerShell 창을 열고 값 자체를 출력하지 않는 다음 진단을 실행한다.

```powershell
python -c "import os; print({k: bool(os.getenv(k)) for k in ['KAGGLE_API_TOKEN','BEAM_TOKEN','BEAM_TASK_QUEUE_URL','HF_TOKEN']})"
python -m pytest -q tests
```

API 서버를 실행한 뒤 공급자 상태를 확인한다.

```powershell
uvicorn video_api:app --host 127.0.0.1 --port 7860
```

다른 터미널:

```powershell
Invoke-RestMethod http://127.0.0.1:7860/v1/providers
```

## 5. 무료 사용량 안전장치

```env
BEAM_DAILY_VIDEO_SECONDS=1200
KAGGLE_DAILY_VIDEO_SECONDS=600
HF_DAILY_VIDEO_SECONDS=120
VIDEO_SCENE_PARALLELISM=1
```

이 값은 결과 영상 길이 기준의 로컬 보호장치다. 실제 외부 서비스 과금 또는 할당량 조회값은 아니므로 Beam Dashboard의 Usage도 함께 확인한다.
