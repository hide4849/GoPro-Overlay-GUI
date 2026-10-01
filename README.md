[for English](README_EN.md)

# GoPro Overlay GUI Tool

GoProのテレメトリ（GPS/速度など）やダッシュボード風の情報を、動画にオーバーレイ（合成）するためのGUIツールです。  
ドライブ映像用と360度映像用の機能を持たせています。

[time4tea](https://github.com/time4tea) さんの超素晴らしく超ありがたい [gopro-dashboard-overlay](https://github.com/time4tea/gopro-dashboard-overlay) をGUIで簡単に使えるようにしました。

Windows / macOSに対応しています。

このリポジトリは **GPL-3.0** で公開しています。`LICENSE` を参照してください。

<br>
<br>

## 使い方
### ・ モード 『Overlay (MP4)』
GPSデータを含む複数のMP4を、結合せず1本ずつ順番にオーバーレイ処理します。  
完成動画は、指定した Output folder に `元ファイル名_output.mp4` として出力されます。`Save in input file folder` をオンにすると入力MP4と同じフォルダに保存されます。  
出力解像度がOriginalの場合は事前の解像度変換を行いません。2Kの場合も入力幅が1920px以下なら変換を省略し、1920pxより大きい場合だけ2Kへ変換します。

### ・ モード 『DRC(Merge + Overlay)』
長時間連続撮影を行うと、ファイルが自動的に分割されます。  
時系列順にリストにMP4ファイルをD&Dすると、1本のMP4に結合し、GPSデータを合成します。`Load File List` または TXT のD&Dでファイルリストを読み込んだ場合は、TXTに記載された順で結合します（1行1パス、または ffconcat の `file 'パス'` 形式）。相対パスはTXTの保存場所を基準に解決されます。
![長時間動画用ソフト画面](doc/drec1.png "長時間動画用ソフト画面")  
![ドラレコ映像](doc/drec2.png "ドラレコ映像")  
<br>

### ・ モード 『360 Overlay (.mp4 + .360)』
360度動画用のモードです。  
GoProPlayer等でキーフレームを設定し、4Kで出力します。  
出力はカットなしのフル尺でしてください、GPS情報と動画がズレる恐れがあるかもしれません。  
GPSデータ読み込みのため、出力された.MP4と元の.360ファイルの両方をD&Dしてください。  
複数セットのバッチ処理が可能です。
![360度動画用ソフト画面](doc/3601.png "360度動画用ソフト画面")
![360度出力動画](doc/3602.png "360度出力動画")
<br>


### ・ エンコーダの選択
4パターンから選択できます。
- ソフトウェアエンコード
- CPU HWエンコード（Intel / Apple Silicon）
- NVIDIA HWエンコード（Windowsのみ）
- AMD Radeon AMF HWエンコード（Windowsのみ、Ryzen AI Max+ 395 / Radeon 8060S対応）
<br>


### ・ 出力解像度
4Kと2Kが選択できます。
4Kを選択すると1080pへのトランスコード工程がスキップできます。  
が、オーバーレイ処理に時間がかかります。
<br>


### ・ タイムラプス
長時間のドライブ映像用にx5、x10が選択できます。  
x1がタイムラプスなしです。
<br>

### ・ オーバーレイのタイムゾーン
日時オーバーレイに使用するタイムゾーンを選択できます。  
デフォルトは日本（`Asia/Tokyo`）です。海外で撮影した動画では、たとえばフィンランドなら
`Europe/Helsinki` を選択してください。地域名を使うため、夏時間も自動的に反映されます。
<br>

### ・ DJIドローンのSRTオーバーレイ
`DJI (MP4 + SRT)` で、MP4と同じフォルダに同名の `.SRT`（または `.srt`）を置くと自動で読み込みます。
例：`DJI_0001.MP4` と `DJI_0001.SRT`。編集・速度変更前の対応する動画を使用してください。
SRT内の日時は `Overlay Timezone` の地域の時刻として解釈します。

飛行ルート、GPSから計算した水平速度、絶対高度に加え、以下を個別選択できます。
- DJI Recording Time：録画開始からの秒数（離陸からの飛行時間ではありません）
- DJI Relative Altitude：SRTに記録された相対高度
- DJI Vertical Speed：相対高度から計算した上昇・下降速度
- DJI Distance from Rec Start：最初のGPS地点からの水平直線距離
- DJI Distance from Home：`Home lat,lon` に指定した地点からの水平直線距離（空欄なら表示なし）

速度は前後約1秒の位置・高度を使って平滑化します。GPS誤差を含む推定値です。
DJIモードは動画を1本ずつ処理し、録画時間と開始地点は動画ごとにリセットします。
DRC / 360 / OverlayモードではSRTを使用しません。DJIへのGPX補完は未対応です。
SRTに測位状態やDOPがないため、GPS Lock / GPS AccuracyはDJIでは表示しません。
### ・ DJI RC 2 飛行ログのオーバーレイ
`DJI (SRT / Flight Log)` で、`Decoded flight log (.json)` に復号済みの飛行ログJSONを選択します。
バイナリの `.txt` ではなく、`pydjirecord --json` などで生成した `frames` を含むJSONを使用します。
このプロジェクトに `flightrecord-decoded.json` がある場合は起動時に候補として入ります。

- 同名SRTがあれば日時で自動同期します。冒頭の1970年の日時は同期に使用しません。
- SRTがない場合は `Video start` に、動画が始まる時点の飛行経過秒数を指定します。手入力時は動画1本ずつ処理してください。
- DJI専用の選択項目は14項目です：バッテリー残量・温度・電圧・電流、飛行経過時間・相対高度・上昇下降速度、ホーム距離・累積移動距離、GPS衛星数、上り・下り通信強度、飛行モード、スティック位置。
- 水平速度・上昇下降速度は **km/h** で表示します。通信強度は0～100を5段階のアンテナピクトに変換します。
- 飛行モードは左下の速度表示の上にN/S/C/Mで表示します。該当モードを判定できない状態は `--` です。
- 下中央に左右のスティックを四角い枠と点で表示します。初期設定はモード2です。送信機の設定に合わせてモード1・2・3を選んでください。
- `Recommended HUD` で上記の項目と、共通の速度・日時・GPS座標・経路図を選択できます。
- 飛行ログのホーム地点を使用します。`Home lat,lon` に入力するとその地点で上書きします。
- 選択した情報は半透明の3列パネルにまとめ、映像サイズに合わせて拡大縮小します。
- ログより動画が冒頭・末尾で最大5秒長い場合、その区間のログ値は空欄にします。SRTの情報は継続して使えます。それ以上の時刻ずれ・範囲外や、ログ途中の1秒超の欠損がある場合は処理を止めます。欠けた値は推定しません。
- セル数・セル別電圧・寿命推定・残り飛行時間は表示しません。Neo 2で未確認の値を避けています。

元の速度で編集前の動画を使ってください。飛行ログを使わない従来のMP4＋SRT処理も利用できます。
<br>

### ・ GPSロガーによる補完
GPSロガーの `.gpx` ファイルを指定できます。GoPro GPSが正常な地点はそのまま使用し、
GPSロックなし、DOP不良、異常速度などで無効になった地点だけをGPXで補完します。
時刻はGPX内のUTC時刻で自動同期し、60秒を超える未記録区間は補間しません。
<br>


### ・ 中間ファイル削除
これにチェックを入れると、完了後に不要な中間ファイルが削除されます。
<br>

## 構成

- `gopro_overlay_GUI.py` — メインのGUIツール
- `build.spec` — PyInstaller でスタンドアロン実行ファイルを作るための spec
- `requirements.txt` — Python依存パッケージ一覧
- `third_party/` — 同梱しているサードパーティ（FFmpeg、Robotoフォント、gopro-dashboard など）

サードパーティのライセンス詳細は `THIRD_PARTY_NOTICES.md` を参照してください。

<br>
<br>

## 必要環境
- Apple Silicon Mac（推奨）
- Windows 10/11（推奨）
- Python 3.14 で確認
- FFmpeg は `third_party/ffmpeg/` に同梱しています（`ffmpeg.exe` / `ffprobe.exe`）

<br>
<br>

## EXEのビルド（PyInstaller）
### ■Windows

仮想環境の準備：
```bash
python -m venv venv
venv\Scripts\activate
```
依存パッケージをインストール：

```bash
pip install -U pip
pip install -r requirements.txt
```

同梱の spec を使ってビルド：

```bash
PyInstaller -y build.spec
```

出力は `dist/` 配下に生成されます（フォルダ名は spec の内容に依存します）。

> 補足:
> - この`build.spec`は `third_party/` 配下の同梱物（FFmpeg、フォント等）を参照しビルドしています。
> - パスを変更した場合は `build.spec` 側も合わせて修正してください。

<br>

### ■macOS

仮想環境の準備：
```bash
python3 -m venv venv
source venv/bin/activate
```

依存パッケージをインストール：
```bash
pip install -U pip
pip install -r requirements.txt
```

同梱の spec を使ってビルド：

```bash
PyInstaller -y build.spec
```

出力は `dist/` 配下に生成されます（フォルダ名は spec の内容に依存します）。

> 補足:
> - この`build.spec`は `third_party/` 配下の同梱物（FFmpeg、フォント等）を参照しビルドしています。
> - パスを変更した場合は `build.spec` 側も合わせて修正してください。

<br>
<br>

## クレジット / サードパーティ

同梱コンポーネント：

- **FFmpeg / FFprobe** — Windowsビルドの再配布（`third_party/ffmpeg/`）
本リポジトリは Windows向けの FFmpeg バイナリを以下に同梱しています。
`third_party/ffmpeg/ffmpeg.exe`  
`third_party/ffmpeg/ffprobe.exe`  
これらは [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) のビルドを再配布しています。
<br><br>
`third_party/ffmpeg/ffmpeg`  
`third_party/ffmpeg/ffprobe`  
これらは [evermeet.cx](https://evermeet.cx/ffmpeg/) のビルドを再配布しています  。
詳細は `third_party/ffmpeg/LICENSE` を参照してください。  

- **Roboto フォント** — `googlefonts/roboto-3-classic`（OFL-1.1、`third_party/Roboto/`）  

- **gopro-dashboard overlay script** — `time4tea/gopro-dashboard-overlay` から無改変で同梱（GPL-3.0、`third_party/gopro-dashboard/`）
詳細は `THIRD_PARTY_NOTICES.md` を参照してください。

<br>
<br>

## ライセンス

- 本プロジェクト：**GPL-3.0**（`LICENSE`）
- サードパーティ：`THIRD_PARTY_NOTICES.md` および `third_party/` 配下の各ライセンスファイルを参照してください。
