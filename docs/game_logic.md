# Logica de jogo

Este documento descreve a primeira versao da camada de leitura de jogo do MahjongMaster.
Ela ainda nao calcula pontos nem confirma uma mao vencedora. O objetivo atual e transformar as
deteccoes do YOLO em um estado util: pecas da minha mao, chamadas abertas e yakus provaveis.

## Visao geral

O fluxo atual e:

1. A janela principal captura a tela do Mahjong Soul.
2. O modelo YOLO detecta pecas visiveis.
3. `game_analyzer.py` converte as caixas detectadas em uma estimativa da mao do jogador.
4. `mahjong_logic.py` converte nomes de classes em pecas, grupos e yakus provaveis.
5. A UI mostra um resumo no terminal/preview e, se `Auto` estiver ligado, decide chamadas e descartes.

As regioes de tela usadas pela leitura ficam em `configs.json`, em coordenadas da captura normalizada `1592x933`. A janela principal tem o botao `Areas`, que abre um editor visual: arraste a area para mover e puxe o canto inferior direito para redimensionar. Os campos numericos continuam disponiveis para ajuste fino. O checkbox `Areas` liga/desliga o overlay desenhado na preview.

As regioes `Oponente esquerda/frente/direita` representam mao aberta/chamadas laterais dos oponentes. Os descartes ficam em regioes separadas: `Descartes jogador`, `Descartes esquerda`, `Descartes frente` e `Descartes direita`. Somente essas regioes de descarte alimentam a lista de pecas ja vistas para reduzir probabilidades de yakus dependentes de pecas que sairam.

Nas regioes de mao/chamadas dos oponentes, um kan fechado exibido como `tile_back + peca + peca + tile_back` e contabilizado como quatro copias daquela peca. As duas pecas viradas sao inferidas como iguais as duas expostas, entao deixam de aparecer como disponiveis para completar yakus/esperas.

Exemplo de saida:

```text
[MAO] Fechada:
  1-man | 2-man | 3-sou | 4-pin | 5-pin | 6-pin
[MAO] Abertas:
  CHI 1-pin,2-pin,3-pin | KAN 4-sou 4x
[YAKU] Provaveis/ativos:
  All Simples 76% | Half Flush 61%
[DESCARTE] Melhor descarte agora:
  9-man | east-wind
  Motivo: menor contribuicao estimada entre as pecas conhecidas
[FALTAM] Pecas que mais ajudam:
  3-pin 52% (All Simples) | 6-pin 45% (Pure Straight)
[INFO] Pecas conhecidas: 13/13
```

Quando faltam pecas detectadas, a UI preenche com `???` e avisa que a quantidade ainda nao e suficiente para uma decisao confiavel:

```text
[MAO] Fechada:
  6-man | 9-man | 1-pin | ... | ???
[INFO] Pecas conhecidas: 12/13; analise incompleta: faltam 1 peca(s), decisao ainda pode oscilar.
```

A UI tambem mostra os descartes visiveis no centro da mesa quando eles sao detectados. Eles agora sao separados por posicao estimada na mesa:

```text
[DESCARTES VISTOS]
Principal: 8-sou | west-wind
Esquerda: 5-man | 2-pin
Cima: green-dragon
Direita: red-dragon | 1-pin
```

Os logs tecnicos antigos continuam existindo, mas so aparecem quando o checkbox `DEBUG` esta ligado. No menu `Opcoes`, o botao `Deletar logs` apaga os logs de autoplay em `logs/autoplay`.

## Versionamento da logica

Cada JSONL de autoplay grava `logic_version`. Quando uma regra de chamada,
descarte, defesa ou priorizacao de estrategia mudar, incremente
`BOT_LOGIC_VERSION` em `mahjong_master/mahjong_logic.py`. O evento
`session_start` tambem grava `logic_notes`, para comparar partidas de versoes
diferentes e medir se a estrategia melhorou.

## Maquina de estados do bot

O MahjongMaster roda como uma maquina de estados simples em cima dos frames capturados. O estado nao e salvo como uma enum unica no codigo, mas o comportamento efetivo e este:

```text
MONITORANDO
  |
  | Predict le mesa, botoes, turno e pixels
  v
ANALISANDO_MAO
  |
  | Se houver botao Chii/Pon/Kan/Riichi
  v
DECIDINDO_CHAMADA
  |
  | SIM: agenda clique no botao
  | NAO: agenda Skip quando aplicavel
  v
AGUARDANDO_ESTABILIZAR
  |
  | Se for minha vez por 3 frames
  v
DECIDINDO_DESCARTE
  |
  | Move mouse para a pior peca atual
  v
REVALIDANDO_DESCARTE
  |
  | Se a peca ainda e a pior, clica
  | Se mudou, volta a DECIDINDO_DESCARTE
  v
MONITORANDO
```

Se `Ron` ou `Tsumo` for detectado pelos pixels configurados, o AutoPlay interrompe qualquer decisao pendente e agenda esse clique antes de qualquer chamada, descarte, defesa ou rotina de estabilizacao.

Estados auxiliares:

- `ROTINA_FINAL`: so executa quando `Auto` e `Rot` estao ligados. Cada etapa espera seu pixel de trigger e clica no pixel configurado. Se a etapa atual ainda nao apareceu, ela fica aguardando; nao volta para o inicio. O preview desenha os probes de rotina com `SIM`/`NAO`, incluindo o `Confirm`.
- `CAPTURA_DATASET`: so executa quando `Capt` esta ligado e ha predict com deteccoes. Nao usa mais intervalo fixo de tempo.
- `DEFESA_PONDERADA`: nao e um modo binario permanente. A escolha de descarte sempre pondera beneficio do plano, han esperado e risco; quando adversarios entram em Riichi, o peso de risco cresce bastante.

## Captura automatica de dataset

O modo `Capt` agora usa dois thresholds configuraveis em `Opcoes`:

- `Capt 1a foto`: padrao `35` deteccoes.
- `Capt 2a foto`: padrao `75` deteccoes.

A maquina de estados da captura e:

```text
AGUARDANDO_1A_FOTO
  |
  | deteccoes >= Capt 1a foto
  v
SALVA_1A_FOTO
  |
  v
AGUARDANDO_2A_FOTO
  |
  | deteccoes >= Capt 2a foto
  v
SALVA_2A_FOTO
  |
  v
AGUARDANDO_RESET
  |
  | deteccoes < Capt 1a foto
  v
AGUARDANDO_1A_FOTO
```

Na pratica, cada partida gera uma foto quando a mesa ja tem leitura suficiente. Se a partida se prolongar e passar do segundo limiar, gera uma segunda foto. Depois disso o capturador so rearma quando a contagem cai abaixo do primeiro limiar, o que normalmente acontece ao trocar de partida/tela.

## Modulos

### `mahjong_logic.py`

Contem as estruturas puras de dominio:

- `Tile`: representa uma peca. Exemplos: `man_1`, `sou_5_red`, `wind_east`.
- `Meld`: representa uma chamada/grupo aberto: `CHI`, `PON`, `KAN` ou `CALL`.
- `HandState`: representa a leitura atual da mao: pecas fechadas, melds abertos e yakus provaveis.
- `Yaku`: cadastro de yaku/bonus/situacionais.
- `YAKU_REGISTRY`: lista central de yakus conhecidos.

Tambem contem funcoes auxiliares:

- `tile_from_name(name)`: transforma uma classe YOLO em `Tile`; classes visuais como `tile_back` retornam `None` e nao entram na mao.
- `sort_tiles(tiles)`: ordena pecas por naipe e numero.
- `classify_meld(tiles)`: tenta classificar um grupo como `CHI`, `PON` ou `KAN`.
- `likely_yaku(state)`: roda os matchers disponiveis sobre o estado da mao.

Quando nenhum yaku esta confirmado, a camada gera uma estimativa de compatibilidade em porcentagem para planos simples, como `All Simples`, `Half Outside Hand`, `Half Flush`, `Full Flush`, `Pure Straight`, `Seven Pairs`, `Riichi` e yakuhai de dragoes. Essa porcentagem e um score heuristico, nao uma probabilidade matematica real. Planos especulativos agora tambem sao limitados pelo shanten/formacao real da mao, para evitar mostrar um yaku como 100% apenas porque as pecas combinam com ele enquanto a mao ainda esta longe de fechar.

`Riichi` entra como plano valido quando a mao esta fechada. Ele fica mais atrativo quando a mao esta em tenpai ou perto disso e quando ha aka dora/doras, porque esses bonus so precisam de um yaku para pontuar.

Quando nenhum plano unico e claramente superior, a estrategia passa a criar um objetivo de `Mao flexivel`: ela combina progresso real de shanten/ukeire, multiplos yakus possiveis, Riichi, doras e valor baixo aceitavel. Nesse modo, yakus como `Half Outside Hand`, `Fully Outside Hand`, `Half Flush` e `Full Flush` deixam de dominar so por terem muitas pecas compativeis; se a formacao real estiver longe, eles entram apenas como suporte fraco para nao prender a mao em um plano dificil.

A eficiencia da mao tambem calcula qualidade de blocos com `hand_shape_profile`: grupos completos, pares, ryanmen, ryankan, kanchan, penchan, pares demais e pecas isoladas. Essa nota entra na escolha de descarte, nas penalidades de planos especulativos e no log de autoplay. Assim, uma mao com bons ryanmen tende a ser preservada, enquanto penchan/kanchan isolados e honras soltas saem antes mesmo quando um yaku amplo parece tentador.

Quando um descarte deixaria a mao em tenpai, a logica tambem calcula as esperas resultantes. Se alguma espera ja aparece nos descartes do proprio jogador, o descarte recebe penalidade de furiten e tende a ser evitado, salvo quando ele ainda for claramente o melhor avanco de shanten.

O OCR de pontuacao continua rodando em segundo plano, mas a area e o valor so aparecem na preview/terminal quando o checkbox `Areas` esta ligado. Para reduzir custo, ele nao roda a cada frame: a leitura e tentada no maximo tres vezes por mao, apenas quando a mao esta incompleta (`???`), com intervalo minimo entre tentativas.

### `game_analyzer.py`

Contem a camada que interpreta posicao de tela:

- `TileDetection`: uma caixa detectada pelo YOLO, com nome, confianca e coordenadas.
- `detections_from_yolo_result(result)`: converte o resultado do Ultralytics para `TileDetection`.
- `GameAnalyzer`: transforma deteccoes em `HandState`.

Esta camada e intencionalmente separada das regras de Mahjong. Assim podemos trocar heuristicas de tela sem mexer nas regras de yaku.

## Como a mao e identificada hoje

A primeira heuristica e conservadora e focada apenas no jogador local.

1. Filtra pecas na parte inferior da imagem.

   A captura padrao e normalizada para `1592x933`. O analisador usa a regiao configuravel `player_hand` do `configs.json`. Se essa regiao estiver ausente/desativada, volta para a heuristica antiga de considerar pecas abaixo de cerca de `64%` da altura da imagem.

2. Procura a linha inferior mais densa.

   As pecas candidatas sao agrupadas por altura. A linha com mais pecas e mais baixa tende a ser a mao do jogador.

3. Agrupa horizontalmente.

   Dentro dessa linha, as pecas sao ordenadas da esquerda para a direita. Gaps grandes separam grupos.

4. Escolhe a mao fechada.

   A leitura tenta montar uma mao plausivel antes de separar chamadas. Em Mahjong, o conjunto visivel do jogador deve ter pelo menos 13 pecas conhecidas. Quando ha `KAN`, esse minimo sobe em +1 para cada kan, porque a quadra mostra quatro pecas.

5. Grupos separados viram chamadas abertas.

   A leitura tambem observa tamanho e posicao. Pecas em grupos plausiveis de 3/4 que aparecem separadas, mais altas, menores ou deslocadas para a direita da linha principal entram como chamadas abertas. Essas pecas entram em `open_melds`, nao em `hand_tiles`, portanto nunca podem aparecer como melhor descarte.

   Apenas grupos separados, com 3 ou 4 pecas, viram chamadas abertas se a separacao ainda deixar uma quantidade plausivel de pecas na mao fechada:

   - 3 iguais: `PON`
   - 4 iguais: `KAN`
   - 3 consecutivas do mesmo naipe: `CHI`
   - caso contrario: `CALL`

   A contagem minima estimada e:

   ```text
   total visivel minimo = 13 + quantidade de KAN
   mao fechada minima = 13 - 3 * quantidade de chamadas abertas
   ```

   Isso evita tratar um fragmento pequeno da linha inferior como se fosse a mao inteira.

6. A mao e exibida mantendo a ordem visual.

   A mao fechada usa ordem horizontal real da tela. As chamadas sao ordenadas por posicao horizontal.

7. Mapeia descartes no centro da mesa.

   O analisador tambem olha a regiao central da mesa para montar `discarded_tiles` e `discarded_by_player`. A separacao e feita por quadrantes/posicao relativa ao centro da mesa:

   - `principal`: descartes na parte de baixo da mesa.
   - `esquerda`: descartes do jogador a esquerda.
   - `cima`: descartes do jogador de cima.
   - `direita`: descartes do jogador a direita.

   Essa lista ainda e uma heuristica visual, mas ja ajuda a baixar ou eliminar planos que dependem de pecas que claramente ja sairam.

8. Escolhe os planos/yakus mais provaveis.

   A logica agora considera que varios yakus podem ser perseguidos ao mesmo tempo. Quando nao ha yaku confirmado por decomposicao, ela monta candidatos como `All Simples`, ventos, dragoes, flush, straight e pares. Normalmente os tres melhores aparecem na UI com score de compatibilidade.

   Se algum yaku ja estiver em `100%`, ele continua aparecendo, mas a UI tambem mostra planos extras para orientar como fechar a mao. Exemplo: com um yaku completo, aparecem esse yaku e mais tres planos; com tres yakus completos, aparecem esses tres e mais tres planos seguintes.

9. Zera yakus impossiveis.

   Antes de um yaku entrar no Top 3, ele passa por uma camada de bloqueio. Se uma chamada aberta torna aquele yaku impossivel, o score vira `0%`, ele aparece em `[YAKU] Bloqueados` e deixa de influenciar descarte ou pecas faltantes.

   Exemplos:

   - Mao aberta bloqueia yakus `Menzenchin Only`, como `Riichi`, `Pinfu`, `Seven Pairs`, `Nine Gates` e similares.
   - Uma chamada aberta com terminal ou honra bloqueia `All Simples`.
   - Uma chamada aberta `456` bloqueia `Half Outside Hand`, `Fully Outside Hand`, `All Triplets`, `All Terminals and Honors` e outros yakus incompativeis.
   - Chamadas abertas misturando naipes bloqueiam `Half Flush` e `Full Flush`.
   - Quando uma mao ja abriu uma sequencia/trinca de um naipe, planos de flush so podem mirar esse mesmo naipe. Ex.: abrir `2-pin 3-pin 4-pin` impede tentar `Full Flush` de man, mesmo que ainda existam muitas pecas man na mao fechada.
   - Chamadas abertas com pecas nao-verdes bloqueiam `All Green`.

   Pecas soltas na mao fechada nao bloqueiam automaticamente um yaku, porque ainda podem ser descartadas. O bloqueio forte e aplicado principalmente ao que ja ficou permanente: chamadas abertas.

10. Descarta candidatos ruins considerando os planos ativos.

   Quando o yaku mais provavel e estimado, a logica calcula quais pecas conhecidas nao contribuem para nenhum dos planos ativos. A UI pinta a pior peca em vermelho e outras pecas ruins em amarelo. Assim o descarte mais recomendado fica evidente, sem esconder alternativas ruins.

   Quando um yaku ja esta confirmado, a UI ainda calcula eficiencia para fechar a mao. Nesse caso, os red-labels marcam pecas isoladas/fracas que atrapalham completar quatro grupos e um par, sem remover o yaku ja formado. Por exemplo, um trio de `White Dragon` continua sendo yaku confirmado, mas uma honra solta ou um numero isolado pode ser marcado como descarte ruim.

   Exemplo: se a mao tem dois `east-wind` e nenhum `east-wind` foi descartado, o plano de `Prevalent Wind` continua vivo porque `East` e tratado como vento fixo da mesa. Para os ventos dos jogadores, a UI tenta detectar o marcador vermelho de East nas regioes centrais `wind_letter_player`, `wind_letter_left`, `wind_letter_top` e `wind_letter_right`; depois infere os demais pela ordem de turno `esquerda -> principal -> direita -> cima`.

11. Estima pecas que mais ajudam.

   Cada plano provavel tambem informa quais pecas ainda poderiam melhorar aquele plano. A UI combina os tres melhores planos, reduz o peso de pecas que ja apareceram nos descartes e mostra uma lista em `[FALTAM]`.

12. Detecta turno, leste e botoes por pixel configuravel.

   Chii, Pon, Kan, Riichi, Skip, setas de turno e marcador de leste nao sao mais areas retangulares. No editor `Areas`, a secao `Pixel` permite escolher um seletor, clicar na imagem congelada e salvar a posicao, a cor exata e uma tolerancia. Em runtime, se aquele pixel estiver com cor parecida, a variavel correspondente fica ativa. A deteccao propria de Ron/Tsumo por botao foi removida; o placar passa a ser lido por OCR nas quatro areas de pontuacao.

   Para botoes existem dois seletores por acao, porque o Mahjong Soul pode deslocar os botoes quando aparecem varias opcoes. O ponto ativo tambem e usado como destino de clique quando a decisao for `SIM`. Quando o botao aparece, o preview marca o ponto e o resumo mostra:

   ```text
   [CHAMADAS]
     Chii: botao visivel; descarte esquerda 2-sou; opcoes 2-sou com 3-sou+4-sou => 2-3-4-sou
   ```

   A regra considera apenas o descarte estimado do jogador a esquerda, porque no Mahjong japones/Riichi so e possivel chamar `Chii` do jogador anterior.

   Para descobrir de quem veio a peca, a UI consulta os seletores `turn_left`, `turn_top`, `turn_right` e `turn_player`. Com isso o resumo pode mostrar:

   ```text
   [CHAMADAS]
     Pon: botao visivel; origem esquerda; peca green-dragon; opcoes green-dragon de esquerda com green-dragon+green-dragon
   ```

   Se a seta amarela nao for detectada com confianca, a logica nao tenta adivinhar a peca da chamada. Quando ha seta, apenas a area de descarte daquele jogador e considerada. A peca pegavel e escolhida assim:

   - esquerda: peca mais baixa da fileira/coluna mais a esquerda;
   - direita: peca mais alta da fileira/coluna mais a direita;
   - frente: peca mais a esquerda da fileira mais alta;
   - jogador: peca mais a direita da fileira mais baixa.

   A UI tambem simula `Chii`, `Pon` e `Kan` quando aparecem opcoes validas. A decisao compara os planos/yakus antes e depois da chamada e marca `SIM` quando a chamada mantem ou melhora os planos ativos; marca `NAO` quando abrir a mao derruba yakus importantes ou reduz demais a compatibilidade.

13. Sempre sugere um descarte.

   Mesmo quando todas as pecas contribuem para algum plano, a UI calcula uma sugestao de descarte por eficiencia. A ordem de prioridade e:

   - primeiro, pecas que nao ajudam os tres melhores yakus;
   - depois, pecas isoladas ou pouco conectadas;
   - por fim, a peca de menor contribuicao estimada entre as detectadas.

   A ordenacao tambem roda uma simulacao leve de futuro: remove cada candidata, recalcula os principais planos de yaku e penaliza descartes que deixam a mao com planos melhores. Dora real e red five sao protegidos quando existe uma alternativa nao-dora.

14. Calcula dora a partir do indicador.

   A regiao `dora_indicators` detecta a peca indicadora, mas a UI mostra a dora real em `[INFO]`. A regra segue o Riichi:

   - `1` a `8` indicam o numero seguinte; `9` volta para `1`.
   - `east -> south -> west -> north -> east`.
   - `white -> green -> red -> white`.

   Exemplo: se o indicador e `3-sou`, a dora mostrada e `4-sou`.

## Formato das pecas

Internamente, as classes YOLO usam nomes como:

```text
man_1
pin_9
sou_5_red
wind_east
dragon_green
tile_back
```

Na UI, elas aparecem de forma compacta:

```text
1-man
9-pin
5-sour
east-wind
green-dragon
```

O `r` no final indica cinco vermelho.

## Yakus cadastrados

O registry ja inclui yakus, yakumans, doras e situacionais para expansao futura. Nem todos possuem regra ativa ainda.

Regra ativa nesta primeira versao:

- `All Simples`: todas as pecas conhecidas sao simples, de 2 a 8, sem honras.
- `Pinfu`: exige decomposicao fechada com quatro sequencias e par sem honra.
- `Pure Double Sequence`: exige decomposicao fechada com duas sequencias identicas.
- `Twice Pure Double Sequence`: exige duas duplas de sequencias identicas.
- `Seat Wind`: aproximacao por trio de vento enquanto o vento do jogador ainda nao e lido.
- `Prevalent Wind`: trio de `east-wind`; nesta versao `East` e considerado o vento fixo da mesa.
- `Dragons`, `White Dragon`, `Green Dragon`, `Red Dragon`: trio de dragoes.
- `After a Kan` / situacionais similares: cadastrados, mas dependem de eventos que ainda nao monitoramos.
- `All Triplets`: exige decomposicao em quatro trios/quads e um par.
- `Three Quads`: detecta tres quads visiveis/conhecidas.
- `Triple Triplets`: detecta trios do mesmo numero nos tres naipes.
- `Mixed Triple Sequence`: exige a mesma sequencia nos tres naipes.
- `Pure Straight`: exige 123, 456 e 789 no mesmo naipe.
- `Half Outside Hand`: exige decomposicao em que todos os grupos e o par contem terminal ou honra, com pelo menos uma sequencia e alguma honra.
- `Fully Outside Hand`: exige decomposicao em que todos os grupos e o par contem 1 ou 9, sem honras.
- `All Terminals and Honors`: todas as pecas conhecidas sao terminais ou honras.
- `Seven Pairs`: exige sete pares diferentes em mao fechada.
- `Three Concealed Triplets`: aproximacao por tres trios em mao fechada.
- `Little Three Dragons`: dois trios de dragoes e par do terceiro dragao.
- `Red Five`: detecta cinco vermelho.

Yakus cadastrados sem matcher completo ainda:

- `Riichi`
- `Double Riichi`
- `Fully Concealed Hand`
- `Ippatsu`
- `Robbing a Kan`
- `Under the Sea`
- `Under the River`
- `Mangan at Draw`
- `Half Flush`
- `Full Flush`
- `Dora`
- `Kita`
- `Local Yaku: Tsubame-gaeshi`
- yakumans como `Thirteen Orphans`, `Four Concealed Triplets`, `Big Three Dragons`, `Four Little Winds`, `Four Big Winds`, `All Honors`, `All Terminals`, `All Green`, `Nine Gates`, `Four Quads`, `Blessing of Heaven`, `Blessing of Earth` e variantes.

## Limitacoes atuais

Esta etapa e so a base. Ainda ha limitacoes importantes:

- Nao sabemos ainda qual e o vento da rodada nem o vento do jogador.
- Nao distinguimos eventos como riichi, tsumo, ron, ippatsu, rinshan, haitei ou houtei.
- Descartes ja sao lidos por regiao visual, mas ainda nao ha garantia absoluta da ordem real em todos os layouts.
- Ainda nao lemos dora indicators, wall, turno ou botoes de chamada alem do Chii.
- Os botoes Chii e Pon ja tem deteccao por cor, mas Kan/Ron/Riichi ainda nao.
- A ordem exata de "ultimo descarte" por jogador ainda e aproximada por posicao visual.
- As regioes de oponentes, dora, Kan e Riichi ja aparecem no overlay e sao configuraveis, mas ainda serao conectadas a regras especificas nas proximas etapas.
- As chamadas abertas sao inferidas por espaco horizontal, nao por uma leitura semantica da UI.
- Se o YOLO errar classe/caixa, a logica de jogo herda esse erro.
- Maos laterais, topo e centro sao ignorados nesta primeira versao.
- Yaku provavel nao significa yaku confirmado. E uma hipotese baseada nas pecas visiveis.

## Proximos passos

Prioridades naturais:

1. Calibrar regioes da tela por layout do Mahjong Soul.
2. Separar explicitamente: minha mao, chamadas minhas, descartes meus, descartes dos oponentes, dora, winds e turno.
3. Ler vento da rodada e vento do jogador.
4. Detectar estado fechado/aberto com mais confianca.
5. Implementar um decompositor de mao para achar 4 grupos + par, sete pares e treze orfaos.
6. Implementar matchers completos de yaku usando a decomposicao.
7. Adicionar uma tela de debug visual mostrando quais deteccoes entraram na mao e quais foram ignoradas.
