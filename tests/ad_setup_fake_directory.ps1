# In-memory AD command doubles. Never imports or contacts Active Directory.
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1')
$script:mutations = 0
$script:deleted = @()
$script:users = @{}
foreach ($sam in @('SF','OLD','BUDDY','MANAGER','REPORT','EXISTING')) {
    $script:users[$sam] = @{
        SamAccountName=$sam; ObjectGUID=$sam; DistinguishedName="CN=$sam,OU=Source,DC=example,DC=com"
        Title='SF title'; Description='SF description'; Department='SF department'; Company='Example'
        Office='Vilnius'; StreetAddress='Street'; City='Vilnius'; PostalCode='123'; Country='LT'
        Manager='CN=MANAGER,OU=Source,DC=example,DC=com'; EmployeeID='123'; EmailAddress="$sam@example.com"
        extensionAttribute5='SF hierarchy'; extensionAttribute10='MANAGER@example.com'; extensionAttribute14='Old'; extensionAttribute15='SF'
        DirectReports=@(); MemberOf=@(); proxyAddresses=@('SMTP:old@example.com','smtp:target@example.com')
        targetAddress='old@example.com'; Enabled=$true; LockedOut=$false; PasswordLastSet='2026-01-01'; pwdLastSet=123
    }
}
$script:users.BUDDY.Title = 'Template title'
$script:users.BUDDY.Description = 'Distinct template description'
$script:users.BUDDY.Department = 'Template department'
$script:users.BUDDY.DirectReports = @('CN=EXISTING,OU=Source,DC=example,DC=com')
$script:users.EXISTING.Manager = $script:users.BUDDY.DistinguishedName
$script:users.REPORT.Manager = $script:users.SF.DistinguishedName
$script:users.SF.DirectReports = @('CN=REPORT,OU=Source,DC=example,DC=com')

function Find-User($Identity) {
    if ($Identity -is [string] -and $script:users.ContainsKey($Identity)) { return $script:users[$Identity] }
    foreach ($u in $script:users.Values) { if ($u.DistinguishedName -eq [string]$Identity) { return $u } }
    throw "Unknown fake AD user: $Identity"
}
function Get-Credential { param($Message) if ($script:cancelSignIn) { return $null }; return [pscustomobject]@{UserName='test'} }
function Import-Module { param($Name,$ErrorAction) if ($Name -ne 'ActiveDirectory') { throw "Unexpected import: $Name" } }
function Get-ADDomainController { [CmdletBinding()]param([switch]$Discover,[switch]$Writable,$Credential,$Server) [pscustomobject]@{HostName='fake-dc'} }
function Get-ADUser {
    [CmdletBinding()]param($Identity,$Properties,$Filter,$Credential,$Server)
    if ($Server -ne 'fake-dc') { throw 'Directory command must use the pinned DC' }
    if ($Filter) { $Identity='MANAGER' }
    $u = (Find-User $Identity).Clone()
    $u.DirectReports = @($script:users.Values | Where-Object { $_.Manager -eq $u.DistinguishedName } | ForEach-Object { $_.DistinguishedName })
    return [pscustomobject]$u
}
function Set-ADUser {
    [CmdletBinding()]param($Identity,$Title,$Description,$Department,$Company,$Office,$StreetAddress,$City,$PostalCode,$Country,$Manager,$EmployeeID,$EmailAddress,$Replace,$Add,$Remove,$Clear,$ChangePasswordAtLogon,$Credential,$Server)
    $u = Find-User $Identity
    $script:mutations++
    if ($script:changeSf) { $script:users.SF.Company='Changed during setup' }
    foreach ($field in @('Title','Description','Department','Company','Office','StreetAddress','City','PostalCode','Country','Manager','EmployeeID','EmailAddress')) {
        if ($PSBoundParameters.ContainsKey($field) -and $field -ne $script:dropWrite) { $u[$field]=$PSBoundParameters[$field] }
    }
    if ($Replace) { foreach ($field in $Replace.Keys) { if ($field -ne $script:dropWrite) { $u[$field]=$Replace[$field] } } }
    if ($Clear) { foreach ($field in $Clear) { $u[$field]=$null } }
    if ($Add) { foreach ($field in $Add.Keys) { $u[$field]=@($u[$field])+@($Add[$field]) } }
    if ($Remove) { foreach ($field in $Remove.Keys) { $u[$field]=@($u[$field] | Where-Object { $_ -cne $Remove[$field] }) } }
}
function Move-ADObject {
    [CmdletBinding()]param($Identity,$TargetPath,$Credential,$Server)
    $u=Find-User $Identity
    $before=$u.DistinguishedName
    $cn = if ($script:escapedName) { 'Last\, First' } else { $u.SamAccountName }
    $u.DistinguishedName="CN=$cn,$TargetPath"
    foreach ($other in $script:users.Values) { if ($other.Manager -eq $before) { $other.Manager=$u.DistinguishedName } }
    $script:mutations++
}
function Enable-ADAccount { [CmdletBinding()]param($Identity,$Credential,$Server) (Find-User $Identity).Enabled=$true; $script:mutations++ }
function Unlock-ADAccount { [CmdletBinding()]param($Identity,$Credential,$Server) (Find-User $Identity).LockedOut=$false; $script:mutations++ }
function Set-ADAccountPassword { [CmdletBinding()]param($Identity,[switch]$Reset,$NewPassword,$Credential,$Server) $script:mutations++ }
function Get-ADGroup {
    [CmdletBinding()]param($Filter,$Credential,$Server)
    if ($script:unknownGroup) { return }
    [pscustomobject]@{DistinguishedName='CN=Normal Group,DC=example,DC=com'}
}
function Add-ADGroupMember {
    [CmdletBinding()]param($Identity,$Members,$Credential,$Server)
    if ($script:denyGroup) { throw 'Insufficient access rights to perform the operation' }
    (Find-User $Members).MemberOf=@($Identity)
    $script:mutations++
}
function Remove-ADUser {
    [CmdletBinding(SupportsShouldProcess)]param($Identity,$Credential,$Server)
    if ($script:denyDelete) { throw 'SF deletion denied' }
    $u=Find-User $Identity
    $script:deleted += $u.SamAccountName
    $script:users.Remove($u.SamAccountName)
    $script:mutations++
}
function Add-Type { param($AssemblyName) }
function New-Object {
    param($TypeName,$ArgumentList)
    if ($TypeName -ne 'System.DirectoryServices.AccountManagement.PrincipalContext') { throw "Unexpected type: $TypeName" }
    $context=[pscustomobject]@{}
    $context | Add-Member -MemberType ScriptMethod -Name ValidateCredentials -Value { param($user,$password) return (-not $script:denyPassword) }
    return $context
}
# Load only the type used by the script; credential validation is stubbed above.
[void][System.Reflection.Assembly]::LoadWithPartialName('System.DirectoryServices.AccountManagement')
