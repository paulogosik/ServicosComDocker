# notas-api

Serviço de anotações em Python empacotado em imagem Docker própria, com persistência
de dados em volume nomeado.

Atividade prática da disciplina de Computação em Nuvem: construção de imagem própria
para aplicação Python e persistência de dados em volume.

A análise completa, com as saídas de terminal de cada etapa, está em
[RELATORIO.md](RELATORIO.md).

## Estrutura

```
.
├── app.py             aplicação Flask (3 rotas, SQLite)
├── requirements.txt   dependências (flask, gunicorn)
├── Dockerfile         imagem própria a partir de python:3.12-slim
├── .dockerignore      arquivos fora do contexto de build
├── compose.yaml       orquestração opcional (build + volume + healthcheck)
├── README.md          este arquivo
├── RELATORIO.md       relatório da atividade
└── evidencias/        saídas de terminal das etapas 3 a 7
```

## API

| Método | Rota      | Corpo                  | Resposta                                        |
| ------ | --------- | ---------------------- | ----------------------------------------------- |
| GET    | `/health` | —                      | `{"status": "ok"}`                              |
| POST   | `/notas`  | `{"texto": "..."}`     | `201` com `{"id", "texto", "criado_em"}`        |
| GET    | `/notas`  | —                      | `200` com a lista de anotações, ordenada por id |

`POST /notas` responde `400` se `texto` estiver ausente ou vazio.

As anotações são gravadas em SQLite, no arquivo `notas.db` dentro do diretório
apontado pela variável de ambiente `DATA_DIR` (padrão `/app/data`). A data/hora é
gravada em UTC, no formato ISO 8601.

## Executando com Docker

```bash
docker build -t notas-api:1.0 .
docker volume create notas-dados
docker run -d --name notas -p 8000:8000 -v notas-dados:/app/data notas-api:1.0
```

Testando:

```bash
curl http://localhost:8000/health
curl -X POST http://localhost:8000/notas -H "Content-Type: application/json" -d '{"texto": "primeira nota"}'
curl http://localhost:8000/notas
```

As anotações sobrevivem à destruição do container porque vivem no volume
`notas-dados`, e não no sistema de arquivos efêmero do container:

```bash
docker stop notas && docker rm notas
docker run -d --name notas2 -p 8000:8000 -v notas-dados:/app/data notas-api:1.0
curl http://localhost:8000/notas
```

Para remover tudo, inclusive os dados:

```bash
docker stop notas2 && docker rm notas2
docker volume rm notas-dados
```

## Executando com Docker Compose

```bash
docker compose up -d --build
curl http://localhost:8000/health
docker compose down
```

`docker compose down` preserva o volume. Para apagar também os dados, use
`docker compose down -v`.

## Executando localmente, sem Docker

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
DATA_DIR=./data .venv/bin/python app.py
```

O servidor sobe em `http://localhost:8000` e grava em `./data/notas.db`.

## Variáveis de ambiente

| Variável   | Padrão      | Descrição                                        |
| ---------- | ----------- | ------------------------------------------------ |
| `DATA_DIR` | `/app/data` | Diretório onde o banco SQLite é criado           |
| `PORT`     | `8000`      | Porta usada apenas na execução direta via Python |

Dentro do container a porta é fixada em 8000 pelo comando do gunicorn.
