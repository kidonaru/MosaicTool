# macOS の署名と公証

`MosaicTool.app` は Developer ID で署名し、Apple の公証 (notarization) を通すことで、
ダウンロード後にダブルクリックで起動できるようになります。

GitHub Secrets が未設定の場合、ビルドは ad-hoc 署名のまま続行し公証をスキップします。
その場合はダウンロードしたユーザーが `xattr -dr com.apple.quarantine` を実行する必要があります。

## 必要なもの

- Apple Developer Program のメンバーシップ (年 $99)
- Developer ID Application 証明書
- 公証用の App-specific password

## 手順

### 1. 証明書を作る

1. Keychain Access で「証明書アシスタント」→「認証局に証明書を要求」を選び、
   CSR ファイル (`.certSigningRequest`) を保存する
2. [Apple Developer の Certificates](https://developer.apple.com/account/resources/certificates/list) で
   「Developer ID Application」を選び、CSR をアップロードして証明書をダウンロードする
3. ダウンロードした `.cer` をダブルクリックして Keychain へ登録する

### 2. 証明書を .p12 で書き出す

Keychain Access で証明書と秘密鍵をまとめて選び、右クリック →「2 項目を書き出す」で
`.p12` として保存する。書き出し時に設定したパスワードは次の手順で使う。

base64 に変換する。

```
base64 -i certificate.p12 | pbcopy
```

### 3. App-specific password を作る

[appleid.apple.com](https://appleid.apple.com/) にサインインし、
「サインインとセキュリティ」→「App 用パスワード」から発行する。

### 4. GitHub Secrets に登録する

リポジトリの Settings → Secrets and variables → Actions で以下を登録する。

| 名前 | 値 |
|---|---|
| `MACOS_CERTIFICATE` | 手順 2 で base64 化した `.p12` |
| `MACOS_CERTIFICATE_PWD` | `.p12` のパスワード |
| `MACOS_SIGN_IDENTITY` | `Developer ID Application: <名前> (<Team ID>)` |
| `MACOS_TEAM_ID` | Team ID (10 文字) |
| `MACOS_NOTARY_APPLE_ID` | Apple Developer アカウントの Apple ID |
| `MACOS_NOTARY_PASSWORD` | 手順 3 の App-specific password |

`MACOS_SIGN_IDENTITY` の正確な文字列は、証明書を登録した Mac で以下を実行すると確認できる。

```
security find-identity -v -p codesigning
```

## ローカルで署名する

上記の環境変数を設定してビルドすると、同じ経路で署名・公証される。

```
export MACOS_SIGN_IDENTITY="Developer ID Application: ... (TEAMID)"
export MACOS_TEAM_ID="TEAMID"
export MACOS_NOTARY_APPLE_ID="you@example.com"
export MACOS_NOTARY_PASSWORD="xxxx-xxxx-xxxx-xxxx"
python scripts/package.py --clean
```

## 確認

```
codesign --verify --deep --strict --verbose=2 dist/MosaicTool.app
spctl --assess --type execute --verbose dist/MosaicTool.app
xcrun stapler validate dist/MosaicTool.app
```

`spctl` が `accepted` かつ `source=Notarized Developer ID` を返せば成功。
