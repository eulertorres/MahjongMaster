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
5. A UI mostra um resumo abaixo da preview. Esse painel ocupa uma parte grande da janela para caber a analise.

Exemplo de saida:

```text
Hand: 1-man | 2-man | 3-sou | 4-pin | 5-pin | 6-pin | CHI 1-pin,2-pin,3-pin | KAN 4-sou 4x | Yaku provavel: All Simples
```

Quando faltam pecas detectadas, a UI preenche com `???` e avisa que a quantidade ainda nao e suficiente para uma decisao confiavel:

```text
Hand: 6-man | 9-man | 1-pin | ... | ???
Yaku provavel: All Simples 58%
Analise incompleta: faltam 1 peca(s); numero de pecas insuficiente para decisao confiavel.
Descartes que nao contribuem: 9-man | 1-pin | 1-sou
```

A UI tambem mostra os descartes visiveis no centro da mesa quando eles sao detectados:

```text
Descartes vistos: east-wind | 7-pin | red-dragon
```

Os logs tecnicos antigos continuam existindo, mas so aparecem quando o checkbox `DEBUG` esta ligado.

## Modulos

### `mahjong_logic.py`

Contem as estruturas puras de dominio:

- `Tile`: representa uma peca. Exemplos: `man_1`, `sou_5_red`, `wind_east`.
- `Meld`: representa uma chamada/grupo aberto: `CHI`, `PON`, `KAN` ou `CALL`.
- `HandState`: representa a leitura atual da mao: pecas fechadas, melds abertos e yakus provaveis.
- `Yaku`: cadastro de yaku/bonus/situacionais.
- `YAKU_REGISTRY`: lista central de yakus conhecidos.

Tambem contem funcoes auxiliares:

- `tile_from_name(name)`: transforma uma classe YOLO em `Tile`.
- `sort_tiles(tiles)`: ordena pecas por naipe e numero.
- `classify_meld(tiles)`: tenta classificar um grupo como `CHI`, `PON` ou `KAN`.
- `likely_yaku(state)`: roda os matchers disponiveis sobre o estado da mao.

Quando nenhum yaku esta confirmado, a camada gera uma estimativa de compatibilidade em porcentagem para planos simples, como `All Simples`, `Half Outside Hand`, `Half Flush`, `Full Flush`, `Pure Straight`, `Seven Pairs` e yakuhai de dragoes. Essa porcentagem e um score heuristico, nao uma probabilidade matematica real.

### `game_analyzer.py`

Contem a camada que interpreta posicao de tela:

- `TileDetection`: uma caixa detectada pelo YOLO, com nome, confianca e coordenadas.
- `detections_from_yolo_result(result)`: converte o resultado do Ultralytics para `TileDetection`.
- `GameAnalyzer`: transforma deteccoes em `HandState`.

Esta camada e intencionalmente separada das regras de Mahjong. Assim podemos trocar heuristicas de tela sem mexer nas regras de yaku.

## Como a mao e identificada hoje

A primeira heuristica e conservadora e focada apenas no jogador local.

1. Filtra pecas na parte inferior da imagem.

   A captura padrao e normalizada para `1592x933`. O analisador considera como candidatas as pecas cujo centro esta abaixo de cerca de `64%` da altura da imagem.

2. Procura a linha inferior mais densa.

   As pecas candidatas sao agrupadas por altura. A linha com mais pecas e mais baixa tende a ser a mao do jogador.

3. Agrupa horizontalmente.

   Dentro dessa linha, as pecas sao ordenadas da esquerda para a direita. Gaps grandes separam grupos.

4. Escolhe a mao fechada.

   A leitura tenta montar uma mao plausivel antes de separar chamadas. Em Mahjong, o conjunto visivel do jogador deve ter pelo menos 13 pecas conhecidas. Quando ha `KAN`, esse minimo sobe em +1 para cada kan, porque a quadra mostra quatro pecas.

5. Grupos separados viram chamadas abertas.

   Apenas grupos separados no lado direito, com 3 ou 4 pecas, viram chamadas abertas se a separacao ainda deixar uma quantidade plausivel de pecas na mao fechada:

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

   O analisador tambem olha a regiao central da mesa para montar uma lista simples de `discarded_tiles`. Essa lista ainda e uma heuristica visual, mas ja ajuda a baixar ou eliminar planos que dependem de pecas que claramente ja sairam.

8. Escolhe os tres planos/yakus mais provaveis.

   A logica agora considera que varios yakus podem ser perseguidos ao mesmo tempo. Quando nao ha yaku confirmado por decomposicao, ela monta candidatos como `All Simples`, ventos, dragoes, flush, straight e pares. Os tres melhores aparecem na UI com score de compatibilidade.

9. Descarta candidatos ruins considerando os tres planos.

   Quando o yaku mais provavel e estimado, a logica calcula quais pecas conhecidas nao contribuem para nenhum dos tres melhores planos. A UI pinta essas pecas em vermelho translucido no overlay.

   Quando um yaku ja esta confirmado, a UI ainda calcula eficiencia para fechar a mao. Nesse caso, os red-labels marcam pecas isoladas/fracas que atrapalham completar quatro grupos e um par, sem remover o yaku ja formado. Por exemplo, um trio de `White Dragon` continua sendo yaku confirmado, mas uma honra solta ou um numero isolado pode ser marcado como descarte ruim.

   Exemplo: se a mao tem dois `east-wind` e nenhum `east-wind` foi descartado, os planos de `Seat Wind` e `Prevalent Wind` continuam vivos e esses dois ventos nao sao marcados em vermelho. Se varios `east-wind` ja apareceram nos descartes, esses planos perdem confianca ou deixam de ser considerados.

## Formato das pecas

Internamente, as classes YOLO usam nomes como:

```text
man_1
pin_9
sou_5_red
wind_east
dragon_green
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
- `Seat Wind` / `Prevalent Wind`: aproximacao por trio de qualquer vento. Ainda nao diferencia vento do jogador e vento da rodada.
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
- Nao lemos descartes, dora indicators, wall, turno ou botao de chamada.
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
