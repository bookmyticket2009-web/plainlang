$env:PVENV_DIR = Split-Path -Parent $PSScriptRoot
function global:prompt {
    "(.pvenv) PS $($executionContext.SessionState.Path.CurrentLocation)> "
}
function global:deactivate_pvenv {
    Remove-Item Env:PVENV_DIR -ErrorAction SilentlyContinue
    function global:prompt {
        "PS $($executionContext.SessionState.Path.CurrentLocation)> "
    }
    Remove-Item function:global:deactivate_pvenv -Force -ErrorAction SilentlyContinue
}
