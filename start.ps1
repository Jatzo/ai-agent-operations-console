# With no arguments this opens a made-up demo world. Pass --root and --home to use your own folders.
Set-Location -LiteralPath $PSScriptRoot
if ($args.Count -eq 0) {
    python demo.py
} else {
    python serve_dashboard.py @args
}
