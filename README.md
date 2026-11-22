# radar-pi — avisador de radares (Raspberry Pi 4) · protótipo SP

Avisa radar **à frente, na mesma via e no mesmo sentido**, a 450 m medidos **ao longo da via**,
mostra limite e velocidade (vermelho se acima) e fala "radar, 60". Tudo offline no Pi.

## Camadas
| Pasta | O quê |
|---|---|
| `radar_pi/coletor/` | um coletor por fonte oficial (CET, DER-SP, ARTESP, DNIT, ANTT, Inmetro, Mogi) → esquema único; proveniência; se a fonte falhar usa a versão anterior; cruzamento Inmetro; interpolação rodovia+km; duplicatas entre fontes |
| `radar_pi/dados/` | SQLite + índice H3 (res. 9) |
| `radar_pi/mapa/` | malha OSM como grafo dirigido em metros (UTM 23S) + vínculo radar → trecho + sentido |
| `radar_pi/mapmatch/` | map matching HMM online (Newson & Krumm) |
| `radar_pi/alerta/` | motor de alerta (mesma via/sentido, 450 m, para após passar) |
| `radar_pi/voz/`, `radar_pi/ui/` | voz Piper TTS pt-BR (OHF-Voice/piper1-gpl, cache das frases comuns, thread própria) com espeak-ng de fallback; tela 320x240 em PNG |
| `radar_pi/mapa_ui/` | **tela principal**: mapa em tempo real (pygame/SDL), só ruas + nomes + radares do SQLite; velocidade no canto sup. esquerdo, placa do próximo radar da via no sup. direito, faixa "RADAR 320 m" + voz; GPS = simulação ou gpsd |
| `radar_pi/sim/` | malhas sintéticas, trajetos GPS 1 Hz com ruído 5–15 m e túnel, loop principal |

## Rodar
```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
sudo apt install espeak-ng                    # fallback da voz
python -m piper.download_voices pt_BR-faber-medium --data-dir data/vozes   # voz Piper (~63 MB, offline depois)
cp .env.example .env                          # RADAR_TTS=piper|espeak, RADAR_PIPER_VOZ, RADAR_PIPER_LENGTH_SCALE
python -m radar_pi.coletor.pipeline          # coleta + banco (data/radares.sqlite)
PYTHONPATH=. python scripts/construir_tiles.py             # graphml OSM -> data/osm/malha_sp.sqlite (tiles H3)
PYTHONPATH=. python scripts/vincular.py                    # radares -> trechos da malha (grafo "sp")
PYTHONPATH=.:tests pytest tests -q            # testes
PYTHONPATH=. python scripts/telas.py                       # PNGs da tela em resultados/telas/
```
## Tela de mapa (layout principal)
100% offline: desenha a partir da malha OSM em tiles H3 no SQLite (`data/osm/malha_sp.sqlite`, carregada
sob demanda em volta do carro: ~130 MB de RSS), sem internet nem API. Carro pesado: `RADAR_VEICULO=pesado`.
Tamanho/tela cheia/FPS/zoom/rotação no `.env` (`RADAR_TELA=800x480` ou `480x320`, `RADAR_FULLSCREEN=1`,
`RADAR_FPS`, `RADAR_ZOOM_M_PX`, `RADAR_ROTACIONAR`, `RADAR_GPS=sim|gpsd`).
```bash
# box / PC: simulação com janela (ESC sai)
PYTHONPATH=. python -m radar_pi.mapa_ui.app --cenario marginal      # marginal | cet_acima | tamoios | sem_radar
# box sem monitor: PNG e MP4 (com a voz mixada)
PYTHONPATH=. python -m radar_pi.mapa_ui.app --cenario marginal --headless --png "13=tela.png" --video resultados/sim.mp4
# Raspberry Pi 4 (Raspberry Pi OS): GPS USB via gpsd, tela cheia
sudo apt install gpsd gpsd-clients espeak-ng python3-pygame fonts-dejavu-core
sudo systemctl enable --now gpsd           # DEVICES="/dev/ttyACM0" em /etc/default/gpsd
RADAR_FULLSCREEN=1 RADAR_GPS=gpsd PYTHONPATH=. python -m radar_pi.mapa_ui.app --gps gpsd
```

Resultados e erros encontrados: `RELATORIO_TESTES.md`. Sem segredos; custo R$0.

## Voz (Piper)
`piper-tts` (repo novo **OHF-Voice/piper1-gpl**; o `rhasspy/piper` está arquivado). Voz padrão `pt_BR-faber-medium`.
Na partida, "radar, 20"…"radar, 120" e "radar adiante" são pré-gerados em `data/voz_cache/` (chave = voz+velocidade+texto), então o aviso toca sem síntese.
Se a voz não existir ou o Piper falhar, cai para espeak-ng sozinho. Benchmark: `PYTHONPATH=. python scripts/bench_voz.py`.
Vídeos com Piper: `resultados/video_piper_{paulista,radial_leste,tamoios}.mp4`.

## Validação (interna: banco/logs, nada muda na tela)
- `scripts/cruzar_osm.py base.csv` cruza a base com os radares do OSM (`highway=speed_camera` / `enforcement=maxspeed`), com raio de 40 m e "próximo" até 150 m, e gera `divergencias.csv`. O OSM **só valida** e nunca é fonte oficial.
  - Para exportar a base: `--exportar-db base.csv`.
  - Overpass por UF/bbox: `--uf SP` ou `--bbox s,w,n,e`.
  - Sem Overpass, use o extrato Geofabrik:
    ```
    curl -LO https://download.geofabrik.de/south-america/brazil/brazil-latest.osm.pbf
    python scripts/cruzar_osm.py base.csv --pbf brazil-latest.osm.pbf --salvar-osm data/raw/osm/radares_osm.json --aplicar-db
    ```
- Colunas do SQLite: `status_validacao` (`confirmado` = 2+ fontes / `nao_verificado` / `conflito`), `n_fontes`, `fontes_validacao` e `motivo_validacao`. O coletor (`pipeline.validar`) recalcula essas colunas a cada coleta.
- Radar que só existe no OSM vai para a tabela `revisao_osm`, a lista de revisão. Ele não entra em `radares` e, portanto, nunca alerta.
- Nada é removido automaticamente. Um radar só sai com evidência oficial (por exemplo, desativação publicada pela CET), e ausência de relato não significa "não existe".
- Telemetria local (`radar_pi/telemetria/`, `RADAR_TELEMETRIA=1`, só com gpsd) guarda apenas agregados por ponto: célula H3 de ~25 m e sentido.
  - Não grava trajeto, horário nem identificação.
  - Descarta os primeiros e os últimos 500 m de cada viagem.
  - Só analisa pontos com 5 ou mais passagens (k-anonimato).
  - Gera `candidato` (ponto onde o pessoal desacelera mas não há radar) e `suspeito` (radar ativo onde ninguém desacelera) na tabela `telemetria_sinais`, só para revisão.
- Passo futuro (não implementado): conferir os pontos em `conflito`/`revisao_osm` com imagens do Mapillary (placa ou equipamento).
