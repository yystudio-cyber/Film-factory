param([Parameter(Mandatory=$true)][string]$Folder)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech
$reader = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $line = [IO.File]::ReadAllText((Join-Path $Folder "line.txt"))
    $voices = @($reader.GetInstalledVoices() | Where-Object Enabled | ForEach-Object VoiceInfo)
    $culture = $env:FILM_VOICE_CULTURE
    if ($env:FILM_VOICE) {
        # An explicitly selected voice takes priority over language heuristics.
        $reader.SelectVoice($env:FILM_VOICE)
    } else {
        if (!$culture) {
            if ($line -match '[\u3040-\u30ff]') { $culture = "ja" }
            elseif ($line -match '[\uac00-\ud7af]') { $culture = "ko" }
            elseif ($line -match '[\u3400-\u9fff]') { $culture = "zh" }
            elseif ($line -match '[a-z]') { $culture = "en" }
        }
        if ($culture) {
            $matchesCulture = @($voices | Where-Object { $_.Culture.Name -eq $culture -or $_.Culture.Name -like "$culture-*" })
            $voice = $matchesCulture | Where-Object { $_.Name -eq $reader.Voice.Name } | Select-Object -First 1
            if (!$voice) { $voice = $matchesCulture | Select-Object -First 1 }
            if (!$voice) { throw "No enabled desktop SAPI voice for '$culture'. Installed: $($voices.Name -join ', '). Configure FILM_VOICE or install a compatible desktop voice." }
            $reader.SelectVoice($voice.Name)
        }
    }
    $reader.SetOutputToWaveFile((Join-Path $Folder "voice.wav"))
    $reader.Speak($line)
    $metadata = @{name = $reader.Voice.Name; culture = $reader.Voice.Culture.Name} | ConvertTo-Json -Compress
    [IO.File]::WriteAllText((Join-Path $Folder "selected-voice.json"), $metadata)
} finally { $reader.Dispose() }
