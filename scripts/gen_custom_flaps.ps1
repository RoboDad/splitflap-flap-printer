# gen_custom_flaps.ps1 — Generate UV print layouts for custom splitflap flaps
# Run from anywhere: .\scripts\gen_custom_flaps.ps1
# (script cd's to the repo root so job paths below stay short)

Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    #python -m flap_printer examples/example_basic_job.json --dry-run
    #python -m flap_printer examples/example_basic_job.json

    # Showcase job: glyphs, emoji, two procedural generators, a 6-module photo
    # — output lands in examples/example_output/
    python -m flap_printer examples/example_job.json
}
finally {
    Pop-Location
}
