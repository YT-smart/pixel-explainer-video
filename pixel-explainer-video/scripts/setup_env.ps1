# Windows 一键准备环境（免安装，全部放在 %USERPROFILE%\tools，不改系统设置）
#   python 3.12 嵌入版 + pip + pillow numpy faster-whisper edge-tts
#   ffmpeg（gyan.dev essentials 版）
# 用法: powershell -ExecutionPolicy Bypass -File setup_env.ps1
# 已存在的部分会跳过。下载约 140MB；首次识别语音时还会下载约 460MB 模型。
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = 'Tls12'
$ProgressPreference = 'SilentlyContinue'
$d = Join-Path $env:USERPROFILE 'tools'
New-Item -ItemType Directory -Force $d | Out-Null

$py = Join-Path $d 'python\python.exe'
if (-not (Test-Path $py)) {
    # 官方安装器在部分服务器上会因路径问题失败（0x80070003），嵌入版最稳
    Invoke-WebRequest 'https://www.python.org/ftp/python/3.12.7/python-3.12.7-embed-amd64.zip' -OutFile "$d\pyembed.zip"
    Expand-Archive "$d\pyembed.zip" (Join-Path $d 'python') -Force
    $pth = Join-Path $d 'python\python312._pth'
    (Get-Content $pth) -replace '#import site', 'import site' | Set-Content $pth
    Invoke-WebRequest 'https://bootstrap.pypa.io/get-pip.py' -OutFile "$d\get-pip.py"
    & $py "$d\get-pip.py" -q --no-warn-script-location
}
& $py -m pip install -q --no-warn-script-location pillow numpy faster-whisper edge-tts

$ff = Join-Path $d 'ffbin\ffmpeg.exe'
if (-not (Test-Path $ff)) {
    Invoke-WebRequest 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile "$d\ffmpeg.zip"
    Expand-Archive "$d\ffmpeg.zip" "$d\ffm" -Force
    $bin = (Get-ChildItem "$d\ffm" -Directory)[0].FullName + '\bin'
    Move-Item $bin (Join-Path $d 'ffbin')
}
& $py -c "import PIL, numpy, faster_whisper; print('python OK')"
& $ff -version | Select-Object -First 1
Write-Output "PYTHON=$py"
Write-Output "FFMPEG=$ff"
