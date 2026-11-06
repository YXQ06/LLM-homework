# 启动 xAgent 依赖的本地 MCP Server
#
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\start_mcp_servers.ps1
#   .\start_mcp_servers.ps1 -NoPetHospitalApi   # 只启动 MCP，不启动宠物医院 REST API
#
# 服务与端口：
#   8080  PetHospitalServer REST API（pethospital.exe，可选）
#   8090  PetHospitalMCP             Streamable HTTP
#   8091  AnythingLLMMCP             Streamable HTTP
#   8092  Bazi-MCP                  Streamable HTTP（本地 bazi-mcp 包）
#
# 脚本可重复执行：端口已经在监听时会跳过，不会重复启动。

[CmdletBinding()]
param(
    [switch]$NoPetHospitalApi
)

$ErrorActionPreference = "Continue"

try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
}
catch {
}

$ProjectDir = Split-Path -Parent $PSCommandPath
$AgentDir = Split-Path -Parent $ProjectDir
$CourseDir = Split-Path -Parent $AgentDir
$LogDir = Join-Path $ProjectDir "logs"

$PetHospitalWindowsDir = Join-Path $CourseDir "PetHospitalServer\windows"
$PetHospitalMcpDir = Join-Path $PetHospitalWindowsDir "mcp_mvp"
$AnythingLLMDir = Join-Path $CourseDir "AnythingLLMMCP"
$BaziHttpDir = Join-Path $ProjectDir ".tools\bazi-http"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Test-TcpPort {
    param(
        [int]$Port,
        [int]$TimeoutMs = 400
    )
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne($TimeoutMs)) {
            return $false
        }
        $client.EndConnect($iar)
        return $true
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

function Wait-TcpPort {
    param(
        [int]$Port,
        [int]$TimeoutSeconds = 30
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-TcpPort -Port $Port) {
            return $true
        }
        Start-Sleep -Milliseconds 300
    }
    return (Test-TcpPort -Port $Port)
}

function Start-HiddenService {
    param(
        [string]$Name,
        [string]$FilePath,
        [object[]]$ArgumentList,
        [string]$WorkingDirectory,
        [string]$LogName,
        [hashtable]$Environment
    )

    $stdout = Join-Path $LogDir "$LogName.out.log"
    $stderr = Join-Path $LogDir "$LogName.err.log"
    $previous = @{}

    try {
        foreach ($key in $Environment.Keys) {
            $previous[$key] = [Environment]::GetEnvironmentVariable($key, "Process")
            [Environment]::SetEnvironmentVariable($key, [string]$Environment[$key], "Process")
        }

        $process = Start-Process `
            -FilePath $FilePath `
            -ArgumentList $ArgumentList `
            -WorkingDirectory $WorkingDirectory `
            -WindowStyle Hidden `
            -RedirectStandardOutput $stdout `
            -RedirectStandardError $stderr `
            -PassThru

        Write-Output ("  [启动] {0} (PID {1})" -f $Name, $process.Id)
        Write-Output ("         日志: {0}" -f $stdout)
        return $process
    }
    catch {
        Write-Warning ("启动 {0} 失败：{1}" -f $Name, $_.Exception.Message)
        return $null
    }
    finally {
        foreach ($key in $Environment.Keys) {
            [Environment]::SetEnvironmentVariable($key, $previous[$key], "Process")
        }
    }
}

Write-Output "xAgent 本地 MCP Server 启动检查"
Write-Output ("项目目录: {0}" -f $ProjectDir)
Write-Output ""

# 1) 宠物医院 REST API（MCP 工具的实际上游）
if (-not $NoPetHospitalApi) {
    if (Test-TcpPort -Port 8080) {
        Write-Output "  [已运行] PetHospital REST API (8080)"
    }
    else {
        $exe = Join-Path $PetHospitalWindowsDir "pethospital.exe"
        if (Test-Path $exe) {
            Start-HiddenService `
                -Name "PetHospital REST API" `
                -FilePath $exe `
                -ArgumentList @("-no-color") `
                -WorkingDirectory $PetHospitalWindowsDir `
                -LogName "pethospital-api" | Out-Null
        }
        else {
            Write-Warning ("未找到宠物医院后端：{0}" -f $exe)
        }
    }
}
else {
    Write-Output "  [跳过] PetHospital REST API (8080)"
}

# 2) PetHospitalMCP（8090）
if (Test-TcpPort -Port 8090) {
    Write-Output "  [已运行] PetHospitalMCP (8090)"
}
else {
    Start-HiddenService `
        -Name "PetHospitalMCP" `
        -FilePath "python" `
        -ArgumentList @("server.py") `
        -WorkingDirectory $PetHospitalMcpDir `
        -LogName "pethospital-mcp" `
        -Environment @{ MCP_HOST = "127.0.0.1"; MCP_PORT = "8090" } | Out-Null
}

# 3) Bazi-MCP（8092，本地 Streamable HTTP，使用 bazi-mcp 包）
if (Test-TcpPort -Port 8092) {
    Write-Output "  [已运行] Bazi-MCP (8092)"
}
else {
    $baziEntry = Join-Path $BaziHttpDir "node_modules\bazi-mcp\dist\httpServer.js"
    if (Test-Path $baziEntry) {
        Start-HiddenService `
            -Name "Bazi-MCP" `
            -FilePath "node" `
            -ArgumentList @("node_modules/bazi-mcp/dist/httpServer.js") `
            -WorkingDirectory $BaziHttpDir `
            -LogName "bazi-mcp-http" `
            -Environment @{ PORT = "8092" } | Out-Null
    }
    else {
        Write-Warning ("未找到 Bazi-MCP HTTP 入口：{0}" -f $baziEntry)
    }
}

# 4) AnythingLLMMCP（8091）
if (Test-TcpPort -Port 8091) {
    Write-Output "  [已运行] AnythingLLMMCP (8091)"
}
else {
    $anythingEnv = @{ MCP_HOST = "127.0.0.1"; MCP_PORT = "8091" }
    if ($env:ANYTHINGLLM_API_KEY) {
        $anythingEnv["ANYTHINGLLM_API_KEY"] = $env:ANYTHINGLLM_API_KEY
    }
    else {
        Write-Warning "未设置 ANYTHINGLLM_API_KEY：AnythingLLMMCP 可以连接，但工具调用会返回缺少 API Key 的错误。"
    }
    if ($env:ANYTHINGLLM_BASE_URL) {
        $anythingEnv["ANYTHINGLLM_BASE_URL"] = $env:ANYTHINGLLM_BASE_URL
    }

    Start-HiddenService `
        -Name "AnythingLLMMCP" `
        -FilePath "python" `
        -ArgumentList @("server.py") `
        -WorkingDirectory $AnythingLLMDir `
        -LogName "anythingllm-mcp" `
        -Environment $anythingEnv | Out-Null
}

Write-Output ""
Write-Output "等待服务端口就绪..."
$checks = @(
    @{ Name = "PetHospital REST API"; Port = 8080; Url = "http://127.0.0.1:8080/"; Required = (-not $NoPetHospitalApi) },
    @{ Name = "PetHospitalMCP"; Port = 8090; Url = "http://127.0.0.1:8090/mcp"; Required = $true },
    @{ Name = "AnythingLLMMCP"; Port = 8091; Url = "http://127.0.0.1:8091/mcp"; Required = $true },
    @{ Name = "Bazi-MCP"; Port = 8092; Url = "http://127.0.0.1:8092/mcp"; Required = $true }
)

$failed = $false
foreach ($check in $checks) {
    if (-not $check.Required) {
        continue
    }
    if (Wait-TcpPort -Port $check.Port -TimeoutSeconds 30) {
        Write-Output ("  [在线] {0}  {1}" -f $check.Name, $check.Url)
    }
    else {
        Write-Warning ("  [离线] {0}  {1}" -f $check.Name, $check.Url)
        $failed = $true
    }
}

Write-Output ""
if ($failed) {
    Write-Output "部分服务启动失败，请查看日志目录：$LogDir"
    exit 1
}

Write-Output "全部 MCP 服务已就绪。现在可以启动 xAgent（已打开的 TUI 需要重启才能重新连接）。"
exit 0
