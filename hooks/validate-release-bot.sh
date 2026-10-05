#!/usr/bin/env bash
# After an edit to release-bot.yml, run `release_bot validate` and hand any errors
# back to the agent (exit 2 + stderr) so it fixes them. Other files are ignored.
set -uo pipefail
input="$(cat)"
file="$(printf '%s' "$input" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))' 2>/dev/null)"
case "$(basename -- "${file:-}")" in
  release-bot.yml|*.release-bot.yml) ;;
  *) exit 0 ;;
esac
root="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
out="$(PYTHONPATH="$root" python3 -m release_bot --config "$file" validate 2>&1)"
status=$?
if printf '%s' "$out" | grep -q "ModuleNotFoundError"; then
  echo "release-bot: couldn't validate (install deps: pip install -r \"$root/release_bot/requirements.txt\")" >&2
  exit 0
fi
if [ $status -ne 0 ]; then
  printf 'release-bot.yml is invalid — fix it before continuing:\n%s\n' "$out" >&2
  exit 2
fi
exit 0
