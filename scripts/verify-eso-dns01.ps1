[CmdletBinding()]
param([switch]$DiagnoseOnly, [switch]$ResumeStagingPair)

$ErrorActionPreference = 'Stop'
$name = 'eso-retirement-check'
$ns = 'cert-manager'
$issuerCreated = $false
$certificateCreated = $false
$cleanupSafe = $true
try {
    $kubectl = (Get-Command kubectl -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
} catch { throw 'FAIL stage=kubectl-discovery category=application-not-found' }

function Invoke-Kubectl {
    param([string]$Stage, [string[]]$Arguments, [string]$InputJson)
    # Arguments are internal fixed resource names/flags, never credentials or user input.
    if (@($Arguments | Where-Object { $_ -notmatch '^[a-zA-Z0-9./=,:_-]+$' }).Count) {
        throw "FAIL stage=$Stage category=invalid-internal-argument"
    }
    $start = New-Object System.Diagnostics.ProcessStartInfo
    $start.FileName = $kubectl
    $start.Arguments = $Arguments -join ' '
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.RedirectStandardInput = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $start
    try {
        try {
            $null = $process.Start()
            $stdout = $process.StandardOutput.ReadToEndAsync()
            $stderr = $process.StandardError.ReadToEndAsync()
            if ($InputJson) { $process.StandardInput.Write($InputJson) }
            $process.StandardInput.Close()
            $process.WaitForExit()
            $output = $stdout.GetAwaiter().GetResult()
            $errorOutput = $stderr.GetAwaiter().GetResult()
            $exitCode = $process.ExitCode
        } catch { throw "FAIL stage=$Stage category=process-execution" }
        if ($exitCode -ne 0) {
            $category = 'kubectl-exit'
            if ($errorOutput -match '(?i)\bforbidden\b') { $category = 'access-denied' }
            elseif ($errorOutput -match '(?i)\bunauthorized\b|must be logged in|provide credentials') { $category = 'authentication' }
            elseif ($errorOutput -match '(?i)timed out|timeout|deadline exceeded') { $category = 'timeout' }
            elseif ($errorOutput -match '(?i)unable to connect|connection refused|no such host|x509:|TLS handshake') { $category = 'transport' }
            throw "FAIL stage=$Stage category=$category exit=$exitCode"
        }
        return $output
    } finally { $process.Dispose() }
}

function Read-KubectlJson {
    param([string]$Stage, [string[]]$Arguments)
    $text = Invoke-Kubectl -Stage $Stage -Arguments $Arguments
    try {
        if ([string]::IsNullOrWhiteSpace($text)) { throw 'empty' }
        $result = ConvertFrom-Json -InputObject $text -ErrorAction Stop
        if ($null -eq $result -or $result -is [array] -or $result -isnot [pscustomobject]) { throw 'not-object' }
    } catch { throw "FAIL stage=$Stage category=invalid-json-object" }
    return $result
}

function Assert-TestNamesAbsent {
    param([string]$Stage)
    $failed = $false
    foreach ($target in @(@('clusterissuer', $name), @('certificate', $name),
                          @('secret', "$name-account"), @('secret', "$name-tls"))) {
        $scope = if ($target[0] -eq 'clusterissuer') { 'cluster' } else { $ns }
        $targetStage = "$Stage/$scope/$($target[0])/$($target[1])"
        $arguments = @('get', $target[0], $target[1], '--ignore-not-found', '-o', 'name')
        if ($scope -ne 'cluster') { $arguments += @('-n', $ns) }
        try {
            $existing = Invoke-Kubectl -Stage $targetStage -Arguments $arguments
            if (-not [string]::IsNullOrWhiteSpace($existing)) {
                throw "FAIL stage=$targetStage category=name-collision; inspect existing object; no automatic adoption or deletion"
            }
            "PASS stage=$targetStage absent=true"
        } catch {
            # Only fixed diagnostics raised by this script may be shared.
            Write-Output $_.Exception.Message
            $failed = $true
        }
    }
    if ($failed) { throw "FAIL stage=$Stage category=blocked; no automatic retry or cleanup of existing objects" }
}

try {
    if (-not $ResumeStagingPair) { Assert-TestNamesAbsent -Stage 'test-name-preflight' }
    $issuer = Read-KubectlJson -Stage 'issuer-read/cluster/clusterissuer/letsencrypt' -Arguments @('get', 'clusterissuer', 'letsencrypt', '-o', 'json')
    $solvers = @($issuer.spec.acme.solvers)
    if ($issuer.kind -ne 'ClusterIssuer' -or $issuer.metadata.name -ne 'letsencrypt' -or
        $solvers.Count -ne 1 -or
        $solvers[0].dns01.cloudflare.apiTokenSecretRef.name -ne 'cloudflare-connect' -or
        $solvers[0].dns01.cloudflare.apiTokenSecretRef.key -ne 'credential' -or
        @($solvers[0].selector.dnsZones).Count -ne 1 -or
        $solvers[0].selector.dnsZones[0] -ne 'hchu.me') { throw 'FAIL stage=issuer-projection category=contract-mismatch' }
    if ($ResumeStagingPair) {
        # This is recovery of the exact administrator-reviewed pair, not general adoption.
        $existingIssuer = Read-KubectlJson -Stage 'resume/issuer-read' -Arguments @('get', 'clusterissuer', $name, '-o', 'json')
        try {
            $createdAt = ([datetimeoffset]$existingIssuer.metadata.creationTimestamp).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        } catch { throw 'FAIL stage=resume/issuer-contract category=invalid-creation-time' }
        if ($existingIssuer.kind -ne 'ClusterIssuer' -or $existingIssuer.metadata.name -ne $name -or
            -not $existingIssuer.metadata.uid -or $existingIssuer.metadata.deletionTimestamp -or
            $createdAt -cne '2026-11-08T09:58:49Z' -or
            $existingIssuer.spec.acme.server -cne 'https://acme-staging-v02.api.letsencrypt.org/directory' -or
            $existingIssuer.spec.acme.privateKeySecretRef.name -cne "$name-account" -or
            ($existingIssuer.spec.acme.solvers | ConvertTo-Json -Depth 30 -Compress) -cne
                ($issuer.spec.acme.solvers | ConvertTo-Json -Depth 30 -Compress)) {
            throw 'FAIL stage=resume/issuer-contract category=unreviewed-pair'
        }
        $account = Invoke-Kubectl -Stage 'resume/account-metadata' -Arguments @('get', 'secret', "$name-account", '-n', $ns, '-o', 'custom-columns=UID:.metadata.uid,CREATED:.metadata.creationTimestamp,OWNERS:.metadata.ownerReferences,DELETING:.metadata.deletionTimestamp', '--no-headers')
        $parts = $account.Trim() -split '\s+'
        if ($parts.Count -ne 4 -or $parts[0] -notmatch '^[0-9a-f-]{36}$' -or
            $parts[1] -cne '2026-11-08T09:58:49Z' -or $parts[2] -cne '<none>' -or $parts[3] -cne '<none>') {
            throw 'FAIL stage=resume/account-contract category=unreviewed-pair'
        }
        foreach ($kind in @('certificate', 'secret')) {
            $absentName = if ($kind -eq 'certificate') { $name } else { "$name-tls" }
            $found = Invoke-Kubectl -Stage "resume/absent/$kind" -Arguments @('get', $kind, $absentName, '-n', $ns, '--ignore-not-found', '-o', 'name')
            if (-not [string]::IsNullOrWhiteSpace($found)) { throw "FAIL stage=resume/absent/$kind category=name-collision" }
        }
        foreach ($kind in @('certificates.cert-manager.io', 'orders.acme.cert-manager.io', 'challenges.acme.cert-manager.io', 'clusterissuers.cert-manager.io', 'issuers.cert-manager.io')) {
            $list = Read-KubectlJson -Stage "resume/references/$kind" -Arguments @('get', $kind, '-A', '-o', 'json')
            if ($null -eq $list.items -or $list.kind -notmatch 'List$') { throw 'FAIL stage=resume/references category=invalid-list' }
            foreach ($item in $list.items) {
                if ($item.spec.issuerRef.name -eq $name -or
                    ($item.spec.acme.privateKeySecretRef.name -eq "$name-account" -and
                     (($item.kind -eq 'ClusterIssuer' -and $item.metadata.uid -ne $existingIssuer.metadata.uid) -or
                      ($item.kind -eq 'Issuer' -and $item.metadata.namespace -eq $ns)))) {
                    throw 'FAIL stage=resume/references category=existing-consumer; keep staging pair'
                }
            }
        }
        'PASS reviewed-staging-pair no-existing-consumers=true'
    }
    if ($DiagnoseOnly) {
        'PASS DNS01-read-only-preflight; no resources created'
        return
    }
    $testIssuer = @{
        apiVersion = 'cert-manager.io/v1'; kind = 'ClusterIssuer'
        metadata = @{name = $name}
        spec = @{acme = @{
            server = 'https://acme-staging-v02.api.letsencrypt.org/directory'
            privateKeySecretRef = @{name = "$name-account"}; solvers = $solvers
        }}
    }
    if (-not $ResumeStagingPair) {
        $cleanupSafe = $false
        $null = Invoke-Kubectl -Stage 'staging-issuer-create' -Arguments @('create', '-f', '-') -InputJson ($testIssuer | ConvertTo-Json -Depth 20)
    }
    $issuerCreated = $true
    $cleanupSafe = $true
    $certificate = @{
        apiVersion = 'cert-manager.io/v1'; kind = 'Certificate'
        metadata = @{name = $name; namespace = $ns}
        spec = @{
            secretName = "$name-tls"; dnsNames = @('eso-retirement-check.hchu.me')
            issuerRef = @{name = $name; kind = 'ClusterIssuer'}
        }
    }
    $cleanupSafe = $false
    $null = Invoke-Kubectl -Stage 'test-certificate-create' -Arguments @('create', '-f', '-') -InputJson ($certificate | ConvertTo-Json -Depth 20)
    $certificateCreated = $true
    $cleanupSafe = $true
    $null = Invoke-Kubectl -Stage 'fresh-staging-DNS01' -Arguments @('wait', "certificate/$name", '-n', $ns, '--for=condition=Ready', '--timeout=10m')
    $result = Read-KubectlJson -Stage 'fresh-certificate-readback' -Arguments @('get', 'certificate', $name, '-n', $ns, '-o', 'json')
    if ($result.kind -ne 'Certificate' -or $result.metadata.name -ne $name -or
        $result.metadata.namespace -ne $ns -or $result.status.revision -ne 1 -or
        @($result.status.conditions | Where-Object { $_.type -eq 'Ready' -and $_.status -eq 'True' }).Count -ne 1) {
        throw 'FAIL stage=fresh-certificate-readback category=contract-mismatch'
    }
    'PASS fresh-staging-DNS01 certificate-revision=1'
} finally {
    # Preserve issuer/account until cert-manager has removed the temporary TXT record.
    if ($certificateCreated) {
        $null = Invoke-Kubectl -Stage 'test-certificate-cleanup' -Arguments @('delete', 'certificate', $name, '-n', $ns, '--wait=true')
        for ($attempt = 0; $attempt -lt 24; $attempt++) {
            $pending = 0
            foreach ($resource in @(@('challenges.acme.cert-manager.io', 'ChallengeList'), @('orders.acme.cert-manager.io', 'OrderList'))) {
                $remaining = Read-KubectlJson -Stage "challenge-cleanup-read/$($resource[0])" -Arguments @('get', $resource[0], '-n', $ns, '-o', 'json')
                if ($remaining.kind -ne $resource[1] -or $null -eq $remaining.items) {
                    throw 'FAIL stage=challenge-cleanup-read category=invalid-list; keep test issuer/account'
                }
                $pending += @($remaining.items | Where-Object { $_.spec.issuerRef.name -eq $name }).Count
            }
            if (-not $pending) { break }
            Start-Sleep -Seconds 5
        }
        if ($pending) { throw 'FAIL stage=challenge-cleanup category=pending; keep test issuer/account and inspect privately' }
        $null = Invoke-Kubectl -Stage 'temporary-secret-cleanup' -Arguments @('delete', 'secret', "$name-tls", '-n', $ns, '--ignore-not-found', '--wait=true')
    }
    if ($issuerCreated -and $cleanupSafe) {
        $null = Invoke-Kubectl -Stage 'staging-issuer-cleanup' -Arguments @('delete', 'clusterissuer', $name, '--wait=true')
        $null = Invoke-Kubectl -Stage 'staging-account-cleanup' -Arguments @('delete', 'secret', "$name-account", '-n', $ns, '--ignore-not-found', '--wait=true')
        Assert-TestNamesAbsent -Stage 'cleanup-readback'
        'PASS staging-DNS01-cleanup'
    }
    if (-not $cleanupSafe) {
        'FAIL unknown-create-outcome; inspect only test resources before cleanup or retry'
    }
}
