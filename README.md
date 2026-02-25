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
### ・ モード 『Merge + Overlay』
長時間連続撮影を行うと、ファイルが自動的に分割されます。  
時系列順にリストにMP4ファイルをD&Dすると、1本のMP4に結合し、GPSデータを合成します。
![長時間動画用ソフト画面](doc/drec1.png "長時間動画用ソフト画面")  
![ドラレコ映像](doc/drec2.png "ドラレコ映像")  
<br>

### ・ モード 『Batch Overlay』
360度動画用のモードです。  
GoProPlayer等でキーフレームを設定し、4Kで出力します。  
出力はカットなしのフル尺でしてください、GPS情報と動画がズレる恐れがあるかもしれません。  
GPSデータ読み込みのため、出力された.MP4と元の.360ファイルの両方をD&Dしてください。  
複数セットのバッチ処理が可能です。
![360度動画用ソフト画面](doc/3601.png "360度動画用ソフト画面")
![360度出力動画](doc/3602.png "360度出力動画")
<br>


### ・ エンコーダの選択
3パターンから選択できます。  
・ソフトウェアエンコード  
・CPU HWエンコード(intel / AppleSilicon)  
・nVIDIA HWエンコード (Windowsのみ)
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

- **gopro-dashboard overlay script** — `time4tea/gopro-dashboard-overlay` から未改変で同梱（GPL-3.0、`third_party/gopro-dashboard/`）
詳細は `THIRD_PARTY_NOTICES.md` を参照してください。

<br>
<br>

## ライセンス

- 本プロジェクト：**GPL-3.0**（`LICENSE`）
- サードパーティ：`THIRD_PARTY_NOTICES.md` および `third_party/` 配下の各ライセンスファイルを参照してください。
