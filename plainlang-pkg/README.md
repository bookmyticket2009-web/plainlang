# PlainLang

Zero-syntax, plain-English programming language and interactive Tkinter studio (`PlainLangStudio`) with autofill and live variable inspection.

## Usage

plainlang script.plain
plainlang-studio

### 3. `plainlang-pkg/src/plainlang/__init__.py`

from .cli import PlainLangEngine, PlainLangStudio, launch_studio, main

__all__ = ["PlainLangEngine", "PlainLangStudio", "launch_studio", "main"]
__version__ = "4.0.0"