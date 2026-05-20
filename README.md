# Avant Sentinel S2DR4

Aplicativo e pipeline para:

- ler shapefile de fazendas em `Data/`
- gerar blocos 4 x 4 km com 200 m de sobreposicao
- consultar imagens Sentinel-2 no Google Earth Engine
- preparar fila de inferencia S2DR4
- rodar a biblioteca Gamma Earth S2DR4 em ambiente Linux/Python 3.12

## Rodar localmente no Windows

Use:

```powershell
python app.py
```

Guia: [README_LOCAL.md](README_LOCAL.md)

## Rodar no CodeRoom/Linux/GPU

Use:

```bash
bash scripts/setup_coderoom.sh
source .venv/bin/activate
python scripts/prepare_pipeline.py --reference-date 2026-05-19 --months 3 --max-cloud 5
python scripts/run_s2dr4_queue.py
```

Guia completo: [README_CODEROOM.md](README_CODEROOM.md)

## Rodar em Docker/Cloud Run

O Dockerfile usa PyTorch CPU por padrao para evitar estouro de memoria no import
CUDA em ambientes sem GPU. O wheel S2DR4 fica em `vendor/wheels/` e o modelo
S2DR4 de aproximadamente 841 MB e baixado uma vez durante o build da imagem para
`/var/local/S2DR3`, evitando download pesado durante o clique em "Baixar S2DR4".

Se o servico Cloud Run processar S2DR4 em segundo plano sem storage
compartilhado, mantenha `max-instances=1`, CPU sempre alocada e memoria alta
(por exemplo 4 GiB) para que o job continue na mesma instancia.

## Credenciais

Nao envie chaves JSON para o GitHub. Coloque credenciais apenas no ambiente remoto
ou na pasta `auth/` local, que esta ignorada pelo `.gitignore`.
