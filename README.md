# Wonder AI Art Generator 5.1.4 · 协议还原 + 2API 网关

逆向对象：`Wonder AI Art Generator v5.1.4 (Premium).apk`（`com.codeway.wonder`）
产物：可直接跑的 OpenAI 兼容网关 + 网页 Demo + 压测脚本 + 逐字段协议文档。

完整协议（`rs` 签名算法、`ip` 高级版标记、字段漂移、错误码）见 **[`docs/PROTOCOL.md`](docs/PROTOCOL.md)**。
new-api 接入（两条路线 + Go 渠道骨架 + 验收清单）见 **[`docs/NEW-API-INTEGRATION.md`](docs/NEW-API-INTEGRATION.md)**。
分身通道结论见 **[`docs/DREAMBOOTH.md`](docs/DREAMBOOTH.md)**。

---

## 能力总览

| 能力 | 状态 | 证据 |
|---|---|---|
| 文生图（同步出图） | ✅ 已验证 | 40 并发 4 / 30 并发 8 两轮压测 70/70 成功，产物全部可下载 |
| 张数 n=1…10 | ✅ 已验证 | n=10 返回 10 条 URL |
| 宽高比 1:1 / 4:3 / 3:4 / 16:9 / 9:16 | ✅ 已验证 | 逐张下载测尺寸（1024×1024 / 1024×768 / 768×1024 / 1024×576 / 576×1024） |
| 固定 seed 复现 | ✅ 已验证 | seed 回显进产物文件名 |
| 负向提示词 / steps / cfg / expandPrompt | ✅ 已验证 | 200 通过 |
| 图生图（`imageUrl`） | ✅ 已验证 | 200 通过 |
| 4 个 provider 通道 `premium/flux/falai/normal` | ✅ 已验证 | 全部 200 出图 |
| 4x 超分 `/esrgan` | ✅ 已验证 | 硬编码 Bearer，7.7s 返回 `highResImageUrl` |
| 号池（任意 userId = 新账号） | ✅ 已验证 | 无需注册，UUID 即账号 |
| 免费档限制 | ✅ 已探明 | 非高级标记 → 5 积分后 `499 limit`；高级标记 → 0 积分仍出图 |
| 广告加积分 `credit/inc` | ❌ 不可用 | 恒返回 `{"success":false}`（需广告回调凭据） |
| Dreambooth AI 分身 | ⚠️ R1 阻塞 | 端点/参数/素材托管链路全部还原并可调用；训练被付费包门禁清理 → 见 [`docs/DREAMBOOTH.md`](docs/DREAMBOOTH.md) |

---

## 30 秒速查（对接只需要这一段）

```text
base    : https://api.wonderai.app/api/v1/
签名头   : ts(epoch ms) / ip(679449021=高级版) / rs(SHA256 大写)
rs      : sha256(body + "_COvereD062doiNgS855TOshIBa_" + userId + "_" + ts).hex().upper()
账号    : userId 任意字符串 = 新账号（无注册），ip=679449021 积分归零仍可出图
文生图   : POST image/create  {userId,prompt,style,provider,model,numImages,aspectRatio,...} → {urls[],seed}
超分     : POST https://highres.wonderai.app/esrgan {userId,url,platform} + Bearer → {highResImageUrl}
坑      : rs 大写 / 整数浮点必须发 int（8.0→401）/ numImages 才是张数 / 产物只留 30 天
```

---

## 快速开始

```bash
# 依赖：requests fastapi uvicorn httpx pillow（本机已具备）
python server.py                 # 默认 0.0.0.0:8960
# 浏览器打开 http://127.0.0.1:8960/  → Demo 页
```

```bash
# OpenAI 兼容调用
curl -s http://127.0.0.1:8960/v1/images/generations \
  -H 'content-type: application/json' \
  -d '{"prompt":"a crystal fox in a neon forest","model":"wonder-premium","n":2,"size":"1024x576","style":"Anime"}'
```

```json
{"created":1793310231,
 "data":[{"url":"https://storage.googleapis.com/diffusion-api-outputs/...jpeg"},
         {"url":"https://storage.googleapis.com/diffusion-api-outputs/...jpeg"}],
 "seed":4146399875141341,"elapsed":2.63}
```

命令行直连（跳过网关，验证协议本身）：

```bash
python wonder_client.py "a lighthouse on a cliff at sunset" --style s2 --n 2 --ar 16:9
```

---

## HTTP 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | Demo 页面（内置风格缩略图、4x 超分按钮） |
| GET | `/health` | 健康检查 + 风格数 + 支持的宽高比 |
| POST | `/v1/images/generations` | OpenAI 兼容；`model` 映射 provider，`size` 映射宽高比 |
| GET | `/v1/models` | `wonder-premium/flux/falai/normal` |
| POST | `/wonder/image/create` | 原生全参透传（seed/steps/cfg/expandPrompt/imageUrl） |
| POST | `/wonder/upscale` | `{"url": "..."}` → 4x 超分 |
| GET | `/wonder/styles` | 53 条内置风格（value/name/PRO/Priority/样例图） |
| GET | `/wonder/account` | 当前账号积分、今日次数、历史 prompt |
| GET | `/wonder/pool` | 号池状态、并发上限 |

环境变量：`WONDER_UIDS=uid1,uid2`（预置号池）、`WONDER_CONCURRENCY=4`（上游并发闸门）、`WONDER_PORT=8960`。

---

## 压测

```bash
python stress_test.py -n 40 -c 4 --verify-images     # 出图 + 产物下载校验
python stress_test.py -n 30 -c 8                     # 高并发
python stress_test.py --target upscale -n 10 -c 4     # 超分通道
```

本机基线（经本地代理出网，2026-10-30）：

| 轮次 | 成功 | 墙钟 | 吞吐 | p50 / p90 / p99 |
|---|---|---|---|---|
| 40 请求 / 并发 4（n=1，混合 5 种宽高比） | 40/40 | 22.6s | 1.77 req/s | 1.92s / 2.92s / 5.82s |
| 30 请求 / 并发 8 | 30/30 | 19.2s | 1.56 req/s | 3.91s / 5.01s / 7.93s |
| 24 请求 / 并发 4（最终回归，含 b64/超分/原生全参） | 24/24 | 14.3s | 1.68 req/s | 1.92s / 2.91s / 4.95s |
| 产物下载校验 | 8/8 可下载，平均 87KB | — | — | — |

并发继续抬高的瓶颈在本地出网代理与上游排队，不在网关（`WONDER_CONCURRENCY` 控制上游闸门）。

---

## 目录

```text
wonder_client.py   协议客户端（签名、出图、超分、账号）
server.py          FastAPI 网关（OpenAI 兼容 + 原生路由 + 号池）
static/index.html  Demo 页（零依赖，无 CDN）
stress_test.py     异步压测（出图 / 超分 / 产物校验）
styles.json        53 条内置风格表（从 libapp.so 常量区解析）
docs/PROTOCOL.md   逐字段协议文档
raw/               APK 解包产物            （本地，不入库）
blutter_out/       Dart AOT 反解结果        （本地，不入库）
```

---

## 复现逆向

```bash
# 1. 解包
unzip "Wonder AI Art Generator v5.1.4 (Premium).apk" -d raw/

# 2. Dart AOT 反解（需 MSVC + cmake + ninja；blutter 必须跑在 vcvars 环境里）
run_blutter.bat   # 内部调用 vcvars64.bat 后执行 blutter.py raw/lib/arm64-v8a blutter_out

# 3. 关键文件
blutter_out/asm/wonder/services/network/network_service.dart    # rs/ip/ts 生成 + 全部请求体
blutter_out/asm/wonder/services/network/network_constants.dart  # 端点
blutter_out/asm/wonder/helpers/config_helper.dart               # 风格表
```

---

## 说明

- 1 次出图 = 1 积分（与张数无关）；产物在 GCS 只保留 30 天，生产接入需自行转存。
- 上游为同步接口，网关不引入任务队列；`numImages` 上限按实测压到 10。
- 本项目仅为协议研究与本地验证用途。
