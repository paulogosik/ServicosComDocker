import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request

DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
DB_PATH = DATA_DIR / "notas.db"

app = Flask(__name__)


def get_connection():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_connection() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS notas ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  texto TEXT NOT NULL,"
            "  criado_em TEXT NOT NULL"
            ")"
        )


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/notas")
def criar_nota():
    payload = request.get_json(silent=True) or {}
    texto = payload.get("texto")
    if not isinstance(texto, str) or not texto.strip():
        return jsonify({"erro": "o campo 'texto' e obrigatorio"}), 400

    texto = texto.strip()
    criado_em = datetime.now(timezone.utc).isoformat()

    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO notas (texto, criado_em) VALUES (?, ?)",
            (texto, criado_em),
        )
        nota_id = cursor.lastrowid

    return jsonify({"id": nota_id, "texto": texto, "criado_em": criado_em}), 201


@app.get("/notas")
def listar_notas():
    with get_connection() as conn:
        linhas = conn.execute(
            "SELECT id, texto, criado_em FROM notas ORDER BY id"
        ).fetchall()
    return jsonify([dict(linha) for linha in linhas])


init_db()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
