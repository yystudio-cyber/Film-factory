param([Parameter(Mandatory=$true)][string]$Folder)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech
$reader = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $line = [IO.File]::ReadAllText((Join-Path $Folder "line.txt"))
    if ($env:FILM_VOICE) { $reader.SelectVoice($env:FILM_VOICE) }
    elseif ($line -match '[\u3400-\u9fff]') {
        $voice = $reader.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -like 'zh-*' } | Select-Object -First 1
        if (!$voice) { throw "A Chinese Windows SAPI voice is required for this line." }
        $reader.SelectVoice($voice.VoiceInfo.Name)
    }
    $reader.SetOutputToWaveFile((Join-Path $Folder "voice.wav"))
    $reader.Speak($line)
} finally { $reader.Dispose() }
