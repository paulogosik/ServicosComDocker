# Relatório: implementação de serviços com Docker

**Aluno:** Paulo Gosik Mascarenhas Moita
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

- `POST /notas` recebe `{"texto": "..."}`, grava a anotação com data e hora em UTC no
  formato ISO 8601, e devolve `201` com o registro criado;
- `GET /notas` devolve todas as anotações, ordenadas por `id`;
- `GET /health` devolve `{"status": "ok"}`.

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

O mesmo código roda dentro do container sem nenhuma alteração, e isso se deve ao valor
padrão de `DATA_DIR`, que é justamente o caminho onde o volume vai ser montado.

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

**Linha 1:** `FROM python:3.12-slim`
Define a imagem base sobre a qual as camadas seguintes são empilhadas. A variante
`slim` traz o Python completo sobre um Debian mínimo, sem os compiladores, headers e
ferramentas de build que a aplicação não usa. A tag fixa a versão *minor* (3.12), o
que evita que um rebuild futuro caia sem aviso em um Python 3.13. A alternativa
`alpine` daria uma imagem menor, só que usa musl no lugar da glibc e costuma exigir
compilação de dependências que já têm *wheel* pronto para Debian. Para um serviço
deste tamanho, o que se economiza em disco não compensa o tempo de build.

**Linhas 3 e 4:** `ENV PYTHONDONTWRITEBYTECODE=1` e `PYTHONUNBUFFERED=1`
A primeira impede o Python de gravar arquivos `.pyc` dentro do container: seriam lixo
na camada de escrita, já que a imagem é imutável e descartável. A segunda desliga o
buffer de `stdout` e `stderr`, o que faz os logs aparecerem no `docker logs` na hora,
em vez de ficarem presos até o processo encerrar. As duas ficam logo no começo porque
quase nunca mudam, e assim ocupam o topo do cache de camadas.

**Linha 6:** `WORKDIR /app`
Cria (se necessário) e define `/app` como diretório de trabalho para todas as
instruções seguintes e para o processo em execução. Substitui um `RUN mkdir && cd`,
que não funcionaria: cada `RUN` roda em seu próprio shell, e o `cd` se perderia na
instrução seguinte.

**Linha 8:** `COPY requirements.txt ./`
Copia só o arquivo de dependências, e não o projeto inteiro. É aqui que mora o
aproveitamento de cache descrito nas boas práticas: uma camada só é reconstruída
quando muda algum dos arquivos que ela copia, e nesse caso todas as camadas seguintes
são invalidadas junto.

**Linha 10:** `RUN pip install --no-cache-dir -r requirements.txt`
Instala as dependências. Como essa camada depende só do `requirements.txt`, ela vem do
cache em todo rebuild em que mudou apenas o código da aplicação, e o build cai de
dezenas de segundos para frações de segundo. O `--no-cache-dir` descarta o cache de
download do pip, que ficaria gravado dentro da imagem sem serventia nenhuma em tempo
de execução.

**Linha 12:** `COPY app.py ./`
Só agora entra o código da aplicação, que é a parte que mais muda. Por estar depois da
instalação das dependências, uma alteração no `app.py` invalida esta camada e as
posteriores, mas nunca o `pip install`.

**Linhas 14 a 16:** `RUN useradd ... && mkdir -p /app/data && chown -R appuser:appuser /app`
Cria um usuário sem privilégios, o diretório de dados, e ajusta o dono de `/app`.
Encadear os três comandos com `&&` dentro de um único `RUN` gera uma camada só, em vez
de três. O `mkdir` precisa vir aqui, antes da linha 24: se o diretório fosse criado
depois do `VOLUME`, a escrita seria descartada. Criar `/app/data` já com o dono certo
também resolve a permissão do volume, porque na primeira vez que um volume nomeado
vazio é montado o Docker copia para dentro dele o conteúdo e as permissões do
diretório correspondente na imagem.

**Linha 18:** `USER appuser`
Faz o processo rodar sem privilégios de root, de modo que um comprometimento da
aplicação não entregue root dentro do container. Vem depois das instalações porque o
`pip install` precisava escrever em diretórios do sistema.

**Linha 20:** `ENV DATA_DIR=/app/data`
Declara, dentro da imagem, o contrato que a aplicação lê em tempo de execução. Como o
`app.py` já usa `/app/data` como padrão, na prática esta linha é redundante, mas deixa
a configuração explícita para quem lê a imagem e permite trocar o caminho sem mexer no
código, com `docker run -e DATA_DIR=/outro/caminho`.

**Linha 22:** `EXPOSE 8000`
Documenta que o serviço escuta na porta 8000. É só metadado, e não publica porta
nenhuma no host: quem publica continua sendo o `-p 8000:8000` do `docker run`. Serve
para quem lê a imagem com `docker image inspect` e para ferramentas que consomem essa
informação, como o `-P` do `docker run`.

**Linha 24:** `VOLUME /app/data`
Declara `/app/data` como ponto de montagem de volume, o que tem duas consequências
práticas. A primeira é que escritas nesse caminho não vão para a camada gravável do
container. A segunda é que, se o container subir sem `-v`, o Docker cria um volume
*anônimo* sozinho, comportamento que ficou visível na Etapa 6. A instrução vem depois
do `mkdir` e do `chown` porque o que se altera no caminho depois de declarado o
`VOLUME` se perde.

**Linha 26:** `CMD ["gunicorn", ...]`
Define o comando padrão do container na forma *exec* (lista JSON), em que o processo
vira PID 1 diretamente, sem shell no meio. É o que faz o `docker stop` entregar o
`SIGTERM` ao gunicorn e o desligamento sair limpo. Usei gunicorn no lugar do servidor
embutido do Flask, que é de desenvolvimento e não deve servir a aplicação. O
`--bind 0.0.0.0:8000` é obrigatório: escutando em `127.0.0.1`, o processo responderia
apenas dentro do *network namespace* do container e o mapeamento de portas não
alcançaria nada. O `--access-logfile -` joga o log de acesso no `stdout`, que é de
onde o Docker recolhe.

**`.dockerignore`**
Exclui do contexto de build o `__pycache__/`, o `.git/`, os ambientes virtuais
(`.venv/`, `venv/`, `env/`), o diretório `data/` local e os arquivos de documentação.
Sem ele, o `.git/` e o `.venv/` iriam para o daemon a cada build, deixando tudo mais
lento, e no caso do `data/` ainda haveria o risco de embutir na imagem um banco de
testes da máquina local.

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

## 3. Etapa 3: build

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

O Docker 29 trocou a antiga coluna `SIZE` por duas. A `CONTENT SIZE` (49 MB) é a soma
dos *blobs* comprimidos, ou seja, o que trafega num `docker push` ou `pull` e o que o
`docker image inspect` devolve em `.Size`. A `DISK USAGE` (224 MB) é o espaço que a
imagem realmente ocupa na máquina, somando esses blobs e as camadas já descompactadas.
A conta fecha: as camadas descompactadas listadas abaixo dão perto de 175 MB, e
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
- `ENV`, `EXPOSE`, `VOLUME`, `USER` e `CMD` aparecem com 0 B, porque são metadados
  gravados no manifesto da imagem e não camadas de sistema de arquivos.
- O `<missing>` nas camadas intermediárias não é erro. Indica camadas sem tag própria,
  que é o normal em builds com BuildKit.

---

## 4. Etapa 4: execução com volume nomeado

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

## 5. Etapa 5: prova de persistência

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
criação (`20:00:44`), mesmo tendo sido gravadas por um container que já não existe. O
`notas2` não executou `INSERT` nenhum, ele só abriu o mesmo arquivo `notas.db`, que
nunca esteve dentro de container algum. O arquivo está no volume.

---

## 6. Etapa 6: contraexemplo (efemeridade)

A mesma sequência das Etapas 4 e 5, agora sem a opção `-v`.

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

As três anotações desapareceram. O arquivo `notas.db` existe, só que é um banco novo,
criado do zero pelo `CREATE TABLE IF NOT EXISTS` da inicialização, e por isso tem
12288 bytes e nenhuma linha.

### Por que os dados se perderam

A explicação curta é que o sistema de arquivos do container é efêmero: tudo o que a
aplicação grava vai para uma camada de escrita (*copy-on-write*) criada junto com o
container e empilhada sobre as camadas somente-leitura da imagem. Essa camada pertence
ao container, e não à imagem, então vai embora junto no `docker rm`. Um container novo
nasce com a camada de escrita vazia, e daí viria a lista vazia.

Só que, neste caso, a explicação curta está errada, e eu só descobri isso porque
resolvi inspecionar o container antes de escrever o relatório:

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

Os dados nunca estiveram na camada de escrita. Como o Dockerfile declara
`VOLUME /app/data` na linha 24, o Docker criou sozinho um volume anônimo, identificado
por um hash no lugar de um nome, e montou esse volume em `/app/data`. Mesmo sem `-v`,
a gravação aconteceu dentro de um volume.

O resultado prático é o mesmo, mas a perda acontece por outro motivo:

```
$ docker volume ls --filter name=b19ad62c33465c9330da14172efc2479b94a493f30e842ba9d7cdb222ca9fb98
DRIVER    VOLUME NAME
local     b19ad62c33465c9330da14172efc2479b94a493f30e842ba9d7cdb222ca9fb98

$ docker inspect efemero2 --format '{{range .Mounts}}{{.Name}}{{end}}'
fdc024671f1b9f39de887eab267a0f84b8b7540d7d29720b892fb6465fe166e8
```

O volume anônimo do primeiro container sobreviveu ao `docker rm`, só que ficou órfão.
O nome dele é um hash aleatório que ninguém guardou, nenhum container o referencia, e
não existe como pedir que o próximo container o monte. O `efemero2` subiu com outro
volume anônimo, hash diferente e vazio.

É essa a diferença que o volume nomeado resolve, e ela não é sobre persistir. Os dois
volumes persistem no disco. O que o nome dá é endereço: `notas-dados` é estável, e
qualquer container futuro consegue citá-lo em `-v notas-dados:/app/data`. O anônimo
persiste como lixo, ocupando espaço até alguém rodar `docker volume prune` ou
`docker rm -v`. Para quem opera o serviço, dado que não se consegue mais montar é dado
perdido.

---

## 7. Etapa 7: inspeção

### 7.1 Onde, no host, o Docker armazena fisicamente o volume `notas-dados`?

```
$ docker volume inspect notas-dados --format '{{.Mountpoint}}'
/var/lib/docker/volumes/notas-dados/_data
```

Esse é o caminho que o Docker informa, e num host Linux ele é literal: o diretório
existe no sistema de arquivos da máquina, acessível só como root.

Neste trabalho o host é macOS, e aí o caminho não existe:

```
$ ls -la /var/lib/docker/volumes/notas-dados/_data
ls: /var/lib/docker/volumes/notas-dados/_data: No such file or directory

$ uname -s
Darwin
```

O motivo é que o Docker Engine é Linux. No macOS, o Docker Desktop roda o daemon
dentro de uma máquina virtual Linux, e `/var/lib/docker/volumes/notas-dados/_data` é
um caminho de dentro dessa VM. Para provar que o diretório existe lá, basta montar a
raiz da VM em um container auxiliar:

```
$ docker run --rm -v /:/host alpine ls -la /host/var/lib/docker/volumes/notas-dados/_data
total 20
drwxr-xr-x    2 1000     1000          4096 Sep 17 20:00 .
drwx-----x    3 root     root          4096 Sep 17 20:00 ..
-rw-r--r--    1 1000     1000         12288 Sep 17 20:00 notas.db
```

Lá está o `notas.db`, com 12288 bytes, pertencente ao uid 1000 (`appuser`). É o mesmo
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
um diretório gerenciado pelo Docker Engine. Em um host Linux esse caminho fica direto
no sistema de arquivos da máquina; no macOS ele fica dentro da VM Linux do Docker
Desktop, cujo disco é o `Docker.raw` acima. Em nenhum dos dois casos o caminho deveria
ser mexido à mão, porque o acesso suportado é pela API do Docker, com `docker volume`,
`docker cp` ou montando o volume em um container.

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

**Resposta:** um arquivo só, o `notas.db`, banco SQLite de 12288 bytes (três páginas
de 4 kB: cabeçalho, tabela `notas` e o índice do `AUTOINCREMENT`). O dono é o
`appuser`, uid 1000, o usuário sem privilégios criado no Dockerfile, e o volume herdou
essa permissão de `/app/data` na imagem no momento da primeira montagem. O
`docker inspect` confirma que o diretório não faz parte do sistema de arquivos do
container: é o volume `notas-dados` montado em `/app/data` com leitura e escrita.

### 7.3 O que acontece com os dados ao executar `docker volume rm notas-dados` com o container parado e removido?

Antes de chegar lá, vale o que acontece enquanto o container ainda existe, porque o
Docker se recusa a apagar um volume em uso:

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

**Resposta:** os dados são apagados de vez. O `docker volume rm` remove o diretório
`/var/lib/docker/volumes/notas-dados/_data` e todo o conteúdo dele, sem lixeira, sem
confirmação e sem desfazer. O `docker rm` do container é o que tira a última proteção,
já que o daemon recusa a operação enquanto algum container referenciar o volume.
Depois disso o nome `notas-dados` fica livre, e um `docker run -v
notas-dados:/app/data` cria um volume novo e vazio com o mesmo nome, como mostra o
`CreatedAt` acima, posterior à remoção. Esse é o lado perigoso: o serviço sobe
normalmente, sem erro nenhum, só que sem dado algum. A persistência que o volume
oferece vale contra o ciclo de vida dos *containers*, não faz as vezes de backup e não
protege o volume de ser removido.

---

## 8. Extra: Docker Compose

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

O detalhe que importa para o tema da atividade é que o `docker compose down` remove o
container e a rede, mas preserva o volume. Quem apaga os dados é o
`docker compose down -v`, a mesma distinção entre destruir container e destruir volume
que a Etapa 5 mostrou na mão.

---

## 9. Dificuldades e aprendizados

A parte mais difícil não foi montar o Dockerfile, foi perceber que a explicação que eu
ia dar para a Etapa 6 estava errada. A resposta óbvia, que eu tinha na cabeça desde o
começo, era que sem `-v` os dados ficam na camada de escrita do container e morrem com
ele. Rodei `docker inspect efemero --format '{{json .Mounts}}'` mais por capricho do
que por dúvida, e apareceu um volume anônimo montado em `/app/data`. Os dados nunca
tinham passado pela camada de escrita. A causa era a linha `VOLUME /app/data` do meu
próprio Dockerfile, que manda o Docker criar um volume mesmo quando ninguém pede. Isso
mudou a conclusão do exercício: o nome do volume não serve para o dado sobreviver,
serve para eu conseguir encontrá-lo depois. Sem nome, o volume continua lá, órfão, com
um hash que ninguém anotou, ocupando disco com um dado que nenhum container vai montar
de novo. Foi o que eu mais aproveitei do trabalho, e só apareceu porque fui inspecionar
em vez de aceitar o resultado esperado, já que o `[]` do `curl` confirmava a hipótese
errada com a mesma cara com que confirmaria a certa.

A segunda dificuldade foi de ambiente. A Etapa 7 pergunta onde o volume fica
fisicamente no host, e o `docker volume inspect` respondeu
`/var/lib/docker/volumes/notas-dados/_data`, um caminho que simplesmente não existe no
meu macOS. O Docker Engine é Linux, e o Docker Desktop executa ele dentro de uma VM,
então aquele caminho é de lá. Precisei de um `docker run -v /:/host alpine ls ...` para
provar que o diretório existe, e ainda assim só do lado de dentro da VM, cujo disco
inteiro é um arquivo esparso de 41G no meu `~/Library`. Entendi ali por que a
documentação insiste que volume se gerencia pela API do Docker e não pelo sistema de
arquivos: em boa parte das plataformas, o caminho que o `inspect` devolve nem é
alcançável pelo terminal do host.

No Dockerfile, o que mais pesou foi a ordem das instruções. Copiar o `requirements.txt`
e instalar as dependências antes de copiar o `app.py` faz com que editar o código
invalide uma camada de 12,3 kB, em vez de refazer um `pip install` de 7,85 MB. Na
mesma linha, o `mkdir -p /app/data && chown` tem que vir antes do `VOLUME /app/data`,
porque o que se escreve num caminho depois de ele virar volume é descartado no build.
Foi assim, de graça, que a permissão do diretório para o usuário sem privilégios acabou
funcionando, já que o Docker copia conteúdo e permissões da imagem para dentro do
volume nomeado vazio na primeira montagem. Também tive que corrigir duas expectativas
pelo caminho. O `EXPOSE 8000` não publica porta nenhuma, é metadado, e quem publica é o
`-p`. E o `docker image ls` do Docker 29 não traz mais a coluna `SIZE`: mostra
`CONTENT SIZE` (49 MB, o que iria num push) e `DISK USAGE` (224 MB, o que ocupa aqui),
dois números diferentes e os dois honestos para "o tamanho da imagem". A conta só
fechou depois que eu somei as camadas do `docker history`.

O que ficou de mais geral é que imagem, container e volume têm ciclos de vida
independentes. O `docker rm` derrubou container três vezes sem encostar nos dados, e o
`docker volume rm` apagou tudo de uma vez, sem confirmação e sem volta. Guardar em
volume protege contra a destruição do container, que é rotina (em Kubernetes um pod é
recriado a qualquer momento), mas não protege contra a remoção do próprio volume e não
faz as vezes de backup. O modo de falha que mais me chamou atenção foi o da questão
7.3: depois de apagar o volume, subir o serviço com exatamente o mesmo comando
funciona, o `/health` responde `ok`, e a API sobe inteira. Vazia, mas inteira.

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
