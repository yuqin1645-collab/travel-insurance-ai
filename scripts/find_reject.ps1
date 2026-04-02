$files = Get-ChildItem 'd:\PC\旅行险\claims_data' -Recurse -Filter 'claim_info.json'
$statuses = @{}
foreach ($f in $files) {
    $d = Get-Content $f.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
    $status = $d.Final_Status
    if ($status) {
        if (-not $statuses.ContainsKey($status)) {
            $statuses[$status] = $f.FullName
        }
    }
}
foreach ($k in $statuses.Keys) {
    Write-Host "Status: $k"
    Write-Host "File: $($statuses[$k])"
    Write-Host ""
}
