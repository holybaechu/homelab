#requires -Version 7.4
[CmdletBinding()]
param(
    [string]$TokenFile
)
$ErrorActionPreference = 'Stop'
$namespace = 'homelab'
$name = 'hermes-browser-vault'
$stage = 'prerequisites'
$nativeExitCode = $null

function Invoke-Kubectl([string[]]$Arguments, [string]$InputJson = '') {
    $info = [System.Diagnostics.ProcessStartInfo]::new()
    $info.FileName = (Get-Command kubectl -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
    $info.UseShellExecute = $false
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.StandardInputEncoding = [System.Text.UTF8Encoding]::new($false)
    foreach ($argument in $Arguments) { $info.ArgumentList.Add($argument) }
    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $info
    try {
        $null = $process.Start()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if ($InputJson) { $process.StandardInput.Write($InputJson) }
        $process.StandardInput.Close()
        if (-not $process.WaitForExit(30000)) {
            $process.Kill($true)
            throw 'timeout'
        }
        $script:nativeExitCode = $process.ExitCode
        if ($process.ExitCode -ne 0) { throw 'kubectl_failed' }
        # Never print raw kubectl output: admission errors can quote Secret data.
        return $stdout.GetAwaiter().GetResult()
    } finally {
        $process.Dispose()
    }
}

function Read-Bootstrap {
    $raw = Invoke-Kubectl @('-n', $namespace, 'get', 'secret', $name, '--ignore-not-found', '-o', 'json')
    if ([string]::IsNullOrWhiteSpace($raw)) { return $null }
    $object = ConvertFrom-Json -AsHashtable -InputObject $raw
    if ($object.kind -ne 'Secret' -or $object.apiVersion -ne 'v1' -or
        $object.metadata.name -ne $name -or $object.metadata.namespace -ne $namespace -or
        $object.type -ne 'Opaque' -or $object.metadata.ownerReferences -or
        $object.metadata.deletionTimestamp -or $object.immutable -or
        $object.data.Count -ne 1 -or -not $object.data.ContainsKey('connect-token')) {
        throw 'bootstrap_contract_mismatch'
    }
    return $object
}

try {
    $null = Get-Command kubectl -CommandType Application
    $stage = 'read_token'
    if ($TokenFile) {
        # Point at a private existing file; never pass a token as a command argument.
        $token = [System.IO.File]::ReadAllText((Resolve-Path -LiteralPath $TokenFile).Path)
    } else {
        $secure = Read-Host 'Dedicated read-only Hermes Connect client token (hidden)' -AsSecureString
        $token = [System.Net.NetworkCredential]::new('', $secure).Password
    }
    if (-not $token -or $token.Length -gt 16384 -or $token -match '\s') {
        throw 'invalid_token_file_or_input'
    }
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($token))
    $stage = 'read_existing'
    $existing = Read-Bootstrap
    if ($null -eq $existing -or $existing.data['connect-token'] -cne $encoded) {
        $stage = 'apply_unknown_outcome_on_failure'
        $body = @{
            apiVersion = 'v1'; kind = 'Secret'; type = 'Opaque'
            metadata = @{ name = $name; namespace = $namespace }
            data = @{ 'connect-token' = $encoded }
        } | ConvertTo-Json -Depth 5 -Compress
        $null = Invoke-Kubectl @('-n', $namespace, 'apply', '--server-side', '--field-manager=hermes-browser-vault-bootstrap', '-f', '-') $body
    }
    $stage = 'verify_readback'
    $verified = Read-Bootstrap
    if ($null -eq $verified -or $verified.data['connect-token'] -cne $encoded) {
        throw 'readback_mismatch'
    }
    Write-Output 'PASS: homelab/hermes-browser-vault connect-token exact read-back verified.'
    Write-Output 'This proves bootstrap only, not token validity, vault coverage, or browser login.'
    Write-Output 'No restart requested. After the reviewed deployment, use native browser_vault list and an authorized exact-origin login.'
} catch {
    # Deliberately no exception text, native stderr, response bodies, token or digest.
    $category = 'execution_failed'
    if ($_.Exception.Message -cin @('timeout', 'kubectl_failed', 'bootstrap_contract_mismatch', 'invalid_token_file_or_input', 'readback_mismatch')) {
        $category = $_.Exception.Message
    }
    Write-Error -ErrorAction Continue "Browser-vault bootstrap failed: stage=$stage category=$category native_exit=$nativeExitCode. No automatic retry or rollback. Check kubectl context/RBAC and the exact named Secret privately; rerunning first reads the current state."
    exit 1
} finally {
    $token = $null; $encoded = $null; $body = $null; $existing = $null; $verified = $null
    if ($secure) { $secure.Dispose() }
}
