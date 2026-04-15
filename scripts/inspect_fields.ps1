$files = Get-ChildItem 'd:\PC\旅行险\claims_data' -Recurse -Filter 'claim_info.json' | Select-Object -First 10
foreach ($f in $files) {
    Write-Host "=== $($f.FullName) ==="
    $d = Get-Content $f.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
    $d.PSObject.Properties | Where-Object { $_.Name -notmatch 'FileList|SamePolicyClaim' } | ForEach-Object {
        Write-Host "$($_.Name) : $($_.Value)"
    }
    Write-Host ""
}
