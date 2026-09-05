"""Enumerate the apps currently open on this Windows PC and pull their icons.

Shared by gen_appicons.py and gen_switcher.py.  Uses PowerShell twice: once to
list processes that own a visible window, once per exe to extract the
associated icon (works even for WindowsApps-packaged executables, which is why
this beats reading the PE resources directly).
"""
import os, subprocess

SKIP = {'applicationframehost', 'systemsettings', 'explorer', 'textinputhost',
        'searchhost', 'shellexperiencehost', 'startmenuexperiencehost'}

PS_LIST = ("Get-Process | Where-Object { $_.MainWindowTitle -ne '' -and $_.Path } | "
           "Select-Object -ExpandProperty Path -Unique")
PS_EXTRACT = """Add-Type -AssemblyName System.Drawing
$ic = [System.Drawing.Icon]::ExtractAssociatedIcon('{exe}')
$ic.ToBitmap().Save('{png}', [System.Drawing.Imaging.ImageFormat]::Png)"""


def open_app_icons(outdir, verbose=True):
    """-> list of (name, png_path) for real user-facing open apps."""
    r = subprocess.run(['powershell', '-NoProfile', '-Command', PS_LIST],
                       capture_output=True, text=True)
    icons = []
    for exe in r.stdout.splitlines():
        exe = exe.strip()
        if not exe:
            continue
        name = os.path.splitext(os.path.basename(exe))[0]
        key = name.lower().replace(' ', '').replace('.', '')
        if any(key.startswith(s) for s in SKIP) or key.startswith('powertoys'):
            continue
        png = os.path.join(outdir, key + '.png')
        p = subprocess.run(['powershell', '-NoProfile', '-Command',
                            PS_EXTRACT.format(exe=exe, png=png)],
                           capture_output=True, text=True)
        if os.path.exists(png):
            icons.append((name, png))
            if verbose:
                print('  icon:', name)
        elif verbose:
            print('  no icon for %s (%s)' % (name, p.stderr.strip()[:80]))
    return icons
