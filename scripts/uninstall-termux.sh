#!/data/data/com.termux/files/usr/bin/sh
set -eu

if [ "${PREFIX:-}" != "/data/data/com.termux/files/usr" ]; then
  echo "ERROR: run this script inside the Termux app" >&2
  exit 1
fi

rm -f "$PREFIX/bin/mrx"
rm -rf "${MRX_HOME:-$HOME/.local/share/mrx}"
echo "Mrx runtime removed. Configuration remains in $HOME/.config/mrx"
