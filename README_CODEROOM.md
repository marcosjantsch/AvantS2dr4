# Rodar Avant Sentinel/S2DR4 no CodeRoom

Este guia prepara o projeto para subir no GitHub e rodar em um ambiente Linux/GPU
do Google, sem depender do notebook Colab original. O notebook serviu apenas para
descobrir a biblioteca e o wheel S2DR4.

## O que o CodeRoom precisa ter

- Linux x86_64
- Python 3.12
- GPU NVIDIA, preferencialmente T4 ou superior, para processamento mais rapido
- acesso ao projeto Earth Engine `ee-mapa01`
- shapefile em `Data/VisitaGFP.shp`

O wheel usado pela biblioteca e:

```text
https://storage.googleapis.com/0x7ff601307fa5/s2dr4-20260518.1-cp312-cp312-linux_x86_64.whl
```

Ele e especifico para Linux + Python 3.12. O setup baixa o wheel para
`vendor/wheels/` dentro do CodeRoom e instala dali.

## Arquivos importantes

- `app.py`: servidor HTML local
- `sentinel_blocks.py`: geoprocessamento, GEE e filas
- `scripts/setup_coderoom.sh`: instala Python deps, PyTorch e S2DR4
- `scripts/prepare_pipeline.py`: gera blocos, consulta Sentinel e cria fila
- `scripts/run_s2dr4_queue.py`: executa S2DR4 em lote
- `scripts/start_app_coderoom.sh`: inicia o app web no CodeRoom
- `scripts/run_full_pipeline.sh`: roda consulta Sentinel + S2DR4 em sequencia
- `.env.example`: variaveis esperadas
- `.devcontainer/`: opcional para ambientes que aceitam devcontainer com GPU

## Preparar o GitHub

1. Crie um reposititorio GitHub.
2. Suba estes arquivos do projeto.
3. Confirme que o shapefile esta completo em `Data/`:
   - `.shp`
   - `.shx`
   - `.dbf`
   - `.prj`
   - `.cpg`, se existir
4. Nao suba credenciais JSON. A pasta `auth/` aceita o arquivo no CodeRoom, mas
   ele esta ignorado pelo `.gitignore`.

## Configurar o CodeRoom

Dentro do CodeRoom:

```bash
git clone URL_DO_REPOSITORIO avant-sentinel
cd avant-sentinel
cp .env.example .env
```

Edite `.env` se necessario:

```bash
APP_ENV=avantev02
EE_PROJECT=ee-mapa01
APP_GEO_PATH=Data/VisitaGFP.shp
APP_EXPORT_DIR=export
```

## Autenticacao Earth Engine

### Opcao 1: usuario interativo

```bash
earthengine authenticate
```

Depois rode o teste:

```bash
python - <<'PY'
import ee
ee.Initialize(project="ee-mapa01")
print(ee.Number(1).add(1).getInfo())
PY
```

Resultado esperado:

```text
2
```

### Opcao 2: service account

Coloque o JSON em `auth/gee-service-account.json` dentro do CodeRoom, sem subir
para o GitHub, e configure `.env`:

```bash
EE_SERVICE_ACCOUNT_EMAIL=gee-app-runner@ee-mapa01.iam.gserviceaccount.com
EE_CREDENTIALS_PATH=auth/gee-service-account.json
GOOGLE_APPLICATION_CREDENTIALS=auth/gee-service-account.json
```

## Instalar ambiente

```bash
bash scripts/setup_coderoom.sh
source .venv/bin/activate
```

Valide:

```bash
python scripts/validate_coderoom.py
```

Procure por:

- `python_ok: true`
- `modules.s2dr4: true`
- `torch.cuda_available: true` em ambiente com GPU. Em Docker/Cloud Run sem GPU,
  `false` e esperado e o Dockerfile usa PyTorch CPU para manter o import estavel.

Se CUDA nao estiver disponivel, o ambiente ainda pode consultar Sentinel/GEE, mas
a super-resolucao pode ficar lenta ou falhar por falta de GPU.

## Rodar pela interface web

```bash
bash scripts/start_app_coderoom.sh
```

Abra a porta `8787` no CodeRoom. A tela permite:

- selecionar fazenda
- visualizar satelite + perimetro + blocos
- testar login GEE
- buscar Sentinel por blocos
- gerar fila S2DR4

## Rodar pipeline por terminal

Gerar blocos, consultar Sentinel e criar fila:

```bash
source .venv/bin/activate
python scripts/prepare_pipeline.py \
  --reference-date 2026-05-19 \
  --months 3 \
  --max-cloud 5
```

Rodar S2DR4 para toda a fila:

```bash
python scripts/run_s2dr4_queue.py
```

Rodar apenas os primeiros 2 blocos para teste:

```bash
python scripts/run_s2dr4_queue.py --limit 2
```

Rodar uma fazenda especifica:

```bash
python scripts/prepare_pipeline.py \
  --reference-date 2026-05-19 \
  --months 3 \
  --max-cloud 5 \
  --farm-slug maria_izabel

python scripts/run_s2dr4_queue.py --queue export/s2dr4_queue_last.csv
```

Pipeline completo:

```bash
REFERENCE_DATE=2026-05-19 MONTHS=3 MAX_CLOUD=5 bash scripts/run_full_pipeline.sh
```

## Saidas

Consulta Sentinel:

- `export/manifest.json`
- `export/sentinel_search.csv`
- `export/sentinel_search.json`
- `export/s2dr4_queue.csv`

S2DR4 por bloco:

```text
export/<fazenda>/blocks/<block_id>/s2dr4/
```

Cada bloco recebe:

- GeoTIFFs gerados pelo S2DR4
- `s2dr4_manifest.json`

Resumo global:

```text
export/s2dr4_run_summary.json
```

## Como o runner S2DR4 funciona

O notebook original grava em `/content/output`. Para manter isso compativel fora
do Colab, o runner cria um link simbolico:

```text
/content/output -> export/<fazenda>/blocks/<block_id>/s2dr4
```

Assim, a biblioteca S2DR4 continua achando o caminho esperado, mas cada bloco
fica separado na estrutura do projeto.

Se o ambiente nao permitir criar `/content`, rode uma vez:

```bash
sudo mkdir -p /content
sudo chown "$USER:$USER" /content
```

## Observacao de licenca

A biblioteca Gamma Earth informa uso para teste/validacao e pede contato para
uso comercial ou funcionalidade estendida. Este projeto automatiza a inferencia
com o pacote publico; nao extrai treinamento, pesos internos ou codigo fonte da
biblioteca.
