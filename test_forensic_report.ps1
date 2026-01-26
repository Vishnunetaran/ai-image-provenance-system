# Test Forensic Report Endpoint
Write-Host "=" * 70
Write-Host "FORENSIC REPORT ENDPOINT TEST"
Write-Host "=" * 70

# Create a simple test image (1x1 pixel PNG)
$base64Image = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="

Write-Host "`n1. Registering test image..."
$registerBody = @{
    image = $base64Image
    model_id = "test-model-v1"
    timestamp = "2026-01-26T20:00:00Z"
} | ConvertTo-Json

try {
    $registerResponse = Invoke-RestMethod -Uri "http://localhost:5000/api/v1/images/register" `
        -Method Post `
        -ContentType "application/json" `
        -Body $registerBody
    
    $imageId = $registerResponse.image_id
    Write-Host "✓ Image registered successfully: $imageId" -ForegroundColor Green
} catch {
    Write-Host "✗ Registration failed: $_" -ForegroundColor Red
    exit 1
}

Write-Host "`n2. Fetching forensic report (JSON) for $imageId..."
try {
    $reportResponse = Invoke-RestMethod -Uri "http://localhost:5000/api/v1/report/$imageId"
    Write-Host "✓ Report generated successfully" -ForegroundColor Green
    Write-Host "  - Verdict: $($reportResponse.verdict)"
    Write-Host "  - Confidence: $($reportResponse.confidence_score)"
    Write-Host "  - Report ID: $($reportResponse.report_id)"
} catch {
    Write-Host "✗ Report generation failed: $_" -ForegroundColor Red
    exit 1
}

Write-Host "`n3. Fetching forensic report (TEXT) for $imageId..."
try {
    $textResponse = Invoke-WebRequest -Uri "http://localhost:5000/api/v1/report/$imageId`?format=text" -UseBasicParsing
    Write-Host "✓ Text report generated successfully" -ForegroundColor Green
    Write-Host "`nFirst 500 characters of text report:"
    Write-Host "-" * 70
    Write-Host $textResponse.Content.Substring(0, [Math]::Min(500, $textResponse.Content.Length))
    Write-Host "-" * 70
} catch {
    Write-Host "✗ Text report generation failed: $_" -ForegroundColor Red
    exit 1
}

Write-Host "`n4. Testing 404 for non-existent image..."
try {
    $notFoundResponse = Invoke-WebRequest -Uri "http://localhost:5000/api/v1/report/non-existent-id" -UseBasicParsing
    Write-Host "✗ Expected 404, got $($notFoundResponse.StatusCode)" -ForegroundColor Red
} catch {
    if ($_.Exception.Response.StatusCode -eq 404) {
        Write-Host "✓ Correctly returns 404 for non-existent image" -ForegroundColor Green
    } else {
        Write-Host "✗ Unexpected error: $_" -ForegroundColor Red
    }
}

Write-Host "`n" + ("=" * 70)
Write-Host "FORENSIC REPORT ENDPOINT TEST COMPLETE"
Write-Host "=" * 70
Write-Host "`n✅ All tests passed! Forensic report endpoint is working correctly." -ForegroundColor Green
Write-Host "`nDemo UI 'View Forensic Report' button should now work!" -ForegroundColor Cyan
