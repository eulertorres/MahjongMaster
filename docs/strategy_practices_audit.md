# Auditoria de boas praticas de Riichi/Mahjong Soul

Fontes consultadas:

- Riichi Book 1, Daina Chiba: https://repo.riichi.moe/books/rb1/index.html
- Riichi Book 1, capitulo 3, tile efficiency: https://repo.riichi.moe/books/rb1/ch3.html
- Push fold chart: https://repo.riichi.moe/guides/EV-Push-Fold.html
- Defense, Japanese Mahjong Wiki: https://riichi.wiki/Defense
- Dora strategy, Japanese Mahjong Wiki: https://riichi.wiki/Dora_strategy
- AMA com Sora Niihara, jogador profissional japones: https://www.reddit.com/r/Mahjong/comments/1rxhto1/ama_i_am_a_japanese_professional_riichi_mahjong/

## Lista critica

| Pratica | Fonte | O bot aplica hoje? | Avaliacao critica |
| --- | --- | --- | --- |
| Priorizar reduzir shanten sem destruir ukeire. | Riichi Book 1 explica shanten/ukeire e que 1-shanten ruim pode ser pior que manter aceitacao ampla. | Parcial. Usa `standard_shanten_number`, `best_shanten_number` e `improving_tiles_for_completion`; `blended_discard_candidates` pesa shanten e ukeire. | Bom inicio, mas ainda e heuristico. Nao compara qualidade de espera final com precisao suficiente: ryanmen vs kanchan/penchan aparece indiretamente por conexoes, nao como avaliacao estrutural forte. |
| Valorizar formas boas: ryanmen > kanchan > penchan; 3-7 mais versateis que 2/8, depois 1/9, depois honras. | Riichi Book 1, cap. 3. | Implementado. `hand_shape_profile` avalia blocos, grupos completos, ryanmen, ryankan, kanchan, penchan, pares, pares demais e isoladas. A pontuacao entra na escolha de descarte, no objetivo flexivel, nas explicacoes e nos logs. | Riscado como pratica base. Ainda pode evoluir para comparar waits finais e EV por tipo de espera, mas o bot agora para de tratar qualquer conexao como igual. |
| Evitar perseguir yaku dificil quando mao eficiente/riichi barato e suficiente. | Riichi Book 1 e AMA: velocidade/riichi geralmente tem EV alto; no East-only velocidade pesa mais. | Melhorou. `flexible_hand_objective` evita prender em um yaku unico e inclui Riichi/dora. | Ainda precisa calibrar por sala/formato: East-only deveria pesar velocidade mais que valor; hanchan pode aceitar mais valor/forma. |
| Riichi e valido mesmo sem outro yaku, especialmente com boa espera/dora. | Riichi Book 1; AMA diz que riichi costuma superar damaten em EV. | Parcial/bom. `riichi_candidates` cria plano de Riichi para mao fechada e bonus com dora. | Falta decisao fina de riichi vs dama: boa espera, valor ja garantido, perigo na mesa, colocacao e turno. Hoje o bot tende a aceitar Riichi quando botao aparece, mas sem comparar dama/defesa. |
| Push/fold deve combinar progresso, valor, risco do tile, turno, dealer, qualidade da espera e placar. | Push fold chart e Defense wiki. | Parcial. `choose_strategy_mode`, `table_risk_pressure` e `tile_danger_score` consideram riichi adversario, shanten, risco e dora. | Lacuna grande: nao usa placar/posicao, dealer adversario, honba/riichi sticks, nem qualidade da espera ao decidir push. O risco e escala heuristica, nao probabilidade de deal-in calibrada. |
| Contra riichi, 2-shanten ou mais normalmente deve foldar; 1-shanten ruim tambem. | Defense wiki. | Sim, parcialmente. `immediate_defense_reason` entra em defesa imediata se oponente riichi e mao 2-shanten+. | Bom comportamento base. Falta distinguir 1-shanten bom vs ruim usando ukeire real e valor esperado. |
| Em betaori, descarte genbutsu antes de suji/no-chance/honras semi-seguras. | Defense wiki. | Parcial. `tile_danger_score` reduz perigo para genbutsu, suji, honras visiveis. | Precisa endurecer prioridade: em modo defesa, genbutsu deveria dominar quase sempre. Hoje a pontuacao ainda pode escolher alternativas por ordenacao heuristica se varias segurancas competem. |
| Dora e red five sao valiosos, mas dora nao e yaku e nao deve negar yaku/velocidade quando isso importa. | Dora strategy wiki e RiichiScore. | Parcial/bom. `bonus_han_items`, `attack_dora_synergy_bonus` e descartes protegem dora/red five. | Falta tratar dora perto da dora como util/perigosa. Falta diferenciar "dora que ajuda" de "dora morta que bloqueia tanyao/defesa". |
| Dora e tiles perto da dora sao mais perigosos para descartar. | Dora strategy wiki. | Parcial. `tile_danger_score` aumenta perigo da dora. | Nao aumenta perigo dos tiles adjacentes a dora, embora a fonte indique aumento relevante. |
| Chamadas devem ser decididas antes: abrir mao so se mantem yaku aberto viavel, melhora velocidade/valor ou completa yakuhai. | Riichi Book 1 e AMA sobre decidir chamadas com antecedencia. | Parcial/bom. `best_call_decision` simula chamada, exige yaku aberto viavel e melhora score; Chii e mais restrito. | Falta uma politica declarativa por tipo de mao: quando abrir tanyao, yakuhai, honitsu, defesa contra shimocha, ou manter fechado para riichi. |
| Sakigiri: descartar cedo tiles perigosos que nao contribuem, antes que fiquem perigosos. | Defense wiki e AMA. | Fraco. O bot descarta isoladas/fracas e pesa perigo atual, mas nao tem uma regra explicita de sakigiri. | Implementar "perigo futuro": tiles centrais/dora-adjacentes pouco uteis devem sair mais cedo se a mao esta barata/lenta. |
| Leitura de descarte/oponente: inferir yaku provavel para achar tiles seguros ou perigosos. | Defense wiki. | Parcial. Bot detecta riichi, descartes, open tiles e alguns riscos. | Ainda nao faz leitura robusta de honitsu/chinitsu/toitoi/tanyao adversario para defesa. Isso e importante quando oponente tem chamadas abertas sem riichi. |
| Considerar colocacao/pontuacao, principalmente all-last. | Push fold chart e Defense wiki. | Quase nao. OCR de pontos existe, mas a estrategia nao usa ranking/meta de pontos. | Prioridade alta. Sem placar, o bot nao sabe quando precisa atacar mesmo perigoso ou quando deve preservar 2o/3o lugar. |

## Prioridades recomendadas

1. Melhorar push/fold com valor esperado simples: shanten, han/fu estimado, ukeire, turno, dealer, risco do descarte e quantidade de tiles seguros.
2. Integrar placar OCR na estrategia: ranking, distancia para 1o/4o, all-last e necessidade de ataque.
3. Separar modos defensivos: betaori puro, mawashi e push total. Hoje existe `defense/cautious/balanced`, mas a escolha ainda e numerica demais.
4. Adicionar perigo de tiles ao redor da dora e politica de sakigiri.
5. Formalizar politica de chamadas por plano: yakuhai, tanyao aberto, honitsu, chii de eficiencia, pon de par valioso, kan seguro/perigoso.

## Resumo

O bot ja aplica varias ideias basicas: shanten, ukeire, dora, furiten, riichi como yaku valido, defesa contra riichi, genbutsu/suji e qualidade estrutural de blocos. A proxima evolucao deve sair de "qual descarte parece menos ruim" para "qual decisao tem melhor EV considerando valor, espera, risco, turno e placar".
