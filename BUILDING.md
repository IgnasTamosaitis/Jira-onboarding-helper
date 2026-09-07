# Building the Windows installer

The release installer is a per-user MSI containing a single PyInstaller-built
Windows executable. The executable bundles Python and the packages from
`requirements.txt`; target machines do not need Python or .NET.

## Local build prerequisites

- Windows 10 or 11
- Python 3.11 or later
- .NET SDK 8 or later

From the repository root, run:

```powershell
.\packaging\build_installer.ps1
```

The script reads `version.txt`, installs the Python build dependencies, creates
the application icon and version metadata, builds the executable, and compiles
the MSI with WiX Toolset. The result is:

```text
dist\installer\Jira-Reminders-x.y.z.msi
```

Use `-SkipDependencyInstall` when the Python dependencies are already present.
Use `-Version 1.3.0` to override `version.txt` for a test build.

### Code signing

The local test MSI is unsigned. Before company-wide distribution, sign both the
executable and MSI with the Girteka Authenticode code-signing certificate:

```powershell
.\packaging\build_installer.ps1 `
  -SigningCertificateThumbprint "CERTIFICATE_SHA1_THUMBPRINT"
```

The certificate must be available to the build user and `signtool.exe` must be
installed from the Windows SDK. The build script applies SHA-256 signatures and
a trusted timestamp to both artifacts. A signed installer avoids the **Unknown
publisher** warning and allows recipients to verify that the package came from
Girteka.

## Release build

Pushing a tag such as `v1.3.0` runs
`.github/workflows/build-installer.yml`. The workflow:

1. builds the MSI on a clean Windows runner;
2. publishes it as a workflow artifact; and
3. creates the GitHub release with the MSI already attached.

Do not publish a release before pushing the tag. The workflow deliberately waits
until the MSI is ready so installed copies cannot discover an incomplete release.
If you want to prepare a custom title and notes beforehand, create a **draft**
release for the tag; the workflow attaches the MSI and publishes that draft only
after the build succeeds. If no draft exists, it creates the release with
automatically generated notes.

## Installer behavior

- installs to `%LOCALAPPDATA%\Girteka\Jira Reminders`;
- creates Desktop, Start menu, and Startup shortcuts;
- launches the app after the first installation;
- upgrades older MSI versions in place;
- registers with Windows Installed Apps for standard uninstall;
- keeps user data in `%USERPROFILE%\.jira-reminders` when uninstalled.

## Python DLL error after an automatic update

If the automatic restart reports `Failed to load Python DLL` from a temporary
`_MEI...` folder, dismiss the error and open Jira Reminders from the Start menu
or Desktop shortcut. The MSI may already have installed successfully; check
the app's version after reopening it.

The update helper must launch with `PYINSTALLER_RESET_ENVIRONMENT=1`. WScript
and PowerShell otherwise pass the old application's PyInstaller environment to
the restarted executable, which can try to reuse temporary Python files that
were deleted when the old app exited. See
[PyInstaller's restart guidance](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#using-sys-executable-to-spawn-subprocesses-that-outlive-the-application-process-implementing-application-restart).

The fixed updater takes effect once the new build is installed. An update
started by an older installed build can still show the error once; reopen the
app manually if that happens. If a normal shortcut launch also fails, investigate
the installation and endpoint-security logs on the affected computer; a missing
DLL or one of its dependencies can have other causes.
