"""Sobe a aplicação apontada para o Postgres de demonstração."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.update(
    DATABASE_URL="postgresql://grifo@127.0.0.1:5433/grifo",
    SECRET_KEY="apresentacao-grifo", APP_ENV="development",
    DISABLE_BACKGROUND_JOBS="1", STORAGE_DIR="/tmp/grifo-demo/storage",
    APP_NAME="Grifo", PUBLIC_URL="http://127.0.0.1:5177",
    FEATURE_FAMILY="true", FEATURE_STUDY_PLANNER="true",
)
from agenda import create_app  # noqa: E402

create_app().run(host="127.0.0.1", port=5177, use_reloader=False, threaded=True)
