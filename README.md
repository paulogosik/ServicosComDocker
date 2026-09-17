# notas-api

API de anotações em Python, empacotada numa imagem Docker própria, com os dados
guardados num volume nomeado.

Trabalho da disciplina de Computação em Nuvem. O relatório, com as saídas de terminal
de cada etapa, está em [RELATORIO.md](RELATORIO.md).

## O que o serviço faz

São três rotas, na porta 8000:

- `POST /notas` recebe `{"texto": "..."}`, salva a anotação com data e hora, e
  devolve 201 com o registro criado. Se o texto vier vazio ou faltando, devolve 400.
- `GET /notas` lista todas as anotações, em ordem de id.
- `GET /health` devolve `{"status": "ok"}`.

As anotações vão para um banco SQLite, no arquivo `notas.db`. O diretório onde ele é
criado vem da variável de ambiente `DATA_DIR`, que por padrão aponta para `/app/data`.
É nesse caminho que o volume é montado, e é por isso que os dados sobrevivem ao
container.

## Rodando com Docker

```bash
docker build -t notas-api:1.0 .
docker volume create notas-dados
docker run -d --name notas -p 8000:8000 -v notas-dados:/app/data notas-api:1.0
```

Criando e listando anotações:

```bash
curl -X POST http://localhost:8000/notas -H "Content-Type: application/json" -d '{"texto": "primeira nota"}'
curl http://localhost:8000/notas
```

Para ver a persistência funcionando, basta destruir o container e subir outro apontando
para o mesmo volume. As anotações continuam lá:

```bash
docker stop notas && docker rm notas
docker run -d --name notas2 -p 8000:8000 -v notas-dados:/app/data notas-api:1.0
curl http://localhost:8000/notas
```

## Rodando com Compose

`docker compose up -d --build` faz a mesma coisa, já com o volume nomeado e um
healthcheck batendo no `/health`. O `docker compose down` derruba o container e mantém
o volume; quem apaga os dados é o `docker compose down -v`.

## Rodando sem Docker

Útil para testar a API antes de construir a imagem:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
DATA_DIR=./data .venv/bin/python app.py
```

O servidor sobe em http://localhost:8000 e grava em `./data/notas.db`. Fora do
container dá para trocar a porta com a variável `PORT`; dentro dela a porta é fixa em
8000, definida no comando do gunicorn.

## Limpando tudo

```bash
docker stop notas2 && docker rm notas2
docker volume rm notas-dados
docker image rm notas-api:1.0
```

O `docker volume rm` apaga os dados de vez, sem confirmação. Enquanto existir algum
container apontando para o volume, mesmo parado, o Docker recusa a remoção.

## Arquivos

```
app.py             a API em Flask
requirements.txt   flask e gunicorn
Dockerfile         a imagem, a partir de python:3.12-slim
.dockerignore      o que fica de fora do contexto de build
compose.yaml       o mesmo serviço em Compose
RELATORIO.md       o relatório do trabalho
evidencias/        as saídas de terminal das etapas 3 a 7
```

## Um detalhe do Dockerfile que me pegou

A linha `VOLUME /app/data` faz o Docker criar um volume anônimo sozinho, mesmo quando o
container sobe sem `-v`. Ou seja, os dados não ficam na camada de escrita do container,
como eu imaginava que ficassem. Eles vão para um volume cujo nome é um hash que se
perde assim que o container é removido. A saída do `docker inspect` que mostra isso está
na seção 6 do relatório.
