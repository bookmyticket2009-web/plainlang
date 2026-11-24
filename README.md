# PlainLang

**PlainLang** is a standalone, plain-English programming language and desktop IDE built in Python—packaged as native executables so end users can write and run `.plain` scripts **without installing Python**.

PlainLang uses **zero punctuation syntax and no quotation marks** for everyday text, variables, conditions, loops, functions, objects, and file/system operations.

---

## Features

* **Zero Quotes & Zero Syntax:** Write readable plain-English instructions without quotes or semicolons.
* **PlainLang Studio (GUI):** Built-in dark-mode Tkinter IDE with syntax highlighting, live output console, and live variable/object inspector.
* **Variables, Lists & Objects:** Supports variables, dot-notation properties (`profile.name`), lists (`Create list`, `Append`, `Get item`, `For each`), and JSON (`Parse json`, `To json`).
* **Control Flow & Functions:** Includes `If` / `Else if` / `Else`, `While`, `Repeat ... times`, `Break`, `Continue`, `Define ... with ...`, `Do ... with ...`, `Return`, and `Try` / `Catch`.
* **Built-In Capabilities:** File read/write (`Load`, `Save`, `Check file`), web requests (`Fetch`), shell commands (`Run command`), timestamps, random numbers, and dynamic Python built-in calls (`Call <builtin> with <args> into <var>`).

---

## Installation (No Python Required)

Download the archive for your operating system from the **Releases** page:

### Windows
1. Download `plainlang-windows-x64.zip` (or the `PlainLang-Setup-*.exe` installer) from the latest release.
2. Extract `plainlang.exe` (or run the installer to install into `C:\Program Files\PlainLang`, add it to System `PATH`, and associate `.plain` files).
3. Run `plainlang.exe` to open **PlainLang Studio**, or run a script from Command Prompt / PowerShell:
   ```powershell
   plainlang.exe script.plain