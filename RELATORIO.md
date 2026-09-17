# Relatório — Implementação de Serviços com Docker

**Aluno(a):** _preencher_
**Disciplina:** Computação em Nuvem
**Data de execução:** 17/09/2026

**Ambiente utilizado**

| Item             | Valor                                     |
| ---------------- | ----------------------------------------- |
| Host             | macOS (Darwin 27.0.0), Apple Silicon (arm64) |
| Docker Engine    | 29.6.1 (Docker Desktop, API 1.55)         |
| Imagem base      | `python:3.12-slim` (Python 3.12.14)       |
| Servidor HTTP    | gunicorn 23.0.0, 2 workers                |
| Framework        | Flask 3.1.0                               |
| Armazenamento    | SQLite (`$DATA_DIR/notas.db`)             |

Todas as saídas de terminal reproduzidas neste relatório estão salvas na íntegra em
[`evidencias/`](evidencias/).

---

## 1. A aplicação

`app.py` expõe três rotas na porta 8000:

- `POST /notas` — recebe `{"texto": "..."}`, grava a anotação com data/hora UTC em
  ISO 8601 e devolve `201` com o registro criado;
- `GET /notas` — devolve todas as anotações, ordenadas por `id`;
- `GET /health` — devolve `{"status": "ok"}`.

O diretório de dados é lido da variável de ambiente `DATA_DIR`, com padrão
`/app/data`, conforme exigido pelo enunciado:

```python
DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
DB_PATH = DATA_DIR / "notas.db"
```

Antes de qualquer passo com Docker, a aplicação foi testada fora do container
(Etapa 1), apontando `DATA_DIR` para um diretório local:

```
$ DATA_DIR=./data .venv/bin/python app.py &

$ curl -s http://localhost:8000/health
{"status":"ok"}

$ curl -s -X POST http://localhost:8000/notas -H "Content-Type: application/json" -d '{"texto": "teste local fora do docker"}'
{"criado_em":"2026-09-17T19:59:48.166768+00:00","id":1,"texto":"teste local fora do docker"}

$ curl -s http://localhost:8000/notas
[{"criado_em":"2026-09-17T19:59:48.166768+00:00","id":1,"texto":"teste local fora do docker"}]

$ curl -s -o /dev/null -w "status=%{http_code}\n" -X POST http://localhost:8000/notas -H "Content-Type: application/json" -d '{}'
status=400

$ ls -la data/
-rw-r--r--@  1 paulogosik  staff  12288 Sep 17 16:59 notas.db
```

O mesmo código, sem nenhuma alteração, roda dentro do container apenas por causa do
valor padrão de `DATA_DIR` — que é exatamente o ponto onde o volume será montado.

---

## 2. Explicação linha a linha do Dockerfile

```dockerfile
 1  FROM python:3.12-slim
 2
 3  ENV PYTHONDONTWRITEBYTECODE=1 \
 4      PYTHONUNBUFFERED=1
 5
 6  WORKDIR /app
 7
 8  COPY requirements.txt ./
 9
10  RUN pip install --no-cache-dir -r requirements.txt
11
12  COPY app.py ./
13
14  RUN useradd --create-home --uid 1000 appuser \
15      && mkdir -p /app/data \
16      && chown -R appuser:appuser /app
17
18  USER appuser
19
20  ENV DATA_DIR=/app/data
21
22  EXPOSE 8000
23
24  VOLUME /app/data
25
26  CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--access-logfile", "-", "app:app"]
```

**Linha 1 — `FROM python:3.12-slim`**
Define a imagem base sobre a qual as camadas seguintes são empilhadas. A variante
`slim` traz o Python completo sobre um Debian mínimo, sem compiladores, headers e
ferramentas de build que a aplicação não usa. A tag fixa a versão *minor* (3.12), o
que evita que um rebuild futuro caia sem aviso em um Python 3.13. A alternativa
`alpine` produziria uma imagem menor, mas usa musl em vez de glibc e costuma exigir
compilação de dependências que têm *wheel* pronto para Debian — para este serviço, o
ganho de tamanho não compensaria o tempo de build.

**Linhas 3–4 — `ENV PYTHONDONTWRITEBYTECODE=1` e `PYTHONUNBUFFERED=1`**
A primeira impede o Python de gravar arquivos `.pyc` dentro do container: eles seriam
lixo na camada de escrita, já que a imagem é imutável e descartável. A segunda
desliga o buffer de `stdout`/`stderr`, garantindo que os logs apareçam imediatamente
em `docker logs` em vez de ficarem presos no buffer até o processo encerrar. São
declaradas antes de tudo porque quase nunca mudam — ou seja, ficam no topo do cache
de camadas.

**Linha 6 — `WORKDIR /app`**
Cria (se necessário) e define `/app` como diretório de trabalho para todas as
instruções seguintes e para o processo em execução. Substitui um `RUN mkdir && cd`,
que não funcionaria: cada `RUN` roda em seu próprio shell, e o `cd` se perderia na
instrução seguinte.

**Linha 8 — `COPY requirements.txt ./`**
Copia **apenas** o arquivo de dependências, e não o projeto inteiro. Este é o ponto
central do aproveitamento de cache descrito nas boas práticas: uma camada só é
reconstruída quando os arquivos que ela copia mudam, e todas as camadas seguintes são
invalidadas junto.

**Linha 10 — `RUN pip install --no-cache-dir -r requirements.txt`**
Instala as dependências. Como essa camada depende apenas de `requirements.txt`, ela é
reaproveitada do cache em todo rebuild em que só o código da aplicação mudou — o
build cai de dezenas de segundos para frações de segundo. `--no-cache-dir` descarta o
cache de download do pip, que ficaria gravado dentro da imagem sem nenhuma utilidade
em tempo de execução.

**Linha 12 — `COPY app.py ./`**
Só agora entra o código da aplicação, que é a parte que mais muda. Estando depois da
instalação de dependências, uma alteração em `app.py` invalida apenas esta camada e
as posteriores — nunca a instalação do pip.

**Linhas 14–16 — `RUN useradd ... && mkdir -p /app/data && chown -R appuser:appuser /app`**
Cria um usuário sem privilégios, o diretório de dados e ajusta o dono de `/app`.
Encadear os três comandos com `&&` em um único `RUN` produz **uma** camada em vez de
três. O `mkdir` precisa acontecer aqui, antes da linha 24: se o diretório fosse criado
depois de `VOLUME`, a escrita seria descartada. Criar `/app/data` já com o dono certo
também resolve a permissão do volume — quando um volume nomeado vazio é montado,
o Docker copia para dentro dele o conteúdo *e as permissões* do diretório
correspondente na imagem.

**Linha 18 — `USER appuser`**
Faz o processo rodar sem privilégios de root. Se a aplicação for comprometida, o
atacante não ganha root dentro do container. Vem depois das instalações justamente
porque o `pip install` precisava escrever em diretórios do sistema.

**Linha 20 — `ENV DATA_DIR=/app/data`**
Declara, dentro da imagem, o contrato que a aplicação lê em tempo de execução. Como
`app.py` já usa `/app/data` como padrão, esta linha é redundante na prática, mas
documenta a configuração de forma explícita e permite sobrescrevê-la sem tocar no
código (`docker run -e DATA_DIR=/outro/caminho`).

**Linha 22 — `EXPOSE 8000`**
Documenta que o serviço escuta na porta 8000. É metadado: **não** publica a porta no
host. A publicação continua sendo responsabilidade do `-p 8000:8000` no `docker run`.
Serve para quem lê a imagem (`docker image inspect`) e para ferramentas que usam essa
informação, como o `-P` do `docker run`.

**Linha 24 — `VOLUME /app/data`**
Declara `/app/data` como ponto de montagem de volume. Duas consequências práticas:
escritas nesse caminho não vão para a camada gravável do container, e se o container
subir **sem** `-v`, o Docker cria um volume *anônimo* automaticamente — comportamento
que ficou visível na Etapa 6. Fica depois do `mkdir`/`chown` porque alterações feitas
no caminho após a declaração de `VOLUME` são perdidas.

**Linha 26 — `CMD ["gunicorn", ...]`**
Define o comando padrão do container, na forma *exec* (lista JSON), em que o processo
vira PID 1 diretamente, sem um shell intermediário — isso faz o `docker stop` entregar
o `SIGTERM` ao gunicorn e o desligamento ser limpo. Optou-se pelo gunicorn em vez do
servidor embutido do Flask, que é de desenvolvimento e impróprio para servir a
aplicação. `--bind 0.0.0.0:8000` é obrigatório: se o processo escutasse em
`127.0.0.1`, ele responderia apenas dentro do *network namespace* do container e o
mapeamento de portas não alcançaria nada. `--access-logfile -` manda o log de acesso
para `stdout`, que é onde o Docker o coleta.

**`.dockerignore`**
Exclui do contexto de build `__pycache__/`, `.git/`, ambientes virtuais (`.venv/`,
`venv/`, `env/`), o diretório `data/` local e os arquivos de documentação. Sem ele,
`.git/` e `.venv/` seriam enviados ao daemon a cada build — mais lento e, no caso de
`data/`, com risco de embutir na imagem um banco de testes da máquina local.

O efeito é mensurável na saída do build: o diretório do projeto ocupa 21 MB (20 MB
só de `.venv/`), mas o contexto efetivamente enviado ao daemon foi de 1,85 kB.

```
$ grep "transferring context" evidencias/etapa3-build.txt
#3 transferring context: 246B done
#4 transferring context: 1.85kB done

$ du -sh .venv .git
 20M    .venv
120K    .git
```

---

## 3. Etapa 3 — Build

```
$ docker build -t notas-api:1.0 .

#8 [4/6] RUN pip install --no-cache-dir -r requirements.txt
#8 2.966 Successfully installed Jinja2-3.1.6 MarkupSafe-3.0.3 Werkzeug-3.1.8 blinker-1.9.0
          click-8.5.0 flask-3.1.0 gunicorn-23.0.0 itsdangerous-2.2.0 packaging-26.3
#8 DONE 3.2s

#9 [5/6] COPY app.py ./
#9 DONE 0.0s

#10 [6/6] RUN useradd --create-home --uid 1000 appuser && mkdir -p /app/data && chown -R appuser:appuser /app
#10 DONE 0.2s

#11 exporting to image
#11 naming to docker.io/library/notas-api:1.0 done
#11 DONE 0.3s
```

### Tamanho final da imagem

```
$ docker image ls notas-api
IMAGE           ID             DISK USAGE   CONTENT SIZE   EXTRA
notas-api:1.0   049792fe8485        224MB           49MB

$ docker image inspect notas-api:1.0 --format '{{.Size}}'
48977825
```

O Docker 29 trocou a antiga coluna `SIZE` por duas colunas. `CONTENT SIZE` (49 MB) é a
soma dos *blobs* comprimidos — é o que trafega em um `docker push`/`pull` e o que
`docker image inspect` devolve em `.Size`. `DISK USAGE` (224 MB) é o espaço realmente
ocupado na máquina, que inclui tanto esses blobs quanto as camadas já descompactadas.
A conta fecha: as camadas descompactadas listadas abaixo somam ≈ 175 MB, e
175 + 49 = 224 MB.

### Lista de camadas

```
$ docker history notas-api:1.0
IMAGE          CREATED         CREATED BY                                      SIZE      COMMENT
049792fe8485   6 seconds ago   CMD ["gunicorn" "--bind" "0.0.0.0:8000" "--w…   0B        buildkit.dockerfile.v0
<missing>      6 seconds ago   VOLUME [/app/data]                              0B        buildkit.dockerfile.v0
<missing>      6 seconds ago   EXPOSE [8000/tcp]                               0B        buildkit.dockerfile.v0
<missing>      6 seconds ago   ENV DATA_DIR=/app/data                          0B        buildkit.dockerfile.v0
<missing>      6 seconds ago   USER appuser                                    0B        buildkit.dockerfile.v0
<missing>      6 seconds ago   RUN /bin/sh -c useradd --create-home --uid 1…   86kB      buildkit.dockerfile.v0
<missing>      6 seconds ago   COPY app.py ./ # buildkit                       12.3kB    buildkit.dockerfile.v0
<missing>      6 seconds ago   RUN /bin/sh -c pip install --no-cache-dir -r…   7.85MB    buildkit.dockerfile.v0
<missing>      9 seconds ago   COPY requirements.txt ./ # buildkit             12.3kB    buildkit.dockerfile.v0
<missing>      9 seconds ago   WORKDIR /app                                    8.19kB    buildkit.dockerfile.v0
<missing>      9 seconds ago   ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFER…   0B        buildkit.dockerfile.v0
<missing>      2 weeks ago     CMD ["python3"]                                 0B        buildkit.dockerfile.v0
<missing>      2 weeks ago     RUN /bin/sh -c set -eux;  for src in idle3 p…   16.4kB    buildkit.dockerfile.v0
<missing>      2 weeks ago     RUN /bin/sh -c set -eux;   savedAptMark="$(a…   44.6MB    buildkit.dockerfile.v0
<missing>      2 weeks ago     ENV PYTHON_SHA256=5c8462af5790baf43a321a1559…   0B        buildkit.dockerfile.v0
<missing>      2 weeks ago     ENV PYTHON_VERSION=3.12.14                      0B        buildkit.dockerfile.v0
<missing>      2 weeks ago     ENV GPG_KEY=7169605F62C751356D054A26A821E680…   0B        buildkit.dockerfile.v0
<missing>      2 weeks ago     RUN /bin/sh -c set -eux;  apt-get update;  a…   13.1MB    buildkit.dockerfile.v0
<missing>      2 weeks ago     ENV LANG=C.UTF-8                                0B        buildkit.dockerfile.v0
<missing>      2 weeks ago     ENV PATH=/usr/local/bin:/usr/local/sbin:/usr…   0B        buildkit.dockerfile.v0
<missing>      3 weeks ago     # debian.sh --arch 'arm64' out/ 'trixie' '@1…   109MB     debuerreotype 0.17
```

Observações sobre a lista:

- As 9 camadas de baixo (`3 weeks ago` / `2 weeks ago`) vêm da imagem base e somam
  ≈ 167 MB: 109 MB do Debian mínimo, 13,1 MB de dependências de sistema e 44,6 MB do
  interpretador Python.
- As camadas construídas por este Dockerfile somam apenas ≈ 8 MB, quase todos no
  `pip install` (7,85 MB). O código da aplicação ocupa 12,3 kB.
- `ENV`, `EXPOSE`, `VOLUME`, `USER` e `CMD` aparecem com **0 B**: são metadados
  gravados no manifesto da imagem, não camadas de sistema de arquivos.
- `<missing>` nas camadas intermediárias não é erro — indica camadas que não têm uma
  tag própria, o que é o normal em builds com BuildKit.

---

## 4. Etapa 4 — Execução com volume nomeado

```
$ docker volume create notas-dados
notas-dados

$ docker run -d --name notas -p 8000:8000 -v notas-dados:/app/data notas-api:1.0
e8a4644a9812f819b51c2c506b76b8d8d0886e043b3a19d5dfb5c06c1e2d39f1

$ docker ps
CONTAINER ID   IMAGE           COMMAND                  CREATED         STATUS         PORTS                       NAMES
e8a4644a9812   notas-api:1.0   "gunicorn --bind 0.0…"   5 seconds ago   Up 4 seconds   0.0.0.0:8000->8000/tcp      notas

$ docker logs notas
[2026-09-17 20:00:34 +0000] [1] [INFO] Starting gunicorn 23.0.0
[2026-09-17 20:00:34 +0000] [1] [INFO] Listening at: http://0.0.0.0:8000 (1)
[2026-09-17 20:00:34 +0000] [1] [INFO] Using worker: sync
[2026-09-17 20:00:34 +0000] [7] [INFO] Booting worker with pid: 7
[2026-09-17 20:00:34 +0000] [8] [INFO] Booting worker with pid: 8
```

Inserção das três anotações:

```
$ curl -s http://localhost:8000/health
{"status":"ok"}

$ curl -X POST http://localhost:8000/notas -H 'Content-Type: application/json' -d '{"texto": "primeira nota"}'
{"criado_em":"2026-09-17T20:00:44.068032+00:00","id":1,"texto":"primeira nota"}

$ curl -X POST http://localhost:8000/notas -H 'Content-Type: application/json' -d '{"texto": "segunda nota: volumes sobrevivem ao container"}'
{"criado_em":"2026-09-17T20:00:44.082459+00:00","id":2,"texto":"segunda nota: volumes sobrevivem ao container"}

$ curl -X POST http://localhost:8000/notas -H 'Content-Type: application/json' -d '{"texto": "terceira nota: escrita em /app/data/notas.db"}'
{"criado_em":"2026-09-17T20:00:44.095010+00:00","id":3,"texto":"terceira nota: escrita em /app/data/notas.db"}

$ curl http://localhost:8000/notas
[
    {"criado_em": "2026-09-17T20:00:44.068032+00:00", "id": 1, "texto": "primeira nota"},
    {"criado_em": "2026-09-17T20:00:44.082459+00:00", "id": 2, "texto": "segunda nota: volumes sobrevivem ao container"},
    {"criado_em": "2026-09-17T20:00:44.095010+00:00", "id": 3, "texto": "terceira nota: escrita em /app/data/notas.db"}
]
```

---

## 5. Etapa 5 — Prova de persistência

**1. Destruir completamente o container**

```
$ docker stop notas && docker rm notas
notas
notas

$ docker ps -a --filter name=notas
CONTAINER ID   IMAGE     COMMAND   CREATED   STATUS    PORTS     NAMES
```

A listagem vazia confirma que o container foi removido, e não apenas parado.

**2. Confirmar que o volume continua existindo**

```
$ docker volume ls --filter name=notas-dados
DRIVER    VOLUME NAME
local     notas-dados

$ docker volume inspect notas-dados
[
    {
        "CreatedAt": "2026-09-17T20:00:33Z",
        "Driver": "local",
        "Labels": null,
        "Mountpoint": "/var/lib/docker/volumes/notas-dados/_data",
        "Name": "notas-dados",
        "Options": null,
        "Scope": "local"
    }
]
```

**3. Subir um NOVO container com o mesmo volume**

```
$ docker run -d --name notas2 -p 8000:8000 -v notas-dados:/app/data notas-api:1.0
ade5a6309e8b90d9f562069e488306ec7b689b5348a612adbd45bc5a1c2ae98c

$ docker ps --filter name=notas2
CONTAINER ID   IMAGE           COMMAND                  CREATED         STATUS         PORTS                    NAMES
ade5a6309e8b   notas-api:1.0   "gunicorn --bind 0.0…"   4 seconds ago   Up 4 seconds   0.0.0.0:8000->8000/tcp   notas2
```

**4. Verificar que as anotações anteriores continuam lá**

```
$ curl http://localhost:8000/notas
[
    {"criado_em": "2026-09-17T20:00:44.068032+00:00", "id": 1, "texto": "primeira nota"},
    {"criado_em": "2026-09-17T20:00:44.082459+00:00", "id": 2, "texto": "segunda nota: volumes sobrevivem ao container"},
    {"criado_em": "2026-09-17T20:00:44.095010+00:00", "id": 3, "texto": "terceira nota: escrita em /app/data/notas.db"}
]

Total de notas: 3
```

As três anotações voltaram intactas, com os mesmos `id` e os mesmos *timestamps* de
criação (`20:00:44`), embora tenham sido gravadas por um container que já não existe.
O container `notas2` nunca executou nenhum `INSERT`: ele apenas abriu o mesmo arquivo
`notas.db`, que nunca esteve dentro de container nenhum — está no volume.

---

## 6. Etapa 6 — Contraexemplo (efemeridade)

A mesma sequência das Etapas 4 e 5, agora **sem** a opção `-v`.

```
$ docker stop notas2                      # libera a porta 8000
notas2

$ docker run -d --name efemero -p 8000:8000 notas-api:1.0
90774925b5fed713e925246f19f3534667fd8ae9954b1c21ccd457428770ecfb

$ curl -X POST .../notas -d '{"texto": "nota efemera 1"}'
{"criado_em":"2026-09-17T20:01:27.736697+00:00","id":1,"texto":"nota efemera 1"}
$ curl -X POST .../notas -d '{"texto": "nota efemera 2"}'
{"criado_em":"2026-09-17T20:01:27.754441+00:00","id":2,"texto":"nota efemera 2"}
$ curl -X POST .../notas -d '{"texto": "nota efemera 3"}'
{"criado_em":"2026-09-17T20:01:27.768493+00:00","id":3,"texto":"nota efemera 3"}

$ curl http://localhost:8000/notas
[
    {"criado_em": "2026-09-17T20:01:27.736697+00:00", "id": 1, "texto": "nota efemera 1"},
    {"criado_em": "2026-09-17T20:01:27.754441+00:00", "id": 2, "texto": "nota efemera 2"},
    {"criado_em": "2026-09-17T20:01:27.768493+00:00", "id": 3, "texto": "nota efemera 3"}
]
```

Destruindo o container e subindo outro, igualmente sem `-v`:

```
$ docker stop efemero && docker rm efemero
efemero
efemero

$ docker run -d --name efemero2 -p 8000:8000 notas-api:1.0
4344f0f5a59aadc4405d1ec736d740b20cb04e7726f24ae4aaf7431fe6db3702

$ curl http://localhost:8000/notas
[]

$ docker exec efemero2 ls -la /app/data
total 20
drwxr-xr-x 2 appuser appuser  4096 Sep 17 20:01 .
drwxr-xr-x 1 appuser appuser  4096 Sep 17 20:00 ..
-rw-r--r-- 1 appuser appuser 12288 Sep 17 20:01 notas.db
```

As três anotações desapareceram. O arquivo `notas.db` existe, mas é um banco novo,
criado do zero pelo `CREATE TABLE IF NOT EXISTS` na inicialização — por isso tem
12288 bytes e nenhuma linha.

### Por que os dados se perderam

A explicação curta é que o sistema de arquivos do container é efêmero: tudo o que a
aplicação grava vai para uma camada de escrita (*copy-on-write*) criada junto com o
container, empilhada sobre as camadas somente-leitura da imagem. Essa camada pertence
ao container, não à imagem, e é destruída junto com ele no `docker rm`. Um container
novo nasce com uma camada de escrita vazia — daí a lista vazia.

Neste caso específico, porém, há uma nuance que só apareceu ao inspecionar o
container, e que vale registrar porque contraria a explicação simples acima:

```
$ docker inspect efemero --format '{{json .Mounts}}'
[
    {
        "Type": "volume",
        "Name": "b19ad62c33465c9330da14172efc2479b94a493f30e842ba9d7cdb222ca9fb98",
        "Source": "/var/lib/docker/volumes/b19ad62c.../_data",
        "Destination": "/app/data",
        "Driver": "local",
        "RW": true
    }
]
```

Os dados **não** estavam na camada de escrita. Como o Dockerfile declara
`VOLUME /app/data` (linha 24), o Docker criou automaticamente um **volume anônimo** —
identificado por um hash em vez de um nome — e montou-o em `/app/data`. Ou seja: mesmo
sem `-v`, a gravação aconteceu em um volume.

O efeito prático é o mesmo, e a perda acontece por um motivo ligeiramente diferente:

```
$ docker volume ls --filter name=b19ad62c33465c9330da14172efc2479b94a493f30e842ba9d7cdb222ca9fb98
DRIVER    VOLUME NAME
local     b19ad62c33465c9330da14172efc2479b94a493f30e842ba9d7cdb222ca9fb98

$ docker inspect efemero2 --format '{{range .Mounts}}{{.Name}}{{end}}'
fdc024671f1b9f39de887eab267a0f84b8b7540d7d29720b892fb6465fe166e8
```

O volume anônimo do primeiro container sobreviveu ao `docker rm`, mas ficou **órfão**:
seu nome é um hash aleatório que ninguém guardou, nenhum container o referencia e não
há como pedir que o próximo container o monte. O `efemero2` ganhou um volume anônimo
novo, com outro hash — e vazio.

É essa a diferença que o volume nomeado resolve. Não é que um volume nomeado persista
e um anônimo não: **os dois** persistem no disco. O volume nomeado persiste de forma
*endereçável* — `notas-dados` é um nome estável que qualquer container futuro pode
citar em `-v notas-dados:/app/data`. O anônimo persiste como lixo: ocupa espaço,
sobrevive ao container e some no primeiro `docker volume prune` ou `docker rm -v`.
Do ponto de vista de quem opera o serviço, dado que não se consegue mais montar é dado
perdido.

---

## 7. Etapa 7 — Inspeção

### 7.1 Onde, no host, o Docker armazena fisicamente o volume `notas-dados`?

```
$ docker volume inspect notas-dados --format '{{.Mountpoint}}'
/var/lib/docker/volumes/notas-dados/_data
```

Esse é o caminho informado pelo Docker, e em um host **Linux** ele é literal: o
diretório existe no sistema de arquivos da máquina (acessível apenas como root).

Neste trabalho, porém, o host é macOS, e aí o caminho **não** existe:

```
$ ls -la /var/lib/docker/volumes/notas-dados/_data
ls: /var/lib/docker/volumes/notas-dados/_data: No such file or directory

$ uname -s
Darwin
```

O motivo é que o Docker Engine é Linux. No macOS, o Docker Desktop roda o daemon
dentro de uma máquina virtual Linux, e `/var/lib/docker/volumes/notas-dados/_data` é
um caminho **dentro dessa VM**. Para provar que o diretório existe lá, basta montar a
raiz da VM em um container auxiliar:

```
$ docker run --rm -v /:/host alpine ls -la /host/var/lib/docker/volumes/notas-dados/_data
total 20
drwxr-xr-x    2 1000     1000          4096 Sep 17 20:00 .
drwx-----x    3 root     root          4096 Sep 17 20:00 ..
-rw-r--r--    1 1000     1000         12288 Sep 17 20:00 notas.db
```

Lá está o `notas.db`, com 12288 bytes, pertencente ao uid 1000 (`appuser`) — o mesmo
arquivo que os containers `notas` e `notas2` enxergaram como `/app/data/notas.db`.

E a VM inteira, vista do macOS, é um único arquivo de disco esparso:

```
$ ls -lh ~/Library/Containers/com.docker.docker/Data/vms/0/data/Docker.raw
-rw-r--r--@ 1 paulogosik  staff   228G Sep 17 17:02 .../Docker.raw

$ du -h -d0 ~/Library/Containers/com.docker.docker/Data/vms/0/data/Docker.raw
 41G    .../Docker.raw
```

(228G é o tamanho máximo declarado; 41G é a ocupação real em disco.)

**Resposta:** o Docker guarda o volume em `/var/lib/docker/volumes/notas-dados/_data`,
um diretório gerenciado pelo Docker Engine. Em um host Linux esse caminho está
diretamente no sistema de arquivos da máquina; no macOS ele fica dentro da VM Linux do
Docker Desktop, cujo disco é o arquivo `Docker.raw` acima. Em nenhum dos dois casos o
caminho deve ser manipulado à mão — o acesso suportado é via API do Docker (`docker
volume`, `docker cp`, montagem em um container).

### 7.2 Qual é o conteúdo do diretório `/app/data` dentro do container?

```
$ docker exec notas2 ls -la /app/data
total 20
drwxr-xr-x 2 appuser appuser  4096 Sep 17 20:00 .
drwxr-xr-x 1 appuser appuser  4096 Sep 17 20:00 ..
-rw-r--r-- 1 appuser appuser 12288 Sep 17 20:00 notas.db

$ docker exec notas2 sh -c 'echo $DATA_DIR'
/app/data

$ docker inspect notas2 --format '{{json .Mounts}}'
[
    {
        "Type": "volume",
        "Name": "notas-dados",
        "Source": "/var/lib/docker/volumes/notas-dados/_data",
        "Destination": "/app/data",
        "Driver": "local",
        "Mode": "z",
        "RW": true
    }
]
```

**Resposta:** um único arquivo, `notas.db`, o banco SQLite com 12288 bytes (três
páginas de 4 kB: cabeçalho, tabela `notas` e o índice do `AUTOINCREMENT`). O dono é
`appuser` (uid 1000), o usuário sem privilégios criado no Dockerfile — o volume
herdou a permissão de `/app/data` na imagem no momento da primeira montagem. O
`docker inspect` confirma que esse diretório não é parte do sistema de arquivos do
container: é o volume `notas-dados` montado em `/app/data` com leitura e escrita.

### 7.3 O que acontece com os dados ao executar `docker volume rm notas-dados` com o container parado e removido?

Primeiro, o que acontece **antes** de o container ser removido — o Docker se recusa a
apagar um volume em uso:

```
$ docker volume rm notas-dados
Error response from daemon: remove notas-dados: volume is in use - [ade5a6309e8b...]
exit code: 1

$ docker stop notas2
notas2
$ docker volume rm notas-dados
Error response from daemon: remove notas-dados: volume is in use - [ade5a6309e8b...]
exit code: 1
```

Parar o container não basta: a referência sobrevive ao `stop`, porque o container
continua existindo. Só depois do `docker rm` a remoção é aceita:

```
$ docker rm notas2
notas2

$ docker volume rm notas-dados
notas-dados
exit code: 0

$ docker volume ls --filter name=notas-dados
DRIVER    VOLUME NAME

$ docker run --rm -v /:/host alpine ls -la /host/var/lib/docker/volumes/ | grep notas-dados
(diretorio do volume nao existe mais na VM)
```

E recriar um volume com o mesmo nome não recupera nada:

```
$ docker run -d --name notas3 -p 8000:8000 -v notas-dados:/app/data notas-api:1.0
f4fd73659cb304331825e75f54e002de4cf356a352e6ab5c57eaeb52e2219588

$ curl http://localhost:8000/notas
[]

$ docker volume inspect notas-dados --format 'Recriado em: {{.CreatedAt}}'
Recriado em: 2026-09-17T20:03:20Z
```

**Resposta:** os dados são apagados, definitivamente. O `docker volume rm` remove o
diretório `/var/lib/docker/volumes/notas-dados/_data` e todo o seu conteúdo — não há
lixeira, confirmação nem desfazer. O `docker rm` do container é justamente o que
remove a última proteção: enquanto qualquer container referenciar o volume, o daemon
recusa a operação. Depois disso, o nome `notas-dados` fica livre e um `docker run -v
notas-dados:/app/data` simplesmente cria um volume **novo e vazio** com o mesmo nome
(veja o `CreatedAt` acima, posterior à remoção), o que torna o erro fácil de não
perceber: o serviço sobe normalmente, só que sem dado nenhum. A persistência que o
volume oferece é em relação ao ciclo de vida dos *containers* — ela não é backup, e
não protege contra a remoção do próprio volume.

---

## 8. Extra — Docker Compose

O mesmo serviço foi descrito em `compose.yaml`, com o volume nomeado fixado em
`notas-dados` (sem o prefixo do nome do projeto, graças a `name: notas-dados`) e um
*healthcheck* que chama `/health`:

```
$ docker compose up -d
 Volume notas-dados       Created
 Container notas-compose  Started

$ docker compose ps
NAME            IMAGE           COMMAND                  SERVICE   STATUS                   PORTS
notas-compose   notas-api:1.0   "gunicorn --bind 0.0…"   notas     Up 8 seconds (healthy)   0.0.0.0:8000->8000/tcp

$ curl -X POST .../notas -d '{"texto": "nota criada via docker compose"}'
{"criado_em":"2026-09-17T20:03:40.751642+00:00","id":1,"texto":"nota criada via docker compose"}
```

O detalhe que importa para o tema da atividade: `docker compose down` remove o
container e a rede, mas **preserva** o volume. Só `docker compose down -v` apaga os
dados — a mesma distinção entre destruir o container e destruir o volume que a
Etapa 5 demonstrou na mão.

---

## 9. Dificuldades e aprendizados

A maior dificuldade não foi escrever o Dockerfile, e sim descobrir que a explicação
que eu daria para a Etapa 6 estava errada. A narrativa óbvia é "sem `-v`, os dados
ficam na camada de escrita do container e morrem com ele". Ao rodar
`docker inspect efemero --format '{{json .Mounts}}'`, apareceu um volume anônimo
montado em `/app/data` — os dados nunca estiveram na camada de escrita. A causa é a
linha `VOLUME /app/data` do meu próprio Dockerfile: ela faz o Docker criar um volume
automaticamente mesmo quando ninguém pede. Isso mudou a conclusão do exercício. O
volume nomeado não é o que faz o dado sobreviver — ele é o que torna o dado
*reencontrável*. Sem nome, o volume continua existindo, órfão, com um hash que ninguém
anotou; na prática é espaço em disco ocupado por um dado que nenhum container vai
montar de novo. Foi o achado mais útil do trabalho, e só apareceu porque decidi
inspecionar em vez de confiar no resultado esperado — o `[]` do `curl` "confirmava" a
hipótese errada perfeitamente.

A segunda dificuldade foi de ambiente, e igualmente instrutiva. A Etapa 7 pergunta
onde o volume está fisicamente no host; o `docker volume inspect` respondeu
`/var/lib/docker/volumes/notas-dados/_data`, mas esse caminho não existe no macOS,
porque o Docker Engine é Linux e o Docker Desktop o executa dentro de uma VM. Levou um
`docker run -v /:/host alpine ls ...` para provar que o diretório existe — só que do
lado de dentro da VM, cujo disco inteiro é um único arquivo esparso no meu
`~/Library`. Ficou claro por que a documentação insiste que volumes são gerenciados
*pela API do Docker* e não pelo sistema de arquivos: em metade das plataformas o
caminho que o `inspect` devolve nem sequer é alcançável pelo terminal do host.

No Dockerfile, o aprendizado mais concreto foi o peso da ordem das instruções. Copiar
`requirements.txt` e instalar as dependências **antes** de copiar `app.py` faz com que
editar o código invalide uma camada de 12,3 kB em vez de refazer o `pip install` de
7,85 MB. Do mesmo modo, `mkdir -p /app/data && chown` precisa vir antes de
`VOLUME /app/data`: o que se escreve em um caminho depois de ele ser declarado como
volume é descartado no build — e foi assim que a permissão do diretório para o usuário
não-root acabou funcionando de graça, já que o Docker copia conteúdo e permissões da
imagem para dentro de um volume nomeado vazio na primeira montagem. Também foi um
ajuste de expectativa descobrir que `EXPOSE 8000` não publica porta nenhuma (é só
metadado; quem publica é o `-p`) e que `docker image ls`, no Docker 29, deixou de ter
a coluna `SIZE`: passou a mostrar `CONTENT SIZE` (49 MB, o que trafega em um push) e
`DISK USAGE` (224 MB, o que ocupa aqui) — dois números legitimamente diferentes para
"o tamanho da imagem", e a conta só fechou depois de somar as camadas do
`docker history`.

Por fim, ficou a distinção que dá sentido à atividade inteira: imagem, container e
volume têm ciclos de vida independentes. O `docker rm` derrubou o container três
vezes sem tocar nos dados; o `docker volume rm` apagou tudo de uma vez, sem
confirmação e sem volta. Persistir em volume protege contra a destruição do container,
que é rotina — em Kubernetes, um pod é recriado a qualquer momento. Não protege contra
a remoção do volume, nem substitui backup. E o modo de falha mais perigoso que vi foi
o da questão 7.3: depois de apagar o volume, subir o serviço com exatamente o mesmo
comando funciona, o `/health` responde `ok` e a API sobe saudável — só que vazia.

---

## 10. Como reproduzir

```bash
docker build -t notas-api:1.0 .
docker volume create notas-dados
docker run -d --name notas -p 8000:8000 -v notas-dados:/app/data notas-api:1.0

curl -X POST http://localhost:8000/notas -H "Content-Type: application/json" -d '{"texto": "primeira nota"}'
curl http://localhost:8000/notas

docker stop notas && docker rm notas
docker run -d --name notas2 -p 8000:8000 -v notas-dados:/app/data notas-api:1.0
curl http://localhost:8000/notas
```

Limpeza (apaga os dados):

```bash
docker stop notas2 && docker rm notas2
docker volume rm notas-dados
docker image rm notas-api:1.0
```

## Referências

- DOCKER INC. *Docker Docs: Get started*. Disponível em: https://docs.docker.com/get-started/. Acesso em: 17 set. 2026.
- DOCKER INC. *Dockerfile reference*. Disponível em: https://docs.docker.com/reference/dockerfile/. Acesso em: 17 set. 2026.
- DOCKER INC. *Volumes*. Disponível em: https://docs.docker.com/engine/storage/volumes/. Acesso em: 17 set. 2026.
- DOCKER INC. *Building best practices*. Disponível em: https://docs.docker.com/build/building/best-practices/. Acesso em: 17 set. 2026.
- DOCKER INC. *python – Official Image*. Docker Hub. Disponível em: https://hub.docker.com/_/python. Acesso em: 17 set. 2026.
- MOUAT, Adrian. *Usando Docker: desenvolvendo e implantando software com containers*. São Paulo: Novatec, 2017.
