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
if ((Test-Path $planPath) -or (Test-Path $statePath) -or (Test-Path $dataPath)) {
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
try { $plan = $json -join "`n" | ConvertFrom-Json -Depth 100 }
catch { Stop-Review 'Invalid plan JSON; no plan approved.' }
finally { $json = $null }
$changes = @($plan.resource_changes)
$managed = @($changes | Where-Object { $_.mode -eq 'managed' })
if ($managed.Count -ne 1 -or $managed[0].address -ne 'b2_bucket.backup' -or
    ($managed[0].change.actions -join ',') -ne 'no-op' -or
    $managed[0].change.importing.id -ne '8a6f23878ab0beb4a3120e11') {
    Stop-Review 'Stop: expected only the unchanged B2 bucket import. No apply authorized.'
}
foreach ($change in $changes) {
    if ($change.mode -ne 'managed') { Stop-Review 'Unexpected plan mode/resource; stop.' }
}
foreach ($change in $changes) {
    Write-Output ('{0}: {1}' -f $change.address, ($change.change.actions -join ','))
}
$plan = $null
Write-Output 'PASS: unchanged B2 metadata import only. Encrypted plan retained; no import/apply performed.'
Write-Output 'STOP here. SDK write authentication and Scaleway account/naming/runtime provisioning remain separate gates.'
