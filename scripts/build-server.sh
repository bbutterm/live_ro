#!/usr/bin/env bash
# Совместимость: сборка в текущем checkout (для разработки).
# Для лаборатории используйте `scripts/lab deploy <commit>` — сборку в отдельный release.
set -euo pipefail
exec "$(dirname "$0")/lab" build "$(cd "$(dirname "$0")/.." && pwd)"
