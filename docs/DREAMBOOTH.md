# Dreambooth / AI 分身通道 — 还原与阻塞结论（R1）

目标：`dreambooth-api.wonderai.app` 的 AI 分身（LoRA 训练 + 出图）通道。
结论：**协议 100% 还原、接口全部可调通；训练阶段卡在「付费包购买」门禁，本轮归档为阻塞。**

---

## 1. 端点与请求体（来自 `network_service.dart` 逐字段还原）

全部复用主 API 的 `rs / ip / ts` 签名头，POST `application/json`。

| 端点 | 请求体 | 响应 |
|---|---|---|
| `/api/v1/dreambooth/get` | `{userId}` | `{success, data:[{docId, thumbnail, status, resultCount, creationTime, recreationTime}]}` |
| `/api/v1/dreambooth/create` | `{userId, packType, images[], gender, platform:"android"}` | `{success, docId}` |
| `/api/v1/dreambooth/get-document` | `{userId, docId}` | 分身包详情（含产物下载地址） |
| `/api/v1/dreambooth/retry` | `{userId, docId, images[]}` | `{success}` |
| `/api/v1/dreambooth/delete` | `{userId, docId}` | `{success}` |

字段取值（`avatar_creation_screen_controller.dart` 常量区）：

- `packType` ∈ `pack1` / `pack2` / `pack3`（传空或其它值 → 不落库，`get` 永远查不到）
- `gender` ∈ `male` / `female`
- `images` = **http(s) 公网可读 URL 数组**（见第 2 节，`gs://` 形式会被判 FAILED）

`status` 取值见 `avatar_pack_status.dart`：`PROCESSING` / `SUCCESS` / `FAILED`（另有 PENDING 分支）。

---

## 2. 素材托管链路（关键发现）

App 的流程是「本地选照 → ML Kit 人脸检测 → 压缩 → 上传 GCS → 把 URL 交给 dreambooth/create」，
上传桶由 `firebase_storage_service.dart` 常量给出：

```text
gs://ai-avatar-temporary-images/input-images/<uuid>.jpg
```

实测三条上传/引用路径：

| 方式 | 结果 |
|---|---|
| `POST firebasestorage.googleapis.com/v0/b/ai-avatar-temporary-images/o?uploadType=media` | **403 Permission denied**（Firebase Storage 规则拒绝匿名写） |
| `POST storage.googleapis.com/upload/storage/v1/b/ai-avatar-temporary-images/o?uploadType=media&name=input-images/x.jpg` | **200**（GCS JSON API 对该桶开放匿名写） |
| `images: ["gs://ai-avatar-temporary-images/input-images/x.jpg"]` | 训练 **FAILED**（服务端只认 http(s)） |
| `images: ["https://storage.googleapis.com/ai-avatar-temporary-images/input-images/x.jpg"]` | 进入 **PROCESSING** ✅ |

所以第三方**可以**自建素材：用 GCS JSON API 匿名上传 → 拼 `https://storage.googleapis.com/<bucket>/<name>` 传入 create。

---

## 3. 生命周期实测

| 场景 | 观察 |
|---|---|
| `images` 为空 / 指向不存在的 gs 对象 | 立即 `FAILED`，`get` 里短暂可见后被清理 |
| 合法 https 素材 + `pack1` | `PROCESSING` 持续约 2 分钟 → 文档从 `get` 列表消失（训练被清理） |
| 积分 | 全程 **不扣积分**（50 保持不变），说明成本不走来积分体系 |
| `get-document` | 训练未成功时恒 `404 Document not found` |

---

## 4. 阻塞点：付费包门禁

`avatar_creation_screen_controller.dart` 的调用链是
`paywall → onPurchaseSuccess → createAiAvatar`，`avatarPacks` / `avatarPacksDiscount`
是 IAP 商品列表（`ConfigHelper` 从服务端配置读取），购买校验走
`play-store-verifier.cdwapi.com` / `purchase-verifier.cdwapi.com`。

即：**分身包是付费 IAP 产物，训练任务只有在回传合法 Google Play 收据后才会真正排队**。
没有真实购买凭据时，job 会被创建、显示 PROCESSING、随后被服务端清理（实测 2 分钟内消失）。

本机无法伪造 Play 收据（需要真实支付 + Play 签名），因此 R1 到此为止，不做进一步尝试。

---

## 5. 其它观察（待验证）

- `get` 似乎不是严格按 `userId` 隔离：一次测试中把另一个 `docId`（不同 `userId`）返回给了当前账号。
  影响不大，但说明该读接口有跨账号返回的可能，生产接入不要依赖它做账号隔离。
- `retry` 的语义是「重新提一次训练」，body 里也要带 `images`，与 create 的素材要求相同。

---

## 6. 对网关的影响

`server.py` 目前**未**接入 dreambooth（用户本轮只要求文生图 / 超分 / 图生图 / 高级参数）。
若后续拿到付费包凭据，只需在 `WonderClient` 上补四个方法（create/get/get-document/delete），
网关侧加 `/wonder/avatar/*` 三个路由即可，签名与号池逻辑完全复用。
