param(
    [string]$KaggleKernelId = "",
    [switch]$SkipKaggle,
    [switch]$SkipBeam,
    [switch]$SkipHuggingFace
)

$ErrorActionPreference = "Stop"

function Read-SecretPlainText([string]$Label) {
    $secure = Read-Host $Label -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }
}

function Set-UserSecret([string]$Name, [string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        throw "$Name cannot be empty."
    }
    [Environment]::SetEnvironmentVariable($Name, $Value, "User")
    Set-Item -Path "Env:$Name" -Value $Value
    Write-Host "$Name registered for the current user."
}

if (-not $SkipKaggle) {
    $kaggleToken = Read-SecretPlainText "Kaggle API Token"
    Set-UserSecret "KAGGLE_API_TOKEN" $kaggleToken
    if (-not [string]::IsNullOrWhiteSpace($KaggleKernelId)) {
        [Environment]::SetEnvironmentVariable("KAGGLE_KERNEL_ID", $KaggleKernelId, "User")
        $env:KAGGLE_KERNEL_ID = $KaggleKernelId
        Write-Host "KAGGLE_KERNEL_ID registered."
    }
}

if (-not $SkipBeam) {
    $beamToken = Read-SecretPlainText "Beam API Token"
    Set-UserSecret "BEAM_TOKEN" $beamToken
    [Environment]::SetEnvironmentVariable("BEAM_AUTH_SCHEME", "Bearer", "User")
    $env:BEAM_AUTH_SCHEME = "Bearer"

    python -m pip install --upgrade beam-client
    beam configure default --token $beamToken
    Write-Host "Beam CLI configured."
}

if (-not $SkipHuggingFace) {
    $hfToken = Read-SecretPlainText "Hugging Face Read Token"
    Set-UserSecret "HF_TOKEN" $hfToken
    [Environment]::SetEnvironmentVariable(
        "HF_VIDEO_SPACE_PRESET",
        "helios_realtime",
        "User"
    )
    $env:HF_VIDEO_SPACE_PRESET = "helios_realtime"
    Write-Host "Hugging Face Helios preset enabled."
}

Write-Host "Credential registration complete. Open a new terminal before deployment."
Write-Host "No secret value was printed by this script."
