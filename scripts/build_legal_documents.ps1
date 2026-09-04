[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new()

$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$incoming = Join-Path $root 'docs\legal\incoming'
$docxOutput = Join-Path $root 'docs\legal\current'
$pdfOutput = Join-Path $root 'static\legal\current'
$offerSource = Join-Path $incoming 'SPYON_Public_Offer_KZ_2026.docx'
$tariffSource = Join-Path $incoming 'Tariff_Policy_SPYON.docx'

$expectedSources = @{
    $offerSource = 'E7AEDC79ED3770C9AEEEB11EAA72B42D38E85093C9C4BC1DE34782422E95A695'
    $tariffSource = '4E0C0CECF86798DEB888C944BFAA7AE54E2DAE6B3987BD57885375145360DF7D'
}

foreach ($source in $expectedSources.Keys) {
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Authoritative legal source is missing: $source"
    }
    $actual = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
    if ($actual -ne $expectedSources[$source]) {
        throw "Authoritative legal source hash does not match the approved revision: $source"
    }
}

New-Item -ItemType Directory -Path $docxOutput -Force | Out-Null
New-Item -ItemType Directory -Path $pdfOutput -Force | Out-Null

$wdDoNotSaveChanges = 0
$wdFormatDocumentDefault = 16
$wdExportFormatPDF = 17
$wdHeaderFooterPrimary = 1
$wdFieldPage = 33
$wdFieldNumPages = 26
$wdAlignParagraphCenter = 1
$wdAlignParagraphRight = 2
$wdCollapseEnd = 0

function ConvertFrom-Utf8Base64 {
    param([Parameter(Mandatory)][string]$Value)
    return [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($Value))
}

function Get-ParagraphText {
    param([Parameter(Mandatory)]$Paragraph)
    return (($Paragraph.Range.Text -replace '[\r\a]+$', '').Trim())
}

function Find-ParagraphIndex {
    param(
        [Parameter(Mandatory)]$Document,
        [Parameter(Mandatory)][string]$Text
    )
    for ($index = 1; $index -le $Document.Paragraphs.Count; $index++) {
        if ((Get-ParagraphText $Document.Paragraphs.Item($index)) -eq $Text) {
            return $index
        }
    }
    throw "Paragraph not found: $Text"
}

function Set-OfficialLayout {
    param(
        [Parameter(Mandatory)]$Document,
        [int]$CenteredOpeningParagraphs = 1
    )
    foreach ($section in $Document.Sections) {
        $section.PageSetup.PageWidth = 595.3
        $section.PageSetup.PageHeight = 841.9
        $section.PageSetup.TopMargin = 56.7
        $section.PageSetup.BottomMargin = 56.7
        $section.PageSetup.LeftMargin = 62.35
        $section.PageSetup.RightMargin = 56.7

        $header = $section.Headers.Item($wdHeaderFooterPrimary).Range
        $header.Text = 'Spyon.kz'
        $header.ParagraphFormat.Alignment = $wdAlignParagraphRight
        $header.Font.Name = 'Arial'
        $header.Font.Size = 9
        $header.Font.Color = 8421504

        $footer = $section.Footers.Item($wdHeaderFooterPrimary).Range
        $revisionLabel = ConvertFrom-Utf8Base64 '0KDQtdC00LDQutGG0LjRjw=='
        $pageLabel = ConvertFrom-Utf8Base64 '0KHRgtGALg=='
        $ofLabel = ConvertFrom-Utf8Base64 '0LjQtw=='
        $footer.Text = "Spyon.kz | $revisionLabel 04.09.2026 | $pageLabel "
        $footer.ParagraphFormat.Alignment = $wdAlignParagraphCenter
        $footer.Font.Name = 'Arial'
        $footer.Font.Size = 9
        $footer.Font.Color = 8421504
        $footer.Collapse($wdCollapseEnd)
        [void]$section.Footers.Item($wdHeaderFooterPrimary).Range.Fields.Add(
            $section.Footers.Item($wdHeaderFooterPrimary).Range.Characters.Last,
            $wdFieldPage
        )
        $footer = $section.Footers.Item($wdHeaderFooterPrimary).Range
        $footer.InsertAfter(" $ofLabel ")
        $footer.Collapse($wdCollapseEnd)
        [void]$section.Footers.Item($wdHeaderFooterPrimary).Range.Fields.Add(
            $section.Footers.Item($wdHeaderFooterPrimary).Range.Characters.Last,
            $wdFieldNumPages
        )
    }

    $seen = 0
    for ($index = 1; $index -le $Document.Paragraphs.Count; $index++) {
        $paragraph = $Document.Paragraphs.Item($index)
        $text = Get-ParagraphText $paragraph
        if (-not $text) {
            continue
        }
        $seen++
        if ($seen -le $CenteredOpeningParagraphs) {
            $paragraph.Alignment = $wdAlignParagraphCenter
            $paragraph.Range.Font.Bold = -1
        }
        if ($text -match '^\d+\.\s+\p{Lu}') {
            $paragraph.Range.Font.Bold = -1
            $paragraph.Range.Font.Size = 13
            $paragraph.Format.KeepWithNext = -1
            $paragraph.Format.SpaceBefore = 12
            $paragraph.Format.SpaceAfter = 6
        }
    }

    foreach ($table in $Document.Tables) {
        $table.AutoFitBehavior(2)
        $table.Range.Font.Name = 'Arial'
        $table.Range.Font.Size = 9
        foreach ($row in $table.Rows) {
            $row.AllowBreakAcrossPages = 0
        }
    }
}

function Save-And-Export {
    param(
        [Parameter(Mandatory)]$Document,
        [Parameter(Mandatory)][string]$DocxPath,
        [Parameter(Mandatory)][string]$PdfPath
    )
    $Document.Fields.Update() | Out-Null
    $Document.SaveAs2($DocxPath, $wdFormatDocumentDefault)
    $Document.ExportAsFixedFormat($PdfPath, $wdExportFormatPDF)
}

function New-ExtractedDocument {
    param(
        [Parameter(Mandatory)]$Word,
        [Parameter(Mandatory)]$Source,
        [Parameter(Mandatory)][string]$StartText,
        [string]$EndText = '',
        [Parameter(Mandatory)][string]$DocxPath,
        [Parameter(Mandatory)][string]$PdfPath
    )
    $startIndex = Find-ParagraphIndex $Source $StartText
    $start = $Source.Paragraphs.Item($startIndex).Range.Start
    $end = $Source.Content.End
    if ($EndText) {
        $endIndex = Find-ParagraphIndex $Source $EndText
        $end = $Source.Paragraphs.Item($endIndex).Range.Start
    }
    $selection = $Source.Range($start, $end)
    $document = $Word.Documents.Add()
    try {
        $document.Range(0, 0).FormattedText = $selection.FormattedText
        Set-OfficialLayout $document 1
        Save-And-Export $document $DocxPath $PdfPath
    }
    finally {
        $document.Close($wdDoNotSaveChanges)
    }
}

$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0

try {
    $offerDocx = Join-Path $docxOutput 'public-offer.docx'
    Copy-Item -LiteralPath $offerSource -Destination $offerDocx -Force
    $offer = $word.Documents.Open($offerDocx, $false, $false)
    try {
        $appendixIndex = Find-ParagraphIndex $offer (
            ConvertFrom-Utf8Base64 '0J/QoNCY0JvQntCW0JXQndCY0JUgMS4='
        )
        $offer.Range(
            $offer.Paragraphs.Item($appendixIndex).Range.Start,
            $offer.Content.End
        ).Delete() | Out-Null
        for ($index = $offer.Paragraphs.Count; $index -ge 1; $index--) {
            $text = Get-ParagraphText $offer.Paragraphs.Item($index)
            $appendixTitle = ConvertFrom-Utf8Base64 '0J/RgNC40LvQvtC20LXQvdC40LU='
            if ($text -match ('^' + [regex]::Escape($appendixTitle) + ' [1-4]\.')) {
                $offer.Paragraphs.Item($index).Range.Delete() | Out-Null
            }
        }
        Set-OfficialLayout $offer 2
        Save-And-Export $offer $offerDocx (Join-Path $pdfOutput 'public-offer.pdf')
    }
    finally {
        $offer.Close($wdDoNotSaveChanges)
    }

    $sourceOffer = $word.Documents.Open($offerSource, $false, $true)
    try {
        New-ExtractedDocument $word $sourceOffer `
            (ConvertFrom-Utf8Base64 '0J/QoNCQ0JLQmNCb0JAg0JTQntCf0KPQodCi0JjQnNCe0JPQniDQmNCh0J/QntCb0KzQl9Ce0JLQkNCd0JjQrw==') `
            (ConvertFrom-Utf8Base64 '0J/QoNCY0JvQntCW0JXQndCY0JUgMy4=') `
            (Join-Path $docxOutput 'acceptable-use.docx') `
            (Join-Path $pdfOutput 'acceptable-use.pdf')
        New-ExtractedDocument $word $sourceOffer `
            (ConvertFrom-Utf8Base64 '0KHQntCT0JvQkNCh0JjQlSDQndCQINCe0JHQoNCQ0JHQntCi0JrQoyDQn9CV0KDQodCe0J3QkNCb0KzQndCr0KUg0JTQkNCd0J3Qq9Cl') `
            (ConvertFrom-Utf8Base64 '0J/QoNCY0JvQntCW0JXQndCY0JUgNC4=') `
            (Join-Path $docxOutput 'personal-data-consent.docx') `
            (Join-Path $pdfOutput 'personal-data-consent.pdf')
        New-ExtractedDocument $word $sourceOffer `
            (ConvertFrom-Utf8Base64 '0J/QntCb0JjQotCY0JrQkCDQmtCe0J3QpNCY0JTQldCd0KbQmNCQ0JvQrNCd0J7QodCi0Jgg0JjQndCi0JXQoNCd0JXQoi3QodCV0KDQktCY0KHQkCBTUFlPTg==') '' `
            (Join-Path $docxOutput 'privacy-policy.docx') `
            (Join-Path $pdfOutput 'privacy-policy.pdf')
    }
    finally {
        $sourceOffer.Close($wdDoNotSaveChanges)
    }

    $tariffDocx = Join-Path $docxOutput 'tariff-policy.docx'
    Copy-Item -LiteralPath $tariffSource -Destination $tariffDocx -Force
    $tariff = $word.Documents.Open($tariffDocx, $false, $false)
    try {
        $needle = ConvertFrom-Utf8Base64 '0J/RgNCw0LLQuNC7INC00L7Qv9GD0YHRgtC40LzQvtCz0L4g0LjRgdC/0L7Qu9GM0LfQvtCy0LDQvdC40Y8gKNCf0YDQuNC70L7QttC10L3QuNC1IOKEljIg0Log0J7RhNC10YDRgtC1KQ=='
        $replacement = ConvertFrom-Utf8Base64 '0J/RgNCw0LLQuNC7INC00L7Qv9GD0YHRgtC40LzQvtCz0L4g0LjRgdC/0L7Qu9GM0LfQvtCy0LDQvdC40Y8gU1BZT04='
        $match = $tariff.Content.Duplicate
        if (-not $match.Find.Execute($needle)) {
            throw 'Approved Tariff Policy reference was not found.'
        }
        $match.Text = $replacement
        Set-OfficialLayout $tariff 2
        Save-And-Export $tariff $tariffDocx (Join-Path $pdfOutput 'tariff-policy.pdf')
    }
    finally {
        $tariff.Close($wdDoNotSaveChanges)
    }
}
finally {
    $word.Quit()
    [Runtime.InteropServices.Marshal]::ReleaseComObject($word) | Out-Null
}

Write-Output 'Built five authoritative legal DOCX files and five PDFs.'
