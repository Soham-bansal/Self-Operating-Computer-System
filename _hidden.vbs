' Runs the .bat passed as the first argument with NO visible window.
' Used by run.bat to start the OmniParser + backend servers hidden.
Set sh = CreateObject("WScript.Shell")
sh.Run """" & WScript.Arguments(0) & """", 0, False
