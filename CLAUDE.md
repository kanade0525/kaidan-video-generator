# CLAUDE.md

## 必須ルール

### Docker再ビルド（最重要）
Pythonコード・設定ファイルを変更したら、**必ず** `docker compose up --build -d` を実行してコンテナに反映すること。
ローカルファイルを変更しただけではDockerコンテナには反映されない。テスト実行前、UI確認前、すべてのタイミングで再ビルドが必要。

### コード変更時のフロー
1. コード変更
2. テスト実行 (`pytest`)
3. `docker compose up --build -d`
4. 動作確認
