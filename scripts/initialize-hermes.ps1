# Creates dashboard credentials once; existing credentials are never replaced.
$ErrorActionPreference = 'Stop'

$existing = kubectl get secret hermes-dashboard -n homelab --ignore-not-found -o name
if ($LASTEXITCODE -ne 0) { throw 'Could not check the Hermes dashboard secret.' }
if ($existing) {
    Write-Output 'Hermes dashboard credentials already exist.'
    return
}

function New-HermesSecret {
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return [Convert]::ToBase64String($bytes)
}

$secret = @{
    apiVersion = 'v1'
    kind = 'Secret'
    metadata = @{ name = 'hermes-dashboard'; namespace = 'homelab' }
    type = 'Opaque'
    stringData = @{
        HERMES_DASHBOARD_BASIC_AUTH_USERNAME = 'admin'
        HERMES_DASHBOARD_BASIC_AUTH_PASSWORD = New-HermesSecret
        HERMES_DASHBOARD_BASIC_AUTH_SECRET = New-HermesSecret
    }
}
$secret | ConvertTo-Json -Depth 5 -Compress | kubectl create -f -
if ($LASTEXITCODE -ne 0) { throw 'Could not create the Hermes dashboard secret.' }
Write-Output 'Hermes dashboard credentials created in the cluster; no secret was saved to Git.'
