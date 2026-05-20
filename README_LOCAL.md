# Avant Sentinel Local

Aplicativo local para preparar blocos 4 x 4 km sobre os poligonos do shapefile,
consultar Sentinel-2 no Google Earth Engine e organizar os resultados por fazenda.

## Entrada

- Shapefile padrao: `Data/VisitaGFP.shp`
- Coluna de fazenda: `FAZENDA`
- Pasta de saida: `export`

## Autenticacao Earth Engine

O app segue o padrao Avant:

```powershell
$env:APP_ENV="avantev02"
$env:EE_PROJECT="ee-mapa01"
```

Para usuario local, autentique antes se necessario:

```powershell
earthengine authenticate
```

Para service account:

```powershell
$env:APP_ENV="avantev02"
$env:EE_PROJECT="ee-mapa01"
$env:EE_SERVICE_ACCOUNT_EMAIL="gee-app-runner@ee-mapa01.iam.gserviceaccount.com"
$env:EE_CREDENTIALS_PATH="C:\caminho\seguro\gee-service-account.json"
```

## Rodar

```powershell
python app.py
```

Abra:

```text
http://127.0.0.1:8787
```

A tela local exibe:

- mapa com imagem de satelite de fundo
- perimetro da fazenda selecionada
- blocos 4 x 4 km sobrepostos em 200 m
- painel superior com status, progresso e tempo de execucao
- login/teste automatico do Earth Engine pelo ambiente Avant

## Preparar blocos sem abrir a tela

```powershell
python app.py --prepare
```

## Regra Sentinel-2

- Colecao: `COPERNICUS/S2_SR_HARMONIZED`
- Data padrao de referencia: mes corrente, na tela deixado como `2026-05-19`
- Janela: ultimos 3 meses, na ordem maio, abril, marco de 2026
- Preferencia: primeira imagem por mes com `CLOUDY_PIXEL_PERCENTAGE <= 5`
- Fallback: se nenhum dos meses ficar abaixo de 5%, usa a menor nuvem disponivel

## Saidas

- `export/manifest.json`: todos os blocos e fazendas
- `export/<fazenda>/blocks.geojson`: blocos da fazenda
- `export/<fazenda>/blocks.csv`: centros dos blocos
- `export/<fazenda>/blocks/<block_id>/sentinel.json`: Sentinel escolhido por bloco
- `export/sentinel_search.json`: resumo global da busca
- `export/sentinel_search.csv`: resumo tabular com chamada S2DR4 sugerida

## Super-resolucao S2DR4

O pacote do notebook Gamma Earth atual e um wheel Linux Python 3.12. Nesta maquina
local Windows/Python 3.11, o app prepara as chamadas e a selecao Sentinel. Para
rodar a inferencia S2DR4, use o CodeRoom/Cloud Run Linux preparado por
`README_CODEROOM.md`; o runner emula os marcadores minimos do Colab
(`COLAB_GPU=0` e pastas `/content/...`) para manter apenas a inferencia local.

Se for testar manualmente em um Linux Python 3.12, instale:

```python
!pip -q install https://storage.googleapis.com/0x7ff601307fa5/s2dr4-20260518.1-cp312-cp312-linux_x86_64.whl
```

As chamadas por bloco ficam em `export/sentinel_search.csv`, coluna `s2dr4_call`.

Pela tela, o botao `Fila S2DR4` cria:

- `export/s2dr4_queue.csv`, quando todas as fazendas estao selecionadas
- `export/s2dr4_queue_last.csv`, quando uma fazenda especifica esta selecionada

Se o app for executado em Linux/Python 3.12 com o pacote `s2dr4` instalado, essa
fila ja contem os parametros necessarios para automatizar a inferencia e salvar
os produtos por bloco.
