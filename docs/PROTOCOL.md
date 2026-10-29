# Wonder AI Art Generator 5.1.4 — 协议规格（逐字段还原）

目标 APK：`Wonder AI Art Generator v5.1.4 (Premium).apk`
包名 `com.codeway.wonder`（Codeway Dijital）· Flutter AOT（Dart 3.4.4）· 无 native 协议层

| 证据来源 | 取出的内容 |
|---|---|
| `blutter` 反解 `libapp.so` → `blutter_out/asm/wonder/services/network/network_service.dart` | 请求头、`rs` 签名算法、`ip` 语义、`ts` 单位、全部请求体字段 |
| `blutter_out/asm/wonder/services/network/network_constants.dart` | 全部端点 URL |
| `blutter_out/asm/wonder/helpers/config_helper.dart` | 53 条内置风格表（value / name / 优先级 / 是否 PRO） |
| `blutter_out/asm/wonder/models/advanced_settings.dart` | 高级参数 JSON 键名 |
| 线上探针（本仓库 `wonder_client.py`） | 签名正确性、错误码、积分语义、并发/时延、宽高比映射 |

---

## 1. 端点清单

```text
base_api   = https://api.wonderai.app/api/v1/
base_hires = https://highres.wonderai.app
base_db    = https://dreambooth-api.wonderai.app/api/v1/dreambooth/
```

| 方法 | 路径 | 作用 | 鉴权 |
|---|---|---|---|
| POST | `/api/v1/user/get` | 积分余额 / 今日次数 / 已存 prompt | `rs+ip+ts` |
| POST | `/api/v1/image/create` | 文生图 / 图生图（同步返回 URL） | `rs+ip+ts` |
| POST | `/api/v1/ad/show` | 是否可看激励视频 | `rs+ip+ts` |
| POST | `/api/v1/credit/inc` | 看广告加积分 | `rs+ip+ts` |
| POST | `/api/v1/prompt/delete` | 删除历史 prompt | `rs+ip+ts` |
| POST | `https://highres.wonderai.app/esrgan` | 4x 超分 | **硬编码 Bearer**（无用户鉴权） |
| POST | `/api/v1/dreambooth/{get,create,retry,delete,get-document}` | AI 分身 / LoRA 训练 | `rs+ip+ts` |

`user/get` 之外的响应都是 `{"success": bool, ...}`；失败统一为
`{"success":false,"error":"<code>","errorMessage":"<text>"}`。

---

## 2. 鉴权：`ts` / `ip` / `rs`

`NetworkService._getHeaders()` 组装，三个自定义头（CORS 预检也确认了它们）：
`access-control-allow-headers: Content-Type, authorization, ts, rs`。

### 2.1 `ts`

- **epoch 毫秒**（Dart 里 `now().microsecondsSinceEpoch ~/ 1000`）。
- 服务端往回只容忍极小时差：实测 `ts = now-30s` 即返回
  `401 {"error":"request-signature","errorMessage":"Request time is expired"}`；
  `ts = now+2min` 通过。→ 客户端时钟**不能慢**，可以超前。
- `wonder_client.py` 遇到 expired 会自动 `+5s` 重签并记住偏移。

### 2.2 `ip`

`_getHeaders()` 里：

```text
_cerebro.isPremium() ? 679449021 : _generateRandomInt()   # random 0..1e9，命中 679449021 时改成 1234
```

即 **`ip` 是「我是高级版」的自报标记**，值为常量 `679449021`。
服务端完全信任该标记：

| `ip` | `/user/get` 初始积分 | 积分耗尽后 |
|---|---|---|
| `679449021` | 50（随后显示 999 前缀） | **0 积分仍然继续出图**（实测 74 次请求全成功） |
| 其他整数 | 5 | 第 6 次开始 `499 {"error":"limit","errorMessage":"User has reached limits."}` |

### 2.3 `rs`

```dart
rs = sha256( jsonEncode(body) + "_" + "COvereD062doiNgS855TOshIBa" + "_" + userId + "_" + ts )
       .toString().toUpperCase()      // 必须大写十六进制
```

要点（踩坑记录）：

1. `jsonEncode(body)` 必须在 Dart 侧无空格；用 Python 时
   `json.dumps(body, separators=(",", ":"))`，且**签名串必须与实际发出的字节完全一致**。
2. 摘要转成 **大写** hex；小写 → `Invalid request signature`。
3. `userId` 是 `Cerebro` 的第一个字段（`field_f`），由本地生成并持久化。
4. 三段分隔符都是下划线，顺序为 `body _ KEY _ userId _ ts`。
5. **数值必须规范化**：服务端会先 `JSON.parse` 再 `JSON.stringify` 后才验签，因此
   `8.0` 会被规范化成 `8`。若请求体里出现「整数值的浮点」（典型：`cfgScale: 8.0`），
   签名与规范化后的 body 不一致 → `Invalid request signature`。
   实测：`cfgScale:8` → 200，`cfgScale:8.0` → 401。
   `wonder_client.py` 的 `_canonical()` 在序列化前把所有整数值浮点转成 int。

### 2.4 `userId`

无注册流程：**任意字符串都会在首次调用时被服务端建档**
（`uuid4`、`mydevice-abc-123` 均可，实测都返回 200 并给 50 积分）。
→ 号池成本为 0，无需 Firebase / 设备指纹 / Play 收据。

---

## 3. `/image/create` 请求体

原始字段（Dart map 逐键写入顺序）：

```json
{
  "userId": "<uuid>",
  "prompt": "a lighthouse on a cliff at sunset",
  "style": "s2",
  "provider": "premium",
  "num_images": 1,
  "platform": "android",
  "appVersion": "5.1.4",
  "enableSafetyChecker": false,
  "adWatched": false,
  "adReason": "",
  "feature": "create",
  "model": "default",
  "numImages": 1,
  "aspectRatio": "1:1",
  "isSeedFixed": false,
  "negativePrompt": "blurry",
  "seed": 123456,
  "numInferenceSteps": 30,
  "cfgScale": 7.5,
  "expandPrompt": true,
  "imageUrl": "https://…/init.jpeg"
}
```

| 字段 | 取值 | 说明 |
|---|---|---|
| `style` | `s2`…`s99`（53 个，见 `styles.json`） | 传 name（如 `Anime`）会被当非法值 500；网关已做 name→value 别名映射 |
| `provider` | `premium` / `flux` / `falai` / `normal` | 4 个通道都实测出图 |
| `model` | `default` / `sdxl` / `flux` / `lightning` | 其余值（`dev`/`pro`/`schnell`/`s2`）→ 500 |
| `numImages` | 1–10 | **真正的张数**；`num_images`（下划线）被服务端忽略（这是 5.x 的字段漂移，照抄即可） |
| `aspectRatio` | `1:1` `4:3` `3:4` `16:9` `9:16` | → 1024×1024 / 1024×768 / 768×1024 / 1024×576 / 576×1024；其它值静默退化成 1:1 |
| `seed` + `isSeedFixed` | int / true | 固定种子会原样回显在产物文件名里（可验证） |
| `imageUrl` | 图片 URL | 传入即图生图（`prompt` 仍必填） |
| `feature` | `create` / `recreate` | 同一接口两种入口 |
| `numInferenceSteps` / `cfgScale` / `expandPrompt` / `negativePrompt` | 可选 | 均通过，值由上游模型消费 |

### 3.1 响应（同步，不是任务轮询）

```json
{
  "success": true,
  "urls": ["https://storage.googleapis.com/diffusion-api-outputs/30-day-outputs/com.codeway.wonder-<uid>-<seed>-<ts>.jpeg"],
  "seed": 4146399875141341,
  "creditActive": true,
  "remainingCredits": 999,
  "dailyProcessCount": 12
}
```

- 1 次请求 = 1 积分，**与 `numImages` 张数无关**（n=10 也是 1 积分）。
- 产物落在 `diffusion-api-outputs/30-day-outputs/`（30 天保留，需及时转存）。
- 单请求时延实测 p50 ≈ 1.9s、p90 ≈ 2.9s（并发 4，n=1）。

### 3.2 错误码

| 状态 | body | 含义 |
|---|---|---|
| 401 | `error:"request-signature"` / `errorMessage:"Invalid request signature"` | `rs` 不对（大小写/拼接/body 不一致） |
| 401 | `…"Request time is expired"` | `ts` 落后于服务端 |
| 499 | `error:"limit"` | 免费档积分耗尽 |
| 500 | `error:{}` | 参数非法（未知 style / model / 未知 provider 组合） |
| 500 | `error:"internal-error"` + `Request failed with status code 500` | 上游生成失败（如 `numImages=20`） |

---

## 4. 4x 超分 `/esrgan`

```http
POST https://highres.wonderai.app/esrgan
Content-Type: application/json
Authorization: Bearer d5jf7w9b4QdGm3KSK4P1cRUlWTjWziPmdjBJVlEHv4SyyhCpjIpkUwYXjHsW6983

{"userId":"<uuid>","url":"<原图URL>","platform":"android"}
```

```json
{"highResImageUrl":"https://storage.googleapis.com/wonder-images-us/high-resolution-images/…jpeg","success":true}
```

- 该 Bearer 是**编译期常量**（`_getHeadersForHighRes`），不走用户鉴权、不签 `rs`。
- 不消耗积分；不校验 `userId` 归属。实测 1 张 ≈ 7.7s。

---

## 5. 其它端点

| 端点 | 请求体 | 响应 |
|---|---|---|
| `user/get` | `{"userId":uid}` | `{success, prompts[], creditActive, remainingCredits, dailyProcessCount}` |
| `ad/show` | `{"userId":uid}` | `{success, canShowAd:false, adType:null, reason:"error"}`（无广告 SDK 时恒不可用） |
| `credit/inc` | `{"userId":uid}` | `{"success":false}`（需要广告回调凭据，非免费路径） |
| `prompt/delete` | `{"userId":uid,"prompt":"<text>"}` | `{success:true}` |
| `dreambooth/get` | `{"userId":uid}` | 分身训练状态（未深挖，本轮未接入） |

---

## 6. 与本仓库网关的对应关系

| OpenAI 侧 | Wonder 侧 |
|---|---|
| `POST /v1/images/generations {prompt,n,size,style,seed,model}` | `/image/create`（`size`→`aspectRatio`，`n`→`numImages`） |
| `model="wonder-premium|wonder-flux|wonder-falai|wonder-normal"` | `provider` |
| `POST /wonder/image/create` | 原生全参透传 |
| `POST /wonder/upscale {url}` | `/esrgan` |
| `GET /wonder/styles` | 内置 53 条风格表 |
| `GET /wonder/account` · `/wonder/pool` | 号池积分与身份状态 |

---

## 7. 环境坑（本机实测）

1. **本机时钟必须不慢于服务端**：慢 30s 即全部 401 expired。
2. 本地代理（`HTTPS_PROXY=127.0.0.1:10808`）会掐断空闲 keep-alive 连接，表现为
   `SSLEOFError / ConnectionAborted 10053`；客户端已在异常时重建 `requests.Session` 重试。
3. 服务端对同一 `userId` 的 `user/get` 偶发 500（连续重复调用时），重试即恢复，不影响出图。
4. `numImages=20` 会打穿上游（500 `internal-error`），网关侧限到 10。
