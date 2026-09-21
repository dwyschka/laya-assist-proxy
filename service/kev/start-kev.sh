#!/bin/sh
# Startet den kev-Server, den laya-assist-proxy als alternativen Entscheider
# ansprechen kann (Einstellung engine.name = kev).
#
# kev liegt als eigenes Repo daneben und bringt seine eigene Umgebung mit --
# Python 3.13, weil es fuer 3.14 keine Torch-Wheels gibt.
KEV=${KEV_HOME:-$HOME/Services/kev}
MODELL=${KEV_RUN:-jaredpalmer/kev-0.8b}
PORT=${KEV_PORT:-8009}

if [ ! -x "$KEV/.venv/bin/python" ]; then
    echo "$(date '+%F %T') kev-Umgebung fehlt in $KEV -- 'uv venv --python 3.13 && uv sync --extra serve'"
    exit 1
fi

cd "$KEV" || exit 1
export KEV_DTYPE=${KEV_DTYPE:-bf16}
exec .venv/bin/python -m kev.serve --run "$MODELL" --port "$PORT"
