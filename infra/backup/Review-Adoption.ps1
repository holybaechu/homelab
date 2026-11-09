#Requires -Version 7.4
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$PrivateDirectory
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
# Native stderr and plan JSON must never become PowerShell error records.
$PSNativeCommandUseErrorActionPreference = $false

function Stop-Review([string]$Message) { throw $Message }

if (-not $IsWindows) { Stop-Review 'Run this handoff on Windows, not a remote agent.' }
if (-not [IO.Path]::IsPathFullyQualified($PrivateDirectory)) {
    Stop-Review 'Use the absolute private directory created in the guide.'
}
$private = (Resolve-Path -LiteralPath $PrivateDirectory).Path
$source = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$repository = [IO.Path]::GetFullPath((Join-Path $source '..\..'))
if ($private -eq $repository -or
    $private.StartsWith($repository + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    Stop-Review 'Keep private artifacts outside the source worktree.'
}
$acl = Get-Acl -LiteralPath $private
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$rules = @($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))
if ($rules.Count -eq 0 -or @($rules | Where-Object {
    $_.IdentityReference.Value -ne $sid -or $_.AccessControlType -ne 'Allow'
}).Count -ne 0) {
    Stop-Review 'Private directory ACL differs from the user-only ACL in the guide.'
}
# Reject inherited switches, debug logging and alternative authentication sources.
$conflicts = @(Get-ChildItem Env: | Where-Object {
    $_.Name -match '^(TF_LOG.*|TF_CLI_ARGS.*|OP_CONNECT_HOST|OP_CONNECT_TOKEN|OP_SERVICE_ACCOUNT_TOKEN)$'
})
if ($conflicts.Count) { Stop-Review 'Start a clean process without logging, CLI overrides or automation authentication.' }
foreach ($name in @('OP_ACCOUNT', 'B2_APPLICATION_KEY_ID', 'B2_APPLICATION_KEY',
                    'TF_VAR_state_passphrase')) {
    $value = [Environment]::GetEnvironmentVariable($name)
    if ([string]::IsNullOrWhiteSpace($value) -or $value.StartsWith('op://')) {
        Stop-Review 'Required local injection is missing; check the reference file privately.'
    }
}
$planPath = Join-Path $private 'adoption.tfplan'
$statePath = Join-Path $private 'backup.tfstate'
$dataPath = Join-Path $private 'tofu-data'
$existing = @($planPath, $statePath, $dataPath, "$statePath.backup",
    (Join-Path $private '.backup.tfstate.lock.info')) | Where-Object { Test-Path -LiteralPath $_ }
if ($existing) {
    Stop-Review 'Existing artifacts found. Preserve them and review recovery; do not overwrite or choose fresh state.'
}
$versionText = & tofu version -json 2>$null
if ($LASTEXITCODE -ne 0) { Stop-Review 'OpenTofu version check failed.' }
try { $version = ($versionText -join "`n" | ConvertFrom-Json).terraform_version }
catch { Stop-Review 'OpenTofu version response was invalid.' }
if ($version -ne '1.13.2') { Stop-Review 'Install the pinned OpenTofu 1.13.2.' }
$env:TF_DATA_DIR = $dataPath
$env:TF_IN_AUTOMATION = '1'
$env:TF_INPUT = '0'

& tofu "-chdir=$source" init -input=false -lockfile=readonly "-backend-config=path=$statePath" 1>$null 2>$null
if ($LASTEXITCODE -ne 0) { Stop-Review 'Initialization failed; no plan approved. Do not enable debug logs.' }
& tofu "-chdir=$source" plan -input=false -detailed-exitcode "-out=$planPath" `
    -var=provision_scaleway=false -var=provision_runtime_credentials=false 1>$null 2>$null
$planExit = $LASTEXITCODE
if ($planExit -notin @(0, 2)) { Stop-Review 'Read-only plan failed; no plan approved. Check local authentication and cloud prerequisites privately.' }
# Capture producer first; never pipe failed/partial decrypted output into a gate.
$json = & tofu "-chdir=$source" show -json $planPath 2>$null
if ($LASTEXITCODE -ne 0) { $json = $null; Stop-Review 'Plan decryption failed; no plan approved.' }
try {
    $plan = ConvertFrom-Json -InputObject ($json -join "`n") -Depth 100 -AsHashtable -NoEnumerate
    if ($plan -isnot [System.Collections.IDictionary] -or
        $plan['format_version'] -isnot [string] -or
        -not [string]::Equals($plan['format_version'], '1.2', [StringComparison]::Ordinal) -or
        $plan['terraform_version'] -isnot [string] -or
        -not [string]::Equals($plan['terraform_version'], '1.13.2', [StringComparison]::Ordinal) -or
        $plan['errored'] -isnot [bool] -or $plan['errored'] -or
        $plan['resource_changes'] -isnot [array] -or $plan['resource_changes'].Count -ne 1) {
        throw 'schema'
    }
    # OpenTofu 1.13.2 format 1.2 emits errored, but has no complete field.
    $resource = $plan['resource_changes'][0]
    if ($resource -isnot [System.Collections.IDictionary] -or
        $resource['mode'] -isnot [string] -or
        -not [string]::Equals($resource['mode'], 'managed', [StringComparison]::Ordinal) -or
        $resource['address'] -isnot [string] -or
        -not [string]::Equals($resource['address'], 'b2_bucket.backup', [StringComparison]::Ordinal) -or
        $resource['change'] -isnot [System.Collections.IDictionary]) { throw 'identity' }
    $change = $resource['change']
    if ($change['actions'] -isnot [array] -or $change['actions'].Count -ne 1 -or
        $change['actions'][0] -isnot [string] -or
        -not [string]::Equals($change['actions'][0], 'no-op', [StringComparison]::Ordinal) -or
        $change['importing'] -isnot [System.Collections.IDictionary] -or
        $change['importing']['id'] -isnot [string] -or
        -not [string]::Equals($change['importing']['id'], '8a6f23878ab0beb4a3120e11', [StringComparison]::Ordinal)) {
        throw 'action/import'
    }
}
catch { Stop-Review 'Stop: invalid plan or unexpected B2 import. No apply authorized.' }
finally { $json = $null; $plan = $null; $resource = $null; $change = $null }
Write-Output 'b2_bucket.backup: no-op'
Write-Output 'PASS: unchanged B2 metadata import only. Encrypted plan retained; no import/apply performed.'
Write-Output 'STOP here. SDK write authentication and Scaleway account/naming/runtime provisioning remain separate gates.'
