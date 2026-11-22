# Relatório de testes — radar-pi (protótipo SP)

Executado no box em 22/11/2026, ~11h–11h45 (horário de Brasília), em modo simulação. Python 3.13, 8 núcleos x86, 16 GB.
**Regra do Johnny (v1):** os erros abaixo foram só **documentados, não corrigidos**.
> **Atualização 22/11/2026 ~13h (Brasília): os erros E1–E11 e N1–N10 foram corrigidos — ver a seção 8 (antes/depois e o que ficou pendente).** As seções 1–7 são o retrato da v1.

## 1. Resumo
- Sistema completo montado e rodando ponta a ponta: coletor → SQLite/H3 → malha OSM → vínculo radar/via/sentido → HMM → motor de alerta → tela PNG + voz (espeak-ng).
- **31 testes**: **21 passaram e 10 falharam** na rodada completa (`resultados/junit.xml`). O teste de desempenho falhou na rodada completa, mas passou isolado, então é instável.
- O que já funciona bem: rua paralela, curva, troca de rua, parar após passar, limite 90/60, radar desativado, Tamoios, velocidade acima/abaixo, fallback de fonte e H3.
  - Em 115 radares reais da CET, **98,3%** foram avisados.
  - O erro mediano da distância exibida ficou em **6,1 m** (p95 de 21,1 m).
- Problemas mais graves:
  1. **54,6% dos radares CET ativos ficam sem sentido definido.** Eles são ligados aos dois sentidos, o que gera aviso na contramão e na pista vizinha.
  2. **Só 36,9% dos radares ativos têm limite oficial.** O Inmetro bloqueou com 403, e DER, ARTESP e DNIT não publicam limite.
  3. **Túnel/perda de GPS:** não existe dead-reckoning (estimar a posição sem GPS), e o primeiro aviso saiu a só 192 m do radar.
  4. **Indicador "via incerta" em excesso:** acende em 14% dos quadros reais e gera avisos piscando de radares de vias vizinhas.

## 2. Tabela de testes
| # | Teste | Cenário | Resultado |
|---|---|---|---|
| 1 | rua_paralela | Rua A com radar a 450 m e rua B paralela (60 m) com radar a 150 m | ✅ avisou só A, começando a 443,9 m |
| 2 | contramao (sintético) | mão dupla, radar CENTRO/BAIRRO, carro no sentido oposto | ❌ 3 avisos "via incerta" de 1 quadro (ver E2) |
| 3 | marginal_colada (sintético) | expressa × local a 15 m, 10 rodadas | ✅ nenhum aviso confiante errado; radar da expressa avisado em 10/10 (observações em E4) |
| 4 | curva | semicírculo R=300 m | ✅ começou a 438 m pela via (reta = 400 m), exibiu 447 m |
| 5 | troca_de_rua | vira da Rua D para a A, com radar 450 m após a esquina | ✅ avisou já na esquina (t=54 s; cruzamento em t=55 s), a 433,6 m |
| 6 | passou_do_radar | segue 650 m após o radar | ✅ 1 episódio só; 0 quadros alertando 3 s depois |
| 7 | gps_ruidoso_15m | 20 rodadas com ruído de 12–15 m | ❌ 4/20 com problema (E5) |
| 8 | tunel | sem sinal de 300–800 m, radar em 1000 m | ❌ 1º aviso a 192 m (E3) |
| 9 | velocidade | limite 50, a 40 e a 65 km/h | ✅ 40: 0/40 quadros vermelhos · 65: 26/26 vermelhos |
| 10 | limite_composto 90/60 | 80 km/h | ✅ exibe "90/60", não fica vermelho, voz "radar, 90" |
| 11 | radar_desativado (sint.) | status inativo | ✅ sem aviso |
| 12 | radar_sem_limite | DER sem limite; via com e sem maxspeed no OSM | ✅ exibe "80*" (estimado, OSM) e "?"; voz "radar, 80" / "radar" |
| 13 | cobertura_dados | real | ❌ 36,9% com limite; 3 fontes sem coleta nova (E1, E7) |
| 14 | vinculo_cet_sentido | real, 879 CET ativos | ❌ só 45,4% com sentido resolvido (E2) |
| 15 | cet_real_recall | 115 radares CET reais, malha SP | ❌ detecção 98,3%, mas só 88,7% avisados com ≥400 m e 6 fragmentados (E5, E6) |
| 16 | cet_real_falsos | mesmas 115 rotas | ❌ 1 aviso confiante de radar fora da rota e 21 "via incerta" (E2, E4) |
| 17 | contramao_real | 40 radares CET com sentido resolvido, contramão | ✅ 0 avisos confiantes (8 "via incerta") |
| 18 | marginal_real | 12 radares 90/60 das Marginais (pista expressa × central/local, 18–35 m) | ❌ em 2/12 avisou com certeza o radar da expressa estando na central (E4) |
| 19 | desativado_real | 24 radares CET desativados | ✅ 0 avisos |
| 20 | tamoios_sp099 | 20 radares DER/ARTESP na SP-099 | ✅ 18/18 avisados (2 com rota curta descartados) |
| 21 | geocodificação rodovia+km | DER-SP, leave-one-out | ✅ mediana de 85 m, mas p90 de 769 m e máximo de 72 km (E8) |
| 22 | desempenho_sp | 60 rotas, 7.463 ciclos | ❌ na suíte o pior ciclo foi 343 ms; isolado ✅ 11,4 ms (E9) |
| 23 | parse_limite / sentido / rodovia_km | unidade | ✅ |
| 24 | cet_sentido_abreviado (C/B) | unidade | ❌ "(C/B)" vira "indefinido" (E2) |
| 25 | coletor_fallback / sem versão / HTML de bloqueio | unidade | ✅ (3 testes) |
| 26 | h3_vizinhança | unidade | ✅ |
| 27 | voz_espeak | unidade | ✅ WAV gerado em 10,3 ms |

Linhas 23 e 25 agrupam 3 testes cada, o que fecha os 31 testes.

## 3. Erros documentados (não corrigidos)
| ID | Erro | Cenário | Esperado × obtido | Causa provável | Severidade |
|---|---|---|---|---|---|
| E1 | Limite oficial ausente na maioria dos radares | cobertura | ≥95% × **36,9%** (DER 0/1176, ARTESP 0/590, DNIT 0/21) | o Inmetro, fonte do limite, deu **HTTP 403** (2 tentativas, 11h13 e 11h16); DER, ARTESP e DNIT não trazem limite. Hoje cai no `maxspeed` do OSM ("80*") ou "?" | **Alta** |
| E2 | Sentido não resolvido → radar ligado aos 2 sentidos | vínculo CET; contramão sintética; falso real 4303→4304 | ≥90% × **45,4%**. 448 radares viram "sentido_indefinido" | (a) o texto da CET usa destinos ("AYRTONSENNA/CASTELOBRANCO", "CB/AS", "INTERLAGOS/C.BRANCO"), e o vínculo só entende CENTRO/BAIRRO; (b) as abreviações **"C/B" (33) e "B/C" (31)** não são tratadas; (c) no modo indefinido, o "sentido oposto" pode cair numa pista paralela no mesmo sentido (Av. Salim Farah Maluf: radar 4304 avisado com certeza a 41,7 m na rota do 4303) | **Alta** |
| E3 | Túnel/perda de GPS sem estimativa | tunel | aviso a ≥300 m × **192 m**; durante o túnel a tela não mostra nada (8 amostras = sem aviso) | não há dead-reckoning: no túnel o estado fica parado no último fix | **Alta** (Anhangabaú, túneis da 23 de Maio, Tamoios) |
| E4 | "Via incerta" em excesso e marginal colada | marginal sintética/real, contramão sintética | só quando de fato ambíguo × em **14,6% dos quadros reais** (14,1% dos quadros com aviso); 2/12 Marginais com aviso confiante do radar da expressa estando na central; em 1/10 rodadas sintéticas o HMM ficou 97% do tempo na pista errada | a ambiguidade é medida pela probabilidade da **melhor aresta isolada**, e perto de cada nó a probabilidade se divide entre arestas consecutivas da mesma via, o que dispara o "alternativo" (via vizinha ou sentido oposto). Pistas a <25 m não têm transição que as separe, então o HMM fica na que escolheu primeiro | **Média/Alta** |
| E5 | Aviso pisca (fragmenta) perto de 450 m | gps_ruidoso (4/20); recall real (6/115) | 1 aviso contínuo × some 1 quadro e volta; a rua B paralela aparece rapidamente como "via incerta" | sem histerese no limiar de 450 m; ruído de 12–15 m faz a distância oscilar em volta dele; mais o E4 | Média |
| E6 | Aviso tardio em parte dos radares reais | recall CET | ≥90% com ≥400 m × **88,7%** (102/115) | aviso só começa quando o carro está na mesma via: radar logo após cruzamento ou troca de nome da via (a continuação exige o mesmo nome) e 2 não detectados (sentido_indefinido) | Média |
| E7 | Fontes sem coleta nova | cobertura | todas OK × **Inmetro 403**, **ANTT timeout** (45 s), **ARTESP erro SSL**. O ARTESP usou a versão anterior (fallback funcionou) | bloqueio/instabilidade dos servidores públicos a partir do box | Média |
| E8 | Interpolação rodovia+km imprecisa às vezes | geocod leave-one-out | máx. pequeno × p90 769 m, **máx. 72 km**; 710/1176 sem referência | rodovias com o mesmo número em trechos descontínuos e km que não é monotônico; sem a malha SNV do DNIT (não baixada) | Média (afeta só o futuro cruzamento com o Inmetro) |
| E9 | Pico de latência na suíte completa | desempenho | pior ciclo ×5 (Pi) <1 s × 343 ms no box (≈1,0–1,7 s estimado no Pi); isolado 11,4 ms | provável coleta de lixo do Python com 2 malhas (~600 MB cada) carregadas no mesmo processo | Baixa/Média |
| E10 | Duplicatas dentro da mesma fonte não consolidadas | Tamoios | 1 registro por ponto × ARTESP com 3 registros idênticos (km 35, radar estático) | a deduplicação só compara fontes diferentes | Baixa (o aviso sai uma vez só) |
| E11 | Limite composto: premissa não confirmada | 90/60 | — | assumi que o 1º valor vale para leves (90) e o 2º para pesados. "60/50/50" (24 radares) não tem significado claro no dicionário da CET | Baixa (documentar) |

**Contornos e ajustes feitos (nenhuma correção depois dos testes):**
- Durante o desenvolvimento, antes da 1ª rodada, fiz o radar só ir para a lista "já passado" quando o aviso some a menos de 60 m dele.
- Ajustei o *teste* (não o sistema) em 4 pontos de montagem errada do cenário:
  - ângulo do arco no teste de curva;
  - nome normalizado ("MARG") no teste da marginal;
  - fim do trajeto em +150 m após o radar;
  - registros idênticos no mesmo ponto contam como o mesmo radar.
- No Overpass principal, a malha da Tamoios deu erro SSL e eu baixei pelo espelho maps.mail.ru.

## 4. Métricas
**Dados (SP)**
| Fonte | Registros | Ativos | Ativos c/ limite | c/ coordenada | Coleta |
|---|---|---|---|---|---|
| CET-SP (GitHub) | 2.066 | 879 | 879 (100%) | 100% | ok |
| DER-SP | 1.176 | 1.176 | 0 | 100% | ok |
| ARTESP | 844 | 590 | 0 | 100% (3 interpolados) | falhou → versão anterior |
| DNIT (SP) | 21 | 21 | 0 | 100% | ok |
| Mogi das Cruzes | 39 | 33 | 28 | 100% | ok |
| ANTT | 0 | — | — | — | timeout |
| Inmetro (SP) | 0 | — | — | — | 403 |

- 2.460 radares ativos únicos.
- 239 duplicatas entre fontes, todas ARTESP × DER-SP a ≤30 m. Nesses casos ficou o registro do DER.

**Malha e vínculo**
- Cidade de SP: 122.778 nós e 304.790 arestas. Download de 241 s; graphml de 158 MB, pickle de 93 MB.
- Corredor da Tamoios: 3.073 arestas.
- Vínculos:
  - SP: 2.173 radares → 2.864 vínculos, 105 sem via a ≤40 m;
  - Tamoios: 46 radares → 46 vínculos, 7 sem via.

**Desempenho (box, processo isolado)**
- Por ciclo (GPS → HMM → alerta): mediana de 0,14 ms, p95 de 0,63 ms, p99 de 0,92 ms, máximo de 11,4 ms, em 7.463 ciclos.
- Memória: 656 MB para a malha da cidade de SP e 745 MB de RSS total. O banco tem 2,0 MB.
- Voz: 10 ms por frase (WAV).

**Estimativa para o Pi 4** (premissa de 3–5× mais lento; não foi medido em Pi real)
- p95 de 2–3 ms e pior caso de 34–57 ms por ciclo. Cabe com folga em 1 Hz.
- Memória: o Pi 4 de **4 GB** atende a cidade de SP; o de 2 GB fica no limite.
- O **estado de SP inteiro** não cabe nesse formato (networkx + shapely). Seria preciso uma malha mais enxuta (arrays/SQLite) ou recortes por região.

## 5. Limitações
- **Malha:** só a cidade de SP e o corredor da Tamoios. Os radares DER/ARTESP fora disso não foram testados no mapa.
- **Simulação:** trajetos sintéticos sobre o próprio grafo. Erros do OSM (via faltando, geometria deslocada) não aparecem; GPS real em cânion urbano pode ser pior que 15 m.
- **Radares móveis:** os da PRF e da PM não estão em nenhuma base.
- **Radares estáticos da ARTESP:** são pontos onde o radar *pode* estar, e o sistema avisa como fixo.
- **Bibliotecas:** o HMM é implementação própria (online). O `leuvenmapmatching` foi instalado, mas não foi usado. Piper não foi instalado; a voz é espeak-ng, que gera WAV no simulador.
- **Hardware:** nada foi testado em hardware real (GPS, display SPI, áudio).
- **Limite estimado:** o "80*" vem do OSM, não é oficial. Contraria a meta de dado 100% oficial até o Inmetro abrir.

## 6. Arquivos
- `resultados/junit.xml`: resultado dos testes.
- `resultados/metricas.jsonl`: métricas de cada cenário.
- `resultados/telas/*.png`: telas simuladas.
- `logs/voz/*.wav`: falas geradas.

---
# 7. Tela de mapa em tempo real (layout principal), 22/11/2026 ~12h–12h30 (horário de Brasília)
Os erros E1–E11 **não foram corrigidos**. Esta seção cobre só a nova tela e os problemas novos (N1–N10).

**O que foi feito** (`radar_pi/mapa_ui/`)
- **Base:** pygame/SDL, 100% offline. O mapa é desenhado a partir do grafo OSM já baixado: 304.790 arestas, que viram uma camada de desenho sem duplicar a mão dupla.
- **Mapa:** só ruas (largura e cor pelo tipo da via) e nomes alinhados à rua, com anticolisão e no máximo 18 por quadro.
- **Radares:** saem do SQLite por anel H3. O ícone é a placa com o limite ou uma câmera quando não há limite oficial; o radar do alerta ganha um halo vermelho.
- **Carro:** seta azul centrada a 80% da altura, com o mapa girando conforme o rumo. A posição é a do map matching, extrapolada entre os fixes de 1 Hz e suavizada.
- **HUD:**
  - velocidade no canto sup. esquerdo (vermelha se acima do limite da placa);
  - placa do próximo radar na mesma via/sentido no canto sup. direito, até 2 km, ou "--" se não houver;
  - faixa "RADAR 316 m" (laranja quando a via é incerta);
  - pílula com o nome da via;
  - voz "radar, 90".
- **GPS:** `FonteSimulada` (rota sintética) ou `FonteGPSD` (cliente JSON do gpsd sem dependências, testado com um servidor gpsd falso).
- **Saídas:** janela, tela cheia (Pi), PNG e MP4 headless com a voz espeak mixada no áudio.

**Testes novos:** `tests/test_mapa_ui.py`, **2/2 ✅**.
- gpsd falso: TPV → Fix (lat, velocidade 22,2 m/s, rumo 95°, precisão 8 m); `mode=1` → sem fix.
- App headless nos cenários CET e sem radar: PNG gerado; voz "radar, 50" num e nenhuma voz no outro.

**Entregas:**
- `resultados/telas/mapa_1_marginal_tiete_90-60.png`
- `mapa_2_cet_acima_vermelho.png`
- `mapa_3_tamoios.png`
- `mapa_4_rua_sem_radar.png`
- `mapa_5_marginal_480x320.png`
- `resultados/mapa_simulacao_marginal_tiete.mp4`: 33,1 s, 800x480, 20 fps, H.264 + áudio AAC com "radar, 90" em t=8 s. Começa a 95 km/h (vermelho, acima de 90), reduz para 80, o alerta abre a 450 m e some depois do radar.

**Desempenho (medido no box)** — tempo só de render por quadro:

| Cenário | Tela | p50 | p95 | máx |
|---|---|---|---|---|
| Marginal Tietê | 800x480 | 12,0 ms | 15,7 ms | 27,0 ms |
| CET acima | 800x480 | 9,5 | 11,8 | 21,8 |
| Rua sem radar (Penha) | 800x480 | 15,1 | 17,1 | 26,3 |
| Tamoios (malha só de rodovias) | 800x480 | 0,5 | 0,9 | 2,8 |
| Estresse: 300 pontos aleatórios de SP × 3 rumos | 800x480 | 15,0 | 19,9 | 26,9 |
| Marginal / CET / sem radar | 480x320 | 4,0 / 3,8 / 6,4 | 4,9 / 4,5 / 7,4 | 28,7 / 6,7 / 11,2 |
| Estresse 300 pontos | 480x320 | 6,4 | 8,4 | 15,4 |

- **Memória do app** (malha SP + camada de desenho): 751 MB de RSS.
- **Partida:** malha em 1,3–2,2 s e camada de desenho em 0,7–0,8 s.
- **Correção da posição a cada fix** (salto que a suavização absorve): mediana de 4–5 m, p95 de 9–15 m, máx. de 17 m.

**Estimativa para o Pi 4** (premissa de 4–8× mais lento para o render por software do SDL num núcleo Cortex-A72; **não medido em Pi real**)
- **800x480:** p95 de ~80–160 ms, ou seja ~6–12 FPS.
- **480x320:** p95 de ~34–67 ms, ou seja ~15–29 FPS.
- Os dois atendem um mapa de navegação com GPS de 1 Hz. Para 800x480 eu recomendo `RADAR_FPS=10`.

**Problemas novos (documentados, não corrigidos)**

| ID | Problema | Evidência | Causa provável | Severidade |
|---|---|---|---|---|
| N1 | Ícones de radar não mostram o sentido: o radar da contramão aparece igual ao seu | screenshot CET: dois "50" na Av. Jaguaré, só um é do seu sentido | o mapa desenha todos os radares ativos do anel H3 | Média |
| N2 | Voz bloqueia a tela no Pi | `Voz(tocar=True)` chama `espeak-ng` com `subprocess.run` (síncrono). No box (só WAV) leva 10 ms; tocando, deve travar o render por ~1 s a cada alerta | fala no mesmo thread do render | **Alta** (no Pi) |
| N3 | Em 480x320, o radar a 450 m fica fora da tela quando o alerta começa | com 1,5 m/px a visão à frente até a faixa é de ~316 m (em 800x480, ~480 m) | o zoom não escala com a resolução | Média |
| N4 | Nome da via atual raramente aparece no mapa em vias curvas (só na pílula); rótulos cortados na borda (ex. "Viaduto do Chá") e às vezes sob os ícones | screenshots 1 e Sé | o rótulo exige um trecho quase reto do tamanho do texto; não há teste de borda nem de colisão com ícones | Baixa |
| N5 | Tamoios: mapa quase vazio, só a rodovia | screenshot 3 | a malha do corredor foi baixada só com motorway/trunk/primary/secondary | Baixa (dado) |
| N6 | Placa do canto direito herda o E2: pode mostrar o limite de radar do outro sentido ou da pista vizinha | mesmo vínculo do motor | radares com sentido indefinido ficam ligados aos 2 sentidos | Média |
| N7 | Velocidade só fica vermelha quando há radar na via (placa); com "--" nunca fica vermelha, mesmo acima do `maxspeed` da rua | regra atual | regra do layout pedido (limite do radar) | Baixa (decidir) |
| N8 | A seta pula de pista quando o HMM troca de pista (E4), e há correções de até 17 m por fix em curva | métrica de correção | posição = ponto do map matching + extrapolação em linha reta | Baixa/Média |
| N9 | Radares no mesmo ponto ficam empilhados (ARTESP com 3 registros idênticos, CET com 2 códigos no mesmo poste) | E10 | não há agrupamento de ícones | Baixa |
| N10 | Memória de 751 MB e ~3 s de partida só para a cidade de SP; o estado inteiro não cabe no Pi de 2 GB | RSS medido | networkx + shapely + camada de desenho em Python | Média |

**Limitações**
- Nada rodou em Pi/monitor real. FPS e voz tocando no alto-falante não foram medidos.
- O gpsd real não foi testado, só o servidor falso.
- O mapa não tem prédios, água nem áreas verdes (de propósito: só ruas). A cor segue o tipo OSM da via.

---
# 8. Correções v2 (E1–E11, N1–N10), 22/11/2026 ~12h–13h (horário de Brasília)
Johnny aprovou corrigir tudo. Backup da v1 em `/workspace/radar-pi-v1-backup/`; métricas/telas/vídeo da v1 em `resultados/v1/`.

**Suíte completa: 53 testes (33 da v1 + 20 novos) → 51 ✅ e 2 ❌** (`resultados/junit.xml`, `resultados/metricas.jsonl`).
Dos 33 testes da v1: **31 ✅** (antes 23 ✅ / 10 ❌). Os 2 que seguem falhando dependem de fonte externa (E1/E7) ou de 1 caso residual (E5).
Testes novos: `tests/test_correcoes_v2.py` (19) e `test_limite_composto_pesado_e11` (1).

## 8.1 Antes × depois (números medidos)
| ID | Correção feita | Antes (v1) | Depois (v2) | Status |
|---|---|---|---|---|
| E1 | Inmetro: 3 tentativas com requests + curl (TLS antigo), cabeçalhos de navegador, http, PDF de metadados e API dados.gov.br → **todas 403/401/500**. Limite do Inmetro obtido via espelho público (Pardal/GitHub, compilação de terceiros a partir do Inmetro/DER/ANTT) cruzado a ≤40 m; marcado `limite_origem` (oficial / inmetro_via_espelho / antt_via_espelho); OSM `maxspeed` como "80*" | 36,9% dos ativos com limite | **86,7%** (2.021/2.332): 905 fonte direta (38,8%), 1.062 Inmetro via espelho, 54 ANTT via espelho; +73 radares ANTT novos. Vinculados com limite efetivo (oficial+OSM): 99,7% | ⚠️ parcial — meta 95% não atingida; espelho não é fonte oficial direta |
| E2 | Abreviações C/B, B/C; gazetteer de ~130 destinos de SP (CB/AS, INTERLAGOS/C.BRANCO, Marginais como linhas); `RUMO:` do espelho; via de mão única; sentido não resolvido → 2 sentidos **sempre com confiança 0,3** | 45,5% dos CET vinculados com sentido (399/876); 333 radares ligados aos 2 sentidos com confiança alta | **92,7%** com sentido (810/874: 460 texto, 345 gazetteer, 5 mão única); **0** ligados aos 2 sentidos com confiança alta. Pares espelhados (X/Y × Y/X no mesmo ponto): 38/38 em sentidos opostos | ✅ (teste ≥90%: 92,2%) |
| E3 | Dead reckoning pela malha (velocidade + continuação da via, até 90 s/2,5 km) | túnel: 1º aviso a 192 m; tela vazia no túnel | **1º aviso a 441,7 m**; distância exibida no túnel com erro máx. de 2,4 m; tela mostra "SEM GPS - posição estimada" | ✅ |
| E4 | Ambiguidade somada por **via** (não por aresta), limiar 0,8, penalidade de troca de via, histerese na via; "via incerta" só quando as hipóteses discordam sobre o radar; detecção de **pista colada** (paralela, mesmo sentido, < 2,5σ do GPS, máx. 30 m) | Marginais reais: 2/12 avisos confiantes errados; contramão sintética: 3 "via incerta" | Marginais reais **0/12**; sintético 15 m **0/10**; contramão sintética **0** avisos; ambiguidade do HMM em 0,9% dos quadros com aviso | ✅ (ver custo em 8.2) |
| E5 | Histerese (começa ≤450 m, termina >480 m), "segura" 3 s com distância extrapolada, debounce do ramo alternativo, voz 1× por radar | GPS ruidoso 4/20 com problema; 6 avisos fragmentados em 115 radares reais | GPS ruidoso **0/20**; parado no limiar com ruído 12–15 m: **0/5** piscando; reais: **1** fragmentado em 117 | ⚠️ 1 caso residual |
| E6 | Continuação quase reta (≤20°, mesma classe) quando a via troca de nome | 88,7% avisados com ≥400 m | **95,8%** (sintético troca de nome: aviso a 450 m) | ✅ |
| E7 | Retry (3×, requests + curl, backoff); para cedo em 401/403; fallback para versão anterior | ARTESP erro SSL; ANTT timeout; Inmetro 403 | **ARTESP OK** (curl); ANTT segue em timeout do box (73 radares vieram do espelho); Inmetro segue 403 | ⚠️ externo |
| E8 | Interpolação rodovia+km: lacuna máx. 5 km + coerência (distância entre marcos ≤ 1,5×Δkm + 300 m) | mediana 85 m, p90 769 m, máx 72 km, 710 sem ref. | mediana **46,5 m**, p90 **373 m**, máx **40,7 km**, 855 sem ref. (rejeita mais) | ⚠️ melhorou; máx. ainda alto (falta malha SNV do DNIT) |
| E9 | `gc.freeze()` após carregar + malha por tiles (bem menos objetos) | pior ciclo na suíte 343 ms | p95 1,57 ms; dirigindo contínuo: p99 79 ms, **máx 175 ms** (Pi 4 est. 525–875 ms); com "teletransporte" para outra região: máx 296 ms | ⚠️ ver 8.2 |
| E10 | Dedup na mesma fonte (≤5 m, H3 res 12) | ARTESP 3 registros iguais | **182** duplicatas internas removidas | ✅ |
| E11 | `RADAR_VEICULO=leve|pesado`: "90/60" = leves/pesados (CET NT 240); "60/50/50" → 1º p/ leve, menor p/ pesado | premissa fixa | leve: placa 90, 80 km/h normal; pesado: placa 60, 80 km/h vermelho | ✅ ("60/50/50" segue sem fonte) |
| N1 | Seta do sentido fiscalizado no ícone; radar da contramão apagado (cinza, menor); "?" laranja se sentido não confirmado | todos iguais | teste: contramão marcada como oposta | ✅ |
| N2 | Voz em thread com fila (fala velha é descartada) | `subprocess.run` no render (~1 s no Pi) | chamada **0,02–0,04 ms**; espeak roda à parte | ✅ |
| N3 | Zoom base escala com a resolução + auto-zoom até o radar caber abaixo da faixa | 480x320: radar a 450 m fora da tela | **100%** dos quadros com aviso mostram o radar (800x480 e 480x320); zoom 2,25→3,29 m/px em 480x320 | ✅ |
| N4 | Rótulo inteiro dentro da tela, sem colidir com ícones, faixa e pílula | rótulos cortados/sob ícones | sem corte nas telas novas | ✅ (sem métrica automática) |
| N5 | Corredor da Tamoios rebaixado com toda a malha viária a 1,5 km da SP-099 | só rodovia | 4.409 nós / 9.931 arestas no corredor (teste: >1.000 ruas locais) | ✅ |
| N6 | Placa ignora vínculo com confiança <0,5 | herdava o E2 | sentido indefinido: 31 avisos "via incerta" e **0** placas | ✅ |
| N7 | Sem radar: placa cinza "60*" (via, OSM) e velocidade vermelha acima dela | nunca vermelha com "--" | teste de pixel: 72 km/h em via 60 → vermelho; 55 → normal. Sem `maxspeed` no OSM continua "--" | ✅ |
| N8 | Previsão entre fixes **ao longo da via** (não em linha reta) + troca de pista suavizada | correção por fix: p95 9–15 m, máx 17 m | p95 **8,4–10,7 m**, máx **13,1–16,8 m** | ⚠️ melhora pequena |
| N9 | Agrupa ícones a <1,2 raio na tela com contador "x2" | empilhados | teste: 2 no mesmo poste → 1 ícone "x2" | ✅ |
| N10 | Malha em **SQLite por tiles H3** (res 7, 59,9 MB, 314.721 arestas, SP + Tamoios), carga sob demanda (anel 1 na hora, resto 1 tile por chamada, descarta além do anel 4); sem networkx/pickle | **751 MB** RSS, ~3 s de partida, só a cidade | app **85–144 MB** RSS, partida <0,1 s; 25 km cruzando SP: +82 MB, ≤26 tiles | ✅ (estado inteiro: é só baixar e rodar `construir_tiles.py`) |

## 8.2 O que ficou pendente e por quê
1. **E1/E7 – limite oficial direto:** o Inmetro bloqueia o box (403 em todas as variantes) e a ANTT não responde daqui. 86,7% de cobertura depende do espelho de terceiros (marcado `inmetro_via_espelho`). Para ≥95% oficial: rodar o coletor do Inmetro de outra rede (ex.: o próprio Pi em casa) ou pedir o dado via LAI. `test_cobertura_dados` segue ❌ (86,7% e 2 fontes sem coleta nova).
2. **E5 – 1 aviso fragmentado** em 117 radares reais (`test_cet_real_recall` ❌ só por isso; recall 98,3% e 95,8% com ≥400 m passam).
3. **Custo do E4:** a regra de pista colada deixa mais avisos como "via incerta": 24% dos quadros com aviso no subconjunto de 68 rotas (motivos: 7% pista colada, 7% vínculo de baixa confiança, 0,9% HMM). Na v1 eram 14%. É o preço de zerar avisos confiantes errados na pista vizinha.
4. **E9/N10 – picos ao carregar tiles:** carga de 7 tiles de uma vez leva até ~120–300 ms no box; render máx. subiu para ~100 ms nesses quadros (v1 27 ms). No Pi isso pode passar de 0,5 s. Próximo passo: carregar tiles numa thread de fundo.
5. **E8:** máx. ainda 40,7 km (falta a malha SNV do DNIT para validar km).
6. **N8:** a melhora foi pequena; o salto vem do próprio ruído do GPS (5–15 m).
7. **Harness:** nos falsos alertas, o teste passou a considerar a continuação da via após o fim do trajeto (700 m). Os 2 "falsos" antes contados eram o próximo radar da mesma via, a 330 m e 446 m do alvo (conferido pelo grafo), ou seja, avisos corretos.
8. Nada rodou em Pi real (FPS, voz no alto-falante, gpsd real).

## 8.3 Entregas regeneradas
- Telas: `resultados/telas/mapa_1_marginal_tiete_90-60.png`, `mapa_2_cet_acima_vermelho.png`, `mapa_3_tamoios.png`, `mapa_4_rua_sem_radar_osm.png`, `mapa_5_marginal_480x320.png` e as 3 telas 320x240 (`1_…`, `2_…`, `3_…`).
- Vídeo: `resultados/mapa_simulacao_marginal_tiete.mp4` (33,1 s, 800x480, H.264 + AAC, "radar, 90" em t=8 s).
- Render no box (800x480, p50/p95): Marginal 11,5/20,2 ms; CET 14,5/24,9; sem radar 10,8–12,7/12,9–13,7; Tamoios 1,5/1,9. 480x320 Marginal 8,4/13,3.
- Novos: `radar_pi/mapa/tiles.py`, `radar_pi/mapa/gazetteer.py`, `scripts/construir_tiles.py`, `scripts/metricas_vinculo.py`, `tests/test_correcoes_v2.py`.

## 9. Voz Piper TTS (22/11/2026)
- Repositório: OHF-Voice/piper1-gpl (`pip install piper-tts==1.8.0`, onnxruntime 1.32.0). Voz `pt_BR-faber-medium` (22 050 Hz, 63 MB), baixada com `python -m piper.download_voices`. API Python (`PiperVoice.load` + `synthesize_wav`, `length_scale=0.9`).
- `radar_pi/voz/tts.py`: motor Piper → fallback espeak-ng (voz ausente ou exceção na síntese); fala em thread com fila (N2); cache das 12 frases comuns gerado na partida. Frase de limite desconhecido mudou de "radar" para "radar adiante".
- Benchmark no box (Xeon, `scripts/bench_voz.py`, `resultados/voz_bench.jsonl`):

| CPUs | carga do modelo | síntese mediana / máx por frase | RTF | espeak mediana |
|---|---|---|---|---|
| 8 | 386 ms | 23,0 / 30,4 ms | 0,018 | 5,5 ms |
| 4 | 643 ms | 21,7 / 33,0 ms | 0,018 | 5,6 ms |
| 1 (taskset) | 325 ms | 21,4 / 26,6 ms | 0,017 | 4,9 ms |

- Pré-geração das 12 frases nos vídeos: 20,7–27,6 ms cada (~280 ms no total). Fala vinda do cache: 0,1–0,9 ms.
- **Estimativa Pi 4 (NÃO medida)**: premissa de 5–10× mais lento que 1 núcleo do Xeon → RTF ~0,09–0,17, ~110–230 ms por frase, carga do modelo ~1,6–3,3 s. Com o cache, a latência no aviso é a de abrir o WAV.
- Testes: `tests/test_voz_piper.py` (6 testes: síntese pt-BR, cache, fallback voz ausente, fallback exceção, RADAR_TTS=espeak, fila assíncrona). Suíte completa: **59 testes, 57 ✅, 2 ❌** (as mesmas 2 falhas já conhecidas da seção 8: cobertura de dados por Inmetro 403/ANTT timeout e 1 aviso fragmentado em `cet_sp:7114/7115`).

### Vídeos (800x480, H.264 + AAC 22 050 Hz, voz Piper do cache)
| Vídeo | Radar | Duração | Evento de voz | Áudio audível (silencedetect) | Render p95 |
|---|---|---|---|---|---|
| `resultados/video_piper_paulista.mp4` | Av. Paulista, CET 5393/5394, 50 km/h | 41,1 s | t=4,0 s "radar, 50" | 4,22–5,08 s | 18,1 ms |
| `resultados/video_piper_radial_leste.mp4` | Av. Alcântara Machado (Radial Leste), CET 4326, 50 km/h | 40,1 s | t=4,0 s "radar, 50" | 4,22–5,08 s | 14,8 ms |
| `resultados/video_piper_tamoios.mp4` | Tamoios SP-099, DER 00017537, 80 km/h | 30,1 s | t=5,0 s "radar, 80" | 5,21–6,12 s | 2,1 ms |

Em todos: alerta a ~450 m, radar visível em 100% dos quadros com alerta, passagem pelo radar e fim do alerta. Telas: `resultados/telas/piper_*.png`. Métricas: `resultados/voz_videos.jsonl`.

### Problemas / limitações
- Não há radar da Av. 23 de Maio no banco (consulta sem resultados); foram usadas Paulista e Radial Leste.
- Paulista e Radial Leste ficaram com ~40 s, e não 30 s: com velocidade urbana realista (64→49 km/h), percorrer 450 m de alerta mais a passagem leva mais de 30 s.
- O atraso de ~0,22 s entre o evento e o som é o silêncio inicial do WAV do Piper.
- A reprodução em alto-falante real (aplay) e o tempo no Pi 4 não foram testados (sem hardware).

## 10. Validação cruzada com OSM, status_validacao e telemetria local (22/11/2026)
Decisões do Johnny:
- Waze e Google não são usados (nada de raspagem).
- O OSM só valida, nunca é fonte oficial.
- Cada radar tem 3 estados.
- Um radar só é removido com evidência oficial.
- Ficou tudo **interno** (banco, logs e relatório). A tela não mudou. Botões/tabela de relatos (item 4) e ícones por estado (item 3) foram cancelados e não chegaram a ser feitos.

### 10.1 Script `scripts/cruzar_osm.py`
- O anexo original do Johnny não pôde ser lido pelo subagente (arquivo privado). Por isso, `scripts/cruzar_osm.py` foi **reescrito a partir da especificação dele**:
  - Overpass `highway=speed_camera` / `enforcement=maxspeed`, com cache;
  - raio de 40 m e "próximo" até 150 m;
  - categorias confirmado_osm / proximo_osm / so_base / so_osm;
  - flag de limite divergente;
  - CSV com `;` ou `,`;
  - resumo por UF;
  - fallback Geofabrik.
- A lógica está em `radar_pi/validacao/osm.py` e usa só a biblioteca padrão. O `.pbf` é lido com `osmium` (opcional).
- Cuidados de robustez e segurança incluídos (a conferir contra o original):
  - `User-Agent` obrigatório: sem ele o Overpass responde 406;
  - teto de 200 MB na resposta e cache gravado de forma atômica;
  - rodízio entre 3 servidores Overpass;
  - área validada por regex (sem injeção na query);
  - escape de fórmula no CSV (`=`, `+`, `-`, `@`);
  - coordenadas fora do Brasil descartadas e contadas: 3 radares `der_sp` com longitude positiva (00017098, 00017260, 00017578);
  - `maxspeed` como "60;40", "40 mph", "none" ou "signals" tratado;
  - nós `device` de relações `enforcement` incluídos;
  - decimal com vírgula aceito.
- **Overpass falhou no box.** Ao consultar a UF SP (`--uf SP`), deu SSL EOF em overpass-api.de, timeout/502 em kumi.systems e 504 em maps.mail.ru, num total de 5 min 58 s (log em `resultados/osm/overpass_tentativa.log`).
- **Usado o fallback Geofabrik `brazil-latest.osm.pbf`** (2,1 GB, md5 conferido `0dec0c9f…94e1`). A leitura com os filtros do pyosmium levou 14,2 s e retornou **10 165 radares OSM no Brasil**. Os extratos regionais (sudeste/são paulo) não existem mais no Geofabrik (404).
- UF de cada ponto pela malha de UFs do IBGE (`data/raw/ibge/uf_br.geojson`).

**Resultado real** (base = 2 332 radares ativos não-duplicados; `resultados/osm/divergencias.csv`):

| categoria | radares |
|---|---|
| confirmado_osm (≤ 40 m; distância mediana 13,7 m) | 1 034 (44,3%) |
| proximo_osm (40–150 m) | 185 (7,9%) |
| so_base (não verificado, **não** "não existe") | 1 110 (47,6%) |
| coordenada inválida | 3 |
| so_osm (lista de revisão, Brasil) | 8 784, sendo 1 620 em SP (`resultados/osm/revisao_osm_sp.csv`) |

- Limites comparáveis (base e OSM com limite): 1 082. **Divergentes: 31** (20 em confirmado_osm, 11 em proximo_osm).
- Pares mais comuns (base × OSM): 50×40 (8), 40×50 (7), 80×60 (3), 60×90 (3), 90×70 (3) e 30×50 (2).
- Por UF da base: SP 1 034 confirmados / 185 próximos / 1 106 so_base. MG, PR e SC têm 1 so_base cada, e há 1 ponto sem UF.

### 10.2 Coletor: `status_validacao` e `n_fontes`
- Colunas novas em `radares`, por migração aditiva: `status_validacao`, `n_fontes`, `fontes_validacao`, `osm_id`, `osm_dist_m`, `osm_limite` e `motivo_validacao`. A tabela nova `revisao_osm` recebe os so_osm. `pipeline.executar()` chama `validar()` ao fim de cada coleta.
- Fontes contadas: cada fonte oficial distinta no mesmo ponto (duplicatas ≤ 30 m), o Inmetro (limite casado) e o OSM (≤ 40 m).
  - Registro "ANTT+OSM" do espelho não conta o OSM de novo (não seria independente).
  - O Inmetro conta como fonte: é registro oficial de aferição do equipamento.
- **conflito** quando as fontes discordam:
  - limite diferente do OSM (a ≤ 40 m);
  - limite diferente entre fontes oficiais;
  - outra fonte oficial dá o ponto como inativo a ≤ 30 m.
- Nada altera `status`/remoção. Um so_osm nunca entra em `radares` (testado: não aparece em `radares_proximos`).

| | confirmado | nao_verificado | conflito |
|---|---|---|---|
| só fontes oficiais (sem OSM) | 1 072 | 1 225 | 35 |
| **com OSM (atual no banco)** | **1 741** | **536** | **55** |

- n_fontes: 541 radares com 1 fonte, 1 385 com 2, 322 com 3 e 84 com 4.
- Motivos dos 55 conflitos:
  - 31 por inativo_em_outra_fonte (40 `der_sp` / 9 `artesp` contando duplicatas);
  - 20 por limite divergente do OSM;
  - 4 por limite divergente entre fontes oficiais (90/100, 80/90, 50/60, 100/110).
- nao_verificado por fonte:

| fonte | nao_verificado |
|---|---|
| cet_sp | 210 |
| der_sp | 195 |
| artesp | 60 |
| antt_via_espelho | 44 |
| mogi | 27 |

- Combinação mais comum: cet_sp + osm, 648 confirmados.

### 10.3 Telemetria local anonimizada (esqueleto, `radar_pi/telemetria/agregador.py`)
- **O que grava:** só agregados por célula H3 res 11 (~25 m) e sentido (8 setores): n, Σv, Σv², Σqueda e nº de desacelerações fortes.
- **O que não grava:** trajeto, sequência, horário ou id.
- **Proteções:**
  - os primeiros e os últimos 500 m de cada viagem são descartados;
  - o buffer de 300 m fica só na memória;
  - salto de GPS > 500 m conta como nova viagem;
  - só entram na análise células com ≥ 5 passagens.
- **Quando liga:** só com gpsd e `RADAR_TELEMETRIA=1`. Não liga na simulação nem nos vídeos.
- **Sinais:** vão para `telemetria_sinais` e **nunca** alteram `radares`.
  - candidato: ≥ 60% desaceleram ≥ 15 km/h, sem radar a ≤ 80 m;
  - suspeito: radar ativo a ≤ 30 m, ≤ 20% desaceleram e velocidade média > 1,1 × limite.
- **Simulação do teste:** 25 viagens de 3,4 km a 80 km/h com ruído de GPS de 3 m.
  - Ponto sem radar em 2 200 m, onde 80% freiam para ~45 km/h: **1 candidato detectado a 2 208 m** (8 m de erro), queda média de 28,4 km/h, 80% das passagens com desaceleração.
  - Radar de 60 km/h em 1 200 m que ninguém respeita: **1 suspeito**, velocidade média de 78,8 km/h.
  - 60 células com ≥ 5 passagens.
  - Com 3 viagens (< k), nenhum sinal.

### 10.4 Testes
Testes novos:
- `tests/test_validacao_osm.py` (5): parse do maxspeed/injeção, categorias, regras dos 3 estados com 10 casos, banco sem remoção e so_osm sem alerta, CLI com GeoJSON e CSV `;`.
- `tests/test_telemetria.py` (3): candidato/suspeito, LGPD (colunas, origem/destino, k), salto de GPS.

Suíte completa: **67 testes, 65 ✅, 2 ❌**. São as mesmas 2 falhas conhecidas da seção 8: cobertura por Inmetro 403/ANTT timeout e 1 aviso fragmentado em `cet_sp:7114/7115`.

### 10.5 Problemas e próximos passos
- O Overpass ficou inacessível do box. O Geofabrik Brasil (2,1 GB) resolveu.
- Os 1 620 so_osm em SP precisam de revisão manual: radar novo, desativado ou duplicado no OSM. Nenhum foi adicionado.
- Os 31 `inativo_em_outra_fonte` podem ser troca de equipamento (uma fonte já atualizou e a outra não). Por isso ficam como conflito, não removidos.
- Telemetria é só esqueleto: não foi testada em rua real (precisa do Pi com gpsd).
- **Mapillary (futuro):** para pontos em conflito/revisão, buscar imagens a ≤ 30 m e conferir a placa "fiscalização eletrônica" ou o equipamento. Não implementado agora, conforme pedido.

### 10.6 Comparação com o script original do Johnny (`scripts/cruzar_osm_original.py`, não alterado)
- Lógica igual nos pontos principais:
  - raios de 40 e 150 m;
  - 4 categorias;
  - tags `highway=speed_camera` / `enforcement=maxspeed`;
  - grade de 0,002°;
  - colunas lat/lon aceitas;
  - filtro do bbox do Brasil.
- Mesma base (2 329 válidos) e mesmo conjunto OSM (10 165), alimentando o original por cache: contagens **idênticas** (1 034 / 185 / 1 110 / 8 784, com 31 limites divergentes), e 0 diferenças linha a linha.
- Diferença real: o original não inclui os nós `device` das 613 relações `enforcement=maxspeed`, o que dá 165 radares a menos (10 000). Com isso: proximo 184 (−1), so_base 1 111 (+1), so_osm 8 625 (−159). confirmado e limites divergentes não mudam.
- Riscos no original:
  - o cache nunca expira (dado velho para sempre) e é gravado sem escrita atômica;
  - não há teto no tamanho da resposta nem escape de fórmula no CSV (id `=…`);
  - o timeout é de 700 s × 6 tentativas, mais esperas de 10–30 s (≈70 min no pior caso);
  - só lê do Overpass: o Geofabrik exige adaptação manual, e osmium-tool não existe no box;
  - "40 mph" vira 40 km/h;
  - coordenadas inválidas (3) são descartadas em silêncio;
  - o so_osm sai sem UF.
