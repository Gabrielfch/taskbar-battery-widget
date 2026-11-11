# Third-party notices

Taskbar Battery Widget itself is released under the MIT License (see `LICENSE`). The executables
(`TaskbarBatteryWidget-Setup-<version>.exe` and `TaskbarBatteryWidget-portable-<version>.exe`) bundle the
following third-party components.

| Component | Version | License |
| --- | --- | --- |
| Python (CPython runtime) | 3.14.5 | PSF License Version 2 |
| PySide6 / PySide6_Essentials (Qt for Python) | 6.12.0 | LGPL-3.0 |
| Shiboken6 | 6.12.0 | LGPL-3.0 |
| Qt 6 libraries (bundled with PySide6) | 6.12.0 | LGPL-3.0 |
| winrt-runtime and winrt-Windows.* projections (python-winrt) | 4.1.0 / 10.0.28000.2957 | MIT |
| PyInstaller bootloader | 6.22.3 | GPL-2.0 with bootloader exception |
| Microsoft Visual C++ runtime DLLs | as shipped with Python / Qt | Microsoft redistributable terms |

## Python

Python is distributed under the PSF License Version 2:
https://docs.python.org/3/license.html

## Qt for Python (PySide6, Shiboken6) and Qt 6 - LGPL v3

This application uses PySide6, Shiboken6 and the Qt 6 libraries under the terms of the GNU Lesser General
Public License version 3 (LGPLv3). The Qt libraries are used unmodified.

- Full license text: https://www.gnu.org/licenses/lgpl-3.0.html
- Qt for Python source code: https://code.qt.io/cgit/pyside/pyside-setup.git
- Qt source code: https://download.qt.io/official_releases/qt/

Relinking / replacing the libraries:

- **Installed version (Setup).** The Qt, PySide6 and Shiboken6 libraries are shipped as separate DLL / PYD
  files in the application folder (`_internal`), not statically linked into the executable. You may replace
  them with a compatible build of the same libraries; the application loads them at start-up.
- **Portable version.** The portable executable is a self-extracting single file: when it starts it extracts
  the very same DLLs to a temporary folder and runs from there. To replace the libraries, install the
  Setup version (or build from source with `packaging\build.ps1`) and swap the DLLs in the installed
  folder as described above.

Qt is a registered trademark of The Qt Company Ltd.

## winrt (python-winrt) - MIT

Copyright (c) Microsoft Corporation. All rights reserved.
Copyright (c) 2021-2026 David Lechner <david@pybricks.com>
Project: https://github.com/pywinrt/pywinrt

```
MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## PyInstaller bootloader - GPL v2 with bootloader exception

The executables are built with PyInstaller, whose bootloader is licensed under the GPL v2 with a special
exception that explicitly permits distributing the bootloader as part of a bundled application under any
license.

- License and exception: https://github.com/pyinstaller/pyinstaller/blob/develop/COPYING.txt
- GPL v2 text: https://www.gnu.org/licenses/old-licenses/gpl-2.0.html

## Microsoft Visual C++ runtime

The Python and Qt distributions include Microsoft Visual C++ runtime DLLs (for example `vcruntime140.dll`,
`msvcp140.dll`), which are redistributable under Microsoft's terms:
https://learn.microsoft.com/cpp/windows/redistributing-visual-cpp-files
