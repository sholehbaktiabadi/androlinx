#!/bin/sh
# Build the .deb without debhelper.
#
# The project's standard route is `dpkg-buildpackage -us -uc -b`, which uses
# debian/rules and debhelper. This script produces a package with the same
# contents for machines that do not have debhelper, dh-python and
# pybuild-plugin-pyproject installed -- useful when installing build packages
# needs root access you do not have yet.
#
# The two packages are compared against each other in CI, so this file cannot
# drift from debian/ without something noticing.
#
# Usage: tools/build-deb.sh [output directory]

set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUTDIR=${1:-$ROOT}
PKG=androlinx
APP_ID=io.github.sholehbaktiabadi.AndroLinx
VERSION=$(sed -n '1s/.*(\(.*\)).*/\1/p' "$ROOT/debian/changelog")
ARCH=all
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

echo "Building $PKG $VERSION"

install -d "$STAGE/DEBIAN"
install -d "$STAGE/usr/bin"
install -d "$STAGE/usr/lib/python3/dist-packages/$PKG"
install -d "$STAGE/usr/share/applications"
install -d "$STAGE/usr/share/icons/hicolor/scalable/apps"
install -d "$STAGE/usr/share/metainfo"
install -d "$STAGE/usr/share/man/man1"
install -d "$STAGE/usr/share/doc/$PKG"

# A flat glob, which is why src/androlinx/ has no subdirectories. If that ever
# changes, this line has to change with it.
install -m 644 "$ROOT/src/$PKG"/*.py "$STAGE/usr/lib/python3/dist-packages/$PKG/"

cat > "$STAGE/usr/bin/$PKG" <<'LAUNCHER'
#!/usr/bin/python3
from androlinx.cli import main

raise SystemExit(main())
LAUNCHER
chmod 755 "$STAGE/usr/bin/$PKG"

install -m 644 "$ROOT/data/$APP_ID.desktop"      "$STAGE/usr/share/applications/"
install -m 644 "$ROOT/data/$APP_ID.svg"          "$STAGE/usr/share/icons/hicolor/scalable/apps/"
install -m 644 "$ROOT/data/$APP_ID.metainfo.xml" "$STAGE/usr/share/metainfo/"
gzip -9nc "$ROOT/data/$PKG.1" > "$STAGE/usr/share/man/man1/$PKG.1.gz"
chmod 644 "$STAGE/usr/share/man/man1/$PKG.1.gz"

install -m 644 "$ROOT/debian/copyright" "$STAGE/usr/share/doc/$PKG/copyright"
# changelog.gz, not changelog.Debian.gz: this is a native package (see
# debian/source/format), and lintian rightly objects to the other name.
gzip -9nc "$ROOT/debian/changelog" > "$STAGE/usr/share/doc/$PKG/changelog.gz"
chmod 644 "$STAGE/usr/share/doc/$PKG/changelog.gz"
install -m 644 "$ROOT/README.md" "$STAGE/usr/share/doc/$PKG/README.md"

# dh_compress gzips anything in usr/share/doc over 4 KiB, leaving the copyright
# file alone. Reproducing that rule rather than hard-coding "gzip the README"
# keeps the two build routes matching even if the documentation changes size.
find "$STAGE/usr/share/doc/$PKG" -type f ! -name copyright ! -name '*.gz' \
     -size +4k -exec gzip -9n {} +

SIZE=$(du -ks "$STAGE" | cut -f1)

# The Depends field mirrors the binary stanza in debian/control, with the
# ${python3:Depends} that dh-python would normally expand replaced by an
# explicit python3 dependency.
cat > "$STAGE/DEBIAN/control" <<CONTROL
Package: $PKG
Version: $VERSION
Section: utils
Priority: optional
Architecture: $ARCH
Depends: python3 (>= 3.10), python3-gi, gir1.2-gtk-4.0 (>= 4.12), gir1.2-adw-1 (>= 1.7), adb, scrcpy (>= 3.0)
Recommends: android-sdk-platform-tools-common
Installed-Size: $SIZE
Maintainer: sholehbaktiabadi <sholehbaktiabadi@gmail.com>
Homepage: https://github.com/sholehbaktiabadi/androlinx
Description: desktop front-end for adb and scrcpy
 AndroLinx puts a GTK4 interface on the two tools people already use to mirror
 an Android phone to a Linux desktop. Phones appear and disappear in the sidebar
 on their own, mirroring options live in named profiles instead of in your shell
 history, and connecting over Wi-Fi is a dialog rather than three commands typed
 in the right order.
 .
 A phone frame shows a live preview of the selected device, so you can see which
 one you are about to mirror before starting. Recording to an MP4 is one button,
 and anything already running is listed with a stop button beside it.
 .
 Mirroring itself is done by scrcpy, which opens its own window. AndroLinx finds
 the devices, assembles the command, and shows exactly what it is going to run.
CONTROL

# There is no prerm: aqualizer needed one only to disable a systemd unit, and
# AndroLinx ships no unit. Nothing to undo before the files are removed.
cat > "$STAGE/DEBIAN/postinst" <<'POSTINST'
#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
    command -v update-desktop-database >/dev/null 2>&1 && \
        update-desktop-database -q /usr/share/applications || true
    command -v gtk4-update-icon-cache >/dev/null 2>&1 && \
        gtk4-update-icon-cache -qtf /usr/share/icons/hicolor || true
fi
exit 0
POSTINST
chmod 755 "$STAGE/DEBIAN/postinst"

cat > "$STAGE/DEBIAN/postrm" <<'POSTRM'
#!/bin/sh
set -e
if [ "$1" = "remove" ] || [ "$1" = "purge" ]; then
    command -v update-desktop-database >/dev/null 2>&1 && \
        update-desktop-database -q /usr/share/applications || true
    command -v gtk4-update-icon-cache >/dev/null 2>&1 && \
        gtk4-update-icon-cache -qtf /usr/share/icons/hicolor || true
fi
exit 0
POSTRM
chmod 755 "$STAGE/DEBIAN/postrm"

find "$STAGE" -type d -exec chmod 755 {} +

mkdir -p "$OUTDIR"
DEB="$OUTDIR/${PKG}_${VERSION}_${ARCH}.deb"
dpkg-deb --root-owner-group --build "$STAGE" "$DEB" >/dev/null
echo "Wrote $DEB"
dpkg-deb --info "$DEB" | sed -n '2,8p'
