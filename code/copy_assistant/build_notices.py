"""Collect the licenses and source references for the portable bundle."""

import json
import posixpath
import shutil
import ssl
import sys
from importlib import metadata
from pathlib import Path, PurePosixPath
from urllib.request import urlretrieve
from zipfile import ZipFile

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import qVersion


DISTRIBUTIONS = (
    "PySide6-Essentials", "shiboken6", "requests", "certifi", "charset-normalizer", "idna",
    "urllib3", "keyring", "pywin32-ctypes", "importlib_metadata", "jaraco.classes",
    "jaraco.context", "jaraco.functools", "more-itertools", "backports.tarfile", "zipp",
    "pyinstaller", "altgraph", "packaging", "pefile", "setuptools",
)
QT_DOWNLOAD = "https://download.qt.io/official_releases"


def copy_wheel_notices(destination: Path) -> list[str]:
    """Copy declared license files and record installed versions."""
    components = []
    for name in DISTRIBUTIONS:
        package = metadata.distribution(name)
        components.append(f"{name} {package.version}")
        for entry in package.files or ():
            if not any(marker in entry.name.lower() for marker in ("license", "copying", "notice")):
                continue
            if not any(part.endswith(".dist-info") for part in entry.parts):
                continue
            folder = destination / name
            folder.mkdir(exist_ok=True)
            shutil.copyfile(entry.locate(), folder / entry.name)
    return components


def qt_sources() -> dict[str, tuple[str, str]]:
    """Return the matching source archives for the bundled Qt components."""
    version = qVersion()
    minor = ".".join(version.split(".")[:2])
    base = f"{QT_DOWNLOAD}/qt/{minor}/{version}/submodules"
    modules = {
        name: (version, f"{base}/{name}-everywhere-src-{version}.zip")
        for name in ("qtbase", "qtsvg", "qtimageformats")
    }
    pyside = f"{QT_DOWNLOAD}/QtForPython/pyside6/PySide6-{pyside_version}-src"
    modules["pyside"] = (
        pyside_version, f"{pyside}/pyside-setup-everywhere-src-{pyside_version}.zip"
    )
    return modules


def qt_notices(archive: Path, destination: Path) -> None:
    """Copy Qt license texts and the license declarations from its source archive."""
    destination.mkdir(exist_ok=True)
    with ZipFile(archive) as source:
        names = set(source.namelist())
        for name in sorted(names):
            parts = PurePosixPath(name).parts
            if len(parts) == 3 and parts[1] == "LICENSES" and not name.endswith("/"):
                (destination / parts[2]).write_bytes(source.read(name))

        notices = []
        license_files: set[str] = set()
        for name in sorted(names):
            if not PurePosixPath(name).name.startswith("qt_attribution") or not name.endswith(".json"):
                continue
            data = json.loads(source.read(name), strict=False)
            for item in data if isinstance(data, list) else [data]:
                notices.append(item)
                files = item.get("LicenseFiles", item.get("LicenseFile", []))
                if isinstance(files, str):
                    files = [files]
                for license_file in files:
                    license_files.add(posixpath.normpath(str(PurePosixPath(name).parent / license_file)))

        if notices:
            (destination / "Qt-Attributions.json").write_text(
                json.dumps(notices, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        for index, name in enumerate(sorted(license_files & names), 1):
            (destination / f"{index:03d}-{PurePosixPath(name).name}").write_bytes(source.read(name))


def collect(bundle: Path, build: Path) -> None:
    """Add the notices to the folder users will download."""
    destination = bundle / "third_party"
    destination.mkdir(exist_ok=True)
    cache = build / "qt-sources"
    cache.mkdir(parents=True, exist_ok=True)
    python_version = sys.version.split()[0]
    shutil.copyfile(Path(sys.base_prefix) / "LICENSE.txt", destination / "Python-LICENSE.txt")
    components = [f"CPython {python_version}", *copy_wheel_notices(destination)]
    openssl = ".".join(str(part) for part in ssl.OPENSSL_VERSION_INFO[:3])
    openssl_license = cache / f"OpenSSL-{openssl}-LICENSE.txt"
    if not openssl_license.exists():
        urlretrieve(
            f"https://raw.githubusercontent.com/openssl/openssl/openssl-{openssl}/LICENSE.txt",
            openssl_license,
        )
    shutil.copyfile(openssl_license, destination / openssl_license.name)
    lines = [
        "CopyAssistant third-party components",
        "",
        "The CopyAssistant source is licensed under MIT. The Qt libraries, PySide and Shiboken",
        "are distributed under LGPL-3.0. Their upstream sources are unmodified.",
        "The Qt and PySide binaries are separate files under _internal. They may be replaced",
        "with ABI-compatible modified versions. You may rebuild the application from its",
        "public source and the instructions in README.md.",
        "",
        "Corresponding source archives (no charge)",
    ]
    for name, (version, url) in qt_sources().items():
        lines.append(f"{name} {version}: {url}")
        archive = cache / f"{name}-{version}.zip"
        if not archive.exists():
            temporary = archive.with_suffix(".part")
            urlretrieve(url, temporary)
            temporary.replace(archive)
        qt_notices(archive, destination / name)
    lines.extend([
        "",
        f"CPython source: https://www.python.org/downloads/release/python-{python_version.replace('.', '')}/",
        f"OpenSSL {openssl} source: https://github.com/openssl/openssl/tree/openssl-{openssl}",
        "Microsoft Visual C++ runtime: https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist",
        "If a source link is unavailable, request the matching archive through the project",
        "issue tracker at https://github.com/lauxian5520/CopyAssistant/issues.",
        "",
        "Python and bootloader components",
        *components,
        "",
    ])
    (destination / "COMPONENTS.txt").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    collect(Path(sys.argv[1]), Path(sys.argv[2]))
