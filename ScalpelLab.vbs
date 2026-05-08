' ScalpelLab launcher — double-click to start the dashboard.
'
' Runs run_app.py via pythonw.exe (no console window). On failure, errors
' are written to scalpellab_launch.log next to this file.

Option Explicit

Dim objShell, objFSO, scriptDir, pythonw, runScript, logFile, cmdLine

Set objShell = CreateObject("WScript.Shell")
Set objFSO   = CreateObject("Scripting.FileSystemObject")

scriptDir = objFSO.GetParentFolderName(WScript.ScriptFullName)
pythonw   = "C:\Users\User\anaconda3\pythonw.exe"
runScript = scriptDir & "\run_app.py"
logFile   = scriptDir & "\scalpellab_launch.log"

If Not objFSO.FileExists(pythonw) Then
    MsgBox "pythonw.exe not found:" & vbCrLf & pythonw & vbCrLf & vbCrLf & _
           "Edit ScalpelLab.vbs and set the correct path.", _
           vbExclamation, "ScalpelLab"
    WScript.Quit 1
End If

If Not objFSO.FileExists(runScript) Then
    MsgBox "run_app.py not found next to this launcher:" & vbCrLf & runScript, _
           vbExclamation, "ScalpelLab"
    WScript.Quit 1
End If

objShell.CurrentDirectory = scriptDir

' cmd /c lets us redirect stderr to a log file so silent crashes are diagnosable.
' Window style 0 keeps cmd hidden.
cmdLine = "cmd /c """"" & pythonw & """ """ & runScript & _
          """ > """ & logFile & """ 2>&1"""

objShell.Run cmdLine, 0, False
