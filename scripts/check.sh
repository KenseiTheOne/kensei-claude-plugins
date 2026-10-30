#!/usr/bin/env bash
# Release check, shared by CI and local runs.
#   scripts/check.sh                 everything
#   scripts/check.sh tests           script tests only (python3, git)
#   scripts/check.sh validate yaml   manifests and frontmatter (claude, PyYAML)
set -euo pipefail

cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python3}"
steps=("$@")
[ ${#steps[@]} -gt 0 ] || steps=(tests validate yaml)

run_tests() {
  "$PYTHON" --version
  "$PYTHON" plugins/kensei-toolkit/skills/ticket/guard_test.py
  "$PYTHON" plugins/kensei-toolkit/skills/diff-tour/difftour_test.py
  "$PYTHON" plugins/kensei-toolkit/skills/commit/sessioncommit_test.py
  "$PYTHON" plugins/kensei-toolkit/skills/codex-img/codex_img_test.py
  "$PYTHON" plugins/kensei-toolkit/skills/svg-diagram/diagram_kit_test.py
  "$PYTHON" plugins/kensei-statusline/scripts/statusline_test.py
}

run_validate() {
  claude --version
  claude plugin validate --strict .
  for plugin in plugins/*/; do
    claude plugin validate --strict "$plugin"
  done
}

# `claude plugin validate` accepts some invalid YAML in frontmatter (such as ": " inside a plain
# scalar); other tools reject it, so every frontmatter and eval case must parse as strict YAML.
run_yaml() {
  "$PYTHON" - <<'EOF'
import glob, sys
try:
    import yaml
except ImportError:
    sys.exit("PyYAML is missing: python3 -m pip install pyyaml")

bad = 0
fronts = sorted(glob.glob("plugins/*/skills/*/SKILL.md") + glob.glob("plugins/*/evals/*/prompt.md")
                + glob.glob("plugins/*/evals/*/graders/*.md"))
cases = sorted(glob.glob("plugins/*/evals/*/case.yaml"))
for path in fronts + cases:
    text = open(path, encoding="utf-8").read()
    try:
        if path.endswith(".md"):
            _, text, _ = text.split("---\n", 2)
        assert isinstance(yaml.safe_load(text), dict), "not a mapping"
    except Exception as error:
        bad += 1
        print(f"{path}: {error}")
print(f"strict YAML: {len(fronts) + len(cases) - bad} of {len(fronts) + len(cases)} files parse")
sys.exit(1 if bad else 0)
EOF
}

for step in "${steps[@]}"; do
  case "$step" in
    tests | validate | yaml) echo "== $step"; "run_$step" ;;
    *) echo "unknown step: $step (tests, validate, yaml)" >&2; exit 2 ;;
  esac
done
echo "== all checks passed: ${steps[*]}"
