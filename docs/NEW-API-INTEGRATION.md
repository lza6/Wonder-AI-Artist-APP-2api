# Wonder AI → new-api 对接指南（交给其他会话执行）

本文档是**自包含交接书**：另一个会话不需要看逆向过程，只看这一份就能把 Wonder 图片能力接进 new-api。
上游协议细节见 [`PROTOCOL.md`](PROTOCOL.md)，分身通道见 [`DREAMBOOTH.md`](DREAMBOOTH.md)。

仓库：https://github.com/lza6/Wonder-AI-Artist-APP-2api

---

## 0. 先选路线（5 分钟决策）

| 路线 | 改动量 | 适用 | 生产建议 |
|---|---|---|---|
| **A. 直连本仓库网关**（OpenAI 兼容渠道） | new-api 零改代码，只加一个渠道 | 先跑通、先上线试量 | 可行，但多一跳；号池/签名在 Python 侧 |
| **B. 写原生 Go 渠道 `wonder`** | 新增 5 个文件 + 4 处注册 | 长期生产、要计费/限流/号池复用 new-api 能力 | **推荐** |

两条路可以并存：先 A 验证上游是否被限流，再落 B。

---

## 1. 上游能力速查（写死这些，不要自己再探）

```text
base   : https://api.wonderai.app/api/v1/
hires  : https://highres.wonderai.app/esrgan
签名头 : ts(epoch ms) / ip(679449021=高级版) / rs(SHA256大写)
rs     : sha256(jsonBody + "_COvereD062doiNgS855TOshIBa_" + userId + "_" + ts).hex().upper()
账号   : userId = 任意字符串，首次调用自动建档，无注册
```

| 能力 | 上游端点 | 形态 | 计费 |
|---|---|---|---|
| 文生图 / 图生图 | `POST image/create` | 同步，返回 `urls[]` + `seed` | 1 次请求 = 1 credit（与张数无关） |
| 4x 超分 | `POST https://highres.wonderai.app/esrgan` | 同步，返回 `highResImageUrl` | 不耗积分，走硬编码 Bearer |
| 账号查询 | `POST user/get` | 同步 | — |

**关键参数（实测有效值）**

| 字段 | 取值 | 备注 |
|---|---|---|
| `provider` | `premium` / `flux` / `falai` / `normal` | 4 通道都能出图 |
| `model` | `default` / `sdxl` / `flux` / `lightning` | 其它值 500 |
| `numImages` | 1–10 | **张数用驼峰 `numImages`**；`num_images` 被上游忽略（照抄即可） |
| `aspectRatio` | `1:1` `4:3` `3:4` `16:9` `9:16` | → 1024×1024 / 1024×768 / 768×1024 / 1024×576 / 576×1024 |
| `style` | `s2`…`s99`（53 个） | 传 name（`Anime`）会 500；`styles.json` 里有全表 |
| `seed` + `isSeedFixed` | int + true | 固定种子可复现，seed 回显在文件名里 |
| `imageUrl` | http(s) URL | 传了就是图生图 |
| `negativePrompt` / `numInferenceSteps` / `cfgScale` / `expandPrompt` | 可选 | 全部有效 |

**错误码映射（必须原样处理，别当普通 5xx 重试）**

| 上游 | 含义 | new-api 侧处理 |
|---|---|---|
| `401 request-signature: Invalid request signature` | `rs` 算错（大小写 / 数值规范化 / body 被改动） | 500（网关 bug）；**不要重试** |
| `401 request-signature: Request time is expired` | `ts` 落后服务端 | 时钟同步后重签重试 1 次 |
| `499 limit: User has reached limits.` | 免费档额度耗尽 | 视为「渠道 key 失效」→ 换号 or 禁用渠道 |
| `500 error:{}` | 参数非法（未知 style/model/provider 组合） | 400 透出，别重试 |
| `500 internal-error` + `Request failed with status code 500` | 上游生成失败（如 `numImages=20`） | 429/502，可重试 1 次 |
| 网络 `SSLEOFError` / `ConnectionAborted` | 本地代理掐 keep-alive | 重建连接重试（最多 3 次） |

---

## 2. 路线 A：直连本仓库网关（零改码）

### 2.1 起服务

```bash
cd D:/apk-analysis/wonder514
python server.py                    # 监听 0.0.0.0:8960
curl -s localhost:8960/health       # {"status":"ok","styles":53,...}
```

### 2.2 在 new-api 里加渠道

- 类型：选**任意 OpenAI 兼容渠道**（`OpenAI` / `自定义 OpenAI` 均可）
- Base URL：`http://<网关主机>:8960`
- Key：随便填（网关不校验；上游鉴权在网关头里）
- 模型：`wonder-premium`、`wonder-flux`、`wonder-falai`、`wonder-normal`

### 2.3 参数映射（客户端 → 网关）

```jsonc
POST {{base}}/v1/images/generations
{
  "model": "wonder-premium",        // ← 映射上游 provider
  "prompt": "...",
  "n": 2,                            // ← numImages（上限 10）
  "size": "1024x576",                // ← aspectRatio；也接受 "16:9" 写法
  "style": "Anime",                  // ← name 或 value（s23）都行
  "response_format": "url",          // ← b64_json 也支持
  "seed": 123,                       // 可选
  "negative_prompt": "blurry",       // 可选
  "upstream_model": "flux",          // 可选：default/sdxl/flux/lightning
  "provider": "flux"                 // 可选：覆盖 model 推出的 provider
}
```

返回：`{"created":…, "data":[{"url":…,"revised_prompt":…}], "seed":…, "elapsed":2.63}`

### 2.4 其它路由（网关已实现）

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/wonder/image/create` | 原生全参透传（steps/cfg/expandPrompt/imageUrl） |
| POST | `/wonder/upscale` | `{"url":"..."}` → 4x 超分 |
| GET | `/wonder/styles` | 53 条风格表 |
| GET | `/wonder/account` / `/wonder/pool` | 号池与积分状态 |
| GET | `/v1/models` | 4 个模型名 |

### 2.5 路线 A 的硬限制（决定它能不能上生产）

- 网关是**单进程同步阻塞**（FastAPI threadpool + `WONDER_CONCURRENCY` 闸门），并发上限约 4–8，实测 1.6–1.8 req/s。
- 网关无鉴权、无计费、无重试预算控制 → **公网暴露前必须加防火墙或反代鉴权**。
- 号池按 userId 轮转，落盘无持久化 → 重启即换新号（不影响出图，只影响积分统计）。

---

## 3. 路线 B：写原生 Go 渠道 `wonder`（推荐）

模块路径：`github.com/lza6/new-api-Max`。参考实现：`relay/channel/apero/`（同样的「自签头 + 同步出图 + 多通道」形态，直接照抄骨架）。

### 3.1 注册四处（必须全改，漏一个就 404 / 模型广场不显示）

**① `constant/channel.go`** — 渠道类型编号（当前最大 80，取 81）

```go
ChannelTypeWonderAI = 81
```

同文件顶部默认 base URL 数组按序号追加（第 81 项）：

```go
"https://api.wonderai.app",   // 81 Wonder AI
```

同文件渠道名映射：

```go
ChannelTypeWonderAI: "Wonder AI",
```

**② `constant/api_type.go`** — 追加到 iota 列表末尾（**只能追加，不能插队**）

```go
APITypeWonderAI
```

**③ `relay/relay_adaptor.go`** — `GetAdaptor` 的 switch 里加：

```go
case constant.APITypeWonderAI:
    return &wonder.Adaptor{}
```

**④ `relay/channel/wonder/`** — 新包（详见下）

### 3.2 新包文件结构

```text
relay/channel/wonder/
  wonder.go          # 常量、ModelList、provider/model 映射表
  sign.go            # rs 签名 + JSON 规范化（核心，必须单测）
  adaptor.go         # Adaptor 接口实现（Init/GetRequestURL/SetupRequestHeader/ConvertImageRequest/GetModelList/GetChannelName）
  do_request.go      # DoRequest 发上游 + DoResponse 解析 + 错误映射 + 号池
  do_request_test.go # 表驱动：签名黄金向量、数值规范化回归、错误码映射
```

### 3.3 `wonder.go`：常量与模型

```go
package wonder

const ChannelName = "Wonder AI"

const (
	hostAPI   = "https://api.wonderai.app/api/v1/"
	hostHires = "https://highres.wonderai.app/esrgan"
	rsKey     = "COvereD062doiNgS855TOshIBa"
	premiumIP = 679449021 // 高级版自报标记：积分耗尽仍可出图
	appVer    = "5.1.4"
	platform  = "android"
	maxImages = 10 // 实测 20 打穿上游
)

// 模型名 → (provider, upstream model)
type route struct{ provider, model string }

var ModelRoutes = map[string]route{
	"wonder-premium": {"premium", "default"},
	"wonder-flux":    {"flux", "flux"},
	"wonder-falai":   {"falai", "default"},
	"wonder-normal":  {"normal", "sdxl"},
}

var ModelList = []string{"wonder-premium", "wonder-flux", "wonder-falai", "wonder-normal"}

// size → aspectRatio（不在这张表里的一律拒 400，别让上游静默退化成 1:1）
var AspectBySize = map[string]string{
	"1024x1024": "1:1", "1024x768": "4:3", "768x1024": "3:4",
	"1024x576": "16:9", "576x1024": "9:16",
}
```

### 3.4 `sign.go`：签名（**最容易出错的地方**）

必须满足两条，否则一直 401：

1. **对「实际发出的字节」签名**：先序列化，再对同一份 `[]byte` 签名和发送，禁止二次 marshal。
2. **数值规范化**：上游是 `JSON.parse → stringify` 之后才验签，`8.0` 会变 `8`。
   所以序列化前把所有「整数值的 float」降成 int（`cfgScale: 8.0` → `8`）。

```go
package wonder

import (
	"crypto/sha256"
	"encoding/hex"
	"strconv"
	"strings"

	"github.com/lza6/new-api-Max/common"
)

// canonicalBody 序列化并保证「数值规范化」——上游验签用的是 JSON round-trip 后的 body。
func canonicalBody(v any) ([]byte, error) {
	raw, err := common.Marshal(v)
	if err != nil {
		return nil, err
	}
	// 用 RawMessage 递归把 8.0 → 8：上游 JSON.parse/stringify 的等价行为
	var generic any
	if err := common.Unmarshal(raw, &generic); err != nil {
		return nil, err
	}
	return common.Marshal(normalizeNumbers(generic))
}

func normalizeNumbers(v any) any {
	switch t := v.(type) {
	case float64:
		if t == float64(int64(t)) {
			return int64(t)
		}
		return t
	case map[string]any:
		for k, item := range t {
			t[k] = normalizeNumbers(item)
		}
		return t
	case []any:
		for i := range t {
			t[i] = normalizeNumbers(t[i])
		}
		return t
	}
	return v
}

// Sign 计算 rs：sha256(body + "_" + rsKey + "_" + userId + "_" + ts)，大写十六进制。
func Sign(body []byte, userID string, ts int64) string {
	var b strings.Builder
	b.Write(body)
	b.WriteByte('_')
	b.WriteString(rsKey)
	b.WriteByte('_')
	b.WriteString(userID)
	b.WriteByte('_')
	b.WriteString(strconv.FormatInt(ts, 10))
	sum := sha256.Sum256([]byte(b.String()))
	return strings.ToUpper(hex.EncodeToString(sum[:]))
}
```

> 黄金向量（用于单测，必须固定）：用同一个 `body`/`userId`/`ts` 打两次，`Sign` 结果全等；
> 并且把 `cfgScale=8.0` 的 body 过 `canonicalBody` 后应变成 `8`（JSON 里不再出现 `8.0`）。

### 3.5 `adaptor.go`：接口实现

```go
func (a *Adaptor) Init(info *relaycommon.RelayInfo) {}
func (a *Adaptor) GetRequestURL(info *relaycommon.RelayInfo) (string, error) { return hostAPI + "image/create", nil }
func (a *Adaptor) GetModelList() []string { return ModelList }
func (a *Adaptor) GetChannelName() string { return ChannelName }

// 上游不需要 Authorization 头；鉴权在 body 签名里，SetupRequestHeader 只放 Content-Type。
func (a *Adaptor) SetupRequestHeader(c *gin.Context, req *http.Header, info *relaycommon.RelayInfo) error {
	req.Set("Content-Type", "application/json")
	req.Set("Accept", "application/json")
	return nil
}

func (a *Adaptor) ConvertImageRequest(c *gin.Context, info *relaycommon.RelayInfo, request dto.ImageRequest) (any, error) {
	return request, nil // 真正映射在 DoRequest 里做（需要 channel key = userId）
}
```

其余接口方法（`ConvertOpenAIRequest` / `ConvertClaudeRequest` / `ConvertEmbeddingRequest` …）按 `apero` 的做法返回 `nil, nil` 或 `not implemented` 错误——图片渠道不接受 chat 请求。

### 3.6 `do_request.go`：请求映射 + 号池 + 响应

```go
func (a *Adaptor) DoRequest(c *gin.Context, info *relaycommon.RelayInfo, requestBody io.Reader) (any, error) {
	req := info.ImageRequest // 或从 gin context 取 dto.ImageRequest
	userID := info.ChannelKey // ★ 渠道 key 直接当 userId：一个 key = 一个上游账号
	rt, ok := ModelRoutes[req.Model]
	if !ok {
		return nil, types.NewError(errors.New("unknown wonder model"), types.ErrorCodeBadResponseStatusCode)
	}
	aspect, ok := AspectBySize[orDefault(req.Size, "1024x1024")]
	if !ok {
		return nil, types.NewError(errors.New("unsupported size"), types.ErrorCodeBadResponseStatusCode)
	}
	n := int(derefUint(req.N, 1))
	if n < 1 || n > maxImages {
		return nil, types.NewError(errors.New("n out of range 1..10"), types.ErrorCodeBadResponseStatusCode)
	}

	body := map[string]any{
		"userId": userID, "prompt": req.Prompt, "style": styleOrDefault(req.Style),
		"provider": rt.provider, "model": rt.model,
		"num_images": n, "numImages": n,   // 两个都发：上游只认驼峰，保留下划线对齐 App 行为
		"aspectRatio": aspect, "platform": platform, "appVersion": appVer,
		"enableSafetyChecker": false, "adWatched": false, "adReason": "",
		"feature": "create", "isSeedFixed": req.Seed != nil,
	}
	if req.Seed != nil { body["seed"] = *req.Seed }
	if p := extraString(req, "negative_prompt"); p != "" { body["negativePrompt"] = p }

	raw, err := canonicalBody(body)         // ★ 与签名同一份字节
	if err != nil { return nil, err }

	// ts 偏移自愈：渠道级缓存 skew，遇到 "expired" 就 +5s 重签（最多 3 次）
	ts := time.Now().UnixMilli() + skewFor(userID)
	httpReq, _ := http.NewRequestWithContext(c.Request.Context(), http.MethodPost, hostAPI+"image/create", bytes.NewReader(raw))
	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("Accept", "application/json")
	httpReq.Header.Set("ts", strconv.FormatInt(ts, 10))
	httpReq.Header.Set("ip", strconv.Itoa(premiumIP))
	httpReq.Header.Set("rs", Sign(raw, userID, ts))

	resp, err := client.Do(httpReq)
	...
	return resp, nil
}
```

`DoResponse` 里把上游 JSON 转成 OpenAI 形状，并按第 1 节的错误码表分流：

```go
type wonderCreateResp struct {
	Success          bool     `json:"success"`
	URLs             []string `json:"urls"`
	Seed             *int64   `json:"seed"`
	RemainingCredits *int     `json:"remainingCredits"`
	Error            string   `json:"error"`
	ErrorMessage     string   `json:"errorMessage"`
}
// → dto.ImageResponse{Created: time.Now().Unix(), Data: []dto.ImageData{{Url: u}...}}
```

超分是**独立端点且换 host**（`highres.wonderai.app`），建议做成第二个模型 `wonder-upscale`：
`ConvertImageRequest` 里把输入图 URL 塞进 `imageUrl`，或走独立的 `/v1/images/upscale` 自定义路由（保持 OpenAI 语义清晰）。

### 3.7 计费建议

- 上游成本：**1 次请求 = 1 credit**，与张数无关（n=10 也是 1 credit）→ 上游极度便宜，
  但**必须在 new-api 侧按「张数」计费**，否则 n=10 的用户把 1 credit 的成本摊给 10 张图。
- 推荐：把 `n` 计入 `PriceData` 的图片倍率（`OtherRatios`），而不是按次固定价。
- 参考现有 `setting/billing_setting/builtin_billing.go` 的表达方式，**新内置价必须写成自包含表达式**。

### 3.8 号池（关键设计）

上游 `userId` 是免费身份，所以 **new-api 的渠道 key = 上游 userId** 最自然：

- 一个渠道多 key → new-api 自动轮转，天然号池；
- 遇到 `499 limit` → 该 key 标记失败（`EnableChannel` 逻辑已有），后续自动跳过；
- 高级版标记 `ip=679449021` 让「积分耗尽仍出图」，实测 74 次连续请求积分打到 0 照样成功——
  所以**正常情况不会触发 499**，号池主要是防单号被封。

---

## 4. 验收清单（路线 B）

```bash
cd <new-api-repo>

# 1) 编译（含 relaykit 独立可编译，AGENTS.md 强制要求）
go build ./...
cd relaykit && GOWORK=off go build ./... && cd ..

# 2) 单测（签名 + 数值规范化 + 错误码映射）
go test ./relay/channel/wonder/ -run 'TestSign|TestCanonical|TestErrorMap' -v

# 3) 起本地服务，建渠道（类型 81，key 填一个 uuid），再发真实请求
curl -s localhost:3000/v1/images/generations \
  -H 'Authorization: Bearer sk-xxx' -H 'Content-Type: application/json' \
  -d '{"model":"wonder-premium","prompt":"a crystal fox","n":2,"size":"1024x576"}'
# 期望：200，data 里有 2 条 GCS url，且 url 里能看到 seed 段

# 4) 反向验证（必须全绿再收工）
#    - 未知 style → 400（不是 500）
#    - n=11 → 400
#    - size=512x512 → 400
#    - 把 ts 人为改早 60s → 触发一次重签后仍成功（日志可见 retry）
#    - 拔掉一个 key 的额度（用免费档账号）→ 499 → 渠道失败计数 +1，轮转到下一个 key
```

---

## 5. 已知坑（照抄即可少踩）

1. **数值规范化**：`cfgScale: 8.0` 必 401，必须发 `8`（见 3.4）。
2. **大小写**：`rs` 必须大写 hex。
3. **ts 只能超前不能落后**：慢 30s → `expired`。容器里跑要注意 NTP。
4. **`numImages` 才是张数**，`num_images` 是历史字段（保留但无效）。
5. **`aspectRatio` 非法值静默退化成 1:1**，网关必须自己白名单校验。
6. **`numImages` > 10 会被上游打成 `500 internal-error`**。
7. **产物只保 30 天**（`diffusion-api-outputs/30-day-outputs/`），要长期可用必须转存。
8. **超分不需要 key**（硬编码 Bearer），但**不要公网暴露**这个能力，否则会被白嫖算力。
9. 上游 `user/get` 偶发 500（连续重复调用），重试即恢复，别当渠道故障。

---

## 6. 前置知识（如需自己再验证协议）

- `wonder_client.py` 是**协议参考实现**（Python，签名/重试/时钟偏移都在里面），照它翻 Go 最省事。
- `styles.json` 有 53 条风格的 value/name/PRO 标记，可直接生成前端下拉。
- `server.py` 里有完整的「OpenAI 形状 ↔ 上游形状」映射逻辑，可直接当需求规格读。

---

## 7. 非目标（本轮不做）

- 分身（dreambooth）训练通道：被 IAP 付费包门禁阻断，见 `DREAMBOOTH.md`。
- 广告加积分：`credit/inc` 恒失败。
- 流式出图：上游是同步接口，没有 SSE。
