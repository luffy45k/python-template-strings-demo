#!/data/data/com.termux/files/usr/bin/sh
set -eu

if [ "${PREFIX:-}" != "/data/data/com.termux/files/usr" ]; then
  echo "ERROR: run this script inside the Termux app" >&2
  exit 1
fi

project_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
runtime_dir="${MRX_HOME:-$HOME/.local/share/mrx}"
venv="$runtime_dir/venv"
launcher="$PREFIX/bin/mrx"

printf '%s\n' \
  "Installing Mrx for Termux" \
  "Project: $project_root" \
  "Runtime: $runtime_dir"

pkg install -y python ca-certificates
mkdir -p "$runtime_dir" "$HOME/.config/mrx"
python -m venv "$venv"
"$venv/bin/python" -m pip install --upgrade pip
"$venv/bin/python" -m pip install --no-compile "$project_root"

cat > "$launcher" <<EOF
#!/data/data/com.termux/files/usr/bin/sh
set -eu
exec "$venv/bin/tstrings-agent" "\$@"
EOF
chmod 755 "$launcher"

cat > "$HOME/.config/mrx/example-request.json" <<'EOF'
{
  "goal": "Render text",
  "source": "Hello {name}",
  "values": {"name": "Termux"}
}
EOF

printf '%s\n' \
  "Mrx installed successfully." \
  "Run: mrx ~/.config/mrx/example-request.json" \
  "Or:  echo '{\"goal\":\"Render text\",\"source\":\"Hello\"}' | mrx -"
