# MahjongMaster

Ferramenta inicial em PyQt6 para monitorar uma janela aberta do Mahjong Soul.

## Rodando

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m mahjong_master
```

Na tela inicial, a janela `MahjongSoul-Steam` sera selecionada automaticamente quando estiver aberta.
Se necessario, escolha outra janela na lista.

A preview atualiza em 3 Hz. Use **Salvar screenshot** ou pressione **F3** para salvar uma imagem, mesmo quando o foco estiver no jogo.

## Comecando com YOLO

A estrategia inicial e treinar um detector unico com 38 classes:

- `man_1` a `man_9`
- `pin_1` a `pin_9`
- `sou_1` a `sou_9`
- `wind_east`, `wind_south`, `wind_west`, `wind_north`
- `dragon_white`, `dragon_green`, `dragon_red`
- `man_5_red`, `pin_5_red`, `sou_5_red`
- `tile_back` para pecas viradas/laranjas

### 1. Coletar screenshots

Rode o app, selecione a janela do Mahjong Soul e clique em **Salvar screenshot** varias vezes durante partidas/replays.

As imagens vao para:

```text
dataset/raw
```

Capture variacao: maos, descartes, chamadas, pecas laterais, topo, pecas de cabeca para baixo, mesas e iluminacoes diferentes.

### 2. Anotar

Clique em **Abrir anotador** no app.

No anotador:

- selecione uma screenshot na lista
- escolha a classe ativa pelos botoes ou combo
- arraste na imagem para desenhar uma box em cada peca visivel
- escolha `train`, `val` ou `test`
- clique em **Salvar**

Na lista de screenshots, nomes verdes ja foram salvos em `train`, nomes azuis em `val` e nomes roxos em `test`.
O painel **Pecas catalogadas** mostra quantas imagens/anotacoes existem em cada split, a porcentagem atual e a divisao recomendada de `80% train`, `15% val` e `5% test`.

O proprio app organiza os arquivos assim:

```text
dataset/
  images/
    train/
    val/
    test/
  labels/
    train/
    val/
    test/
```

Atalhos:

```text
A / S / D / F: troca para man, pin, sou ou honras
Q / W / E / T: East, South, West, North
Y / U / I: White, Green, Red
V: peca virada/laranja
1-9: seleciona o numero do naipe ativo
R: seleciona o 5 vermelho do naipe ativo
Z / X: imagem anterior ou proxima
Delete: remove a box selecionada
Ctrl+Z: desfaz a ultima box
Enter: salva a imagem atual
```

O painel **Atalhos** no lado direito do anotador permite trocar qualquer tecla.
Clique no campo do atalho desejado e pressione a nova combinacao.
As alteracoes ficam salvas localmente em `config/shortcuts.json`.

O arquivo de dataset esta em:

```text
data/mahjong_soul.yaml
```

### 3. Treinar

Pela interface, clique em **Abrir treino**. A janela de treino permite configurar modelo, epocas, tamanho da imagem, batch e device.
Use **Nome do treino** para escolher a pasta salva em `runs/detect`.
Se vazio, o nome e gerado como `800e_yolo11n_1600p_5b`; se ja existir, usa `_V2`, `_V3` e assim por diante.
O nome automatico acompanha mudancas em epocas, modelo base e resolucao. O campo **Modelo base** e um dropdown editavel com modelos YOLO comuns.
No dropdown, `n/s/m/l/x` indicam modelos cada vez mais pesados: nano, small, medium, large e xlarge.
Use **Patience** para controlar o EarlyStopping; `0` desativa a parada antecipada.
Durante o treino, os tres graficos sao atualizados a partir do `results.csv` gerado pelo YOLO: loss de treino, loss de validacao e metricas de validacao.
O app usa `weights/best.pt` como resultado recomendado, porque ele representa a melhor epoca na validacao.
`weights/last.pt` e apenas a ultima epoca treinada.
Depois que o treino termina, o script carrega esse `weights/best.pt` e roda uma avaliacao final no split `test`, salvando os artefatos em `runs/detect/<nome_do_treino>/test`.
O botao **Resumo dos treinos** lista as execucoes em `runs/detect` com `results.csv`.
Ele mostra comparativos dos 9 dados do treino, velocidade por treino, detalhe do treino selecionado, tabela por classe quando disponivel e imagens geradas pelo YOLO como matriz de confusao, `results.png`, batches e predicoes de validacao.

Antes do primeiro treino, instale as dependencias:

```powershell
pip install -r requirements-train.txt
```

Tambem e possivel treinar pelo terminal:

```powershell
python scripts/train_yolo.py --model yolo11n.pt --epochs 100 --imgsz 1600 --batch 8
```

Os resultados ficam em:

```text
runs/detect/mahjong_soul_tiles
```

### 4. Testar um modelo

Use o `best.pt` do treino escolhido:

```powershell
python scripts/predict_yolo.py --weights runs/detect/mahjong_soul_tiles/weights/best.pt --source dataset/raw --imgsz 1600 --conf 0.25 --device 0
```

As imagens com as deteccoes desenhadas ficam em:

```text
runs/predict/mahjong_soul_tiles
```

### 5. Predict em tempo real

Na tela principal:

- escolha um `best.pt` no campo **Modelo**
- marque **Predict**
- ajuste a taxa em **FPS**
- use **Pausar apos proximo predict** para rodar apenas uma predicao e congelar o modo predict

O overlay desenha as bounding boxes e labels diretamente na preview da janela monitorada.
A primeira camada de leitura de jogo tenta identificar sua mao, chamadas abertas e yakus provaveis.
Detalhes da arquitetura e limitacoes estao em [`docs/game_logic.md`](docs/game_logic.md).

### 6. Treinar no Google Colab

Gere um pacote do dataset local:

```powershell
python scripts/prepare_colab_package.py
```

Isso cria:

```text
colab_packages/mahjongmaster_colab_dataset.zip
```

No Google Drive, crie a pasta:

```text
MyDrive/MahjongMaster
```

Envie o `.zip` para essa pasta. Depois abra:

```text
notebooks/train_colab.ipynb
```

No Colab:

1. Use **Runtime > Change runtime type > GPU**.
2. Rode as celulas em ordem.
3. Se ainda nao enviou o pacote ao Drive, use a celula **Upload do dataset** no proprio notebook.
4. Rode a celula **TensorBoard** antes do treino para monitorar losses, metricas, matriz de confusao e imagens geradas.
5. Ajuste `MODEL`, `EPOCHS`, `IMGSZ`, `BATCH` e `PATIENCE` na celula de treino.

O resultado volta para:

```text
MyDrive/MahjongMaster/runs/detect/<nome_do_treino>
```

Baixe ou copie o `weights/best.pt` para `runs/detect` local se quiser usar no predict do app.
