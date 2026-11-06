# 停止 xAgent 依赖的本地 MCP Server
#
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\stop_mcp_servers.ps1
#   .\stop_mcp_servers.ps1 -IncludePetHospitalApi   # 同时停止 8080 后端

[CmdletBinding()]
param(
    [switch]$IncludePetHospitalApi
)

try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
}
catch {
}

$ports = @(8090, 8091, 8092)
if ($IncludePetHospitalApi) {
    $ports += 8080
}

foreach ($port in $ports) {
    $connections = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
    $processIds = @($connections | Select-Object -ExpandProperty OwningProcess -Unique)
    if ($processIds.Count -eq 0) {
        Write-Host ("  [未运行] 端口 {0}" -f $port)
        continue
    }

    foreach ($processId in $processIds) {
        try {
            Stop-Process -Id $processId -Force -ErrorAction Stop
            Write-Host ("  [已停止] 端口 {0}，PID {1}" -f $port, $processId)
        }
        catch {
            Write-Warning ("停止端口 {0} 的进程 {1} 失败：{2}" -f $port, $processId, $_.Exception.Message)
        }
    }
}
