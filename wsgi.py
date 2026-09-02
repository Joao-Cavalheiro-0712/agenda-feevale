"""Ponto de entrada WSGI (Railway/Gunicorn) e execução local.

    gunicorn wsgi:app
    python wsgi.py     # desenvolvimento
"""
from agenda import config, create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=config.PORT, debug=not config.IS_PRODUCTION)
