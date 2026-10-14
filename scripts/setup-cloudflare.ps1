$ErrorActionPreference = "Stop"

$workerUrl = "https://aiap-topic-moderator.external-bot.workers.dev"
$workerDirectory = Split-Path -Parent $PSScriptRoot
$secretFile = Join-Path $env:TEMP ("aiap-worker-secrets-" + [guid]::NewGuid().ToString("N") + ".json")
$secretPointer = [IntPtr]::Zero
$tokenSecure = $null
$botToken = $null
$webhookSecret = $null

try {
    $npx = Get-Command npx.cmd -ErrorAction SilentlyContinue
    if (-not $npx) {
        $nodeBin = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages\OpenJS.NodeJS.LTS_Microsoft.Winget.Source_8wekyb3d8bbwe\node-v24.20.0-win-x64"
        $npxPath = Join-Path $nodeBin "npx.cmd"
        if (Test-Path -LiteralPath $npxPath) {
            $npx = Get-Item -LiteralPath $npxPath
        }
    }
    if (-not $npx) {
        throw "Node.js and npm are required. Install them, then run this script again."
    }

    $npxPath = $npx.Source
    if (-not $npxPath) {
        $npxPath = $npx.FullName
    }
    $nodeBin = Split-Path -Parent $npxPath
    $env:PATH = "$nodeBin;$env:PATH"

    $tokenSecure = Read-Host "Enter your BotFather token (input is hidden)" -AsSecureString
    $secretPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($tokenSecure)
    $botToken = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($secretPointer)
    if ([string]::IsNullOrWhiteSpace($botToken)) {
        throw "A BotFather token is required."
    }

    $randomBytes = New-Object byte[] 32
    $randomGenerator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $randomGenerator.GetBytes($randomBytes)
    }
    finally {
        $randomGenerator.Dispose()
    }
    $webhookSecret = [BitConverter]::ToString($randomBytes).Replace("-", "").ToLowerInvariant()

    $secretJson = @{
        TELEGRAM_BOT_TOKEN = $botToken
        WEBHOOK_SECRET = $webhookSecret
    } | ConvertTo-Json -Compress
    [IO.File]::WriteAllText($secretFile, $secretJson, [Text.UTF8Encoding]::new($false))

    Push-Location $workerDirectory
    try {
        & $npxPath --yes wrangler@4.149.0 secret bulk $secretFile
        if ($LASTEXITCODE -ne 0) {
            throw "Cloudflare secret upload failed."
        }
    }
    finally {
        Pop-Location
    }

    $telegramBody = @{
        url = "$workerUrl/telegram/webhook"
        secret_token = $webhookSecret
        allowed_updates = @("message", "edited_message")
    } | ConvertTo-Json -Compress
    try {
        $telegramResult = Invoke-RestMethod `
            -Uri "https://api.telegram.org/bot$botToken/setWebhook" `
            -Method Post `
            -ContentType "application/json" `
            -Body $telegramBody
    }
    catch {
        throw "Telegram webhook registration failed. Check the BotFather token and try again."
    }

    if (-not $telegramResult.ok) {
        throw "Telegram did not confirm webhook registration."
    }

    Write-Host "Telegram webhook registered successfully at $workerUrl/telegram/webhook."
}
catch {
    Write-Error $_.Exception.Message
    exit 1
}
finally {
    if (Test-Path -LiteralPath $secretFile) {
        Remove-Item -LiteralPath $secretFile -Force
    }
    if ($secretPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($secretPointer)
    }
    if ($tokenSecure) {
        $tokenSecure.Dispose()
    }
    $botToken = $null
    $webhookSecret = $null
    $telegramBody = $null
    $secretJson = $null
}
