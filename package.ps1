<#
.SYNOPSIS
    Упаковка «Учёт ТО» для передачи пользователю: dist\TO-Uchet-v2.zip.

.DESCRIPTION
    Собирает папку dist\package\TO-Uchet\:
        TO-Uchet.exe       — программа (параметр -Exe)
        data\to.db         — база, если есть dist\data\to.db (без -wal/-shm, backups, app.log, server.json, photos);
                             без неё программа создаст пустую базу при первом запуске
        Instrukciya.pdf    — инструкция, если есть файл из параметра -Guide
        README.txt         — коротко, как запустить (UTF-8 с BOM, строки CRLF — читается Блокнотом старой Windows)
    и сжимает её в dist\TO-Uchet-v2.zip (перезаписывается). Имена внутри архива — только латиница:
    на Windows 7 кириллица в zip превращается в «кракозябры».
    В конце — размер архива, список файлов и проверка: архив распаковывается во временную папку,
    список файлов и их содержимое (SHA-256) сравниваются с исходной папкой.

    Совместим с Windows PowerShell 5.1. Запуск из корня проекта:
        powershell -ExecutionPolicy Bypass -File .\package.ps1
        powershell -ExecutionPolicy Bypass -File .\package.ps1 -Exe dist\TO-Uchet-x64.exe

.PARAMETER Exe
    Какой exe положить в архив (внутри он всегда называется TO-Uchet.exe). По умолчанию dist\TO-Uchet.exe.

.PARAMETER Guide
    PDF-инструкция для пользователя (внутри архива — Instrukciya.pdf). По умолчанию dist\Instrukciya.pdf;
    файла нет — архив собирается без инструкции.
#>
param(
    [string]$Exe = 'dist\TO-Uchet.exe',
    [string]$Guide = 'dist\Instrukciya.pdf'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$root = $PSScriptRoot
if (-not $root) { $root = Split-Path -Parent $MyInvocation.MyCommand.Path }

function Resolve-InProject([string]$path) {
    if ([System.IO.Path]::IsPathRooted($path)) { return $path }
    return (Join-Path $root $path)
}

function Format-Size([long]$bytes) {
    if ($bytes -ge 1MB) { return ('{0:N1} МБ' -f ($bytes / 1MB)) }
    return ('{0:N0} КБ' -f [math]::Ceiling($bytes / 1KB))
}

function Get-FileList([string]$base) {
    # относительный путь (с \), размер и SHA-256 каждого файла в папке
    $baseFull = (Resolve-Path $base).ProviderPath.TrimEnd('\') + '\'
    Get-ChildItem -LiteralPath $base -Recurse -File | Sort-Object FullName | ForEach-Object {
        [pscustomobject]@{
            Path   = $_.FullName.Substring($baseFull.Length)
            Length = $_.Length
            Hash   = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
        }
    }
}

# ───── что кладём ─────

$exePath  = Resolve-InProject $Exe
$dbPath   = Join-Path $root 'dist\data\to.db'
$pdfPath  = Resolve-InProject $Guide
$pkgBase  = Join-Path $root 'dist\package'
$pkgDir   = Join-Path $pkgBase 'TO-Uchet'
$zipPath  = Join-Path $root 'dist\TO-Uchet-v2.zip'

if (-not (Test-Path -LiteralPath $exePath -PathType Leaf)) { throw "Нет файла: $exePath — сначала соберите exe (build.bat или build-compat.bat)" }
$withDb  = Test-Path -LiteralPath $dbPath -PathType Leaf
$withPdf = Test-Path -LiteralPath $pdfPath -PathType Leaf

# База в режиме WAL: если рядом лежит непустой to.db-wal, часть данных ещё не перенесена в to.db
# (программа запущена или закрылась аварийно) — копия одного to.db была бы неполной.
$wal = $dbPath + '-wal'
if ($withDb -and (Test-Path -LiteralPath $wal) -and ((Get-Item -LiteralPath $wal).Length -gt 0)) {
    throw "Рядом с базой есть непустой $wal — закройте программу (TO-Uchet.exe из dist) и подождите пару минут, потом повторите."
}

Write-Host "Программа:  $exePath ($(Format-Size (Get-Item -LiteralPath $exePath).Length), $((Get-Item -LiteralPath $exePath).LastWriteTime.ToString('dd.MM.yyyy HH:mm')))"
if ($withDb) {
    Write-Host "База:       $dbPath ($(Format-Size (Get-Item -LiteralPath $dbPath).Length))"
} else {
    Write-Host "База:       нет dist\data\to.db — программа создаст пустую базу при первом запуске"
}
if ($withPdf) {
    Write-Host "Инструкция: $pdfPath ($(Format-Size (Get-Item -LiteralPath $pdfPath).Length))"
} else {
    Write-Host "Инструкция: нет $pdfPath — архив без PDF-инструкции"
}

# ───── папка пакета (каждый раз заново) ─────

if (Test-Path -LiteralPath $pkgDir) { Remove-Item -LiteralPath $pkgDir -Recurse -Force }
New-Item -ItemType Directory -Path $pkgDir -Force | Out-Null

Copy-Item -LiteralPath $exePath -Destination (Join-Path $pkgDir 'TO-Uchet.exe')
if ($withDb) {
    New-Item -ItemType Directory -Path (Join-Path $pkgDir 'data') -Force | Out-Null
    Copy-Item -LiteralPath $dbPath -Destination (Join-Path $pkgDir 'data\to.db')
}
if ($withPdf) { Copy-Item -LiteralPath $pdfPath -Destination (Join-Path $pkgDir 'Instrukciya.pdf') }

# Страховка для старой Windows 7 без обновлений: там нет Universal CRT, и 32-битный exe на Python 3.8 не стартует
# («отсутствует api-ms-win-crt-runtime-l1-1-0.dll»). Официальный установщик Microsoft (aka.ms/vs/17/release/vc_redist.x86.exe)
# кладётся рядом, если лежит в dist\ (скачивается вручную, подпись проверяется Get-AuthenticodeSignature).
$redistPath = Join-Path $root 'dist\vc_redist.x86.exe'
$withRedist = Test-Path -LiteralPath $redistPath -PathType Leaf
if ($withRedist) {
    $sig = Get-AuthenticodeSignature -LiteralPath $redistPath
    if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch 'Microsoft Corporation') {
        throw "dist\vc_redist.x86.exe не подписан Microsoft (статус: $($sig.Status)) — скачайте заново с aka.ms/vs/17/release/vc_redist.x86.exe"
    }
    Copy-Item -LiteralPath $redistPath -Destination (Join-Path $pkgDir 'vc_redist.x86.exe')
    Write-Host "VC++ Redistributable: $redistPath ($(Format-Size (Get-Item -LiteralPath $redistPath).Length)), подпись Microsoft — в порядке"
} else {
    Write-Warning "Нет dist\vc_redist.x86.exe — архив собирается без него (на Windows 7 без обновлений программа может не запуститься)."
}

$steps = @(
    'Распакуйте архив целиком: правой кнопкой по архиву → «Извлечь всё…».',
    'Откройте папку TO-Uchet и запустите TO-Uchet.exe. Через 5–10 секунд откроется окно программы.',
    'Если Windows пишет «Система Windows защитила ваш компьютер» — нажмите «Подробнее», затем «Выполнить в любом случае».',
    'Не разделяйте TO-Uchet.exe и папку data: в data лежит ваша база, они должны быть в одной папке.'
)
if ($withPdf) { $steps += 'Подробная инструкция с картинками — в файле Instrukciya.pdf.' }
if ($withRedist) {
    $steps += 'Только для старой Windows 7: если при запуске пишет «отсутствует api-ms-win-crt-runtime-l1-1-0.dll», запустите vc_redist.x86.exe (установщик Microsoft, без интернета) и попробуйте снова.'
}
$readme = @('Учёт ТО 2.0 — программа учёта ТО автопарка. Работает без интернета.')
for ($i = 0; $i -lt $steps.Count; $i++) { $readme += ('{0}. {1}' -f ($i + 1), $steps[$i]) }
$readme = $readme -join "`r`n"
[System.IO.File]::WriteAllText((Join-Path $pkgDir 'README.txt'), $readme + "`r`n", (New-Object System.Text.UTF8Encoding($true)))

# только латиница в именах (Windows 7 портит кириллицу в zip)
$bad = Get-ChildItem -LiteralPath $pkgDir -Recurse | Where-Object { $_.Name -notmatch '^[A-Za-z0-9._ -]+$' }
if ($bad) { throw ('Имена не латиницей: ' + (($bad | ForEach-Object { $_.FullName }) -join ', ')) }

# ───── архив ─────

if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
Compress-Archive -LiteralPath $pkgDir -DestinationPath $zipPath -CompressionLevel Optimal -Force

$zip = Get-Item -LiteralPath $zipPath
Write-Host ''
Write-Host "Архив: $($zip.FullName) — $(Format-Size $zip.Length)"
Write-Host 'Внутри:'
$source = @(Get-FileList $pkgDir)
foreach ($f in $source) { Write-Host ('  {0,-28} {1,10}' -f ('TO-Uchet\' + $f.Path), (Format-Size $f.Length)) }

# ───── проверка: распаковать во временную папку и сравнить ─────

$check = Join-Path ([System.IO.Path]::GetTempPath()) ('TO-Uchet-check-' + [guid]::NewGuid().ToString('N'))
try {
    Expand-Archive -LiteralPath $zipPath -DestinationPath $check
    $top = @(Get-ChildItem -LiteralPath $check | ForEach-Object { $_.Name })
    if (($top.Count -ne 1) -or ($top[0] -ne 'TO-Uchet')) { throw ('В корне архива ожидалась одна папка TO-Uchet, а там: ' + ($top -join ', ')) }
    $unpacked = @(Get-FileList (Join-Path $check 'TO-Uchet'))
    $diff = Compare-Object -ReferenceObject $source -DifferenceObject $unpacked -Property Path, Length, Hash
    if ($diff) {
        $diff | Format-Table -AutoSize | Out-String | Write-Host
        throw 'Проверка не прошла: распакованный архив отличается от папки пакета.'
    }
    # имена записей в самом zip: разделитель и латиница
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $za = [System.IO.Compression.ZipFile]::OpenRead($zipPath)
    try {
        $entries = @($za.Entries | ForEach-Object { $_.FullName })
    } finally {
        $za.Dispose()
    }
    $nonAscii = @($entries | Where-Object { $_ -match '[^\x20-\x7E]' })
    if ($nonAscii.Count) { throw ('В архиве имена не латиницей: ' + ($nonAscii -join ', ')) }
    $sep = if (@($entries | Where-Object { $_.Contains('\') }).Count) { 'обратная косая черта (\) — так пишет Compress-Archive из PowerShell 5.1; Проводник Windows такие архивы распаковывает' } else { 'прямая косая черта (/)' }
    Write-Host ''
    Write-Host "Проверка: архив распакован, файлов $($unpacked.Count), состав и содержимое (SHA-256) совпадают с папкой пакета."
    Write-Host "Разделитель путей в архиве: $sep."
} finally {
    if (Test-Path -LiteralPath $check) { Remove-Item -LiteralPath $check -Recurse -Force }
}
