# 启动 CODESYS Control Win V3 - x64 软 PLC 服务（需管理员权限，由 UAC 提权调用）
# 结果写入 workspace/start_runtime.log 供外层读取
$log = 'D:\Study\SIMENS PLC\workspace\start_runtime.log'
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
try {
    Start-Service -Name 'CODESYS Control Win V3 - x64' -ErrorAction Stop
    'START OK' | Out-File $log -Encoding utf8
} catch {
    ("Start-Service FAILED: " + $_.Exception.Message) | Out-File $log -Encoding utf8
    try {
        $r = & sc.exe start 'CODESYS Control Win V3 - x64' 2>&1
        ("sc.exe start output: " + ($r -join ' / ')) | Out-File $log -Append -Encoding utf8
    } catch {
        ("sc.exe also failed: " + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    }
}
